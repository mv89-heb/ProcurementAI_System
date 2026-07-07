# -*- coding: utf-8 -*-
"""
שכבת גישה למסד הנתונים (Neon/Postgres).
קובץ זה חייב להתקיים - build.sh קורא לו ישירות בזמן הדיפלוי ב-Render:
    python -c 'from core.database import init_db; init_db()'
"""
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """יוצר את הטבלאות הנדרשות אם הן לא קיימות. בטוח להרצה חוזרת (idempotent)."""
    if not DATABASE_URL:
        print("אזהרה: משתנה הסביבה DATABASE_URL לא מוגדר - מדלג על אתחול DB.")
        return

    conn = get_connection()
    cur = conn.cursor()

    # --- טבלת ספקים מרכזית (ללא פרטי קשר - אלה נמצאים ב-supplier_contacts) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            name TEXT NOT NULL,
            category TEXT,
            notes TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (company_id, name)
        );
    """)

    # --- טבלת אנשי קשר/טלפונים לספקים - מופרדת לחלוטין ממחירוני הספקים ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_contacts (
            id SERIAL PRIMARY KEY,
            supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
            phone TEXT,
            phone_2 TEXT,
            mobile TEXT,
            contact_person TEXT,
            customer_number TEXT,
            delivery_days TEXT,
            source_file TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_supplier_contacts_supplier_id ON supplier_contacts (supplier_id);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pricelist_items (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            supplier_name TEXT NOT NULL,
            sku TEXT NOT NULL DEFAULT '',
            description TEXT,
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            source_file TEXT,
            uploaded_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (company_id, supplier_name, sku, description)
        );
    """)

    # --- טבלה חדשה ונפרדת לגמרי, ייעודית לנתונים שחולצו מקבצי האקסל הישנים ---
    # (לא נוגעים בכלל ב-pricelist_items הקיימת, כדי לא להסתבך עם מבנה שאולי
    # השתנה במהלך השנים בטבלה האמיתית שלך ב-Neon)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_catalog_items (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
            supplier_name TEXT NOT NULL,
            sku TEXT DEFAULT '',
            description TEXT,
            unit TEXT,
            category TEXT,
            price NUMERIC(12,2) NOT NULL DEFAULT 0,
            source_file TEXT,
            source_sheet TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_supplier_catalog_items_supplier_id ON supplier_catalog_items (supplier_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_supplier_catalog_items_company ON supplier_catalog_items (company_id);")

    # לוג של כל קובץ שנסרק/הועלה - שימושי למעקב אחרי הזנות מ-NAPS2 ולדיבוג
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upload_log (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            filename TEXT NOT NULL,
            supplier_name TEXT,
            doc_type TEXT DEFAULT 'pricelist',
            items_extracted INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_message TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_pricelist_company_supplier ON pricelist_items (company_id, supplier_name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_suppliers_category ON suppliers (category);")

    # --- טבלאות הצלבת חשבוניות מול תעודות משלוח ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            doc_type TEXT NOT NULL CHECK (doc_type IN ('invoice', 'delivery_note')),
            doc_number TEXT,
            reference_number TEXT,
            supplier_name TEXT NOT NULL,
            doc_date TEXT,
            source_file TEXT,
            uploaded_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)

    # --- היסטוריית מחירים - יומן שמצטבר ולעולם לא נדרס, כך שאפשר להשוות מחיר
    # נוכחי (מחשבונית שנסרקה) מול מחירים קודמים שנצפו (ממחירונים או מחשבוניות
    # קודמות), ולא רק מול "המחיר הכי עדכני" היחיד שנשמר ב-pricelist_items.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            supplier_name TEXT NOT NULL,
            sku TEXT DEFAULT '',
            description TEXT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            source TEXT NOT NULL DEFAULT 'pricelist',
            source_file TEXT,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_price_history_lookup ON price_history (company_id, supplier_name, description, recorded_at DESC);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_items (
            id SERIAL PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            sku TEXT DEFAULT '',
            description TEXT,
            quantity NUMERIC(14,3) DEFAULT 0,
            unit_price NUMERIC(12,2) DEFAULT 0,
            line_total NUMERIC(12,2) DEFAULT 0
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS match_log (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL DEFAULT 'demo_company',
            invoice_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            delivery_note_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            danger_count INTEGER DEFAULT 0,
            warning_count INTEGER DEFAULT 0,
            estimated_overcharge NUMERIC(12,2) DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_company_type_supplier ON documents (company_id, doc_type, supplier_name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_items_document_id ON document_items (document_id);")

    conn.commit()
    cur.close()
    conn.close()
    print("DB אותחל בהצלחה (suppliers, supplier_contacts, supplier_catalog_items, pricelist_items, price_history, upload_log, documents, document_items, match_log).")


if __name__ == "__main__":
    init_db()
