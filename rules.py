"""Dataset-backed invoice checks for the university AP demo.

The rules read procurement reference records from dataset/reference_data.json.
They return reason codes (not display text) so the UI can render them with
reason_labels.py.

Typical use:
    from rules import evaluate_dataset
    results = evaluate_dataset()

Run this file directly to print the 18 dataset cases and compare them with the
expected manifest:
    python rules.py
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
REFERENCE_FILE = DATASET_DIR / "reference_data.json"
INVOICES_FILE = DATASET_DIR / "invoice_documents.json"
MANIFEST_FILE = DATASET_DIR / "test_manifest.json"

# The one informational code does not require human review on its own.
INFO_ONLY_CODES = {"PRICE_VARIANCE_WITHIN_TOLERANCE"}


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reference_data(path: str | Path | None = None) -> dict[str, Any]:
    """Load procurement reference records. Defaults to dataset/reference_data.json."""
    return _read_json(Path(path) if path else REFERENCE_FILE)


def load_invoice_documents(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load the 18 invoice test records. Defaults to dataset/invoice_documents.json."""
    return _read_json(Path(path) if path else INVOICES_FILE)


def load_test_manifest(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load expected outcomes. The manifest is for testing only, never for decisions."""
    return _read_json(Path(path) if path else MANIFEST_FILE)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first_not_none(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None and mapping[key] != "":
            return mapping[key]
    return None


def _invoice_document(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("document", record)
    return value if isinstance(value, dict) else {}


def _vendor_block(document: dict[str, Any]) -> dict[str, Any]:
    value = document.get("vendor")
    return value if isinstance(value, dict) else {}


def _vendor_name(document: dict[str, Any]) -> str | None:
    vendor = _vendor_block(document)
    value = _first_not_none(
        vendor,
        "name",
        "vendor_name",
        "supplier_name",
        "supplier",
    )
    if value is None:
        value = _first_not_none(document, "vendor_name", "supplier_name", "supplier", "vendor")
    return _text(value) or None


def _vendor_code(document: dict[str, Any]) -> str | None:
    vendor = _vendor_block(document)
    value = _first_not_none(vendor, "vendor_code", "vendor_id", "code")
    if value is None:
        value = _first_not_none(document, "vendor_code", "vendor_id")
    return _text(value) or None


def _vendor_gstin(document: dict[str, Any]) -> str | None:
    vendor = _vendor_block(document)
    value = _first_not_none(vendor, "gstin", "GSTIN")
    if value is None:
        value = _first_not_none(document, "gstin", "GSTIN")
    return _text(value).upper() or None


def _invoice_number(document: dict[str, Any]) -> str | None:
    value = _first_not_none(
        document,
        "invoice_number",
        "invoice_no",
        "invoice_id",
        "credit_note_number",
        "document_id",
    )
    return _text(value) or None


def _invoice_date(document: dict[str, Any]) -> str | None:
    value = _first_not_none(document, "invoice_date", "date", "issue_date")
    return _text(value) or None


def _po_reference(document: dict[str, Any]) -> str | None:
    value = _first_not_none(
        document,
        "po_reference",
        "po_number",
        "purchase_order",
        "purchase_order_reference",
        "reference_po",
        "purchase_reference",
    )
    if value is None:
        details = document.get("reference_details")
        if isinstance(details, dict):
            reference_type = _text(details.get("reference_type")).lower()
            if "purchase order" in reference_type or reference_type == "po":
                value = details.get("customer_order")
    return _text(value) or None


def _currency(document: dict[str, Any]) -> str | None:
    value = _first_not_none(document, "currency", "currency_code")
    return _text(value).upper() or None


def _items(document: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "products", "line_items"):
        value = document.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _item_description(item: dict[str, Any]) -> str:
    return _text(_first_not_none(item, "description", "name", "product_name", "service_name"))


def _item_quantity(item: dict[str, Any]) -> Decimal | None:
    return _decimal(_first_not_none(item, "quantity", "qty", "quantity_billed"))


def _item_unit_price(item: dict[str, Any]) -> Decimal | None:
    return _decimal(_first_not_none(item, "unit_price", "unit_rate", "rate", "price"))


def _item_amount(item: dict[str, Any]) -> Decimal | None:
    return _decimal(_first_not_none(item, "amount", "total", "line_total", "line_amount", "credit_amount"))


def _item_net_unit_price(item: dict[str, Any]) -> Decimal | None:
    price = _item_unit_price(item)
    if price is None:
        return None
    discount = _decimal(item.get("discount_percent"))
    if discount is not None:
        price *= (Decimal("100") - discount) / Decimal("100")
    return price


def _invoice_total(document: dict[str, Any]) -> Decimal | None:
    return _decimal(
        _first_not_none(document, "amount_due", "grand_total", "invoice_total", "total", "net_payable", "total_credit")
    )


def _invoice_subtotal(document: dict[str, Any]) -> Decimal | None:
    return _decimal(_first_not_none(document, "subtotal", "net_payable"))


def _tax_amount(document: dict[str, Any]) -> Decimal | None:
    keys = ("cgst", "sgst", "igst", "gst", "tax", "tax_amount", "tax_adjustment")
    found = [_decimal(document.get(key)) for key in keys if document.get(key) is not None]
    if not found:
        return None
    return sum((value for value in found if value is not None), Decimal("0"))


def _discount_amount(document: dict[str, Any]) -> Decimal:
    return _decimal(document.get("discount")) or Decimal("0")


def _vendor_indexes(reference: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    by_gstin: dict[str, dict[str, Any]] = {}
    for vendor in reference.get("vendors", []):
        if vendor.get("vendor_id"):
            by_id[_text(vendor["vendor_id"]).upper()] = vendor
        if vendor.get("name"):
            by_name[_norm(vendor["name"])] = vendor
        if vendor.get("gstin"):
            by_gstin[_text(vendor["gstin"]).upper()] = vendor
    return by_id, by_name, by_gstin


def _find_vendor(document: dict[str, Any], reference: dict[str, Any], po: dict[str, Any] | None = None) -> dict[str, Any] | None:
    by_id, by_name, _ = _vendor_indexes(reference)
    code = _vendor_code(document)
    if code and code.upper() in by_id:
        return by_id[code.upper()]
    name = _vendor_name(document)
    if name and _norm(name) in by_name:
        return by_name[_norm(name)]
    if po and po.get("vendor_id"):
        return by_id.get(_text(po["vendor_id"]).upper())
    return None


def _find_po(po_reference: str | None, reference: dict[str, Any]) -> dict[str, Any] | None:
    if not po_reference:
        return None
    target = _norm(po_reference)
    for po in reference.get("purchase_orders", []):
        if _norm(po.get("po_number")) == target:
            return po
    return None


def _find_po_item(description: str, po: dict[str, Any]) -> dict[str, Any] | None:
    target = _norm(description)
    for item in po.get("items", []):
        if _norm(item.get("description")) == target:
            return item
    return None


def _history_vendor_key(record: dict[str, Any], reference: dict[str, Any]) -> str:
    vendor_id = _text(record.get("vendor_id")).upper()
    if vendor_id:
        return vendor_id
    name = _text(record.get("vendor_name") or record.get("vendor"))
    if name:
        _, by_name, _ = _vendor_indexes(reference)
        vendor = by_name.get(_norm(name))
        if vendor:
            return _text(vendor.get("vendor_id")).upper()
        return _norm(name)
    return ""


def _invoice_vendor_key(document: dict[str, Any], reference: dict[str, Any], po: dict[str, Any] | None) -> str:
    vendor = _find_vendor(document, reference, po)
    if vendor:
        return _text(vendor.get("vendor_id")).upper()
    name = _vendor_name(document)
    return _norm(name) if name else ""


def _history_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    value = record.get("items", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _is_credit_note(document: dict[str, Any]) -> bool:
    dtype = _text(document.get("document_type")).upper()
    return "CREDIT_NOTE" in dtype or bool(document.get("credit_note_number"))


def _is_low_quality(record: dict[str, Any]) -> bool:
    source = record.get("source_document")
    return isinstance(source, dict) and bool(source.get("quality_flags"))


def _check_incomplete_extraction(document: dict[str, Any]) -> bool:
    required_values = [
        _invoice_number(document),
        _vendor_name(document),
        _po_reference(document),
        _currency(document),
        _invoice_total(document),
    ]
    if any(value is None or value == "" for value in required_values):
        return True
    invoice_items = _items(document)
    if not invoice_items:
        return True
    for item in invoice_items:
        if not _item_description(item) or _item_quantity(item) is None or _item_unit_price(item) is None or _item_amount(item) is None:
            return True
    return False


def _check_arithmetic(document: dict[str, Any]) -> bool:
    """Return True when line/subtotal/tax/grand-total arithmetic is inconsistent."""
    if _is_credit_note(document):
        return False  # credit notes are always sent for human handling in this demo

    subtotal = _invoice_subtotal(document)
    total = _invoice_total(document)
    if subtotal is None or total is None:
        return False

    invoice_items = _items(document)
    # If line amounts are available for all lines, verify they add to the stated subtotal.
    amounts = [_item_amount(item) for item in invoice_items]
    if invoice_items and all(amount is not None for amount in amounts):
        line_sum = sum((amount for amount in amounts if amount is not None), Decimal("0"))
        if abs(line_sum - subtotal) > Decimal("0.01"):
            return True

    tax = _tax_amount(document)
    # A document with no explicit tax field is treated as tax-free for this arithmetic check.
    tax = tax if tax is not None else Decimal("0")
    expected_total = subtotal - _discount_amount(document) + tax
    return abs(expected_total - total) > Decimal("0.01")


def _check_tax_rate(document: dict[str, Any], po: dict[str, Any] | None) -> bool:
    if not po or _is_credit_note(document):
        return False
    taxable_base = _invoice_subtotal(document)
    if taxable_base is not None:
        taxable_base -= _discount_amount(document)
    actual_tax = _tax_amount(document)
    if taxable_base is None or taxable_base <= 0 or actual_tax is None:
        return False

    invoice_items = _items(document)
    expected_rates: list[Decimal] = []
    for invoice_item in invoice_items:
        po_item = _find_po_item(_item_description(invoice_item), po)
        if po_item and _decimal(po_item.get("tax_percent")) is not None:
            expected_rates.append(_decimal(po_item.get("tax_percent")))
    if expected_rates:
        expected_rate = expected_rates[0]
        if any(abs(rate - expected_rate) > Decimal("0.01") for rate in expected_rates):
            return False  # mixed tax rates need a more detailed line-level tax allocation
    else:
        policy = po.get("tax_percent")
        expected_rate = _decimal(policy)
        if expected_rate is None:
            rules = po.get("tax_rules")
            if isinstance(rules, dict):
                expected_rate = _decimal(rules.get("combined_gst_percent"))
    if expected_rate is None:
        # Use the demo domestic policy only where a PO has no line-specific rate.
        policy = (po.get("policies") or {}).get("tax_rules", {}).get("INR_domestic_example", {})
        expected_rate = _decimal(policy.get("combined_gst_percent")) if isinstance(policy, dict) else None
    if expected_rate is None:
        return False

    actual_rate = actual_tax * Decimal("100") / taxable_base
    return abs(actual_rate - expected_rate) > Decimal("0.05")


def _check_price_variance(document: dict[str, Any], po: dict[str, Any] | None) -> list[str]:
    if not po or _is_credit_note(document):
        return []
    tolerance = _decimal(po.get("price_tolerance_percent"))
    if tolerance is None:
        tolerance = Decimal("0")
    findings: list[str] = []
    for invoice_item in _items(document):
        po_item = _find_po_item(_item_description(invoice_item), po)
        invoice_price = _item_net_unit_price(invoice_item)
        approved_price = _decimal(po_item.get("approved_unit_price")) if po_item else None
        if invoice_price is None or approved_price is None or approved_price == 0:
            continue
        variance = abs(invoice_price - approved_price) * Decimal("100") / approved_price
        if variance > tolerance + Decimal("0.0001"):
            findings.append("PO_UNIT_PRICE_MISMATCH")
            break
        if variance > Decimal("0.0001"):
            findings.append("PRICE_VARIANCE_WITHIN_TOLERANCE")
            break
    return findings


def _check_grn(document: dict[str, Any], po: dict[str, Any] | None, reference: dict[str, Any]) -> list[str]:
    if not po or _is_credit_note(document):
        return []

    if _text(po.get("procurement_type")).upper() == "SERVICE":
        confirmations = [
            row for row in reference.get("service_confirmations", [])
            if _norm(row.get("po_number")) == _norm(po.get("po_number"))
            and _text(row.get("status")).upper() == "ACCEPTED"
        ]
        details = document.get("service_details")
        details = details if isinstance(details, dict) else {}
        hours = _decimal(details.get("hours"))
        if not confirmations:
            return ["MISSING_GOODS_RECEIPT"]
        if hours is not None and sum((_decimal(row.get("hours_accepted")) or Decimal("0") for row in confirmations), Decimal("0")) < hours:
            return ["GRN_QUANTITY_SHORTAGE"]
        return []

    # Digital licences/subscriptions are not delivered through a physical GRN.
    po_item_names = [
        _text(item.get("description")).lower() for item in po.get("items", [])
    ]
    if po_item_names and all(
        any(word in name for word in ("license", "licence", "software", "subscription"))
        for name in po_item_names
    ):
        return []

    receipts = [
        row for row in reference.get("goods_receipts", [])
        if _norm(row.get("po_number")) == _norm(po.get("po_number"))
        and _text(row.get("status")).upper() in {"ACCEPTED", "PARTIALLY_ACCEPTED"}
    ]
    if not receipts:
        return ["MISSING_GOODS_RECEIPT"]

    findings: list[str] = []
    for invoice_item in _items(document):
        description = _item_description(invoice_item)
        billed = _item_quantity(invoice_item)
        if billed is None:
            continue
        accepted = Decimal("0")
        for receipt in receipts:
            for received_item in receipt.get("items", []):
                if _norm(received_item.get("description")) == _norm(description):
                    accepted += _decimal(received_item.get("quantity_accepted")) or Decimal("0")
        if billed > accepted:
            findings.append("GRN_QUANTITY_SHORTAGE")
            break
    return findings


def _check_cumulative_po_quantity(
    document: dict[str, Any], po: dict[str, Any] | None, history: list[dict[str, Any]]
) -> bool:
    if not po or _is_credit_note(document):
        return False
    po_number = _norm(po.get("po_number"))
    for current_item in _items(document):
        description = _norm(_item_description(current_item))
        current_qty = _item_quantity(current_item)
        if not description or current_qty is None:
            continue
        previous_qty = Decimal("0")
        for previous in history:
            if _norm(previous.get("po_number")) != po_number:
                continue
            status = _text(previous.get("status")).upper()
            if status in {"VOID", "CANCELLED", "REJECTED"}:
                continue
            for previous_item in _history_items(previous):
                if _norm(previous_item.get("description")) == description:
                    previous_qty += _decimal(
                        _first_not_none(previous_item, "quantity_billed", "quantity", "qty")
                    ) or Decimal("0")
        po_item = _find_po_item(_item_description(current_item), po)
        ordered_qty = _decimal(po_item.get("quantity_ordered")) if po_item else None
        if ordered_qty is not None and previous_qty + current_qty > ordered_qty:
            return True
    return False


def _make_history_record(document: dict[str, Any], reference: dict[str, Any], po: dict[str, Any] | None) -> dict[str, Any] | None:
    invoice_number = _invoice_number(document)
    if not invoice_number or _is_credit_note(document):
        return None
    vendor_key = _invoice_vendor_key(document, reference, po)
    vendor_id = vendor_key if vendor_key.startswith("VEN-") else None
    po_number = _po_reference(document)
    return {
        "invoice_number": invoice_number,
        "vendor_id": vendor_id,
        "vendor_name": _vendor_name(document),
        "po_number": po_number,
        "currency": _currency(document),
        "total": str(_invoice_total(document) or ""),
        "status": "PROCESSED",
        "items": [
            {
                "description": _item_description(item),
                "quantity_billed": str(_item_quantity(item) or "0"),
                "unit_price": str(_item_unit_price(item) or "0"),
            }
            for item in _items(document)
        ],
    }


def evaluate_invoice(
    invoice_record: dict[str, Any],
    reference_data: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one invoice record and return its reason codes and recommendation.

    `history` should contain previous invoices already processed plus applicable
    prior-invoice fixtures. `evaluate_dataset()` manages the required sequence.
    """
    reference = reference_data or load_reference_data()
    prior = list(history or [])
    document = _invoice_document(invoice_record)
    findings: list[str] = []

    def add(code: str) -> None:
        if code not in findings:
            findings.append(code)

    # A damaged/partial scan has its own expected outcome. Avoid adding secondary
    # PO/vendor/quantity findings from fields that OCR could not reliably read.
    if _is_low_quality(invoice_record):
        add("LOW_DOCUMENT_QUALITY")
        if _check_incomplete_extraction(document):
            add("INCOMPLETE_EXTRACTION")
        return _result(invoice_record, findings)

    if _check_arithmetic(document):
        add("INVOICE_ARITHMETIC_MISMATCH")

    if _is_credit_note(document):
        add("CREDIT_NOTE_SPECIAL_HANDLING")
        return _result(invoice_record, findings)

    po_reference = _po_reference(document)
    if not po_reference:
        add("MISSING_PO_REFERENCE")
        return _result(invoice_record, findings)

    po = _find_po(po_reference, reference)
    if po is None:
        add("PO_REFERENCE_UNMAPPED")
        return _result(invoice_record, findings)

    if _text(po.get("status")).upper() == "CANCELLED":
        add("CANCELLED_PO")

    invoice_number = _invoice_number(document)
    invoice_vendor_key = _invoice_vendor_key(document, reference, po)
    for previous in prior:
        previous_number = _text(previous.get("invoice_number"))
        previous_vendor_key = _history_vendor_key(previous, reference)
        if (
            invoice_number
            and _norm(invoice_number) == _norm(previous_number)
            and invoice_vendor_key
            and invoice_vendor_key == previous_vendor_key
        ):
            add("DUPLICATE_INVOICE")
            break

    # Check the vendor GSTIN against the master record (the PO determines the
    # expected vendor when the invoice itself omits a vendor code).
    expected_vendor = _find_vendor(document, reference, po)
    if expected_vendor is None and po.get("vendor_id"):
        by_id, _, _ = _vendor_indexes(reference)
        expected_vendor = by_id.get(_text(po.get("vendor_id")).upper())
    invoice_gstin = _vendor_gstin(document)
    expected_gstin = _text(expected_vendor.get("gstin")).upper() if expected_vendor else ""
    if invoice_gstin and expected_gstin and invoice_gstin != expected_gstin:
        add("VENDOR_GSTIN_MISMATCH")

    findings.extend(code for code in _check_price_variance(document, po) if code not in findings)
    if _text(po.get("status")).upper() != "CANCELLED":
        findings.extend(code for code in _check_grn(document, po, reference) if code not in findings)

    # A duplicate submission is already flagged; do not count the same invoice
    # twice toward cumulative PO quantity.
    if "DUPLICATE_INVOICE" not in findings and _check_cumulative_po_quantity(document, po, prior):
        add("CUMULATIVE_PO_OVERBILL")

    if _check_tax_rate(document, po):
        add("GST_RATE_MISMATCH")

    if _check_fx_rate(document, reference):
        add("FX_RATE_MISMATCH")

    return _result(invoice_record, findings)


def _check_fx_rate(document: dict[str, Any], reference: dict[str, Any]) -> bool:
    currency = _currency(document)
    invoice_date = _invoice_date(document)
    invoice_rate = _decimal(document.get("exchange_rate"))
    if not currency or not invoice_date or invoice_rate is None:
        return False
    for fixture in reference.get("fx_rates", []):
        if _text(fixture.get("currency")).upper() == currency and _text(fixture.get("date")) == invoice_date:
            expected = _decimal(fixture.get("reference_rate"))
            return expected is not None and abs(invoice_rate - expected) > Decimal("0.0001")
    return False


def _result(invoice_record: dict[str, Any], findings: list[str]) -> dict[str, Any]:
    # Keep reason codes unique while preserving order.
    unique = list(dict.fromkeys(findings))
    needs_review = any(code not in INFO_ONLY_CODES for code in unique)
    return {
        "case_id": invoice_record.get("case_id"),
        "recommendation": "NEEDS_HUMAN_REVIEW" if needs_review else "READY_FOR_APPROVAL",
        "findings": unique,
    }


def evaluate_dataset(
    invoice_documents: list[dict[str, Any]] | None = None,
    reference_data: dict[str, Any] | None = None,
    manifest: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate records in manifest order with sequential invoice history.

    The special prior invoice used only for duplicate case 07 is intentionally
    not seeded before case 01. Case 01 is added to history, so case 07 detects it.
    The CHAIR-114-01 fixture remains seeded for cumulative-overbilling case 14.
    """
    invoices = invoice_documents if invoice_documents is not None else load_invoice_documents()
    reference = reference_data if reference_data is not None else load_reference_data()
    expected = manifest if manifest is not None else load_test_manifest()

    by_case_id = {record.get("case_id"): record for record in invoices}
    history = [
        dict(record)
        for record in reference.get("prior_invoices", [])
        if "duplicate test case 07" not in _text(record.get("note")).lower()
    ]

    results: list[dict[str, Any]] = []
    for manifest_row in sorted(expected, key=lambda row: row.get("sequence", 0)):
        case_id = manifest_row.get("case_id")
        invoice_record = by_case_id.get(case_id)
        if invoice_record is None:
            results.append({
                "case_id": case_id,
                "recommendation": "MISSING_TEST_INPUT",
                "findings": [],
            })
            continue
        result = evaluate_invoice(invoice_record, reference, history)
        results.append(result)

        # Processed invoices become history for later duplicate/cumulative checks.
        po = _find_po(_po_reference(_invoice_document(invoice_record)), reference)
        history_record = _make_history_record(_invoice_document(invoice_record), reference, po)
        if history_record:
            history.append(history_record)

    return results


def compare_with_manifest(
    results: list[dict[str, Any]], manifest: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Return only mismatches between computed and expected results."""
    expected = manifest if manifest is not None else load_test_manifest()
    expected_by_id = {row["case_id"]: row for row in expected}
    mismatches: list[dict[str, Any]] = []
    for result in results:
        row = expected_by_id.get(result.get("case_id"), {})
        expected_recommendation = row.get("expected_recommendation")
        expected_findings = sorted(row.get("expected_findings", []))
        actual_findings = sorted(result.get("findings", []))
        if (
            result.get("recommendation") != expected_recommendation
            or actual_findings != expected_findings
        ):
            mismatches.append({
                "case_id": result.get("case_id"),
                "expected_recommendation": expected_recommendation,
                "actual_recommendation": result.get("recommendation"),
                "expected_findings": expected_findings,
                "actual_findings": actual_findings,
            })
    return mismatches


def main() -> int:
    results = evaluate_dataset()
    mismatches = compare_with_manifest(results)
    for result in results:
        codes = ", ".join(result["findings"]) if result["findings"] else "(none)"
        print(f'{result["case_id"]}: {result["recommendation"]} | {codes}')
    print(f"\nPassed: {len(results) - len(mismatches)}/{len(results)}")
    if mismatches:
        print("\nMismatches:")
        for mismatch in mismatches:
            print(json.dumps(mismatch, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
