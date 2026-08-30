"""
Receipt / hold-ticket printer.

Supported protocols
--------------------
escpos   — ESC/POS over a raw TCP socket to the printer's network port
           (default 9100). Uses python-escpos's Network client — no
           driver install needed since it's a bare TCP connection.
manual   — No hardware integration; logs only. Use when no printer is
           installed, or as a safe default so the POS never blocks on
           printer failures.

Settings (DB settings table)
------------------------------
printer_enabled   '1' / '0'
printer_protocol  'escpos' | 'manual'
printer_host      e.g. '192.168.1.60'
printer_port      '9100'  (ESC/POS network default)

USB printers are not supported in this version — python-escpos's Usb()
class requires pyusb + a platform USB backend (libusb), which is more
fragile to deploy across multiple physical terminals than a network-attached
printer at a fixed IP. Network is the only wired path implemented; add a
Usb() branch here later if a store needs it.
"""
import logging

_cached_settings: dict | None = None


def _settings() -> dict:
    global _cached_settings
    if _cached_settings is not None:
        return _cached_settings
    from database.connection import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'printer_%'"
        ).fetchall()
        _cached_settings = {r['key']: (r['value'] or '') for r in rows}
    finally:
        conn.close()
    return _cached_settings


def invalidate_settings_cache():
    """Clear the cached printer settings so the next call re-reads from DB."""
    global _cached_settings
    _cached_settings = None


def is_enabled() -> bool:
    return _settings().get('printer_enabled', '0') == '1'


def get_protocol() -> str:
    return _settings().get('printer_protocol', 'manual')


def _get_escpos_client():
    from escpos.printer import Network
    s = _settings()
    return Network(s.get('printer_host', ''), port=int(s.get('printer_port', '9100') or '9100'))


def print_receipt(txn: dict):
    """
    Print a completed-sale receipt.

    Never raises — logs and returns on any failure so a printer fault can't
    block the till from finishing a sale.
    """
    if not is_enabled() or get_protocol() != 'escpos':
        logging.info("[printer] Receipt for %s — printing disabled/manual", txn.get('reference'))
        return
    try:
        p = _get_escpos_client()
        p.set(align='center', bold=True)
        p.text(f"{txn.get('reference', '')}\n")
        p.set(align='left', bold=False)
        for item in txn.get('items', []):
            line_total = item.get('line_total', round(item['qty'] * item['unit_price'], 2))
            p.text(f"{item['qty']:g} x {item['description']}  {line_total:.2f}\n")
        p.text(f"\nTOTAL: {txn.get('total', 0):.2f}\n")
        p.cut()
        p.close()
    except Exception as e:
        logging.warning("[printer] print_receipt(%s) failed: %s", txn.get('reference'), e)


def print_hold_ticket(reference: str, items: list, total: float):
    """
    Print a hold ticket: basket summary + a scannable Code128 barcode of
    `reference`.

    python-escpos's .barcode() emits the native ESC/POS barcode command
    (GS k) so the printer renders the barcode itself — no QR/image
    generation library needed. Never raises — logs and returns on failure.
    """
    if not is_enabled() or get_protocol() != 'escpos':
        logging.info("[printer] Hold ticket %s — printing disabled/manual", reference)
        return
    try:
        p = _get_escpos_client()
        p.set(align='center', bold=True)
        p.text("SALE ON HOLD\n")
        p.set(bold=False)
        for item in items:
            p.text(f"{item['qty']:g} x {item['description']}\n")
        p.text(f"\nTOTAL: {total:.2f}\n\n")
        # Signature verified against python-escpos 3.1 (pinned via requirements.txt):
        # barcode(code, bc, height=64, width=3, pos='BELOW', font='A', align_ct=True, ...).
        p.barcode(reference, 'CODE128', height=64, width=2, pos='BELOW', align_ct=True)
        p.cut()
        p.close()
    except Exception as e:
        logging.warning("[printer] print_hold_ticket(%s) failed: %s", reference, e)


def test_connection() -> tuple:
    """
    Test connectivity to the configured ESC/POS printer.
    Returns (success, message). Only meaningful for 'escpos' protocol.
    """
    if get_protocol() != 'escpos':
        return True, "Manual mode — no connection test needed."
    s = _settings()
    try:
        p = _get_escpos_client()
        p.text("Test print - connection OK\n")
        p.cut()
        p.close()
        return True, f"Connected to printer at {s.get('printer_host')}:{s.get('printer_port')}"
    except Exception as e:
        return False, (
            f"Could not reach printer at {s.get('printer_host')}:{s.get('printer_port')}.\n\n"
            f"Error: {e}\n\n"
            "Check the printer is powered on and network-reachable."
        )
