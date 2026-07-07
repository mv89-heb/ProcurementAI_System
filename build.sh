#!/usr/bin/env bash
set -o errexit
python -m pip install --upgrade pip
pip install -r requirements.txt
python -c 'from core.database import init_db; init_db()'

# סנכרון אוטומטי של מחירוני הספקים + טלפונים מתוך legacy_data/ בכל דיפלוי.
# בטוח להרצה חוזרת (UPSERT) - לא יוצר כפילויות, רק מרענן מחירים/ספקים.
if [ -n "$DATABASE_URL" ]; then
    python scripts/import_legacy_data.py --data-dir legacy_data
else
    echo "מדלג על סנכרון legacy_data: DATABASE_URL לא מוגדר (ריצה מקומית?)"
fi
