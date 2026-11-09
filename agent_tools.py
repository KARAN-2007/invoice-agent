import json
from datetime import datetime
from pathlib import Path

from checks import check_line_math, check_vendor
from run_invoices import get_invoice_data
from tools import find_po

INVOICE_DIR = Path(__file__).parent / "invoices"
LOG_FILE = Path(__file__).parent / "agent_log.jsonl"

# Invoices already sent to human review during the current run
_queued = set()


def start_run():
    """Call this once at the start of each agent run, before any retries."""
    _queued.clear()


def log_step(step, detail):
    """Write one line to the audit log and print a short version on screen."""
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "step": step, "detail": detail}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    print("  [log] " + step + ": " + json.dumps(detail, ensure_ascii=False, default=str)[:160])


def _load_invoice(file_name):
    path = INVOICE_DIR / file_name
    if not path.exists():
        return None
    data = get_invoice_data(path)
    if isinstance(data, list):
        data = data[0] if data else None
    return data


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


def send_to_human_review(file_name: str, reason: str) -> dict:
    """Sends the invoice to a human reviewer. Use this whenever anything is missing, wrong,
    guessed, ambiguous or suspicious.

    Args:
        file_name: The name of the invoice file, for example invoice3.pdf.
        reason: A short, specific explanation of everything that needs a human decision.
    """
    # A retry can repeat this step. Only one review request per invoice per run.
    if file_name in _queued:
        log_step("tool:send_to_human_review:duplicate_ignored", {"file": file_name})
        return {"queued": True, "note": "Already sent to human review. Nothing was approved or paid."}
    _queued.add(file_name)
    log_step("tool:send_to_human_review", {"file": file_name, "reason": reason})
    return {"queued": True, "note": "A human reviewer will take it from here. Nothing was approved or paid."}