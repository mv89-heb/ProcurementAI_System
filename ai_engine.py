# -*- coding: utf-8 -*-
import json
from google import genai
from google.genai import types

class AIEngine:
    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)

    def prepare(self, file_storage):
        data = file_storage.read()
        file_storage.seek(0)
        
        if file_storage.filename.lower().endswith(".pdf"):
            return types.Part.from_bytes(data=data, mime_type="application/pdf")
        return types.Part.from_bytes(data=data, mime_type="image/jpeg")

    def extract_pricelist(self, file_storage):
        part = self.prepare(file_storage)
        prompt = """
        חלץ את המחירון מהמסמך. 
        החזר אך ורק אובייקט JSON תקין במבנה הבא:
        {
          "items": [
            {"sku": "string", "description": "string", "price": number}
          ]
        }
        """
        try:
            res = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            return json.loads(res.text).get("items", [])
        except Exception as e:
            print("AI Extraction Error:", e)
            return []

    def extract_document(self, file_storage, doc_type="invoice"):
        """
        מחלץ מסמך חשבונית או תעודת משלוח, כולל כותרת (מספר מסמך, ספק, תאריך)
        ורשימת שורות עם כמויות (ולחשבונית - גם מחירים).

        doc_type: 'invoice' (חשבונית) או 'delivery_note' (תעודת משלוח)
        מחזיר dict: {doc_number, supplier_name, doc_date, reference_number, items: [...]}
        """
        part = self.prepare(file_storage)

        if doc_type == "delivery_note":
            prompt = """
            זהו תעודת משלוח (Delivery Note). חלץ ממנה בעברית או באנגלית לפי מה שמופיע במסמך:
            - doc_number: מספר תעודת המשלוח
            - supplier_name: שם הספק/החברה המשלחת (כפי שמופיע בכותרת המסמך)
            - doc_date: תאריך בפורמט YYYY-MM-DD אם ניתן לזהות, אחרת מחרוזת ריקה
            - items: רשימת פריטים, עבור כל אחד: sku (מק"ט אם קיים, אחרת ריק),
              description (תיאור הפריט), quantity (כמות שסופקה, מספר)
            אם אין מחירים במסמך (שכיח בתעודות משלוח) - זה בסדר, אל תמציא מחיר.
            החזר אך ורק JSON תקין במבנה:
            {
              "doc_number": "string",
              "supplier_name": "string",
              "doc_date": "string",
              "items": [{"sku": "string", "description": "string", "quantity": number}]
            }
            """
        else:
            prompt = """
            זהו חשבונית מס (Invoice). חלץ ממנה:
            - doc_number: מספר החשבונית
            - supplier_name: שם הספק (כפי שמופיע בכותרת המסמך)
            - doc_date: תאריך בפורמט YYYY-MM-DD אם ניתן לזהות, אחרת מחרוזת ריקה
            - reference_number: אם מוזכר במסמך מספר תעודת משלוח מקושרת - הכנס אותו כאן,
              אחרת השאר מחרוזת ריקה
            - items: רשימת שורות, עבור כל אחת: sku (מק"ט אם קיים, אחרת ריק),
              description (תיאור הפריט), quantity (כמות), unit_price (מחיר ליחידה),
              line_total (סה"כ לשורה אם מופיע, אחרת quantity*unit_price)
            החזר אך ורק JSON תקין במבנה:
            {
              "doc_number": "string",
              "supplier_name": "string",
              "doc_date": "string",
              "reference_number": "string",
              "items": [{"sku": "string", "description": "string", "quantity": number, "unit_price": number, "line_total": number}]
            }
            """

        try:
            res = self.client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            data = json.loads(res.text)
            data.setdefault("items", [])
            return data
        except Exception as e:
            print("Document Extraction Error:", e)
            return {"items": []}

    def get_embedding(self, text):
        try:
            res = self.client.models.embed_content(
                model="text-embedding-004",
                contents=text
            )
            return res.embeddings[0].values
        except Exception as e:
            print("Embedding Error:", e)
            return None
