import os
import io
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz
from google import genai
from google.genai import types

app = Flask(__name__, template_folder='templates')

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
client = genai.Client(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# --- פונקציות עזר (AI ועיבוד) ---
def convert_to_optimized_image_part(file_storage):
    file_bytes = file_storage.read()
    filename = file_storage.filename.lower()
    try:
        if filename.endswith('.pdf'):
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            return types.Part.from_bytes(data=pix.tobytes("jpeg"), mime_type='image/jpeg')
        else:
            img = Image.open(io.BytesIO(file_bytes))
            img.thumbnail((1200, 1200))
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=80)
            return types.Part.from_bytes(data=buf.getvalue(), mime_type='image/jpeg')
    except:
        file_storage.seek(0)
        return types.Part.from_bytes(data=file_storage.read(), mime_type='application/pdf')

def extract_data_with_gemini(document_part, doc_type="price"):
    import json
    prompt = "חלץ מהמסמך את כל הפריטים. החזר מק''ט (sku), תיאור (description) ומחיר ליחידה (value)." if doc_type == "price" else "חלץ מק''ט וכמות."
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[document_part, prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
    )
    return json.loads(response.text).get("items", [])

# --- API Endpoints ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT supplier_name FROM pricelist_items ORDER BY supplier_name")
        rows = cursor.fetchall()
        suppliers = [row['supplier_name'] for row in rows]
        cursor.close(); conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    supplier_name = request.form.get('supplier_name', '')
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT sku, description, price FROM pricelist_items WHERE supplier_name = %s", (supplier_name,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        
        pricelist = {row['sku']: {'description': row['description'], 'price': float(row['price'])} for row in rows}
        
        # עיבוד ה-AI
        part = convert_to_optimized_image_part(request.files['invoice'])
        invoice_items = extract_data_with_gemini(part, "price")
        
        results = []
        for item in invoice_items:
            sku = item.get('sku')
            inv_price = float(item.get('value', 0))
            base = pricelist.get(sku)
            results.append({
                "sku": sku,
                "description": item.get('description', ''),
                "approved_price": base['price'] if base else 0,
                "invoice_price": inv_price,
                "status": "תקין" if base and inv_price <= base['price'] else "חריגה",
                "is_match": base and inv_price <= base['price']
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
