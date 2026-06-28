import json
from google import genai
from google.genai import types


class AIEngine:

    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)

    def prepare(self, file):
        data = file.read()

        if file.filename.lower().endswith(".pdf"):
            return types.Part.from_bytes(data=data, mime_type="application/pdf")

        return types.Part.from_bytes(data=data, mime_type="image/jpeg")

    def extract_invoice(self, file):

        part = self.prepare(file)

        prompt = """
        חלץ חשבונית.

        החזר JSON בלבד:
        {
          "items":[
            {
              "sku": "string",
              "description": "string",
              "price": number
            }
          ]
        }

        אל תמציא נתונים.
        """

        try:
            res = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[part, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0
                )
            )

            return json.loads(res.text).get("items", [])

        except Exception as e:
            print("AI ERROR:", e)
            return []

    def extract_pricelist(self, file):

        part = self.prepare(file)

        prompt = """
        חלץ מחירון.

        החזר JSON:
        {
          "supplier_name": "string",
          "items":[
            {
              "sku": "string",
              "description": "string",
              "price": number
            }
          ]
        }
        """

        res = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0
            )
        )

        return json.loads(res.text)
