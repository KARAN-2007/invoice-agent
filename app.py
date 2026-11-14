
import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import run_agent
from auth import authenticate
from db_reports import latest_log_id, log_since, recent_log
from db_store import (
    decide_request,
    list_approval_requests,
    save_log,
    save_approval_request,
    has_queued_approval_request,
)
from extract_invoice import SUPPORTED_TYPES
from theme import inject_theme, LOGIN_HERO_HTML, LOGIN_FOOTER_HTML
from reason_labels import render_reasons, short_reasons


INVOICE_DIR = Path(__file__).parent / "invoices"
EXTRACTED_DIR = Path(__file__).parent / "data" / "invoices"

ROLE_PAGES = {
    "vendor": ["Upload invoice"],
    "approver": ["Approvals"],
    "admin": ["Audit log"],
}

st.set_page_config(
    page_title="Invoice agent",
    page_icon="🧾",
    layout="wide",
)

# All styling lives in theme.py
inject_theme()


# ---------- GENERAL HELPERS ----------

def require_role(user, allowed):
    if user["role"] not in allowed:
        save_log(
            "auth:access_denied",
            {"user": user["username"], "role": user["role"]}
        )
        st.error("Your role is not allowed to open this page.")
        st.stop()


def safe_name(original):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", original)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return "upload_" + timestamp + "_" + cleaned


def money(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-" if value is None else str(value)


def load_extracted(file_name):
    path = EXTRACTED_DIR / (file_name + ".json")

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None

    # Support the versioned extraction cache written by run_invoices.py.
    if (
        isinstance(data, dict)
        and "_cache_version" in data
        and "data" in data
    ):
        data = data["data"]

    # Support files containing multiple extracted invoices.
    if isinstance(data, list):
        data = data[0] if data else None

    return data if isinstance(data, dict) else None


def show_invoice(file_name):
    data = load_extracted(file_name)

    if data is None:
        st.caption("No saved reading of this invoice.")
        return

    lines = [
        "**Vendor:** " + str(data.get("vendor") or "-"),
        "**Invoice number:** " + str(data.get("invoice_number") or "-"),
        "**Date:** " + str(data.get("date") or "-"),
        "**PO number:** " + str(data.get("po_number") or "-"),
    ]

    if data.get("tax") is not None:
        lines.append("**Tax:** " + money(data.get("tax")))

    lines.append("**Total:** " + money(data.get("total")))

    st.markdown("  \n".join(lines))

    items = data.get("items") or []

    if items:
        st.dataframe([
            {
                "item": item.get("name"),
                "qty": item.get("quantity"),
                "unit price": money(item.get("unit_price")),
                "line total": money(item.get("line_total")),
            }
            for item in items
        ], use_container_width=True, hide_index=True)


def show_original(file_name, key):
    path = INVOICE_DIR / file_name

    if not path.exists():
        st.caption("Original invoice file is unavailable.")
        return

    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        st.image(str(path), caption="Original invoice")

    st.download_button(
        "Download original file",
        data=path.read_bytes(),
        file_name=path.name,
        key="download_" + str(key)
    )


def event_detail(event):
    detail = event.get("detail", {})
    return detail if isinstance(detail, dict) else {}


def event_result(event):
    result = event_detail(event).get("result", {})
    return result if isinstance(result, dict) else {}


def first_event(steps, step_name):
    for event in steps:
        if event.get("step") == step_name:
            return event
    return None


def describe_step(step, detail):
    detail = detail if isinstance(detail, dict) else {}

    result = detail.get("result")
    result = result if isinstance(result, dict) else {}

    if step == "tool:read_invoice":
        if "error" in result:
            return "Could not read the invoice."

        return (
            "Read invoice "
            + str(result.get("invoice_number") or "?")
            + " from "
            + str(result.get("vendor") or "?")
        )

    if step == "tool:check_invoice_math":
        if result.get("math_ok") is True:
            return "The invoice arithmetic is correct."

        issues = result.get("issues") or []

        issue_text = " ".join(
            str(item.get("detail", ""))
            for item in issues if isinstance(item, dict)
        )

        return (
            "Arithmetic check needs attention. "
            + (issue_text or str(result.get("error", "No detail recorded.")))
        )

    if step == "tool:lookup_po":
        status = result.get("status")
        po = result.get("po") or {}

        if status == "found":
            return (
                "Found purchase order "
                + str(po.get("po_number") or "")
                + "."
            )

        if status in ("guessed", "ambiguous"):
            return str(result.get("note", "The PO number could not be confirmed."))

        return str(result.get("note", "No matching purchase order was found."))

    if step == "tool:check_vendor_matches_po":
        status = result.get("status")

        if status == "match":
            return "The vendor matches the purchase order."

        if status == "mismatch":
            return "Vendor mismatch: " + str(result.get("note", ""))

        return "The vendor check was skipped or could not be completed."

    if step == "tool:send_to_human_review":
        return "Sent to human review: " + str(detail.get("reason", ""))

    if step == "agent:error":
        return (
            "AI service attempt "
            + str(detail.get("attempt", "?"))
            + " failed; a retry was recorded."
        )

    if step == "agent:retry":
        return (
            "Agent scheduled a retry after "
            + str(detail.get("waiting_seconds", "?"))
            + " seconds."
        )

    if step == "agent:safety_override":
        return (
            "Independent safety check found: "
            + " | ".join(str(problem) for problem in detail.get("problems", []))
        )

    if step == "agent:gave_up":
        return "AI service was unavailable; invoice was routed to human review."

    if step == "agent:recorded":
        return (
            "Saved recommendation "
            + str(detail.get("recommendation"))
            + " as request #"
            + str(detail.get("approval_request_id"))
            + "."
        )

    if step == "agent:finish":
        return (
            "Gemini run completed using model "
            + str(detail.get("model", "unknown"))
            + "."
        )

    if step == "agent:start":
        return "Agent started processing the invoice."

    return None


# ---------- VERIFICATION REPORT ----------

def make_check(name, status, message, detail=""):
    return {
        "name": name,
        "status": status,
        "message": message,
        "detail": detail,
    }


def build_verification_checks(file_name, steps, result, request_record=None):
    checks = []
    extracted = load_extracted(file_name)

    # Extraction
    read_event = first_event(steps, "tool:read_invoice")

    if read_event:
        read_result = event_result(read_event)

        if "error" in read_result:
            checks.append(make_check(
                "Invoice extraction",
                "review",
                "The invoice could not be read successfully.",
                str(read_result.get("error", ""))
            ))
        else:
            vendor = (
                read_result.get("vendor")
                or (extracted or {}).get("vendor")
                or "Not recorded"
            )

            invoice_number = (
                read_result.get("invoice_number")
                or (extracted or {}).get("invoice_number")
                or "Not recorded"
            )

            checks.append(make_check(
                "Invoice extraction",
                "passed",
                "Invoice data was read by the extraction tool.",
                "Vendor: " + str(vendor) + " · Invoice: " + str(invoice_number)
            ))
    else:
        checks.append(make_check(
            "Invoice extraction",
            "not_recorded",
            "No invoice-reading event was found in the retrieved run logs."
        ))

    # Arithmetic
    math_event = first_event(steps, "tool:check_invoice_math")

    if math_event:
        math_result = event_result(math_event)

        if math_result.get("math_ok") is True:
            checks.append(make_check(
                "Invoice arithmetic",
                "passed",
                "The arithmetic check returned math_ok = true."
            ))

        elif math_result.get("math_ok") is False:
            issues = math_result.get("issues") or []

            issue_text = " | ".join(
                str(item.get("detail", item))
                if isinstance(item, dict)
                else str(item)
                for item in issues
            )

            checks.append(make_check(
                "Invoice arithmetic",
                "review",
                "The arithmetic check identified an issue.",
                issue_text or str(math_result.get("error", "See raw event details."))
            ))

        else:
            checks.append(make_check(
                "Invoice arithmetic",
                "not_recorded",
                "The arithmetic tool ran, but returned no clear math_ok value.",
                str(math_result.get("error", ""))
            ))
    else:
        checks.append(make_check(
            "Invoice arithmetic",
            "not_recorded",
            "No arithmetic-check event was found in the retrieved run logs."
        ))

    # Purchase order
    po_event = first_event(steps, "tool:lookup_po")
    po_number = (extracted or {}).get("po_number") if extracted else None

    if po_event:
        po_result = event_result(po_event)
        po_status = po_result.get("status")
        po = po_result.get("po") or {}

        if po_status == "found":
            checks.append(make_check(
                "Purchase order lookup",
                "passed",
                "A matching purchase order was found.",
                "PO: " + str(po.get("po_number") or po_number or "Not recorded")
            ))

        elif po_status in ("guessed", "ambiguous"):
            checks.append(make_check(
                "Purchase order lookup",
                "review",
                "The purchase order match was not definitive.",
                str(po_result.get("note", "Manual confirmation is required."))
            ))

        else:
            checks.append(make_check(
                "Purchase order lookup",
                "review",
                "The purchase order could not be confirmed.",
                str(po_result.get("note", "No matching purchase order was found."))
            ))

    elif extracted is not None and not po_number:
        checks.append(make_check(
            "Purchase order lookup",
            "review",
            "No PO number was present in the extracted invoice data.",
            "The PO comparison could not be performed from the extracted PO number."
        ))

    elif po_number:
        checks.append(make_check(
            "Purchase order lookup",
            "not_recorded",
            "A PO number was extracted, but no lookup event was found.",
            "Extracted PO: " + str(po_number)
        ))

    else:
        checks.append(make_check(
            "Purchase order lookup",
            "not_recorded",
            "There is not enough recorded data to determine whether PO lookup was performed."
        ))

    # Vendor matching
    vendor_event = first_event(steps, "tool:check_vendor_matches_po")

    if vendor_event:
        vendor_result = event_result(vendor_event)
        vendor_status = vendor_result.get("status")

        if vendor_status == "match":
            checks.append(make_check(
                "Vendor verification",
                "passed",
                "The vendor matches the purchase order."
            ))

        elif vendor_status == "mismatch":
            checks.append(make_check(
                "Vendor verification",
                "review",
                "The vendor does not match the purchase order.",
                str(vendor_result.get("note", ""))
            ))

        else:
            checks.append(make_check(
                "Vendor verification",
                "not_recorded",
                "The vendor verification tool did not return a definitive match status.",
                str(vendor_result.get("note", ""))
            ))

    elif po_event and event_result(po_event).get("status") not in ("found", "guessed"):
        checks.append(make_check(
            "Vendor verification",
            "not_run",
            "Vendor matching was not run because a usable PO was not found."
        ))

    else:
        checks.append(make_check(
            "Vendor verification",
            "not_recorded",
            "No vendor-to-PO verification event was found."
        ))

    # Independent Python safety check
    recommendation = result.get("recommendation")

    recorded_reason = ""

    if isinstance(request_record, dict):
        recorded_reason = str(request_record.get("review_reason") or "")

    if recommendation == "READY_FOR_APPROVAL":
        checks.append(make_check(
            "Independent Python safety check",
            "passed",
            "The final programmatic checks produced no blocking review reason."
        ))

    elif recommendation == "NEEDS_HUMAN_REVIEW":
        checks.append(make_check(
            "Independent Python safety check",
            "review",
            "The final programmatic decision requires human review.",
            recorded_reason or "The recommendation was NEEDS_HUMAN_REVIEW."
        ))

    else:
        checks.append(make_check(
            "Independent Python safety check",
            "not_recorded",
            "No final recommendation was available to display."
        ))

    return checks


def render_check_card(check):
    with st.container(border=True):
        left, right = st.columns([3, 1])

        with left:
            st.markdown("**" + check["name"] + "**")
            st.write(check["message"])

            if check.get("detail"):
                st.caption(check["detail"])

        with right:
            status = check["status"]

            if status == "passed":
                st.success("Passed")
            elif status == "review":
                st.warning("Review")
            elif status == "not_run":
                st.info("Not run")
            else:
                st.caption("Not recorded")


def render_verification_report(file_name, result, steps, request_record=None):
    recommendation = result.get("recommendation")

    checks = build_verification_checks(
        file_name, steps, result, request_record
    )

    passed_count = sum(1 for check in checks if check["status"] == "passed")
    review_count = sum(1 for check in checks if check["status"] == "review")
    unrecorded_count = sum(
        1 for check in checks
        if check["status"] in ("not_recorded", "not_run")
    )

    finish_event = first_event(steps, "agent:finish")
    model_name = (
        event_detail(finish_event).get("model")
        if finish_event else None
    )

    st.divider()

    st.markdown(
        "<div class='page-eyebrow'>EVIDENCE-BASED VERIFICATION</div>",
        unsafe_allow_html=True
    )

    st.title("AI verification report")

    top_left, top_right = st.columns([3, 1])

    with top_left:
        if recommendation == "READY_FOR_APPROVAL":
            st.success(
                "AI recommendation: Ready for human approval. "
                "This is not an approval or payment decision."
            )
        elif recommendation == "NEEDS_HUMAN_REVIEW":
            st.warning(
                "AI recommendation: Human review required. "
                "Do not treat this invoice as cleared."
            )
        else:
            st.info("A final recommendation was not recorded.")

        if result.get("summary"):
            st.markdown("**Agent summary**")
            st.write(result["summary"])

    with top_right:
        st.markdown("**Run information**")
        st.caption("Invoice: " + file_name)

        if result.get("approval_saved"):
            st.success(
                "Approval request saved to PostgreSQL. Request ID: "
                + str(result.get("approval_request_id"))
            )
        elif result.get("recovery_queued"):
            st.warning(
                "Approval request saved locally. It will synchronize "
                "when PostgreSQL becomes available."
            )
        else:
            st.error(
                "Approval request could not be saved. "
                "Administrator recovery is required."
            )

        if model_name:
            st.caption("Gemini model: " + str(model_name))

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric("Checks passed", passed_count)
    metric2.metric("Need review", review_count)
    metric3.metric("Not recorded / run", unrecorded_count)

    st.markdown("### Verification breakdown")

    for start in range(0, len(checks), 2):
        col1, col2 = st.columns(2, gap="medium")

        with col1:
            render_check_card(checks[start])

        if start + 1 < len(checks):
            with col2:
                render_check_card(checks[start + 1])

    st.markdown("### Invoice evidence")

    invoice_tab, original_tab = st.tabs([
        "Extracted invoice data",
        "Original document"
    ])

    with invoice_tab:
        show_invoice(file_name)

    with original_tab:
        show_original(
            file_name,
            str(result.get("approval_request_id") or "report")
        )

    st.markdown("### Agent execution trace")

    if not steps:
        st.info("No run events were returned by the audit log query.")
    else:
        for event in steps:
            description = describe_step(
                event.get("step", ""),
                event.get("detail", {})
            )

            if description:
                st.write("• " + description)

    with st.expander("Inspect raw run events"):
        for event in steps:
            st.markdown(
                "**#"
                + str(event.get("id", "?"))
                + " · "
                + str(event.get("step", "unknown"))
                + "**"
            )
            st.json(event.get("detail", {}))


# ---------- AUDIT HELPERS ----------

def audit_category(step):
    name = str(step).lower()

    if name.startswith("tool:"):
        return "AI tools"
    if name.startswith("agent:"):
        return "AI agent"
    if "approval" in name or "human" in name or "decision" in name:
        return "Human decisions"
    if "auth" in name:
        return "Access"
    if "vendor" in name:
        return "Vendor activity"

    return "System"


def create_audit_csv(rows):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["ID", "Time", "Category", "Event", "Details"])

    for row in rows:
        writer.writerow([
            row.get("id", ""),
            str(row.get("time", "")),
            audit_category(row.get("step", "")),
            row.get("step", ""),
            json.dumps(
                row.get("detail", {}),
                ensure_ascii=False,
                default=str
            )
        ])

    return output.getvalue().encode("utf-8-sig")


# ---------- LOGIN PAGE ----------

def login_screen():
    left, right = st.columns([1.2, 0.85], gap="large")

    with left:
        st.html(LOGIN_HERO_HTML)

    with right:
        st.markdown(
            "<div class='login-label'>Account access</div>",
            unsafe_allow_html=True
        )

        st.markdown("## Welcome back")
        st.caption("Sign in to access your invoice workspace.")

        with st.form("login_form"):
            username = st.text_input(
                "Username",
                placeholder="Enter your username"
            )

            password = st.text_input(
                "Password",
                type="password",
                placeholder="Enter your password"
            )

            submitted = st.form_submit_button(
                "Sign in →",
                type="primary",
                use_container_width=True
            )

        # The result is shown right under the form, so it is always visible.
        if submitted:
            try:
                user = authenticate(username, password)

            except Exception as e:
                st.error(
                    "Could not reach the database. Check that PostgreSQL "
                    "is running and that .env is correct."
                )
                st.caption(str(e)[:200])
                return

            if user:
                st.session_state["user"] = user
                st.rerun()
            else:
                st.error(
                    "Wrong username or password. Passwords are case-sensitive, "
                    "so check Caps Lock."
                )

        st.html(LOGIN_FOOTER_HTML)
        st.caption("Demo accounts are listed in the README. Passwords are case-sensitive.")


# ---------- VENDOR UPLOAD PAGE ----------

def upload_page(user):
    require_role(user, ["vendor"])

    st.markdown(
        "<div class='page-eyebrow'>VENDOR WORKSPACE</div>",
        unsafe_allow_html=True
    )

    st.title("Submit an invoice")

    st.caption(
        "Upload your invoice for automated verification. "
        "The system checks the information and sends it for human approval."
    )

    file_types = [t.lstrip(".") for t in sorted(SUPPORTED_TYPES)]

    left, right = st.columns([1.5, 0.8], gap="large")

    uploaded = None
    process_clicked = False

    with left:
        with st.container(border=True):
            st.subheader("01 · Upload your document")

            st.write(
                "Choose the invoice file you want the AI agent to verify."
            )

            uploaded = st.file_uploader(
                "Select invoice",
                type=file_types,
                help="Supported formats: " + ", ".join(file_types)
            )

            if uploaded is not None:
                st.divider()
                st.markdown("**Selected file**")

                file_col, size_col = st.columns(2)

                with file_col:
                    st.write("📄 " + uploaded.name)

                with size_col:
                    st.write(f"{uploaded.size / 1024:,.1f} KB")

                process_clicked = st.button(
                    "Run AI verification →",
                    type="primary",
                    use_container_width=True,
                    key="run_invoice_agent"
                )
            else:
                st.info(
                    "Upload a PDF or image to begin the verification process."
                )

    with right:
        with st.container(border=True):
            st.markdown("### How it works")

            st.markdown(
                '<div class="upload-step"><b>01 · Upload</b><br>'
                '<small>Submit your invoice document.</small></div>',
                unsafe_allow_html=True
            )

            st.write("")

            st.markdown(
                '<div class="upload-step"><b>02 · Verify</b><br>'
                '<small>AI reads the invoice and checks company records.</small></div>',
                unsafe_allow_html=True
            )

            st.write("")

            st.markdown(
                '<div class="upload-step"><b>03 · Review</b><br>'
                '<small>A human makes the final approval decision.</small></div>',
                unsafe_allow_html=True
            )

            st.divider()

            st.caption(
                "AI recommendations do not automatically approve payments."
            )

    if uploaded is None or not process_clicked:
        return

    file_name = safe_name(uploaded.name)
    file_path = INVOICE_DIR / file_name

    # 1. Save the uploaded document safely.
    try:
        INVOICE_DIR.mkdir(parents=True, exist_ok=True)

        with file_path.open("xb") as file_handle:
            file_handle.write(uploaded.getvalue())

    except OSError as exc:
        st.error("Could not save the uploaded invoice.")
        st.caption(str(exc)[:200])
        return

    # 2. Record the upload. save_log returns False on database failure.
    try:
        audit_saved = save_log(
            "vendor:upload",
            {"user": user["username"], "file": file_name}
        )

        if audit_saved is False:
            st.warning(
                "The invoice was saved, but its upload audit entry "
                "could not be saved."
            )

    except Exception as exc:
        st.warning(
            "The invoice was saved, but its upload audit entry failed."
        )
        st.caption(str(exc)[:150])

    # 3. Retrieve the starting audit log ID if available.
    try:
        last_id = latest_log_id()

    except Exception:
        last_id = None

        st.warning(
            "Audit tracing is temporarily unavailable. "
            "Invoice processing will continue."
        )

    # 4. Run the AI agent safely.
    try:
        with st.spinner(
            "The AI agent is reading and checking your invoice..."
        ):
            result = run_agent(file_name)

        if not isinstance(result, dict):
            raise RuntimeError("The agent returned an invalid result.")

    except Exception as exc:
        failure_summary = (
            "Automated processing stopped unexpectedly. "
            "Human verification is required."
        )

        failure_reason = (
            "INCOMPLETE_EXTRACTION | "
            "Automated processing failed unexpectedly."
        )

        st.error(
            "Invoice processing encountered an unexpected error. "
            "The original document has been saved."
        )
        st.caption(str(exc)[:200])

        # Attempt to preserve a human-review request.
        try:
            request_id = save_approval_request(
                file_name,
                "NEEDS_HUMAN_REVIEW",
                failure_summary,
                failure_reason
            )

            if request_id is not None:
                st.warning(
                    "Fallback human-review request saved as #"
                    + str(request_id)
                )

            elif has_queued_approval_request(file_name):
                st.warning(
                    "Fallback human-review request saved locally. "
                    "It will synchronize when PostgreSQL becomes available."
                )

            else:
                st.error(
                    "The fallback review request could not be saved "
                    "to PostgreSQL or local recovery storage. "
                    "Administrator intervention is required."
                )

        except Exception as db_error:
            st.error(
                "Database recovery also failed. "
                "The original invoice remains saved at: "
                + file_name
            )
            st.caption(str(db_error)[:150])

        return

    # 5. Retrieve execution logs if the database is available.
    try:
        steps = log_since(last_id) if last_id is not None else []

    except Exception:
        steps = []

        st.warning(
            "The invoice was processed, but its execution trace "
            "could not be retrieved."
        )

    # 6. Retrieve the approval record only if it was saved to PostgreSQL.
    request_id = result.get("approval_request_id")
    request_record = None

    if request_id is not None:
        try:
            request_record = next(
                (
                    row for row in list_approval_requests()
                    if str(row.get("id")) == str(request_id)
                ),
                None
            )

        except Exception:
            st.warning(
                "The approval record was saved, but its details "
                "could not be retrieved."
            )

    # 7. Display the verification report.
    render_verification_report(
        file_name,
        result,
        steps,
        request_record
    )


# ---------- APPROVALS PAGE ----------

def approvals_page(user):
    require_role(user, ["approver"])

    st.markdown(
        "<div class='page-eyebrow'>HUMAN REVIEW WORKSPACE</div>",
        unsafe_allow_html=True
    )

    st.title("Invoice approval center")

    st.caption(
        "Review the agent's findings, inspect the source invoice, "
        "and make the final decision."
    )

    if "message" in st.session_state:
        st.success(st.session_state.pop("message"))

    try:
        pending = list_approval_requests("PENDING")

        decided = [
            row for row in list_approval_requests()
            if row["status"] != "PENDING"
        ]

    except Exception as e:
        st.error(
            "Could not reach the database. Check that PostgreSQL "
            "is running and that .env is correct."
        )
        st.caption(str(e)[:200])
        return

    approved_count = sum(
        1 for row in decided if row["status"] == "APPROVED"
    )

    rejected_count = sum(
        1 for row in decided if row["status"] == "REJECTED"
    )

    metric1, metric2, metric3 = st.columns(3)

    metric1.metric("Awaiting review", len(pending))
    metric2.metric("Approved", approved_count)
    metric3.metric("Rejected", rejected_count)

    st.info(
        "Human control is active: the AI recommendation is advisory. "
        "Only an approver can approve or reject an invoice."
    )

    queue_tab, history_tab = st.tabs([
        "Pending review (" + str(len(pending)) + ")",
        "Decision history (" + str(len(decided)) + ")"
    ])

    with queue_tab:
        st.subheader("Invoices requiring a decision")

        if not pending:
            st.success(
                "You're all caught up. There are no invoices waiting for review."
            )

            st.caption(
                "New vendor submissions will appear here after the AI agent "
                "finishes checking them."
            )

        for req in pending:
            request_id = str(req["id"])
            file_name = req["file_name"]

            needs_review = (
                req["recommendation"] == "NEEDS_HUMAN_REVIEW"
            )

            with st.container(border=True):
                header_left, header_right = st.columns([3, 1])

                with header_left:
                    st.markdown("### Review request #" + request_id)
                    st.caption(file_name)

                with header_right:
                    if needs_review:
                        st.warning("Human review needed")
                    else:
                        st.success("No issue flagged")

                st.divider()

                detail_left, detail_right = st.columns(
                    [1, 1.15],
                    gap="large"
                )

                with detail_left:
                    st.markdown("**AI assessment**")

                    review_reason_text = req.get("review_reason") or ""

                    if review_reason_text:
                        # Show informational labels as well as blocking reasons.
                        render_reasons(review_reason_text)

                    elif needs_review:
                        render_reasons("No specific reason was recorded.")

                    else:
                        st.success(
                            "The agent found no blocking issues in its checks."
                        )

                    if req["summary"]:
                        st.markdown("**Agent summary**")
                        st.write(req["summary"])
                    else:
                        st.caption("The agent did not provide a summary.")

                    st.caption(
                        "Recommendation: " + str(req["recommendation"])
                    )

                with detail_right:
                    st.markdown("**Invoice details extracted by AI**")
                    show_invoice(file_name)

                    with st.expander("View original invoice and download"):
                        show_original(file_name, request_id)

                st.divider()
                st.markdown("**Your decision**")

                note = st.text_input(
                    "Reviewer note (optional)",
                    key="note_" + request_id,
                    placeholder=(
                        "Explain your decision or note any follow-up needed..."
                    )
                )

                action_left, action_right, _ = st.columns([1, 1, 2])

                decision = None

                if action_left.button(
                    "Approve invoice",
                    type="primary",
                    key="approve_" + request_id,
                    use_container_width=True
                ):
                    decision = "APPROVED"

                if action_right.button(
                    "Reject invoice",
                    type="secondary",
                    key="reject_" + request_id,
                    use_container_width=True
                ):
                    decision = "REJECTED"

                if decision:
                    try:
                        done = decide_request(
                            req["id"],
                            decision,
                            user["username"],
                            note
                        )

                        if done:
                            st.session_state["message"] = (
                                decision.capitalize() + ": " + str(done)
                            )
                        else:
                            st.session_state["message"] = (
                                "That request was already decided."
                            )

                    except Exception as exc:
                        st.session_state["message"] = (
                            "Could not save the decision. "
                            "Please check the database connection."
                        )
                        st.error(str(exc)[:200])

                    st.rerun()

    with history_tab:
        st.subheader("Previously decided invoices")

        if decided:
            history_rows = [
                {
                    "Request": row["id"],
                    "Invoice": row["file_name"],
                    "AI recommendation": row["recommendation"],
                    "Flagged for": short_reasons(row["review_reason"]),
                    "Human decision": row["status"],
                    "Decided by": row["decided_by"],
                    "Decision time": row["decided_at"],
                    "Reviewer note": row["decision_note"],
                }
                for row in decided
            ]

            st.dataframe(
                history_rows,
                use_container_width=True,
                hide_index=True
            )

        else:
            st.caption("No approval decisions have been recorded yet.")


# ---------- AUDIT PAGE ----------

def audit_page(user):
    require_role(user, ["admin"])

    st.markdown(
        "<div class='page-eyebrow'>ADMINISTRATION AND CONTROL</div>",
        unsafe_allow_html=True
    )

    st.title("Audit & activity center")

    st.caption(
        "A searchable record of system activity, AI operations, "
        "vendor uploads and human decisions."
    )

    limit = st.slider(
        "Number of recent events to retrieve",
        min_value=20,
        max_value=500,
        value=100,
        step=20
    )

    try:
        all_rows = recent_log(limit)

    except Exception as e:
        st.error("Could not reach the database.")
        st.caption(str(e)[:200])
        return

    for row in all_rows:
        row["_category"] = audit_category(row.get("step", ""))

    ai_count = sum(
        1 for row in all_rows
        if row["_category"] in ("AI tools", "AI agent")
    )

    access_count = sum(
        1 for row in all_rows
        if row["_category"] == "Access"
    )

    decision_count = sum(
        1 for row in all_rows
        if row["_category"] == "Human decisions"
    )

    st.markdown("### Activity overview")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Events retrieved", len(all_rows))
    c2.metric("AI activities", ai_count)
    c3.metric("Human decisions", decision_count)
    c4.metric("Access events", access_count)

    st.divider()
    st.markdown("### Explore audit records")

    filter_left, filter_right = st.columns([1, 1.4])

    categories = [
        "All categories",
        "AI tools",
        "AI agent",
        "Human decisions",
        "Access",
        "Vendor activity",
        "System"
    ]

    with filter_left:
        selected_category = st.selectbox(
            "Filter by event category",
            categories
        )

    with filter_right:
        search_query = st.text_input(
            "Search audit records",
            placeholder="Search by event, user, invoice, or detail..."
        ).strip().lower()

    filtered_rows = list(all_rows)

    if selected_category != "All categories":
        filtered_rows = [
            row for row in filtered_rows
            if row["_category"] == selected_category
        ]

    if search_query:
        filtered_rows = [
            row for row in filtered_rows
            if search_query in " ".join([
                str(row.get("id", "")),
                str(row.get("time", "")),
                str(row.get("step", "")),
                str(row.get("detail", {}))
            ]).lower()
        ]

    st.caption(
        str(len(filtered_rows))
        + " matching events out of "
        + str(len(all_rows))
        + " retrieved events."
    )

    if filtered_rows:
        st.download_button(
            "Download filtered audit report (CSV)",
            data=create_audit_csv(filtered_rows),
            file_name="invoice_audit_report.csv",
            mime="text/csv",
            key="download_audit_csv"
        )

    st.divider()
    st.markdown("### Recent activity")

    if not filtered_rows:
        st.info(
            "No events match your current filters. Try a different search."
        )

    else:
        display_rows = [
            {
                "ID": row.get("id"),
                "Time": str(row.get("time", "")),
                "Category": row["_category"],
                "Event": row.get("step", ""),
                "Details": json.dumps(
                    row.get("detail", {}),
                    ensure_ascii=False,
                    default=str
                )[:350]
            }
            for row in filtered_rows
        ]

        st.dataframe(
            display_rows,
            use_container_width=True,
            hide_index=True,
            height=400
        )

        st.divider()
        st.markdown("### Event inspector")

        event_options = {}

        for row in filtered_rows:
            label = (
                "#"
                + str(row.get("id"))
                + " · "
                + str(row.get("step"))
                + " · "
                + str(row.get("time"))
            )
            event_options[label] = row

        selected_event = st.selectbox(
            "Select an event to inspect",
            list(event_options.keys())
        )

        event = event_options[selected_event]

        with st.container(border=True):
            st.markdown("**Event #" + str(event.get("id")) + "**")

            event_col1, event_col2 = st.columns(2)

            with event_col1:
                st.markdown("**Event type**")
                st.write(event.get("step", "Unknown"))

            with event_col2:
                st.markdown("**Category**")
                st.write(event.get("_category", "System"))

            st.markdown("**Timestamp**")
            st.write(str(event.get("time", "Unknown")))

            st.divider()

            st.markdown("**Recorded event details**")
            st.json(event.get("detail", {}))

    with st.expander("About the audit system"):
        st.markdown(
            "The audit trail provides a record of actions taken by the "
            "application and its AI agent. AI tool calls, invoice-related "
            "activity, human decisions and authentication events can be "
            "inspected. Records are retrieved from the existing audit database."
        )


# ---------- MAIN ----------

user = st.session_state.get("user")

if user is None:
    login_screen()
    st.stop()


st.sidebar.markdown("## 🧾 Invoice agent")
st.sidebar.caption("Smart invoice management")
st.sidebar.divider()
st.sidebar.markdown("**Current workspace**")

page = ROLE_PAGES[user["role"]][0]

sidebar_page_html = (
    '<div style="background:rgba(255,255,255,0.10);border-left:4px solid #8F95FF;'
    'padding:13px;border-radius:8px;color:#FFFFFF;font-weight:600;">'
    + page + '</div>'
)

st.sidebar.markdown(sidebar_page_html, unsafe_allow_html=True)
st.sidebar.write("")
st.sidebar.markdown("**Your account**")
st.sidebar.markdown(user["full_name"])
st.sidebar.caption(user["role"].capitalize())
st.sidebar.divider()

if st.sidebar.button("Log out", use_container_width=True):
    save_log("auth:logout", {"user": user["username"]})
    st.session_state.pop("user", None)
    st.rerun()


if page == "Upload invoice":
    upload_page(user)

elif page == "Approvals":
    approvals_page(user)

elif page == "Audit log":
    audit_page(user)
