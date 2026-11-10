from db import get_connection


def latest_log_id():
    """The id of the newest row in agent_log, or None if the database cannot be reached."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(id), 0) FROM agent_log;")
                return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None


def log_since(last_id):
    """The agent steps logged after the given id, oldest first. Empty list if unavailable."""
    if last_id is None:
        return []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, logged_at, step, detail FROM agent_log WHERE id > %s ORDER BY id;",
                    (last_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "time": r[1], "step": r[2], "detail": r[3]} for r in rows]
    except Exception:
        return []


def recent_log(limit=100):
    """The newest rows of the audit log, newest first. Raises an error if the database cannot be reached."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, logged_at, step, detail FROM agent_log ORDER BY id DESC LIMIT %s;",
                (limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "time": r[1], "step": r[2], "detail": r[3]} for r in rows]