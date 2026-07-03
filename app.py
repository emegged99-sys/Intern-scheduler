# -*- coding: utf-8 -*-
"""Web backend for the monthly on-call scheduler.
Exposes POST /generate which takes the interns CSV (and optional
holidays/external CSVs) + year/month, runs monthly_scheduler.py, and
streams back the generated .xlsx file.

Run locally:   python3 app.py            (http://localhost:5000)
Run in prod:   gunicorn app:app --timeout 300 --workers 1
"""
import hmac
import os
import shutil
import subprocess
import tempfile
from functools import wraps

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow the editor (hosted on a different domain) to call this API

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULER = os.path.join(BASE_DIR, "monthly_scheduler.py")

# Shared-secret password, set as an env var on the hosting platform (never
# commit it to git). If APP_PASSWORD is unset, auth is disabled (open access) —
# useful for local testing, but set it before deploying anywhere public.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


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

        if external_file is not None and external_file.filename:
            external_path = os.path.join(workdir, "external.csv")
            external_file.save(external_path)
            cmd += ["--external", external_path]

        result = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, timeout=540
        )

        if result.returncode != 0 or not os.path.exists(out_path):
            log = (result.stdout or "")[-3000:] + "\n" + (result.stderr or "")[-3000:]
            return jsonify(error="השיבוץ נכשל", log=log), 500

        download_name = f"schedule_{year}_{month.zfill(2)}.xlsx"
        return send_file(
            out_path,
            as_attachment=True,
            download_name=download_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except subprocess.TimeoutExpired:
        return jsonify(error="השיבוץ ארך יותר מדי זמן (timeout)"), 504
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
