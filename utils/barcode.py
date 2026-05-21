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
