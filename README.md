# ProcureAI - מערכת רכש חכמה

מערכת רכש שמחלצת מחירונים, חשבוניות ותעודות משלוח סרוקות באמצעות AI (Gemini),
משווה מחירים בין ספקים, ומצליבה חשבוניות מול תעודות משלוח כדי לאתר פערי כמות ומחיר.

## מה יש כאן

- **השוואת ספקים** - הצלבת מחירונים בין ספקים שונים לאיתור הזדמנויות חיסכון.
- **העלאת מחירונים סרוקים** - גרירת קובץ (PDF/JPG/PNG) והמערכת מזינה אותו אוטומטית ל-DB.
- **הצלבת חשבוניות מול תעודות משלוח** - העלאת חשבונית + תעודת משלוח, והמערכת:
  - מזהה פערי כמות (הותקן פחות/יותר ממה שחויב).
  - מזהה פריטים שחויבו ולא סופקו, או להפך.
  - משווה את מחיר החשבונית מול המחירון של הספק ומדגישה חריגות.
- **סוכן AI למשא ומתן** - ניסוח אוטומטי של פנייה לספק לתיקון מחיר.
- **אינטגרציה עם NAPS2** - סקריפט (`naps2_watcher.py`) שמריץ מחשב מקומי, מזהה סריקות חדשות
  ומעלה אותן אוטומטית למערכת, בלי צורך להיכנס לדפדפן בכלל.

## דרישות מקדימות

- חשבון [Neon](https://neon.tech) (Postgres בענן, יש טיר חינמי) - ליצירת `DATABASE_URL`.
- מפתח API של Gemini מ-[Google AI Studio](https://aistudio.google.com/apikey) - ל-`GEMINI_API_KEY`.
- חשבון [Render](https://render.com) - לאירוח האתר.
- חשבון GitHub - להעלאת הקוד.

## שלב 1: העלאה ל-GitHub

```bash
cd ProcurementAI_System-main
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO_NAME.git
git push -u origin main
```

## שלב 2: יצירת מסד נתונים ב-Neon

1. היכנס ל-neon.tech, צור פרויקט חדש.
2. העתק את ה-**Connection String** (נראה כך: `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).
3. שמור אותו בצד - תזדקק לו בשלב הבא כ-`DATABASE_URL`.

## שלב 3: דיפלוי ל-Render

### אופציה א' - Blueprint אוטומטי (מומלץ, מהיר יותר)
1. ב-Render: **New -> Blueprint**.
2. חבר את ה-repo מ-GitHub. Render יזהה את קובץ `render.yaml` אוטומטית.
3. יתבקש ממך להזין את משתני הסביבה (ראה למטה) - הזן אותם ואשר.

### אופציה ב' - הגדרה ידנית
1. ב-Render: **New -> Web Service** -> חבר את ה-repo.
2. **Build Command**: `./build.sh`
3. **Start Command**: `gunicorn app:app`
4. הוסף את משתני הסביבה הבאים תחת Environment:

| משתנה | ערך |
|---|---|
| `DATABASE_URL` | ה-Connection String מ-Neon |
| `GEMINI_API_KEY` | המפתח מ-Google AI Studio |
| `UPLOAD_API_KEY` | (אופציונלי) מחרוזת סודית משלך - להגנה על נקודות ההעלאה |

5. לחץ **Create Web Service**. הדיפלוי הראשון ייקח כמה דקות ויריץ את `build.sh`,
   שיוצר את כל טבלאות ה-DB אוטומטית (`core/database.py`).

## שלב 4: חיבור NAPS2 (סריקה אוטומטית)

ראה הוראות מפורטות בראש הקובץ `naps2_watcher.py`. בקצרה:

1. הרץ מקומית: `pip install requests`
2. ערוך ב-`naps2_watcher.py` את `API_BASE_URL` לכתובת ה-Render שקיבלת (למשל `https://your-app.onrender.com`).
3. אם הגדרת `UPLOAD_API_KEY` - הזן אותו גם בסקריפט.
4. הרץ `python naps2_watcher.py` - הוא ייצור אוטומטית 3 תיקיות תחת `C:\ProcurementScans`:
   `pricelists`, `invoices`, `delivery_notes`.
5. ב-NAPS2 הגדר 3 פרופילי סריקה (Profiles) נפרדים, כל אחד עם "Auto Save" שמפנה
   לתיקייה המתאימה. כל מה שנסרק לתיקייה הנכונה יטען אוטומטית לסוג המסמך המתאים במערכת -
   כולל הצלבת חשבוניות מול תעודות משלוח, בלי להיכנס לדפדפן.

## מבנה הפרויקט

```
app.py                  - השרת הראשי (Flask) וכל ה-API endpoints
ai_engine.py             - שכבת התקשורת עם Gemini (חילוץ מחירונים/מסמכים)
core/database.py         - יצירת טבלאות ה-DB (רץ אוטומטית ב-build.sh)
core/matching.py         - לוגיקת ההצלבה בין חשבונית לתעודת משלוח ולמחירון
naps2_watcher.py         - סקריפט מקומי לחיבור NAPS2 למערכת
templates/index.html     - ממשק המשתמש (SPA, RTL, עברית)
build.sh                 - סקריפט הבנייה שרץ ב-Render
render.yaml              - הגדרת Blueprint לדיפלוי אוטומטי ב-Render
requirements.txt         - תלויות Python
```

## טבלאות DB עיקריות

- `pricelist_items` - מחירוני ספקים.
- `documents` + `document_items` - חשבוניות ותעודות משלוח שהועלו, וכותרותיהן/שורותיהן.
- `upload_log`, `match_log` - לוגים לצורכי מעקב וביקורת.

## פיתוח מקומי

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://..."
export GEMINI_API_KEY="..."
python app.py
```

השרת ירוץ על `http://localhost:5000`.
