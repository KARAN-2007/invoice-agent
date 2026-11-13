import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options={"timeout": 45000})

# The main model is tried first. If FALLBACK_MODEL is set in .env, the reader switches to it
# when a try fails, so one busy model does not stop an invoice from being read.
MAIN_MODEL = (os.getenv("MODEL") or "gemini-3.1-flash-lite").strip()
fallback_candidates = os.getenv("FALLBACK_MODELS", "").split(",") + [os.getenv("FALLBACK_MODEL", "")]
MODELS = []
for model_name in [MAIN_MODEL] + fallback_candidates:
    model_name = model_name.strip()
    if model_name and model_name not in MODELS:
        MODELS.append(model_name)
MODEL = MODELS[0]

MAX_ATTEMPTS = 4
WAIT_SECONDS = [3, 8, 15]  # pause before each retry

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

    for attempt in range(1, MAX_ATTEMPTS + 1):
        model = MODELS[(attempt - 1) % len(MODELS)]
        try:
            response = client.models.generate_content(
                model=model,
                contents=[file_part, PROMPT],
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text)
        except Exception as e:
            print("Reading attempt", attempt, "with", model, "failed:", str(e)[:100])
            if attempt < MAX_ATTEMPTS:
                time.sleep(WAIT_SECONDS[min(attempt - 1, len(WAIT_SECONDS) - 1)])
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