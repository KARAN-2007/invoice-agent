import hashlib
import hmac
import secrets

from db import get_connection
from db_store import save_log

ITERATIONS = 200_000


def hash_password(password):
    """Turn a password into a salted hash that is safe to store."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return "pbkdf2_sha256$" + str(ITERATIONS) + "$" + salt + "$" + digest.hex()


def verify_password(password, stored):
    """Check a password against a stored hash."""
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except ValueError:
        return False


def create_user(username, full_name, role, password):
    """Add a user, or update the name, role and password if the username already exists."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, full_name, role, password_hash) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (username) DO UPDATE SET full_name = EXCLUDED.full_name, "
                "role = EXCLUDED.role, password_hash = EXCLUDED.password_hash;",
                (username.strip().lower(), full_name, role, hash_password(password)),
            )
        conn.commit()
    finally:
        conn.close()


def authenticate(username, password):
    """Return the user as a dictionary if the login is correct, otherwise None. Every attempt is logged."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, full_name, role, password_hash FROM users WHERE username = %s;",
                (username.strip().lower(),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row and verify_password(password, row[3]):
        save_log("auth:login", {"user": row[0], "role": row[2]})
        return {"username": row[0], "full_name": row[1], "role": row[2]}
    save_log("auth:login_failed", {"user": username.strip()[:50]})
    return None