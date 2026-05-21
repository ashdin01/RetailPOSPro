"""
PIN-based login screen — touch friendly.
Shows operator buttons (large, tappable) + a PIN entry pad.
"""
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QDialog, QFormLayout, QLineEdit, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
import models.operator as op_model
from config.settings import APP_NAME, TOUCH_BTN_HEIGHT

_MAX_ATTEMPTS   = 5
_LOCKOUT_SECS   = 30

_failed:  dict = {}
_lockout: dict = {}


class _SetupPinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Up Admin PIN")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog  { background: #1a2332; color: #e6edf3; }
            QLabel   { color: #e6edf3; background: transparent; }
            QLineEdit { background: #1e2a38; color: #e6edf3;
                        border: 1px solid #2a3a4a; border-radius: 4px;
                        padding: 8px; font-size: 22px; letter-spacing: 6px; }
            QPushButton { background: #1565c0; color: white; border: none;
                          border-radius: 6px; padding: 12px 20px;
                          font-size: 15px; font-weight: bold; }
            QPushButton:hover { background: #1976d2; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(16)

        title = QLabel(f"Welcome to {APP_NAME}")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        lay.addWidget(title)

        info = QLabel("No PIN has been set yet.\nCreate a 4-digit PIN for the Admin account to continue.")
        info.setStyleSheet("color: #8b949e; font-size: 13px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        form.setSpacing(12)
        self.pin1 = QLineEdit()
        self.pin1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin1.setMaxLength(4)
        self.pin1.setPlaceholderText("••••")
        self.pin1.setFixedHeight(52)
        self.pin2 = QLineEdit()
        self.pin2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin2.setMaxLength(4)
        self.pin2.setPlaceholderText("••••")
        self.pin2.setFixedHeight(52)
        form.addRow("New PIN (4 digits):", self.pin1)
        form.addRow("Confirm PIN:", self.pin2)
        lay.addLayout(form)

        btn = QPushButton("Set PIN & Continue")
        btn.setFixedHeight(52)
        btn.clicked.connect(self._save)
        lay.addWidget(btn)

        self.pin1.returnPressed.connect(lambda: self.pin2.setFocus())
        self.pin2.returnPressed.connect(self._save)
        self.pin1.setFocus()

    def _save(self):
        p1, p2 = self.pin1.text().strip(), self.pin2.text().strip()
        if len(p1) != 4 or not p1.isdigit():
            QMessageBox.warning(self, "Invalid PIN", "PIN must be exactly 4 digits.")
            return
        if p1 != p2:
            QMessageBox.warning(self, "Mismatch", "PINs do not match.")
            self.pin2.clear()
            self.pin2.setFocus()
            return
        op_model.set_pin('admin', p1)
        self.accept()


class LoginScreen(QWidget):
    def __init__(self, on_login):
        super().__init__()
        self._on_login = on_login
        self._selected = None
        self._countdown_user = None
        self.setWindowTitle(f"{APP_NAME} — Login")
        self.setMinimumSize(540, 480)
        self.setStyleSheet("QWidget { background: #1a2332; color: #e6edf3; }")
        self._build_ui()
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)
        self._load_operators()

        if not op_model.has_any_pin():
            dlg = _SetupPinDialog(self)
            dlg.exec()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        h.addStretch()

        card = QWidget()
        card.setFixedWidth(420)
        card.setStyleSheet("QWidget { background: #1e2a38; border-radius: 14px; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(36, 36, 36, 36)
        lay.setSpacing(18)

        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #e6edf3; background: transparent;")
        lay.addWidget(title)

        sub = QLabel("Select your name and enter your PIN")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("font-size: 13px; color: #8b949e; background: transparent;")
        lay.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a3a4a; background: transparent;")
        lay.addWidget(sep)

        self._op_area = QVBoxLayout()
        self._op_area.setSpacing(10)
        lay.addLayout(self._op_area)

        pin_lbl = QLabel("PIN")
        pin_lbl.setStyleSheet("color: #8b949e; font-size: 12px; background: transparent;")
        lay.addWidget(pin_lbl)

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_input.setMaxLength(4)
        self.pin_input.setPlaceholderText("••••")
        self.pin_input.setFixedHeight(56)
        self.pin_input.setStyleSheet("""
            QLineEdit {
                background: #1a2332; color: #e6edf3;
                border: 2px solid #2a3a4a; border-radius: 8px;
                padding: 8px; font-size: 26px; letter-spacing: 10px;
            }
            QLineEdit:focus { border-color: #1565c0; }
        """)
        self.pin_input.returnPressed.connect(self._attempt_login)
        lay.addWidget(self.pin_input)

        self.status_lbl = QLabel("")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color: #f85149; font-size: 12px; background: transparent;")
        lay.addWidget(self.status_lbl)

        self.login_btn = QPushButton("Login")
        self.login_btn.setFixedHeight(56)
        self.login_btn.setStyleSheet("""
            QPushButton { background: #1565c0; color: white; border: none;
                          border-radius: 8px; font-size: 16px; font-weight: bold; }
            QPushButton:hover { background: #1976d2; }
            QPushButton:disabled { background: #2a3a4a; color: #6e7681; }
        """)
        self.login_btn.clicked.connect(self._attempt_login)
        lay.addWidget(self.login_btn)

        h.addWidget(card)
        h.addStretch()
        root.addStretch()
        root.addLayout(h)
        root.addStretch()

    def _load_operators(self):
        for i in reversed(range(self._op_area.count())):
            w = self._op_area.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._operators = op_model.get_all_active()
        self._selected = None
        for op in self._operators:
            name = op['full_name'] or op['username']
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1a2332; color: #e6edf3;
                    border: 1px solid #2a3a4a; border-radius: 8px;
                    font-size: 15px; text-align: left; padding: 0 16px;
                }
                QPushButton:checked { background: #1565c0; border-color: #1976d2; }
                QPushButton:hover   { background: #2a3a4a; }
            """)
            btn.clicked.connect(lambda _, o=op, b=btn: self._select(o, b))
            self._op_area.addWidget(btn)
        if len(self._operators) == 1:
            self._select(self._operators[0], self._op_area.itemAt(0).widget())

    def _select(self, op, btn):
        self._selected = op
        for i in range(self._op_area.count()):
            w = self._op_area.itemAt(i).widget()
            if w and w is not btn:
                w.setChecked(False)
        btn.setChecked(True)
        self.pin_input.clear()
        self._countdown_timer.stop()
        self._countdown_user = None
        username = op['username']
        until = _lockout.get(username)
        if until and datetime.now() < until:
            self._start_countdown(username)
        else:
            self.status_lbl.clear()
            self.login_btn.setEnabled(True)
            self.pin_input.setEnabled(True)
            self.pin_input.setFocus()

    def _attempt_login(self):
        if not self._selected:
            self.status_lbl.setText("Please select your name first.")
            return
        username = self._selected['username']
        until = _lockout.get(username)
        if until and datetime.now() < until:
            self._start_countdown(username)
            return
        pin = self.pin_input.text().strip()
        if not pin:
            self.status_lbl.setText("Please enter your PIN.")
            return
        if op_model.verify_pin(username, pin):
            _failed.pop(username, None)
            _lockout.pop(username, None)
            self._on_login(self._selected)
        else:
            attempts = _failed.get(username, 0) + 1
            _failed[username] = attempts
            self.pin_input.clear()
            if attempts >= _MAX_ATTEMPTS:
                _failed[username] = 0
                _lockout[username] = datetime.now() + timedelta(seconds=_LOCKOUT_SECS)
                self._start_countdown(username)
            else:
                remaining = _MAX_ATTEMPTS - attempts
                self.status_lbl.setText(f"Incorrect PIN. {remaining} attempt(s) remaining.")

    def _start_countdown(self, username):
        self._countdown_user = username
        self.login_btn.setEnabled(False)
        self.pin_input.setEnabled(False)
        if not self._countdown_timer.isActive():
            self._countdown_timer.start()
        self._tick()

    def _tick(self):
        username = self._countdown_user
        if not username:
            return
        until = _lockout.get(username)
        if not until:
            self._unlock()
            return
        remaining = max(0, int((until - datetime.now()).total_seconds()))
        if remaining <= 0:
            self._unlock()
        else:
            self.status_lbl.setText(f"Too many attempts. Try again in {remaining}s.")

    def _unlock(self):
        self._countdown_timer.stop()
        self._countdown_user = None
        self.login_btn.setEnabled(True)
        self.pin_input.setEnabled(True)
        self.pin_input.setFocus()
        self.status_lbl.clear()
