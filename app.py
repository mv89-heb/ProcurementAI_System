# -*- coding: utf-8 -*-
import os
from flask import Flask, request, jsonify, render_template
import psycopg2
from ai_engine import AIEngine

app = Flask(__name__, template_folder='.')

# הגדרות סביבה (נלקחות מ-Render או מוגדרות מקומית)
DATABASE_URL = os.environ.get("DATABASE_URL", "הכנס_כאן_את_הקישור_מ_NEON")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "הכנס_כאן_את_המפתח")
COMPANY_ID = "demo_company"

# אתחול מנוע ה-AI
ai_engine = AIEngine(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route('/')
def index():
    # מגיש את ממשק המשתמש
    return render_template('index.html')

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
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
    data = request.json
    suppliers = data.get('suppliers', [])
    
    if len(suppliers) < 2:
        return jsonify({"error": "יש לבחור לפחות 2 ספקים"}), 400
        
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # שליפת מוצרים חופפים בין הספקים שנבחרו
        query = """
            SELECT description, supplier_name, price 
            FROM pricelist_items 
            WHERE company_id = %s AND supplier_name = ANY(%s)
        """
        cur.execute(query, (COMPANY_ID, suppliers))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # סידור התוצאות
        products_dict = {}
        for desc, sup, price in rows:
            if desc not in products_dict:
                products_dict[desc] = []
            products_dict[desc].append({"supplier": sup, "price": float(price)})
        
        results = []
        for product, offers in products_dict.items():
            if len(offers) > 1: # רק מוצרים שמופיעים אצל יותר מספק אחד
                best_offer = min(offers, key=lambda x: x['price'])
                for o in offers:
                    o['difference'] = round(o['price'] - best_offer['price'], 2)
                    
                results.append({
                    "product": product,
                    "offers": offers,
                    "best_supplier": best_offer['supplier'],
                    "best_price": best_offer['price']
                })
        
        insights = [
            f"נמצאו {len(results)} מוצרים תואמים להשוואה.",
            "בדוק את אפשרויות המשא ומתן עבור הפריטים בהם התגלה פער מחירים."
        ]
        
        return jsonify({
            "results": results,
            "ranking": suppliers,
            "insights": insights
        })
    except Exception as e:
        print("Compare Error:", e)
        return jsonify({"error": "שגיאה בחישוב ההשוואה"}), 500

@app.route('/api/generate-negotiation', methods=['POST'])
def generate_negotiation():
    data = request.json
    supplier = data.get('supplier', 'ספק')
    product = data.get('product', 'מוצר')
    current_price = data.get('currentPrice', 0)
    target_price = data.get('targetPrice', 0)
    aggressive = data.get('aggressive', False)

    tone = "אסרטיבי, ענייני ומציב אולטימטום מרומז" if aggressive else "מנומס, שותפותי אך מקצועי"
    
    prompt = f"""
    אתה מנהל רכש בכיר בחברת ענק. תפקידך לכתוב אימייל קצר וקולע לספק כדי להוריד מחירים.
    פרטי המקרה:
    - ספק: {supplier}
    - מוצר: {product}
    - מחיר נוכחי: {current_price} ש"ח
    - מחיר יעד (שוק): {target_price} ש"ח
    הנחיות: כתוב אימייל בעברית תקנית. הטון: {tone}. אל תמציא נתונים. החזר אך ורק את תוכן האימייל המוכן לשליחה.
    """

    try:
        res = ai_engine.client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return jsonify({"draft": res.text.strip(), "success": True})
    except Exception as e:
        print(f"Negotiation AI Error: {e}")
        return jsonify({"error": "שגיאה ביצירת הטיוטה", "success": False}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
