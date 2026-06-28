import os
import io
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, render_template
from PIL import Image
import fitz
from google import genai
from google.genai import types

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_db():
    return psycopg2.connect(DATABASE_URL)


@app.route('/')
def home():
    return render_template("index.html")


# ✅ ספקים
@app.route('/api/get-suppliers')
def get_suppliers():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT DISTINCT supplier_name FROM pricelist_items ORDER BY supplier_name")
            rows = cur.fetchall()

    return jsonify({"suppliers": [r["supplier_name"] for r in rows]})


# ✅ AI: קריאת חשבונית
def extract_invoice(file):

    data = file.read()

    if file.filename.endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc.load_page(0)
        pix = page.get_pixmap()
        data = pix.tobytes("jpeg")

    part = types.Part.from_bytes(data=data, mime_type="image/jpeg")

    prompt = """
    חלץ מוצרים מחשבונית

    JSON:
    {
      "items":[
        {"sku":"string","description":"string","price":number}
      ]
    }
    """

    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0
            )
        )

        return json.loads(res.text).get("items", [])

    except:
        return []


# ✅ השוואה חכמה
@app.route('/api/analyze-invoice', methods=['POST'])
def analyze_invoice():

    supplier = request.form.get("supplier")
    file = request.files.get("file")

    if not supplier or not file:
        return jsonify({"error": "missing data"}), 400

    # DB
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT sku, description, price
                FROM pricelist_items
                WHERE supplier_name = %s
            """, (supplier,))
            rows = cur.fetchall()

    pricelist = {r["sku"]: float(r["price"]) for r in rows}

    # AI
    items = extract_invoice(file)

    results = []
    alerts = []

    for i in items:
        sku = i.get("sku")
        price = float(i.get("price", 0))

        db_price = pricelist.get(sku)

        if db_price:
            diff = price - db_price

            status = "תקין" if price <= db_price else "חריגה"

            if price > db_price:
                alerts.append(f"{sku} יקר ב-{round(diff,2)}")

        else:
            status = "לא נמצא"
            diff = None

        results.append({
            "sku": sku,
            "invoice_price": price,
            "approved_price": db_price,
            "difference": diff,
            "status": status
        })

    return jsonify({
        "results": results,
        "insights": {
            "total": len(results),
            "issues": len([r for r in results if r["status"] != "תקין"]),
            "alerts": alerts[:5]
        }
    })


if __name__ == "__main__":
    app.run(debug=True)
