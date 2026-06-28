from flask import Flask, request, jsonify, render_template
import os

from database import get_all_suppliers, get_supplier_pricelist, save_pricelist
from ai_engine import AIEngine

app = Flask(__name__)

ai = AIEngine(os.environ.get("GEMINI_API_KEY"))


@app.route('/')
def home():
    return render_template("index.html")


# ✅ ספקים מהקבצים
@app.route('/api/get-suppliers')
def suppliers():
    return jsonify({"suppliers": get_all_suppliers()})


# ✅ העלאת מחירון
@app.route('/api/upload-pricelist', methods=['POST'])
def upload_pricelist():
    file = request.files.get("file")

    data = ai.extract_pricelist(file)

    supplier = data.get("supplier_name", "Unknown")
    save_pricelist(supplier, data)

    return jsonify({"status": "ok", "supplier": supplier})


# ✅ ניתוח חשבונית
@app.route('/api/analyze-prices', methods=['POST'])
def analyze():

    supplier = request.form.get("supplier_name")
    file = request.files.get("invoice")

    pricelist = get_supplier_pricelist(supplier)

    db = {i["sku"]: i["price"] for i in pricelist.get("items", [])}

    items = ai.extract_invoice(file)

    results = []

    for i in items:
        sku = i.get("sku")
        price = i.get("price", 0)

        ref = db.get(sku)

        results.append({
            "sku": sku,
            "invoice_price": price,
            "approved_price": ref,
            "found": ref is not None
        })

    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(debug=True)
