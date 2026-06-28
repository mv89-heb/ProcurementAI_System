import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    return psycopg2.connect(DATABASE_URL)


# ✅ עמוד ראשי
@app.route('/')
def home():
    return render_template("index.html")


# ✅ ספקים מה־DB
@app.route('/api/get-suppliers')
def suppliers():
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT DISTINCT supplier_name
                    FROM pricelist_items
                    ORDER BY supplier_name
                """)
                rows = cur.fetchall()

        return jsonify({
            "suppliers": [r["supplier_name"] for r in rows]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ בדיקת חשבונית מול DB
@app.route('/api/analyze-prices', methods=['POST'])
def analyze():

    supplier = request.form.get("supplier_name")

    if not supplier:
        return jsonify({"error": "missing supplier"}), 400

    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT sku, description, price
                    FROM pricelist_items
                    WHERE supplier_name = %s
                """, (supplier,))
                rows = cur.fetchall()

        # ✅ אם אין נתונים
        if not rows:
            return jsonify({
                "error": "אין נתונים לספק הזה",
                "results": []
            })

        return jsonify({
            "results": rows
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
