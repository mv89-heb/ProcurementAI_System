# -*- coding: utf-8 -*-
import os
import sys
from flask import Flask, request, jsonify, render_template
import psycopg2
from ai_engine import AIEngine

# הגדרת קידוד לפלט הטרמינל
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)

# הגדרות סביבה - נלקחות אוטומטית מ-Render או מההגדרות המקומיות שלך
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:הסיסמה_שלך@ep-xxx-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSy...")
COMPANY_ID = "demo_company"

# אתחול מנוע ה-AI
ai_engine = AIEngine(api_key=GEMINI_API_KEY)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route('/')
def index():
    """הצגת דף הבית וממשק המערכת"""
    return render_template('index.html')

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    """שליפת רשימת הספקים הייחודיים הקיימים ב-DB"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = "SELECT DISTINCT supplier_name FROM pricelist_items WHERE company_id = %s ORDER BY supplier_name"
        cur.execute(query, (COMPANY_ID,))
        suppliers = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        print("Error fetching suppliers:", e)
        return jsonify({"error": "שגיאה בשליפת רשימת הספקים מהמחולל"}), 500

@app.route('/api/compare-suppliers', methods=['POST'])
def compare_suppliers():
    """הצלבת מוצרים וחילוץ פערי מחירים בין הספקים שנבחרו"""
    data = request.json or {}
    suppliers = data.get('suppliers', [])
    
    if len(suppliers) < 2:
        return jsonify({"error": "יש לבחור לפחות 2 ספקים לצורך ביצוע הצלבה"}), 400
        
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # שליפת כל הפריטים השייכים לספקים שנבחרו
        query = """
            SELECT description, supplier_name, price, sku 
            FROM pricelist_items 
            WHERE company_id = %s AND supplier_name = ANY(%s)
        """
        cur.execute(query, (COMPANY_ID, suppliers))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        # ארגון והצלבת פריטים לפי תיאור המוצר
        products_dict = {}
        for desc, sup, price, sku in rows:
            product_key = desc.strip() if desc and desc.strip() else f"מוצר ללא תיאור (מק\"ט {sku})"
            if product_key not in products_dict:
                products_dict[product_key] = []
            products_dict[product_key].append({"supplier": sup, "price": float(price)})
        
        results = []
        for product, offers in products_dict.items():
            # מציגים רק מוצרים שמופיעים אצל יותר מספק אחד (הצלבה אמיתית)
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
        
        # יצירת תובנות דינמיות על בסיס נתוני האמת שנמצאו
        savings_count = sum(1 for r in results if any(o['difference'] > 0 for o in r['offers']))
        insights = [
            f"הצלבת הנתונים הושלמה: נמצאו {len(results)} מוצרים זהים בין הספקים שנבחרו.",
            f"זוהו {savings_count} הזדמנויות למשא ומתן והוזלת עלויות ברכש הנוכחי."
        ]
        
        return jsonify({
            "results": results,
            "ranking": suppliers,
            "insights": insights
        })
    except Exception as e:
        print("Compare Error:", e)
        return jsonify({"error": f"שגיאה בעיבוד הנתונים מבסיס הנתונים: {str(e)}"}), 500

@app.route('/api/generate-negotiation', methods=['POST'])
def generate_negotiation():
    """פנייה למנוע ג'מיני לצורך ניסוח מכתב מו\"מ עסקי"""
    data = request.json or {}
    supplier = data.get('supplier', 'ספק')
    product = data.get('product', 'מוצר')
    current_price = data.get('currentPrice', 0)
    target_price = data.get('targetPrice', 0)
    aggressive = data.get('aggressive', False)

    tone = "אסרטיבי, חד משמעי ומציב אלטרנטיבות שוק" if aggressive else "שותפותי, מכובד ומקצועי"
    
    prompt = f"""
    אתה מנהל רכש בכיר. תפקידך לכתוב פנייה רשמית וקולעת באימייל לספק כדי לבקש הנחה והתאמת מחיר.
    פרטי הפנייה:
    - שם הספק: {supplier}
    - שם הפריט/המוצר: {product}
    - המחיר שאנו משלמים לו כיום: {current_price} ש"ח
    - מחיר היעד שמצאנו אצל מתחרים בשוק: {target_price} ש"ח
    הנחיות:
    כתוב את המכתב בעברית עסקית רהוטה. הטון צריך להיות {tone}. אל תפרט נתונים שלא קיימים בפרומפט. 
    החזר אך ורק את טקסט המכתב המוכן לשליחה ללא פתיחים כמו "הנה הטיוטה שלך".
    """

    try:
        res = ai_engine.client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return jsonify({"draft": res.text.strip(), "success": True})
    except Exception as e:
        print(f"Negotiation AI Error: {e}")
        return jsonify({"error": "מנוע ה-AI לא זמין כרגע", "success": False}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
