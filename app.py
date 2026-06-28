import os
import io
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from PIL import Image
import fitz
from google import genai
from google.genai import types

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def normalize_sku(sku):
    if not sku:
        return ""
    return sku.strip().replace("-", "").replace(" ", "").lower()


def convert_to_optimized_image_part(file_storage):
    file_bytes = file_storage.read()
    filename = file_storage.filename.lower()

    if filename.endswith('.pdf'):
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return types.Part.from_bytes(data=pix.tobytes("jpeg"), mime_type='image/jpeg')

    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return types.Part.from_bytes(data=buf.getvalue(), mime_type='image/jpeg')


def extract_data_with_gemini(part):
    prompt = """
    חלץ טבלת מוצרים מהחשבונית.
    
    החזר JSON בלבד:
    {
      "items": [
        {
          "sku": "string",
          "description": "string",
          "value": number
        }
      ]
    }
    
    אם אין מק\"ט ברור - נחש.
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0
            )
        )

        data = json.loads(response.text)
        return data.get("items", [])

    except Exception as e:
        print("❌ Gemini error:", e)
        return []


@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():

    supplier_name = request.form.get('supplier_name', '')
    file = request.files.get('invoice')

    if not supplier_name or not file:
        return jsonify({"error": "חסר ספק או קובץ"}), 400

    debug_info = {}

    try:
        # --- DB ---
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT sku, description, price
                    FROM pricelist_items
                    WHERE supplier_name = %s
                """, (supplier_name,))
                rows = cursor.fetchall()

        debug_info["db_count"] = len(rows)

        pricelist = {
            normalize_sku(r['sku']): {
                "price": float(r['price'])
            } for r in rows
        }

        # --- AI ---
        part = convert_to_optimized_image_part(file)
        invoice_items = extract_data_with_gemini(part)

        debug_info["ai_items"] = invoice_items[:5]

        results = []

        for item in invoice_items:
            raw_sku = item.get("sku", "")
            sku = normalize_sku(raw_sku)

            price = float(item.get("value", 0))
            base = pricelist.get(sku)

            results.append({
                "sku": raw_sku,
                "invoice_price": price,
                "approved_price": base["price"] if base else None,
                "found_in_db": base is not None
            })

        return jsonify({
            "results": results,
            "debug": debug_info
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "debug": debug_info
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
