import os
import io
import time
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz  # PyMuPDF
import psycopg2
from psycopg2.extras import RealDictCursor

from google import genai
from google.genai import types

app = Flask(__name__)

# ================= התקנות והגדרות סביבה =================
# חובה להכניס את מפתח ה-Gemini שלך
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# חובה להכניס את חיבור ה-Neon שלך
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://USER:PASSWORD@ep-xxx.neon.tech/neondb?sslmode=require")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        print("--- חיבור ל-DB הצליח! ---")
        return conn
    except Exception as e:
        print(f"--- שגיאה בחיבור ל-DB: {e} ---")
        raise e

item_schema = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "items": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "sku": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                    "value": types.Schema(type=types.Type.NUMBER)
                },
                required=["sku", "value"]
            )
        )
    }
)

def extract_data_with_gemini(document_part, doc_type="price"):
    prompt = "חלץ מהמסמך את כל הפריטים. החזר מק''ט (sku), תיאור (description) ומחיר ליחידה (value)."
    if doc_type == "qty":
        prompt = "חלץ מהמסמך את כל הפריטים. החזר מק''ט (sku), תיאור (description) וכמות פריטים (value)."

    import json
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[document_part, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=item_schema,
            temperature=0.0
        ),
    )
    return json.loads(response.text).get("items", [])


# ================= ראוטרים (API Routes) =================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/upload-multiple-pricelists', methods=['POST'])
def upload_multiple_pricelists():
    if 'pricelists' not in request.files: return jsonify({"error": "לא נבחרו קבצים"}), 400
    file = request.files['pricelists']
    if not file or not file.filename.endswith('.pdf'): return jsonify({"error": "קובץ לא תקין"}), 400

    clean_filename = file.filename.replace('\\', '/').split('/')[-1]
    supplier_name = os.path.splitext(clean_filename)[0]
    
    try:
        # בדיקה מהירה מול ה-DB האם הספק כבר קיים (מונע שליחה מיותרת לגוגל)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM pricelist_items WHERE supplier_name = %s LIMIT 1", (supplier_name,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({"message": f"המחירון {supplier_name} כבר קיים במערכת."})

        doc_part = convert_to_optimized_image_part(file)
        raw_items = extract_data_with_gemini(doc_part, doc_type="price")
        
        valid_items = [item for item in raw_items if item.get("sku")]
        if not valid_items:
            cursor.close(); conn.close()
            return jsonify({"error": "לא נמצאו נתונים"}), 400

        # הכנסה למאגר SQL
        for item in valid_items:
            cursor.execute("""
                INSERT INTO pricelist_items (supplier_name, sku, description, price)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (supplier_name, sku) DO UPDATE 
                SET price = EXCLUDED.price, description = EXCLUDED.description;
            """, (supplier_name, item["sku"], item.get("description", ""), float(item["value"])))
            
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": f"נשמר בהצלחה: {supplier_name}"})
        
    except Exception as e:
        print(f"שגיאה בהעלאה: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT supplier_name FROM pricelist_items ORDER BY supplier_name")
        suppliers = [row['supplier_name'] for row in cursor.fetchall()]
        cursor.close(); conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
def analyze_documents():
    try:
        del_items = extract_data_with_gemini(convert_to_optimized_image_part(request.files['delivery']), "qty")
        inv_items = extract_data_with_gemini(convert_to_optimized_image_part(request.files['invoice']), "qty")
        del_dict = {item['sku']: item for item in del_items if item.get('sku')}
        inv_dict = {item['sku']: item for item in inv_items if item.get('sku')}
        
        results = []
        for sku, del_info in del_dict.items():
            inv_info = inv_dict.get(sku)
            del_qty = int(del_info.get('value', 0))
            if not inv_info:
                inv_qty, status, is_match = 0, "חסר בחשבונית!", False
            else:
                inv_qty = int(inv_info.get('value', 0))
                status, is_match = (f"פער! חסר {del_qty - inv_qty}", False) if inv_qty != del_qty else ("תקין", True)

            results.append({"sku": sku, "description": del_info.get('description', ''), "delivery_qty": del_qty, "invoice_qty": inv_qty, "status": status, "is_match": is_match})
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    supplier_name = request.form.get('supplier_name', '')
    
    try:
        # טעינת המחירון הספציפי מה-SQL
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku, description, price FROM pricelist_items WHERE supplier_name = %s", (supplier_name,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        
        if not rows: return jsonify({"error": "המחירון לא קיים או ריק."}), 404
        
        pricelist = {row['sku']: {'description': row['description'], 'price': float(row['price'])} for row in rows}

        inv_part = convert_to_optimized_image_part(request.files['invoice'])
        
        check = client.models.generate_content(model='gemini-2.5-flash', contents=[inv_part, "מה שם הספק המנפיק בחשבונית? רק שם."])
        if supplier_name.lower() not in check.text.lower():
            return jsonify({"error": f"שים לב: החשבונית שייכת ל-{check.text}, בחרת {supplier_name}!", "is_mismatch": True}), 400

        invoice_items = extract_data_with_gemini(inv_part, "price")
        results, has_alerts = [], False
        
        for item in invoice_items:
            sku = item.get('sku')
            if not sku: continue
            inv_price = float(item.get('value', 0.0))
            base_info = pricelist.get(sku)
            
            if not base_info:
                status, approved, match = "לא במחירון!", 0.0, False
                has_alerts = True
            elif inv_price > base_info['price']:
                status, approved, match = f"חיוב יתר של {round(inv_price - base_info['price'], 2)} ₪", base_info['price'], False
                has_alerts = True
            else:
                status, approved, match = "תקין", base_info['price'], True

            results.append({"sku": sku, "description": item.get('description', ''), "approved_price": approved, "invoice_price": inv_price, "status": status, "is_match": match})
            
        return jsonify({"results": results, "has_alerts": has_alerts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/compare-quotes', methods=['POST'])
def compare_quotes():
    try:
        threshold = float(request.form.get('threshold', 5.0))
        
        part_a = convert_to_optimized_image_part(request.files['quote_a'])
        items_a = extract_data_with_gemini(part_a, "price")
        time.sleep(2) 
        part_b = convert_to_optimized_image_part(request.files['quote_b'])
        items_b = extract_data_with_gemini(part_b, "price")

        dict_a = {item['sku']: item for item in items_a if item.get('sku')}
        dict_b = {item['sku']: item for item in items_b if item.get('sku')}

        results = []
        total_a = total_b = total_split = 0.0

        common_skus = set(dict_a.keys()).intersection(set(dict_b.keys()))
        if not common_skus: return jsonify({"error": "לא נמצאו מק\"טים חופפים."}), 400

        for sku in common_skus:
            price_a = float(dict_a[sku].get('value', 0))
            price_b = float(dict_b[sku].get('value', 0))
            desc = dict_a[sku].get('description', '')

            diff_abs = abs(price_a - price_b)
            min_price = min(price_a, price_b)
            diff_pct = (diff_abs / min_price * 100) if min_price > 0 else 0

            winner = "A" if price_a < price_b else ("B" if price_b < price_a else "Tie")
            gap_type = "זניח" if diff_pct <= threshold else "משמעותי"

            total_a += price_a
            total_b += price_b
            total_split += min_price

            results.append({"sku": sku, "description": desc, "price_a": price_a, "price_b": price_b, "winner": winner, "diff_pct": diff_pct, "gap_type": gap_type})

        best_single_total = min(total_a, total_b)
        best_single_name = "הצעה א'" if total_a < total_b else "הצעה ב'"
        savings_value = best_single_total - total_split
        savings_percent = (savings_value / best_single_total * 100) if best_single_total > 0 else 0

        if savings_percent > threshold:
            recommendation = f"💡 המלצה: לפצל את ההזמנה! הפיצול חוסך {savings_value:.2f} ₪ ({savings_percent:.1f}%)."
            action_class = "split-alert"
        else:
            recommendation = f"📦 המלצה: להזמין הכל מ{best_single_name}. פיצול יחסוך רק {savings_value:.2f} ₪ ({savings_percent:.1f}%), שזה זניח."
            action_class = "consolidate-alert"

        summary = {"total_a": round(total_a, 2), "total_b": round(total_b, 2), "total_split": round(total_split, 2), "savings_value": round(savings_value, 2), "savings_percent": round(savings_percent, 1), "recommendation": recommendation, "action_class": action_class}

        return jsonify({"results": results, "summary": summary})

    except Exception as e:
        return jsonify({"error": "אירעה שגיאה בעיבוד ההצעות."}), 500

@app.route('/api/compare-pricelists', methods=['POST'])
def compare_pricelists():
    try:
        data = request.get_json()
        selected_suppliers = data.get('suppliers', [])
        
        if len(selected_suppliers) < 2: return jsonify({"error": "יש לבחור לפחות 2 ספקים."}), 400

        # שליפת הנתונים מה-SQL
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # שימוש ב-IN clause כדי לשלוף רק את הספקים שנבחרו
        format_strings = ','.join(['%s'] * len(selected_suppliers))
        cursor.execute(f"SELECT supplier_name, sku, description, price FROM pricelist_items WHERE supplier_name IN ({format_strings})", tuple(selected_suppliers))
        rows = cursor.fetchall()
        cursor.close(); conn.close()

        all_skus = {} 
        for row in rows:
            sku = row['sku']
            sup = row['supplier_name']
            if sku not in all_skus:
                all_skus[sku] = {"description": row['description'], "prices": {}}
            all_skus[sku]["prices"][sup] = float(row['price'])

        winners_board = {sup: [] for sup in selected_suppliers}
        
        for sku, data in all_skus.items():
            prices = data["prices"]
            if len(prices) < 2: continue
                
            best_price = min(prices.values())
            best_sups = [s for s, p in prices.items() if p == best_price]
            
            for sup in best_sups:
                winners_board[sup].append({
                    "sku": sku,
                    "description": data["description"],
                    "best_price": best_price,
                    "other_prices": {s: p for s, p in prices.items() if s != sup}
                })
                
        for sup in winners_board:
            winners_board[sup] = sorted(winners_board[sup], key=lambda x: x['sku'])
                
        return jsonify({"winners_board": winners_board})

    except Exception as e:
        return jsonify({"error": "אירעה שגיאה בעיבוד הנתונים."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
