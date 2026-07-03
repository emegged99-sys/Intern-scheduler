# פריסת פרויקט השיבוץ לאינטרנט

הפרויקט מורכב משני חלקים:

1. **Backend** (`app.py` + `monthly_scheduler.py`) — שרת Python שמריץ את מנוע
   השיבוץ ומחזיר קובץ Excel. מוגן בסיסמה (ראו שלב 0).
2. **Frontend** (`intern_editor.html`) — קובץ אחד, בלי תלות בשרת, מתחבר
   לבקאנד דרך לשונית "יצירת שיבוץ".

---

## שלב 0 — הסיסמה

ה-backend קורא סיסמה ממשתנה סביבה בשם `APP_PASSWORD`. **הסיסמה אינה כתובה
בקוד** — תגדירו אותה בממשק של Render (שלב 3 למטה), כדי שלא תהיה חשופה ב-GitHub.
אם לא תוגדר, ה-API פתוח לגמרי — לכן אל תשכחו את שלב 3.5.

בחרו עכשיו סיסמה חזקה ושמרו אותה בצד.

---

## שלב 1 — התקנת Git (אם עוד אין לכם)

בדקו אם כבר מותקן:
```
git --version
```
אם לא — הורידו והתקינו מ-[git-scm.com](https://git-scm.com/downloads), ואז
הגדירו זהות (פעם אחת בלבד, בכל מחשב):
```
git config --global user.name "השם שלך"
git config --global user.email "you@example.com"
```

---

## שלב 2 — יצירת Repository ב-GitHub והעלאת הקבצים

1. היכנסו ל-[github.com](https://github.com) (או צרו חשבון אם אין).
2. למעלה מימין → **+** → **New repository**.
   - Repository name: `intern-scheduler` (או כל שם אחר)
   - Public או Private — שניהם עובדים עם Render. **Private מומלץ** כי יש שם
     קוד של שרת שמנהל נתוני מתמחים.
   - **לא** לסמן "Add a README" (יש לנו כבר קבצים מוכנים).
   - Create repository.
3. בעמוד שנפתח, תחת "…or push an existing repository from the command line",
   תראו כתובת כמו `https://github.com/<username>/intern-scheduler.git` —
   העתיקו אותה.
4. בטרמינל, בתיקייה שבה נמצאים הקבצים שקיבלתם
   (`app.py`, `monthly_scheduler.py`, `requirements.txt`, `Procfile`,
   `intern_editor.html`, `README.md`):
   ```
   cd path/to/webapp
   git init
   git add .
   git commit -m "Initial commit: scheduler + web editor"
   git branch -M main
   git remote add origin https://github.com/<username>/intern-scheduler.git
   git push -u origin main
   ```
   בפעם הראשונה תתבקשו להתחבר — עדיף עם **Personal Access Token** במקום
   סיסמה (GitHub לא מקבל סיסמאות רגילות ל-git push יותר):
   Settings → Developer settings → Personal access tokens → Generate new
   token (classic) → סמנו `repo` → Generate → העתיקו את הטוקן והשתמשו בו
   במקום סיסמה כשמתבקשים.
5. רעננו את עמוד ה-repo ב-GitHub — אמורים לראות את כל הקבצים שם.

---

## שלב 3 — פריסת ה-Backend ב-Render כ-Web Service

1. היכנסו ל-[render.com](https://render.com) → **Sign up** (הכי נוח: להתחבר
   ישירות עם GitHub).
2. לוח הבקרה → **New +** → **Web Service**.
3. **Build and deploy from a Git repository** → **Connect** ליד ה-repo
   `intern-scheduler` (אם לא רואים אותו — Configure account → תנו ל-Render
   הרשאה לגשת ל-repo, ואז חזרו).
4. מסך ההגדרות:
   - **Name:** `intern-scheduler` (יקבע את הכתובת: `intern-scheduler.onrender.com`)
   - **Region:** הקרוב אליכם (למשל Frankfurt)
   - **Branch:** `main`
   - **Runtime:** Python 3 (אמור להיבחר אוטומטית)
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --timeout 300 --workers 1 --threads 2 --bind 0.0.0.0:$PORT`
     (זהה לתוכן `Procfile`, Render לרוב מזהה זאת לבד)
   - **Instance Type:** Free (מספיק להתחלה; "נרדם" אחרי חוסר פעילות — הבקשה
     הראשונה אחרי שינה איטית בכ-30-60 שניות)
5. **שלב 3.5 — הסיסמה:** גללו למטה ל-**Environment Variables** → **Add
   Environment Variable**:
   - Key: `APP_PASSWORD`
   - Value: הסיסמה שבחרתם בשלב 0
6. **Create Web Service**. המתינו כ-2-3 דקות לבנייה הראשונה (רואים לוג חי
   על המסך).
7. כשהסטטוס הופך ל-**Live**, תקבלו כתובת בראש העמוד, כמו
   `https://intern-scheduler.onrender.com`. בדקו:
   `https://intern-scheduler.onrender.com/health` → אמור להחזיר
   `{"status":"ok"}`.

---

## שלב 4 — פריסת ה-Frontend

הכי פשוט, בלי GitHub: **[app.netlify.com/drop](https://app.netlify.com/drop)**
— גררו את `intern_editor.html` לעמוד, תקבלו כתובת ציבורית תוך שניות.

או, דרך GitHub Pages: הוסיפו את `intern_editor.html` לאותו repo (או חדש) →
Settings → Pages → Branch `main` → Save → כתובת
`https://<username>.github.io/<repo>/intern_editor.html`.

---

## שלב 5 — חיבור השניים

1. פתחו את כתובת ה-frontend.
2. לשונית **"יצירת שיבוץ"**:
   - "כתובת השרת" → `https://intern-scheduler.onrender.com`
   - "סיסמת שרת" → הסיסמה מ-`APP_PASSWORD`
   (שניהם נשמרים בדפדפן, מזינים פעם אחת בלבד)
3. ערכו נתונים בלשוניות האחרות כרגיל.
4. חזרו ל"יצירת שיבוץ" → שנה/חודש → **"צור שיבוץ והורד Excel"**.

---

## עדכון הקוד בעתיד

כל שינוי בקבצים → בתיקיית ה-repo המקומית:
```
git add .
git commit -m "תיאור השינוי"
git push
```
Render בונה ומפריס אוטומטית תוך דקות ספורות מכל push ל-`main`.

## פתרון תקלות

- **`/health` לא עונה:** בדקו את הלוג ב-Render (Logs tab) — לרוב שגיאת build
  (חסר חבילה ב-`requirements.txt`) או start command שגוי.
- **401 מה-frontend:** הסיסמה שהוזנה בעורך לא תואמת ל-`APP_PASSWORD` ב-Render,
  או שנשמרה עם רווח מיותר.
- **504 / timeout:** שיבוץ עם הרבה מתמחים/ריצות SA יכול לקחת זמן; אפשר
  להעלות instance type ב-Render לביצועים טובים יותר, או להקטין את `iters`
  ב-`monthly_scheduler.py`.
