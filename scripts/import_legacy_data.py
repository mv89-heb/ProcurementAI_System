# -*- coding: utf-8 -*-
"""
ייבוא נתונים היסטוריים (מחירונים + טלפוני ספקים) מקבצי האקסל הישנים אל ה-DB.

שימוש:
    export DATABASE_URL="postgresql://USER:PASSWORD@ep-xxx.neon.tech/neondb?sslmode=require"
    python scripts/import_legacy_data.py --data-dir /path/to/legacy_files

הסקריפט:
  1. קורא את הקבצים לפי שם (חיפוש substring, לא צריך שם מדויק).
  2. מפרק כל גיליון לפי הלוגיקה שפותחה במיוחד לקבצים האלה (ראה legacy_parser.py /
     legacy_special_cases.py).
  3. יוצר/מעדכן ספקים בטבלת suppliers (לפי שם, ללא כפילויות).
  4. טוען פריטי מחירון לטבלת pricelist_items, מקושרים ל-supplier_id.
  5. טוען טלפונים/אנשי קשר לטבלת supplier_contacts - נפרד לגמרי מהמחירונים.

בטוח להרצה חוזרת (UPSERT לפי UNIQUE constraint על pricelist_items,
ו-suppliers מזוהים לפי שם).
"""
import os
import sys
import glob
import time
import argparse
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _THIS_DIR)          # כדי לאפשר "import legacy_parser"
sys.path.insert(0, _PROJECT_ROOT)      # כדי לאפשר "from core.database import init_db"
from legacy_parser import parse_pricelist_sheet, parse_contacts_sheet, normalize_supplier_name
from legacy_special_cases import (
    parse_two_col_supplier_sheet,
    parse_named_columns_sheet,
    parse_side_by_side_blocks,
)

COMPANY_ID = "demo_company"

FILE_PATTERNS = {
    "food": "מחירים_-_ספקי_מזון",
    "cleaning": "חד_פעמי_וחומרי_ניקיון",
    "produce": "פירות_וירקות",
    "contacts_service": "טלפונים_ספקים_ונותני_שירות",
    "contacts_food": "טלפונים_ספקי_מזון",
    "frozen": "קפואים",
}


def find_file(data_dir, pattern):
    matches = glob.glob(os.path.join(data_dir, f"*{pattern}*"))
    if len(matches) > 1:
        print(f"אזהרה: הדפוס '{pattern}' תואם ליותר מקובץ אחד: {matches} - נבחר הראשון, כדאי לבדוק.")
    return matches[0] if matches else None


def build_rows(data_dir):
    pricelist_rows, contact_rows = [], []

    def add_pricelist(rows, category):
        for r in rows:
            r["category"] = category
            r["supplier_name"] = normalize_supplier_name(r["supplier_name"])
            pricelist_rows.append(r)

    food_file = find_file(data_dir, FILE_PATTERNS["food"])
    if food_file:
        xls = pd.ExcelFile(food_file)
        df = xls.parse("גיליון1", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "גיליון1", default_supplier="מאפיית אנג'ל"), "מזון")
        df = xls.parse("מאפיים", header=None)
        add_pricelist(parse_named_columns_sheet(df, food_file, "מאפיים", header_row_idx=1, product_col=0), "מזון")
        df = xls.parse("תנובה", header=None)
        add_pricelist(parse_two_col_supplier_sheet(df, food_file, "תנובה", "תנובה", 0, 1, 1), "מזון")
        df = xls.parse("גידרון", header=None)
        add_pricelist(parse_named_columns_sheet(df, food_file, "גידרון", header_row_idx=2,
                                                  product_col=0, unit_col=1, name_row_idx=1), "מזון")
        df = xls.parse("וגשל", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "וגשל", default_supplier="ווגשל"), "מזון")
        df = xls.parse("חד פעמי", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "חד פעמי"), "חד פעמי וניקיון")
        df = xls.parse("חד פעמי חדש", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "חד פעמי חדש"), "חד פעמי וניקיון")
        df = xls.parse("אנגל", header=None)
        add_pricelist(parse_two_col_supplier_sheet(df, food_file, "אנגל", "אנג'ל", 0, 1, 1), "מזון")
        df = xls.parse("הודיה פלסט", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "הודיה פלסט", default_supplier="הודיה פלסט"), "חד פעמי וניקיון")
        df = xls.parse("יפאורה", header=None)
        add_pricelist(parse_pricelist_sheet(df, food_file, "יפאורה", default_supplier="יפאורה"), "מזון")
    else:
        print("אזהרה: לא נמצא קובץ ספקי מזון בתיקייה", data_dir)

    cleaning_file = find_file(data_dir, FILE_PATTERNS["cleaning"])
    if cleaning_file:
        xls2 = pd.ExcelFile(cleaning_file)
        df = xls2.parse("22.11.21", header=None)
        add_pricelist(parse_pricelist_sheet(df, cleaning_file, "22.11.21"), "חד פעמי וניקיון")
        df = xls2.parse("גיליון1", header=None)
        add_pricelist(parse_pricelist_sheet(df, cleaning_file, "גיליון1"), "חד פעמי וניקיון")
        df = xls2.parse("מחירים שילת", header=None)
        add_pricelist(parse_two_col_supplier_sheet(df, cleaning_file, "מחירים שילת", "שילת פלסט", 0, 1, 6), "חד פעמי וניקיון")
        contact_rows.append({"source_file": cleaning_file, "source_sheet": "מחירים שילת",
                              "supplier_name": "שילת פלסט", "phone": "052-7677217",
                              "mobile": None, "contact_person": None})
        df = xls2.parse("גיליון3", header=None)
        add_pricelist(parse_pricelist_sheet(df, cleaning_file, "גיליון3", default_supplier="פעמית"), "חד פעמי וניקיון")
    else:
        print("אזהרה: לא נמצא קובץ כלים חד פעמי/ניקיון בתיקייה", data_dir)

    produce_file = find_file(data_dir, FILE_PATTERNS["produce"])
    if produce_file:
        xls3 = pd.ExcelFile(produce_file)
        df = xls3.parse("אוהבי ירושלים", header=None)
        add_pricelist(parse_side_by_side_blocks(df, produce_file, "אוהבי ירושלים", header_row_idx=2,
                                                  block_width=4, supplier_names=["פאר לי", "שמח", "סגלוביץ"]), "פירות וירקות")
    else:
        print("אזהרה: לא נמצא קובץ פירות וירקות בתיקייה", data_dir)

    frozen_file = find_file(data_dir, FILE_PATTERNS["frozen"])
    if frozen_file:
        xls4 = pd.ExcelFile(frozen_file)
        df = xls4.parse("גיליון1", header=None)
        add_pricelist(parse_pricelist_sheet(df, frozen_file, "גיליון1"), "קפואים ובשר")
        df = xls4.parse("בן גריס", header=None)
        add_pricelist(parse_pricelist_sheet(df, frozen_file, "בן גריס", default_supplier="בן גריס"), "קפואים ובשר")
        df = xls4.parse("קפואים", header=None)
        add_pricelist(parse_pricelist_sheet(df, frozen_file, "קפואים", default_supplier="שחר קפואים"), "קפואים ובשר")
        df = xls4.parse("בצקים", header=None)
        add_pricelist(parse_pricelist_sheet(df, frozen_file, "בצקים"), "קפואים ובשר")
    else:
        print("אזהרה: לא נמצא קובץ השוואת מחירים קפואים בתיקייה", data_dir)

    contacts_service_file = find_file(data_dir, FILE_PATTERNS["contacts_service"])
    if contacts_service_file:
        xls5 = pd.ExcelFile(contacts_service_file)
        df = xls5.parse("גיליון1", header=None)
        contact_rows.extend(parse_contacts_sheet(df, contacts_service_file, "גיליון1"))
    else:
        print("אזהרה: לא נמצא קובץ טלפונים ספקים ונותני שירות בתיקייה", data_dir)

    contacts_food_file = find_file(data_dir, FILE_PATTERNS["contacts_food"])
    if contacts_food_file:
        xls6 = pd.ExcelFile(contacts_food_file)
        df = xls6.parse("גיליון1", header=None)
        contact_rows.extend(parse_contacts_sheet(df, contacts_food_file, "גיליון1"))
    else:
        print("אזהרה: לא נמצא קובץ טלפונים ספקי מזון בתיקייה", data_dir)

    for c in contact_rows:
        c["supplier_name"] = normalize_supplier_name(c["supplier_name"])

    return pricelist_rows, contact_rows


def clean_val(v):
    """ממיר NaN של pandas ל-None וכל ערך אחר למחרוזת, כדי שפרמטרים לא-קיימים
    לא יגיעו ל-psycopg2 בתור float('nan') (מה שגורם לשגיאות type mismatch ב-Postgres)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def bulk_get_or_create_suppliers(cur, names_with_categories):
    """יוצר/מוצא ID לכל הספקים בבת אחת (מקסימום 2 round-trips ל-DB, לא אחד לכל שורה).
    names_with_categories: dict {name: category_or_None}"""
    names_with_categories = {
        (name or "").strip(): cat for name, cat in names_with_categories.items() if (name or "").strip()
    }
    if not names_with_categories:
        return {}

    rows = [(COMPANY_ID, name, cat) for name, cat in names_with_categories.items()]
    execute_values(cur, """
        INSERT INTO suppliers (company_id, name, category)
        VALUES %s
        ON CONFLICT (company_id, name) DO UPDATE
            SET category = COALESCE(EXCLUDED.category, suppliers.category)
    """, rows)

    cur.execute("SELECT id, name FROM suppliers WHERE company_id = %s", (COMPANY_ID,))
    return {name: sid for sid, name in cur.fetchall()}


def run_import(data_dir, dry_run=False):
    """מריץ את הסנכרון בפועל: מוחק לגמרי ובונה מחדש את שתי הטבלאות הייעודיות
    (supplier_catalog_items, supplier_contacts) מתוך קבצי האקסל - 'עדכון מ-0' פשוט
    ומלא בכל הרצה, בלי להתעסק עם UPSERT/מפתחות ייחודיים. הטבלה suppliers לא נמחקת
    (רק מתעדכנת/מתווספת), כדי לא לאבד קטגוריות שאולי נערכו ידנית בעתיד.
    כל הכתיבה נעשית ב-bulk (execute_values) ולא שורה-שורה, כי כל insert בודד הוא
    round-trip נפרד ל-DB מרוחק (Neon) - עם ~2,500 שורות זה ההבדל בין שניות לדקות.
    אפשר לקרוא לזה גם ישירות מ-app.py בעליית האפליקציה, לא רק מה-CLI."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url and not dry_run:
        print("שגיאה: יש להגדיר את משתנה הסביבה DATABASE_URL (או להריץ עם --dry-run).")
        return False

    t0 = time.time()
    pricelist_rows, contact_rows = build_rows(data_dir)
    price_df = pd.DataFrame(pricelist_rows)
    contact_df = pd.DataFrame(contact_rows)

    print(f"[legacy import] נמצאו {len(price_df)} שורות מחירון ו-{len(contact_df)} שורות טלפונים/אנשי קשר "
          f"(פרסינג לקח {time.time() - t0:.1f} שניות).")

    if dry_run:
        print("[legacy import] --dry-run: לא בוצעה כתיבה ל-DB.")
        return True

    t1 = time.time()
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    from core.database import init_db
    init_db()

    # מחיקה מלאה ובנייה מחדש - שתי הטבלאות האלה שייכות אך ורק לייבוא הזה,
    # כך שאין שום סיכון לפגוע בנתונים אחרים של האפליקציה.
    cur.execute("DELETE FROM supplier_catalog_items;")
    cur.execute("DELETE FROM supplier_contacts;")

    # --- שלב 1: כל הספקים הייחודיים בבת אחת ---
    names_with_categories = {}
    for _, r in price_df.iterrows():
        name = r["supplier_name"]
        cat = clean_val(r.get("category"))
        if name not in names_with_categories or cat:
            names_with_categories[name] = cat
    for _, r in contact_df.iterrows():
        names_with_categories.setdefault(r["supplier_name"], None)

    supplier_ids = bulk_get_or_create_suppliers(cur, names_with_categories)

    # --- שלב 2: כל פריטי הקטלוג בבת אחת ---
    catalog_rows = []
    for _, r in price_df.iterrows():
        category_val = clean_val(r.get("category"))
        unit_val = clean_val(r.get("unit"))
        sku_val = clean_val(r.get("sku")) or ""
        desc_val = clean_val(r.get("description"))
        source_file_val = clean_val(r.get("source_file"))
        source_sheet_val = clean_val(r.get("source_sheet"))
        sid = supplier_ids.get(r["supplier_name"].strip())
        catalog_rows.append((
            COMPANY_ID, sid, r["supplier_name"], sku_val, desc_val,
            unit_val, category_val, r["price"], source_file_val, source_sheet_val,
        ))
    if catalog_rows:
        execute_values(cur, """
            INSERT INTO supplier_catalog_items
                (company_id, supplier_id, supplier_name, sku, description, unit, category,
                 price, source_file, source_sheet)
            VALUES %s
        """, catalog_rows)

    # --- שלב 3: כל אנשי הקשר בבת אחת ---
    contact_rows_sql = []
    for _, r in contact_df.iterrows():
        sid = supplier_ids.get(r["supplier_name"].strip())
        contact_rows_sql.append((
            sid, clean_val(r.get("phone")), clean_val(r.get("mobile")),
            clean_val(r.get("contact_person")), clean_val(r.get("source_file")),
        ))
    if contact_rows_sql:
        execute_values(cur, """
            INSERT INTO supplier_contacts (supplier_id, phone, mobile, contact_person, source_file)
            VALUES %s
        """, contact_rows_sql)

    conn.commit()
    cur.close()
    conn.close()
    print(f"[legacy import] הייבוא הושלם בהצלחה ({time.time() - t1:.1f} שניות כתיבה ל-DB).")
    return True


def main():
    parser = argparse.ArgumentParser(description="ייבוא מחירונים וטלפוני ספקים מקבצי אקסל ישנים")
    parser.add_argument("--data-dir", required=True, help="תיקייה שמכילה את 6 קבצי האקסל המקוריים")
    parser.add_argument("--dry-run", action="store_true", help="רק להציג סיכום, בלי לכתוב ל-DB")
    args = parser.parse_args()
    run_import(args.data_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
