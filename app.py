import os
import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from flask import Flask, request, jsonify, render_template
from ai_engine import AIEngine

app = Flask(__name__, template_folder="templates")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# אתחול מנוע ה-AI
ai = AIEngine(GEMINI_API_KEY)

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn) # רישום pgvector מול מסד הנתונים
    return conn

@app.route('/')
def home():
    return render_template("index.html")

# ✅ שליפת ספקים (לפי לקוח - Multi-Tenancy)
@app.route('/api/get-suppliers', methods=['GET'])
def suppliers():
    company_id = request.args.get("company_id", "demo_company")
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT DISTINCT supplier_name FROM pricelist_items WHERE company_id = %s ORDER BY supplier_name", (company_id,))
                rows = cur.fetchall()
        return jsonify({"suppliers": [r["supplier_name"] for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ העלאת מחירון (הזרקת נתונים ו-Embeddings ל-DB)
@app.route('/api/upload-pricelist', methods=['POST'])
def upload_pricelist():
    company_id = request.form.get("company_id", "demo_company")
    supplier_name = request.form.get("supplier_name")
    file = request.files.get("file")
    
    if not supplier_name or not file:
        return jsonify({"error": "חסרים פרטים"}), 400

    # פענוח המסמך דרך ג'מיני (מתוך ai_engine.py)
    items = ai.extract_pricelist(file)
    if not items:
        return jsonify({"error": "לא זוהו פריטים במסמך או שהייתה שגיאה בפענוח"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                for item in items:
                    desc = item.get("description", "")
                    price = item.get("price", 0)
                    sku = item.get("sku", "")
                    if not desc or not sku: 
                        continue
                    
                    # יצירת Embedding לכל מוצר לשם השוואות עתידיות
                    embedding = ai.get_embedding(desc)
                    if not embedding: 
                        continue

                    # עדכון או הוספה (Upsert)
                    cur.execute("""
                        INSERT INTO pricelist_items (company_id, supplier_name, sku, description, price, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, supplier_name, sku) DO UPDATE 
                        SET price = EXCLUDED.price, 
                            description = EXCLUDED.description, 
                            embedding = EXCLUDED.embedding;
                    """, (company_id, supplier_name, sku, desc, price, embedding))
            conn.commit()
        return jsonify({"message": f"המחירון של {supplier_name} עובד ונשמר בהצלחה כולל מנוע החיפוש הווקטורי!"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ✅ בקרת חשבונית מול מחירון מאושר
@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    company_id = request.form.get('company_id', 'demo_company')
    supplier_name = request.form.get('supplier_name', '')
    file = request.files.get('invoice')

    if not supplier_name or not file:
        return jsonify({"error": "חסרים פרטים"}), 400

    try:
        # 1. שליפת מחירון הספק מהמסד הנתונים
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT sku, description, price FROM pricelist_items WHERE company_id = %s AND supplier_name = %s", (company_id, supplier_name))
                rows = cur.fetchall()
        
        # יצירת מילון לחיפוש מהיר לפי מק"ט
        pricelist = {row['sku']: {'description': row['description'], 'price': float(row['price'])} for row in rows}

        # 2. ניתוח החשבונית בעזרת ג'מיני
        invoice_items = ai.extract_pricelist(file) 
        
        results = []
        for item in invoice_items:
            sku = item.get('sku')
            if not sku: 
                continue
            
            inv_price = float(item.get('price', 0))
            base = pricelist.get(sku)
            
            is_match = base is not None and inv_price <= base['price']
            
            results.append({
                "sku": sku,
                "description": item.get('description', ''),
                "approved_price": base['price'] if base else 0,
                "invoice_price": inv_price,
                "status": "תקין" if is_match else "חריגה / לא במחירון",
                "is_match": is_match
            })
            
        return jsonify({"results": results})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ✅ השוואת ספקים (Enterprise) באמצעות pgvector
@app.route('/api/compare-suppliers', methods=['POST'])
def compare():
    data = request.json
    company_id = data.get("company_id", "demo_company")
    suppliers = data.get("suppliers", [])

    if not suppliers or len(suppliers) < 2:
        return jsonify({"error": "בחר לפחות 2 ספקים"}), 400

    base_supplier = suppliers[0]
    other_suppliers = suppliers[1:]
    results = []
    supplier_scores = {s: 0 for s in suppliers}

    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # שליפת מוצרי ספק הבסיס
                cur.execute("""
                    SELECT sku, description, price, embedding 
                    FROM pricelist_items 
                    WHERE company_id = %s AND supplier_name = %s
                """, (company_id, base_supplier))
                base_items = cur.fetchall()

                for base_item in base_items:
                    # חיפוש וקטורי ב-SQL מול שאר הספקים (אופרטור <=> מחשב מרחק קוסינוס)
                    cur.execute("""
                        SELECT supplier_name, description, price, 1 - (embedding <=> %s::vector) AS similarity
                        FROM pricelist_items
                        WHERE company_id = %s AND supplier_name = ANY(%s) AND 1 - (embedding <=> %s::vector) > 0.85
                    """, (base_item['embedding'], company_id, other_suppliers, base_item['embedding']))
                    
                    matches = cur.fetchall()
                    if not matches: 
                        continue

                    # בניית קבוצת ההשוואה
                    group = [{"supplier": base_supplier, "price": float(base_item['price'])}]
                    for m in matches:
                        group.append({"supplier": m["supplier_name"], "price": float(m['price'])})

                    best = min(group, key=lambda x: x["price"])
                    
                    row = {
                        "product": base_item["description"],
                        "offers": [],
                        "best_supplier": best["supplier"],
                        "best_price": best["price"]
                    }

                    for offer in group:
                        diff = offer["price"] - best["price"]
                        supplier_scores[offer["supplier"]] += diff
                        row["offers"].append({
                            "supplier": offer["supplier"],
                            "price": offer["price"],
                            "difference": round(diff, 2)
                        })

                    row["offers"].sort(key=lambda x: x["price"])
                    results.append(row)

        ranking = sorted(supplier_scores.items(), key=lambda x: x[1])
        insights = []
        if ranking:
            insights.append(f"🏆 הספק הזול ביותר בממוצע: {ranking[0][0]}")
            if len(ranking) > 1:
                insights.append(f"📉 פער הפסד פוטנציאלי בבחירת הספק היקר: {round(ranking[-1][1] - ranking[0][1], 2)} ₪")

        return jsonify({"results": results, "ranking": ranking, "insights": insights})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": "שגיאה בחישוב ההשוואה: " + str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
