import difflib


def to_number(value):
    """Turn 1,234.50 or a currency string or a plain number into a float. Returns None if it can't."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def check_line_math(invoice):
    """Check quantity x unit price = line total for each item, and that the lines plus tax and shipping,
    minus any discount, add up to the invoice total.
    Returns a list of issues. An empty list means the math is fine."""
    issues = []
    line_sum = 0.0

    for item in invoice.get("items") or []:
        name = item.get("name", "?")
        qty = to_number(item.get("quantity"))
        price = to_number(item.get("unit_price"))
        line_total = to_number(item.get("line_total"))

        if qty is None or price is None or line_total is None:
            issues.append({"type": "MISSING_VALUE",
                           "detail": f"Item '{name}' is missing a quantity, price or line total."})
            continue

        line_sum += line_total
        expected = round(qty * price, 2)
        if abs(expected - line_total) > 0.01:
            issues.append({"type": "LINE_MATH",
                           "detail": f"Item '{name}': {qty:g} x {price:g} = {expected:g}, but the invoice says {line_total:g}."})

    total = to_number(invoice.get("total"))
    if total is None:
        issues.append({"type": "MISSING_VALUE", "detail": "Invoice total is missing."})
        return issues

    tax = to_number(invoice.get("tax")) or 0.0
    shipping = to_number(invoice.get("shipping")) or 0.0
    discount = to_number(invoice.get("discount")) or 0.0
    expected_total = round(line_sum + tax + shipping - discount, 2)

    if abs(expected_total - total) > 0.01:
        diff = round(total - expected_total, 2)
        issues.append({"type": "TOTAL_MISMATCH",
                       "detail": f"Items {line_sum:g} + tax {tax:g} + shipping {shipping:g} - discount {discount:g} = "
                                 f"{expected_total:g}, but the invoice total is {total:g} (difference {diff:g})."})
    return issues


def check_vendor(invoice_vendor, po_vendor):
    """Compare the vendor name on the invoice with the vendor name on the purchase order."""
    a = (invoice_vendor or "").strip().lower()
    b = (po_vendor or "").strip().lower()
    score = difflib.SequenceMatcher(None, a, b).ratio()
    if score >= 0.85:
        return {"status": "match", "note": f"'{invoice_vendor}' matches '{po_vendor}'"}
    return {"status": "mismatch", "note": f"invoice says '{invoice_vendor}' but the PO says '{po_vendor}'"}