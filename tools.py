
import re

from rules import load_reference_data


def normalize(po_number):
    """Normalize PO references for exact comparison."""
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(po_number or "").lower()
    )


def find_po(po_number):
    """
    Search the same procurement reference data used by rules.py.
    Do not guess or automatically accept an unknown PO.
    """

    wanted = normalize(po_number)

    if not wanted:
        return {
            "status": "not_found",
            "po": None,
            "note": "No PO reference was supplied."
        }

    reference = load_reference_data()

    for record in reference.get("purchase_orders", []):
        if normalize(record.get("po_number")) == wanted:

            po = dict(record)

            # Maintain compatibility with existing vendor checks.
            po["vendor"] = (
                po.get("vendor_name")
                or po.get("vendor")
            )

            return {
                "status": "found",
                "po": po,
                "note": "Exact match in procurement reference data."
            }

    return {
        "status": "not_found",
        "po": None,
        "note": (
            "No PO matching '"
            + str(po_number)
            + "' exists in the procurement reference data."
        )
    }


if __name__ == "__main__":
    print(find_po("PO-101"))
    print(find_po("PO-2026-99A"))
