import hashlib
import hmac
import os
from datetime import datetime, timedelta
from database.connection import get_connection

_ITERATIONS = 200_000
_SALT_BYTES  = 16


def _hash_pin(pin: str) -> str:
    """Hash a PIN with PBKDF2-HMAC-SHA256 and a random salt. Returns 'salt_hex:dk_hex'."""
    salt = os.urandom(_SALT_BYTES)
    dk   = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}:{dk.hex()}"


def _verify_hash(pin: str, stored: str) -> bool:
    """Verify pin against a stored hash. Accepts both PBKDF2 and legacy SHA-256 formats."""
    if ':' in stored:
        salt_hex, dk_hex = stored.split(':', 1)
        salt = bytes.fromhex(salt_hex)
        dk   = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, _ITERATIONS)
        return hmac.compare_digest(dk.hex(), dk_hex)
    # Legacy: bare SHA-256 (no salt) — accepted so existing PINs keep working
    return hmac.compare_digest(hashlib.sha256(pin.encode()).hexdigest(), stored)


def get_all_active() -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, full_name, role FROM operators "
            "WHERE active=1 ORDER BY full_name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verify_pin(username: str, pin: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT pin FROM operators WHERE username=? AND active=1",
            (username,)
        ).fetchone()
        if not row or not row['pin']:
            return False
        stored = row['pin']
        if not _verify_hash(pin, stored):
            return False
        # Transparently upgrade legacy SHA-256 hashes to PBKDF2 on first successful login
        if ':' not in stored:
            conn.execute(
                "UPDATE operators SET pin=? WHERE username=?",
                (_hash_pin(pin), username)
            )
            conn.commit()
        return True
    finally:
        conn.close()


def set_pin(username: str, pin: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE operators SET pin=? WHERE username=?",
            (_hash_pin(pin), username)
        )
        conn.commit()
    finally:
        conn.close()


def has_any_pin() -> bool:
    """Return False if no operators have a PIN set — triggers first-run setup."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM operators WHERE pin IS NOT NULL AND active=1"
        ).fetchone()
        return row[0] > 0
    finally:
        conn.close()


# ── PIN lockout — persisted in the settings table so state survives restarts ──

def get_lockout_until(username: str) -> datetime | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"pin_lockout_{username}",)
        ).fetchone()
        return datetime.fromisoformat(row['value']) if row and row['value'] else None
    finally:
        conn.close()


def set_lockout(username: str, until: datetime):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"pin_lockout_{username}", until.isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def get_failed_attempts(username: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"pin_failed_{username}",)
        ).fetchone()
        return int(row['value']) if row and row['value'] else 0
    finally:
        conn.close()


def set_failed_attempts(username: str, n: int):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"pin_failed_{username}", str(n))
        )
        conn.commit()
    finally:
        conn.close()


def clear_lockout(username: str):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM settings WHERE key=?", (f"pin_lockout_{username}",))
        conn.execute("DELETE FROM settings WHERE key=?", (f"pin_failed_{username}",))
        conn.commit()
    finally:
        conn.close()


def record_failed_attempt(
    username: str, max_attempts: int, lockout_secs: int
) -> tuple[int, datetime | None]:
    """
    Increment the failed-attempt counter for username in a single DB transaction.

    If max_attempts is reached, also writes the lockout timestamp (before
    resetting the counter) so that a crash between the two writes leaves the
    lockout intact.

    Returns (new_attempt_count, lockout_until_datetime | None).
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"pin_failed_{username}",)
        ).fetchone()
        attempts = (int(row['value']) if row and row['value'] else 0) + 1

        if attempts >= max_attempts:
            until = datetime.now() + timedelta(seconds=lockout_secs)
            # Write lockout first so a crash between the two writes leaves it intact
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"pin_lockout_{username}", until.isoformat())
            )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, '0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'",
                (f"pin_failed_{username}",)
            )
            conn.commit()
            return attempts, until
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"pin_failed_{username}", str(attempts))
            )
            conn.commit()
            return attempts, None
    finally:
        conn.close()
