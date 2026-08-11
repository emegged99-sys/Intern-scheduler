# -*- coding: utf-8 -*-
"""Drives the same request sequence the editor's JavaScript makes, so the
frontend/backend contract is checked without a browser: load state, edit, run,
archive the result, switch months, freeze, and read an archived month back.
"""
import base64
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ["SCHED_DB"] = os.path.join(TMP, "editor.db")
os.environ["APP_PASSWORD"] = "pw"
os.environ["SECRET_KEY"] = "t"
sys.path.insert(0, ROOT)

import app as backend                                              # noqa: E402

FAILS = []
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def call(c, method, path, body=None):
    kw = {"headers": AUTH}
    if body is not None:
        kw["json"] = body
    r = getattr(c, method.lower())(path, **kw)
    return r.status_code, (r.get_json() or {})


def main():
    backend.app.config["TESTING"] = True
    c = backend.app.test_client()

    print("\n[the editor's own JavaScript]")
    src = open(os.path.join(ROOT, "intern_editor.html"), encoding="utf-8").read()
    js = "\n".join(re.findall(r"<script>(.*?)</script>", src, re.S))
    for fn in ("srvApi", "renderMonths", "selectMonth", "refreshMonths",
               "archiveResult", "renderCodes", "paintMonthChip", "openArchived"):
        check(f"{fn} defined once", len(re.findall(r"(?:async function|function|const) " + fn + r"\b", js)) == 1)
    check("months tab wired", 'id="tabMonths"' in src and '"months"' in js)
    check("freeze button wired", 'id="freezeBtn"' in src and "/freeze" in js)
    calls = len(re.findall(r"(?<!function )archiveResult\(data\)", js))
    check("archives after both run paths", calls == 2, calls)
    check("month chip in the header", 'id="monthChip"' in src)
    check("no leftover calls to the old helper name",
          not re.search(r"[^v]\bapi\(", js.replace("srvApi(", "")))

    print("\n[boot: what the editor asks for on load]")
    st, r = call(c, "GET", "/api/state/interns")
    check("GET /api/state/interns", st == 200 and "data" in r)
    st, r = call(c, "GET", "/api/months")
    check("GET /api/months returns a current month", st == 200 and r["current"], r)
    ym = r["current"]
    check("current month is this month", ym == backend._default_ym(), ym)

    print("\n[edit and save, exactly as the editor does]")
    interns = [{"id": "intern-1", "name": "אפרת", "active": True, "religion": "jewish",
                "preferredStationId": "", "notes": "", "maxTotalShifts": "5",
                "minTotalShifts": "", "maxFridayShifts": "", "maxSaturdayShifts": "",
                "maxWeekendShifts": "", "maxSandwiches": "", "blocked": [], "preferred": [],
                "approved": {s: True for s in ["er1", "er2", "nicu1", "nicu2", "ward", "picu"]},
                "strength": {s: "3" for s in ["er1", "er2", "nicu1", "nicu2", "ward", "picu"]},
                "requests": [{"type": "pin", "day": 3, "station": "er1"}]}]
    for key, val in (("interns", interns), ("external", {"2|ward": "חוץ"}),
                     ("holidays", {ym + "-15": {"kind": "eve", "name": "ערב חג"}})):
        st, _ = call(c, "POST", f"/api/state/{key}", val)
        check(f"save {key}", st == 200)
    st, r = call(c, "GET", "/api/state/interns")
    check("special requests survive the round trip",
          r["data"][0]["requests"][0]["type"] == "pin", r["data"][0].get("requests"))
    check("minTotalShifts / maxSandwiches fields survive",
          "minTotalShifts" in r["data"][0] and "maxSandwiches" in r["data"][0])

    print("\n[archive the result of a run]")
    data = {"assignments": [{"day": 3, "station": "er1", "id": "intern-1", "name": "אפרת"}],
            "stats": "filled 1/1 | hard-violations 0",
            "xlsx": base64.b64encode(b"PK\x03\x04zz").decode()}
    st, r = call(c, "POST", f"/api/months/{ym}/assignment", data)
    check("archiveResult() endpoint accepts it", st == 200, r)
    st, r = call(c, "GET", "/api/months")
    me = [m for m in r["months"] if m["ym"] == ym][0]
    check("archive shows the assignment", me["assigned"] == 1, me)
    check("archive shows the workbook", me["hasXlsx"] is True)

    print("\n[switch months, the way the months tab does]")
    nxt = "2027-01"
    st, _ = call(c, "POST", "/api/months", {"ym": nxt, "sourceYm": ym, "copyExternal": True})
    check("create + inherit roster", st == 200)
    st, r = call(c, "GET", "/api/state/interns")
    check("selecting the new month switches the editor's state",
          len(r["data"]) == 1 and r["ym"] == nxt, r.get("ym"))
    check("inherited roster keeps caps and requests",
          r["data"][0]["maxTotalShifts"] == "5" and r["data"][0]["requests"], r["data"][0])
    check("inherited month starts with no dates",
          r["data"][0]["blocked"] == [] and r["data"][0]["preferred"] == [])
    st, r = call(c, "GET", "/api/state/external")
    check("external copied when asked", r["data"] == {"2|ward": "חוץ"}, r.get("data"))
    st, r = call(c, "GET", "/api/state/holidays")
    check("holidays NOT copied when not asked", r["data"] == {}, r.get("data"))

    st, _ = call(c, "POST", f"/api/months/{ym}/select")
    st, r = call(c, "GET", "/api/state/interns")
    check("switching back restores the first month", r["ym"] == ym)

    print("\n[freeze from the editor]")
    st, r = call(c, "POST", f"/api/months/{ym}/freeze", {"frozen": True})
    check("freeze accepted", st == 200 and r["month"]["status"] == "frozen", r)
    st, r = call(c, "POST", "/api/state/interns", interns)
    check("editor cannot save into a frozen month", st == 409, r)
    check("and is told why", "מוקפא" in r.get("error", ""), r)
    st, r = call(c, "POST", f"/api/months/{ym}/assignment", data)
    check("a run cannot overwrite a frozen archive", st == 409)

    print("\n[read an archived month back — no re-solving]")
    st, r = call(c, "GET", f"/api/months/{ym}")
    m = r["month"]
    check("openArchived() gets assignment + external + stats",
          m["assignment"] and m["external"] and m["stats"], list(m.keys()))
    check("it is the frozen one", m["status"] == "frozen")
    st, r = call(c, "GET", f"/api/months/{ym}/xlsx")
    check("workbook downloads from the archive",
          base64.b64decode(r["xlsx"]) == b"PK\x03\x04zz")

    print("\n[landing page]")
    r = c.get("/")
    check("root serves a page, not a 404", r.status_code == 200)
    body = r.data.decode()
    check("it links to the portal", "/portal/" in body)
    check("it shows the address to paste into the editor", "intern_editor.html" in body)
    check("it lists the archive", "חודשים בארכיון" in body)
    check("it links to the editor", "/editor" in body)

    r = c.get("/editor")
    check("editor served by the backend", r.status_code == 200)
    check("and it is the real editor", "עורך נתוני שיבוץ" in r.data.decode())
    check("legacy filename also works", c.get("/intern_editor.html").status_code == 200)
    check("editor defaults to its own origin when served",
          "location.origin" in js)

    print("\n[portal is reachable from the same origin]")
    r = c.get("/portal/")
    check("portal page served", r.status_code == 200 and b"code" in r.data)
    check("portal html is self-contained",
          b"<script>" in r.data and b"src=" not in r.data.split(b"<script>")[0][-200:])

    print("\n" + ("ALL CHECKS PASSED" if not FAILS else "FAILURES: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
