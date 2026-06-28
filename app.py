import os
import json
import psycopg2
import numpy as np
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, render_template
from google import genai

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)


def get_db():
    return psycopg2.connect(DATABASE_URL)


# ✅ embedding
def get_embedding(text):
    res = client.models.embed_content(
        model="text-embedding-004",
        content=text
    )
    return res.embeddings[0].values


# ✅ cosine
def cosine(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


@app.route('/')
def home():
    return render_template("index.html")


# ✅ ספקים
@app.route('/api/get-suppliers')
def suppliers():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT DISTINCT supplier_name FROM pricelist_items")
            rows = cur.fetchall()

    return jsonify({"suppliers":[r["supplier_name"] for r in rows]})


# ✅ השוואת ספקים + תובנות + המלצות
@app.route('/api/compare-suppliers', methods=['POST'])
def compare():

    suppliers = request.json.get("suppliers")

    if not suppliers or len(suppliers) < 2:
        return jsonify({"error":"בחר לפחות 2 ספקים"}), 400

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT supplier_name, description, price, embedding
                FROM pricelist_items
                WHERE supplier_name = ANY(%s)
                AND embedding IS NOT NULL
            """, (suppliers,))

            rows = cur.fetchall()

    # ✅ grouping לפי embedding
    groups = []

    for item in rows:

        matched = False

        for g in groups:
            score = cosine(item["embedding"], g[0]["embedding"])

            if score > 0.85:
                g.append(item)
                matched = True
                break

        if not matched:
            groups.append([item])

    results = []
    supplier_scores = {}

    for g in groups:

        best = min(g, key=lambda x: x["price"])

        row = {
            "product": best["description"],
            "offers": [],
            "best_supplier": best["supplier_name"],
            "best_price": best["price"]
        }

        for i in g:

            diff = i["price"] - best["price"]

            # ✅ score supplier
            supplier = i["supplier_name"]
            supplier_scores[supplier] = supplier_scores.get(supplier, 0) + diff

            row["offers"].append({
                "supplier": supplier,
                "price": i["price"],
                "difference": round(diff,2)
            })

        row["offers"].sort(key=lambda x: x["price"])
        results.append(row)

    # ✅ ranking
    ranking = sorted(supplier_scores.items(), key=lambda x: x[1])

    insights = []

    if ranking:
        insights.append(f"🏆 הספק הזול ביותר: {ranking[0][0]}")

        if len(ranking) > 1:
            insights.append(
                f"📉 פער בין זול ליקר: {round(ranking[-1][1] - ranking[0][1],2)}"
            )

    return jsonify({
        "results": results,
        "ranking": ranking,
        "insights": insights
    })
