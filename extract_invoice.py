import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options={"timeout": 30000})

MODEL = "gemini-3.1-flash-lite"

with open("invoices/invoice1.pdf", "rb") as f:
    pdf_bytes = f.read()

pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
prompt = (
    "Read this invoice. Return JSON with these keys: "
    "invoice_number, vendor, date, po_number, "
    "items (a list of objects with name, quantity, unit_price), total."
)

response = None
for attempt in range(1, 6):
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[pdf_part, prompt],
            config={"response_mime_type": "application/json"},
        )
        break
    except Exception as e:
        print("Attempt", attempt, "failed:", str(e)[:120])
        time.sleep(10)

if response is None:
    print("Gemini is too busy right now. Try again later.")
else:
    data = json.loads(response.text)
    print(json.dumps(data, indent=2))