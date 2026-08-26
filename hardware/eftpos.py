"""
EFTPOS terminal integration.

Supported protocols
-------------------
linkly   — Linkly (PC-EFTPOS) REST client.  Requires Linkly Client v1.x+
           running locally (or on a networked PC).  The Linkly service is
           the standard payment middleware used by CBA, NAB, ANZ, Westpac
           and most other Australian banks.
           Install: https://www.linkly.com.au/
           Default local URL: https://localhost:4443/

manual   — No software integration.  The POS shows the amount and waits
           for the cashier to confirm Approved / Declined on the physical
           terminal.  Use this if your terminal is not Linkly-connected.

Settings (DB settings table)
------------------------------
eftpos_enabled    '1' / '0'
eftpos_protocol   'linkly' | 'manual'
eftpos_host       e.g. 'localhost' or '192.168.1.50'
eftpos_port       '4443'  (Linkly default)
eftpos_username   Linkly pairing username
eftpos_password   Linkly pairing password
eftpos_merchant   Merchant ID / terminal number  (default '00')
"""

import base64
import logging
import time
import uuid

import requests

_TIMEOUT_CONNECT = 5    # seconds to establish connection
_TIMEOUT_POLL    = 120  # seconds to wait for terminal response
_POLL_INTERVAL   = 1.5  # seconds between status polls


# ── Result object ─────────────────────────────────────────────────────────────

class EFTPOSResult:
    APPROVED  = 'approved'
    DECLINED  = 'declined'
    CANCELLED = 'cancelled'
    ERROR     = 'error'

    def __init__(self, status: str, response_code: str = '',
                 message: str = '', receipt: str = '', reference: str = ''):
        self.status        = status
        self.response_code = response_code
        self.message       = message
        self.receipt       = receipt    # receipt text from terminal, if any
        self.reference     = reference  # bank reference / RRN

    @property
    def approved(self) -> bool:  return self.status == self.APPROVED
    @property
    def declined(self) -> bool:  return self.status == self.DECLINED
    @property
    def cancelled(self) -> bool: return self.status == self.CANCELLED
    @property
    def error(self) -> bool:     return self.status == self.ERROR

    def __repr__(self):
        return f"EFTPOSResult({self.status!r}, code={self.response_code!r}, msg={self.message!r})"


# ── Settings helpers ──────────────────────────────────────────────────────────

_cached_settings: dict | None = None


def _settings() -> dict:
    global _cached_settings
    if _cached_settings is not None:
        return _cached_settings
    from database.connection import get_connection
    from utils.credentials import get_credential
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'eftpos_%'"
        ).fetchall()
        s = {r['key']: (r['value'] or '') for r in rows}
    finally:
        conn.close()
    # Prefer keyring for the password; DB may hold an empty placeholder
    kr_pass = get_credential('eftpos_password')
    if kr_pass is not None:
        s['eftpos_password'] = kr_pass
    _cached_settings = s
    return s


def invalidate_settings_cache():
    """Clear the cached EFTPOS settings so the next call re-reads from DB and keyring."""
    global _cached_settings
    _cached_settings = None


def is_enabled() -> bool:
    s = _settings()
    return s.get('eftpos_enabled', '0') == '1'


def get_protocol() -> str:
    return _settings().get('eftpos_protocol', 'manual')


# ── Linkly REST client ────────────────────────────────────────────────────────

class _LinklyClient:
    """
    Thin wrapper around the Linkly Client REST API v2.

    Linkly Client must be installed, running, and paired with a terminal.
    The pairing username/password are set when you pair in Linkly Client.

    API base:   https://{host}:{port}/
    Auth:       Basic base64(username:password)  →  bearer token per session
    Purchase:   POST /api/v1/sessions/{sid}/transaction
    Poll:       GET  /api/v1/sessions/{sid}/transaction
    Cancel:     DELETE /api/v1/sessions/{sid}/transaction
    """

    def __init__(self, host: str, port: int, username: str,
                 password: str, merchant: str = '00'):
        self._base     = f"https://{host}:{port}"
        self._username = username
        self._password = password
        self._merchant = merchant
        self._session  = None   # (session_id, token) tuple
        # Disable SSL verification for self-signed Linkly localhost cert
        self._verify   = False

    # ── Session ───────────────────────────────────────────────────────

    def _authenticate(self) -> tuple[str, str]:
        """
        Obtain a session token.
        Returns (session_id, bearer_token).
        """
        creds = base64.b64encode(
            f"{self._username}:{self._password}".encode()
        ).decode()
        r = requests.post(
            f"{self._base}/api/v1/sessions",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/json"},
            json={},
            timeout=_TIMEOUT_CONNECT,
            verify=self._verify,
        )
        r.raise_for_status()
        data = r.json()
        session_id = data.get("SessionId") or data.get("sessionId", "")
        token      = data.get("Token")     or data.get("token",     "")
        if not session_id or not token:
            raise ValueError(f"Unexpected auth response: {data}")
        return session_id, token

    def _auth_headers(self, token: str) -> dict:
        return {"Authorization": token,
                "Content-Type":  "application/json"}

    # ── Transaction ───────────────────────────────────────────────────

    def purchase(self, amount_cents: int, txn_ref: str,
                 cancel_flag: list | None = None) -> EFTPOSResult:
        """
        Initiate a purchase and poll until complete.

        Parameters
        ----------
        amount_cents : int
            Amount in cents (e.g. 1250 for $12.50).
        txn_ref : str
            Unique reference for this transaction (up to 16 chars).
        cancel_flag : list
            Single-element list; set cancel_flag[0] = True from another
            thread to request cancellation.
        """
        try:
            session_id, token = self._authenticate()
        except Exception as e:
            logging.warning("[EFTPOS/Linkly] Auth failed: %s", e)
            return EFTPOSResult(EFTPOSResult.ERROR, message=f"Linkly auth failed: {e}")

        headers = self._auth_headers(token)

        payload = {
            "Request": {
                "Merchant":        self._merchant,
                "TxnType":         "P",          # Purchase
                "AmtPurchase":     amount_cents,
                "AmtCash":         0,
                "AmtTip":          0,
                "AuthCode":        "",
                "TxnRef":          txn_ref[:16],
                "ReceiptAutoPrint":"7",           # print on terminal
                "CutReceipt":      "0",
                "PanSource":       "",
                "Track2":          "",
                "AccountType":     "0",
                "Application":     "00",
            }
        }

        try:
            r = requests.post(
                f"{self._base}/api/v1/sessions/{session_id}/transaction",
                headers=headers,
                json=payload,
                timeout=_TIMEOUT_CONNECT,
                verify=self._verify,
            )
            r.raise_for_status()
        except Exception as e:
            logging.warning("[EFTPOS/Linkly] Transaction POST failed: %s", e)
            return EFTPOSResult(EFTPOSResult.ERROR, message=f"Terminal unavailable: {e}")

        # ── Poll for result ───────────────────────────────────────────
        deadline = time.time() + _TIMEOUT_POLL
        while time.time() < deadline:
            if cancel_flag and cancel_flag[0]:
                self._cancel(session_id, headers)
                return EFTPOSResult(EFTPOSResult.CANCELLED, message="Cancelled by operator")

            time.sleep(_POLL_INTERVAL)

            try:
                rp = requests.get(
                    f"{self._base}/api/v1/sessions/{session_id}/transaction",
                    headers=headers,
                    timeout=_TIMEOUT_CONNECT,
                    verify=self._verify,
                )
                rp.raise_for_status()
                data = rp.json()
            except Exception as e:
                logging.warning("[EFTPOS/Linkly] Poll error: %s", e)
                continue

            response = data.get("Response") or data.get("response") or {}
            if not response:
                continue  # still processing

            return self._parse_response(response)

        # Timed out — try to cancel
        self._cancel(session_id, headers)
        return EFTPOSResult(EFTPOSResult.ERROR, message="Terminal timed out — check terminal")

    def settle(self) -> EFTPOSResult:
        """Initiate end-of-day settlement."""
        try:
            session_id, token = self._authenticate()
        except Exception as e:
            return EFTPOSResult(EFTPOSResult.ERROR, message=f"Linkly auth failed: {e}")

        headers = self._auth_headers(token)
        payload = {"Request": {"Merchant": self._merchant, "TxnType": "S", "SettlementData": ""}}

        try:
            r = requests.post(
                f"{self._base}/api/v1/sessions/{session_id}/transaction",
                headers=headers, json=payload,
                timeout=_TIMEOUT_CONNECT, verify=self._verify,
            )
            r.raise_for_status()
        except Exception as e:
            return EFTPOSResult(EFTPOSResult.ERROR, message=f"Settlement request failed: {e}")

        deadline = time.time() + _TIMEOUT_POLL
        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            try:
                rp = requests.get(
                    f"{self._base}/api/v1/sessions/{session_id}/transaction",
                    headers=headers, timeout=_TIMEOUT_CONNECT, verify=self._verify,
                )
                rp.raise_for_status()
                data = rp.json()
            except Exception as e:
                logging.warning("[EFTPOS/Linkly] Settlement poll error: %s", e)
                continue
            response = data.get("Response") or data.get("response") or {}
            if not response:
                continue
            return self._parse_response(response)

        self._cancel(session_id, headers)
        return EFTPOSResult(EFTPOSResult.ERROR, message="Settlement timed out")

    def _cancel(self, session_id: str, headers: dict):
        try:
            requests.delete(
                f"{self._base}/api/v1/sessions/{session_id}/transaction",
                headers=headers,
                timeout=_TIMEOUT_CONNECT,
                verify=self._verify,
            )
        except Exception as e:
            logging.debug("[EFTPOS/Linkly] Cancel error: %s", e)

    @staticmethod
    def _parse_response(resp: dict) -> EFTPOSResult:
        code    = str(resp.get("ResponseCode", "")).strip()
        text    = str(resp.get("ResponseText", "")).strip()
        success = resp.get("Success", False)
        rrn     = str(resp.get("RRN", resp.get("AuthCode", ""))).strip()
        receipt = str(resp.get("ReceiptData", "")).strip()

        if success or code == "00":
            return EFTPOSResult(
                EFTPOSResult.APPROVED,
                response_code=code,
                message=text or "Approved",
                receipt=receipt,
                reference=rrn,
            )
        if code == "17":   # 17 = customer-initiated cancel on terminal
            return EFTPOSResult(
                EFTPOSResult.CANCELLED,
                response_code=code,
                message=text or "Cancelled",
            )
        # Code 75 = PIN tries exceeded (hard issuer decline) — falls through to DECLINED below
        return EFTPOSResult(
            EFTPOSResult.DECLINED,
            response_code=code,
            message=text or "Declined",
        )


# ── Public API ────────────────────────────────────────────────────────────────

def process_purchase(amount_cents: int, txn_ref: str = '',
                     cancel_flag: list | None = None) -> EFTPOSResult:
    """
    Process an EFTPOS purchase.

    For 'manual' protocol → returns EFTPOSResult.ERROR with
    status='manual_required' so the caller can show a confirmation dialog.

    For 'linkly' protocol → communicates with the Linkly client and returns
    the terminal's response.  Blocks until approved / declined / cancelled.
    Call from a background thread; pass cancel_flag to allow UI cancellation.
    """
    s        = _settings()
    protocol = s.get('eftpos_protocol', 'manual')

    if protocol == 'manual':
        return EFTPOSResult('manual_required')

    if protocol == 'linkly':
        ref = txn_ref or uuid.uuid4().hex[:16]
        client = _LinklyClient(
            host     = s.get('eftpos_host', 'localhost'),
            port     = int(s.get('eftpos_port', '4443') or '4443'),
            username = s.get('eftpos_username', ''),
            password = s.get('eftpos_password', ''),
            merchant = s.get('eftpos_merchant', '00') or '00',
        )
        return client.purchase(amount_cents, ref, cancel_flag)

    return EFTPOSResult(EFTPOSResult.ERROR, message=f"Unknown protocol: {protocol}")


def settle_terminal() -> EFTPOSResult:
    """
    Initiate an end-of-day EFTPOS settlement.

    For 'manual' protocol → returns a result with status='manual_required'
    so the caller can prompt the operator to settle on the physical terminal.
    For 'linkly' protocol → sends a settlement request and blocks until done.
    """
    s        = _settings()
    protocol = s.get('eftpos_protocol', 'manual')

    if protocol == 'manual':
        return EFTPOSResult('manual_required',
                            message="Manual mode — settle on terminal directly")

    if protocol == 'linkly':
        client = _LinklyClient(
            host     = s.get('eftpos_host', 'localhost'),
            port     = int(s.get('eftpos_port', '4443') or '4443'),
            username = s.get('eftpos_username', ''),
            password = s.get('eftpos_password', ''),
            merchant = s.get('eftpos_merchant', '00') or '00',
        )
        return client.settle()

    return EFTPOSResult(EFTPOSResult.ERROR, message=f"Unknown protocol: {protocol}")


def test_connection() -> tuple[bool, str]:
    """
    Test Linkly connectivity. Returns (success, message).
    Only meaningful for 'linkly' protocol.
    """
    s = _settings()
    if s.get('eftpos_protocol', 'manual') == 'manual':
        return True, "Manual mode — no connection test needed."

    try:
        client = _LinklyClient(
            host     = s.get('eftpos_host', 'localhost'),
            port     = int(s.get('eftpos_port', '4443') or '4443'),
            username = s.get('eftpos_username', ''),
            password = s.get('eftpos_password', ''),
        )
        client._authenticate()
        return True, f"Connected to Linkly at {client._base}"
    except Exception as e:
        return False, (
            f"Could not reach Linkly at {s.get('eftpos_host')}:{s.get('eftpos_port')}.\n\n"
            f"Error: {e}\n\n"
            "Ensure Linkly Client is running and the terminal is paired."
        )
