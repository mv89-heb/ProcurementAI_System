# -*- coding: utf-8 -*-
"""טיפול ידני בגיליונות עם מבנה חריג (בלי מילת כותרת 'מוצר' מפורשת)."""
import pandas as pd


def _to_price(v):
    if pd.isna(v):
        return None
    try:
        return float(str(v).replace(",", "").split("(")[0].strip())
    except (ValueError, TypeError):
        return None


def parse_two_col_supplier_sheet(df_raw, source_file, source_sheet, supplier_name,
                                  product_col=0, price_col=1, start_row=1):
    """גיליון פשוט: עמודת מוצר + עמודת מחיר של ספק בודד, ללא כותרת 'מוצר' מפורשת.
    (תנובה, אנג'ל)"""
    df = df_raw.copy().dropna(axis=1, how="all").reset_index(drop=True)
    rows_out = []
    for r in range(start_row, len(df)):
        row = df.iloc[r]
        product = row.get(product_col)
        if pd.isna(product) or str(product).strip() == "":
            continue
        price = _to_price(row.get(price_col))
        if price is None:
            continue
        rows_out.append({
            "source_file": source_file, "source_sheet": source_sheet,
            "supplier_name": supplier_name, "sku": "",
            "description": str(product).strip(), "unit": None, "price": price,
        })
    return rows_out


GENERIC_LABEL_SUBSTRINGS = ['מחיר', "הנחה", 'מע"מ', "כולל"]


def parse_named_columns_sheet(df_raw, source_file, source_sheet, header_row_idx,
                               product_col=0, start_row=None, unit_col=None,
                               skip_cols=None, name_row_idx=None):
    """גיליון שבו שורת הכותרת מכילה תוויות מחיר גנריות ("לפני מע\"מ" וכו'),
    כאשר שמות הספקים האמיתיים נמצאים בשורה שמעליה (מאפיים, גידרון)."""
    df = df_raw.copy().dropna(axis=1, how="all").reset_index(drop=True)
    header_row = df.iloc[header_row_idx]
    name_row = df.iloc[name_row_idx] if name_row_idx is not None else (
        df.iloc[header_row_idx - 1] if header_row_idx > 0 else None
    )
    skip_cols = skip_cols or set()
    start_row = start_row if start_row is not None else header_row_idx + 1

    def clean(name):
        name = str(name).strip()
        name = name.replace('+ מע"מ', "").replace('+מע"מ', "").strip()
        return name

    supplier_cols = {}
    for col_idx, val in header_row.items():
        if col_idx == product_col or col_idx == unit_col or col_idx in skip_cols:
            continue
        if pd.isna(val):
            continue
        val_s = str(val).strip()
        is_generic = any(tok in val_s for tok in GENERIC_LABEL_SUBSTRINGS)
        name = None
        if is_generic and name_row is not None and pd.notna(name_row.get(col_idx)):
            name = clean(name_row.get(col_idx))
        if not name:
            name = clean(val_s)
        if not name or any(tok in name for tok in GENERIC_LABEL_SUBSTRINGS):
            continue
        supplier_cols.setdefault(name, []).append(col_idx)

    rows_out = []
    for r in range(start_row, len(df)):
        row = df.iloc[r]
        product = row.get(product_col)
        if pd.isna(product) or str(product).strip() == "":
            continue
        product_s = str(product).strip()
        unit_val = row.get(unit_col) if unit_col is not None else None
        unit_s = str(unit_val).strip() if pd.notna(unit_val) else None
        for sup_name, cols in supplier_cols.items():
            # קח את הערך הראשון הלא-ריק מתוך העמודות ששייכות לספק הזה (למשל לפני/אחרי מע"מ)
            price = None
            for c in cols:
                price = _to_price(row.get(c))
                if price is not None:
                    break
            if price is None:
                continue
            rows_out.append({
                "source_file": source_file, "source_sheet": source_sheet,
                "supplier_name": sup_name, "sku": "",
                "description": product_s, "unit": unit_s, "price": price,
            })
    return rows_out


def parse_side_by_side_blocks(df_raw, source_file, source_sheet, header_row_idx,
                               block_width, supplier_names):
    """גיליון עם כמה טבלאות זהות זו לצד זו (אוהבי ירושלים: המוצר/פאר לי/שמח/סגלוביץ חוזר 3 פעמים)."""
    df = df_raw.copy().dropna(axis=1, how="all").reset_index(drop=True)
    n_cols = df.shape[1]
    rows_out = []
    for block_start in range(0, n_cols, block_width):
        if block_start + 1 > n_cols:
            break
        product_col = block_start
        for r in range(header_row_idx + 1, len(df)):
            row = df.iloc[r]
            product = row.get(product_col)
            if pd.isna(product) or str(product).strip() == "":
                continue
            product_s = str(product).strip()
            for i, sup_name in enumerate(supplier_names):
                col_idx = block_start + 1 + i
                if col_idx >= n_cols:
                    continue
                price = _to_price(row.get(col_idx))
                if price is None:
                    continue
                rows_out.append({
                    "source_file": source_file, "source_sheet": source_sheet,
                    "supplier_name": sup_name, "sku": "",
                    "description": product_s, "unit": None, "price": price,
                })
    return rows_out
