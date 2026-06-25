import fitz
import io
import json
from PIL import Image
from google import genai
from google.genai import types

class AIEngine:
    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)
        
    def _prepare_document(self, file_storage):
        """תמיכה ב-PDF מרובה עמודים ישירות ללא המרה כבדה לתמונה"""
        file_bytes = file_storage.read()
        filename = file_storage.filename.lower()
        file_storage.seek(0)
        
        if filename.endswith('.pdf'):
            # מעביר את כל ה-PDF כ-Native Part לביצועים טובים וללא איבוד עמודים
            return types.Part.from_bytes(data=file_bytes, mime_type='application/pdf')
        else:
            # טיפול בתמונות כרגיל
            return types.Part.from_bytes(data=file_bytes, mime_type='image/jpeg')

    def classify_document(self, file_storage):
        part = self._prepare_document(file_storage)
        schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "doc_type": types.Schema(type=types.Type.STRING, enum=["pricelist", "invoice", "quote", "delivery", "po", "other"]),
                "confidence_score": types.Schema(type=types.Type.NUMBER)
            }
        )
        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[part, "סווג את סוג המסמך המצורף. החזר את הסוג ואת רמת הביטחון שלך בזיהוי (0-100)."],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0.0)
        )
        res = json.loads(response.text)
        return res.get("doc_type", "other"), res.get("confidence_score", 0)

    def extract_items(self, file_storage, prompt):
        part = self._prepare_document(file_storage)
        item_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "supplier_name": types.Schema(type=types.Type.STRING),
                "items": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "sku": types.Schema(type=types.Type.STRING),
                            "description": types.Schema(type=types.Type.STRING),
                            "qty": types.Schema(type=types.Type.NUMBER),
                            "price": types.Schema(type=types.Type.NUMBER),
                            "uom": types.Schema(type=types.Type.STRING),
                            "currency": types.Schema(type=types.Type.STRING), # תיקון: מטבע
                            "confidence_score": types.Schema(type=types.Type.NUMBER) # טיפול בהזיות
                        }
                    )
                )
            }
        )
        
        system_instruction = "אתה מומחה בקרת רכש. חלץ מידע מכל עמודי המסמך. אל תמציא נתונים. אם נתון חסר, השאר אותו ריק. תן ציון ביטחון 0-100 לכל שורה."
        
        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[part, system_instruction, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=item_schema, temperature=0.0)
        )
        return json.loads(response.text)

    def extract_pricelist(self, file):
        return self.extract_items(file, "חלץ את מחירון הספק מכל עמודי המסמך. הבא מק\"ט, תיאור, מחיר, מטבע ויחידת מידה.")
        
    def extract_invoice(self, file):
        return self.extract_items(file, "חלץ את החשבונית מכל עמודי המסמך. הבא מק\"ט, תיאור, כמות שסופקה בפועל, מחיר ומטבע.")