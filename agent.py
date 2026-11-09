import os
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agent_tools import (
    check_invoice_math,
    check_vendor_matches_po,
    log_step,
    lookup_po,
    read_invoice,
    review_reason,
    send_to_human_review,
    start_run,
    verify_invoice,
)
from db_store import save_approval_request

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options={"timeout": 45000})

# The primary model is used first. If FALLBACK_MODEL is set in .env, the agent alternates to it
# when a try fails, so one busy model does not stop the whole run.
MODELS = [os.getenv("MODEL", "gemini-3.1-flash-lite")]
if os.getenv("FALLBACK_MODEL"):
    MODELS.append(os.getenv("FALLBACK_MODEL"))

MAX_ATTEMPTS = 5
WAIT_SECONDS = [5, 15, 30, 60]  # pause before each retry

SYSTEM_PROMPT = """You are an accounts payable agent for a university. You check one vendor invoice at a time.

Work through these steps in order:
1. Call read_invoice to get the invoice data.
2. Call check_invoice_math to check its arithmetic.
3. If the invoice has a PO number, call lookup_po with it exactly as printed.
4. If lookup_po found a PO (status found or guessed), call check_vendor_matches_po.
5. If anything is missing, wrong, guessed, ambiguous or not found, call send_to_human_review once, with a clear reason that lists every problem.

Rules:
- Never invent or correct a PO number, a vendor or an amount yourself. Only report what the tools return.
- You cannot approve or pay anything. A human always decides.
- Call each tool at most once, unless it returned an error.
- Finish with a short summary and one recommendation line: READY_FOR_APPROVAL if every check passed, otherwise NEEDS_HUMAN_REVIEW."""


def record_outcome(file_name, summary):
    """Decide the final recommendation in plain code and save it for a human to approve or reject.
    The AI's own recommendation is never trusted alone: the checks are repeated without the AI."""
    problems = verify_invoice(file_name)
    reason = review_reason(file_name)
    if problems and not reason:
        reason = "Safety check found: " + " | ".join(problems)
        log_step("agent:safety_override", {"file": file_name, "problems": problems})
    recommendation = "NEEDS_HUMAN_REVIEW" if reason else "READY_FOR_APPROVAL"
    request_id = save_approval_request(file_name, recommendation, summary, reason)
    log_step("agent:recorded", {"file": file_name, "recommendation": recommendation, "approval_request_id": request_id})
    return {"summary": summary, "recommendation": recommendation, "approval_request_id": request_id}


def run_agent(file_name):
    start_run()
    log_step("agent:start", {"file": file_name, "models": MODELS})
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0,
        tools=[read_invoice, check_invoice_math, lookup_po, check_vendor_matches_po, send_to_human_review],
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        model = MODELS[(attempt - 1) % len(MODELS)]
        try:
            # A fresh chat for every attempt, so a failed try leaves nothing half-finished behind
            chat = client.chats.create(model=model, config=config)
            response = chat.send_message("Check the invoice file " + file_name + ".")
            log_step("agent:finish", {"model": model, "summary": response.text})
            return record_outcome(file_name, response.text)
        except Exception as e:
            log_step("agent:error", {"attempt": attempt, "model": model, "error": str(e)[:150]})
            if attempt < MAX_ATTEMPTS:
                wait = WAIT_SECONDS[min(attempt - 1, len(WAIT_SECONDS) - 1)]
                log_step("agent:retry", {"waiting_seconds": wait})
                time.sleep(wait)

    # Fail safe: if the agent cannot run, the invoice must not be silently dropped
    log_step("agent:gave_up", {"file": file_name})
    send_to_human_review(file_name, "The automated check could not finish because the AI service was unavailable. Please check this invoice manually.")
    return record_outcome(file_name, "The automated check could not finish. The invoice was sent to human review.")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "invoice3.pdf"
    print("=== Agent checking " + name + " ===")
    result = run_agent(name)
    print("\n" + (result["summary"] or "The automated check could not finish."))
    print("\nRecommendation: " + result["recommendation"])
    if result["approval_request_id"] is not None:
        print("Saved as approval request #" + str(result["approval_request_id"]) + ". A human must approve or reject it.")
    else:
        print("Could not save the approval request (database unavailable).")
        