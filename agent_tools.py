import copy
import json
import re
from datetime import datetime
from pathlib import Path

from checks import check_line_math, check_vendor
from db_store import save_log
from run_invoices import get_invoice_data
from tools import (
    find_po,
    load_saved_goods_receipts,
    load_saved_purchase_orders,
)
from rules import evaluate_invoice, load_reference_data

INVOICE_DIR = Path(__file__).resolve().parent / "invoices"
LOG_FILE = Path(__file__).resolve().parent / "agent_log.jsonl"

_review_reasons = {}
_dataset_results = {}
_dataset_history = {}
_tool_results = {}
_reference_data_cache = None
_db_warning_shown = False

_CACHE_MISS = object()


def _cache_key(tool_name, file_name, extra=None):
    return (
        str(file_name),
        tool_name,
        str(extra) if extra is not None else "",
    )


def _get_cached(key):
    value = _tool_results.get(key, _CACHE_MISS)

    if value is _CACHE_MISS:
        return _CACHE_MISS

    return copy.deepcopy(value)


def _store_cached(key, value):
    _tool_results[key] = copy.deepcopy(value)
    return copy.deepcopy(value)


def _first_value(data, *keys):
    for key in keys:
        value = data.get(key)

        if value is not None and value != "":
            return value

    return None


def _invoice_po_reference(data):
    """Extract the PO reference from the actual invoice data."""

    if not isinstance(data, dict):
        return None

    value = _first_value(
        data,
        "po_reference",
        "po_number",
        "purchase_order",
        "purchase_order_reference",
        "reference_po",
        "purchase_reference",
    )

    if value is None:
        details = data.get("reference_details")

        if isinstance(details, dict):
            reference_type = str(
                details.get("reference_type") or ""
            ).lower()

            if (
                "purchase order" in reference_type
                or reference_type == "po"
            ):
                value = details.get("customer_order")

    return str(value).strip() if value else None


def _normalize_po(value):
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value or "").lower(),
    )


def _merge_saved_procurement_records(reference):
    """Include app-created PO and GRN JSON records in deterministic checks."""
    if not isinstance(reference, dict):
        raise ValueError("Procurement reference data must be a dictionary.")

    merged = copy.deepcopy(reference)
    for key in ("purchase_orders", "goods_receipts"):
        if not isinstance(merged.get(key), list):
            merged[key] = []
        merged[key] = [row for row in merged[key] if isinstance(row, dict)]

    # A saved PO is the current record for its PO number; keep fixtures that
    # are not replaced by a saved record.
    for saved_po in load_saved_purchase_orders():
        wanted = _normalize_po(saved_po.get("po_number"))
        match_index = next(
            (
                index for index, row in enumerate(merged["purchase_orders"])
                if _normalize_po(row.get("po_number")) == wanted
            ),
            None,
        )
        if match_index is None:
            merged["purchase_orders"].append(saved_po)
        else:
            merged["purchase_orders"][match_index] = saved_po

    # Merge GRNs by GRN number so reference data can retain multiple partial
    # receipts for a PO while avoiding duplicate copies of the same GRN.
    for saved_grn in load_saved_goods_receipts():
        grn_number = _normalize_po(saved_grn.get("grn_number"))
        match_index = None
        if grn_number:
            match_index = next(
                (
                    index for index, row in enumerate(merged["goods_receipts"])
                    if _normalize_po(row.get("grn_number")) == grn_number
                ),
                None,
            )
        if match_index is None:
            merged["goods_receipts"].append(saved_grn)
        else:
            merged["goods_receipts"][match_index] = saved_grn

    return merged


def start_run(file_name):
    """Clear this invoice's per-run state and refresh procurement records."""
    global _reference_data_cache
    _reference_data_cache = None
    file_name = str(file_name)

    _review_reasons.pop(file_name, None)
    _dataset_results.pop(file_name, None)

    for key in list(_tool_results):
        if key[0] == file_name:
            _tool_results.pop(key, None)


def review_reason(file_name):
    return _review_reasons.get(str(file_name))


def log_step(step, detail):
    """Write audit events without allowing logging failures to stop tools."""

    global _db_warning_shown

    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "detail": detail,
    }

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    entry,
                    ensure_ascii=False,
                    default=str,
                ) + "\n"
            )

    except Exception as exc:
        print(
            "  [warning] Local audit log failed:",
            str(exc)[:120],
        )

    try:
        if not save_log(step, detail) and not _db_warning_shown:
            print(
                "  [warning] Could not write to the database. "
                "Using local logging only."
            )
            _db_warning_shown = True

    except Exception as exc:
        if not _db_warning_shown:
            print(
                "  [warning] Database logging failed:",
                str(exc)[:120],
            )
            _db_warning_shown = True

    print(
        "  [log] " + step + ": " +
        json.dumps(detail, ensure_ascii=False, default=str)[:160]
    )


def _load_invoice(file_name):
    invoice_dir = INVOICE_DIR.resolve()
    path = (invoice_dir / str(file_name)).resolve()

    if path.parent != invoice_dir or not path.is_file():
        return None

    try:
        data = get_invoice_data(path)

    except Exception as exc:
        log_step(
            "tool:load_invoice:error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )
        return None

    if isinstance(data, list):
        data = data[0] if data else None

    return data


def verify_invoice(file_name):
    """Run independent checks in plain Python."""

    data = _load_invoice(file_name)

    if data is None:
        return ["Invoice could not be read."]

    problems = [
        issue["detail"]
        for issue in check_line_math(data)
    ]

    po_number = _invoice_po_reference(data)

    if not po_number:
        problems.append("No PO number on the invoice.")
        return problems

    po_result = find_po(po_number)

    if po_result.get("status") != "found":
        problems.append(po_result.get("note", "PO not found."))
        return problems

    po = po_result["po"]

    vendor_check = check_vendor(
        data.get("vendor") or data.get("vendor_name"),
        po.get("vendor") or po.get("vendor_name"),
        invoice_vendor_id=data.get("vendor_id"),
        po_vendor_id=po.get("vendor_id"),
    )

    if vendor_check["status"] != "match":
        problems.append(
            "Vendor mismatch: " + vendor_check["note"]
        )

    return problems


def read_invoice(file_name: str) -> dict:
    """Read an invoice. Do not cache failed reads."""

    file_name = str(file_name)

    key = _cache_key("read_invoice", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        data = None
        log_step(
            "tool:read_invoice:error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

    if not isinstance(data, dict):
        result = {
            "error": (
                "Could not read the invoice '" +
                file_name + "'. Try reading it again."
            )
        }

        log_step(
            "tool:read_invoice",
            {
                "file": file_name,
                "result": result,
            },
        )

        # Important: failed results are not cached.
        return result

    log_step(
        "tool:read_invoice",
        {
            "file": file_name,
            "result": data,
        },
    )

    return _store_cached(key, data)


def check_invoice_math(file_name: str) -> dict:
    """Check line arithmetic and invoice totals."""

    file_name = str(file_name)

    key = _cache_key("check_invoice_math", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        data = None
        error_text = str(exc)[:150]

    else:
        error_text = ""

    if data is None:
        result = {
            "math_ok": False,
            "issues": [],
            "error": error_text or "Invoice not available.",
        }

        log_step(
            "tool:check_invoice_math",
            {"file": file_name, "result": result},
        )

        return result

    issues = check_line_math(data)

    result = {
        "math_ok": len(issues) == 0,
        "issues": issues,
    }

    log_step(
        "tool:check_invoice_math",
        {"file": file_name, "result": result},
    )

    return _store_cached(key, result)


def lookup_po(file_name: str, po_number: str) -> dict:
    """
    Verify the supplied PO against the PO actually extracted
    from the specified invoice.
    """

    file_name = str(file_name)

    key = _cache_key(
        "lookup_po",
        file_name,
        po_number,
    )

    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    data = _load_invoice(file_name)

    if not isinstance(data, dict):
        return {
            "status": "cannot_check",
            "po": None,
            "note": "The invoice could not be read. PO lookup was not performed.",
        }

    actual_po = _invoice_po_reference(data)

    if not actual_po:
        return {
            "status": "not_found",
            "po": None,
            "note": "No PO reference was found in the invoice.",
        }

    supplied_po = str(po_number or "").strip()

    mismatch = (
        supplied_po
        and _normalize_po(supplied_po) != _normalize_po(actual_po)
    )

    if mismatch:
        log_step(
            "tool:lookup_po:input_mismatch",
            {
                "file": file_name,
                "supplied_po": supplied_po,
                "actual_invoice_po": actual_po,
            },
        )

    # Always search using the PO extracted from the original invoice.
    result = find_po(actual_po)

    result["invoice_po_reference"] = actual_po

    if mismatch:
        result["supplied_po_number"] = supplied_po
        result["note"] = (
            "The supplied PO did not match the invoice. "
            "Lookup used the actual invoice PO '" +
            actual_po + "'. " +
            result.get("note", "")
        )

    log_step(
        "tool:lookup_po",
        {
            "file": file_name,
            "result": result,
        },
    )

    return _store_cached(key, result)


def check_vendor_matches_po(
    file_name: str,
    po_number: str
) -> dict:
    """Check vendor identity using the actual invoice PO."""

    file_name = str(file_name)

    key = _cache_key(
        "check_vendor_matches_po",
        file_name,
        po_number,
    )

    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

    except Exception as exc:
        return {
            "status": "cannot_check",
            "note": str(exc)[:150],
        }

    if not isinstance(data, dict):
        return {
            "status": "cannot_check",
            "note": "Invoice could not be read.",
        }

    actual_po = _invoice_po_reference(data)

    if not actual_po:
        return {
            "status": "cannot_check",
            "note": "No PO reference was found in the invoice.",
        }

    supplied_po = str(po_number or "").strip()

    if (
        supplied_po
        and _normalize_po(supplied_po) != _normalize_po(actual_po)
    ):
        log_step(
            "tool:check_vendor_matches_po:input_mismatch",
            {
                "file": file_name,
                "supplied_po": supplied_po,
                "actual_invoice_po": actual_po,
            },
        )

    po_result = find_po(actual_po)

    if po_result.get("status") != "found":
        return {
            "status": "cannot_check",
            "note": (
                "The invoice PO '" + actual_po +
                "' could not be found in saved or reference purchase orders."
            ),
        }

    po = po_result["po"]

    result = check_vendor(
        data.get("vendor") or data.get("vendor_name"),
        po.get("vendor") or po.get("vendor_name"),
        invoice_vendor_id=data.get("vendor_id"),
        po_vendor_id=po.get("vendor_id"),
    )

    log_step(
        "tool:check_vendor_matches_po",
        {
            "file": file_name,
            "actual_invoice_po": actual_po,
            "result": result,
        },
    )

    return _store_cached(key, result)


def _make_agent_history_record(data):
    """Convert extracted invoice data into the history shape."""

    if not isinstance(data, dict):
        return None

    document_type = str(
        data.get("document_type") or ""
    ).upper()

    if (
        "CREDIT_NOTE" in document_type
        or data.get("credit_note_number")
    ):
        return None

    invoice_number = _first_value(
        data,
        "invoice_number",
        "invoice_no",
        "invoice_id",
    )

    if not invoice_number:
        return None

    vendor = data.get("vendor")

    if isinstance(vendor, dict):
        vendor_name = _first_value(
            vendor,
            "name",
            "vendor_name",
        )
    else:
        vendor_name = vendor or data.get("vendor_name")

    items = data.get("items") or data.get("line_items") or []
    history_items = []

    for item in items:
        if not isinstance(item, dict):
            continue

        history_items.append({
            "description": _first_value(
                item,
                "description",
                "name",
                "product_name",
            ),
            "quantity_billed": _first_value(
                item,
                "quantity",
                "quantity_billed",
                "qty",
            ),
            "unit_price": _first_value(
                item,
                "unit_price",
                "unit_rate",
                "rate",
                "price",
            ),
        })

    return {
        "invoice_number": invoice_number,
        "vendor_name": vendor_name,
        "po_number": _invoice_po_reference(data),
        "status": "PROCESSED",
        "items": history_items,
    }


def check_dataset_rules(file_name: str) -> dict:
    """Run deterministic dataset checks once per invoice."""

    global _reference_data_cache

    file_name = str(file_name)

    key = _cache_key("check_dataset_rules", file_name)
    cached = _get_cached(key)

    if cached is not _CACHE_MISS:
        return cached

    try:
        data = _load_invoice(file_name)

        if data is None:
            result = {
                "case_id": file_name,
                "recommendation": "NEEDS_HUMAN_REVIEW",
                "findings": ["INCOMPLETE_EXTRACTION"],
            }

        else:
            if _reference_data_cache is None:
                _reference_data_cache = _merge_saved_procurement_records(
                    load_reference_data()
                )

            history = [
                dict(row)
                for row in _reference_data_cache.get(
                    "prior_invoices", []
                )
                if "duplicate test case 07"
                not in str(row.get("note", "")).lower()
            ]

            history.extend(
                record
                for previous_file, record in _dataset_history.items()
                if previous_file != file_name
            )

            invoice_record = {
                "case_id": file_name,
                "document": data,
            }

            result = evaluate_invoice(
                invoice_record,
                reference_data=_reference_data_cache,
                history=history,
            )

            history_record = _make_agent_history_record(data)

            if history_record:
                _dataset_history[file_name] = history_record

    except Exception as exc:
        result = {
            "case_id": file_name,
            "recommendation": "NEEDS_HUMAN_REVIEW",
            "findings": ["INCOMPLETE_EXTRACTION"],
            "error": str(exc)[:150],
        }

    if not isinstance(result, dict):
        result = {
            "case_id": file_name,
            "recommendation": "NEEDS_HUMAN_REVIEW",
            "findings": ["INCOMPLETE_EXTRACTION"],
            "error": "Dataset rules returned an invalid result.",
        }

    _dataset_results[file_name] = copy.deepcopy(result)

    log_step(
        "tool:check_dataset_rules",
        {
            "file": file_name,
            "result": result,
        },
    )

    return _store_cached(key, result)


def get_dataset_result(file_name):
    """Return a copy of the deterministic result."""

    result = _dataset_results.get(str(file_name))

    return copy.deepcopy(result) if result is not None else None


def send_to_human_review(
    file_name: str,
    reason: str
) -> dict:
    """Record one human-review routing event per invoice per run."""

    file_name = str(file_name)

    if file_name in _review_reasons:
        log_step(
            "tool:send_to_human_review:duplicate_ignored",
            {"file": file_name},
        )

        return {
            "queued": True,
            "note": (
                "Already sent to human review. "
                "Nothing was approved or paid."
            ),
        }

    _review_reasons[file_name] = reason

    log_step(
        "tool:send_to_human_review",
        {
            "file": file_name,
            "reason": reason,
        },
    )

    return {
        "queued": True,
        "note": (
            "A human reviewer will take it from here. "
            "Nothing was approved or paid."
        ),
    }
