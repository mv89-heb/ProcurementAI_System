# -*- coding: utf-8 -*-
"""
core/matching.py
=================
לוגיקת ההצלבה בין חשבונית לתעודת משלוח (ובנוסף - מול מחירון הספק לבדיקת פערי מחיר).

עקרון ההתאמה בין שורות:
1. התאמה מדויקת לפי מק"ט (SKU) אם קיים בשני הצדדים.
2. אם אין מק"ט תואם - התאמה "מטושטשת" (fuzzy) לפי דמיון בתיאור הפריט.
"""
import re
import difflib

FUZZY_MATCH_THRESHOLD = 0.6
PRICE_DIFF_TOLERANCE = 0.01  # אגורות - סף להתעלמות מהפרשי עיגול

# --- ספי שינוי מחיר (באחוזים) - מבוססים על מקובל בענף הרכש/סיטונאות מזון ---
# מתחת לזה: תנודה רגילה (מחירי שוק/עונתיות), לא מוצגת כלל כבעיה.
PRICE_CHANGE_IGNORE_PCT = 7.0
# בין כאן לסף ה"סכנה": שינוי מורגש, אבל לא בהכרח בעייתי בפריט בודד -
# מוצג כמידע, ומצטבר לכדי התרעה רק אם מופיע במספר פריטים (ראו MIN_ITEMS_FOR_PATTERN_ALERT).
PRICE_CHANGE_NOTABLE_PCT = 15.0
# מעל כאן: שינוי גדול מאוד שמצדיק התרעה גם אם זה פריט בודד בחשבונית.
PRICE_CHANGE_DANGER_PCT = 30.0
# כמה פריטים "מורגשים" (מעל NOTABLE אך מתחת ל-DANGER) צריך למצוא באותה חשבונית
# כדי שזה ייחשב דפוס שמצדיק התרעה, ולא רק חריגה נקודתית של מוצר אחד.
MIN_ITEMS_FOR_PATTERN_ALERT = 2


def _normalize(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similarity(a, b):
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def find_best_match(item, candidates, used_ids):
    """מחפש את ההתאמה הטובה ביותר עבור item מתוך רשימת candidates, תוך התעלמות ממה שכבר שויך (used_ids)."""
    item_sku = (item.get("sku") or "").strip()

    # שלב 1: התאמה מדויקת לפי מק"ט
    if item_sku:
        for c in candidates:
            if c["id"] in used_ids:
                continue
            if (c.get("sku") or "").strip() == item_sku:
                return c

    # שלב 2: התאמה מטושטשת לפי תיאור
    best, best_score = None, 0.0
    for c in candidates:
        if c["id"] in used_ids:
            continue
        score = _similarity(item.get("description"), c.get("description"))
        if score > best_score:
            best_score, best = score, c

    if best_score >= FUZZY_MATCH_THRESHOLD:
        return best
    return None


def match_invoice_to_delivery(invoice_items, delivery_items):
    """
    משווה בין שורות חשבונית לשורות תעודת משלוח.
    כל item הוא dict עם: id, sku, description, quantity, unit_price
    מחזיר רשימת שורות תוצאה + סיכום.
    """
    results = []
    used_delivery_ids = set()

    for inv in invoice_items:
        match = find_best_match(inv, delivery_items, used_delivery_ids)
        row = {
            "sku": inv.get("sku") or "",
            "description": inv.get("description") or "",
            "invoice_quantity": _to_float(inv.get("quantity")),
            "invoice_unit_price": _to_float(inv.get("unit_price")),
            "delivery_quantity": None,
            "quantity_diff": None,
            "flags": [],
            "severity": "ok",  # ok | warning | danger
        }

        if match:
            used_delivery_ids.add(match["id"])
            row["delivery_quantity"] = _to_float(match.get("quantity"))
            diff = round(row["invoice_quantity"] - row["delivery_quantity"], 3)
            row["quantity_diff"] = diff
            if abs(diff) > 0.001:
                row["flags"].append(
                    f"פער בכמות: חויבו {row['invoice_quantity']:g} אך סופקו {row['delivery_quantity']:g}"
                )
                row["severity"] = "danger"
        else:
            row["flags"].append("הפריט מהחשבונית לא נמצא בתעודת המשלוח")
            row["severity"] = "danger"

        results.append(row)

    # פריטים שסופקו בתעודת המשלוח אך לא הופיעו בחשבונית בכלל
    for d in delivery_items:
        if d["id"] not in used_delivery_ids:
            results.append({
                "sku": d.get("sku") or "",
                "description": d.get("description") or "",
                "invoice_quantity": None,
                "invoice_unit_price": None,
                "delivery_quantity": _to_float(d.get("quantity")),
                "quantity_diff": None,
                "flags": ["הפריט סופק בתעודת המשלוח אך לא נמצא בחשבונית"],
                "severity": "warning",
            })

    return results


def classify_price_change_pct(pct_change):
    """מסווג שינוי מחיר (באחוזים, יכול להיות שלילי = הוזלה) לפי הסף המקובל.
    מחזיר אחד מ: 'ignore' | 'notable' | 'danger'"""
    abs_pct = abs(pct_change)
    if abs_pct >= PRICE_CHANGE_DANGER_PCT:
        return "danger"
    if abs_pct >= PRICE_CHANGE_NOTABLE_PCT:
        return "notable"
    return "ignore"


def enrich_with_pricelist(rows, pricelist_items, price_history_lookup=None):
    """
    מוסיף לכל שורת תוצאה השוואה מול מחיר קודם - מתוך היסטוריית מחירים
    (price_history_lookup) אם קיימת, ואם לא, מול מחיר המחירון הנוכחי (pricelist_items).

    pricelist_items: רשימת dict עם id, sku, description, price
    price_history_lookup: dict אופציונלי {normalized_description: previous_price} -
        המחיר הידוע האחרון לפני המסמך הנוכחי (יכול לכלול גם מחירים מחשבוניות קודמות,
        לא רק ממחירונים) - זה מה שמאפשר "היסטוריה" אמיתית ולא רק את הרשומה האחרונה.

    ההשוואה היא **אחוזית**, לא בשקלים בודדים, כדי לא להתריע על הפרשי עיגול:
    - שינוי קטן מ-PRICE_CHANGE_IGNORE_PCT: לא מוצג בכלל.
    - שינוי בינוני (עד PRICE_CHANGE_DANGER_PCT): מוצג כמידע ('notable'), לא מעלה
      את חומרת השורה לבד - רק אם זה קורה במספר פריטים באותה חשבונית (ראו
      apply_price_alert_policy).
    - שינוי גדול מאוד (מעל PRICE_CHANGE_DANGER_PCT): מסומן מיד כ-'danger', גם
      אם זה הפריט היחיד שחרג.
    """
    used_ids = set()
    price_history_lookup = price_history_lookup or {}

    for row in rows:
        if row.get("invoice_unit_price") is None:
            continue

        pseudo_item = {"sku": row["sku"], "description": row["description"]}
        match = find_best_match(pseudo_item, pricelist_items, used_ids)

        list_price = _to_float(match.get("price")) if match else None
        prev_price = price_history_lookup.get(_normalize(row["description"]))
        # אם יש היסטוריה אמיתית - היא עדיפה על "המחיר הנוכחי במחירון" (שיכול
        # להיות בעצמו כבר עודכן ממחיר החשבונית הזו או ממקור אחר).
        reference_price = prev_price if prev_price is not None else list_price

        row["pricelist_price"] = list_price
        row["previous_known_price"] = prev_price

        if reference_price is None or reference_price == 0:
            row["price_diff"] = None
            row["price_change_pct"] = None
            row["price_change_severity"] = None
            continue

        price_diff = round(row["invoice_unit_price"] - reference_price, 2)
        pct_change = round((price_diff / reference_price) * 100, 1)
        row["price_diff"] = price_diff
        row["price_change_pct"] = pct_change

        if abs(price_diff) <= PRICE_DIFF_TOLERANCE:
            row["price_change_severity"] = None
            continue

        item_severity = classify_price_change_pct(pct_change)
        row["price_change_severity"] = item_severity if item_severity != "ignore" else None

        direction = "עלייה" if price_diff > 0 else "ירידה"
        if item_severity == "danger":
            row["flags"].append(
                f"עלייה/ירידה חריגה במחיר לעומת המחיר הידוע הקודם: {direction} של "
                f"{abs(pct_change):.1f}% ({reference_price:.2f} ש\"ח \u2192 {row['invoice_unit_price']:.2f} ש\"ח)"
            )
            if row["severity"] != "danger":
                row["severity"] = "danger"
        elif item_severity == "notable":
            # לא מעלים חומרה כבר עכשיו - זה קורה ב-apply_price_alert_policy,
            # ורק אם זה חוזר על עצמו במספיק פריטים (לא חריגה של מוצר בודד).
            row["flags"].append(
                f"שינוי מחיר מורגש (מידע בלבד): {direction} של {abs(pct_change):.1f}% "
                f"({reference_price:.2f} ש\"ח \u2190 {row['invoice_unit_price']:.2f} ש\"ח)"
            )

    return rows


def apply_price_alert_policy(rows):
    """
    מיישם את הכלל: לא מתריעים על בסיס פריט בודד עם שינוי מחיר בינוני - רק אם:
    (א) יש פריט אחד עם שינוי *גדול מאוד* (danger) - זה כבר טופל ישירות ב-enrich_with_pricelist.
    (ב) יש כמה פריטים (MIN_ITEMS_FOR_PATTERN_ALERT ומעלה) עם שינוי בינוני ('notable') -
        זה מעיד על דפוס (למשל ספק שהעלה מחירים בהדרגה על פני כמה מוצרים), ואז
        כן מעלים את כל אותם פריטים לחומרת 'warning' ומוסיפים דגל מסכם.
    מחזיר (rows, pattern_alert: bool, pattern_count: int).
    """
    notable_rows = [r for r in rows if r.get("price_change_severity") == "notable"]

    if len(notable_rows) >= MIN_ITEMS_FOR_PATTERN_ALERT:
        for r in notable_rows:
            if r["severity"] == "ok":
                r["severity"] = "warning"
        return rows, True, len(notable_rows)

    return rows, False, len(notable_rows)


def build_summary(rows, pattern_alert=False, pattern_count=0):
    total_lines = len(rows)
    danger_count = sum(1 for r in rows if r["severity"] == "danger")
    warning_count = sum(1 for r in rows if r["severity"] == "warning")
    ok_count = total_lines - danger_count - warning_count

    overcharge_total = sum(
        (r.get("price_diff") or 0) * (r.get("invoice_quantity") or 0)
        for r in rows
        if r.get("price_diff") and r["price_diff"] > 0
    )

    return {
        "total_lines": total_lines,
        "ok_count": ok_count,
        "warning_count": warning_count,
        "danger_count": danger_count,
        "estimated_overcharge": round(overcharge_total, 2),
        "price_pattern_alert": pattern_alert,
        "price_pattern_item_count": pattern_count,
    }
