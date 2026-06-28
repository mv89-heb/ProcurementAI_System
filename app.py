import os
import io
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz  # PyMuPDF
from google import genai
from google.genai import types

# --- הגדרות אפליקציה ---
app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # עד 10MB

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

client = genai.Client(api_key=GEMINI_API_KEY)

# --- חיבור למסד נתונים ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


# --- פונקציות עזר ---

def normalize_sku(sku):
    if not sku:
        return ""
    return sku.strip().replace("-", "").replace(" ", "").lower()


def is_price_valid(invoice_price, approved_price):
    TOLERANCE = 0.01  # 1%
    if approved_price == 0:
        return False
    return invoice_price <= approved_price * (1 + TOLERANCE)


def convert_to_optimized_image_part(file_storage):
    file_bytes = file_storage.read()
    filename = file_storage.filename.lower()

    try:
        if filename.endswith('.pdf'):
            doc = fitz.open(stream=file_bytes, filetype="pdf")

            if doc.page_count == 0:
                raise Exception("PDF ריק")

            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))

            return types.Part.from_bytes(
                data=pix.tobytes("jpeg"),
                mime_type='image/jpeg'
            )

        else:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            img.thumbnail((1200, 1200))

            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=80)

            return types.Part.from_bytes(
                data=buf.getvalue(),
                mime_type='image/jpeg'
            )

    except Exception as e:
        print("שגיאה בהמרת קובץ:", str(e))

        return types.Part.from_bytes(
            data=file_bytes,
            mime_type='application/octet-stream'
        )


def extract_data_with_gemini(document_part, doc_type="price"):
    prompt = (
        "חלץ מהמסמך את כל הפריטים והחזר JSON בלבד בפורמט: "
        "{ items: [{ sku: string, description: string, value: number }] }"
        if doc_type == "price"
        else "{ items: [{ sku: string, quantity: number }] }"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[document_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )

        data = json.loads(response.text)
        return data.get("items", [])

    except Exception as e:
        print("שגיאה בפענוח Gemini:", str(e))
        return []


# --- API ---

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT DISTINCT supplier_name
                    FROM pricelist_items
                    ORDER BY supplier_name
                """)
                rows = cursor.fetchall()

        suppliers = [row['supplier_name'] for row in rows]

        return jsonify({"suppliers": suppliers})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    supplier_name = request.form.get('supplier_name', '')

    # בדיקות קלט
    if not supplier_name:
        return jsonify({"error": "חסר שם ספק"}), 400

    if 'invoice' not in request.files:
        return jsonify({"error": "לא הועלה קובץ"}), 400

    file = request.files['invoice']

    if file.filename == "":
        return jsonify({"error": "שם קובץ ריק"}), 400

    try:
        # שליפת מחירון
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT sku, description, price
                    FROM pricelist_items
                    WHERE supplier_name = %s
                """, (supplier_name,))

                rows = cursor.fetchall()

        pricelist = {
            normalize_sku(row['sku']): {
                'description': row['description'],
                'price': float(row['price'])
            }
            for row in rows
        }

        # ניתוח קובץ עם AI
        part = convert_to_optimized_image_part(file)
        invoice_items = extract_data_with_gemini(part, "price")

        results = []

        for item in invoice_items:
            raw_sku = item.get('sku', '')
            sku = normalize_sku(raw_sku)

            try:
                invoice_price = float(item.get('value', 0))
            except:
                invoice_price = 0

            base = pricelist.get(sku)
            valid = base and is_price_valid(invoice_price, base['price'])

            results.append({
                "sku": raw_sku,
                "description": item.get('description', ''),
                "approved_price": base['price'] if base else 0,
                "invoice_price": invoice_price,
                "difference": round(invoice_price - base['price'], 2) if base else None,
                "status": "תקין" if valid else "חריגה",
                "reason": None if valid else (
                    "לא נמצא במחירון" if not base else "מחיר גבוה מהמותר"
                ),
                "is_match": bool(valid)
            })

        return jsonify({"results": results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- הפעלה ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
