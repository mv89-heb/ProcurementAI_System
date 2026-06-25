import os
import json
import time

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database')

def init_db():
    folders = ['pricelists', 'pricelists_history', 'invoices', 'audit_logs', 'learning_db']
    for folder in folders:
        os.makedirs(os.path.join(DB_PATH, folder), exist_ok=True)

def safe_filename(name):
    # תיקון קריטי: פונקציות אבטחה רגילות מוחקות אותיות בעברית!
    # לכן יצרתי פילטר שמונע חדירה למערכת אבל שומר על השפה.
    return str(name).replace("/", "").replace("\\", "").replace("..", "").strip()

def save_db(path_category, supplier_name, data):
    init_db()
    safe_sup = safe_filename(supplier_name)
    if not safe_sup:
        safe_sup = "Unknown_Supplier"
        
    timestamp = int(time.time())
    history_path = os.path.join(DB_PATH, f"{path_category}_history")
    os.makedirs(history_path, exist_ok=True)
    with open(os.path.join(history_path, f"{safe_sup}_{timestamp}.json"), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    full_path = os.path.join(DB_PATH, path_category, f"{safe_sup}.json")
    with open(full_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_all_pricelists():
    init_db()
    pricelists_monitored = {}
    pricelist_dir = os.path.join(DB_PATH, 'pricelists')
    
    if os.path.exists(pricelist_dir):
        for filename in os.listdir(pricelist_dir):
            if filename.endswith('.json'):
                supplier_name = filename[:-5]
                try:
                    with open(os.path.join(pricelist_dir, filename), 'r', encoding='utf-8') as f:
                        pricelists_monitored[supplier_name] = json.load(f)
                except Exception as e:
                    print(f"⚠️ שגיאה בטעינת המחירון {filename}: {e}")
                    
    return pricelists_monitored

def get_supplier_pricelist(supplier_name):
    safe_sup = safe_filename(supplier_name)
    path = os.path.join(DB_PATH, 'pricelists', f"{safe_sup}.json")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}