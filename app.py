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


# ✅ הבאת ספקים
@app.route('/api/get-suppliers')
def get_suppliers():
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


# ✅ הבאת מחירון של ספק
@app.route('/api/get-pricelist')
def get_pricelist():

    supplier = request.args.get("supplier")

    if not supplier:
        return jsonify({"error": "missing supplier"}), 400

    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                cur.execute("""
                    SELECT sku, description, price
                    FROM pricelist_items
                    WHERE supplier_name = %s
                    ORDER BY sku
                """, (supplier,))

                rows = cur.fetchall()

        return jsonify({"items": rows})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ בדיקה בסיסית
@app.route('/test')
def test():
    return "Server is working ✅"


if __name__ == "__main__":
    app.run(debug=True)
``
