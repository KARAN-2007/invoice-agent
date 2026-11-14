import json
from pathlib import Path

from checks import check_line_math, check_vendor
from extract_invoice import extract_invoice, SUPPORTED_TYPES
from tools import find_po

INVOICE_DIR = Path(__file__).parent / "invoices"
CACHE_DIR = Path(__file__).parent / "data" / "invoices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_VERSION = 2


def get_invoice_data(path):
    path = Path(path)

    cache_file = CACHE_DIR / (path.name + ".json")
    source_stat = path.stat()

    # Reuse cache only if it was created with the current version.
    if cache_file.exists():
        try:
            cached = json.loads(
                cache_file.read_text(encoding="utf-8")
            )

            if (
                isinstance(cached, dict)
                and cached.get("_cache_version") == CACHE_VERSION
                and cached.get("_source_size") == source_stat.st_size
                and cached.get("_source_mtime_ns") == source_stat.st_mtime_ns
            ):
                return cached.get("data")

        except (json.JSONDecodeError, OSError):
            pass

    # Extract the invoice again if the cache is outdated.
    data = extract_invoice(str(path))

    if data is not None:
        cache_data = {
            "_cache_version": CACHE_VERSION,
            "_source_size": source_stat.st_size,
            "_source_mtime_ns": source_stat.st_mtime_ns,
            "data": data
        }

        cache_file.write_text(
            json.dumps(cache_data, indent=2),
            encoding="utf-8"
        )

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

        # Checks that only need the invoice itself
        issues = check_line_math(data)
        if issues:
            for issue in issues:
                print("  ! " + issue["type"] + ": " + issue["detail"])
        else:
            print("  math: OK")

        po_number = str(data.get("po_number") or "").strip().upper()
        if not po_number:
            print("  no PO number on invoice -> manual review")
            continue

        result = find_po(po_number)
        print("  extracted PO: " + po_number)
        print("  status: " + result["status"] + " | " + result["note"])
        if result["po"]:
            vendor_check = check_vendor(data.get("vendor"), result["po"]["vendor"])
            print("  vendor: " + vendor_check["status"] + " | " + vendor_check["note"])


if __name__ == "__main__":
    main()