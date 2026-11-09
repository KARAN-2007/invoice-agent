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


REQUEST_COLUMNS = [
    "id", "file_name", "recommendation", "summary", "review_reason",
    "status", "created_at", "decided_by", "decided_at", "decision_note",
]


def list_approval_requests(status=None):
    """Return approval requests as a list of dictionaries, newest first.
    Pass status='PENDING' to get only the ones waiting for a human decision."""
    sql = "SELECT " + ", ".join(REQUEST_COLUMNS) + " FROM approval_requests"
    params = ()
    if status:
        sql += " WHERE status = %s"
        params = (status,)
    sql += " ORDER BY created_at DESC, id DESC;"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(REQUEST_COLUMNS, row)) for row in rows]


def decide_request(request_id, decision, decided_by, note):
    """Record a human decision (APPROVED or REJECTED) and write it to the audit log.
    Returns the invoice file name, or None if the request was already decided."""
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("decision must be APPROVED or REJECTED")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE approval_requests "
                "SET status = %s, decided_by = %s, decided_at = CURRENT_TIMESTAMP, decision_note = %s "
                "WHERE id = %s AND status = 'PENDING' RETURNING file_name;",
                (decision, decided_by, note, request_id),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if row is None:
        return None
    save_log("human:" + decision.lower(), {"request_id": request_id, "file": row[0], "by": decided_by, "note": note})
    return row[0]