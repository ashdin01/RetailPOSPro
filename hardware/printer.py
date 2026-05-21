"""
Receipt printer stub.
Replace with ESC/POS or platform-specific implementation.
"""
import logging


def print_receipt(txn: dict):
    """Print a receipt for the given transaction dict."""
    logging.info(f"[printer] Receipt for {txn.get('reference')} — not yet implemented")
