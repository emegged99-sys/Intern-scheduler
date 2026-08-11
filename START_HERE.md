# מדריך מלא — התקנה, העלאה, וכתובות

## שינוי חשוב מהגרסה הקודמת

קודם היו לכם **שני מקומות**: שרת ב-Render ועורך ב-Netlify, וצריך היה להזין
לעורך את כתובת השרת.

עכשיו **הכול יושב במקום אחד**. השרת מגיש גם את מסך המנהל וגם את מסך המתמחה.
כל הקבצים הולכים ל-GitHub → Render, ואין יותר מה להעלות ל-Netlify.

---

## חלק 1 — הקבצים

### קבצים שנדרשים להרצה

| קובץ | מה זה |
|---|---|
| `app.py` | השרת: ה-API, מסך המנהל ומסך המתמחה |
| `store.py` | שכבת הנתונים: ארכיון החודשים, קודי גישה, הקפאה |
| `monthly_scheduler.py` | מנוע השיבוץ |
| `intern_editor.html` | מסך המנהל |
| `portal/portal.html` | מסך המתמחה — **חייב להישאר בתיקייה `portal`** |
| `requirements.txt` | רשימת החבילות שהשרת מתקין |
| `Procfile` | פקודת ההפעלה ב-Render |
| `.python-version` | גרסת פייתון ב-Render (3.12.7) |

### קבצים נוספים בחבילה (לא נדרשים להרצה)

`INSTALL.md` · `PUBLISH.md` · `README.md` · `README_PORTAL.md` ·
`.gitignore` · `tests/` (שתי בדיקות אוטומטיות)

### מבנה התיקייה

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

**אל תזיזו את `portal/portal.html`** ואל תשטחו את התיקייה — השרת מחפש אותו שם.

---

## חלק 2 — הרצה על המחשב (Windows)

### 1. חילוץ
קליק ימני על ה-ZIP → **Properties** → **Unblock** → **Apply** → **Extract All**,
למשל ל-`C:\Users\<שם>\scheduler`.

### 2. התקנת חבילות
```powershell
cd C:\Users\<שם>\scheduler
python -m pip install flask flask-cors openpyxl
```
**לא** `-r requirements.txt` — הוא מיועד לשרת ומכיל `gunicorn` (לינוקס בלבד)
ו-`psycopg`, ואם אחד מהם נופל שום דבר לא מותקן.

### 3. הפעלה
```powershell
$env:APP_PASSWORD='בחרו-סיסמה'
python app.py
```
(ב-PowerShell זה `$env:` — לא `export` ולא `set`.)

### 4. הכתובות

| מסך | כתובת |
|---|---|
| **מנהל** | `http://localhost:5000/editor` |
| **מתמחה** | `http://localhost:5000/portal/` |
| עמוד בית | `http://localhost:5000` |

בכניסה הראשונה למסך המנהל יופיע חלון שמבקש **סיסמת שרת** — זו הסיסמה מסעיף 3.
**אין שם משתמש.** הסיסמה נשמרת בדפדפן.

ממכשיר אחר באותה רשת (טלפון, מחשב אחר) — החליפו `localhost` ב-IP של המחשב,
למשל `http://10.100.102.28:5000/portal/`.

**השאירו את חלון ה-PowerShell פתוח** — סגירתו עוצרת את השרת.

---

## חלק 3 — העלאה לאוויר (חינם)

### שלב 0 — גיבוי ⚠
פתחו את העורך **הישן** שלכם, המתינו שייטען מהשרת, לחצו **💾 גיבוי** ושמרו את
הקובץ. הנתונים שלכם כרגע קבצים על דיסק Render, והדיפלוי מוחק אותו.

### שלב 1 — בסיס נתונים (Neon)
1. [neon.tech](https://neon.tech) → **New project** → אזור
   **AWS eu-central-1 (Frankfurt)**.
2. העתיקו את ה-**Connection string**.

### שלב 2 — כל הקבצים ל-GitHub
מהתיקייה שחילצתם:
```powershell
git add -A
git commit -m "portal, freeze, month archive"
git push -u origin feature/portal-lock-archive
```
זהו — **זו ההעלאה היחידה**. כל הקבצים מגיעים ל-Render מכאן.

### שלב 3 — משתני סביבה ב-Render
בשירות → **Environment** → הוסיפו (`APP_PASSWORD` כבר קיים):

| Key | Value |
|---|---|
| `DATABASE_URL` | המחרוזת מ-Neon |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SECURE_COOKIES` | `1` |

### שלב 4 — דיפלוי
**Settings** → **Branch:** `feature/portal-lock-archive` →
**Manual Deploy** → **Deploy latest commit**. 2–4 דקות.

### שלב 5 — הכתובות באוויר

| מסך | כתובת |
|---|---|
| **מנהל** | `https://<השירות>.onrender.com/editor` |
| **מתמחה** | `https://<השירות>.onrender.com/portal/` |
| בדיקת תקינות | `https://<השירות>.onrender.com/health` |

בעמוד הבית, תחת «בסיס נתונים», חייב להופיע **Postgres**. אם כתוב SQLite —
`DATABASE_URL` לא נקלט, והנתונים יימחקו בדיפלוי הבא.

### שלב 6 — שחזור
במסך המנהל → **📂 שחזור** → קובץ הגיבוי משלב 0.

### שלב 7 — Netlify
**אין מה להעלות לשם.** מסך המנהל מגיע מהשרת. אפשר למחוק את האתר הישן, או
לפחות להפסיק להשתמש בו — אחרת מישהו יפתח מתוך הרגל גרסה ישנה בלי לשונית
«חודשים» ויעבוד על נתונים לא מסונכרנים.

---

## חלק 4 — מה שולחים למתמחים

מסך המנהל → לשונית **חודשים** → **קודי גישה**. לכל מתמחה קוד קבוע בן 6 תווים.

> היי, מעכשיו מזינים תורנויות כאן:
> https://<השירות>.onrender.com/portal/
> הקוד האישי שלך: XXXXXX
> אפשר לסמן ימים שאי אפשר לתרון בהם וימים שמעדיפים. אחרי שהשיבוץ ייסגר
> תוכל/י לראות שם את הלוח המלא ואת התורנויות שלך.

**אל תשלחו להם את `/editor`** — שם רואים ועורכים את הכול.

---

## חלק 5 — סדר עבודה חודשי

1. **חודשים** → **+ חודש חדש**, מבוסס על הקודם.
2. עדכון תורני חוץ וחגים.
3. הודעה למתמחים שההזנה פתוחה.
4. **יצירת שיבוץ** → **צור שיבוץ** (3–6 דקות בענן; להשאיר את החלון פתוח).
5. בדיקת התוצאה, תיקון והרצה חוזרת אם צריך.
6. **חודשים** → **🔒 הקפאת השיבוץ** — הלוח מתפרסם למתמחים.

---

## פתרון תקלות

| מה קורה | הסיבה |
|---|---|
| `ModuleNotFoundError: flask` | הריצו `python -m pip install flask flask-cors openpyxl` |
| `export : not recognized` | ב-PowerShell: `$env:APP_PASSWORD='...'` |
| הדפדפן מבקש שם משתמש בחלון קופץ | `app.py` ישן. בחדש יש חלון של האפליקציה, בלי שם משתמש |
| `Not Found` | `app.py` ישן. בחדש יש `/`, `/editor`, `/portal/` |
| מסך המתמחה 404 | `portal/portal.html` לא במקומו |
| לשונית «חודשים» חסרה | פתחתם את Netlify הישן. עברו ל-`/editor` |
| עמוד הבית מראה SQLite בענן | `DATABASE_URL` חסר |
| מתמחים מנותקים כל הזמן | `SECRET_KEY` חסר |
| הבנייה נכשלת על `psycopg` | `.python-version` לא עלה ל-GitHub |
| שיבוץ נכשל, קוד 9009 | `app.py` ישן |

---

## בדיקה שהכול תקין

```powershell
python tests\test_portal_archive.py
python tests\test_editor_contract.py
```
שתיהן צריכות להסתיים ב-`ALL CHECKS PASSED`.
