import os
import json
import psycopg2

# --- הכנס כאן את ה-Connection String שלך מ-Neon ---
DATABASE_URL = "postgresql://USER:PASSWORD@ep-xxx.neon.tech/neondb?sslmode=require"

DB_DIR = os.path.join(os.path.dirname(__file__), 'database')

def migrate():
    print("מתחיל חיבור ל-Neon DB...")
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    if not os.path.exists(DB_DIR):
        print("תיקיית database לא קיימת.")
        return

    for filename in os.listdir(DB_DIR):
        if filename.endswith('.json'):
            supplier_name = os.path.splitext(filename)[0]
            print(f"מעביר את המחירון של: {supplier_name}...")
            
            with open(os.path.join(DB_DIR, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            for sku, details in data.items():
                desc = details.get('description', '')
                price = details.get('price', 0.0)
                
                # פקודת UPSERT: מכניס נתונים, ואם קיימים - מעדכן אותם
                cursor.execute("""
                    INSERT INTO pricelist_items (supplier_name, sku, description, price)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (supplier_name, sku) DO UPDATE 
                    SET price = EXCLUDED.price, description = EXCLUDED.description;
                """, (supplier_name, sku, desc, price))
                
            conn.commit()
            print(f"הספק {supplier_name} הועבר בהצלחה!")

    cursor.close()
    conn.close()
    print("העברת הנתונים הושלמה! אפשר למחוק את תיקיית database ואת קובצי ה-JSON.")

if __name__ == "__main__":
    migrate()
