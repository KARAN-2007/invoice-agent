import json

from db import get_connection


def save_log(step, detail):
    """Write one agent step to the agent_log table. Returns True if it worked."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_log (step, detail) VALUES (%s, %s);",
                    (step, json.dumps(detail, ensure_ascii=False, default=str)),
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def save_approval_request(file_name, recommendation, summary, review_reason):
    """Create the row a human will approve or reject. Returns its id, or None if saving failed.
    If the same invoice is still waiting for a decision, that row is updated instead of duplicated."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE approval_requests "
                    "SET recommendation = %s, summary = %s, review_reason = %s, created_at = CURRENT_TIMESTAMP "
                    "WHERE file_name = %s AND status = 'PENDING' RETURNING id;",
                    (recommendation, summary, review_reason, file_name),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "INSERT INTO approval_requests (file_name, recommendation, summary, review_reason) "
                        "VALUES (%s, %s, %s, %s) RETURNING id;",
                        (file_name, recommendation, summary, review_reason),
                    )
                    row = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return row[0]
    except Exception as e:
        print("  [warning] could not save the approval request: " + str(e)[:120])
        return None