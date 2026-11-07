import difflib
import re

from sample_data import PURCHASE_ORDERS


def normalize(po_number):
    s = po_number.upper().strip()
    s = re.sub(r"[\s_#:/\\]+", "-", s)
    m = re.match(r"^(PO-?)(.*)$", s)
    if m:
        s = m.group(1) + m.group(2).replace("O", "0")
    return s


def find_po(po_number):
    numbers = [po["po_number"] for po in PURCHASE_ORDERS]

    # 1. Exact match
    if po_number in numbers:
        po = PURCHASE_ORDERS[numbers.index(po_number)]
        return {"status": "found", "po": po, "note": "exact match"}

    # 2. Close matches (typo recovery)
    wanted = normalize(po_number)
    scored = []
    for n in numbers:
        score = difflib.SequenceMatcher(None, wanted, normalize(n)).ratio()
        if score >= 0.6:
            scored.append((score, n))
    scored.sort(reverse=True)
    close = [n for score, n in scored[:3]]
    # If the best match is clearly better than the runner-up, treat it as the one guess
    if len(scored) > 1 and scored[0][0] - scored[1][0] >= 0.15:
        close = [scored[0][1]]

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