# -*- coding: utf-8 -*-
"""Web backend for the monthly on-call scheduler.
POST /generate → JSON with schedule data + base64 xlsx.
GET/POST /api/state/<key> → persistent file-based storage for editor state.

Run locally:   python3 app.py            (http://localhost:5000)
Run in prod:   gunicorn app:app --timeout 600 --workers 1
"""
import base64
import csv
import hmac
import json
import os
import shutil
import subprocess
import tempfile
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULER = os.path.join(BASE_DIR, "monthly_scheduler.py")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

ALLOWED_KEYS = {"interns", "external", "holidays"}


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not APP_PASSWORD:
            return view(*args, **kwargs)
        auth = request.authorization
        supplied = auth.password if auth else ""
        if not auth or not hmac.compare_digest(supplied, APP_PASSWORD):
            return jsonify(error="נדרשת סיסמה"), 401, {
                "WWW-Authenticate": 'Basic realm="Scheduler"'
            }
        return view(*args, **kwargs)
    return wrapped


@app.get("/health")
def health():
    return jsonify(status="ok")


# ---- file-based state storage ----
@app.get("/api/state/<key>")
@require_auth
def get_state(key):
    if key not in ALLOWED_KEYS:
        return jsonify(error="Invalid key"), 400
    path = os.path.join(DATA_DIR, f"{key}.json")
    if not os.path.exists(path):
        return jsonify(data=None)
    with open(path, encoding="utf-8") as f:
        return jsonify(data=json.load(f))


@app.post("/api/state/<key>")
@require_auth
def save_state(key):
    if key not in ALLOWED_KEYS:
        return jsonify(error="Invalid key"), 400
    data = request.get_json()
    path = os.path.join(DATA_DIR, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return jsonify(ok=True)


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
        cmd = ["python3", SCHEDULER, interns_path, year, month, out_path]

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

        if has_hard_failure or has_empty_slots:
            log = stdout[-3000:] + "\n" + stderr[-3000:]
            if crash_msg:
                err_msg = f"תקלה בסקדולר: {crash_msg}"
            elif diagnostics:
                reason = " · ".join(diagnostics)
                err_msg = f"השיבוץ נכשל: {reason}"
            elif has_empty_slots:
                err_msg = "השיבוץ נכשל: לא ניתן היה למלא את כל המשבצות בהינתן האילוצים"
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
            year=int(year),
            month=int(month),
            stats=stats_line,
            log=log[-2000:],
        )
    except subprocess.TimeoutExpired:
        return jsonify(error="השיבוץ ארך יותר מדי זמן (timeout)"), 504
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
