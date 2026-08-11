# -*- coding: utf-8 -*-
"""End-to-end check: month archive, freeze/publish, intern portal, privacy.

Uses Flask's test client, so it runs without a server and without network.
"""
import base64
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ["SCHED_DB"] = os.path.join(TMP, "test.db")
os.environ["APP_PASSWORD"] = "pw"
os.environ["SECRET_KEY"] = "test-secret"
sys.path.insert(0, ROOT)

import app as backend                                              # noqa: E402
import store                                                       # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


def admin(c, method, path, body=None):
    fn = getattr(c, method.lower())
    kw = {"headers": AUTH}
    if body is not None:
        kw["json"] = body
    r = fn(path, **kw)
    return r.status_code, (r.get_json() or {})


def make_interns():
    def one(iid, name):
        return {"id": iid, "name": name, "active": True, "religion": "jewish",
                "preferredStationId": "", "notes": "", "maxTotalShifts": "",
                "minTotalShifts": "", "maxFridayShifts": "", "maxSaturdayShifts": "",
                "maxWeekendShifts": "", "maxSandwiches": "",
                "blocked": [], "preferred": [],
                "approved": {s: True for s in ["er1", "er2", "nicu1", "nicu2", "ward", "picu"]},
                "strength": {s: "3" for s in ["er1", "er2", "nicu1", "nicu2", "ward", "picu"]},
                "requests": []}
    return [one("intern-1", "אפרת"), one("intern-2", "ליאת"), one("intern-3", "מוחמד")]


def main():
    backend.app.config["TESTING"] = True
    c = backend.app.test_client()

    print("\n[auth]")
    check("admin API rejects no password", c.get("/api/months").status_code == 401)
    check("health is open", c.get("/health").status_code == 200)
    st, _ = admin(c, "GET", "/api/months")
    check("admin API accepts the password", st == 200)

    print("\n[the editor's existing endpoints still work]")
    # a month for the current date is auto-created on first use, so 2026-08 may
    # already be there; either way it must exist and be usable afterwards
    st, r = admin(c, "POST", "/api/months", {"ym": "2026-08"})
    check("month available", st in (200, 409), (st, r))
    admin(c, "POST", "/api/months/2026-08/select")
    interns = make_interns()
    st, _ = admin(c, "POST", "/api/state/interns", interns)
    check("POST /api/state/interns (unchanged URL)", st == 200)
    st, _ = admin(c, "POST", "/api/state/external", {"3|ward": "דינא"})
    check("POST /api/state/external", st == 200)
    st, _ = admin(c, "POST", "/api/state/holidays",
                  {"2026-08-21": {"kind": "eve", "name": "ערב חג"},
                   "2026-08-22": {"kind": "day", "name": "חג"}})
    check("POST /api/state/holidays", st == 200)
    st, r = admin(c, "GET", "/api/state/interns")
    check("GET returns what was written", len(r["data"]) == 3 and r["ym"] == "2026-08", r.get("ym"))
    check("state is scoped to a month now", r["status"] == "draft")

    print("\n[archive: a second month is independent]")
    st, r = admin(c, "POST", "/api/months", {"ym": "2026-09", "sourceYm": "2026-08"})
    check("september created from august", st == 200, r)
    st, _ = admin(c, "POST", "/api/months", {"ym": "2026-09"})
    check("creating the same month twice is refused", st == 409)
    st, r = admin(c, "GET", "/api/months/2026-09")
    sep = r["month"]
    check("roster carried over", len(sep["interns"]) == 3)
    check("dates start empty", all(not it["blocked"] and not it["preferred"] for it in sep["interns"]))
    check("external NOT copied by default", sep["external"] == {})
    st, r = admin(c, "GET", "/api/months/2026-08")
    check("august still has its own external", r["month"]["external"] == {"3|ward": "דינא"})
    check("august holidays intact", len(r["month"]["holidays"]) == 2)

    # editing september must not touch august
    admin(c, "POST", "/api/months/2026-09/select")
    st, _ = admin(c, "POST", "/api/state/external", {"5|picu": "חוץ ספטמבר"})
    st, r = admin(c, "GET", "/api/months/2026-08")
    check("editing september left august alone", r["month"]["external"] == {"3|ward": "דינא"})

    print("\n[assignment is archived, not regenerated]")
    admin(c, "POST", "/api/months/2026-08/select")
    assignment = [{"day": 1, "station": "er1", "id": "intern-1", "name": "אפרת"},
                  {"day": 1, "station": "er2", "id": "intern-2", "name": "ליאת"},
                  {"day": 2, "station": "er1", "id": "intern-3", "name": "מוחמד"},
                  {"day": 22, "station": "ward", "id": "intern-1", "name": "אפרת"}]
    fake_xlsx = b"PK\x03\x04" + b"x" * 500
    st, r = admin(c, "POST", "/api/months/2026-08/assignment",
                  {"assignments": assignment, "stats": "filled 4/4 | hard-violations 0",
                   "xlsx": base64.b64encode(fake_xlsx).decode()})
    check("assignment saved", st == 200, r)
    st, r = admin(c, "GET", "/api/months/2026-08")
    check("assignment reads back", len(r["month"]["assignment"]) == 4)
    check("stats kept", "hard-violations 0" in r["month"]["stats"])
    st, r = admin(c, "GET", "/api/months/2026-08/xlsx")
    check("workbook archived byte-for-byte",
          base64.b64decode(r["xlsx"]) == fake_xlsx, len(r.get("xlsx", "")))

    print("\n[intern portal]")
    st, r = admin(c, "GET", "/api/codes")
    codes = {x["intern_id"]: x["code"] for x in r["codes"]}
    check("a code per intern", len(codes) == 3, codes)
    check("codes are unique", len(set(codes.values())) == 3)

    p = backend.app.test_client()
    check("portal page is served", p.get("/portal/").status_code == 200)
    check("portal API needs a session", p.get("/api/portal/me").status_code == 401)
    check("wrong code rejected",
          p.post("/api/portal/login", json={"code": "ZZZZZZ"}).status_code == 401)
    r = p.post("/api/portal/login", json={"code": codes["intern-1"]})
    check("code signs the intern in", r.status_code == 200 and r.get_json()["name"] == "אפרת")
    check("intern cannot reach the admin API", p.get("/api/months").status_code == 401)

    r = p.get("/api/portal/months/2026-08")
    d = r.get_json()
    check("draft schedule stays hidden", d["schedule"] is None)
    check("dates are editable on a draft", d["month"]["canEdit"] is True)
    blob = json.dumps(d, ensure_ascii=False)
    check("no approvals or strengths in the payload",
          "approved" not in blob and "strength" not in blob)
    check("no caps or notes in the payload",
          "maxTotalShifts" not in blob and "specialRequests" not in blob)
    check("no other intern is named", "ליאת" not in blob and "מוחמד" not in blob)
    check("holiday eve counts as friday", d["days"][20]["isFri"] is True)

    r = p.put("/api/portal/months/2026-08/dates",
              json={"blocked": ["2026-08-06", "2026-09-01"], "preferred": ["2026-08-14"]})
    d2 = r.get_json()
    check("dates saved", r.status_code == 200, d2)
    check("other months ignored", d2["blocked"] == ["2026-08-06"], d2["blocked"])
    check("preferred saved", d2["preferred"] == ["2026-08-14"])
    st, r = admin(c, "GET", "/api/state/interns")
    me = [i for i in r["data"] if i["id"] == "intern-1"][0]
    check("the editor sees what the intern entered", me["blocked"] == ["2026-08-06"])
    check("and who entered it", me["_datesBy"] == "intern")
    st, r = admin(c, "GET", "/api/months/2026-09")
    sep_me = [i for i in r["month"]["interns"] if i["id"] == "intern-1"][0]
    check("september untouched by an august edit", sep_me["blocked"] == [])

    print("\n[freeze]")
    st, r = admin(c, "POST", "/api/months/2026-09/freeze", {"frozen": True})
    check("cannot freeze without an assignment", st == 400, r)
    st, r = admin(c, "POST", "/api/months/2026-08/freeze", {"frozen": True})
    check("august frozen", st == 200 and r["month"]["status"] == "frozen", r)
    st, r = admin(c, "POST", "/api/state/interns", interns)
    check("editing a frozen month is refused", st == 409, r)
    st, r = admin(c, "POST", "/api/months/2026-08/assignment", {"assignments": []})
    check("overwriting a frozen assignment is refused", st == 409)
    st, r = admin(c, "DELETE", "/api/months/2026-08")
    check("deleting a frozen month is refused", st == 409)
    r = p.put("/api/portal/months/2026-08/dates", json={"blocked": [], "preferred": []})
    check("intern date edits refused once frozen", r.status_code == 409)

    d = p.get("/api/portal/months/2026-08").get_json()
    check("frozen month is published to the intern", d["schedule"] is not None)
    check("intern sees only their own shifts",
          [s["day"] for s in d["myShifts"]] == [1, 22], d["myShifts"])
    check("counts add up",
          d["myCounts"]["total"] == 2 and
          d["myCounts"]["weekday"] + d["myCounts"]["weekend"] == 2, d["myCounts"])
    check("the full table shows everyone",
          d["schedule"]["grid"]["1"]["er2"] == "ליאת")
    check("external duty appears in the table",
          d["schedule"]["grid"]["3"]["ward"] == "דינא")
    # 1/8 and 22/8 are both Saturdays in 2026; 22/8 is also marked a holiday
    check("saturdays counted", d["myCounts"]["saturday"] == 2, d["myCounts"])
    check("holiday label reaches the intern",
          any(s.get("holiday") == "חג" for s in d["myShifts"]), d["myShifts"])
    blob = json.dumps(d, ensure_ascii=False)
    check("still no constraints leaked once published", "approved" not in blob)

    print("\n[unfreeze and archive integrity]")
    st, r = admin(c, "POST", "/api/months/2026-08/freeze", {"frozen": False})
    check("unfreeze works", st == 200 and r["month"]["status"] == "draft")
    st, r = admin(c, "GET", "/api/months/2026-08")
    check("assignment survived the freeze cycle", len(r["month"]["assignment"]) == 4)
    st, r = admin(c, "GET", "/api/months")
    check("both months listed", {m["ym"] for m in r["months"]} == {"2026-08", "2026-09"})
    check("archive reports what it holds",
          [m for m in r["months"] if m["ym"] == "2026-08"][0]["hasXlsx"] is True)

    print("\n[migration of the previous single-state files]")
    legacy = tempfile.mkdtemp()
    with open(os.path.join(legacy, "interns.json"), "w", encoding="utf-8") as f:
        json.dump(make_interns(), f, ensure_ascii=False)
    with open(os.path.join(legacy, "external.json"), "w", encoding="utf-8") as f:
        json.dump({"7|ward": "ישן"}, f, ensure_ascii=False)
    store.set_setting("legacy_migrated", "")
    store._con().execute("DELETE FROM settings WHERE k=?", ("legacy_migrated",))
    got = store.migrate_legacy(legacy, "2026-12")
    check("legacy files adopted into a month", got == "2026-12", got)
    m = store.get_month("2026-12")
    check("legacy interns preserved", len(m["interns"]) == 3)
    check("legacy external preserved", m["external"] == {"7|ward": "ישן"})
    check("migration runs only once", store.migrate_legacy(legacy, "2026-11") is None)

    print("\n" + ("ALL CHECKS PASSED" if not FAILS else "FAILURES: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
