"""
Keyring-backed credential storage for sensitive settings (e.g. EFTPOS password).

On systems with a keyring (GNOME Keyring, macOS Keychain, Windows Credential Store)
credentials are stored outside the SQLite database. On headless systems where no
keyring is available every function degrades gracefully — get returns None, set
returns False — and callers fall back to DB storage with a logged warning.
"""
import logging

_SERVICE = "RetailPOSPro"


def get_credential(key: str) -> str | None:
    """Return the stored secret for key, or None if unavailable."""
    try:
        import keyring
        return keyring.get_password(_SERVICE, key)
    except Exception as e:
        logging.debug("[credentials] keyring get failed (%s)", e)
        return None


def set_credential(key: str, value: str) -> bool:
    """Store secret in the keyring. Returns True on success, False if unavailable."""
    try:
        import keyring
        keyring.set_password(_SERVICE, key, value)
        return True
    except Exception as e:
        logging.warning("[credentials] keyring unavailable (%s) — credential stored in DB", e)
        return False


def delete_credential(key: str):
    """Remove a stored secret from the keyring (best-effort)."""
    try:
        import keyring
        keyring.delete_password(_SERVICE, key)
    except Exception:
        pass
