"""
Barcode utility functions.

Random-weight EAN-13 barcodes
------------------------------
Supermarket scales print EAN-13 barcodes with weight or price embedded.
The first digit is '2' (GS1 internal-use prefix).

Standard format used by most Australian supermarket scales:

  2  D  PPPPP  WWWWW  C     (13 digits total)
  0  1  2···6  7···11  12

  D     — department/zone (1 digit, informational only)
  PPPPP — PLU code, zero-padded to 5 digits
  WWWWW — weight in grams, zero-padded to 5 digits
            e.g. 00625 → 0.625 kg, 01500 → 1.500 kg
  C     — EAN-13 check digit (not validated here; scanner already checks it)
"""


def decode_weight_barcode(barcode: str) -> dict | None:
    """
    Decode a random-weight EAN-13 barcode.

    Returns
    -------
    {'plu': str, 'weight_kg': float}  if this is a weight barcode
    None                               if not a weight barcode / invalid
    """
    if len(barcode) != 13 or not barcode.isdigit() or barcode[0] != '2':
        return None

    plu = barcode[2:7].lstrip('0') or '0'
    try:
        weight_grams = int(barcode[7:12])
    except ValueError:
        return None

    weight_kg = weight_grams / 1000.0
    if weight_kg <= 0:
        return None

    return {'plu': plu, 'weight_kg': weight_kg}


# ── Configurable scale-label barcodes ────────────────────────────────────────
# Pure codec lives in utils/scale_label.py (byte-identical in BackOfficePro and
# RetailPOSPro). This module only adds the settings loader and keeps the
# long-standing names importable.
from utils.scale_label import (  # noqa: F401
    SCALE_LABEL_DEFAULTS, SCALE_LABEL_PRESETS, decode_label, encode_label,
    ean13_check_ok, ean13_check_digit, price_for_weight,
)


def load_scale_label_config() -> dict:
    """Scale-label settings from the local DB, with defaults filled in."""
    from database.connection import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'scale_label_%'"
        ).fetchall()
    finally:
        conn.close()
    cfg = dict(SCALE_LABEL_DEFAULTS)
    for r in rows:
        if (r['value'] or '').strip() != '':
            cfg[r['key']] = r['value'].strip()
    return cfg


def decode_scale_label(barcode: str, cfg: dict | None = None) -> dict | None:
    """Decode a weigh-and-label EAN-13 using the saved (or given) layout."""
    return decode_label(barcode, load_scale_label_config() if cfg is None else cfg)


def encode_scale_label(plu, *, price: float | None = None,
                       weight_kg: float | None = None, cfg: dict | None = None) -> str:
    """Build a weigh-and-label EAN-13 using the saved (or given) layout."""
    return encode_label(plu, price=price, weight_kg=weight_kg,
                        cfg=load_scale_label_config() if cfg is None else cfg)
