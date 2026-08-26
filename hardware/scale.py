"""
Serial scale interface.

Reads weight from a scale connected via RS-232 or USB-serial (e.g. /dev/ttyUSB0).

Supported protocols
-------------------
auto        — send "SI\\r\\n" (MT-SICS immediate weight), then fall back to
              reading whatever the scale outputs continuously.  Works with
              most commercial scales in polling or continuous mode.
mettler     — MT-SICS protocol: send "SI\\r\\n", parse "S S +  1.234 kg" response.
cas         — CAS/A&D simple protocol: read a line, parse the first number found.
continuous  — Don't send any command; just read one line (for scales already in
              continuous/auto-send mode).

Settings (read from the DB settings table)
------------------------------------------
scale_enabled  '1' / '0'
scale_port     e.g. '/dev/ttyUSB0' or 'COM3'
scale_baud     e.g. '9600'  (default)
scale_protocol 'auto' | 'mettler' | 'cas' | 'continuous'  (default 'auto')
"""

import re
import logging

_DEFAULT_BAUD     = 9600
_DEFAULT_PROTOCOL = 'auto'
_READ_TIMEOUT     = 2.0   # seconds


def _get_scale_settings() -> dict:
    from database.connection import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'scale_%'"
        ).fetchall()
        return {r['key']: (r['value'] or '') for r in rows}
    finally:
        conn.close()


def is_enabled() -> bool:
    s = _get_scale_settings()
    return s.get('scale_enabled', '0') == '1' and bool(s.get('scale_port', '').strip())


def read_weight() -> float | None:
    """
    Read the current stable weight from the configured scale.

    Returns weight in kg (positive float) or None on any error.
    """
    try:
        import serial
    except ImportError:
        logging.warning("[Scale] pyserial not installed — cannot read scale")
        return None

    s = _get_scale_settings()
    port     = s.get('scale_port', '').strip()
    baud     = int(s.get('scale_baud', str(_DEFAULT_BAUD)) or _DEFAULT_BAUD)
    protocol = s.get('scale_protocol', _DEFAULT_PROTOCOL).lower() or _DEFAULT_PROTOCOL

    if not port:
        logging.warning("[Scale] No port configured")
        return None

    try:
        with serial.Serial(port, baud, timeout=_READ_TIMEOUT) as ser:
            ser.reset_input_buffer()

            if protocol in ('auto', 'mettler'):
                ser.write(b'SI\r\n')     # MT-SICS: Send Immediate weight
            # continuous / cas: don't send anything, just read

            line = ser.readline().decode('ascii', errors='ignore').strip()

            if not line and protocol == 'auto':
                # Scale didn't respond to SI — try reading continuous output
                ser.write(b'\r\n')
                line = ser.readline().decode('ascii', errors='ignore').strip()

    except Exception as e:
        logging.warning("[Scale] Serial error on %s: %s", port, e)
        return None

    return _parse_weight(line)


def _parse_weight(line: str) -> float | None:
    """
    Extract weight (kg) from a scale output line.

    Handles common formats:
      MT-SICS stable:   "S S +   1.234 kg"
      MT-SICS unstable: "S D +   1.234 kg"  (dynamic — still usable)
      CAS / A&D:        "+001.234KG" or "+001234G" or "001.234"
      Generic:          any string containing a decimal number
    """
    if not line:
        return None

    # MT-SICS: starts with "S " — extract value (ignore stable/dynamic indicator)
    if line.upper().startswith('S '):
        m = re.search(r'[+-]?\s*(\d+\.?\d*)\s*kg', line, re.IGNORECASE)
        if m:
            return abs(float(m.group(1)))
        m = re.search(r'[+-]?\s*(\d+\.?\d*)\s*g\b', line, re.IGNORECASE)
        if m:
            return abs(float(m.group(1))) / 1000.0

    # Explicit kg unit — trust it regardless of magnitude
    m_kg = re.search(r'[+-]?\s*(\d+\.?\d*)\s*kg', line, re.IGNORECASE)
    if m_kg:
        return abs(float(m_kg.group(1)))

    # Explicit gram unit — convert to kg
    m_g = re.search(r'[+-]?\s*(\d+\.?\d*)\s*g\b', line, re.IGNORECASE)
    if m_g:
        return abs(float(m_g.group(1))) / 1000.0

    # No unit suffix: apply a heuristic on the first decimal number found.
    # Retail scales in continuous/unitless mode output either small decimals
    # (e.g. 1.234, meaning kg) or large integers (e.g. 1234, meaning grams).
    # The 5000 threshold (~5 kg) is reasonable for retail produce; it is NOT
    # suitable for industrial scales or scales configured to output lbs.
    m = re.search(r'[+-]?\s*(\d+\.?\d+)', line)
    if m:
        val = abs(float(m.group(1)))
        return val / 1000.0 if val > 5000 else val

    return None


def test_connection() -> tuple[bool, str]:
    """
    Attempt to open the port and read one weight.

    Returns (success: bool, message: str)
    """
    s = _get_scale_settings()
    port = s.get('scale_port', '').strip()
    if not port:
        return False, "No scale port configured."

    weight = read_weight()
    if weight is None:
        return False, f"Could not read weight from {port}.\nCheck port, baud rate, and cable."
    return True, f"Scale connected on {port}.\nReading: {weight:.3f} kg"
