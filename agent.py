
import os
import re
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

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options={"timeout": 45000}
)


# ==================================================
# 1. MODEL CONFIGURATION
# ==================================================

MAIN_MODEL = (
    os.getenv("MODEL") or "gemini-3.1-flash-lite"
).strip()

# Read multiple backup models from .env.
# Keep support for the old FALLBACK_MODEL setting.

fallback_candidates = (
    os.getenv("FALLBACK_MODELS", "").split(",")
    + [os.getenv("FALLBACK_MODEL", "")]
)

# Remove duplicates and empty model names.
MODELS = []
seen_models = set()

for model_name in [MAIN_MODEL] + fallback_candidates:

    model_name = model_name.strip()

    if model_name and model_name not in seen_models:
        MODELS.append(model_name)
        seen_models.add(model_name)


# One attempt per configured model.
MAX_ATTEMPTS = len(MODELS)

# Short pause before switching to the next backup.
BACKUP_WAIT_SECONDS = 2


# ==================================================
# 2. ERROR CLASSIFICATION
# ==================================================

def get_http_status(error):
    """Extract an HTTP status code from a Gemini API exception."""

    for attribute in ("status_code", "code"):

        value = getattr(error, attribute, None)

        if value is not None:

            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    match = re.search(
        r"\b(400|401|403|404|408|429|500|502|503|504)\b",
        str(error)
    )

    return int(match.group(1)) if match else None


def is_global_auth_error(status_code):
    """Authentication errors generally affect every model on this key."""

    return status_code in (401, 403)


# ==================================================
# 3. AGENT INSTRUCTIONS
# ==================================================

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


# ==================================================
# 4. INDEPENDENT PYTHON SAFETY CHECK
# ==================================================

def record_outcome(file_name, summary):

    """The AI's recommendation is never trusted alone."""

    problems = verify_invoice(file_name)
    reason = review_reason(file_name)

    if problems and not reason:

        reason = "Safety check found: " + " | ".join(problems)

        log_step(
            "agent:safety_override",
            {
                "file": file_name,
                "problems": problems
            }
        )

    recommendation = (
        "NEEDS_HUMAN_REVIEW"
        if reason
        else "READY_FOR_APPROVAL"
    )

    request_id = save_approval_request(
        file_name,
        recommendation,
        summary,
        reason
    )

    log_step(
        "agent:recorded",
        {
            "file": file_name,
            "recommendation": recommendation,
            "approval_request_id": request_id
        }
    )

    return {
        "summary": summary,
        "recommendation": recommendation,
        "approval_request_id": request_id
    }


# ==================================================
# 5. MAIN AGENT WITH MULTI-MODEL FAILOVER
# ==================================================

def run_agent(file_name):

    start_run()

    log_step(
        "agent:start",
        {
            "file": file_name,
            "models": MODELS,
            "max_attempts": MAX_ATTEMPTS
        }
    )

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0,
        tools=[
            read_invoice,
            check_invoice_math,
            lookup_po,
            check_vendor_matches_po,
            send_to_human_review
        ],
    )

    for attempt, model in enumerate(MODELS, start=1):

        try:

            log_step(
                "agent:model_attempt",
                {
                    "attempt": attempt,
                    "model": model
                }
            )

            print(
                "\n[AGENT] Trying model "
                + model
                + " ("
                + str(attempt)
                + "/"
                + str(MAX_ATTEMPTS)
                + ")"
            )

            chat = client.chats.create(
                model=model,
                config=config
            )

            response = chat.send_message(
                "Check the invoice file " + file_name + "."
            )

            summary = response.text or (
                "The model completed without returning a text summary."
            )

            log_step(
                "agent:finish",
                {
                    "model": model,
                    "summary": summary
                }
            )

            print("[AGENT] Model succeeded: " + model)

            return record_outcome(file_name, summary)

        except Exception as e:

            status_code = get_http_status(e)

            next_model = (
                MODELS[attempt]
                if attempt < len(MODELS)
                else None
            )

            log_step(
                "agent:error",
                {
                    "attempt": attempt,
                    "model": model,
                    "status_code": status_code,
                    "error": str(e)[:250],
                    "next_model": next_model
                }
            )

            print(
                "[AGENT] Model failed: "
                + model
                + " | HTTP status: "
                + str(status_code)
            )

            # A wrong API key or missing permission will
            # generally affect all models.
            if is_global_auth_error(status_code):

                log_step(
                    "agent:auth_failure",
                    {
                        "model": model,
                        "status_code": status_code,
                        "message": "Check Gemini API key and permissions."
                    }
                )

                print(
                    "[AGENT] Authentication/permission error. "
                    "Stopping model switching."
                )

                break

            # Move to the next model.
            if next_model:

                log_step(
                    "agent:retry",
                    {
                        "waiting_seconds": BACKUP_WAIT_SECONDS,
                        "failed_model": model,
                        "next_model": next_model,
                        "status_code": status_code
                    }
                )

                print(
                    "[AGENT] Switching to backup: "
                    + next_model
                )

                time.sleep(BACKUP_WAIT_SECONDS)

            else:

                print("[AGENT] All configured models have failed.")

    # ==================================================
    # 6. FAIL-SAFE: SEND TO HUMAN REVIEW
    # ==================================================

    log_step(
        "agent:gave_up",
        {
            "file": file_name,
            "models_tried": MODELS
        }
    )

    send_to_human_review(
        file_name,
        "The automated check could not finish because the AI service "
        "was unavailable. Please check this invoice manually."
    )

    return record_outcome(
        file_name,
        "The automated check could not finish. "
        "The invoice was sent to human review."
    )


# ==================================================
# 7. DIRECT EXECUTION
# ==================================================

if __name__ == "__main__":

    name = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "invoice3.pdf"
    )

    print("\n=== Invoice Agent ===")

    print("Primary model: " + MAIN_MODEL)

    print("Configured models:")

    for index, model in enumerate(MODELS, start=1):
        print("  " + str(index) + ". " + model)

    print("\nMaximum attempts: " + str(MAX_ATTEMPTS))

    result = run_agent(name)

    print("\n" + (
        result["summary"]
        or "The automated check could not finish."
    ))

    print("\nRecommendation: " + result["recommendation"])

    if result["approval_request_id"] is not None:

        print(
            "Saved as approval request #"
            + str(result["approval_request_id"])
            + ". A human must approve or reject it."
        )

    else:

        print(
            "Could not save the approval request "
            "(database unavailable)."
        )
