import json
from datetime import datetime
from pathlib import Path

from checks import check_line_math, check_vendor
from db_store import save_log
from run_invoices import get_invoice_data
from tools import find_po
from rules import evaluate_invoice, load_reference_data

INVOICE_DIR = Path(__file__).parent / "invoices"
LOG_FILE = Path(__file__).parent / "agent_log.jsonl"

# Invoices sent to human review during the current run, with the reason given
_review_reasons = {}
_dataset_results = {}
_dataset_history = {}
_reference_data_cache = None
_db_warning_shown = False


def start_run(file_name):
    """Clear only this invoice's previous review reason."""
    _review_reasons.pop(file_name, None)
    _dataset_results.pop(file_name, None)


def review_reason(file_name):
    """The reason the agent gave for sending this invoice to human review, or None."""
    return _review_reasons.get(file_name)


def log_step(step, detail):
    """Write one line to the audit log (database and backup file) and print a short version on screen."""
    global _db_warning_shown
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "step": step, "detail": detail}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    if not save_log(step, detail) and not _db_warning_shown:
        print("  [warning] could not write to the database. Logging to the file only.")
        _db_warning_shown = True
    print("  [log] " + step + ": " + json.dumps(detail, ensure_ascii=False, default=str)[:160])


def _load_invoice(file_name):
    invoice_dir = INVOICE_DIR.resolve()
    path = (invoice_dir / file_name).resolve()

    if path.parent != invoice_dir or not path.is_file():
        return None
    try:
        data = get_invoice_data(path)
    except Exception as e:
        log_step("tool:load_invoice:error", {"file": file_name, "error": str(e)[:150]})
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    return data


def verify_invoice(file_name):
    """Re-run the checks in plain code, without the AI, and return a list of problems.
    This is a safety net: the AI's recommendation is never trusted on its own."""
    try:
        data = _load_invoice(file_name)
    except Exception as e:
        return ["Invoice could not be read: " + str(e)[:100]]
    if data is None:
        return ["Invoice could not be read."]

    problems = [issue["detail"] for issue in check_line_math(data)]

    po_number = str(data.get("po_number") or "").strip().upper()
    if not po_number:
        problems.append("No PO number on the invoice.")
    else:
        po_result = find_po(po_number)
        if po_result["status"] != "found":
            problems.append(po_result["note"])
        if po_result["po"] is not None:
            vendor_check = check_vendor(data.get("vendor"), po_result["po"]["vendor"])
            if vendor_check["status"] != "match":
                problems.append("Vendor mismatch: " + vendor_check["note"])
    return problems


def read_invoice(file_name: str) -> dict:
    """Reads an invoice file (PDF or image) from the invoices folder and returns its data:
    invoice_number, vendor, date, po_number, items and total. Always call this first.

    Args:
        file_name: The name of the invoice file, for example invoice3.pdf.
    """
    try:
        data = _load_invoice(file_name)
    except Exception as e:
        data = None
        log_step("tool:read_invoice:error", {"file": file_name, "error": str(e)[:150]})
    result = data if data is not None else {"error": "Could not read the invoice '" + file_name + "'."}
    log_step("tool:read_invoice", {"file": file_name, "result": result})
    return result


def check_invoice_math(file_name: str) -> dict:
    """Checks the invoice's own arithmetic: quantity times unit price for each item,
    and whether the items add up to the invoice total.

    Args:
        file_name: The name of the invoice file, for example invoice3.pdf.
    """
    data = _load_invoice(file_name)
    if data is None:
        result = {"error": "Invoice not available. Read the invoice first."}
    else:
        issues = check_line_math(data)
        result = {"math_ok": len(issues) == 0, "issues": issues}
    log_step("tool:check_invoice_math", {"file": file_name, "result": result})
    return result


def lookup_po(po_number: str) -> dict:
    """Looks up a purchase order (PO) in the company records. It handles small typos.
    Status is 'found' for an exact match, 'guessed' when only a close match exists and a human
    must confirm it, 'ambiguous' when several POs look similar, and 'not_found' when nothing matches.

    Args:
        po_number: The PO number exactly as printed on the invoice.
    """
    result = find_po(str(po_number).strip().upper())
    log_step("tool:lookup_po", {"po_number": po_number, "result": result})
    return result


def check_vendor_matches_po(file_name: str, po_number: str) -> dict:
    """Checks that the vendor name on the invoice matches the vendor on the purchase order.

    Args:
        file_name: The name of the invoice file, for example invoice3.pdf.
        po_number: The PO number to compare against, as printed on the invoice.
    """
    data = _load_invoice(file_name)
    po_result = find_po(str(po_number).strip().upper())
    if data is None or po_result["po"] is None:
        result = {"status": "cannot_check", "note": "Invoice or PO not available."}
    else:
        result = check_vendor(data.get("vendor"), po_result["po"]["vendor"])
    log_step("tool:check_vendor_matches_po", {"file": file_name, "po_number": po_number, "result": result})
    return result

def _first_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def _make_agent_history_record(data):
    """Convert an extracted invoice into the history format used by rules.py."""

    if not isinstance(data, dict):
        return None

    document_type = str(data.get("document_type") or "").upper()

    if "CREDIT_NOTE" in document_type or data.get("credit_note_number"):
        return None

    invoice_number = _first_value(
        data, "invoice_number", "invoice_no", "invoice_id"
    )

    if not invoice_number:
        return None

    vendor = data.get("vendor")

    if isinstance(vendor, dict):
        vendor_name = _first_value(vendor, "name", "vendor_name")
    else:
        vendor_name = vendor or data.get("vendor_name")

    items = data.get("items") or data.get("line_items") or []

    history_items = []

    for item in items:
        if not isinstance(item, dict):
            continue

        history_items.append({
            "description": _first_value(
                item, "description", "name", "product_name"
            ),
            "quantity_billed": _first_value(
                item, "quantity", "quantity_billed", "qty"
            ),
            "unit_price": _first_value(
                item, "unit_price", "unit_rate", "rate", "price"
            )
        })

    return {
        "invoice_number": invoice_number,
        "vendor_name": vendor_name,
        "po_number": _first_value(
            data, "po_reference", "po_number", "purchase_order"
        ),
        "status": "PROCESSED",
        "items": history_items
    }


def check_dataset_rules(file_name: str) -> dict:
    """Run deterministic checks with reference data and invoice history."""

    global _reference_data_cache

    try:
        data = _load_invoice(file_name)

        if data is None:
            result = {
                "case_id": file_name,
                "recommendation": "NEEDS_HUMAN_REVIEW",
                "findings": ["INCOMPLETE_EXTRACTION"]
            }

        else:
            if _reference_data_cache is None:
                _reference_data_cache = load_reference_data()

            # Include genuine prior invoices, but exclude the special
            # case-07 fixture that is deliberately represented by case 01.
            history = [
                dict(row)
                for row in _reference_data_cache.get("prior_invoices", [])
                if "duplicate test case 07"
                not in str(row.get("note", "")).lower()
            ]

            # Include invoices processed during this application session.
            history.extend(
                record
                for previous_file, record in _dataset_history.items()
                if previous_file != file_name
            )

            invoice_record = {
                "case_id": file_name,
                "document": data
            }

            result = evaluate_invoice(
                invoice_record,
                reference_data=_reference_data_cache,
                history=history
            )

            # Remember this invoice for subsequent checks.
            history_record = _make_agent_history_record(data)

            if history_record:
                _dataset_history[file_name] = history_record

    except Exception as e:
        result = {
            "case_id": file_name,
            "recommendation": "NEEDS_HUMAN_REVIEW",
            "findings": ["INCOMPLETE_EXTRACTION"],
            "error": str(e)[:150]
        }

    _dataset_results[file_name] = result

    log_step(
        "tool:check_dataset_rules",
        {"file": file_name, "result": result}
    )

    return result


def get_dataset_result(file_name):
    return _dataset_results.get(file_name)

def send_to_human_review(file_name: str, reason: str) -> dict:
    """Sends the invoice to a human reviewer. Use this whenever anything is missing, wrong,
    guessed, ambiguous or suspicious.

    Args:
        file_name: The name of the invoice file, for example invoice3.pdf.
        reason: A short, specific explanation of everything that needs a human decision.
    """
    # A retry can repeat this step. Only one review request per invoice per run.
    if file_name in _review_reasons:
        log_step("tool:send_to_human_review:duplicate_ignored", {"file": file_name})
        return {"queued": True, "note": "Already sent to human review. Nothing was approved or paid."}
    _review_reasons[file_name] = reason
    log_step("tool:send_to_human_review", {"file": file_name, "reason": reason})
    return {"queued": True, "note": "A human reviewer will take it from here. Nothing was approved or paid."}