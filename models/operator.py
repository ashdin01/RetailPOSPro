import hashlib
from database.connection import get_connection


def _hash(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


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
        return row['pin'] == _hash(pin)
    finally:
        conn.close()


def set_pin(username: str, pin: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE operators SET pin=? WHERE username=?",
            (_hash(pin), username)
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
