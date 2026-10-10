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
#
# A weigh-and-label machine prints an EAN-13 with the item's PLU and either the
# weight or the total price embedded. Layouts differ between machines, so the
# positions are settings (all 0-based digit offsets into the 13 digits):
#
#   scale_label_enabled     '1' / '0'
#   scale_label_prefixes    comma-separated leading digits that mark a label, e.g. '2' or '20,21,22'
#   scale_label_plu_start   offset of the first PLU digit
#   scale_label_plu_len     number of PLU digits
#   scale_label_value_start offset of the first weight/price digit
#   scale_label_value_len   number of weight/price digits
#   scale_label_value_type  'weight' (grams) or 'price' (cents)
#   scale_label_check       '1' to reject labels whose EAN-13 check digit is wrong
#
# Defaults reproduce the original fixed layout (2 D PPPPP WWWWW C, weight).

SCALE_LABEL_DEFAULTS = {
    'scale_label_enabled':     '1',
    'scale_label_prefixes':    '2,02',
    'scale_label_plu_start':   '2',
    'scale_label_plu_len':     '5',
    'scale_label_value_start': '7',
    'scale_label_value_len':   '5',
    'scale_label_value_type':  'weight',
    'scale_label_check':       '0',
}

# name -> (prefixes, plu_start, plu_len, value_start, value_len); '2' = prefix digit
SCALE_LABEL_PRESETS = {
    '2 D PPPPP VVVVV C':  ('2', 2, 5, 7, 5),
    '2 PPPPPP VVVVV C':   ('2', 1, 6, 7, 5),
    '2 PPPPP VVVVVV C':   ('2', 1, 5, 6, 6),
    '2 PPPP VVVVVVV C':   ('2', 1, 4, 5, 7),
}


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


def ean13_check_ok(code: str) -> bool:
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(code[:12]))
    return (10 - total % 10) % 10 == int(code[12])


def decode_scale_label(barcode: str, cfg: dict | None = None) -> dict | None:
    """
    Decode a weigh-and-label EAN-13 using the configured layout.

    Returns {'plu': str, 'weight_kg': float | None, 'price': float | None}
    (exactly one of weight_kg / price is set, per scale_label_value_type), or
    None if the barcode isn't a scale label under the current settings.
    """
    if cfg is None:
        cfg = load_scale_label_config()
    c = {**SCALE_LABEL_DEFAULTS, **cfg}
    if c['scale_label_enabled'] != '1':
        return None
    barcode = (barcode or '').strip()
    if len(barcode) != 13 or not barcode.isdigit():
        return None
    prefixes = [p.strip() for p in c['scale_label_prefixes'].split(',') if p.strip()]
    if not any(barcode.startswith(p) for p in prefixes):
        return None
    try:
        plu_start, plu_len = int(c['scale_label_plu_start']), int(c['scale_label_plu_len'])
        val_start, val_len = int(c['scale_label_value_start']), int(c['scale_label_value_len'])
    except ValueError:
        return None
    if min(plu_start, plu_len, val_start, val_len) < 0 or plu_len == 0 or val_len == 0:
        return None
    if plu_start + plu_len > 12 or val_start + val_len > 12:
        return None
    if c['scale_label_check'] == '1' and not ean13_check_ok(barcode):
        return None

    plu = barcode[plu_start:plu_start + plu_len].lstrip('0') or '0'
    raw = int(barcode[val_start:val_start + val_len])
    if raw <= 0:
        return None
    if c['scale_label_value_type'] == 'price':
        return {'plu': plu, 'weight_kg': None, 'price': raw / 100.0}
    return {'plu': plu, 'weight_kg': raw / 1000.0, 'price': None}
