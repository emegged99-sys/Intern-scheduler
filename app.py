# -*- coding: utf-8 -*-
"""Web backend for the monthly on-call scheduler.

Admin (HTTP Basic, APP_PASSWORD) — used by intern_editor.html:
  POST /generate                    run the solver, return schedule + base64 xlsx
  GET/POST /api/state/<key>         editor state; now scoped to the current month
  GET/POST /api/months...           month archive, freeze/publish, access codes

Intern portal (session cookie, personal code) — served from /portal/:
  GET  /api/portal/me               who am I, which months can I see
  GET  /api/portal/months/<ym>      my dates, my shifts, the published table
  PUT  /api/portal/months/<ym>/dates

Every month is stored whole — roster, external duties, holidays, assignment and
workbook together — so a past month is re-read, never re-solved.

Run locally:   python3 app.py            (http://localhost:5000)
Run in prod:   gunicorn app:app --timeout 600 --workers 1
"""
import base64
import calendar
import csv
import datetime
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from functools import wraps

from flask import Flask, request, jsonify, session, send_from_directory

try:
    from flask_cors import CORS
except ImportError:                       # keep the app runnable without the extra
    CORS = None

import store

app = Flask(__name__)
# The editor is hosted separately (Netlify) and authenticates with Basic auth,
# so it needs CORS. The portal is served from this same origin and uses a
# session cookie, which must never be readable cross-site.
# Paths the separately-hosted editor calls cross-origin. The portal is served
# from this origin and must NOT be listed: its session cookie has to stay
# same-site.
CROSS_ORIGIN = ("/api/state", "/generate", "/api/months", "/api/codes",
                "/api/audit", "/health")

if CORS is not None:
    CORS(app, resources={p + "*": {"origins": "*"} for p in CROSS_ORIGIN})
else:
    @app.after_request
    def _cors(resp):
        if request.path.startswith(CROSS_ORIGIN):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return resp

    @app.route("/<path:_any>", methods=["OPTIONS"])
    def _preflight(_any):
        return ("", 204)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES") == "1",
                  PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULER = os.path.join(BASE_DIR, "monthly_scheduler.py")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

ALLOWED_KEYS = {"interns", "external", "holidays"}

# Interns see a month's table once it is frozen. Drafts change on every run, so
# they stay private unless this is turned on.
SHOW_DRAFTS = os.environ.get("SHOW_DRAFTS") == "1"

PORTAL_DIR = os.path.join(BASE_DIR, "portal")
STATION_HE = {"er1": "מיון 1", "er2": "מיון 2", "nicu1": "פגיה 1",
              "nicu2": "פגיה 2", "ward": "מחלקה", "picu": 'טיפ"נ'}
HEB_DOW = ["ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "שבת", "א׳"]      # Monday-indexed
MONTHS_HE = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
             "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]


def _default_ym():
    t = datetime.date.today()
    return f"{t.year:04d}-{t.month:02d}"


store.init_db()
store.migrate_legacy(DATA_DIR, _default_ym())


@app.teardown_request
def _release_db(exc=None):
    store.release()


def current_ym():
    ym = store.setting("current_ym")
    if ym and store.get_month(ym):
        return ym
    months = store.list_months()
    if months:
        ym = months[0]["ym"]
    else:
        ym = _default_ym()
        store.create_month(ym)
    store.set_setting("current_ym", ym)
    return ym


def ym_label(ym):
    y, m = (int(x) for x in ym.split("-"))
    return f"{MONTHS_HE[m-1]} {y}"


def day_meta(ym, holidays):
    """Day-by-day calendar facts. A holiday eve counts as Friday and a holiday
    as Saturday, matching what the solver does."""
    y, m = (int(x) for x in ym.split("-"))
    out = []
    for d in range(1, calendar.monthrange(y, m)[1] + 1):
        iso = f"{y:04d}-{m:02d}-{d:02d}"
        dow = datetime.date(y, m, d).weekday()
        is_fri, is_sat = dow == 4, dow == 5
        h = holidays.get(iso)
        kind = None
        if h:
            kind = h.get("kind") if isinstance(h, dict) else str(h)
            if kind == "eve":
                is_fri = True
            else:
                is_sat = True
        out.append({"day": d, "iso": iso, "dowHe": HEB_DOW[dow],
                    "isFri": is_fri, "isSat": is_sat, "isWeekend": is_fri or is_sat,
                    "holiday": (h.get("name") if isinstance(h, dict) else None) or
                               ("ערב חג" if kind == "eve" else "חג" if kind else None),
                    "holidayKind": kind})
    return out


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not APP_PASSWORD:
            return view(*args, **kwargs)
        auth = request.authorization
        supplied = auth.password if auth else ""
        if not auth or not hmac.compare_digest(supplied, APP_PASSWORD):
            # Deliberately no WWW-Authenticate header: it makes the browser pop
            # its own Basic-auth dialog with a username box, and there is no
            # username here — only a password. The editor asks for it itself.
            return jsonify(error="נדרשת סיסמת שרת"), 401
        return view(*args, **kwargs)
    return wrapped


@app.get("/editor")
@app.get("/intern_editor.html")
def editor():
    """Serve the admin editor from the backend too. It still works as a
    standalone file (and on Netlify), but served from here it is reachable from
    any machine on the network and needs no server address typed in."""
    return send_from_directory(BASE_DIR, "intern_editor.html")


@app.get("/")
def root():
    """A bare backend root used to 404, which looks broken to anyone who opens
    the address in a browser. Say what this is and where to go instead."""
    base = request.url_root.rstrip("/")
    months = store.list_months()
    cur = current_ym()
    rows = "".join(
        f"<tr><td>{ym_label(m['ym'])}</td>"
        f"<td>{'🔒 מוקפא' if m['status'] == 'frozen' else 'טיוטה'}</td>"
        f"<td>{m['interns']}</td><td>{m['assigned'] or '—'}</td></tr>"
        for m in months[:6])
    return f"""<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>שרת השיבוץ</title>
<style>
 body{{margin:0;background:#F0F3F8;color:#14213A;font-size:15px;line-height:1.55;
   font-family:"Segoe UI",system-ui,Arial,"Noto Sans Hebrew",sans-serif}}
 .wrap{{max-width:620px;margin:0 auto;padding:40px 20px}}
 h1{{font-size:22px;color:#1F3864;margin:0 0 4px}}
 .sub{{color:#5A6577;font-size:13.5px;margin:0 0 26px}}
 .card{{background:#fff;border:1px solid #DCE3EE;border-radius:12px;padding:18px 20px;
   margin-bottom:14px;box-shadow:0 1px 2px rgba(31,56,100,.06),0 8px 24px rgba(31,56,100,.06)}}
 .card h2{{margin:0 0 6px;font-size:16px;color:#1F3864}}
 .card p{{margin:0 0 12px;color:#5A6577;font-size:13.5px}}
 a.go{{display:inline-block;background:#2E5496;color:#fff;text-decoration:none;
   font-weight:600;padding:9px 18px;border-radius:9px}}
 code{{background:#E7ECF6;color:#1F3864;padding:3px 9px;border-radius:6px;
   font-family:ui-monospace,Consolas,monospace;font-size:13.5px;direction:ltr;display:inline-block}}
 table{{width:100%;border-collapse:collapse;font-size:13.5px}}
 th{{text-align:right;font-size:11.5px;color:#5A6577;padding:5px 6px;border-bottom:1px solid #DCE3EE}}
 td{{padding:6px;border-bottom:1px solid #F0F3F8}}
 .ok{{color:#548235;font-weight:600}}
</style>
<div class="wrap">
  <h1>שרת השיבוץ</h1>
  <p class="sub">השרת פועל <span class="ok">✓</span> · בסיס נתונים: {store.engine_label().split(' · ')[0]}
     · חודש נוכחי: {ym_label(cur)}</p>

  <div class="card">
    <h2>מתמחים</h2>
    <p>כניסה עם הקוד האישי לצפייה בתורנויות ולעדכון תאריכים.</p>
    <a class="go" href="/portal/">כניסה למסך שלי ←</a>
  </div>

  <div class="card">
    <h2>מנהל</h2>
    <p>עריכת מתמחים, תורני חוץ, חגים, יצירת שיבוץ, ארכיון והקפאה.</p>
    <a class="go" href="/editor">פתיחת מסך המנהל ←</a>
    <p style="margin:12px 0 0">אפשר גם לפתוח את הקובץ <code>intern_editor.html</code>
       ישירות; במקרה כזה הזינו תחת «כתובת השרת»: <code>{base}</code></p>
  </div>

  <div class="card">
    <h2>חודשים בארכיון</h2>
    {"<table><tr><th>חודש</th><th>מצב</th><th>מתמחים</th><th>משבצות</th></tr>" + rows + "</table>"
      if months else "<p style='margin:0'>עדיין אין חודשים.</p>"}
  </div>
</div></html>"""


@app.get("/health")
def health():
    return jsonify(status="ok")


# ---- file-based state storage ----
@app.get("/api/state/<key>")
@require_auth
def get_state(key):
    """Kept at the same URL the editor already calls, but the data now belongs
    to the currently selected month instead of being one global blob."""
    if key not in ALLOWED_KEYS:
        return jsonify(error="Invalid key"), 400
    ym = request.args.get("ym") or current_ym()
    m = store.get_month(ym)
    return jsonify(data=(m or {}).get(key), ym=ym,
                   status=(m or {}).get("status", "draft"),
                   version=(m or {}).get("version", 0),
                   lease=(m or {}).get("lease"))


@app.post("/api/state/<key>")
@require_auth
def save_state(key):
    if key not in ALLOWED_KEYS:
        return jsonify(error="Invalid key"), 400
    ym = request.args.get("ym") or current_ym()
    if store.is_frozen(ym):
        return jsonify(error=f"החודש {ym_label(ym)} מוקפא — יש לבטל את ההקפאה כדי לערוך"), 409
    if store.get_month(ym) is None:
        store.create_month(ym)

    # Optimistic concurrency. A browser tells us which version it read; if the
    # month has moved on since, someone else has written and this request would
    # silently erase their work. A lease alone cannot prevent that — a browser
    # may have loaded its copy long before any lease existed.
    base = request.args.get("version")
    who = request.headers.get("X-Client-Id", "")
    cur = store.version_of(ym)
    if base not in (None, "") and int(base) != cur:
        m = store.get_month(ym)
        return jsonify(error="הנתונים בשרת השתנו בינתיים", conflict=True,
                       serverVersion=cur, yourVersion=int(base),
                       data={"interns": m["interns"], "external": m["external"],
                             "holidays": m["holidays"]}), 409

    held = (store.get_month(ym) or {}).get("lease")
    if held and who and held["by"] != who:
        return jsonify(error=f"{held.get('name') or 'משתמש אחר'} עורך את החודש הזה כרגע",
                       locked=True, holder=held), 423

    store.patch_month(ym, **{key: request.get_json()})
    store.log("admin", f"edit_{key}", ym)
    return jsonify(ok=True, ym=ym, version=store.version_of(ym))


@app.get("/api/versions")
@require_auth
def api_versions():
    """Polled by open editors to notice another browser's changes."""
    return jsonify(versions=store.versions(), current=current_ym())


@app.post("/api/months/<ym>/lease")
@require_auth
def api_lease(ym):
    b = request.get_json(silent=True) or {}
    who = request.headers.get("X-Client-Id") or b.get("clientId") or ""
    if not who:
        return jsonify(error="חסר מזהה דפדפן"), 400
    if b.get("release"):
        store.drop_lease(ym, who)
        return jsonify(ok=True, released=True)
    ok, holder = store.take_lease(ym, who, b.get("name") or "")
    if not ok:
        return jsonify(ok=False, error=f"{holder.get('name') or 'משתמש אחר'} עורך כרגע",
                       holder=holder), 423
    return jsonify(ok=True, lease=holder, version=store.version_of(ym))


# =====================================================================
#                        month archive
# =====================================================================
@app.get("/api/months")
@require_auth
def api_months():
    months = store.list_months()
    for m in months:
        m["label"] = ym_label(m["ym"])
    return jsonify(months=months, current=current_ym())


@app.post("/api/months")
@require_auth
def api_create_month():
    b = request.get_json(silent=True) or {}
    ym = (b.get("ym") or "").strip()
    if not re.match(r"^\d{4}-\d{2}$", ym):
        return jsonify(error="פורמט חודש לא תקין (YYYY-MM)"), 400
    if store.get_month(ym):
        return jsonify(error="החודש כבר קיים"), 409
    store.create_month(ym, source_ym=b.get("sourceYm") or None,
                       copy_external=bool(b.get("copyExternal")),
                       copy_holidays=bool(b.get("copyHolidays")))
    store.set_setting("current_ym", ym)
    store.log("admin", "create_month", ym, f"source={b.get('sourceYm') or 'blank'}")
    return jsonify(ok=True, ym=ym, month=store.month_meta(ym))


@app.post("/api/months/<ym>/select")
@require_auth
def api_select_month(ym):
    if store.get_month(ym) is None:
        return jsonify(error="החודש לא נמצא"), 404
    store.set_setting("current_ym", ym)
    return jsonify(ok=True, ym=ym)


@app.get("/api/months/<ym>")
@require_auth
def api_get_month(ym):
    m = store.get_month(ym)
    if m is None:
        return jsonify(error="החודש לא נמצא"), 404
    m["label"] = ym_label(ym)
    m["hasXlsx"] = store.get_artifact(ym)[0] is not None
    return jsonify(month=m)


@app.delete("/api/months/<ym>")
@require_auth
def api_delete_month(ym):
    if store.is_frozen(ym):
        return jsonify(error="אי אפשר למחוק חודש מוקפא"), 409
    store.delete_month(ym)
    store.log("admin", "delete_month", ym)
    return jsonify(ok=True)


@app.post("/api/months/<ym>/assignment")
@require_auth
def api_save_assignment(ym):
    """Called by the editor right after a successful run, so the archive holds
    the result and not just the inputs."""
    if store.is_frozen(ym):
        return jsonify(error="החודש מוקפא — השיבוץ לא שונה"), 409
    b = request.get_json(silent=True) or {}
    if store.get_month(ym) is None:
        # Silently creating one here produced a month holding an assignment but
        # no roster: the admin froze it, and every intern was told they had no
        # schedule because none of them were in it.
        return jsonify(error=f"החודש {ym_label(ym)} לא קיים. צרו אותו בלשונית «חודשים» לפני יצירת שיבוץ."), 404
    store.patch_month(ym, assignment=b.get("assignments") or [],
                      stats=b.get("stats") or "", generated_at=store.now())
    if b.get("xlsx"):
        try:
            store.put_artifact(ym, f"schedule_{ym}.xlsx", base64.b64decode(b["xlsx"]))
        except Exception:                                          # noqa: BLE001
            pass
    store.log("admin", "save_assignment", ym, b.get("stats") or "")
    return jsonify(ok=True, month=store.month_meta(ym))


@app.post("/api/months/<ym>/freeze")
@require_auth
def api_freeze(ym):
    m = store.get_month(ym)
    if m is None:
        return jsonify(error="החודש לא נמצא"), 404
    want = bool((request.get_json(silent=True) or {}).get("frozen", True))
    if want and not m["assignment"]:
        return jsonify(error="אין שיבוץ להקפיא — יש ליצור שיבוץ קודם"), 400
    if want and not m["interns"]:
        return jsonify(error="אין מתמחים בחודש הזה — אף אחד לא יראה את הלוח"), 400
    store.set_status(ym, "frozen" if want else "draft")
    store.log("admin", "freeze" if want else "unfreeze", ym)
    return jsonify(ok=True, month=store.month_meta(ym))


@app.get("/api/months/<ym>/xlsx")
@require_auth
def api_month_xlsx(ym):
    name, blob = store.get_artifact(ym)
    if not blob:
        return jsonify(error="לא נשמר קובץ אקסל לחודש הזה"), 404
    return jsonify(filename=name, xlsx=base64.b64encode(blob).decode("ascii"))


@app.get("/api/codes")
@require_auth
def api_codes():
    return jsonify(codes=store.list_codes())


@app.post("/api/codes/<intern_id>/reset")
@require_auth
def api_reset_code(intern_id):
    code = store.reset_code(intern_id)
    store.log("admin", "reset_code", None, intern_id)
    return jsonify(ok=True, code=code)


@app.get("/api/audit")
@require_auth
def api_audit():
    return jsonify(entries=store.audit_tail(120))


@app.post("/generate")
@require_auth
def generate():
    year = request.form.get("year", "2026").strip()
    month = request.form.get("month", "7").strip()
    interns_file = request.files.get("interns")
    holidays_file = request.files.get("holidays")
    external_file = request.files.get("external")

    if interns_file is None or interns_file.filename == "":
        return jsonify(error="חסר קובץ מתמחים (interns CSV)"), 400
    if not year.isdigit() or not month.isdigit():
        return jsonify(error="שנה/חודש לא תקינים"), 400

    workdir = tempfile.mkdtemp(prefix="sched_")
    try:
        interns_path = os.path.join(workdir, "interns.csv")
        interns_file.save(interns_path)

        out_path = os.path.join(workdir, "schedule.xlsx")
        cmd = [sys.executable or "python3", SCHEDULER,
               interns_path, year, month, out_path]

        if holidays_file is not None and holidays_file.filename:
            holidays_path = os.path.join(workdir, "holidays.csv")
            holidays_file.save(holidays_path)
            cmd += ["--holidays", holidays_path]

        ext_data = {}
        if external_file is not None and external_file.filename:
            external_path = os.path.join(workdir, "external.csv")
            external_file.save(external_path)
            cmd += ["--external", external_path]
            with open(external_path, encoding="utf-8-sig") as ef:
                for row in csv.DictReader(ef):
                    try:
                        d = int(str(row.get("day", "")).strip())
                    except (ValueError, TypeError):
                        continue
                    st = (row.get("station") or "").strip()
                    nm = (row.get("name") or "").strip()
                    if st and nm:
                        ext_data[f"{d}|{st}"] = nm

        # mid-month re-optimization: base schedule + locks
        base_file = request.files.get("base")
        locks_file = request.files.get("locks")
        if base_file is not None and base_file.filename:
            base_path = os.path.join(workdir, "base.csv")
            base_file.save(base_path)
            cmd += ["--base", base_path, "--from", "1"]
        if locks_file is not None and locks_file.filename:
            locks_path = os.path.join(workdir, "locks.csv")
            locks_file.save(locks_path)
            cmd += ["--locks", locks_path]

        # constraint relaxations
        if request.form.get("relax_pair") == "1":
            cmd += ["--relax-pair", "1"]
        if request.form.get("relax_cross") == "1":
            cmd += ["--relax-cross", "1"]

        result = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, timeout=540
        )

        # parse diagnostics from stdout
        stdout = result.stdout or ""
        diagnostics = []
        notices = []          # things the admin should see but that are not failures
        balance = ""
        in_shortage = False
        in_empty = False
        for line in stdout.split("\n"):
            line = line.strip()
            if line == "DIAG_SHORTAGE_START":
                in_shortage = True; continue
            if line == "DIAG_SHORTAGE_END":
                in_shortage = False; continue
            if line == "DIAG_EMPTY_START":
                in_empty = True; continue
            if line == "DIAG_EMPTY_END":
                in_empty = False; continue
            if line.startswith("DIAG:") and (in_shortage or in_empty):
                diagnostics.append(line[len("DIAG:"):].strip())
            elif line.startswith("DIAG_CAPACITY:"):
                diagnostics.append(line[len("DIAG_CAPACITY:"):].strip())
            elif line.startswith("DIAG_NO_APPROVAL:"):
                diagnostics.append(line[len("DIAG_NO_APPROVAL:"):].strip())
            elif line.startswith("DIAG_BLOCKED:"):
                diagnostics.append(line[len("DIAG_BLOCKED:"):].strip())
            elif line.startswith("DIAG_PIN_CONFLICT:"):
                # an explicit pin that contradicts the same intern's ceilings
                notices.append(line[len("DIAG_PIN_CONFLICT:"):].strip())
            elif line.startswith("DIAG_BALANCE:"):
                balance = line[len("DIAG_BALANCE:"):].strip()
            elif line.startswith("WARN "):
                notices.append(line[len("WARN "):].strip())

        # detect failure: process crashed, or produced empty slots, or hard violations
        has_hard_failure = (result.returncode != 0 or not os.path.exists(out_path))
        has_empty_slots = "EMPTY" in stdout and "VIOLATION: EMPTY" in stdout

        # if the scheduler crashed with a Python exception, extract the exception line
        stderr = result.stderr or ""
        combined = stdout + "\n" + stderr
        crash_msg = None
        if has_hard_failure:
            # find last exception line (e.g. "KeyError: 'intern-12'")
            for line in reversed(combined.split("\n")):
                line = line.strip()
                if not line:
                    continue
                # typical Python exception line: "ExceptionType: message"
                if ":" in line and not line.startswith(("File ", " ", "~")):
                    parts = line.split(":", 1)
                    if parts[0].strip() and parts[0].strip()[0].isupper() and "Error" in parts[0]:
                        crash_msg = line
                        break

        # A crash leaves us with nothing. Unfilled slots do not: the solver still
        # produced its best attempt, and that partial schedule is exactly what
        # shows which constraint has to give. Returning only an error message
        # threw away the most useful artefact of the run.
        if has_hard_failure:
            log = stdout[-3000:] + "\n" + stderr[-3000:]
            if crash_msg:
                err_msg = f"תקלה בסקדולר: {crash_msg}"
            elif diagnostics:
                err_msg = "השיבוץ נכשל: " + " · ".join(diagnostics)
            else:
                err_msg = "השיבוץ נכשל"
            return jsonify(error=err_msg, diagnostics=diagnostics, log=log), 500

        # read assignments CSV
        asg_path = out_path.replace(".xlsx", "_assignments.csv")
        assignments = []
        if os.path.exists(asg_path):
            with open(asg_path, encoding="utf-8-sig") as af:
                for row in csv.DictReader(af):
                    assignments.append({
                        "day": int(row["day"]),
                        "station": row["station"],
                        "id": row["id"],
                        "name": row["name"],
                    })

        # Which slots came back empty? Everything the interns were meant to
        # cover, minus what the solver filled and what external staff cover.
        filled = {(a["day"], a["station"]) for a in assignments}
        covered = set()
        for key in ext_data:
            try:
                d, st = key.split("|")
                covered.add((int(d), st))
            except ValueError:
                continue
        ndays = calendar.monthrange(int(year), int(month))[1]
        gaps = [{"day": d, "station": st, "stationHe": STATION_HE.get(st, st)}
                for d in range(1, ndays + 1)
                for st in ["er1", "er2", "nicu1", "nicu2", "ward", "picu"]
                if (d, st) not in filled and (d, st) not in covered]

        # read xlsx as base64
        with open(out_path, "rb") as xf:
            xlsx_b64 = base64.b64encode(xf.read()).decode("ascii")

        # parse log for stats
        log = result.stdout or ""
        stats_line = ""
        for line in log.split("\n"):
            if "filled" in line and "hard-violations" in line:
                stats_line = line.strip()
                break

        return jsonify(
            assignments=assignments,
            external=ext_data,
            xlsx=xlsx_b64,
            partial=bool(gaps),
            gaps=gaps,
            diagnostics=diagnostics,
            notices=notices,
            balance=balance,
            year=int(year),
            month=int(month),
            stats=stats_line,
            log=log[-2000:],
        )
    except subprocess.TimeoutExpired:
        return jsonify(error="השיבוץ ארך יותר מדי זמן (timeout)"), 504
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# =====================================================================
#                          intern portal
# =====================================================================
# Served from this origin so it can use a session cookie. It never receives
# approvals, strengths, caps, notes, or anyone else's blocked days — those are
# not filtered in the browser, they are never put in the response.
@app.get("/portal/")
@app.get("/portal")
def portal_index():
    return send_from_directory(PORTAL_DIR, "portal.html")


@app.get("/portal/<path:filename>")
def portal_asset(filename):
    return send_from_directory(PORTAL_DIR, filename)


def _who():
    iid = session.get("intern_id")
    if not iid:
        return None
    row = store._con().execute(
        "SELECT intern_id, name FROM intern_codes WHERE intern_id=?", (iid,)).fetchone()
    return row


def portal_auth(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not _who():
            return jsonify(error="נדרשת התחברות"), 401
        return view(*a, **kw)
    return wrapped


@app.post("/api/portal/login")
def portal_login():
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip().upper()
    who = store.find_by_code(code) if code else None
    if not who:
        return jsonify(error="הקוד לא מזוהה. אפשר לקבל קוד חדש מרכז/ת השיבוץ."), 401
    session.permanent = True
    session["intern_id"] = who["intern_id"]
    store.log(who["intern_id"], "intern_login")
    return jsonify(ok=True, name=who["name"])


@app.post("/api/portal/logout")
def portal_logout():
    session.clear()
    return jsonify(ok=True)


def _visible(m):
    return m["status"] == "frozen" or SHOW_DRAFTS


@app.get("/api/portal/me")
@portal_auth
def portal_me():
    who = _who()
    months, skipped = [], 0
    for meta in store.list_months():
        m = store.get_month(meta["ym"])
        if not any((it.get("id") == who["intern_id"]) for it in m["interns"]):
            # a month this intern is not part of. Counted so the portal can say
            # "ask the coordinator" instead of implying nothing exists at all.
            if m["status"] == "frozen" or m["assignment"]:
                skipped += 1
            continue
        months.append({"ym": m["ym"], "label": ym_label(m["ym"]), "status": m["status"],
                       "canEdit": m["status"] != "frozen",
                       "published": _visible(m) and bool(m["assignment"])})
    return jsonify(name=who["name"], months=months, otherMonths=skipped)


@app.get("/api/portal/months/<ym>")
@portal_auth
def portal_month(ym):
    who = _who()
    m = store.get_month(ym)
    if m is None:
        return jsonify(error="החודש לא נמצא"), 404
    me = next((it for it in m["interns"] if it.get("id") == who["intern_id"]), None)
    if me is None:
        return jsonify(error="אינך משובץ בחודש הזה"), 403

    days = day_meta(ym, m["holidays"])
    prefix = ym + "-"
    editable = m["status"] != "frozen"
    out = {
        "month": {"ym": ym, "label": ym_label(ym), "status": m["status"],
                  "canEdit": editable, "frozenAt": m["frozen_at"]},
        "days": days,
        "me": {"name": me.get("name", ""),
               "blocked": [d for d in (me.get("blocked") or []) if d.startswith(prefix)],
               "preferred": [d for d in (me.get("preferred") or []) if d.startswith(prefix)],
               "updatedAt": me.get("_datesAt"), "updatedBy": me.get("_datesBy")},
        "schedule": None, "myShifts": [], "myCounts": None,
    }
    if _visible(m) and m["assignment"]:
        grid = {}
        for a in m["assignment"]:
            grid.setdefault(str(a["day"]), {})[a["station"]] = a.get("name") or ""
        ext_cells = []
        for key, name in (m["external"] or {}).items():
            try:
                d, st = key.split("|")
            except ValueError:
                continue
            grid.setdefault(str(int(d)), {})[st] = name
            ext_cells.append({"day": int(d), "station": st})
        dm = {d["day"]: d for d in days}
        shifts, fri, sat, wknd = [], 0, 0, 0
        for a in m["assignment"]:
            if a.get("id") != who["intern_id"]:
                continue
            d = dm.get(a["day"])
            if not d:
                continue
            shifts.append({"day": a["day"], "station": a["station"], "dowHe": d["dowHe"],
                           "isFri": d["isFri"], "isSat": d["isSat"],
                           "isWeekend": d["isWeekend"], "holiday": d["holiday"]})
            fri += d["isFri"]; sat += d["isSat"]; wknd += d["isWeekend"]
        shifts.sort(key=lambda x: x["day"])
        out["schedule"] = {"grid": grid, "externalCells": ext_cells}
        out["myShifts"] = shifts
        out["myCounts"] = {"total": len(shifts), "friday": fri, "saturday": sat,
                           "weekend": wknd, "weekday": len(shifts) - wknd}
    return jsonify(out)


@app.put("/api/portal/months/<ym>/dates")
@portal_auth
def portal_dates(ym):
    who = _who()
    m = store.get_month(ym)
    if m is None:
        return jsonify(error="החודש לא נמצא"), 404
    if m["status"] == "frozen":
        return jsonify(error="החודש מוקפא — לא ניתן לעדכן תאריכים"), 409
    b = request.get_json(silent=True) or {}
    prefix = ym + "-"
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    blocked = {d for d in (b.get("blocked") or []) if iso.match(d) and d.startswith(prefix)}
    preferred = {d for d in (b.get("preferred") or []) if iso.match(d) and d.startswith(prefix)}
    preferred -= blocked

    interns = m["interns"]
    hit = None
    for it in interns:
        if it.get("id") != who["intern_id"]:
            continue
        # days in other months stay untouched
        keep_b = [d for d in (it.get("blocked") or []) if not d.startswith(prefix)]
        keep_p = [d for d in (it.get("preferred") or []) if not d.startswith(prefix)]
        it["blocked"] = sorted(set(keep_b) | blocked)
        it["preferred"] = sorted(set(keep_p) | preferred)
        it["_datesBy"] = "intern"
        it["_datesAt"] = store.now()
        hit = it
        break
    if hit is None:
        return jsonify(error="אינך משובץ בחודש הזה"), 403
    store.patch_month(ym, interns=interns)
    store.log(who["intern_id"], "intern_dates", ym,
              f"{len(blocked)} blocked / {len(preferred)} preferred")
    return jsonify(ok=True,
                   blocked=[d for d in hit["blocked"] if d.startswith(prefix)],
                   preferred=[d for d in hit["preferred"] if d.startswith(prefix)])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 64)
    print("  שרת השיבוץ פועל")
    print("=" * 64)
    print(f"  מסך מנהל    : http://localhost:{port}/editor")
    print(f"  מסך מתמחה   : http://localhost:{port}/portal/")
    print(f"  בסיס נתונים : {store.engine_label()}")
    if not APP_PASSWORD:
        print("\n  ⚠  APP_PASSWORD לא הוגדר — ה-API פתוח לכל דורש.")
        print("     להרצה מקומית זה בסדר; לפני פריסה חובה להגדיר.")
    print("\n  לעצירה: Ctrl+C")
    print("=" * 64 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
