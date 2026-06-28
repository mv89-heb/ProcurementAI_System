import os
import json
import time

DB_PATH = os.path.join(os.path.dirname(__file__), 'data_store')


def init_db():
    folders = ['pricelists', 'history']
    for f in folders:
        os.makedirs(os.path.join(DB_PATH, f), exist_ok=True)


def safe_filename(name):
    return str(name).replace("/", "").replace("\\", "").replace("..", "").strip()


def save_pricelist(supplier, data):
    init_db()

    name = safe_filename(supplier) or "Unknown"

    path = os.path.join(DB_PATH, "pricelists", f"{name}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    ts = int(time.time())
    hist = os.path.join(DB_PATH, "history", f"{name}_{ts}.json")

    with open(hist, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all_suppliers():
    init_db()
    path = os.path.join(DB_PATH, "pricelists")
    return [f.replace(".json", "") for f in os.listdir(path) if f.endswith(".json")]


def get_supplier_pricelist(supplier):
    name = safe_filename(supplier)
    path = os.path.join(DB_PATH, "pricelists", f"{name}.json")

    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
