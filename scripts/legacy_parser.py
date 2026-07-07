# -*- coding: utf-8 -*-
"""
פרסר גנרי-חצי לקבצי האקסל הישנים של מטבח/מחסן.
מייצר שתי טבלאות נורמליות:
  - pricelist_rows: (source_file, source_sheet, supplier_name, sku, description, unit, price, category)
  - contact_rows:   (source_file, source_sheet, supplier_name, phone, mobile, contact_person)
"""
import re
import pandas as pd

NON_SUPPLIER_TOKENS = {
    "הנחה", "הערות", "מחיר קודם", "מחיר ליחידה קודם", "מכירה לישיבות",
    "המכירה לישיבות", "קוד", "מוצר", "המוצר", "יחידה", "כמות",
}

PRICE_GENERIC_LABELS = {
    "מחיר", "המחיר", 'מחיר לפני מע"מ', "מחיר אחרי הנחה",
    'מחיר כולל מע"מ', "מחיר ליחדה חדשה", 'לפני מע"מ', "",
}

PRODUCT_TOKENS = ["מוצר", "המוצר"]
UNIT_TOKENS = ["יחידה", "כמות"]
CODE_TOKENS = ["קוד"]
SKIP_PRICE_TOKENS = {"הנחה", "הערות", "מחיר קודם", "מחיר ליחידה קודם",
                     "מכירה לישיבות", "המכירה לישיבות"}


def clean_supplier_label(label: str) -> str:
    if label is None:
        return ""
    s = str(label).strip()
    s = re.sub(r'\+?\s*מע"מ\s*$', '', s).strip()
    s = re.sub(r'^\s*לפני\s*', '', s).strip()
    return s


HEADER_SUPPORT_TOKENS = ["יחידה", "כמות", "מחיר", "קוד", "הנחה", "הערות"]


def find_header_rows(df: pd.DataFrame):
    """מוצא את כל השורות שיכולות לשמש כשורת כותרת.
    דורש הופעה של מילת 'מוצר'/'המוצר' *כתא נפרד* (לא כתת-מחרוזת בתוך שם מוצר
    כמו 'מוצרלה' או 'מוצרט'), יחד עם עוד תווית כותרת אחת לפחות באותה שורה -
    כדי למנוע זיהוי-שווא של שורת נתונים רגילה כשורת כותרת."""
    header_idxs = []
    for i in range(len(df)):
        cells = [str(v).strip() for v in df.iloc[i].tolist() if pd.notna(v)]
        has_product_cell = any(
            c == tok or c.replace(" ", "") == tok for c in cells for tok in PRODUCT_TOKENS
        )
        has_supplier_cell = any(c == "הספק" for c in cells)
        if not (has_product_cell or has_supplier_cell):
            continue
        joined = " ".join(cells)
        support_hits = sum(1 for tok in HEADER_SUPPORT_TOKENS if tok in joined)
        if has_supplier_cell or support_hits >= 1:
            header_idxs.append(i)
    return header_idxs


def parse_pricelist_sheet(df_raw: pd.DataFrame, source_file, source_sheet, default_supplier=None):
    """מפרק גיליון מחירון (יכול להכיל כמה טבלאות אחת מתחת לשנייה)."""
    df = df_raw.copy()
    # לזרוק עמודות שכולן ריקות
    df = df.dropna(axis=1, how="all")
    df = df.reset_index(drop=True)
    if df.empty:
        return []

    header_idxs = find_header_rows(df)
    if not header_idxs:
        return []

    rows_out = []
    header_idxs.append(len(df))  # sentinel לסוף הבלוק האחרון

    for block_i in range(len(header_idxs) - 1):
        h_idx = header_idxs[block_i]
        next_h_idx = header_idxs[block_i + 1]
        header_row = df.iloc[h_idx]

        # שורת "שם ספק" אפשרית - השורה שמעל הכותרת
        supplier_row = df.iloc[h_idx - 1] if h_idx - 1 >= 0 else None

        # זיהוי עמודות
        product_col = None
        unit_col = None
        code_col = None
        col_supplier_map = {}  # col_idx -> supplier name

        for col_idx, val in header_row.items():
            if pd.isna(val):
                continue
            val_s = str(val).strip()
            if product_col is None and any(tok in val_s for tok in PRODUCT_TOKENS):
                product_col = col_idx
                continue
            if unit_col is None and any(tok == val_s or tok in val_s for tok in UNIT_TOKENS):
                unit_col = col_idx
                continue
            if code_col is None and any(tok == val_s for tok in CODE_TOKENS):
                code_col = col_idx
                continue
            if val_s in SKIP_PRICE_TOKENS:
                continue
            # עמודת מחיר פוטנציאלית
            is_generic_header = (
                (val_s in NON_SUPPLIER_TOKENS)
                or (val_s in PRICE_GENERIC_LABELS)
                or ("מחיר" in val_s)
                or ("כולל" in val_s and 'מע"מ' in val_s)
            )
            if is_generic_header:
                # הכותרת עצמה היא תווית מחיר גנרית ("מחיר לפני מע\"מ" וכו') -
                # שם הספק האמיתי (אם קיים) יושב בשורה מעל, אחרת נשתמש בברירת המחדל
                if supplier_row is not None and pd.notna(supplier_row.get(col_idx)):
                    sup_name = clean_supplier_label(supplier_row.get(col_idx))
                    if not sup_name or sup_name in NON_SUPPLIER_TOKENS or sup_name in PRICE_GENERIC_LABELS:
                        sup_name = default_supplier
                else:
                    sup_name = default_supplier
            else:
                # הכותרת עצמה היא שם ספק אמיתי (למשל "דלאס", "קליינס")
                sup_name = clean_supplier_label(val_s)
                if not sup_name:
                    sup_name = default_supplier
            if sup_name:
                col_supplier_map[col_idx] = sup_name

        if product_col is None:
            continue

        # אם אין אף עמודת ספק שזוהתה, וגם יש default_supplier - נשתמש בעמודת "מחיר" הראשונה שנמצאה
        if not col_supplier_map and default_supplier:
            for col_idx, val in header_row.items():
                if col_idx in (product_col, unit_col, code_col):
                    continue
                if pd.isna(val):
                    continue
                val_s = str(val).strip()
                if val_s in SKIP_PRICE_TOKENS:
                    continue
                col_supplier_map[col_idx] = default_supplier
                break

        # עיבוד שורות הנתונים בבלוק
        data_start = h_idx + 1
        for r in range(data_start, min(next_h_idx, len(df))):
            row = df.iloc[r]
            product = row.get(product_col)
            if pd.isna(product) or str(product).strip() == "":
                continue
            product_s = str(product).strip()
            unit_val = row.get(unit_col) if unit_col is not None else None
            unit_s = str(unit_val).strip() if pd.notna(unit_val) else None
            code_val = row.get(code_col) if code_col is not None else None
            code_s = str(code_val).strip() if pd.notna(code_val) else ""

            for col_idx, sup_name in col_supplier_map.items():
                price_val = row.get(col_idx)
                if pd.isna(price_val):
                    continue
                try:
                    price_f = float(str(price_val).replace(",", "").split("(")[0].strip())
                except (ValueError, TypeError):
                    continue
                rows_out.append({
                    "source_file": source_file,
                    "source_sheet": source_sheet,
                    "supplier_name": sup_name,
                    "sku": code_s,
                    "description": product_s,
                    "unit": unit_s,
                    "price": price_f,
                })
    return rows_out


def parse_contacts_sheet(df_raw: pd.DataFrame, source_file, source_sheet):
    """מפרק גיליון טלפונים (עמודות: הספק/ספק, טלפון, נייד, איש קשר)."""
    df = df_raw.copy().dropna(axis=1, how="all").reset_index(drop=True)
    if df.empty:
        return []

    header_idx = None
    for i in range(len(df)):
        row_vals = [str(v) for v in df.iloc[i].tolist() if pd.notna(v)]
        joined = " ".join(row_vals)
        if "הספק" in joined or ("ספק" in joined and "טלפון" in joined):
            header_idx = i
            break
    if header_idx is None:
        return []

    header_row = df.iloc[header_idx]
    col_map = {}
    for col_idx, val in header_row.items():
        if pd.isna(val):
            continue
        val_s = str(val).strip()
        if val_s in ("הספק", "ספק"):
            col_map["supplier"] = col_idx
        elif "נייד" in val_s:
            col_map.setdefault("mobile", col_idx)
        elif "טלפון" in val_s:
            col_map.setdefault("phone", col_idx)
            if "phone2" not in col_map and val_s not in ("טלפון",):
                pass
        elif "איש קשר" in val_s or val_s == "שם איש קשר":
            col_map["contact_person"] = col_idx

    if "supplier" not in col_map:
        return []

    rows_out = []
    for r in range(header_idx + 1, len(df)):
        row = df.iloc[r]
        sup = row.get(col_map["supplier"])
        if pd.isna(sup) or str(sup).strip() == "":
            continue
        rows_out.append({
            "source_file": source_file,
            "source_sheet": source_sheet,
            "supplier_name": str(sup).strip(),
            "phone": str(row.get(col_map.get("phone"))).strip() if col_map.get("phone") is not None and pd.notna(row.get(col_map.get("phone"))) else None,
            "mobile": str(row.get(col_map.get("mobile"))).strip() if col_map.get("mobile") is not None and pd.notna(row.get(col_map.get("mobile"))) else None,
            "contact_person": str(row.get(col_map.get("contact_person"))).strip() if col_map.get("contact_person") is not None and pd.notna(row.get(col_map.get("contact_person"))) else None,
        })
    return rows_out


SUPPLIER_NAME_ALIASES = {
    "וגשל": "ווגשל",
    "בן  ג'ריס": "בן ג'ריס",
    "בנז'ריס": "בן ג'ריס",
    "בן גריס": "בן ג'ריס",
    "גדרון": "גידרון",
}


def normalize_supplier_name(name):
    """מאחד כתיבים שונים של אותו ספק (טעויות הקלדה/רווחים) לשם קנוני אחד."""
    if not name:
        return name
    name = str(name).strip()
    return SUPPLIER_NAME_ALIASES.get(name, name)
