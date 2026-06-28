import os
import io
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify
from PIL import Image
import fitz
from google import genai
from google.genai import types

# הגדרת התיקייה ל-templates כדי ש-Flask ימצא את ה-HTML
app = Flask(__name__, template_folder='templates')

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/get-suppliers', methods=['GET'])
def get_suppliers():
    try:
        conn = get_db_connection()
        # שימוש נכון ב-RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT supplier_name FROM pricelist_items ORDER BY supplier_name")
        rows = cursor.fetchall()
        suppliers = [row['supplier_name'] for row in rows]
        cursor.close()
        conn.close()
        return jsonify({"suppliers": suppliers})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/upload-multiple-pricelists', methods=['POST'])
def upload_multiple_pricelists():
    file = request.files['pricelists']
    supplier_name = os.path.splitext(file.filename.replace('\\', '/').split('/')[-1])[0]
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT 1 FROM pricelist_items WHERE supplier_name = %s LIMIT 1", (supplier_name,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            return jsonify({"message": "ספק קיים"})
        
        # (כאן אמורה להיות הלוגיקה של Gemini, השארתי את החיבור ל-DB תקין)
        # ... לוגיקת Gemini ...
        
        conn.commit()
        cursor.close(); conn.close()
        return jsonify({"message": "הצלחה"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze-prices', methods=['POST'])
def analyze_prices():
    supplier_name = request.form.get('supplier_name', '')
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT sku, description, price FROM pricelist_items WHERE supplier_name = %s", (supplier_name,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        
        # כאן זה יעבוד כי rows הוא רשימה של מילונים
        pricelist = {row['sku']: {'description': row['description'], 'price': float(row['price'])} for row in rows}
        return jsonify({"results": []}) # המשך הלוגיקה שלך
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
