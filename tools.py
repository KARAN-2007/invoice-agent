import difflib
from temp_sample_data import PURCHASE_ORDERS


def find_po(po_number):
    numbers = [po["po_number"] for po in PURCHASE_ORDERS]

    # 1. Exact match
    if po_number in numbers:
        po = PURCHASE_ORDERS[numbers.index(po_number)]
        return {"status": "found", "po": po, "note": "exact match"}

    # 2. Close matches (typo recovery)
    close = difflib.get_close_matches(po_number, numbers, n=3, cutoff=0.75)
    if len(close) == 1:
        po = PURCHASE_ORDERS[numbers.index(close[0])]
        return {
            "status": "guessed",
            "po": po,
            "note": "'" + po_number + "' not found, closest is '" + close[0] + "'. Needs confirmation.",
        }
    if len(close) > 1:
        return {"status": "ambiguous", "po": None, "note": "Several close matches: " + ", ".join(close) + ". Ask a human."}

    # 3. Nothing close
    return {"status": "not_found", "po": None, "note": "No PO like '" + po_number + "'."}


if __name__ == "__main__":
    print(find_po("PO-001"))
    print(find_po("PO-OO1-TYPO"))
    print(find_po("XYZ-999"))