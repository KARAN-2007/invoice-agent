import streamlit as st

from db_store import decide_request, list_approval_requests

st.set_page_config(page_title="Invoice approvals", layout="wide")
st.title("Invoice approvals")

approver = st.sidebar.text_input("Your name (approver)")
st.sidebar.caption("Login and roles will be added later.")

if "message" in st.session_state:
    st.success(st.session_state.pop("message"))

try:
    pending = list_approval_requests("PENDING")
    decided = [r for r in list_approval_requests() if r["status"] != "PENDING"]
except Exception as e:
    st.error("Could not reach the database. Check that PostgreSQL is running and that .env is correct.")
    st.caption(str(e)[:200])
    st.stop()

st.subheader("Waiting for a decision (" + str(len(pending)) + ")")
if not pending:
    st.info("Nothing is waiting. Run the agent on an invoice first.")

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