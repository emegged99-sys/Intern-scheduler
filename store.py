# -*- coding: utf-8 -*-
"""Per-month storage for the scheduler.

Each month is one self-contained record: its roster, external duties, holidays,
the assignment, and the generated workbook. Nothing is shared between months, so
configuration may change freely from month to month and an old month can always
be re-read exactly as it was published — no re-solving.

Two engines behind one interface:
  * SQLite  — default, a single file. Local runs, or any host with a real disk.
  * Postgres — when DATABASE_URL is set. Required for free hosting, where the
    filesystem is wiped on every deploy and every wake from sleep.

The SQL is written so both engines accept it unchanged.
"""
import datetime
import json
import os
import random
import re
import sqlite3
import threading

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)
SQLITE_PATH = os.environ.get(
    "SCHED_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scheduler.db"))

_pg = None
if IS_PG:
    try:
        import psycopg as _pg
    except ImportError:
        try:
            import psycopg2 as _pg
        except ImportError:
            raise SystemExit(
                "DATABASE_URL מוגדר, אבל דרייבר Postgres לא מותקן.\n"
                "  התקינו:  pip install 'psycopg[binary]'\n"
                "  או הסירו את DATABASE_URL כדי לעבוד מול SQLite מקומי.")

_local = threading.local()
SERIAL_PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
BLOB_TYPE = "BYTEA" if IS_PG else "BLOB"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS months (
        ym         TEXT PRIMARY KEY,
        status     TEXT NOT NULL DEFAULT 'draft',
        frozen_at  TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        doc        TEXT NOT NULL,
        version    INTEGER NOT NULL DEFAULT 1,
        lease_by   TEXT,
        lease_name TEXT,
        lease_until TEXT)""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        ym TEXT PRIMARY KEY, filename TEXT NOT NULL,
        blob %s NOT NULL, created_at TEXT NOT NULL)""" % BLOB_TYPE,
    """CREATE TABLE IF NOT EXISTS intern_codes (
        intern_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS settings (
        k TEXT PRIMARY KEY, v TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS audit (
        id %s, ts TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, ym TEXT, detail TEXT)""" % SERIAL_PK,
]


# ------------------------------------------------------------ plumbing ------
def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat(" ")


def _to_pg(sql):
    """`?` -> `%s`, ignoring anything inside a quoted string."""
    out, quote = [], None
    for ch in sql:
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _Cur:
    def __init__(self, cur, cols):
        self._c, self._cols = cur, cols

    def _row(self, r):
        if r is None:
            return None
        return dict(r) if isinstance(r, sqlite3.Row) else dict(zip(self._cols, r))

    def fetchone(self):
        return self._row(self._c.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._c.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    def __init__(self):
        if IS_PG:
            self.raw = _pg.connect(DATABASE_URL)
            self.raw.autocommit = True
        else:
            os.makedirs(os.path.dirname(os.path.abspath(SQLITE_PATH)), exist_ok=True)
            # isolation_level=None => autocommit, matching the Postgres branch.
            # Without it every write is rolled back when the request ends.
            self.raw = sqlite3.connect(SQLITE_PATH, timeout=15, isolation_level=None)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(_to_pg(sql) if IS_PG else sql, tuple(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        return _Cur(cur, cols)

    def close(self):
        try:
            self.raw.close()
        except Exception:                                          # noqa: BLE001
            pass


def _con():
    """One connection per thread, reused within a request and closed after it —
    reconnecting per query would add a round trip to every call, and holding one
    open past the request would keep a scale-to-zero database awake."""
    c = getattr(_local, "c", None)
    if c is not None:
        try:
            c.execute("SELECT 1")
            return c
        except Exception:                                          # noqa: BLE001
            c.close()
    c = _Conn()
    _local.c = c
    return c


def release():
    c = getattr(_local, "c", None)
    if c is not None:
        c.close()
        _local.c = None


def as_bytes(v):
    return bytes(v) if v is not None and not isinstance(v, bytes) else v


def engine_label():
    if not IS_PG:
        return f"SQLite · {os.path.abspath(SQLITE_PATH)}"
    return "Postgres · " + re.sub(r"^.*@", "", DATABASE_URL).split("/")[0].split("?")[0]


ADD_COLUMNS = [
    ("months", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("months", "lease_by", "TEXT"),
    ("months", "lease_name", "TEXT"),
    ("months", "lease_until", "TEXT"),
]


def init_db():
    c = _con()
    for stmt in SCHEMA:
        c.execute(stmt)
    # upgrade a database created before versioning existed
    for table, col, decl in ADD_COLUMNS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception:                                          # noqa: BLE001
            pass                                                   # already there


def log(actor, action, ym=None, detail=""):
    _con().execute("INSERT INTO audit(ts,actor,action,ym,detail) VALUES (?,?,?,?,?)",
                   (now(), actor, action, ym, detail))


def audit_tail(limit=100):
    return _con().execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ------------------------------------------------------------- settings -----
def setting(k, default=None):
    r = _con().execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_setting(k, v):
    _con().execute("""INSERT INTO settings(k,v) VALUES (?,?)
                      ON CONFLICT (k) DO UPDATE SET v=excluded.v""", (k, str(v)))


# --------------------------------------------------------------- months -----
EMPTY_DOC = {"interns": [], "external": {}, "holidays": {},
             "assignment": [], "stats": "", "generated_at": None}


def _doc_of(row):
    d = dict(EMPTY_DOC)
    d.update(json.loads(row["doc"]))
    return d


def get_month(ym):
    r = _con().execute("SELECT * FROM months WHERE ym=?", (ym,)).fetchone()
    if not r:
        return None
    return {"ym": r["ym"], "status": r["status"], "frozen_at": r["frozen_at"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "version": r["version"] or 1, "lease": _lease_of(r), **_doc_of(r)}


def _lease_of(row):
    """An editing lease is advisory and short-lived: it tells a second browser
    that someone else is working, but correctness rests on the version check."""
    until = row["lease_until"] if "lease_until" in row.keys() else None
    if not until or until < now():
        return None
    return {"by": row["lease_by"], "name": row["lease_name"], "until": until}


def month_meta(ym):
    m = get_month(ym)
    if not m:
        return None
    return {"ym": m["ym"], "status": m["status"], "frozenAt": m["frozen_at"],
            "updatedAt": m["updated_at"], "generatedAt": m.get("generated_at"),
            "interns": len(m["interns"]), "assigned": len(m["assignment"]),
            "version": m["version"], "lease": m["lease"],
            "hasXlsx": get_artifact(ym)[0] is not None}


def list_months():
    rows = _con().execute("SELECT ym FROM months ORDER BY ym DESC").fetchall()
    return [month_meta(r["ym"]) for r in rows]


def save_month(ym, doc, status=None):
    c = _con()
    exists = c.execute("SELECT ym FROM months WHERE ym=?", (ym,)).fetchone()
    payload = json.dumps(doc, ensure_ascii=False)
    if exists:
        if status:
            c.execute("""UPDATE months SET doc=?, status=?, updated_at=?,
                         version=version+1 WHERE ym=?""", (payload, status, now(), ym))
        else:
            c.execute("""UPDATE months SET doc=?, updated_at=?, version=version+1
                         WHERE ym=?""", (payload, now(), ym))
    else:
        c.execute("""INSERT INTO months(ym,status,created_at,updated_at,doc,version)
                     VALUES (?,?,?,?,?,1)""", (ym, status or "draft", now(), now(), payload))
    sync_codes(doc.get("interns") or [])


def patch_month(ym, **fields):
    m = get_month(ym)
    if m is None:
        return None
    doc = {k: m[k] for k in EMPTY_DOC}
    doc.update(fields)
    save_month(ym, doc)
    return get_month(ym)


def create_month(ym, source_ym=None, copy_external=False, copy_holidays=False):
    """A new month inherits the roster — approvals, strengths, caps, special
    requests — but starts with an empty calendar: blocked and preferred days are
    date-specific and must be collected again from the interns."""
    doc = dict(EMPTY_DOC)
    doc["interns"], doc["external"], doc["holidays"] = [], {}, {}
    if source_ym:
        src = get_month(source_ym)
        if src:
            fresh = []
            for it in src["interns"]:
                it = dict(it)
                it["blocked"], it["preferred"] = [], []
                it.pop("_datesBy", None)
                it.pop("_datesAt", None)
                fresh.append(it)
            doc["interns"] = fresh
            if copy_external:
                doc["external"] = dict(src["external"])
            if copy_holidays:
                doc["holidays"] = dict(src["holidays"])
    save_month(ym, doc, status="draft")
    return get_month(ym)


def set_status(ym, status):
    _con().execute("""UPDATE months SET status=?, frozen_at=?, updated_at=?,
                      version=version+1 WHERE ym=?""",
                   (status, now() if status == "frozen" else None, now(), ym))


def version_of(ym):
    r = _con().execute("SELECT version FROM months WHERE ym=?", (ym,)).fetchone()
    return (r["version"] or 1) if r else None


def versions():
    """Cheap poll payload: what every month is at right now."""
    rows = _con().execute("SELECT ym, version, status, updated_at FROM months").fetchall()
    return {r["ym"]: {"version": r["version"] or 1, "status": r["status"],
                      "updatedAt": r["updated_at"]} for r in rows}


# ------------------------------------------------------------- leases -------
LEASE_SECONDS = 45


def take_lease(ym, who, name=""):
    """Grant or renew a short editing lease. Returns (ok, holder)."""
    c = _con()
    r = c.execute("SELECT lease_by, lease_name, lease_until FROM months WHERE ym=?",
                  (ym,)).fetchone()
    if r is None:
        return False, None
    held = _lease_of(r)
    if held and held["by"] != who:
        return False, held
    until = (datetime.datetime.now()
             + datetime.timedelta(seconds=LEASE_SECONDS)).replace(microsecond=0).isoformat(" ")
    c.execute("UPDATE months SET lease_by=?, lease_name=?, lease_until=? WHERE ym=?",
              (who, name, until, ym))
    return True, {"by": who, "name": name, "until": until}


def drop_lease(ym, who):
    c = _con()
    r = c.execute("SELECT lease_by FROM months WHERE ym=?", (ym,)).fetchone()
    if r and r["lease_by"] == who:
        c.execute("UPDATE months SET lease_by=NULL, lease_name=NULL, lease_until=NULL WHERE ym=?",
                  (ym,))


def delete_month(ym):
    c = _con()
    c.execute("DELETE FROM months WHERE ym=?", (ym,))
    c.execute("DELETE FROM artifacts WHERE ym=?", (ym,))


def is_frozen(ym):
    m = get_month(ym)
    return bool(m and m["status"] == "frozen")


# ------------------------------------------------------------ artifacts -----
def put_artifact(ym, filename, blob):
    _con().execute("""INSERT INTO artifacts(ym,filename,blob,created_at) VALUES (?,?,?,?)
                      ON CONFLICT (ym) DO UPDATE SET
                        filename=excluded.filename, blob=excluded.blob,
                        created_at=excluded.created_at""", (ym, filename, blob, now()))


def get_artifact(ym):
    r = _con().execute("SELECT filename, blob FROM artifacts WHERE ym=?", (ym,)).fetchone()
    return (r["filename"], as_bytes(r["blob"])) if r else (None, None)


# ------------------------------------------------------- intern identity ----
ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"        # no look-alike characters


def _fresh_code(used):
    while True:
        c = "".join(random.choice(ALPHABET) for _ in range(6))
        if c not in used:
            return c


def sync_codes(interns):
    """Every intern keeps one personal code across all months."""
    c = _con()
    have = {r["intern_id"]: r["code"] for r in c.execute("SELECT intern_id, code FROM intern_codes")}
    used = set(have.values())
    for it in interns:
        iid = (it.get("id") or "").strip()
        if not iid:
            continue
        if iid in have:
            c.execute("UPDATE intern_codes SET name=? WHERE intern_id=?", (it.get("name", ""), iid))
        else:
            code = _fresh_code(used)
            used.add(code)
            c.execute("INSERT INTO intern_codes(intern_id,name,code,created_at) VALUES (?,?,?,?)",
                      (iid, it.get("name", ""), code, now()))


def list_codes():
    return _con().execute(
        "SELECT intern_id, name, code FROM intern_codes ORDER BY intern_id").fetchall()


def reset_code(intern_id):
    c = _con()
    used = {r["code"] for r in c.execute("SELECT code FROM intern_codes")}
    code = _fresh_code(used)
    c.execute("UPDATE intern_codes SET code=? WHERE intern_id=?", (code, intern_id))
    return code


def find_by_code(code):
    return _con().execute("SELECT intern_id, name FROM intern_codes WHERE code=?",
                          ((code or "").strip().upper(),)).fetchone()


# -------------------------------------------------------------- migrate -----
def migrate_legacy(data_dir, default_ym):
    """Adopt the previous single-state files (interns/external/holidays.json)
    into a real month, once. Without this the first deploy of this version would
    look like all the data had vanished."""
    if setting("legacy_migrated"):
        return None
    doc = dict(EMPTY_DOC)
    found = False
    for key in ("interns", "external", "holidays"):
        path = os.path.join(data_dir, f"{key}.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    val = json.load(f)
                if val:
                    doc[key] = val
                    found = True
            except Exception:                                      # noqa: BLE001
                pass
    set_setting("legacy_migrated", now())
    if not found:
        return None
    if get_month(default_ym) is None:
        save_month(default_ym, doc, status="draft")
        set_setting("current_ym", default_ym)
        log("system", "migrate_legacy", default_ym,
            f"{len(doc['interns'])} interns from data/*.json")
        return default_ym
    return None
