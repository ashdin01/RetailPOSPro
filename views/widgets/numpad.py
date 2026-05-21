"""
Touch numpad widget.

Emits:
  digit_pressed(str)   — individual digit / '.' was tapped
  backspace_pressed()  — ⌫ tapped
  clear_pressed()      — CLR tapped
  qty_pressed()        — ×QTY tapped (caller should read display() value)
  enter_pressed()      — ↵ / PLU Enter tapped

The parent widget manages what the input stream means (qty override vs PLU entry).
"""
from PyQt6.QtWidgets import QWidget, QGridLayout, QPushButton, QLabel, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt
from config.settings import NUMPAD_BTN_SIZE

_BTN_STYLE = """
    QPushButton {{
        background: {bg};
        color: {fg};
        border: 1px solid #2e3f52;
        border-radius: 6px;
        font-size: 20px;
        font-weight: bold;
    }}
    QPushButton:pressed {{ background: {press}; }}
"""

_DIGIT   = _BTN_STYLE.format(bg="#1e2a38", fg="#e6edf3", press="#2a3f58")
_ACTION  = _BTN_STYLE.format(bg="#152030", fg="#5c9de8", press="#1a3050")
_QTY     = _BTN_STYLE.format(bg="#1a3a1a", fg="#4CAF50", press="#1f4820")
_ENTER   = _BTN_STYLE.format(bg="#1a3060", fg="#90caf9", press="#1e3a78")
_CLR     = _BTN_STYLE.format(bg="#3a1010", fg="#f85149", press="#4a1a1a")


class Numpad(QWidget):
    digit_pressed     = pyqtSignal(str)
    backspace_pressed = pyqtSignal()
    clear_pressed     = pyqtSignal()
    qty_pressed       = pyqtSignal()
    enter_pressed     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        grid = QGridLayout(self)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        S = NUMPAD_BTN_SIZE

        def _btn(label, style, row, col, colspan=1):
            b = QPushButton(label)
            b.setFixedHeight(S)
            if colspan == 1:
                b.setFixedWidth(S)
            else:
                b.setMinimumWidth(S * colspan + 6 * (colspan - 1))
            b.setStyleSheet(style)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            grid.addWidget(b, row, col, 1, colspan)
            return b

        # Row 0: 7 8 9
        _btn("7", _DIGIT, 0, 0).clicked.connect(lambda: self.digit_pressed.emit("7"))
        _btn("8", _DIGIT, 0, 1).clicked.connect(lambda: self.digit_pressed.emit("8"))
        _btn("9", _DIGIT, 0, 2).clicked.connect(lambda: self.digit_pressed.emit("9"))

        # Row 1: 4 5 6
        _btn("4", _DIGIT, 1, 0).clicked.connect(lambda: self.digit_pressed.emit("4"))
        _btn("5", _DIGIT, 1, 1).clicked.connect(lambda: self.digit_pressed.emit("5"))
        _btn("6", _DIGIT, 1, 2).clicked.connect(lambda: self.digit_pressed.emit("6"))

        # Row 2: 1 2 3
        _btn("1", _DIGIT, 2, 0).clicked.connect(lambda: self.digit_pressed.emit("1"))
        _btn("2", _DIGIT, 2, 1).clicked.connect(lambda: self.digit_pressed.emit("2"))
        _btn("3", _DIGIT, 2, 2).clicked.connect(lambda: self.digit_pressed.emit("3"))

        # Row 3: . 0 ⌫
        _btn(".", _DIGIT, 3, 0).clicked.connect(lambda: self.digit_pressed.emit("."))
        _btn("0", _DIGIT, 3, 1).clicked.connect(lambda: self.digit_pressed.emit("0"))
        _btn("⌫", _ACTION, 3, 2).clicked.connect(self.backspace_pressed)

        # Row 4: ×QTY (2 wide)  CLR
        _btn("× QTY", _QTY, 4, 0, colspan=2).clicked.connect(self.qty_pressed)
        _btn("CLR", _CLR, 4, 2).clicked.connect(self.clear_pressed)

        # Row 5: ↵ PLU/Enter (full width)
        enter_btn = QPushButton("↵  PLU / Enter")
        enter_btn.setFixedHeight(S)
        enter_btn.setStyleSheet(_ENTER)
        enter_btn.clicked.connect(self.enter_pressed)
        grid.addWidget(enter_btn, 5, 0, 1, 3)
