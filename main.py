"""
RetailPOSPro — entry point.
"""
import sys
import os
import logging
import subprocess
import requests

# Allow imports from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication

# ── Logging ───────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'retailpos.log')),
        logging.StreamHandler(sys.stdout),
    ]
)

from database.connection import get_connection
from database.schema import setup as setup_db
import models.shift as shift_model

_BOP_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'BackOfficePro')
)


def _ensure_api_running():
    """Start the BackOfficePro API server if it isn't already listening."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='backoffice_url'"
        ).fetchone()
        api_url = (row['value'] if row else 'http://localhost:5050').rstrip('/')
    finally:
        conn.close()

    try:
        import api.backoffice_client as _bop
        r = requests.get(f"{api_url}/api/v1/health", timeout=2,
                         verify=_bop._ssl_verify())
        if r.status_code == 200 and r.json().get('status') == 'ok':
            logging.info("[API] BackOfficePro API already running at %s", api_url)
            return
    except Exception:
        pass

    python = os.path.join(_BOP_DIR, 'venv', 'bin', 'python')
    if not os.path.exists(python):
        python = sys.executable

    log_path = os.path.join(_BOP_DIR, 'data', 'api_server.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    try:
        with open(log_path, 'a') as log_fh:
            subprocess.Popen(
                [python, 'api_server.py'],
                cwd=_BOP_DIR,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,   # detach — survives RetailPOSPro exit
            )
        logging.info("[API] BackOfficePro API server started (log: %s)", log_path)
    except Exception as e:
        logging.warning("[API] Could not start BackOfficePro API: %s", e)


def _init_db():
    conn = get_connection()
    try:
        setup_db(conn)
    finally:
        conn.close()


def _run():
    _init_db()
    _ensure_api_running()
    app = QApplication(sys.argv)
    app.setApplicationName("RetailPOSPro")
    app.setStyle("Fusion")

    # Apply a global dark palette
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#1a2332"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#e6edf3"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#1e2a38"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1b2738"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#e6edf3"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#1e2a38"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#e6edf3"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#1565c0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    _windows = {}
    _customer_display = {'window': None}

    def _read_customer_display_settings():
        conn = get_connection()
        try:
            rows = {
                r['key']: r['value'] for r in conn.execute(
                    "SELECT key, value FROM settings WHERE key IN "
                    "('customer_display_ads_dir', 'customer_display_idle_secs')"
                ).fetchall()
            }
        finally:
            conn.close()
        ads_dir = rows.get('customer_display_ads_dir') or ''
        try:
            idle_secs = int(rows.get('customer_display_idle_secs') or 20)
        except ValueError:
            idle_secs = 20
        return ads_dir, idle_secs

    def _secondary_screen():
        primary = app.primaryScreen()
        others = [s for s in app.screens() if s is not primary]
        return others[0] if others else None

    def _sync_customer_display():
        """Open/close the customer-facing display as a second monitor is
        plugged in or unplugged, so no restart is needed to activate it."""
        screen = _secondary_screen()
        win = _customer_display['window']
        if screen is None:
            if win is not None:
                win.close()
                _customer_display['window'] = None
            return
        if win is None:
            from views.customer_display import CustomerDisplay
            ads_dir, idle_secs = _read_customer_display_settings()
            win = CustomerDisplay(ads_dir=ads_dir, idle_secs=idle_secs)
            _customer_display['window'] = win
        win.setGeometry(screen.geometry())
        win.showFullScreen()

    def _push_to_customer_display(items, subtotal, gst, total):
        win = _customer_display['window']
        if win is not None:
            win.show_basket(items, subtotal, gst, total)

    app.screenAdded.connect(lambda _: _sync_customer_display())
    app.screenRemoved.connect(lambda _: _sync_customer_display())
    _sync_customer_display()

    def _on_login(operator: dict):
        from views.login_screen import LoginScreen
        shift_id = None
        try:
            existing = shift_model.get_open_shift(operator['id'])
            if existing:
                shift_id = existing['id']
            else:
                op_name = operator.get('full_name') or operator.get('username', '')
                from views.float_dialog import OpeningFloatDialog
                float_dlg = OpeningFloatDialog(operator_name=op_name)
                float_dlg.exec()
                shift_id = shift_model.open_shift(
                    operator['id'],
                    op_name,
                    float_open=float_dlg.float_amount,
                    float_open_variance=float_dlg.float_variance,
                )
        except Exception as e:
            logging.warning("Shift management error: %s", e)

        from views.pos_screen import POSScreen
        pos = POSScreen(operator=operator, shift_id=shift_id)

        def _on_lock():
            pos.hide()
            login = LoginScreen(on_login=_on_login)
            login.show()
            _windows['login'] = login

        pos.lock_requested.connect(_on_lock)
        pos.basket_changed.connect(_push_to_customer_display)
        pos.show()
        _windows['pos'] = pos

        if 'login' in _windows:
            _windows['login'].hide()

    from views.login_screen import LoginScreen
    login = LoginScreen(on_login=_on_login)
    login.show()
    _windows['login'] = login

    sys.exit(app.exec())


if __name__ == "__main__":
    _run()
