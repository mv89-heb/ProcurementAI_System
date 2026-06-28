import os
import json
import time

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'database')


def init_db():
    folders = ['pricelists', 'pricelists_history', 'invoices']
    for folder in folders:
        os.makedirs(os.path.join(DB_PATH, folder), exist_ok=True)


def safe_filename(name):
    return str(name).replace("/", "").replace("\\", "").replace("..", "").strip()


def save_pricelist(supplier_name, data):
    init_db()

    safe_sup = safe_filename(supplier_name) or "Unknown"

    path = os.path.join(DB_PATH, "pricelists", f"{safe_sup}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # היסטוריה
    timestamp = int(time.time())
    hist = os.path.join(DB_PATH, "pricelists_history", f"{safe_sup}_{timestamp}.json")

    with open(hist, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all_suppliers():
    init_db()
    path = os.path.join(DB_PATH, "pricelists")

    return [f.replace(".json", "") for f in os.listdir(path) if f.endswith(".json")]


def get_supplier_pricelist(supplier_name):
    safe_sup = safe_filename(supplier_name)
    path = os.path.join(DB_PATH, "pricelists", f"{safe_sup}.json")

    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
