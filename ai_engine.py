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
                model="gemini-2.5-flash",
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

    def get_embedding(self, text):
        try:
            res = self.client.models.embed_content(
                model="text-embedding-004",
                contents=text
            )
            # ג'מיני מחזיר רשימה של אובייקטים, ניקח את הראשון
            return res.embeddings[0].values
        except Exception as e:
            print("Embedding Error:", e)
            return None
