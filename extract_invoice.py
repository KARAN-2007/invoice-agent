import os
import json
import time
import mimetypes
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options={"timeout": 30000})

MODEL = os.getenv("MODEL", "gemini-3.1-flash-lite")

PROMPT = (
    "Read this invoice. Return JSON with these keys: "
    "invoice_number, vendor, date, po_number, "
        "items (a list of objects with name, quantity, unit_price, line_total), "
    "subtotal, tax, shipping, discount, total. "
    "Use null for subtotal, tax, shipping or discount if the invoice does not show it."
)

SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def extract_invoice(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    mime_type = SUPPORTED_TYPES.get(ext)
    if mime_type is None:
        raise ValueError("Unsupported file type: " + ext)

    with open(file_path, "rb") as f:
        file_bytes = f.read()
    file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    for attempt in range(1, 6):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[file_part, PROMPT],
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text)
        except Exception as e:
            print("Attempt", attempt, "failed:", str(e)[:120])
            time.sleep(10)
    return None


if __name__ == "__main__":
    for name in sorted(os.listdir("invoices")):
        if os.path.splitext(name)[1].lower() in SUPPORTED_TYPES:
            print("=== " + name + " ===")
            result = extract_invoice("invoices/" + name)
            if result is None:
                print("Could not read this invoice.")
            else:
                print(json.dumps(result, indent=2))