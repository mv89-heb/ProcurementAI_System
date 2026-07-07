# -*- coding: utf-8 -*-
"""
naps2_watcher.py
=================
סקריפט שרץ מקומית על המחשב שלך (לצד NAPS2) וסוגר את הפער בין "סריקה" ל"טעינה למערכת".

התהליך תומך בשלושה סוגי מסמכים, כל אחד עם תיקיית סריקה נפרדת משלו:
  - מחירונים        -> WATCH_FOLDERS['pricelist']
  - חשבוניות         -> WATCH_FOLDERS['invoice']
  - תעודות משלוח     -> WATCH_FOLDERS['delivery_note']

איך זה עובד:
1. ב-NAPS2 מגדירים 3 פרופילי סריקה (Profiles), כל אחד עם "Auto Save" שמפנה לתיקייה
   המתאימה (למשל: C:\\ProcurementScans\\pricelists, C:\\ProcurementScans\\invoices,
   C:\\ProcurementScans\\delivery_notes).
2. הסקריפט הזה רץ ברקע, בודק כל כמה שניות אם נחתו קבצים חדשים בכל אחת מהתיקיות.
3. לכל קובץ חדש - נשלח אוטומטית ל-endpoint המתאים במערכת (מחירון/חשבונית/תעודת משלוח).
4. הקובץ עובר לתיקיית "processed" או "failed" בהתאם לתוצאה, כדי שלא יעובד פעמיים.

--------------------------------------------------------------------------
הגדרת NAPS2 (פעם אחת, לכל סוג מסמך):
  1. פתח את NAPS2 -> Profiles -> New Profile.
  2. סמן "Auto Save" -> בחר את התיקייה המתאימה (ראה BASE_FOLDER למטה).
  3. מומלץ: התחל את שם הקובץ בשם הספק ואז קו תחתון, למשל "אחוואה_2026-07-06.pdf" -
     כך המערכת תשייך את הקובץ לספק הנכון אוטומטית. אם לא צוין - היא תנחש
     מתחילת שם הקובץ.
  4. סרוק כרגיל דרך הפרופיל המתאים לסוג המסמך - זהו, אין צורך לגעת בדפדפן.

--------------------------------------------------------------------------
הרצה:
  1. pip install requests
  2. ערוך את ההגדרות למטה (BASE_FOLDER, API_BASE_URL, API_KEY אם הגדרת).
  3. הרץ: python naps2_watcher.py
  4. (אופציונלי) כדי שזה ירוץ אוטומטית עם כל הפעלת מחשב:
     צור קיצור דרך לקובץ הזה בתיקיית ההפעלה של Windows (Win+R -> shell:startup),
     או הגדר Task ב-Task Scheduler שמריץ אותו בהתחברות.
--------------------------------------------------------------------------
"""

import os
import time
import requests

# ============ הגדרות - ערוך לפי הצורך ============

# תיקיית הבסיס - כל סוגי המסמכים ייווצרו כתתי-תיקיות מתחתיה אוטומטית
BASE_FOLDER = r"C:\ProcurementScans"

# כתובת הבסיס של המערכת שלך (לדוגמה: כתובת ה-Render שלך, בלי סלאש בסוף)
API_BASE_URL = "https://your-app-name.onrender.com"

# אם הגדרת UPLOAD_API_KEY בצד השרת (משתנה סביבה), רשום אותו כאן גם.
# אם לא הגדרת בצד השרת - השאר ריק.
API_KEY = ""

# כל כמה שניות לבדוק אם יש קבצים חדשים
POLL_INTERVAL_SECONDS = 10

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

# הגדרת שלושת סוגי המסמכים: תיקיית סריקה, endpoint, ופרמטרים נוספים שיישלחו
DOCUMENT_CONFIGS = {
    "pricelist": {
        "folder_name": "pricelists",
        "endpoint": "/api/upload-pricelist",
        "extra_form_data": {},
    },
    "invoice": {
        "folder_name": "invoices",
        "endpoint": "/api/upload-document",
        "extra_form_data": {"doc_type": "invoice"},
    },
    "delivery_note": {
        "folder_name": "delivery_notes",
        "endpoint": "/api/upload-document",
        "extra_form_data": {"doc_type": "delivery_note"},
    },
}

# ===================================================


def get_folder_paths(doc_key):
    base = os.path.join(BASE_FOLDER, DOCUMENT_CONFIGS[doc_key]["folder_name"])
    return {
        "incoming": base,
        "processed": os.path.join(base, "processed"),
        "failed": os.path.join(base, "failed"),
    }


def ensure_folders():
    for doc_key in DOCUMENT_CONFIGS:
        paths = get_folder_paths(doc_key)
        for p in paths.values():
            os.makedirs(p, exist_ok=True)


def upload_file(filepath, doc_key):
    config = DOCUMENT_CONFIGS[doc_key]
    filename = os.path.basename(filepath)
    url = API_BASE_URL.rstrip("/") + config["endpoint"]
    headers = {"X-API-Key": API_KEY} if API_KEY else {}

    with open(filepath, "rb") as f:
        files = {"file": (filename, f)}
        try:
            res = requests.post(url, files=files, data=config["extra_form_data"], headers=headers, timeout=60)
        except requests.exceptions.RequestException as e:
            print(f"  [שגיאת רשת] לא ניתן להתחבר לשרת: {e}")
            return False

    try:
        data = res.json()
    except ValueError:
        print(f"  [שגיאה] תגובה לא תקינה מהשרת (סטטוס {res.status_code})")
        return False

    if res.ok and data.get("success"):
        supplier = data.get("supplier") or data.get("supplier_name") or "?"
        print(f"  [הצלחה] נטענו {data.get('items_count')} פריטים עבור הספק \"{supplier}\"")
        return True
    else:
        print(f"  [נכשל] {data.get('error', 'שגיאה לא ידועה')}")
        return False


def process_new_files_for(doc_key):
    paths = get_folder_paths(doc_key)
    incoming = paths["incoming"]

    for filename in os.listdir(incoming):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        full_path = os.path.join(incoming, filename)
        if not os.path.isfile(full_path):
            continue

        # מוודא שהקובץ סיים להישמר לגמרי (NAPS2 עדיין עלול לכתוב אליו)
        try:
            size_before = os.path.getsize(full_path)
            time.sleep(1)
            size_after = os.path.getsize(full_path)
            if size_before != size_after:
                continue  # עדיין נכתב, ננסה שוב בסבב הבא
        except OSError:
            continue

        print(f"[{doc_key}] קובץ חדש: {filename} - שולח לעיבוד...")
        success = upload_file(full_path, doc_key)

        target_folder = paths["processed"] if success else paths["failed"]
        try:
            os.rename(full_path, os.path.join(target_folder, filename))
        except OSError as e:
            print(f"  [שגיאה בהעברת קובץ] {e}")


def main():
    ensure_folders()
    print("=" * 70)
    print("NAPS2 Watcher פעיל - מאזין לתיקיות הבאות:")
    for doc_key in DOCUMENT_CONFIGS:
        print(f"  [{doc_key}] {get_folder_paths(doc_key)['incoming']}")
    print(f"שרת יעד: {API_BASE_URL}")
    print("לעצירה: Ctrl+C")
    print("=" * 70)

    while True:
        try:
            for doc_key in DOCUMENT_CONFIGS:
                process_new_files_for(doc_key)
        except Exception as e:
            print(f"[שגיאה כללית בלולאה] {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
