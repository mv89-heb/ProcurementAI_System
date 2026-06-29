# -*- coding: utf-8 -*-
import os
import sys
from flask import Flask, request, jsonify, render_template
import psycopg2
from ai_engine import AIEngine

# הגדרת קידוד לפלט הטרמינל
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# אתחול השרת - ברירת המחדל תחפש קבצי HTML בתיקיית templates/
app = Flask(__name__)

# הגדרות סביבה - נלקחות אוטומטית מ-Render
DATABASE_URL = os.environ.get("DATABASE_URL", "הכנס_כאן_את_הקישור_מ_NEON")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "הכנס_מפתח")
COMPANY_ID = "demo_company"

# אתחול מנוע ה-AI
ai_engine = AIEngine(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route('/')
def index():
    """הצגת ממשק המערכת"""
    return render_template('index.html')

@app.route('/api/get-dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    """שליפת נתוני אמת סטטיסטיים מלוח המחירונים"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # ספירת סך כל הפריטים הייחודיים במערכת (מק"טים)
        cur.execute("SELECT COUNT(*) FROM pricelist_items WHERE company_id = %s", (COMPANY_ID,))
        total_items = cur.fetchone()[0]
        
        # התפלגות כמות הפריטים לכל ספק (לצורך גרף עוגה אמיתי)
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
