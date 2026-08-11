# Start here

Complete guide: what's in the package, how to run it, what to upload where, and
which URL opens what.

---

## What changed from your previous version

You used to run **two things**: a backend on Render and an editor on Netlify,
with the server address typed into the editor by hand.

Now **one service serves everything**. Render hands out the admin screen, the
intern screen, and the API. Everything goes to GitHub → Render, and there is
nothing left to upload to Netlify.

---

## Part 1 — The files

### Required to run

| File | What it is |
|---|---|
| `app.py` | The server: API, admin screen, intern screen |
| `store.py` | Data layer: month archive, access codes, freeze state |
| `monthly_scheduler.py` | The scheduling engine |
| `intern_editor.html` | Admin screen |
| `portal/portal.html` | Intern screen — **must stay inside the `portal` folder** |
| `requirements.txt` | Package list the server installs |
| `Procfile` | Start command for Render |
| `.python-version` | Python version for Render (3.12.7) |

### Also in the package (not needed to run)

`START_HERE.md` · `INSTALL.md` · `PUBLISH.md` · `README.md` ·
`README_PORTAL.md` · `.gitignore` · `tests/` (two automated test suites)

### Folder structure

```
scheduler/
├── app.py
├── store.py
├── monthly_scheduler.py
├── intern_editor.html
├── portal/
│   └── portal.html
├── requirements.txt
├── Procfile
├── .python-version
└── tests/
```

**Don't move `portal/portal.html`** or flatten the folder — the server looks
for it there.

---

## Part 2 — Running on your computer (Windows)

### 1. Extract

Right-click the ZIP → **Properties** → tick **Unblock** → **Apply** →
**Extract All**, to something like `C:\Users\<name>\scheduler`.

(Unblock is needed because Windows blocks files downloaded from the internet.)

### 2. Install the packages

```powershell
cd C:\Users\<name>\scheduler
python -m pip install flask flask-cors openpyxl
```

**Don't** run `-r requirements.txt` on Windows: that file is for the server and
includes `gunicorn` (Linux only) and `psycopg`. If either fails, pip installs
nothing at all — including flask.

### 3. Start it

```powershell
$env:APP_PASSWORD='pick-a-password'
python app.py
```

In PowerShell it's `$env:` — not `export`, not `set`. The variable only lives in
that window, so set it again if you open a new terminal.

### 4. The URLs

| Screen | Address |
|---|---|
| **Admin** | `http://localhost:5000/editor` |
| **Intern** | `http://localhost:5000/portal/` |
| Home | `http://localhost:5000` |

The first time you open the admin screen it asks for the **server password** —
the one from step 3. **There is no username.** It's saved in your browser.

From another device on the same network (a phone, another PC), replace
`localhost` with your machine's IP, e.g. `http://10.100.102.28:5000/portal/`.

**Leave the PowerShell window open** — closing it stops the server.

---

## Part 3 — Publishing (free)

### Step 0 — Back up first ⚠

Open your **current** editor, wait for it to load from the server, click
**💾 גיבוי** and save the file. Your data currently sits as files on Render's
disk, and deploying wipes that disk.

### Step 1 — Database (Neon)

1. [neon.tech](https://neon.tech) → **New project** → region
   **AWS eu-central-1 (Frankfurt)**.
2. Copy the **Connection string**.

Free tier: 0.5 GB, no expiry, no credit card. A full month of data, workbook
included, is under 1 MB.

### Step 2 — Push everything to GitHub

From the project folder:

```powershell
git add -A
git commit -m "portal, freeze, month archive"
git push -u origin feature/portal-lock-archive
```

That's it — **this is the only upload**. Every file reaches Render from here.

### Step 3 — Environment variables on Render

Your service → **Environment** → add these (`APP_PASSWORD` already exists):

| Key | Value | Why |
|---|---|---|
| `DATABASE_URL` | the Neon string | without it your data is wiped on every deploy |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` | without it every intern is logged out on each restart |
| `SECURE_COOKIES` | `1` | cookie only over HTTPS |

### Step 4 — Deploy

**Settings** → **Branch:** `feature/portal-lock-archive` →
**Manual Deploy** → **Deploy latest commit**. Takes 2–4 minutes.

### Step 5 — The live URLs

| Screen | Address |
|---|---|
| **Admin** | `https://<service>.onrender.com/editor` |
| **Intern** | `https://<service>.onrender.com/portal/` |
| Health check | `https://<service>.onrender.com/health` |

On the home page, under "בסיס נתונים", it must say **Postgres**. If it says
SQLite, `DATABASE_URL` didn't take — and your data will vanish on the next
deploy.

### Step 6 — Restore your data

Admin screen → **📂 שחזור** → the backup file from step 0.

### Step 7 — Netlify

**Nothing to upload there.** The admin screen comes from the server now. Delete
the old site, or at least stop using it — otherwise someone opens it out of
habit and works in a stale editor with no "חודשים" tab.

---

## Part 4 — What to send the interns

Admin screen → **חודשים** tab → **קודי גישה**. Each intern has a permanent
6-character code.

> Hi — from now on shift preferences go here:
> https://\<service\>.onrender.com/portal/
> Your personal code: XXXXXX
> You can mark days you can't work and days you'd prefer. Once the schedule is
> finalised you'll see the full table and your own shifts there.

**Never send them `/editor`** — that's the full admin screen.

---

## Part 5 — Monthly routine

1. **חודשים** → **+ חודש חדש**, based on the previous month.
2. Update external duties and holidays.
3. Tell the interns date entry is open.
4. **יצירת שיבוץ** → **צור שיבוץ** (3–6 minutes in the cloud — leave the
   window open).
5. Review, adjust, re-run if needed.
6. **חודשים** → **🔒 הקפאת השיבוץ** — this publishes it to the interns.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: flask` | Run `python -m pip install flask flask-cors openpyxl` |
| `export : not recognized` | PowerShell syntax: `$env:APP_PASSWORD='...'` |
| Browser pops up a username/password box | Old `app.py`. The new one prompts in-app, no username |
| `Not Found` on the server address | Old `app.py`. The new one has `/`, `/editor`, `/portal/` |
| Intern screen 404s | `portal/portal.html` isn't where it belongs |
| "חודשים" tab missing | You opened the old Netlify editor. Use `/editor` |
| Home page says SQLite in the cloud | `DATABASE_URL` missing |
| Interns keep getting logged out | `SECRET_KEY` missing |
| Build fails on `psycopg` | `.python-version` didn't reach GitHub |
| Scheduling fails with code 9009 | Old `app.py` |
| "החודש מוקפא" on every save | Working as intended. **חודשים** → unfreeze |

---

## Verifying the install

```powershell
python tests\test_portal_archive.py
python tests\test_editor_contract.py
```

Both should end with `ALL CHECKS PASSED`. They run without a server and without
internet access.
