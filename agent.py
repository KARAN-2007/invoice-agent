
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

from db_store import (
    save_approval_request,
    has_queued_approval_request,
)

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

primary_model = os.getenv(
    "MODEL", "gemini-3.5-flash-lite"
).strip()

fallback_models = os.getenv(
    "FALLBACK_MODELS", ""
).split(",")

legacy_fallback = os.getenv(
    "FALLBACK_MODEL", ""
).strip()

MODELS = []

for model_name in [
    primary_model,
    *fallback_models,
    legacy_fallback,
]:
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
# 2. SAFE LOGGING
# ==================================================

def _safe_log(step, detail):
    """Prevent audit logging failures from stopping the agent."""

    try:
        log_step(step, detail)

    except Exception as exc:
        print(
            "[warning] Could not record event:",
            step,
            str(exc)[:150],
        )


# ==================================================
# 3. SYSTEM PROMPT
# ==================================================

SYSTEM_PROMPT = """
You are an accounts payable agent for a university.

You check one vendor invoice at a time.

Follow these steps in order:

1. Call read_invoice with the exact filename supplied.

2. Inspect the result.

   If the result contains an error or does not contain valid
   invoice data, call read_invoice ONE more time.

   If the second attempt also fails:
   - Do not invent invoice information.
   - Do not guess a PO number or vendor.
   - Continue to check_dataset_rules.
   - Follow its recommendation.

3. Call check_invoice_math using the exact filename.

4. Read the PO reference from the actual read_invoice result.

   Use po_reference or po_number from that result.

   Never invent, reconstruct, or substitute a PO number.

5. If a PO reference exists, call lookup_po with TWO arguments:
   - file_name: the exact invoice filename
   - po_number: the PO reference extracted from read_invoice

   This tool independently verifies the PO against the original
   invoice. Its result is authoritative.

6. If lookup_po finds the PO, call check_vendor_matches_po
   with TWO arguments:
   - file_name: the exact invoice filename
   - po_number: the PO reference extracted from read_invoice

7. Always call check_dataset_rules exactly once.

8. Treat the recommendation and findings from check_dataset_rules
   as authoritative.

9. If the deterministic recommendation is NEEDS_HUMAN_REVIEW,
   call send_to_human_review once with all reason codes.

10. If the only finding is PRICE_VARIANCE_WITHIN_TOLERANCE,
    do not send the invoice for human review.

IMPORTANT RULES:

- Never invent or correct a PO number, vendor or amount.
- Never use a PO number that is not present in the invoice data.
- Never claim a PO was missing without checking the actual invoice.
- Never replace deterministic findings with your own judgement.
- Do not mistake an informational finding for a blocking issue.
- You cannot approve or pay anything.
- A human always makes the final approval decision.
- Call each tool at most once, unless it returned an error.
- Retry read_invoice only once after an error.
- Finish with a short factual summary.
- Mention only information supported by the tool results.
"""


# ==================================================
# 4. RECORD FINAL OUTCOME
# ==================================================

def record_outcome(
    file_name,
    summary,
    force_human_review_reason=None,
):
    """Save the deterministic recommendation and track its status."""

    try:
        dataset_result = get_dataset_result(file_name)

    except Exception as exc:
        dataset_result = None

        _safe_log(
            "agent:dataset_result_read_error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

    if not isinstance(dataset_result, dict):

        try:
            dataset_result = check_dataset_rules(file_name)

        except Exception as exc:
            dataset_result = None

            _safe_log(
                "agent:dataset_rules_error",
                {
                    "file": file_name,
                    "error": str(exc)[:150],
                },
            )

    if not isinstance(dataset_result, dict):
        dataset_result = {}

    recommendation = dataset_result.get("recommendation")
    raw_findings = dataset_result.get("findings", [])

    if not isinstance(raw_findings, list):
        raw_findings = []
        recommendation = "NEEDS_HUMAN_REVIEW"

    reason_codes = list(dict.fromkeys(
        str(code).strip()
        for code in raw_findings
        if code is not None and str(code).strip()
    ))

    if recommendation not in (
        "READY_FOR_APPROVAL",
        "NEEDS_HUMAN_REVIEW",
    ):
        recommendation = "NEEDS_HUMAN_REVIEW"

        if "INCOMPLETE_EXTRACTION" not in reason_codes:
            reason_codes.append("INCOMPLETE_EXTRACTION")

    if any(
        code not in INFO_ONLY_CODES
        for code in reason_codes
    ):
        recommendation = "NEEDS_HUMAN_REVIEW"

    if recommendation == "NEEDS_HUMAN_REVIEW" and not reason_codes:
        reason_codes = ["INCOMPLETE_EXTRACTION"]

    extra_note = None

    if force_human_review_reason:
        recommendation = "NEEDS_HUMAN_REVIEW"
        extra_note = str(force_human_review_reason)

        if not reason_codes:
            reason_codes = ["INCOMPLETE_EXTRACTION"]

    reason_parts = list(reason_codes)

    if extra_note:
        reason_parts.append(extra_note)

    review_reason_text = (
        " | ".join(reason_parts)
        if reason_parts
        else None
    )

    if recommendation == "NEEDS_HUMAN_REVIEW":

        try:
            send_to_human_review(
                file_name,
                review_reason_text or "Manual review required.",
            )

        except Exception as exc:
            _safe_log(
                "agent:human_review_routing_error",
                {
                    "file": file_name,
                    "error": str(exc)[:150],
                },
            )

    _safe_log(
        "agent:dataset_decision",
        {
            "file": file_name,
            "recommendation": recommendation,
            "findings": reason_codes,
        },
    )

    # Save once, outside the Gemini retry loop.
    try:
        request_id = save_approval_request(
            file_name,
            recommendation,
            summary,
            review_reason_text,
        )

    except Exception as exc:
        request_id = None

        _safe_log(
            "agent:approval_save_error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

    # Check whether a failed database save was queued locally.
    try:
        recovery_queued = (
            request_id is None
            and has_queued_approval_request(file_name)
        )

    except Exception:
        recovery_queued = False

    if request_id is not None:
        approval_status = "SAVED_TO_DATABASE"

    elif recovery_queued:
        approval_status = "QUEUED_LOCALLY"

    else:
        approval_status = "NOT_SAVED"

    _safe_log(
        "agent:recorded",
        {
            "file": file_name,
            "recommendation": recommendation,
            "approval_request_id": request_id,
            "approval_status": approval_status,
        },
    )

    return {
        "summary": summary,
        "recommendation": recommendation,
        "approval_request_id": request_id,
        "approval_saved": request_id is not None,
        "recovery_queued": recovery_queued,
        "approval_status": approval_status,
        "findings": reason_codes,
    }


# ==================================================
# 5. MAIN AGENT
# ==================================================

def run_agent(file_name):
    """Run Gemini tools and save the outcome once."""

    try:
        start_run(file_name)

    except Exception as exc:
        _safe_log(
            "agent:start_run_error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

    _safe_log(
        "agent:start",
        {
            "file": file_name,
            "models": MODELS,
        },
    )

    if client is None:
        return record_outcome(
            file_name,
            "The AI service could not start. Human review is required.",
            force_human_review_reason=(
                "The AI service could not start because "
                "GEMINI_API_KEY is missing."
            ),
        )

    try:
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

    except Exception as exc:
        _safe_log(
            "agent:configuration_error",
            {
                "file": file_name,
                "error": str(exc)[:150],
            },
        )

        return record_outcome(
            file_name,
            "The agent configuration failed. Human review is required.",
            force_human_review_reason="AI tool configuration failed.",
        )

    summary = None
    ai_failure_reason = None

    for attempt in range(1, MAX_ATTEMPTS + 1):

        model = MODELS[(attempt - 1) % len(MODELS)]

        try:
            _safe_log(
                "agent:model_attempt",
                {
                    "attempt": attempt,
                    "model": model,
                },
            )

            chat = client.chats.create(
                model=model,
                config=config,
            )

            response = chat.send_message(
                "Check the invoice file " + str(file_name) + "."
            )

            summary = getattr(response, "text", None) or (
                "Automated checks completed. "
                "The recommendation uses deterministic rules."
            )

            _safe_log(
                "agent:finish",
                {
                    "model": model,
                    "summary": summary,
                },
            )

            break

        except Exception as exc:

            ai_failure_reason = str(exc)[:200]

            _safe_log(
                "agent:error",
                {
                    "attempt": attempt,
                    "model": model,
                    "error": ai_failure_reason,
                },
            )

            # Do not repeat completed deterministic checks.
            try:
                existing_result = get_dataset_result(file_name)

            except Exception:
                existing_result = None

            if isinstance(existing_result, dict):

                summary = (
                    "Deterministic checks completed, but Gemini "
                    "failed to return its final summary. "
                    "The recommendation uses the saved rule results."
                )

                _safe_log(
                    "agent:retry_suppressed",
                    {
                        "file": file_name,
                        "reason": "dataset_result_already_available",
                    },
                )

                break

            if attempt < MAX_ATTEMPTS:

                wait = WAIT_SECONDS[
                    min(attempt - 1, len(WAIT_SECONDS) - 1)
                ]

                _safe_log(
                    "agent:retry",
                    {
                        "waiting_seconds": wait,
                    },
                )

                time.sleep(wait)

    if summary is None:

        _safe_log(
            "agent:gave_up",
            {
                "file": file_name,
                "models_tried": MODELS,
            },
        )

        return record_outcome(
            file_name,
            (
                "The AI service could not complete processing. "
                "Human review is required."
            ),
            force_human_review_reason=(
                "AI service unavailable after configured attempts. "
                + (ai_failure_reason or "")
            ),
        )

    # Never rerun Gemini because approval saving failed.
    return record_outcome(file_name, summary)


# ==================================================
# 6. DIRECT EXECUTION
# ==================================================

if __name__ == "__main__":

    name = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "invoice3.pdf"
    )

    print("=== Agent checking " + name + " ===")

    result = run_agent(name)

    print("\n" + result["summary"])

    print("\nRecommendation:", result["recommendation"])

    print("Approval status:", result["approval_status"])

    if result["approval_request_id"] is not None:
        print(
            "Database request ID:",
            result["approval_request_id"],
        )

    elif result["recovery_queued"]:
        print("Approval request is waiting for database recovery.")

    else:
        print("Approval request could not be saved.")

    if result["findings"]:
        print("Reason codes:", ", ".join(result["findings"]))