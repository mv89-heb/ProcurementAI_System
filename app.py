import os
import time
import traceback
from flask import Flask, render_template, request, jsonify
from core.ai_engine import AIEngine
from core.database import save_db, load_all_pricelists, get_supplier_pricelist

app = Flask(__name__)
ai = AIEngine(api_key=os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6L-8tRXy__KdraSbkW3Ndga4O02i_Lm8cEgw6R4nfjHtQ"))

@app.route('/')
def home():
    return render_template('index.html')

# --- נתוני דשבורד וניהול ---
@app.route('/api/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    current_db = load_all_pricelists()
    total_items = sum(len(data.get("items", [])) if "items" in data else len([k for k in data.keys() if k != "supplier_name"]) for data in current_db.values() if isinstance(data, dict))
    return jsonify({"total_suppliers": len(current_db), "total_items": total_items, "processed_invoices": 0, "open_exceptions": 0, "total_savings": 0})

@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    return jsonify({"count": len(load_all_pricelists()), "suppliers": list(load_all_pricelists().keys())})

@app.route('/api/pricelist/<supplier_name>', methods=['GET'])
def get_pricelist_data(supplier_name):
    return jsonify(get_supplier_pricelist(supplier_name))

# --- מודול 7: ניתוב חכם ---
@app.route('/api/upload', methods=['POST'])
def handle_upload():
    try:
        if 'file' not in request.files: return jsonify({"error": "No file provided"}), 400
        doc_type, conf = ai.classify_document(request.files['file'])
        return jsonify({"status": "classified", "doc_type": doc_type, "confidence": conf})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- מודול 2: בקרת חשבוניות מול מחירון ---
@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    supplier_name = request.form.get('supplier_name', '')
    pricelist = get_supplier_pricelist(supplier_name)
    if not pricelist: return jsonify({"error": "המחירון לא קיים או לא נבחר."}), 404

    try:
        # חילוץ נתונים מהמבנה הישן או החדש של המחירון
        base_items = pricelist.get('items', [])
        if not base_items:
            base_items = [{"sku": k, "price": v.get("price", 0)} for k, v in pricelist.items() if k != "supplier_name" and isinstance(v, dict)]
        base_dict = {str(item.get('sku')): item for item in base_items}

        invoice_data = ai.extract_invoice(request.files['invoice'])
        invoice_items = invoice_data.get('items', [])
        
        results, has_alerts = [], False
        for item in invoice_items:
            sku = str(item.get('sku', ''))
            if not sku: continue
            inv_price = float(item.get('price', 0.0))
            base_info = base_dict.get(sku)
            
            if not base_info:
                status, approved, match = "לא במחירון!", 0.0, False
                has_alerts = True
            else:
                base_price = float(base_info.get('price', 0.0))
                if inv_price > base_price:
                    status, approved, match = f"חיוב יתר של {round(inv_price - base_price, 2)} ₪", base_price, False
                    has_alerts = True
                else:
                    status, approved, match = "תקין", base_price, True

            results.append({"sku": sku, "description": item.get('description', ''), "approved_price": approved, "invoice_price": inv_price, "status": status, "is_match": match})
            
        return jsonify({"results": results, "has_alerts": has_alerts})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- מודול 3: השוואת הצעות מחיר (Cherry Picking) ---
@app.route('/api/compare-quotes', methods=['POST'])
def compare_quotes():
    try:
        threshold = float(request.form.get('threshold', 5.0))
        items_a = ai.extract_items(request.files['quote_a'], "חלץ מק\"ט, תיאור ומחיר ליחידה").get('items', [])
        time.sleep(2) # מניעת חסימה של ה-API
        items_b = ai.extract_items(request.files['quote_b'], "חלץ מק\"ט, תיאור ומחיר ליחידה").get('items', [])

        dict_a = {str(item['sku']): item for item in items_a if item.get('sku')}
        dict_b = {str(item['sku']): item for item in items_b if item.get('sku')}

        results = []
        total_a, total_b, total_split = 0.0, 0.0, 0.0
        common_skus = set(dict_a.keys()).intersection(set(dict_b.keys()))
        
        if not common_skus: return jsonify({"error": "לא נמצאו מק\"טים חופפים להשוואה."}), 400

        for sku in common_skus:
            price_a = float(dict_a[sku].get('price', dict_a[sku].get('value', 0)))
            price_b = float(dict_b[sku].get('price', dict_b[sku].get('value', 0)))
            desc = dict_a[sku].get('description', '')

            min_price = min(price_a, price_b)
            diff_pct = (abs(price_a - price_b) / min_price * 100) if min_price > 0 else 0
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

        recommendation = f"💡 המלצה: לפצל! חיסכון של {savings_value:.2f} ₪ ({savings_percent:.1f}%)" if savings_percent > threshold else f"📦 המלצה: להזמין הכל מ{best_single_name}. פיצול יחסוך רק {savings_value:.2f} ₪, שזה זניח."
        
        summary = {"total_a": round(total_a, 2), "total_b": round(total_b, 2), "total_split": round(total_split, 2), "recommendation": recommendation}
        return jsonify({"results": results, "summary": summary})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# --- מודול 4: בקרת משלוחים (Delivery vs Invoice) ---
@app.route('/api/analyze', methods=['POST'])
def analyze_documents():
    try:
        del_items = ai.extract_items(request.files['delivery'], "חלץ מק\"ט, תיאור וכמות (qty)").get('items', [])
        inv_items = ai.extract_items(request.files['invoice'], "חלץ מק\"ט, תיאור וכמות (qty)").get('items', [])
        
        del_dict = {str(item['sku']): item for item in del_items if item.get('sku')}
        inv_dict = {str(item['sku']): item for item in inv_items if item.get('sku')}
        
        results = []
        for sku, del_info in del_dict.items():
            inv_info = inv_dict.get(sku)
            del_qty = int(del_info.get('qty', del_info.get('value', 0)))
            
            if not inv_info:
                inv_qty, status, is_match = 0, "חסר בחשבונית!", False
            else:
                inv_qty = int(inv_info.get('qty', inv_info.get('value', 0)))
                status, is_match = (f"פער! חסר {del_qty - inv_qty}", False) if inv_qty != del_qty else ("תקין", True)

            results.append({"sku": sku, "description": del_info.get('description', ''), "delivery_qty": del_qty, "invoice_qty": inv_qty, "status": status, "is_match": is_match})
        return jsonify({"results": results})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)