import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL)


@app.route('/')
def home():
    return render_template("index.html")


@app.route('/api/get-suppliers')
def get_suppliers():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT TRIM(supplier_name) as supplier_name
                FROM pricelist_items
                ORDER BY supplier_name
            """)
            rows = cur.fetchall()

    return jsonify({"suppliers": [r["supplier_name"] for r in rows]})


@app.route('/api/get-pricelist')
def get_pricelist():

    supplier = request.args.get("supplier")

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT sku, description, price
                FROM pricelist_items
                WHERE LOWER(TRIM(supplier_name)) = LOWER(TRIM(%s))
                ORDER BY description
            """, (supplier,))

            rows = cur.fetchall()

    return jsonify({"items": rows})


@app.route('/api/dashboard')
def dashboard():

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("SELECT COUNT(*) FROM pricelist_items")
            total = cur.fetchone()["count"]

            cur.execute("SELECT AVG(price) FROM pricelist_items")
            avg = cur.fetchone()["avg"]

    return jsonify({"total": total, "avg": avg})
