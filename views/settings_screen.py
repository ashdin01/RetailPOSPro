"""
POS settings screen.
Main screen shows three category buttons; each opens a full-screen sub-dialog.
Admin-only; opened from the main POS via a long-press or admin menu.
"""
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QFormLayout, QPushButton, QLabel,
    QLineEdit, QMessageBox, QGroupBox, QHBoxLayout, QComboBox, QCheckBox,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QEvent, QObject
from database.connection import get_connection
from config.settings import TOUCH_BTN_HEIGHT
import api.backoffice_client as bop

_TERMINAL_FIELDS = [
    ("store_name",      "Store Name",           "e.g. My Supermarket"),
    ("terminal_id",     "Terminal ID",          "e.g. POS-001"),
    ("backoffice_url",  "BackOfficePro URL",    "e.g. http://192.168.1.100:5050"),
    ("receipt_header",  "Receipt Header",       "Printed at top of receipts"),
    ("receipt_footer",  "Receipt Footer",       "e.g. Thank you for shopping with us!"),
    ("cache_sync_mins", "Cache Sync (minutes)", "How often to sync products from BackOfficePro"),
    ("expected_float",  "Expected Opening Float", "e.g. 300.00  — shown at shift start"),
]

_BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
_PROTOCOLS  = [
    ("auto",       "Auto (MT-SICS + fallback)"),
    ("mettler",    "Mettler Toledo MT-SICS"),
    ("cas",        "CAS / A&D simple"),
    ("continuous", "Continuous output (no command)"),
]

_BASE_STYLE   = "QDialog, QWidget { background: #1a2332; color: #e6edf3; }"
_EDIT_STYLE   = """
    QLineEdit { background: #1e2a38; color: #e6edf3;
                border: 1px solid #2a3a4a; border-radius: 6px;
                padding: 0 10px; font-size: 14px; }
    QLineEdit:focus { border-color: #1565c0; }
"""
_COMBO_STYLE  = """
    QComboBox { background: #1e2a38; color: #e6edf3;
                border: 1px solid #2a3a4a; border-radius: 6px;
                padding: 0 10px; font-size: 14px; }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView { background: #1e2a38; color: #e6edf3;
                                  selection-background-color: #1565c0; }
"""


# ── Shared helpers ────────────────────────────────────────────────────────────

class _FocusRouter(QObject):
    def __init__(self, kb, parent=None):
        super().__init__(parent)
        self._kb = kb

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusIn and isinstance(obj, QLineEdit):
            self._kb.show_for(obj)
            self._kb._reposition()
        return False


def _load_settings() -> dict:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r['key']: r['value'] or '' for r in rows}
    finally:
        conn.close()


def _save_settings(pairs: list[tuple[str, str]]):
    conn = get_connection()
    try:
        for k, v in pairs:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v)
            )
        conn.commit()
    finally:
        conn.close()


def _bottom_bar(dialog, save_slot) -> QWidget:
    bar = QWidget()
    bar.setStyleSheet("background: #141e2b; border-top: 1px solid #2a3a4a;")
    row = QHBoxLayout(bar)
    row.setContentsMargins(28, 12, 28, 12)
    back_btn = QPushButton("← Back")
    back_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
    back_btn.clicked.connect(dialog.accept)
    save_btn = QPushButton("Save")
    save_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
    save_btn.setStyleSheet("""
        QPushButton { background: #1565c0; color: white; border: none;
                      border-radius: 6px; font-size: 15px; font-weight: bold; }
        QPushButton:hover { background: #1976d2; }
    """)
    save_btn.clicked.connect(save_slot)
    row.addWidget(back_btn)
    row.addStretch()
    row.addWidget(save_btn)
    return bar


# ── Terminal settings sub-dialog ──────────────────────────────────────────────

class TerminalSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terminal Settings")
        self.setModal(True)
        self._fields: dict[str, QLineEdit] = {}
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()
        self._load()
        self._setup_keyboard()
        self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_kb') and self._kb.isVisible():
            self._kb._reposition()

    def _setup_keyboard(self):
        from views.widgets.keyboard import VirtualKeyboard
        self._kb = VirtualKeyboard(self)
        router = _FocusRouter(self._kb, self)
        for edit in self._fields.values():
            edit.installEventFilter(router)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        title = QLabel("Terminal Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        lay.addWidget(title)

        box = QGroupBox()
        form = QFormLayout(box)
        form.setSpacing(12)
        for key, label, placeholder in _TERMINAL_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(TOUCH_BTN_HEIGHT)
            edit.setStyleSheet(_EDIT_STYLE)
            self._fields[key] = edit
            form.addRow(label + ":", edit)
        lay.addWidget(box)

        test_row = QHBoxLayout()
        test_btn = QPushButton("Test BackOfficePro Connection")
        test_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        test_btn.clicked.connect(self._test_connection)
        test_row.addWidget(test_btn)
        test_row.addStretch()
        lay.addLayout(test_row)

        lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(_bottom_bar(self, self._save))

    def _load(self):
        vals = _load_settings()
        for key, edit in self._fields.items():
            edit.setText(vals.get(key, ''))

    def _save(self):
        pairs = [(k, e.text().strip()) for k, e in self._fields.items()]
        _save_settings(pairs)
        QMessageBox.information(self, "Saved", "Terminal settings saved.")

    def _test_connection(self):
        url = self._fields['backoffice_url'].text().strip()
        if not url:
            QMessageBox.warning(self, "Test Connection", "Enter a BackOfficePro URL first.")
            return
        _save_settings([('backoffice_url', url)])
        if bop.check_health():
            QMessageBox.information(self, "Connection OK",
                f"Successfully connected to BackOfficePro at:\n{url}")
        else:
            QMessageBox.warning(self, "Connection Failed",
                f"Could not reach BackOfficePro at:\n{url}\n\n"
                "Check that the API server is running (start_api.sh) and "
                "the URL and port are correct.")


# ── Scale settings sub-dialog ─────────────────────────────────────────────────

class ScaleSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scale Settings")
        self.setModal(True)
        self._fields: dict[str, QLineEdit] = {}
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()
        self._load()
        self._setup_keyboard()
        self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_kb') and self._kb.isVisible():
            self._kb._reposition()

    def _setup_keyboard(self):
        from views.widgets.keyboard import VirtualKeyboard
        self._kb = VirtualKeyboard(self)
        router = _FocusRouter(self._kb, self)
        for edit in self._fields.values():
            edit.installEventFilter(router)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        title = QLabel("Scale Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        lay.addWidget(title)

        box = QGroupBox()
        form = QFormLayout(box)
        form.setSpacing(12)

        self._scale_enabled_chk = QCheckBox("Scale connected (enable weight reading)")
        self._scale_enabled_chk.setStyleSheet("font-size: 14px;")
        self._scale_enabled_chk.stateChanged.connect(self._on_enabled_changed)
        form.addRow("", self._scale_enabled_chk)

        self._port_edit = QLineEdit()
        self._port_edit.setPlaceholderText("e.g. /dev/ttyUSB0 or COM3")
        self._port_edit.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._port_edit.setStyleSheet(_EDIT_STYLE)
        self._fields['scale_port'] = self._port_edit
        form.addRow("Serial Port:", self._port_edit)

        self._baud_combo = QComboBox()
        self._baud_combo.addItems(_BAUD_RATES)
        self._baud_combo.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._baud_combo.setStyleSheet(_COMBO_STYLE)
        form.addRow("Baud Rate:", self._baud_combo)

        self._proto_combo = QComboBox()
        for key, label in _PROTOCOLS:
            self._proto_combo.addItem(label, key)
        self._proto_combo.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._proto_combo.setStyleSheet(_COMBO_STYLE)
        form.addRow("Protocol:", self._proto_combo)

        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Test Scale Connection")
        self._test_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._test_btn.clicked.connect(self._test_scale)
        test_row.addWidget(self._test_btn)
        test_row.addStretch()
        form.addRow("", test_row)

        lay.addWidget(box)
        lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(_bottom_bar(self, self._save))

    def _on_enabled_changed(self, state):
        enabled = state == Qt.CheckState.Checked.value
        self._port_edit.setEnabled(enabled)
        self._baud_combo.setEnabled(enabled)
        self._proto_combo.setEnabled(enabled)
        self._test_btn.setEnabled(enabled)

    def _load(self):
        vals = _load_settings()
        self._port_edit.setText(vals.get('scale_port', ''))
        self._scale_enabled_chk.setChecked(vals.get('scale_enabled', '0') == '1')
        self._on_enabled_changed(
            Qt.CheckState.Checked.value if vals.get('scale_enabled') == '1'
            else Qt.CheckState.Unchecked.value
        )
        baud = vals.get('scale_baud', '9600')
        idx = self._baud_combo.findText(baud)
        self._baud_combo.setCurrentIndex(idx if idx >= 0 else 3)
        proto = vals.get('scale_protocol', 'auto')
        for i in range(self._proto_combo.count()):
            if self._proto_combo.itemData(i) == proto:
                self._proto_combo.setCurrentIndex(i)
                break

    def _save(self):
        _save_settings([
            ('scale_port',     self._port_edit.text().strip()),
            ('scale_baud',     self._baud_combo.currentText()),
            ('scale_protocol', self._proto_combo.currentData()),
            ('scale_enabled',  '1' if self._scale_enabled_chk.isChecked() else '0'),
        ])
        QMessageBox.information(self, "Saved", "Scale settings saved.")

    def _test_scale(self):
        _save_settings([
            ('scale_port',     self._port_edit.text().strip()),
            ('scale_baud',     self._baud_combo.currentText()),
            ('scale_protocol', self._proto_combo.currentData()),
            ('scale_enabled',  '1'),
        ])
        from hardware.scale import test_connection
        ok, msg = test_connection()
        if ok:
            QMessageBox.information(self, "Scale OK", msg)
        else:
            QMessageBox.warning(self, "Scale Error", msg)


# ── EFTPOS settings sub-dialog ────────────────────────────────────────────────

class EftposSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EFTPOS Settings")
        self.setModal(True)
        self._fields: dict[str, QLineEdit] = {}
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()
        self._load()
        self._setup_keyboard()
        self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_kb') and self._kb.isVisible():
            self._kb._reposition()

    def _setup_keyboard(self):
        from views.widgets.keyboard import VirtualKeyboard
        self._kb = VirtualKeyboard(self)
        router = _FocusRouter(self._kb, self)
        for edit in self._fields.values():
            edit.installEventFilter(router)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        title = QLabel("EFTPOS Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        lay.addWidget(title)

        box = QGroupBox()
        form = QFormLayout(box)
        form.setSpacing(12)

        self._enabled_chk = QCheckBox("EFTPOS terminal connected")
        self._enabled_chk.setStyleSheet("font-size: 14px;")
        self._enabled_chk.stateChanged.connect(self._on_enabled_changed)
        form.addRow("", self._enabled_chk)

        self._proto_combo = QComboBox()
        self._proto_combo.addItem("Manual confirmation (no software link)", "manual")
        self._proto_combo.addItem("Linkly (PC-EFTPOS) — CBA, NAB, ANZ, Westpac…", "linkly")
        self._proto_combo.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._proto_combo.setStyleSheet(_COMBO_STYLE)
        self._proto_combo.currentIndexChanged.connect(self._on_proto_changed)
        form.addRow("Protocol:", self._proto_combo)

        # Linkly-specific fields
        self._linkly_widget = QWidget()
        linkly_form = QFormLayout(self._linkly_widget)
        linkly_form.setContentsMargins(0, 0, 0, 0)
        linkly_form.setSpacing(10)

        def _ef(placeholder):
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFixedHeight(TOUCH_BTN_HEIGHT)
            e.setStyleSheet(_EDIT_STYLE)
            return e

        self._host_edit     = _ef("e.g. localhost or 192.168.1.50")
        self._port_edit     = _ef("4443")
        self._user_edit     = _ef("Linkly pairing username")
        self._pass_edit     = _ef("Linkly pairing password")
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._merchant_edit = _ef("00")

        self._fields['eftpos_host']     = self._host_edit
        self._fields['eftpos_port']     = self._port_edit
        self._fields['eftpos_username'] = self._user_edit
        self._fields['eftpos_password'] = self._pass_edit
        self._fields['eftpos_merchant'] = self._merchant_edit

        linkly_form.addRow("Linkly Host:",  self._host_edit)
        linkly_form.addRow("Linkly Port:",  self._port_edit)
        linkly_form.addRow("Username:",     self._user_edit)
        linkly_form.addRow("Password:",     self._pass_edit)
        linkly_form.addRow("Merchant ID:",  self._merchant_edit)
        form.addRow("", self._linkly_widget)

        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Test EFTPOS Connection")
        self._test_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._test_btn.clicked.connect(self._test_eftpos)
        test_row.addWidget(self._test_btn)
        test_row.addStretch()
        form.addRow("", test_row)

        lay.addWidget(box)
        lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(_bottom_bar(self, self._save))

    def _on_enabled_changed(self, state):
        enabled = state == Qt.CheckState.Checked.value
        self._proto_combo.setEnabled(enabled)
        self._test_btn.setEnabled(enabled)
        if enabled:
            self._on_proto_changed()
        else:
            self._linkly_widget.setVisible(False)

    def _on_proto_changed(self, *_):
        self._linkly_widget.setVisible(
            self._proto_combo.currentData() == 'linkly'
        )

    def _load(self):
        vals = _load_settings()
        for key, edit in self._fields.items():
            edit.setText(vals.get(key, ''))
        self._enabled_chk.setChecked(vals.get('eftpos_enabled', '0') == '1')
        self._on_enabled_changed(
            Qt.CheckState.Checked.value if vals.get('eftpos_enabled') == '1'
            else Qt.CheckState.Unchecked.value
        )
        ep = vals.get('eftpos_protocol', 'manual')
        for i in range(self._proto_combo.count()):
            if self._proto_combo.itemData(i) == ep:
                self._proto_combo.setCurrentIndex(i)
                break

    def _save(self):
        pairs = [(k, e.text().strip()) for k, e in self._fields.items()]
        pairs += [
            ('eftpos_enabled',  '1' if self._enabled_chk.isChecked() else '0'),
            ('eftpos_protocol', self._proto_combo.currentData()),
        ]
        _save_settings(pairs)
        QMessageBox.information(self, "Saved", "EFTPOS settings saved.")

    def _test_eftpos(self):
        _save_settings([
            ('eftpos_host',     self._host_edit.text().strip()),
            ('eftpos_port',     self._port_edit.text().strip()),
            ('eftpos_username', self._user_edit.text().strip()),
            ('eftpos_password', self._pass_edit.text().strip()),
            ('eftpos_merchant', self._merchant_edit.text().strip()),
            ('eftpos_protocol', self._proto_combo.currentData()),
            ('eftpos_enabled',  '1'),
        ])
        from hardware.eftpos import test_connection as eftpos_test
        ok, msg = eftpos_test()
        if ok:
            QMessageBox.information(self, "EFTPOS OK", msg)
        else:
            QMessageBox.warning(self, "EFTPOS Error", msg)


# ── Thin proxy so EODDialog fits the same dialog_cls pattern ─────────────────

class _EODDialogProxy:
    """Instantiated by the menu button; delegates straight to EODDialog."""
    def __init__(self, parent=None):
        from views.eod_dialog import EODDialog
        self._dlg = EODDialog(parent)

    def exec(self):
        return self._dlg.exec()


# ── Main settings screen ──────────────────────────────────────────────────────

class SettingsScreen(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("POS Settings")
        self.setModal(True)
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()
        self.showFullScreen()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 32, 28, 0)
        outer.setSpacing(16)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 22px; font-weight: bold; margin-bottom: 8px;")
        outer.addWidget(title)

        for icon, label, dialog_cls in [
            ("⚙",  "Terminal Settings",  TerminalSettingsDialog),
            ("⚖",  "Scale Settings",     ScaleSettingsDialog),
            ("💳", "EFTPOS Settings",    EftposSettingsDialog),
            ("📋", "End of Day",         _EODDialogProxy),
        ]:
            btn = QPushButton(f"  {icon}   {label}")
            btn.setFixedHeight(TOUCH_BTN_HEIGHT * 2)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1e2a38; color: #e6edf3;
                    border: 1px solid #2a3a4a; border-radius: 8px;
                    font-size: 17px; font-weight: bold;
                    text-align: left; padding-left: 20px;
                }
                QPushButton:pressed { background: #253344; border-color: #1565c0; }
            """)
            btn.clicked.connect(lambda _, cls=dialog_cls: cls(self).exec())
            outer.addWidget(btn)

        outer.addStretch()

        bar = QWidget()
        bar.setStyleSheet("background: #141e2b; border-top: 1px solid #2a3a4a;")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(28, 12, 28, 12)
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        close_btn.clicked.connect(self.accept)
        bar_row.addStretch()
        bar_row.addWidget(close_btn)
        outer.addWidget(bar)
