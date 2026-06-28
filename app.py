import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, render_template
from difflib import get_close_matches
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


# ✅ העלאת מחירון
@app.route('/api/upload-pricelist', methods=['POST'])
def upload_pricelist():

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "no file"}), 400

    data = file.read()

    part = types.Part.from_bytes(data=data, mime_type="application/pdf")

    prompt = """
    חלץ מחירון ספק.

    חוקים:
    - אל תמציא נתונים
    - החזר JSON בלבד

    {
      "supplier_name":"string",
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

        parsed = json.loads(res.text)
    except:
        return jsonify({"error": "AI parsing failed"}), 500

    supplier = parsed.get("supplier_name", "Unknown")

    with get_db() as conn:
        with conn.cursor() as cur:
            for item in parsed.get("items", []):
                cur.execute("""
                    INSERT INTO pricelist_items (supplier_name, sku, description, price)
                    VALUES (%s,%s,%s,%s)
                """, (
                    supplier,
                    item.get("sku"),
                    item.get("description"),
                    item.get("price")
                ))

    return jsonify({"status": "saved", "supplier": supplier})


# ✅ ניקוי טקסטים
def clean(text):
    return text.lower().replace("-", "").strip()


# ✅ matching חכם
def find_match(desc, db_items):
    names = [clean(i["description"]) for i in db_items]
    match = get_close_matches(clean(desc), names, n=1, cutoff=0.6)
    return match[0] if match else None


# ✅ AI חשבונית
def extract_invoice(file):

    data = file.read()

    if file.filename.endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        pix = doc.load_page(0).get_pixmap()
        data = pix.tobytes("jpeg")

    part = types.Part.from_bytes(data=data, mime_type="image/jpeg")

    prompt = """
    חלץ חשבונית.

    חוקים:
    - אל תמציא נתונים
    - מחיר חייב להיות מספר

    JSON בלבד:
    {
      "items":[
        {"description":"string","price":number}
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


# ✅ בדיקת חשבונית
@app.route('/api/analyze-invoice', methods=['POST'])
def analyze():

    supplier = request.form.get("supplier")
    file = request.files.get("file")

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pricelist_items")
            db_items = cur.fetchall()

    invoice_items = extract_invoice(file)

    results = []
    alerts = []

    for i in invoice_items:

        match_name = find_match(i.get("description",""), db_items)

        db_item = next(
            (x for x in db_items if clean(x["description"]) == match_name),
            None
        )

        if db_item:
            diff = float(i["price"]) - float(db_item["price"])
            status = "תקין" if diff <= 0 else "חריגה"

            if diff > 0:
                alerts.append(f"{i['description']} יקר ב-{round(diff,2)}")

        else:
            status = "לא נמצא"
            diff = None

        results.append({
            "description": i.get("description"),
            "invoice_price": i.get("price"),
            "approved_price": db_item["price"] if db_item else None,
            "difference": diff,
            "status": status
        })

    return jsonify({
        "results": results,
        "alerts": alerts[:5]
    })


# ✅ דשבורד
@app.route('/api/dashboard')
def dashboard():

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("SELECT COUNT(*) FROM pricelist_items")
            total = cur.fetchone()["count"]

            cur.execute("SELECT AVG(price) FROM pricelist_items")
            avg = cur.fetchone()["avg"]

    return jsonify({"total": total, "avg": avg})
