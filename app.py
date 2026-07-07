# -*- coding: utf-8 -*-
import os
import sys
import re
from flask import Flask, request, jsonify, render_template
import psycopg2
from werkzeug.utils import secure_filename
from ai_engine import AIEngine
from core.database import init_db
from core.matching import match_invoice_to_delivery, enrich_with_pricelist, apply_price_alert_policy, build_summary, _normalize as normalize_description

# הגדרת קידוד לפלט הטרמינל (למניעת שגיאות ב-Windows/Linux)
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# אתחול השרת - קורא אוטומטית מקבצים בתיקיית templates/
app = Flask(__name__)

# הגדרות סביבה - נלקחות אוטומטית מ-Render
DATABASE_URL = os.environ.get("DATABASE_URL", "הכנס_כאן_את_הקישור_מ_NEON")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "הכנס_מפתח")
COMPANY_ID = "demo_company"

# מפתח חיבור פשוט להגנה על נקודת ההעלאה כשהיא נקראת מהסקריפט המקומי (naps2_watcher.py)
UPLOAD_API_KEY = os.environ.get("UPLOAD_API_KEY", "")

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}

# אתחול מנוע ה-AI
ai_engine = AIEngine(api_key=GEMINI_API_KEY)

# מוודא שהטבלאות קיימות גם אם build.sh לא רץ (למשל בהרצה מקומית)
try:
    init_db()
except Exception as e:
    print("DB init skipped/failed on startup:", e)

# סנכרון אוטומטי של מחירוני הספקים + טלפונים מתוך legacy_data/ בכל עליית האפליקציה.
# רץ כאן (ולא רק ב-build.sh) כי כאן בוודאות יש גישה ל-DATABASE_URL בזמן ריצה ב-Render.
# בטוח לגמרי להרצה חוזרת (UPSERT) - לא יוצר כפילויות בכל restart/דיפלוי.
try:
    from scripts.import_legacy_data import run_import
    _legacy_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy_data")
    if os.path.isdir(_legacy_data_dir):
        run_import(_legacy_data_dir)
    else:
        print("סנכרון legacy_data דולג: התיקייה legacy_data/ לא נמצאה בדיפלוי.")
except Exception as e:
    print("סנכרון legacy_data נכשל (לא עוצר את עליית האפליקציה):", e)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def log_price_history(cur, supplier_name, sku, description, price, source, source_file=None, document_id=None):
    """מוסיף רשומה ליומן היסטוריית המחירים. תמיד INSERT בלבד (append-only) -
    זה מה שמאפשר להשוות בעתיד מול מחירים קודמים, לא רק מול המחיר האחרון."""
    if not description or price is None:
        return
    cur.execute("""
        INSERT INTO price_history (company_id, supplier_name, sku, description, price, source, source_file, document_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (COMPANY_ID, supplier_name, sku or "", description, price, source, source_file, document_id))


def get_previous_prices(cur, supplier_name, exclude_document_id=None):
    """שולף עבור כל תיאור מוצר של הספק את המחיר הידוע האחרון (מכל מקור - מחירון
    או חשבונית קודמת), תוך התעלמות מרשומות ששייכות למסמך הנוכחי (exclude_document_id)
    כדי לא להשוות מחיר לעצמו. מחזיר dict {normalized_description: price}."""
    cur.execute("""
        SELECT DISTINCT ON (description) description, price
        FROM price_history
        WHERE company_id = %s AND supplier_name = %s
          AND (%s::int IS NULL OR document_id IS NULL OR document_id != %s)
        ORDER BY description, recorded_at DESC
    """, (COMPANY_ID, supplier_name, exclude_document_id, exclude_document_id))
    return {normalize_description(desc): float(price) for desc, price in cur.fetchall()}


def guess_supplier_from_filename(filename):
    """מנחש שם ספק מתוך שם הקובץ, למשל 'אחוואה_מחירון_2026-07-06.pdf' -> 'אחוואה'"""
    name = os.path.splitext(filename)[0]
    for sep in ["_", "-", "#"]:
        if sep in name:
            return name.split(sep)[0].strip()
    return name.strip()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    """הצגת ממשק המערכת"""
    return render_template('index.html')

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    """שליפת רשימת הספקים מה-DB"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT supplier_name FROM pricelist_items WHERE company_id = %s ORDER BY supplier_name", (COMPANY_ID,))
        suppliers = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        print("Error fetching suppliers:", e)
        return jsonify({"error": "שגיאה בשליפת ספקים"}), 500

@app.route('/api/compare-suppliers', methods=['POST'])
def compare_suppliers():
    """הצלבת מוצרים וחילוץ פערי מחירים"""
    data = request.json or {}
    suppliers = data.get('suppliers', [])
    
    if len(suppliers) < 2:
        return jsonify({"error": "יש לבחור לפחות 2 ספקים להשוואה"}), 400
        
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        query = """
            SELECT description, supplier_name, price, sku 
            FROM pricelist_items 
            WHERE company_id = %s AND supplier_name = ANY(%s)
        """
        cur.execute(query, (COMPANY_ID, suppliers))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        products_dict = {}
        for desc, sup, price, sku in rows:
            product_key = desc.strip() if desc and desc.strip() else f"מוצר ללא תיאור (מק\"ט {sku})"
            if product_key not in products_dict:
                products_dict[product_key] = []
            products_dict[product_key].append({"supplier": sup, "price": float(price)})
        
        results = []
        for product, offers in products_dict.items():
            if len(offers) > 1:
                best_offer = min(offers, key=lambda x: x['price'])
                for o in offers:
                    o['difference'] = round(o['price'] - best_offer['price'], 2)
                    
                results.append({
                    "product": product,
                    "offers": offers,
                    "best_supplier": best_offer['supplier'],
                    "best_price": best_offer['price']
                })
        
        savings_count = sum(1 for r in results if any(o['difference'] > 0 for o in r['offers']))
        insights = [
            f"הצלבת הנתונים הושלמה: נמצאו {len(results)} מוצרים זהים.",
            f"זוהו {savings_count} הזדמנויות למשא ומתן והוזלת עלויות."
        ]
        
        return jsonify({"results": results, "ranking": suppliers, "insights": insights})
    except Exception as e:
        print("Compare Error:", e)
        return jsonify({"error": "שגיאה בעיבוד הנתונים"}), 500

@app.route('/api/get-supplier-pricelist', methods=['POST'])
def get_supplier_pricelist():
    """שליפת מחירון מלא של ספק ספציפי"""
    data = request.json or {}
    supplier = data.get('supplier')
    
    if not supplier:
        return jsonify({"error": "לא צוין שם ספק"}), 400
        
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "SELECT sku, description, price FROM pricelist_items WHERE company_id = %s AND supplier_name = %s ORDER BY description"
        cur.execute(query, (COMPANY_ID, supplier))
        
        items = [{"sku": r[0], "description": r[1], "price": float(r[2])} for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        return jsonify({"items": items, "supplier": supplier})
    except Exception as e:
        print("Error fetching pricelist:", e)
        return jsonify({"error": "שגיאה בשליפת המחירון ממסד הנתונים"}), 500

@app.route('/api/suppliers', methods=['GET'])
def list_suppliers_full():
    """רשימת כל הספקים במערכת (טבלת suppliers המרכזית), כולל קטגוריה ומספר פריטי קטלוג.
    זהו ה-endpoint ה'מסודר' לניהול ספקים - בניגוד ל-/api/get-suppliers הישן שרק
    שולף שמות ספקים מתוך pricelist_items (המחירונים הסרוקים דרך NAPS2)."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.name, s.category,
                   (SELECT COUNT(*) FROM supplier_catalog_items p WHERE p.supplier_id = s.id) AS catalog_items_count,
                   (SELECT COUNT(*) FROM supplier_contacts c WHERE c.supplier_id = s.id) AS contacts_count
            FROM suppliers s
            WHERE s.company_id = %s
            ORDER BY s.category NULLS LAST, s.name
        """, (COMPANY_ID,))
        suppliers = [
            {
                "id": r[0], "name": r[1], "category": r[2],
                "catalog_items_count": r[3], "contacts_count": r[4],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        print("Error fetching suppliers:", e)
        return jsonify({"error": "שגיאה בשליפת רשימת הספקים"}), 500


@app.route('/api/supplier-catalog', methods=['GET'])
def get_supplier_catalog():
    """שליפת כל פריטי הקטלוג (מחירון) של ספק ספציפי מתוך הנתונים שיובאו מקבצי
    האקסל - נפרד לגמרי מ-/api/get-supplier-pricelist הישן.
    שימוש: /api/supplier-catalog?supplier=שם"""
    supplier_filter = request.args.get('supplier')
    if not supplier_filter:
        return jsonify({"error": "יש לציין פרמטר supplier"}), 400
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT sku, description, unit, category, price, source_file, source_sheet
            FROM supplier_catalog_items
            WHERE company_id = %s AND supplier_name = %s
            ORDER BY description
        """, (COMPANY_ID, supplier_filter))
        items = [
            {
                "sku": r[0], "description": r[1], "unit": r[2], "category": r[3],
                "price": float(r[4]), "source_file": r[5], "source_sheet": r[6],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"supplier": supplier_filter, "items": items})
    except Exception as e:
        print("Error fetching supplier catalog:", e)
        return jsonify({"error": "שגיאה בשליפת קטלוג הספק"}), 500


@app.route('/api/price-history', methods=['GET'])
def get_price_history():
    """שליפת היסטוריית מחירים מלאה לפריט מסוים אצל ספק - כל המחירים שנצפו
    לאורך זמן, ממחירונים וגם מחשבוניות שנסרקו, מהחדש לישן.
    שימוש: /api/price-history?supplier=שם&description=תיאור המוצר"""
    supplier_filter = request.args.get('supplier')
    description_filter = request.args.get('description')
    if not supplier_filter or not description_filter:
        return jsonify({"error": "יש לציין supplier ו-description"}), 400
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT price, source, source_file, recorded_at
            FROM price_history
            WHERE company_id = %s AND supplier_name = %s AND description = %s
            ORDER BY recorded_at DESC
        """, (COMPANY_ID, supplier_filter, description_filter))
        history = [
            {
                "price": float(r[0]), "source": r[1], "source_file": r[2],
                "recorded_at": r[3].isoformat() if r[3] else None,
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"supplier": supplier_filter, "description": description_filter, "history": history})
    except Exception as e:
        print("Error fetching price history:", e)
        return jsonify({"error": "שגיאה בשליפת היסטוריית המחירים"}), 500


@app.route('/api/supplier-contacts', methods=['GET'])
def get_supplier_contacts():
    """שליפת פרטי קשר (טלפון/נייד/איש קשר) לספק - נפרד לגמרי ממחירון הספק.
    תמיכה בפרמטר query אופציונלי: /api/supplier-contacts?supplier=שם"""
    supplier_filter = request.args.get('supplier')
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            SELECT s.name, c.phone, c.phone_2, c.mobile, c.contact_person,
                   c.customer_number, c.delivery_days
            FROM supplier_contacts c
            JOIN suppliers s ON s.id = c.supplier_id
            WHERE s.company_id = %s
        """
        params = [COMPANY_ID]
        if supplier_filter:
            query += " AND s.name = %s"
            params.append(supplier_filter)
        query += " ORDER BY s.name"
        cur.execute(query, tuple(params))
        contacts = [
            {
                "supplier": r[0], "phone": r[1], "phone_2": r[2], "mobile": r[3],
                "contact_person": r[4], "customer_number": r[5], "delivery_days": r[6],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"contacts": contacts})
    except Exception as e:
        print("Error fetching supplier contacts:", e)
        return jsonify({"error": "שגיאה בשליפת פרטי הקשר של הספקים"}), 500


@app.route('/api/generate-negotiation', methods=['POST'])
def generate_negotiation():
    """פנייה למנוע ג'מיני לניסוח אימייל מו"מ"""
    data = request.json or {}
    supplier = data.get('supplier', 'ספק')
    product = data.get('product', 'מוצר')
    current_price = data.get('currentPrice', 0)
    target_price = data.get('targetPrice', 0)
    aggressive = data.get('aggressive', False)

    tone = "אסרטיבי ומציב אלטרנטיבות שוק" if aggressive else "שותפותי ומקצועי"
    prompt = f"""
    אתה מנהל רכש בכיר. כתוב פנייה קצרה ורשמית באימייל לספק כדי לבקש התאמת מחיר.
    - ספק: {supplier}
    - מוצר: {product}
    - מחיר נוכחי: {current_price} ש"ח
    - מחיר יעד בשוק: {target_price} ש"ח
    כתוב בעברית עסקית. טון: {tone}. החזר אך ורק את טקסט המכתב המוכן לשליחה.
    """

    try:
        res = ai_engine.client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return jsonify({"draft": res.text.strip(), "success": True})
    except Exception as e:
        print(f"Negotiation AI Error: {e}")
        return jsonify({"error": "מנוע ה-AI לא זמין כרגע", "success": False}), 500

@app.route('/api/upload-pricelist', methods=['POST'])
def upload_pricelist():
    """
    נקודת הכניסה המרכזית של הזרימה מ-NAPS2/סריקה למערכת:
    מקבלת קובץ (PDF/תמונה), שולחת ל-AI לחילוץ מחירון, ושומרת/מעדכנת ב-DB.
    נתמכת גם קריאה ידנית מהממשק וגם קריאה אוטומטית מסקריפט ה-watcher.
    """
    # הגנה בסיסית - אם הוגדר מפתח, לוודא שהוא תואם (רלוונטי לקריאות מהמחשב המקומי)
    if UPLOAD_API_KEY:
        sent_key = request.headers.get("X-API-Key", "")
        if sent_key != UPLOAD_API_KEY:
            return jsonify({"error": "מפתח גישה שגוי"}), 401

    if 'file' not in request.files:
        return jsonify({"error": "לא נשלח קובץ"}), 400

    file = request.files['file']
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "סוג קובץ לא נתמך (יש להעלות PDF/JPG/PNG)"}), 400

    filename = secure_filename(file.filename)
    supplier = (request.form.get('supplier') or '').strip()
    if not supplier:
        supplier = guess_supplier_from_filename(filename)

    conn = None
    try:
        items = ai_engine.extract_pricelist(file)

        conn = get_db_connection()
        cur = conn.cursor()

        if not items:
            cur.execute("""
                INSERT INTO upload_log (company_id, filename, supplier_name, items_extracted, status, error_message)
                VALUES (%s, %s, %s, 0, 'no_items_found', 'AI לא זיהה פריטים במסמך')
            """, (COMPANY_ID, filename, supplier))
            conn.commit()
            return jsonify({"error": "לא זוהו פריטים במסמך - ודא שהסריקה ברורה", "supplier": supplier}), 422

        for it in items:
            sku = (it.get('sku') or '').strip()
            desc = (it.get('description') or '').strip()
            try:
                price = float(it.get('price') or 0)
            except (TypeError, ValueError):
                price = 0.0

            log_price_history(cur, supplier, sku, desc, price, source='pricelist', source_file=filename)

            cur.execute("""
                INSERT INTO pricelist_items (company_id, supplier_name, sku, description, price, source_file)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, supplier_name, sku, description) DO UPDATE
                SET price = EXCLUDED.price, source_file = EXCLUDED.source_file, uploaded_at = NOW();
            """, (COMPANY_ID, supplier, sku, desc, price, filename))

        cur.execute("""
            INSERT INTO upload_log (company_id, filename, supplier_name, items_extracted, status)
            VALUES (%s, %s, %s, %s, 'success')
        """, (COMPANY_ID, filename, supplier, len(items)))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "supplier": supplier,
            "items_count": len(items),
            "filename": filename
        })
    except Exception as e:
        print("Upload Error:", e)
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return jsonify({"error": "שגיאה בעיבוד הקובץ - נסה שוב או בדוק את איכות הסריקה"}), 500


@app.route('/api/upload-document', methods=['POST'])
def upload_document():
    """
    העלאת חשבונית או תעודת משלוח סרוקה. שולפת כותרת + שורות פריטים דרך ה-AI
    ושומרת בטבלאות documents / document_items, לשימוש בהמשך בהצלבה.
    form-data: file, doc_type ('invoice' | 'delivery_note'), supplier (אופציונלי - override)
    """
    if UPLOAD_API_KEY:
        sent_key = request.headers.get("X-API-Key", "")
        if sent_key != UPLOAD_API_KEY:
            return jsonify({"error": "מפתח גישה שגוי"}), 401

    doc_type = (request.form.get('doc_type') or '').strip()
    if doc_type not in ('invoice', 'delivery_note'):
        return jsonify({"error": "יש לציין doc_type תקין: invoice או delivery_note"}), 400

    if 'file' not in request.files:
        return jsonify({"error": "לא נשלח קובץ"}), 400

    file = request.files['file']
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"error": "סוג קובץ לא נתמך (יש להעלות PDF/JPG/PNG)"}), 400

    filename = secure_filename(file.filename)
    manual_supplier = (request.form.get('supplier') or '').strip()

    conn = None
    try:
        extracted = ai_engine.extract_document(file, doc_type=doc_type)
        items = extracted.get('items', [])

        if not items:
            return jsonify({"error": "לא זוהו שורות פריטים במסמך - ודא שהסריקה ברורה"}), 422

        supplier = manual_supplier or (extracted.get('supplier_name') or '').strip() or guess_supplier_from_filename(filename)

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO documents (company_id, doc_type, doc_number, reference_number, supplier_name, doc_date, source_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            COMPANY_ID, doc_type,
            extracted.get('doc_number') or '',
            extracted.get('reference_number') or '',
            supplier,
            extracted.get('doc_date') or '',
            filename
        ))
        document_id = cur.fetchone()[0]

        for it in items:
            sku = (it.get('sku') or '').strip()
            desc = (it.get('description') or '').strip()
            try:
                quantity = float(it.get('quantity') or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            try:
                unit_price = float(it.get('unit_price') or 0)
            except (TypeError, ValueError):
                unit_price = 0.0
            try:
                line_total = float(it.get('line_total') or (quantity * unit_price))
            except (TypeError, ValueError):
                line_total = quantity * unit_price

            cur.execute("""
                INSERT INTO document_items (document_id, sku, description, quantity, unit_price, line_total)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (document_id, sku, desc, quantity, unit_price, line_total))

            if doc_type == 'invoice' and unit_price:
                log_price_history(cur, supplier, sku, desc, unit_price, source='invoice',
                                   source_file=filename, document_id=document_id)

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "document_id": document_id,
            "doc_type": doc_type,
            "supplier": supplier,
            "doc_number": extracted.get('doc_number') or '',
            "items_count": len(items)
        })
    except Exception as e:
        print("Upload Document Error:", e)
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return jsonify({"error": "שגיאה בעיבוד המסמך"}), 500


@app.route('/api/list-documents', methods=['GET'])
def list_documents():
    """שליפת רשימת מסמכים (חשבוניות/תעודות משלוח) לבחירה בממשק, ממוינת מהחדש לישן"""
    doc_type = request.args.get('doc_type')
    supplier = request.args.get('supplier')

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        query = """
            SELECT d.id, d.doc_type, d.doc_number, d.reference_number, d.supplier_name, d.doc_date,
                   d.source_file, d.uploaded_at, COUNT(di.id) as item_count
            FROM documents d
            LEFT JOIN document_items di ON di.document_id = d.id
            WHERE d.company_id = %s
        """
        params = [COMPANY_ID]

        if doc_type in ('invoice', 'delivery_note'):
            query += " AND d.doc_type = %s"
            params.append(doc_type)
        if supplier:
            query += " AND d.supplier_name = %s"
            params.append(supplier)

        query += " GROUP BY d.id ORDER BY d.uploaded_at DESC LIMIT 100"

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        documents = [{
            "id": r[0], "doc_type": r[1], "doc_number": r[2], "reference_number": r[3],
            "supplier_name": r[4], "doc_date": r[5], "source_file": r[6],
            "uploaded_at": r[7].isoformat() if r[7] else None, "item_count": r[8]
        } for r in rows]

        return jsonify({"documents": documents})
    except Exception as e:
        print("List Documents Error:", e)
        return jsonify({"error": "שגיאה בשליפת רשימת המסמכים"}), 500


@app.route('/api/match-documents', methods=['POST'])
def match_documents():
    """
    מריץ הצלבה בין חשבונית לתעודת משלוח (+ בדיקת פערי מחיר מול המחירון של הספק).
    body: { "invoice_id": int, "delivery_note_id": int (אופציונלי - אם ריק ננסה לאתר אוטומטית) }
    """
    data = request.json or {}
    invoice_id = data.get('invoice_id')
    delivery_note_id = data.get('delivery_note_id')

    if not invoice_id:
        return jsonify({"error": "יש לציין invoice_id"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, doc_type, doc_number, reference_number, supplier_name FROM documents WHERE id = %s AND company_id = %s", (invoice_id, COMPANY_ID))
        invoice_doc = cur.fetchone()
        if not invoice_doc:
            return jsonify({"error": "החשבונית שנבחרה לא נמצאה"}), 404

        _, _, invoice_doc_number, reference_number, supplier_name = invoice_doc

        # אם לא נבחרה תעודת משלוח ידנית - ננסה לאתר אוטומטית: קודם לפי מספר תעודה תואם,
        # ואם לא נמצא - התעודה האחרונה של אותו ספק
        if not delivery_note_id:
            if reference_number:
                cur.execute("""
                    SELECT id FROM documents
                    WHERE company_id = %s AND doc_type = 'delivery_note' AND supplier_name = %s AND doc_number = %s
                    ORDER BY uploaded_at DESC LIMIT 1
                """, (COMPANY_ID, supplier_name, reference_number))
                row = cur.fetchone()
                if row:
                    delivery_note_id = row[0]

            if not delivery_note_id:
                cur.execute("""
                    SELECT id FROM documents
                    WHERE company_id = %s AND doc_type = 'delivery_note' AND supplier_name = %s
                    ORDER BY uploaded_at DESC LIMIT 1
                """, (COMPANY_ID, supplier_name))
                row = cur.fetchone()
                if row:
                    delivery_note_id = row[0]

        if not delivery_note_id:
            return jsonify({"error": f"לא נמצאה תעודת משלוח מתאימה לספק \"{supplier_name}\" - יש להעלות אחת או לבחור ידנית"}), 404

        cur.execute("SELECT id, sku, description, quantity, unit_price FROM document_items WHERE document_id = %s", (invoice_id,))
        invoice_items = [{"id": r[0], "sku": r[1], "description": r[2], "quantity": r[3], "unit_price": r[4]} for r in cur.fetchall()]

        cur.execute("SELECT id, sku, description, quantity FROM document_items WHERE document_id = %s", (delivery_note_id,))
        delivery_items = [{"id": r[0], "sku": r[1], "description": r[2], "quantity": r[3]} for r in cur.fetchall()]

        cur.execute("SELECT id, sku, description, price FROM pricelist_items WHERE company_id = %s AND supplier_name = %s", (COMPANY_ID, supplier_name))
        pricelist_items = [{"id": r[0], "sku": r[1], "description": r[2], "price": r[3]} for r in cur.fetchall()]

        price_history_lookup = get_previous_prices(cur, supplier_name, exclude_document_id=invoice_id)

        rows = match_invoice_to_delivery(invoice_items, delivery_items)
        rows = enrich_with_pricelist(rows, pricelist_items, price_history_lookup=price_history_lookup)
        rows, pattern_alert, pattern_count = apply_price_alert_policy(rows)
        summary = build_summary(rows, pattern_alert=pattern_alert, pattern_count=pattern_count)

        cur.execute("""
            INSERT INTO match_log (company_id, invoice_id, delivery_note_id, danger_count, warning_count, estimated_overcharge)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (COMPANY_ID, invoice_id, delivery_note_id, summary['danger_count'], summary['warning_count'], summary['estimated_overcharge']))
        conn.commit()

        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "invoice_id": invoice_id,
            "delivery_note_id": delivery_note_id,
            "supplier_name": supplier_name,
            "rows": rows,
            "summary": summary
        })
    except Exception as e:
        print("Match Documents Error:", e)
        return jsonify({"error": "שגיאה בהרצת ההצלבה"}), 500


@app.route('/api/get-dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    """שליפת נתוני אמת סטטיסטיים מלוח המחירונים עבור ה-Dashboard"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM pricelist_items WHERE company_id = %s", (COMPANY_ID,))
        total_items = cur.fetchone()[0]
        
        cur.execute("""
            SELECT supplier_name, COUNT(*) 
            FROM pricelist_items 
            WHERE company_id = %s 
            GROUP BY supplier_name 
            ORDER BY COUNT(*) DESC LIMIT 5
        """, (COMPANY_ID,))
        
        distribution = [{"supplier": row[0], "count": row[1]} for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        return jsonify({
            "total_items": total_items,
            "distribution": distribution
        })
    except Exception as e:
        print("Dashboard Stats Error:", e)
        return jsonify({"error": "שגיאה בשליפת נתוני לוח בקרה"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
