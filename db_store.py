
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from db import get_connection


# Local recovery storage.
BASE_DIR = Path(__file__).resolve().parent
RECOVERY_DIR = BASE_DIR / "data" / "recovery" / "approval_requests"


# ==================================================
# 1. AUDIT LOGGING
# ==================================================

def save_log(step, detail):
    """Write one agent step to PostgreSQL. Return True if successful."""

    conn = None

    try:
        conn = get_connection()

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_log (step, detail) VALUES (%s, %s);",
                (
                    step,
                    json.dumps(
                        detail,
                        ensure_ascii=False,
                        default=str
                    ),
                ),
            )

        conn.commit()
        return True

    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "  [warning] Could not save audit log:",
            str(e)[:120]
        )
        return False

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ==================================================
# 2. LOCAL APPROVAL RECOVERY
# ==================================================

def _recovery_file(file_name):
    """Return a safe, stable local recovery filename."""

    key = hashlib.sha256(
        str(file_name).encode("utf-8")
    ).hexdigest()

    return RECOVERY_DIR / (key + ".json")


def has_queued_approval_request(file_name):
    """Check whether an approval request is waiting for database recovery."""

    try:
        return _recovery_file(file_name).is_file()
    except OSError:
        return False


def _queue_approval_request(
    file_name,
    recommendation,
    summary,
    review_reason
):
    """Atomically preserve an approval request on local disk."""

    temporary_path = None

    try:
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": 1,
            "file_name": str(file_name),
            "recommendation": str(recommendation),
            "summary": summary,
            "review_reason": review_reason,
            "queued_at": datetime.now().astimezone().isoformat(),
        }

        destination = _recovery_file(file_name)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=RECOVERY_DIR,
            prefix="approval_",
            suffix=".tmp",
            delete=False,
        ) as temp_file:

            temporary_path = Path(temp_file.name)

            json.dump(
                payload,
                temp_file,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temporary_path, destination)

        print(
            "  [recovery] Approval request saved locally:",
            file_name
        )

        return True

    except Exception as e:
        print(
            "  [critical warning] Could not save approval request "
            "to local recovery storage:",
            str(e)[:150]
        )
        return False

    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _remove_recovery_file(file_name):
    """Remove a recovery record after successful database saving."""

    try:
        _recovery_file(file_name).unlink(missing_ok=True)
    except OSError as e:
        print(
            "  [warning] Could not remove recovered request file:",
            str(e)[:120]
        )


# ==================================================
# 3. DATABASE APPROVAL SAVING
# ==================================================

def _save_approval_request_to_database(
    file_name,
    recommendation,
    summary,
    review_reason
):
    """
    Save one approval request.

    A PostgreSQL transaction-level advisory lock prevents concurrent
    calls using this function from inserting duplicate pending rows
    for the same filename.
    """

    conn = None

    try:
        conn = get_connection()

        with conn.cursor() as cur:

            # Serialize saves for the same invoice filename.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), 0);",
                (str(file_name),),
            )

            # Update an existing pending request.
            cur.execute(
                """
                UPDATE approval_requests
                SET
                    recommendation = %s,
                    summary = %s,
                    review_reason = %s,
                    created_at = CURRENT_TIMESTAMP
                WHERE file_name = %s
                  AND status = 'PENDING'
                RETURNING id;
                """,
                (
                    recommendation,
                    summary,
                    review_reason,
                    file_name,
                ),
            )

            row = cur.fetchone()

            # Insert if no pending request exists.
            if row is None:
                cur.execute(
                    """
                    INSERT INTO approval_requests
                    (
                        file_name,
                        recommendation,
                        summary,
                        review_reason
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        file_name,
                        recommendation,
                        summary,
                        review_reason,
                    ),
                )

                row = cur.fetchone()

        conn.commit()

        if row is None:
            raise RuntimeError(
                "Database did not return an approval request ID."
            )

        return row[0]

    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def save_approval_request(
    file_name,
    recommendation,
    summary,
    review_reason
):
    """
    Save an approval request to PostgreSQL.

    If PostgreSQL is unavailable, preserve the request locally.
    Returns the database request ID, or None if it was queued locally
    or could not be saved.
    """

    try:
        request_id = _save_approval_request_to_database(
            file_name,
            recommendation,
            summary,
            review_reason,
        )

        # Remove any older recovery copy after successful saving.
        _remove_recovery_file(file_name)

        return request_id

    except Exception as e:

        print(
            "  [warning] PostgreSQL approval save failed:",
            str(e)[:150]
        )

        queued = _queue_approval_request(
            file_name,
            recommendation,
            summary,
            review_reason,
        )

        if not queued:
            print(
                "  [critical] Approval request could not be "
                "saved in PostgreSQL or local recovery storage."
            )

        return None


# ==================================================
# 4. RECOVER PENDING APPROVAL REQUESTS
# ==================================================

def sync_pending_approval_requests():
    """
    Retry locally queued approval requests.

    Successful records are removed from the recovery directory.
    Failed records remain available for another recovery attempt.
    """

    result = {
        "synced": 0,
        "remaining": 0,
        "errors": [],
    }

    if not RECOVERY_DIR.is_dir():
        return result

    try:
        recovery_files = sorted(
            RECOVERY_DIR.glob("*.json")
        )
    except OSError as e:
        result["errors"].append(str(e)[:150])
        return result

    for recovery_file in recovery_files:

        try:
            payload = json.loads(
                recovery_file.read_text(encoding="utf-8")
            )

            if not isinstance(payload, dict):
                raise ValueError("Invalid recovery record.")

            file_name = payload.get("file_name")
            recommendation = payload.get("recommendation")

            if not file_name:
                raise ValueError("Recovery record has no filename.")

            if recommendation not in (
                "READY_FOR_APPROVAL",
                "NEEDS_HUMAN_REVIEW",
            ):
                raise ValueError("Invalid approval recommendation.")

            request_id = _save_approval_request_to_database(
                file_name,
                recommendation,
                payload.get("summary"),
                payload.get("review_reason"),
            )

            if request_id is None:
                raise RuntimeError("No database request ID returned.")

            _remove_recovery_file(file_name)

            result["synced"] += 1

            print(
                "  [recovery] Successfully synchronized:",
                file_name
            )

        except Exception as e:

            result["errors"].append(
                str(recovery_file.name) + ": " + str(e)[:150]
            )

            # Stop retrying additional records while the database
            # appears unavailable. Leave every remaining file intact.
            break

    try:
        result["remaining"] = len(
            list(RECOVERY_DIR.glob("*.json"))
        )
    except OSError:
        result["remaining"] = -1

    if result["synced"]:
        print(
            "  [recovery] Synchronized",
            result["synced"],
            "approval request(s)."
        )

    return result


# ==================================================
# 5. APPROVAL REQUEST RETRIEVAL
# ==================================================

REQUEST_COLUMNS = [
    "id",
    "file_name",
    "recommendation",
    "summary",
    "review_reason",
    "status",
    "created_at",
    "decided_by",
    "decided_at",
    "decision_note",
]


def list_approval_requests(status=None):
    """
    Return approval requests, newest first.

    Automatically attempts to recover locally queued requests first.
    """

    recovery_result = sync_pending_approval_requests()

    if recovery_result["remaining"]:
        print(
            "  [recovery] Pending local requests:",
            recovery_result["remaining"]
        )

    conn = get_connection()

    try:
        with conn.cursor() as cur:

            sql = (
                "SELECT "
                + ", ".join(REQUEST_COLUMNS)
                + " FROM approval_requests"
            )

            params = ()

            if status:
                sql += " WHERE status = %s"
                params = (status,)

            sql += " ORDER BY created_at DESC, id DESC;"

            cur.execute(sql, params)
            rows = cur.fetchall()

        return [
            dict(zip(REQUEST_COLUMNS, row))
            for row in rows
        ]

    finally:
        conn.close()


# ==================================================
# 6. HUMAN APPROVAL AND REJECTION
# ==================================================

def decide_request(request_id, decision, decided_by, note):
    """
    Record a human decision.

    Only a pending request can be approved or rejected.
    """

    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(
            "decision must be APPROVED or REJECTED"
        )

    conn = get_connection()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE approval_requests
                SET
                    status = %s,
                    decided_by = %s,
                    decided_at = CURRENT_TIMESTAMP,
                    decision_note = %s
                WHERE id = %s
                  AND status = 'PENDING'
                RETURNING file_name;
                """,
                (
                    decision,
                    decided_by,
                    note,
                    request_id,
                ),
            )

            row = cur.fetchone()

        conn.commit()

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    finally:
        conn.close()

    if row is None:
        return None

    save_log(
        "human:" + decision.lower(),
        {
            "request_id": request_id,
            "file": row[0],
            "by": decided_by,
            "note": note,
        },
    )

    return row[0]
