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

    # ✅ ניקוי רווחים
    suppliers = [r["supplier_name"].strip() for r in rows]

    return jsonify({"suppliers": suppliers})


# ✅ מחירון של ספק (תיקון BUG חשוב כאן!)
@app.route('/api/get-pricelist')
def get_pricelist():

    supplier = request.args.get("supplier")

    if not supplier:
        return jsonify({"items": []})

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ✅ DEBUG (אפשר למחוק אחרי)
            print("Requested supplier:", supplier)

            # ✅ תיקון מלא לבעיה
            cur.execute("""
                SELECT sku, description, price
                FROM pricelist_items
                WHERE TRIM(LOWER(supplier_name)) = TRIM(LOWER(%s))
                ORDER BY description
            """, (supplier,))

            rows = cur.fetchall()

            print("Rows:", len(rows))

    return jsonify({"items": rows})


# ✅ ניקוי טקסט
def clean(text):
    if not text:
        return ""
    return text.lower().replace("-", "").strip()


# ✅ התאמה חכמה
def find_match(desc, db_items):
    names = [clean(i["description"]) for i in db_items]
    match = get_close_matches(clean(desc), names, n=1, cutoff=0.6)
    return match[0] if match else None


# ✅ קריאת חשבונית AI
def extract_invoice(file):

    data = file.read()

    if file.filename.endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        pix = doc.load_page(0).get_pixmap()
        data = pix.tobytes("jpeg")

    part = types.Part.from_bytes(data=data, mime_type="image/jpeg")

    prompt = """
    חלץ חשבונית.

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

        match_name = find_match(i.get("description", ""), db_items)

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


if __name__ == "__main__":
    app.run(debug=True)
