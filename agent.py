import os
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agent_tools import (
    check_dataset_rules,
    check_invoice_math,
    check_vendor_matches_po,
    get_dataset_result,
    log_step,
    lookup_po,
    read_invoice,
    send_to_human_review,
    start_run,
)
from db_store import save_approval_request
from rules import INFO_ONLY_CODES


# ==================================================
# 1. CONFIGURATION
# ==================================================

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
client = (
    genai.Client(
        api_key=API_KEY,
        http_options={"timeout": 45000},
    )
    if API_KEY
    else None
)

MODELS = []

primary_model = os.getenv("MODEL", "gemini-3.5-flash-lite").strip()
fallback_models = os.getenv("FALLBACK_MODELS", "").split(",")
legacy_fallback = os.getenv("FALLBACK_MODEL", "").strip()

for model_name in [primary_model, *fallback_models, legacy_fallback]:
    model_name = model_name.strip()
    if model_name and model_name not in MODELS:
        MODELS.append(model_name)

if not MODELS:
    MODELS = ["gemini-3.5-flash-lite"]

MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "2"))
if MAX_ATTEMPTS < 1:
    raise ValueError("MAX_ATTEMPTS must be at least 1")

WAIT_SECONDS = [3, 8, 15, 30]


# ==================================================
# 2. SYSTEM PROMPT
# ==================================================

SYSTEM_PROMPT = """You are an accounts payable agent for a university. You check one vendor invoice at a time.

Work through these steps in order:
1. Call read_invoice to get the invoice data.
2. Call check_invoice_math to check its arithmetic.
3. If the invoice has a PO number, call lookup_po with it exactly as printed.
4. If lookup_po finds the PO exactly, call check_vendor_matches_po.
5. Always call check_dataset_rules exactly once.
6. Treat the recommendation and findings from check_dataset_rules as authoritative.
7. If the dataset recommendation is NEEDS_HUMAN_REVIEW, call send_to_human_review once with all returned reason codes.
8. If the only finding is PRICE_VARIANCE_WITHIN_TOLERANCE, do not send it for human review.

Rules:
- Never invent or correct a PO number, vendor or amount.
- Never replace the deterministic dataset recommendation with your own judgement.
- You cannot approve or pay anything. A human always makes the final approval decision.
- Call each tool at most once, unless it returned an error.
- Finish with a short summary. The final program will determine the recommendation from the dataset rules.
"""


# ==================================================
# 3. RECORD FINAL OUTCOME
# ==================================================

def record_outcome(file_name, summary, force_human_review_reason=None):
    """Save the deterministic dataset result as the approval request."""

    dataset_result = get_dataset_result(file_name)

    # Do not depend on Gemini remembering to call the rules tool.
    if not isinstance(dataset_result, dict):
        dataset_result = check_dataset_rules(file_name)

    if not isinstance(dataset_result, dict):
        dataset_result = {}

    recommendation = dataset_result.get("recommendation")
    raw_findings = dataset_result.get("findings", [])

    # Invalid rule output must fail closed to human review.
    if not isinstance(raw_findings, list):
        raw_findings = []
        recommendation = "NEEDS_HUMAN_REVIEW"

    reason_codes = list(dict.fromkeys(
        str(code).strip()
        for code in raw_findings
        if code is not None and str(code).strip()
    ))

    if recommendation not in ("READY_FOR_APPROVAL", "NEEDS_HUMAN_REVIEW"):
        recommendation = "NEEDS_HUMAN_REVIEW"
        if "INCOMPLETE_EXTRACTION" not in reason_codes:
            reason_codes.append("INCOMPLETE_EXTRACTION")

    # Only explicitly informational findings may accompany READY_FOR_APPROVAL.
    if any(code not in INFO_ONLY_CODES for code in reason_codes):
        recommendation = "NEEDS_HUMAN_REVIEW"

    if recommendation == "NEEDS_HUMAN_REVIEW" and not reason_codes:
        reason_codes = ["INCOMPLETE_EXTRACTION"]

    extra_note = None
    if force_human_review_reason:
        recommendation = "NEEDS_HUMAN_REVIEW"
        extra_note = str(force_human_review_reason)
        if not reason_codes:
            reason_codes = ["INCOMPLETE_EXTRACTION"]

    # Keep machine-readable reason codes for reason_labels.py.
    reason_parts = list(reason_codes)
    if extra_note:
        reason_parts.append(extra_note)
    review_reason_text = " | ".join(reason_parts) if reason_parts else None

    if recommendation == "NEEDS_HUMAN_REVIEW":
        send_to_human_review(
            file_name,
            review_reason_text or "Manual review required.",
        )

    log_step(
        "agent:dataset_decision",
        {
            "file": file_name,
            "recommendation": recommendation,
            "findings": reason_codes,
        },
    )

    # This is deliberately outside the Gemini retry loop. A database error
    # must not cause the AI to execute invoice tools again.
    request_id = save_approval_request(
        file_name,
        recommendation,
        summary,
        review_reason_text,
    )

    log_step(
        "agent:recorded",
        {
            "file": file_name,
            "recommendation": recommendation,
            "approval_request_id": request_id,
        },
    )

    return {
        "summary": summary,
        "recommendation": recommendation,
        "approval_request_id": request_id,
        "findings": reason_codes,
    }


# ==================================================
# 4. MAIN AGENT
# ==================================================

def run_agent(file_name):
    start_run(file_name)
    log_step("agent:start", {"file": file_name, "models": MODELS})

    if client is None:
        log_step(
            "agent:error",
            {"error": "GEMINI_API_KEY is missing from the .env file."},
        )
        return record_outcome(
            file_name,
            "The AI service could not start. The invoice requires human review.",
            force_human_review_reason=(
                "The AI service could not start because GEMINI_API_KEY is missing."
            ),
        )

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0,
        tools=[
            read_invoice,
            check_invoice_math,
            lookup_po,
            check_vendor_matches_po,
            check_dataset_rules,
            send_to_human_review,
        ],
    )

    summary = None
    ai_failure_reason = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        model = MODELS[(attempt - 1) % len(MODELS)]

        try:
            log_step(
                "agent:model_attempt",
                {"attempt": attempt, "model": model},
            )

            chat = client.chats.create(model=model, config=config)
            response = chat.send_message(
                "Check the invoice file " + file_name + "."
            )

            summary = response.text or (
                "The automated checks completed without an AI text summary."
            )
            log_step("agent:finish", {"model": model, "summary": summary})
            ai_failure_reason = None
            break

        except Exception as e:
            error_text = str(e)[:200]
            log_step(
                "agent:error",
                {
                    "attempt": attempt,
                    "model": model,
                    "error": error_text,
                },
            )
            ai_failure_reason = error_text

            # Critical retry guard: if the deterministic rules tool already
            # completed, do not start a fresh chat and execute tools again.
            # Use the saved result and finish with a fallback summary instead.
            existing_result = get_dataset_result(file_name)
            if isinstance(existing_result, dict):
                summary = (
                    "The deterministic invoice checks completed, but the AI "
                    "response timed out before a final summary was returned. "
                    "The recommendation below is based on the saved dataset checks."
                )
                log_step(
                    "agent:retry_suppressed",
                    {
                        "file": file_name,
                        "reason": "dataset_result_already_available",
                        "attempt": attempt,
                    },
                )
                break

            if attempt < MAX_ATTEMPTS:
                wait = WAIT_SECONDS[
                    min(attempt - 1, len(WAIT_SECONDS) - 1)
                ]
                log_step("agent:retry", {"waiting_seconds": wait})
                time.sleep(wait)

    if summary is None:
        log_step(
            "agent:gave_up",
            {"file": file_name, "models_tried": MODELS},
        )
        summary = (
            "The automated AI summary could not be completed. "
            "The invoice was routed for human review."
        )
        return record_outcome(
            file_name,
            summary,
            force_human_review_reason=(
                "The AI service was unavailable after all configured attempts. "
                + (ai_failure_reason or "No additional error details were returned.")
            ),
        )

    # Record the outcome once, after the AI retry loop has ended. Exceptions
    # here are database/application errors, not reasons to rerun Gemini tools.
    return record_outcome(file_name, summary)


# ==================================================
# 5. DIRECT EXECUTION
# ==================================================

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "invoice3.pdf"
    print("=== Agent checking " + name + " ===")
    result = run_agent(name)

    print("\n" + (result["summary"] or "The automated check could not finish."))
    print("\nRecommendation: " + result["recommendation"])

    if result["approval_request_id"] is not None:
        print(
            "Saved as approval request #"
            + str(result["approval_request_id"])
            + ". A human must approve or reject it."
        )
    else:
        print("Could not save the approval request (database unavailable).")

    if result.get("findings"):
        print("Reason codes: " + ", ".join(result["findings"]))
