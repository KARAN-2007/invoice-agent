import re
from datetime import datetime
from pathlib import Path

import streamlit as st

from agent import run_agent
from db_reports import latest_log_id, log_since
from db_store import decide_request, list_approval_requests
from extract_invoice import SUPPORTED_TYPES

INVOICE_DIR = Path(__file__).parent / "invoices"

st.set_page_config(page_title="Invoice agent", layout="wide")


def safe_name(original):
    """A unique, safe file name, so a new upload never reuses an old saved reading."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", original)
    return "upload_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + cleaned


def upload_page():
    st.header("Upload an invoice")
    st.write("The agent reads the invoice, checks it against the company records, and passes it to a person for approval.")

    file_types = [t.lstrip(".") for t in sorted(SUPPORTED_TYPES)]
    uploaded = st.file_uploader("Invoice (PDF or image)", type=file_types)
    if uploaded is None:
        return

    if st.button("Check this invoice"):
        file_name = safe_name(uploaded.name)
        (INVOICE_DIR / file_name).write_bytes(uploaded.getvalue())
        last_id = latest_log_id()

        with st.spinner("The agent is checking your invoice. This can take up to a minute..."):
            result = run_agent(file_name)

        if result["recommendation"] == "READY_FOR_APPROVAL":
            st.success("No problems found. It is now waiting for a person to approve it.")
        else:
            st.warning("The agent found something that needs a person to look at. It has been sent for human review.")
        st.write(result["summary"] or "")
        if result["approval_request_id"] is not None:
            st.caption("Approval request #" + str(result["approval_request_id"]))
        else:
            st.error("The result could not be saved because the database is unavailable.")

        steps = log_since(last_id)
        if steps:
            with st.expander("What the agent did, step by step"):
                for s in steps:
                    st.markdown("**" + s["step"] + "**")
                    st.json(s["detail"])


def approvals_page(approver):
    st.header("Invoice approvals")

    if "message" in st.session_state:
        st.success(st.session_state.pop("message"))

    try:
        pending = list_approval_requests("PENDING")
        decided = [r for r in list_approval_requests() if r["status"] != "PENDING"]
    except Exception as e:
        st.error("Could not reach the database. Check that PostgreSQL is running and that .env is correct.")
        st.caption(str(e)[:200])
        return

    st.subheader("Waiting for a decision (" + str(len(pending)) + ")")
    if not pending:
        st.info("Nothing is waiting. Upload an invoice first.")

    for req in pending:
        label = "#" + str(req["id"]) + "  " + req["file_name"] + "  |  " + req["recommendation"]
        with st.expander(label, expanded=True):
            if req["recommendation"] == "NEEDS_HUMAN_REVIEW":
                st.warning("The agent flagged this invoice: " + (req["review_reason"] or "no reason given"))
            else:
                st.success("The agent found no problems.")
            st.write(req["summary"] or "")

            note = st.text_input("Note (optional)", key="note_" + str(req["id"]))
            col1, col2 = st.columns(2)
            decision = None
            if col1.button("Approve", key="approve_" + str(req["id"])):
                decision = "APPROVED"
            if col2.button("Reject", key="reject_" + str(req["id"])):
                decision = "REJECTED"

            if decision:
                if not approver.strip():
                    st.error("Please type your name in the sidebar first.")
                else:
                    done = decide_request(req["id"], decision, approver.strip(), note)
                    if done:
                        st.session_state["message"] = decision.capitalize() + ": " + done
                    else:
                        st.session_state["message"] = "That request was already decided."
                    st.rerun()

    st.subheader("Already decided (" + str(len(decided)) + ")")
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


st.title("Invoice agent")
page = st.sidebar.radio("Go to", ["Upload invoice", "Approvals"])
approver = st.sidebar.text_input("Your name (approver)")
st.sidebar.caption("Login and roles will be added later.")

if page == "Upload invoice":
    upload_page()
else:
    approvals_page(approver)