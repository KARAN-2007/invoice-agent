import json
from pathlib import Path

from extract_invoice import extract_invoice, SUPPORTED_TYPES
from tools import find_po

INVOICE_DIR = Path(__file__).parent / "invoices"
CACHE_DIR = Path(__file__).parent / "extracted"
CACHE_DIR.mkdir(exist_ok=True)


def get_invoice_data(path):
    cache_file = CACHE_DIR / (path.name + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    data = extract_invoice(str(path))
    if data is not None:
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main():
    files = sorted(p for p in INVOICE_DIR.iterdir() if p.suffix.lower() in SUPPORTED_TYPES)

    for path in files:
        print("=== " + path.name + " ===")

        data = get_invoice_data(path)
        if data is None:
            print("  extraction failed -> manual review")
            continue

        # Gemini can return a list if the file has several invoices
        if isinstance(data, list):
            data = data[0] if data else {}

        po_number = str(data.get("po_number") or "").strip().upper()
        if not po_number:
            print("  no PO number on invoice -> manual review")
            continue

        result = find_po(po_number)
        print("  extracted PO: " + po_number)
        print("  status: " + result["status"] + " | " + result["note"])
        if result["po"]:
            print("  vendor on PO: " + result["po"]["vendor"])
            print("  vendor on invoice: " + str(data.get("vendor")))


if __name__ == "__main__":
    main()