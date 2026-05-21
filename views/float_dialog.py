"""
Opening float dialog — denomination-based cash count shown at shift start.
After exec(), read:
  .float_amount   (float, dollars counted)
  .float_variance (float, counted − expected; 0.0 if no expected set)
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox, QWidget, QFrame,
)
from config.settings import TOUCH_BTN_HEIGHT

_NOTES = [
    (10000, "$100"),
    ( 5000,  "$50"),
    ( 2000,  "$20"),
    ( 1000,  "$10"),
    (  500,   "$5"),
]
_COINS = [
    (200, "$2"),
    (100, "$1"),
    ( 50, "50¢"),
    ( 20, "20¢"),
    ( 10, "10¢"),
    (  5,  "5¢"),
]

_BASE_STYLE  = "QDialog, QWidget { background: #1a2332; color: #e6edf3; }"
_EDIT_STYLE  = """
    QLineEdit { background: #1e2a38; color: #e6edf3;
                border: 1px solid #2a3a4a; border-radius: 6px;
                padding: 0 10px; font-size: 15px; }
    QLineEdit:focus { border-color: #1565c0; }
"""
_GRP_STYLE   = (
    "QGroupBox { color: #8b949e; font-size: 12px; border: 1px solid #2a3a4a; "
    "border-radius: 4px; margin-top: 6px; padding-top: 12px; }"
)


def _load_expected_float() -> float:
    from database.connection import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='expected_float'"
        ).fetchone()
        return float(row['value']) if row and row['value'] else 0.0
    except (TypeError, ValueError):
        return 0.0
    finally:
        conn.close()


class OpeningFloatDialog(QDialog):
    """
    Full-screen dialog for counting the opening float by denomination.
    After exec(), read:
      .float_amount   — dollars counted
      .float_variance — counted minus expected (0.0 when expected_float not set)
    """

    def __init__(self, operator_name: str = '', parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opening Float")
        self.setModal(True)
        self.setStyleSheet(_BASE_STYLE)
        self.float_amount:   float = 0.0
        self.float_variance: float = 0.0
        self._expected = _load_expected_float()

        self._denom_edits  = {}   # cents → QLineEdit
        self._denom_labels = {}   # cents → QLabel (subtotal)

        self._build_ui(operator_name)
        self.showFullScreen()

    def _build_ui(self, operator_name: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 0)
        outer.setSpacing(16)

        title = QLabel("Opening Float")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        outer.addWidget(title)

        if operator_name:
            sub = QLabel(f"Welcome, {operator_name} — count the float in the till")
        else:
            sub = QLabel("Count the cash in the till before opening")
        sub.setStyleSheet("font-size: 13px; color: #8b949e;")
        outer.addWidget(sub)

        # Denomination groups side by side
        denom_row = QHBoxLayout()
        denom_row.setSpacing(16)
        denom_row.addWidget(self._build_denom_group("Notes", _NOTES))
        denom_row.addWidget(self._build_denom_group("Coins", _COINS))
        outer.addLayout(denom_row)

        # Totals / variance
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a3a4a;")
        outer.addWidget(sep)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(24)

        self._total_lbl = QLabel("Counted:  $0.00")
        self._total_lbl.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #4CAF50;"
        )
        summary_row.addWidget(self._total_lbl)

        if self._expected > 0:
            exp_lbl = QLabel(f"Expected:  ${self._expected:,.2f}")
            exp_lbl.setStyleSheet("font-size: 15px; color: #8b949e;")
            summary_row.addWidget(exp_lbl)

            self._variance_lbl = QLabel("Variance:  $0.00")
            self._variance_lbl.setStyleSheet("font-size: 15px; color: #8b949e;")
            summary_row.addWidget(self._variance_lbl)
        else:
            self._variance_lbl = None

        summary_row.addStretch()
        outer.addLayout(summary_row)

        outer.addStretch()

        # Bottom bar
        bar = QWidget()
        bar.setStyleSheet("background: #141e2b; border-top: 1px solid #2a3a4a;")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(28, 12, 28, 12)

        skip_btn = QPushButton("Skip (float = $0.00)")
        skip_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        skip_btn.setStyleSheet("color: #8b949e;")
        skip_btn.clicked.connect(self.accept)

        confirm_btn = QPushButton("Confirm Float & Open")
        confirm_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        confirm_btn.setStyleSheet("""
            QPushButton { background: #1565c0; color: white; border: none;
                          border-radius: 6px; font-size: 15px; font-weight: bold; }
            QPushButton:pressed { background: #1976d2; }
        """)
        confirm_btn.clicked.connect(self._confirm)

        bar_row.addWidget(skip_btn)
        bar_row.addStretch()
        bar_row.addWidget(confirm_btn)
        outer.addWidget(bar)

    def _build_denom_group(self, title: str, denoms: list) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setStyleSheet(_GRP_STYLE)
        grid = QGridLayout(grp)
        grid.setSpacing(6)

        for r, (cents, label) in enumerate(denoms):
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 14px; font-weight: bold; min-width: 44px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            edit = QLineEdit("0")
            edit.setFixedHeight(TOUCH_BTN_HEIGHT)
            edit.setFixedWidth(80)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setStyleSheet(_EDIT_STYLE)
            edit.setValidator(QIntValidator(0, 9999, self))
            edit.textChanged.connect(self._update_total)
            self._denom_edits[cents] = edit

            sub = QLabel("$0.00")
            sub.setStyleSheet("font-size: 13px; color: #8b949e; min-width: 72px;")
            sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._denom_labels[cents] = sub

            grid.addWidget(lbl,  r, 0)
            grid.addWidget(edit, r, 1)
            grid.addWidget(sub,  r, 2)

        return grp

    def _update_total(self):
        total_cents = 0
        for cents, edit in self._denom_edits.items():
            try:
                count = int(edit.text() or '0')
            except ValueError:
                count = 0
            sub = count * cents
            total_cents += sub
            self._denom_labels[cents].setText(f"${sub / 100:,.2f}")

        self.float_amount   = total_cents / 100
        self.float_variance = self.float_amount - self._expected

        self._total_lbl.setText(f"Counted:  ${self.float_amount:,.2f}")

        if self._variance_lbl is not None:
            v = self.float_variance
            if v == 0:
                self._variance_lbl.setText("Variance:  $0.00")
                self._variance_lbl.setStyleSheet("font-size: 15px; color: #8b949e;")
            elif v > 0:
                self._variance_lbl.setText(f"Variance:  +${v:,.2f}")
                self._variance_lbl.setStyleSheet("font-size: 15px; color: #FF9800; font-weight: bold;")
            else:
                self._variance_lbl.setText(f"Variance:  –${abs(v):,.2f}")
                self._variance_lbl.setStyleSheet("font-size: 15px; color: #f44336; font-weight: bold;")

    def _confirm(self):
        self._update_total()
        self.accept()
