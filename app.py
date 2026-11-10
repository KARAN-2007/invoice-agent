import json
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import run_agent
from auth import authenticate
from db_reports import latest_log_id, log_since, recent_log
from db_store import decide_request, list_approval_requests, save_log
from extract_invoice import SUPPORTED_TYPES

INVOICE_DIR = Path(__file__).parent / "invoices"
EXTRACTED_DIR = Path(__file__).parent / "extracted"

# Which pages each role may open. Every page also checks the role again.
ROLE_PAGES = {
    "vendor": ["Upload invoice"],
    "approver": ["Approvals"],
    "admin": ["Audit log"],
}

st.set_page_config(page_title="Invoice agent", page_icon="🧾", layout="wide")


# ---------- helpers ----------

def require_role(user, allowed):
    """Stop the page if the logged-in user's role is not allowed to see it."""
    if user["role"] not in allowed:
        save_log("auth:access_denied", {"user": user["username"], "role": user["role"]})
        st.error("Your role is not allowed to open this page.")
        st.stop()


def safe_name(original):
    """A unique, safe file name, so a new upload never reuses an old saved reading."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", original)
    return "upload_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + cleaned


def money(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-" if value is None else str(value)


def load_extracted(file_name):
    """The invoice data the AI read from this file, or None."""
    path = EXTRACTED_DIR / (file_name + ".json")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def show_invoice(file_name):
    """Show the invoice data the AI read, as a small card and an items table."""
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
    if data.get("tax"):
        lines.append("**Tax:** " + money(data.get("tax")))
    lines.append("**Total:** " + money(data.get("total")))
    st.markdown("  \n".join(lines))

    items = data.get("items") or []
    if items:
        st.dataframe([
            {
                "item": i.get("name"),
                "qty": i.get("quantity"),
                "unit price": money(i.get("unit_price")),
                "line total": money(i.get("line_total")),
            }
            for i in items
        ])


def show_original(file_name, key):
    """Show the original image, and offer the original file for download."""
    path = INVOICE_DIR / file_name
    if not path.exists():
        return
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        st.image(str(path), caption="Original invoice")
    st.download_button("Download original file", data=path.read_bytes(), file_name=path.name, key="download_" + key)


def describe_step(step, detail):
    """Turn one raw log entry into (icon, sentence), or None to hide it."""
    detail = detail if isinstance(detail, dict) else {}
    result = detail.get("result")
    result = result if isinstance(result, dict) else {}

    if step == "tool:read_invoice":
        if "error" in result:
            return "❌", "Could not read the invoice."
        return "📄", "Read invoice " + str(result.get("invoice_number") or "?") + " from " + str(result.get("vendor") or "?")
    if step == "tool:check_invoice_math":
        if result.get("math_ok"):
            return "✅", "The invoice arithmetic is correct."
        issues = result.get("issues") or []
        text = " ".join(i.get("detail", "") for i in issues) or str(result.get("error", "Could not check the arithmetic."))
        return "⚠️", "Arithmetic problem. " + text
    if step == "tool:lookup_po":
        status = result.get("status")
        po = result.get("po") or {}
        if status == "found":
            return "✅", "Found purchase order " + str(po.get("po_number")) + "."
        if status in ("guessed", "ambiguous"):
            return "⚠️", str(result.get("note", "The PO number could not be confirmed."))
        return "❌", str(result.get("note", "No matching purchase order."))
    if step == "tool:check_vendor_matches_po":
        status = result.get("status")
        if status == "match":
            return "✅", "The vendor matches the purchase order."
        if status == "mismatch":
            return "⚠️", "Vendor mismatch: " + str(result.get("note", ""))
        return "ℹ️", "The vendor check was skipped."
    if step == "tool:send_to_human_review":
        return "🧑", "Sent to a human reviewer: " + str(detail.get("reason", ""))
    if step == "agent:error":
        return "🔁", "The AI service had a problem (try " + str(detail.get("attempt", "?")) + "). Trying again."
    if step == "agent:safety_override":
        return "🛡️", "The safety check found problems the AI missed: " + " | ".join(detail.get("problems", []))
    if step == "agent:gave_up":
        return "❌", "The AI service stayed unavailable, so a person will check this invoice."
    if step == "agent:recorded":
        return "📝", "Saved for approval as request #" + str(detail.get("approval_request_id")) + " (" + str(detail.get("recommendation")) + ")."
    return None


def show_checks(steps):
    """Show what the agent did in plain sentences, with the raw log underneath."""
    for s in steps:
        line = describe_step(s["step"], s["detail"])
        if line:
            st.write(line[0] + " " + line[1])
    with st.expander("Technical details (raw log)"):
        for s in steps:
            st.markdown("**" + s["step"] + "**")
            st.json(s["detail"])


# ---------- pages ----------

def login_screen():
    st.title("🧾 Invoice agent")
    st.write("An AI agent that checks vendor invoices. A person always makes the final decision.")
    left, middle, right = st.columns([1, 2, 1])
    with middle:
        st.subheader("Log in")
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
        st.caption("Demo accounts are listed in the README.")

    if submitted:
        try:
            user = authenticate(username, password)
        except Exception as e:
            st.error("Could not reach the database. Check that PostgreSQL is running and that .env is correct.")
            st.caption(str(e)[:200])
            return
        if user:
            st.session_state["user"] = user
            st.rerun()
        else:
            st.error("Wrong username or password.")


def upload_page(user):
    require_role(user, ["vendor"])
    st.header("📤 Upload an invoice")
    st.write("The agent reads the invoice, checks it against the company records, and passes it to a person for approval.")

    file_types = [t.lstrip(".") for t in sorted(SUPPORTED_TYPES)]
    uploaded = st.file_uploader("Invoice (PDF or image)", type=file_types)
    if uploaded is None:
        return

    if st.button("Check this invoice"):
        file_name = safe_name(uploaded.name)
        (INVOICE_DIR / file_name).write_bytes(uploaded.getvalue())
        save_log("vendor:upload", {"user": user["username"], "file": file_name})
        last_id = latest_log_id()

        with st.spinner("The agent is checking your invoice. This can take up to a minute..."):
            result = run_agent(file_name)

        if result["recommendation"] == "READY_FOR_APPROVAL":
            st.success("No problems found. It is now waiting for a person to approve it.")
        else:
            st.warning("The agent found something that needs a person to look at. It has been sent for human review.")
        if result["approval_request_id"] is None:
            st.error("The result could not be saved because the database is unavailable.")

        st.subheader("What the agent found")
        steps = log_since(last_id)
        if steps:
            show_checks(steps)
        else:
            st.write(result["summary"] or "")

        with st.expander("Invoice as read by the agent"):
            show_invoice(file_name)


def approvals_page(user):
    require_role(user, ["approver"])
    st.header("✅ Invoice approvals")

    if "message" in st.session_state:
        st.success(st.session_state.pop("message"))

    try:
        pending = list_approval_requests("PENDING")
        decided = [r for r in list_approval_requests() if r["status"] != "PENDING"]
    except Exception as e:
        st.error("Could not reach the database. Check that PostgreSQL is running and that .env is correct.")
        st.caption(str(e)[:200])
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Waiting for a decision", len(pending))
    m2.metric("Approved", sum(1 for r in decided if r["status"] == "APPROVED"))
    m3.metric("Rejected", sum(1 for r in decided if r["status"] == "REJECTED"))

    st.subheader("Waiting for a decision")
    if not pending:
        st.info("Nothing is waiting. Upload an invoice first.")

    for req in pending:
        dot = "🟠 " if req["recommendation"] == "NEEDS_HUMAN_REVIEW" else "🟢 "
        label = dot + "#" + str(req["id"]) + "  " + req["file_name"]
        with st.expander(label, expanded=True):
            left, right = st.columns(2)
            with left:
                if req["recommendation"] == "NEEDS_HUMAN_REVIEW":
                    st.warning("The agent flagged this invoice: " + (req["review_reason"] or "no reason given"))
                else:
                    st.success("The agent found no problems.")
                st.write(req["summary"] or "")
            with right:
                show_invoice(req["file_name"])
                show_original(req["file_name"], str(req["id"]))

            note = st.text_input("Note (optional)", key="note_" + str(req["id"]))
            col1, col2 = st.columns(2)
            decision = None
            if col1.button("Approve", key="approve_" + str(req["id"])):
                decision = "APPROVED"
            if col2.button("Reject", key="reject_" + str(req["id"])):
                decision = "REJECTED"

            if decision:
                done = decide_request(req["id"], decision, user["username"], note)
                if done:
                    st.session_state["message"] = decision.capitalize() + ": " + done
                else:
                    st.session_state["message"] = "That request was already decided."
                st.rerun()

    st.subheader("Already decided")
    if decided:
        st.dataframe([
            {
                "id": r["id"],
                "invoice": r["file_name"],
                "agent said": r["recommendation"],
                "decision": r["status"],
                "by": r["decided_by"],
                "when": r["decided_at"],
                "note": r["decision_note"],
            }
            for r in decided
        ])
    else:
        st.caption("No decisions yet.")


def audit_page(user):
    require_role(user, ["admin"])
    st.header("📜 Audit log")
    st.write("Every step the agent took, every login, and every human decision.")

    limit = st.slider("How many recent entries", 20, 500, 100)
    text_filter = st.text_input("Filter by step name (for example human, tool, auth)").strip().lower()

    try:
        rows = recent_log(limit)
    except Exception as e:
        st.error("Could not reach the database.")
        st.caption(str(e)[:200])
        return

    if text_filter:
        rows = [r for r in rows if text_filter in r["step"].lower()]
    st.caption(str(len(rows)) + " entries shown")
    st.dataframe([
        {
            "id": r["id"],
            "time": r["time"],
            "step": r["step"],
            "detail": json.dumps(r["detail"], ensure_ascii=False, default=str)[:300],
        }
        for r in rows
    ])


# ---------- main ----------

user = st.session_state.get("user")
if user is None:
    login_screen()
    st.stop()

st.sidebar.markdown("### 🧾 Invoice agent")
st.sidebar.write("Logged in as **" + user["full_name"] + "** (" + user["role"] + ")")
if st.sidebar.button("Log out"):
    save_log("auth:logout", {"user": user["username"]})
    st.session_state.pop("user", None)
    st.rerun()
st.sidebar.divider()

page = st.sidebar.radio("Go to", ROLE_PAGES[user["role"]])
if page == "Upload invoice":
    upload_page(user)
elif page == "Approvals":
    approvals_page(user)
elif page == "Audit log":
    audit_page(user)
    