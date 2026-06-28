import os
import io
import json
import time
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz  # PyMuPDF

from google import genai
from google.genai import types

app = Flask(__name__)

# --- חובה: מפתח ה-API שלך ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6L-8tRXy__KdraSbkW3Ndga4O02i_Lm8cEgw6R4nfjHtQ")
client = genai.Client(api_key=GEMINI_API_KEY)

# ניהול מסד נתונים
DB_DIR = os.path.join(os.path.dirname(__file__), 'database')
if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

def load_pricelists():
    db = {}
    for filename in os.listdir(DB_DIR):
        if filename.endswith('.json'):
            sup_name = os.path.splitext(filename)[0]
            try:
                with open(os.path.join(DB_DIR, filename), 'r', encoding='utf-8') as f:
                    db[sup_name] = json.load(f)
            except:
                pass
    return db

PRICELISTS_DB = load_pricelists()

# --- מנוע תמונה והכנה ל-AI ---
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
        print(f"שגיאה בהמרה: {e}")
        file_storage.seek(0)
        mime = 'application/pdf' if filename.endswith('.pdf') else 'image/jpeg'
        return types.Part.from_bytes(data=file_storage.read(), mime_type=mime)

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


# --- הראוטרים ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/upload-multiple-pricelists', methods=['POST'])
def upload_multiple_pricelists():
    global PRICELISTS_DB
    if 'pricelists' not in request.files: return jsonify({"error": "לא נבחרו קבצים"}), 400
    
    file = request.files['pricelists']
    if not file or not file.filename.endswith('.pdf'): return jsonify({"error": "קובץ לא תקין"}), 400

    clean_filename = file.filename.replace('\\', '/').split('/')[-1]
    supplier_name = os.path.splitext(clean_filename)[0]
    
    if supplier_name in PRICELISTS_DB: return jsonify({"message": f"המחירון {supplier_name} כבר קיים."})

    try:
        doc_part = convert_to_optimized_image_part(file)
        raw_items = extract_data_with_gemini(doc_part, doc_type="price")
        parsed_dict = {item["sku"]: {"description": item.get("description", ""), "price": float(item["value"])} for item in raw_items if item.get("sku")}
        
        if parsed_dict:
            PRICELISTS_DB[supplier_name] = parsed_dict
            with open(os.path.join(DB_DIR, f"{supplier_name}.json"), 'w', encoding='utf-8') as f:
                json.dump(parsed_dict, f, ensure_ascii=False, indent=4)
            return jsonify({"message": f"נשמר בהצלחה: {supplier_name}"})
        else:
            return jsonify({"error": "לא נמצאו נתונים"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers(): return jsonify({"suppliers": list(PRICELISTS_DB.keys())})

@app.route('/api/get-pricelist', methods=['POST'])
def get_pricelist():
    supplier_name = request.json.get('supplier_name', '')
    pricelist = PRICELISTS_DB.get(supplier_name, {})
    return jsonify({"pricelist": [{"sku": k, "description": v['description'], "price": v['price']} for k, v in pricelist.items()]})

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
    pricelist = PRICELISTS_DB.get(supplier_name)
    if not pricelist: return jsonify({"error": "המחירון לא קיים."}), 404

    try:
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
        
        print("\n--- מתחיל תהליך השוואת הצעות ---")
        
        print("1. ממיר את הצעה א' לתמונה...")
        part_a = convert_to_optimized_image_part(request.files['quote_a'])
        
        print("2. שולח את הצעה א' לשרתים של גוגל...")
        items_a = extract_data_with_gemini(part_a, "price")
        
        print("3. ממתין 2 שניות למניעת חסימה...")
        time.sleep(2) 
        
        print("4. ממיר את הצעה ב' לתמונה...")
        part_b = convert_to_optimized_image_part(request.files['quote_b'])
        
        print("5. שולח את הצעה ב' לשרתים של גוגל...")
        items_b = extract_data_with_gemini(part_b, "price")

        print("6. מתחיל חישוב והשוואה בפייתון...")
        dict_a = {item['sku']: item for item in items_a if item.get('sku')}
        dict_b = {item['sku']: item for item in items_b if item.get('sku')}

        results = []
        total_a = 0.0
        total_b = 0.0
        total_split = 0.0

        common_skus = set(dict_a.keys()).intersection(set(dict_b.keys()))
        
        if not common_skus:
            return jsonify({"error": "לא נמצאו מק\"טים חופפים בין שתי ההצעות."}), 400

        for sku in common_skus:
            price_a = float(dict_a[sku].get('value', 0))
            price_b = float(dict_b[sku].get('value', 0))
            desc = dict_a[sku].get('description', '')

            diff_abs = abs(price_a - price_b)
            min_price = min(price_a, price_b)
            diff_pct = (diff_abs / min_price * 100) if min_price > 0 else 0

            if price_a < price_b:
                winner = "A"
            elif price_b < price_a:
                winner = "B"
            else:
                winner = "Tie"

            gap_type = "זניח" if diff_pct <= threshold else "משמעותי"

            total_a += price_a
            total_b += price_b
            total_split += min_price

            results.append({
                "sku": sku, "description": desc, "price_a": price_a, "price_b": price_b,
                "winner": winner, "diff_pct": diff_pct, "gap_type": gap_type
            })

        best_single_total = min(total_a, total_b)
        best_single_name = "הצעה א'" if total_a < total_b else "הצעה ב'"
        
        savings_value = best_single_total - total_split
        savings_percent = (savings_value / best_single_total * 100) if best_single_total > 0 else 0

        if savings_percent > threshold:
            recommendation = f"💡 המלצה: לפצל את ההזמנה! הפיצול חוסך {savings_value:.2f} ₪ ({savings_percent:.1f}%) לעומת הזמנה מ{best_single_name}."
            action_class = "split-alert"
        else:
            recommendation = f"📦 המלצה: להזמין הכל מ{best_single_name}. פיצול יחסוך רק {savings_value:.2f} ₪ ({savings_percent:.1f}%), שזה זניח."
            action_class = "consolidate-alert"

        summary = {
            "total_a": round(total_a, 2), "total_b": round(total_b, 2), "total_split": round(total_split, 2),
            "savings_value": round(savings_value, 2), "savings_percent": round(savings_percent, 1),
            "recommendation": recommendation, "action_class": action_class
        }

        return jsonify({"results": results, "summary": summary})

    except Exception as e:
        print(f"שגיאה קריטית בהשוואת הצעות: {e}")
        return jsonify({"error": "אירעה שגיאה בעיבוד ההצעות."}), 500


# =====================================================================
# הלוגיקה החדשה: דשבורד השוואת מחירוני ספקים (BI)
# =====================================================================
@app.route('/api/compare-pricelists', methods=['POST'])
def compare_pricelists():
    try:
        data = request.get_json()
        selected_suppliers = data.get('suppliers', [])
        
        if len(selected_suppliers) < 2:
            return jsonify({"error": "יש לבחור לפחות 2 ספקים להשוואה."}), 400

        all_skus = {} 
        
        for sup in selected_suppliers:
            pricelist = PRICELISTS_DB.get(sup, {})
            for sku, details in pricelist.items():
                if sku not in all_skus:
                    all_skus[sku] = {"description": details['description'], "prices": {}}
                all_skus[sku]["prices"][sup] = details['price']

        winners_board = {sup: [] for sup in selected_suppliers}
        
        for sku, data in all_skus.items():
            prices = data["prices"]
            
            if len(prices) < 2:
                continue
                
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
        print(f"שגיאה ביצירת דשבורד השוואה: {e}")
        return jsonify({"error": "אירעה שגיאה בעיבוד הנתונים."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
