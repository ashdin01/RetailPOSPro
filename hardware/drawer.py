"""
Cash drawer.

The drawer has no connection of its own — it's wired via an RJ11/RJ12 cable
into the receipt printer's drawer-kick port, and pops open when the PRINTER
sends an ESC/POS pulse on that port. So this reuses hardware/printer.py's
existing network connection and settings (printer_enabled, printer_protocol,
printer_host/port) rather than having any of its own; with no networked
ESC/POS printer configured there, there is no port to send a kick signal on.

Pin 2 is the near-universal default for a single cash drawer wired through
an RJ11/RJ12 printer port. If a particular drawer doesn't open, its own
documentation will say if it needs pin 5 instead — change _KICK_PIN below.
"""
import logging

import hardware.printer as printer

_KICK_PIN = 2   # RJ11/RJ12 pin the drawer's kick wire is on — 2 or 5


def open_drawer():
    """Pulse the cash drawer open via the receipt printer's kick port.

    Never raises — logs and returns on any failure so a drawer/printer fault
    can't block the till from finishing a sale.
    """
    if not printer.is_enabled() or printer.get_protocol() != 'escpos':
        logging.info("[drawer] open_drawer — printing disabled/manual, no kick port available")
        return
    try:
        p = printer.get_escpos_client()
        p.cashdraw(_KICK_PIN)
        p.close()
        logging.info("[drawer] Kick pulse sent (pin %d)", _KICK_PIN)
    except Exception as e:
        logging.warning("[drawer] open_drawer failed: %s", e)
