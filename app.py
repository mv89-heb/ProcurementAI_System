import os
import io
import time
import psycopg2
from psycopg2.extras import RealDictCursor  # זה החלק הכי חשוב
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz 

from google import genai
from google.genai import types

app = Flask(__name__)

# הגדרות
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://...")
client = genai.Client(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# --- פונקציות המרה ו-AI נשארות כפי שהיו ---
def convert_to_optimized_image_part(file_storage):
    file_bytes = file_storage.read()
    filename = file_storage.filename.lower()
    try:
        if filename.endswith('.pdf'):
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_data = pix.tobytes("jpeg")
            return types.Part.from_bytes(data=img_data, mime_type='image/jpeg')
        else:
            img = Image.open(io.BytesIO(file_bytes))
            img.thumbnail((1200, 1200))
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=80)
            return types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')
    except Exception as e:
        file_storage.seek(0)
        return types.Part.from_bytes(data=file_storage.read(), mime_type='application/pdf' if filename.endswith('.pdf') else 'image/jpeg')

def extract_data_with_gemini(document_part, doc_type="price"):
    import json
    prompt = "חלץ מהמסמך את כל הפריטים. החזר מק''ט (sku), תיאור (description) ומחיר ליחידה (value)." if doc_type == "price" else "חלץ מהמסמך את כל הפריטים. החזר מק''ט (sku), תיאור (description) וכמות פריטים (value)."
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[document_part, prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
    )
    return json.loads(response.text).get("items", [])

# --- להלן הפונקציות המתוקנות עם RealDictCursor ---

@app.route('/api/upload-multiple-pricelists', methods=['POST'])
def upload_multiple_pricelists():
    file = request.files['pricelists']
    supplier_name = os.path.splitext(file.filename.replace('\\', '/').split('/')[-1])[0]
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor) # תוקן
        cursor.execute("SELECT 1 FROM pricelist_items WHERE supplier_name = %s LIMIT 1", (supplier_name,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({"message": f"המחירון {supplier_name} כבר קיים."})

        doc_part = convert_to_optimized_image_part(file)
        raw_items = extract_data_with_gemini(doc_part, doc_type="price")
        
        for item in [it for it in raw_items if it.get("sku")]:
            cursor.execute("""
                INSERT INTO pricelist_items (supplier_name, sku, description, price)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (supplier_name, sku) DO UPDATE 
                SET price = EXCLUDED.price, description = EXCLUDED.description;
            """, (supplier_name, item["sku"], item.get("description", ""), float(item["value"])))
        conn.commit()
        cursor.close(); conn.close()
        return jsonify({"message": "נשמר בהצלחה"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor) # תוקן
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
        cursor = conn.cursor(cursor_factory=RealDictCursor) # תוקן
        cursor.execute("SELECT sku, description, price FROM pricelist_items WHERE supplier_name = %s", (supplier_name,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        
        pricelist = {row['sku']: {'description': row['description'], 'price': float(row['price'])} for row in rows}
        inv_part = convert_to_optimized_image_part(request.files['invoice'])
        invoice_items = extract_data_with_gemini(inv_part, "price")
        
        results = []
        for item in invoice_items:
            sku = item.get('sku')
            if not sku: continue
            inv_price = float(item.get('value', 0.0))
            base_info = pricelist.get(sku)
            results.append({
                "sku": sku, 
                "description": item.get('description', ''), 
                "approved_price": base_info['price'] if base_info else 0, 
                "invoice_price": inv_price, 
                "status": "תקין" if base_info and inv_price <= base_info['price'] else "חריגה/לא במחירון",
                "is_match": base_info and inv_price <= base_info['price']
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/compare-pricelists', methods=['POST'])
def compare_pricelists():
    try:
        selected_suppliers = request.get_json().get('suppliers', [])
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor) # תוקן
        
        format_strings = ','.join(['%s'] * len(selected_suppliers))
        cursor.execute(f"SELECT supplier_name, sku, description, price FROM pricelist_items WHERE supplier_name IN ({format_strings})", tuple(selected_suppliers))
        rows = cursor.fetchall()
        cursor.close(); conn.close()

        all_skus = {} 
        for row in rows:
            sku = row['sku']
            if sku not in all_skus: all_skus[sku] = {"description": row['description'], "prices": {}}
            all_skus[sku]["prices"][row['supplier_name']] = float(row['price'])

        winners_board = {sup: [] for sup in selected_suppliers}
        for sku, data in all_skus.items():
            prices = data["prices"]
            if len(prices) < 2: continue
            best_price = min(prices.values())
            for sup, price in prices.items():
                if price == best_price:
                    winners_board[sup].append({
                        "sku": sku, "description": data["description"],
                        "best_price": best_price, "other_prices": {s: p for s, p in prices.items() if s != sup}
                    })
        return jsonify({"winners_board": winners_board})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- שאר הפונקציות ללא שינוי (analyze, compare_quotes) ---
# (פונקציות אלו לא משתמשות ב-DB אז הן נשארות כפי שהיו)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
