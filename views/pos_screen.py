"""
Main POS screen.

Layout (landscape, touch-friendly):
┌────────────────────────────────────────────────┐
│  Header: store name | operator | clock         │
├──────────────────────────┬─────────────────────┤
│  BASKET TABLE            │  Numpad             │
│  (tall rows, scrollable) │  ─────────────────  │
│                          │  Pending display    │
│                          │  ─────────────────  │
│                          │  Totals card        │
├──────────────────────────┤  ─────────────────  │
│  [Scan input] [VOID][CLR]│  [CASH] [EFTPOS]    │
└──────────────────────────┴─────────────────────┘
"""
import logging
import threading
from datetime import date, datetime

from PyQt6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QSizePolicy, QDialog, QMessageBox,
    QApplication, QMenu
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut, QCursor

from config.settings import (
    APP_NAME, APP_VERSION, TOUCH_BTN_HEIGHT, TOUCH_BTN_HEIGHT_LG,
    BASKET_ROW_HEIGHT
)
from views.widgets.numpad import Numpad
from utils.format import currency, gst_from_total
import models.product as product_model
import models.transaction as txn_model
import models.shift as shift_model
import api.backoffice_client as bop
import hardware.printer as printer
import hardware.drawer as drawer
import hardware.scale as scale
import hardware.eftpos as eftpos
from utils.barcode import decode_weight_barcode

_DARK_BG   = "#1a2332"
_CARD_BG   = "#1e2a38"
_BORDER    = "#2a3a4a"
_TEXT      = "#e6edf3"
_DIM       = "#8b949e"
_GREEN     = "#4CAF50"
_BLUE      = "#2196F3"
_ORANGE    = "#FF9800"
_RED       = "#f44336"


def _make_sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setStyleSheet(f"color: {_BORDER};")
    return s


class _CashPaymentDialog(QDialog):
    """Modal dialog for cash tender. Embeds a Numpad."""

    def __init__(self, total: float, parent=None):
        super().__init__(parent)
        self._total = total
        self._tendered_str = ""
        self.accepted_tendered = 0.0
        self.setWindowTitle("Cash Payment")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setStyleSheet(f"""
            QDialog  {{ background: {_DARK_BG}; color: {_TEXT}; }}
            QLabel   {{ background: transparent; color: {_TEXT}; }}
        """)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(16)

        lay.addWidget(_make_sep())

        total_row = QHBoxLayout()
        total_row.addWidget(QLabel("Total Due:"))
        total_row.addStretch()
        lbl = QLabel(currency(self._total))
        lbl.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {_GREEN};")
        total_row.addWidget(lbl)
        lay.addLayout(total_row)

        lay.addWidget(_make_sep())

        tendered_lbl = QLabel("Amount Tendered:")
        tendered_lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        lay.addWidget(tendered_lbl)

        self._tendered_display = QLabel("$0.00")
        self._tendered_display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._tendered_display.setStyleSheet(
            f"font-size: 36px; font-weight: bold; color: {_TEXT}; "
            f"background: {_CARD_BG}; border: 2px solid {_BORDER}; "
            f"border-radius: 8px; padding: 8px 16px;"
        )
        self._tendered_display.setMinimumHeight(62)
        lay.addWidget(self._tendered_display)

        pad = Numpad(self)
        pad.digit_pressed.connect(self._on_digit)
        pad.backspace_pressed.connect(self._on_backspace)
        pad.clear_pressed.connect(self._on_clear)
        pad.enter_pressed.connect(self._on_confirm)
        # ×QTY is not used here — wire it to enter so a quick tap confirms
        pad.qty_pressed.connect(self._on_confirm)
        lay.addWidget(pad, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addWidget(_make_sep())

        self._change_lbl = QLabel("Change:  —")
        self._change_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._change_lbl.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {_ORANGE};")
        lay.addWidget(self._change_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton("Cancel  [Esc]")
        cancel_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           font-size: 14px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._complete_btn = QPushButton("✓  Complete Sale  [Enter]")
        self._complete_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._complete_btn.setEnabled(False)
        self._complete_btn.setStyleSheet(f"""
            QPushButton {{ background: {_GREEN}; color: white; border: none;
                           border-radius: 6px; font-size: 15px; font-weight: bold; }}
            QPushButton:hover {{ background: #45a049; }}
            QPushButton:disabled {{ background: #2a4a2a; color: #4a7a4a; }}
        """)
        self._complete_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._complete_btn)

        lay.addLayout(btn_row)

        QShortcut(QKeySequence("Escape"), self, self.reject)
        QShortcut(QKeySequence("Return"), self, self._on_confirm)
        QShortcut(QKeySequence("Enter"),  self, self._on_confirm)

    def _refresh(self):
        val = float(self._tendered_str) if self._tendered_str else 0.0
        self._tendered_display.setText(f"${val:,.2f}")
        if val >= self._total:
            change = round(val - self._total, 2)
            self._change_lbl.setText(f"Change:  {currency(change)}")
            self._change_lbl.setStyleSheet(
                f"font-size: 22px; font-weight: bold; color: {_GREEN};"
            )
            self._complete_btn.setEnabled(True)
        else:
            self._change_lbl.setText("Change:  —")
            self._change_lbl.setStyleSheet(
                f"font-size: 22px; font-weight: bold; color: {_ORANGE};"
            )
            self._complete_btn.setEnabled(False)

    def _on_digit(self, d: str):
        if d == '.' and '.' in self._tendered_str:
            return
        # Limit to 2 decimal places
        if '.' in self._tendered_str:
            decimals = self._tendered_str.split('.')[1]
            if len(decimals) >= 2:
                return
        self._tendered_str += d
        self._refresh()

    def _on_backspace(self):
        self._tendered_str = self._tendered_str[:-1]
        self._refresh()

    def _on_clear(self):
        self._tendered_str = ""
        self._refresh()

    def _on_confirm(self):
        val = float(self._tendered_str) if self._tendered_str else 0.0
        if val < self._total:
            return
        self.accepted_tendered = val
        self.accept()


class _PriceChangeDialog(QDialog):
    """Touch-friendly price override dialog with reason code selector."""

    _REASONS = [
        ('',   'No Reason'),
        ('OD', 'Out of Date'),
        ('CP', 'Clearance Price'),
        ('BB', 'Bulk Buy'),
    ]

    def __init__(self, item: dict, reason: str = '', parent=None):
        super().__init__(parent)
        self._item         = item
        self._price_str    = ''
        self._adj_qty      = 1          # units to reprice (1 … item qty)
        self.accepted_price  = None     # None = cancelled
        self.accepted_qty    = 1
        self.accepted_reason = reason

        self.setWindowTitle("Change Price")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(f"""
            QDialog {{ background: {_DARK_BG}; color: {_TEXT}; }}
            QLabel  {{ background: transparent; color: {_TEXT}; }}
        """)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        desc_lbl = QLabel(self._item.get('description', ''))
        desc_lbl.setStyleSheet(f"font-size: 16px; font-weight: bold;")
        lay.addWidget(desc_lbl)

        cur_row = QHBoxLayout()
        cur_row.addWidget(QLabel("Current price:"))
        cur_row.addStretch()
        cur_lbl = QLabel(currency(self._item['unit_price']))
        cur_lbl.setStyleSheet(f"font-size: 16px; color: {_DIM};")
        cur_row.addWidget(cur_lbl)
        lay.addLayout(cur_row)

        lay.addWidget(_make_sep())

        reason_hdr = QLabel("Reason:")
        reason_hdr.setStyleSheet(f"font-size: 13px; color: {_DIM};")
        lay.addWidget(reason_hdr)

        reason_row = QHBoxLayout()
        reason_row.setSpacing(6)
        self._reason_btns: dict = {}
        for code, label in self._REASONS:
            text = f"{code} — {label}" if code else label
            btn = QPushButton(text)
            btn.setFixedHeight(TOUCH_BTN_HEIGHT - 4)
            btn.setCheckable(True)
            btn.setChecked(code == self.accepted_reason)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._style_reason_btn(btn, code == self.accepted_reason)
            btn.clicked.connect(lambda _, c=code, b=btn: self._select_reason(c, b))
            reason_row.addWidget(btn)
            self._reason_btns[code] = btn
        lay.addLayout(reason_row)

        # ── Qty selector (only when line has more than 1 unit) ─────────
        total_qty = self._item['qty']
        if total_qty > 1:
            lay.addWidget(_make_sep())

            qty_hdr = QLabel(f"Units to reprice  (line has {total_qty:g}):")
            qty_hdr.setStyleSheet(f"font-size: 13px; color: {_DIM};")
            lay.addWidget(qty_hdr)

            qty_row = QHBoxLayout()
            qty_row.setSpacing(8)

            minus_btn = QPushButton("−")
            minus_btn.setFixedSize(52, 52)
            minus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            minus_btn.setStyleSheet(f"""
                QPushButton {{ background: {_CARD_BG}; color: {_TEXT};
                               border: 1px solid {_BORDER}; border-radius: 8px;
                               font-size: 22px; font-weight: bold; }}
                QPushButton:pressed {{ background: #152030; }}
            """)
            minus_btn.clicked.connect(self._qty_minus)
            qty_row.addWidget(minus_btn)

            self._qty_display = QLabel("1")
            self._qty_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._qty_display.setFixedHeight(52)
            self._qty_display.setMinimumWidth(80)
            self._qty_display.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {_TEXT}; "
                f"background: {_CARD_BG}; border: 2px solid {_BORDER}; border-radius: 8px;"
            )
            qty_row.addWidget(self._qty_display, stretch=1)

            plus_btn = QPushButton("+")
            plus_btn.setFixedSize(52, 52)
            plus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            plus_btn.setStyleSheet(minus_btn.styleSheet())
            plus_btn.clicked.connect(self._qty_plus)
            qty_row.addWidget(plus_btn)

            all_btn = QPushButton(f"All  ({total_qty:g})")
            all_btn.setFixedHeight(52)
            all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            all_btn.setStyleSheet(f"""
                QPushButton {{ background: {_CARD_BG}; color: {_DIM};
                               border: 1px solid {_BORDER}; border-radius: 8px;
                               font-size: 14px; padding: 0 14px; }}
                QPushButton:hover {{ color: {_TEXT}; }}
                QPushButton:pressed {{ background: #152030; }}
            """)
            all_btn.clicked.connect(lambda: self._set_adj_qty(int(total_qty)))
            qty_row.addWidget(all_btn)

            lay.addLayout(qty_row)

        lay.addWidget(_make_sep())

        new_lbl = QLabel("New price:")
        new_lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        lay.addWidget(new_lbl)

        self._price_display = QLabel("$0.00")
        self._price_display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._price_display.setStyleSheet(
            f"font-size: 36px; font-weight: bold; color: {_TEXT}; "
            f"background: {_CARD_BG}; border: 2px solid {_BORDER}; "
            f"border-radius: 8px; padding: 8px 16px;"
        )
        self._price_display.setMinimumHeight(60)
        lay.addWidget(self._price_display)

        pad = Numpad(self)
        pad.digit_pressed.connect(self._on_digit)
        pad.backspace_pressed.connect(self._on_backspace)
        pad.clear_pressed.connect(self._on_clear)
        pad.enter_pressed.connect(self._on_confirm)
        pad.qty_pressed.connect(self._on_confirm)
        lay.addWidget(pad, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addWidget(_make_sep())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton("Cancel  [Esc]")
        cancel_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           font-size: 14px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("✓  Confirm Price")
        self._confirm_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setStyleSheet(f"""
            QPushButton {{ background: {_GREEN}; color: white; border: none;
                           border-radius: 6px; font-size: 15px; font-weight: bold; }}
            QPushButton:hover {{ background: #45a049; }}
            QPushButton:disabled {{ background: #2a4a2a; color: #4a7a4a; }}
        """)
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm_btn)

        lay.addLayout(btn_row)

        QShortcut(QKeySequence("Escape"), self, self.reject)
        QShortcut(QKeySequence("Return"), self, self._on_confirm)
        QShortcut(QKeySequence("Enter"),  self, self._on_confirm)

    @staticmethod
    def _style_reason_btn(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {_ORANGE}; color: white; border: none;
                               border-radius: 6px; font-size: 12px; font-weight: bold;
                               padding: 0 10px; }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {_CARD_BG}; color: {_DIM};
                               border: 1px solid {_BORDER}; border-radius: 6px;
                               font-size: 12px; padding: 0 10px; }}
                QPushButton:hover {{ color: {_TEXT}; }}
            """)

    def _select_reason(self, code: str, clicked_btn: QPushButton):
        self.accepted_reason = code
        for c, b in self._reason_btns.items():
            active = (b is clicked_btn)
            b.setChecked(active)
            self._style_reason_btn(b, active)

    def _set_adj_qty(self, n: int):
        total = int(self._item['qty'])
        self._adj_qty = max(1, min(n, total))
        if hasattr(self, '_qty_display'):
            self._qty_display.setText(str(self._adj_qty))

    def _qty_minus(self):
        self._set_adj_qty(self._adj_qty - 1)

    def _qty_plus(self):
        self._set_adj_qty(self._adj_qty + 1)

    def _refresh_display(self):
        val = float(self._price_str) if self._price_str else 0.0
        self._price_display.setText(f"${val:,.2f}")
        self._confirm_btn.setEnabled(val > 0)

    def _on_digit(self, d: str):
        if d == '.' and '.' in self._price_str:
            return
        if '.' in self._price_str and len(self._price_str.split('.')[1]) >= 2:
            return
        self._price_str += d
        self._refresh_display()

    def _on_backspace(self):
        self._price_str = self._price_str[:-1]
        self._refresh_display()

    def _on_clear(self):
        self._price_str = ''
        self._refresh_display()

    def _on_confirm(self):
        val = float(self._price_str) if self._price_str else 0.0
        if val <= 0:
            return
        self.accepted_price = val
        self.accepted_qty   = self._adj_qty
        self.accept()


class _EFTPOSDialog(QDialog):
    """
    EFTPOS payment dialog.

    - Linkly mode: shows a spinner while the terminal processes, with a
      Cancel button.  Transaction runs in a background thread.
    - Manual mode: shows the amount and Approved / Declined buttons for
      the cashier to confirm after processing on the physical terminal.
    """
    # Emitted from the background thread → main thread
    _result_signal = pyqtSignal(object)

    def __init__(self, total: float, txn_ref: str, parent=None):
        super().__init__(parent)
        self._total   = total
        self._txn_ref = txn_ref
        self._result  = None
        self._cancel_flag = [False]

        self.setModal(True)
        self.setWindowTitle("EFTPOS Payment")
        self.setFixedSize(480, 320)
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        self._result_signal.connect(self._on_result)

        self._build()
        self._start()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 36, 40, 36)
        lay.setSpacing(20)

        # Amount
        amt_lbl = QLabel(currency(self._total))
        amt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        amt_lbl.setStyleSheet(
            f"font-size: 52px; font-weight: bold; color: {_TEXT};"
        )
        lay.addWidget(amt_lbl)

        protocol = eftpos.get_protocol()

        if protocol == 'manual':
            self._build_manual(lay)
        else:
            self._build_linkly(lay)

    def _build_manual(self, lay):
        hint = QLabel("Process payment on the EFTPOS terminal\nthen confirm the result below.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"font-size: 15px; color: {_DIM};")
        lay.addWidget(hint)
        lay.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        declined_btn = QPushButton("✕  Declined")
        declined_btn.setFixedHeight(60)
        declined_btn.setStyleSheet(f"""
            QPushButton {{ background: #3a1010; color: {_RED};
                           border: 2px solid #5a2020; border-radius: 10px;
                           font-size: 18px; font-weight: bold; }}
            QPushButton:pressed {{ background: #5a1818; }}
        """)
        declined_btn.clicked.connect(self._on_manual_declined)
        btn_row.addWidget(declined_btn)

        approved_btn = QPushButton("✓  Approved")
        approved_btn.setFixedHeight(60)
        approved_btn.setStyleSheet(f"""
            QPushButton {{ background: #1a3a1a; color: {_GREEN};
                           border: 2px solid #2a5a2a; border-radius: 10px;
                           font-size: 18px; font-weight: bold; }}
            QPushButton:pressed {{ background: #1f4820; }}
        """)
        approved_btn.clicked.connect(self._on_manual_approved)
        btn_row.addWidget(approved_btn)

        lay.addLayout(btn_row)

    def _build_linkly(self, lay):
        self._status_lbl = QLabel("Connecting to terminal…")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(f"font-size: 15px; color: {_DIM};")
        lay.addWidget(self._status_lbl)

        # Animated dots label
        self._dots_lbl = QLabel("●  ●  ●")
        self._dots_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots_lbl.setStyleSheet(f"font-size: 22px; color: {_BLUE}; letter-spacing: 4px;")
        lay.addWidget(self._dots_lbl)
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start(600)
        self._dot_state = 0

        lay.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(48)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 8px;
                           font-size: 15px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        cancel_btn.clicked.connect(self._on_cancel)
        lay.addWidget(cancel_btn)

    def _tick_dots(self):
        patterns = ["●  ○  ○", "○  ●  ○", "○  ○  ●", "○  ●  ○"]
        self._dot_state = (self._dot_state + 1) % len(patterns)
        self._dots_lbl.setText(patterns[self._dot_state])

    # ── Start ─────────────────────────────────────────────────────────

    def _start(self):
        protocol = eftpos.get_protocol()
        if protocol == 'manual':
            return  # manual mode — no background work
        # Linkly: run in background thread
        amount_cents = round(self._total * 100)
        cancel_flag  = self._cancel_flag

        def _run():
            result = eftpos.process_purchase(amount_cents, self._txn_ref, cancel_flag)
            self._result_signal.emit(result)

        threading.Thread(target=_run, daemon=True).start()

    # ── Handlers ──────────────────────────────────────────────────────

    def _on_manual_approved(self):
        self._result = eftpos.EFTPOSResult(
            eftpos.EFTPOSResult.APPROVED,
            response_code='00', message='Approved (manual)',
        )
        self.accept()

    def _on_manual_declined(self):
        self._result = eftpos.EFTPOSResult(
            eftpos.EFTPOSResult.DECLINED,
            response_code='51', message='Declined',
        )
        self.reject()

    def _on_cancel(self):
        self._cancel_flag[0] = True
        self._status_lbl.setText("Cancelling…")

    def _on_result(self, result):
        if hasattr(self, '_dot_timer'):
            self._dot_timer.stop()
        self._result = result
        if result.approved:
            self.accept()
        elif result.cancelled:
            self.reject()
        else:
            # Declined or error — show message, close after pause
            if hasattr(self, '_status_lbl'):
                self._status_lbl.setText(result.message or "Declined")
                self._status_lbl.setStyleSheet(f"font-size: 15px; color: {_RED};")
            if hasattr(self, '_dots_lbl'):
                self._dots_lbl.setText("✕")
                self._dots_lbl.setStyleSheet(f"font-size: 28px; color: {_RED};")
            QTimer.singleShot(2500, self.reject)

    def get_result(self):
        return self._result


class _SaleCompleteOverlay(QWidget):
    """Full-screen flash overlay shown for ~3 s after a sale."""

    def __init__(self, txn: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet(f"QWidget {{ background: #0d1a24; color: {_TEXT}; }}")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(20)

        ok_lbl = QLabel("✓  Sale Complete")
        ok_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ok_lbl.setStyleSheet(
            f"font-size: 48px; font-weight: bold; color: {_GREEN};"
        )
        lay.addWidget(ok_lbl)

        ref_lbl = QLabel(txn['reference'])
        ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ref_lbl.setStyleSheet(f"font-size: 18px; color: {_DIM};")
        lay.addWidget(ref_lbl)

        total_lbl = QLabel(currency(txn['total']))
        total_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        total_lbl.setStyleSheet(f"font-size: 56px; font-weight: bold; color: {_TEXT};")
        lay.addWidget(total_lbl)

        method = txn['payment_method']
        if method == 'CASH':
            change_lbl = QLabel(
                f"Tendered: {currency(txn['tendered'])}   "
                f"Change: {currency(txn['change_given'])}"
            )
            change_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            change_lbl.setStyleSheet(f"font-size: 22px; color: {_ORANGE};")
            lay.addWidget(change_lbl)

        hint = QLabel("Tap or scan next item to continue…")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"font-size: 14px; color: {_DIM};")
        lay.addWidget(hint)


class POSScreen(QMainWindow):
    lock_requested       = pyqtSignal()
    _health_signal       = pyqtSignal(bool)   # background thread → _set_online_status
    _store_signal        = pyqtSignal(str)    # background thread → _set_store_name
    _scale_result_signal = pyqtSignal(float)  # background thread → _on_scale_result

    def __init__(self, operator: dict, shift_id: int | None = None):
        super().__init__()
        self._operator    = operator
        self._shift_id    = shift_id
        self._basket: list = []   # {'barcode', 'description', 'qty', 'unit_price', 'tax_rate', 'line_total'}
        self._pending_qty = 0     # 0 means "none set"
        self._numpad_buf  = ""    # digits typed via on-screen numpad / keyboard
        self._overlay     = None  # SaleCompleteOverlay instance
        self._bundles: list = []  # active bundles from BackOfficePro [{id, name, required_qty, price, eligible:[{barcode,unit_qty}]}]

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1200, 800)

        self._health_signal.connect(self._set_online_status)
        self._store_signal.connect(self._set_store_name)
        self._scale_result_signal.connect(self._on_scale_result)

        self._build_ui()
        self._start_clock()
        self._start_sync_worker()

        self.showFullScreen()
        QTimer.singleShot(200, self._scan_input.setFocus)

    # ── UI construction ───────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(f"QMainWindow, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        root_lay.addWidget(self._build_header())
        root_lay.addWidget(_make_sep())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left(),  stretch=6)
        body.addWidget(self._build_vsep())
        body.addWidget(self._build_right(), stretch=4)
        root_lay.addLayout(body, stretch=1)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setStyleSheet(f"background: {_CARD_BG}; border-bottom: 1px solid {_BORDER};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)

        self._store_lbl = QLabel("Loading…")
        self._store_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {_TEXT};"
        )
        lay.addWidget(self._store_lbl)
        lay.addStretch()

        op_name = self._operator.get('full_name') or self._operator.get('username', '')
        op_lbl = QLabel(f"👤  {op_name}")
        op_lbl.setStyleSheet(f"font-size: 13px; color: {_DIM};")
        lay.addWidget(op_lbl)

        lay.addSpacing(20)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"font-size: 14px; color: {_DIM};")
        self._status_lbl = QLabel("Offline")
        self._status_lbl.setStyleSheet(f"font-size: 12px; color: {_DIM};")
        lay.addWidget(self._status_dot)
        lay.addWidget(self._status_lbl)

        lay.addSpacing(20)

        self._clock_lbl = QLabel()
        self._clock_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {_GREEN};"
        )
        lay.addWidget(self._clock_lbl)

        lay.addSpacing(16)

        hist_btn = QPushButton("📋 History")
        hist_btn.setFixedHeight(36)
        hist_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 4px;
                           font-size: 11px; padding: 0 12px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        hist_btn.clicked.connect(self._open_history)
        lay.addWidget(hist_btn)

        lay.addSpacing(4)

        settings_btn = QPushButton("⚙ Settings")
        settings_btn.setFixedHeight(36)
        settings_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 4px;
                           font-size: 11px; padding: 0 12px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)

        lay.addSpacing(8)

        lock_btn = QPushButton("🔒 Lock")
        lock_btn.setFixedHeight(36)
        lock_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 4px;
                           font-size: 11px; padding: 0 12px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        lock_btn.clicked.connect(self.lock_requested)
        lay.addWidget(lock_btn)

        self._refresh_store_name()
        return bar

    def _build_left(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # ── Basket table ──────────────────────────────────────────────
        self._basket_table = QTableWidget()
        self._basket_table.setColumnCount(5)
        self._basket_table.setHorizontalHeaderLabels(
            ["Qty", "Description", "Unit Price", "Total", ""]
        )
        hh = self._basket_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._basket_table.setColumnWidth(0, 60)
        self._basket_table.setColumnWidth(2, 110)
        self._basket_table.setColumnWidth(3, 110)
        self._basket_table.setColumnWidth(4, 48)
        self._basket_table.verticalHeader().setVisible(False)
        self._basket_table.verticalHeader().setDefaultSectionSize(BASKET_ROW_HEIGHT)
        self._basket_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._basket_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._basket_table.setAlternatingRowColors(True)
        self._basket_table.setStyleSheet(f"""
            QTableWidget {{
                background: {_CARD_BG}; alternate-background-color: #1b2738;
                gridline-color: {_BORDER}; font-size: 15px; border: none;
            }}
            QHeaderView::section {{
                background: #152030; color: {_DIM};
                font-size: 12px; font-weight: bold; padding: 6px;
                border: none; border-bottom: 1px solid {_BORDER};
            }}
            QTableWidget::item:selected {{
                background: #1e4080; color: {_TEXT};
            }}
        """)
        lay.addWidget(self._basket_table, stretch=1)

        # ── Bottom row: scan input + action buttons ───────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self._scan_input = QLineEdit()
        self._scan_input.setPlaceholderText("Scan barcode or type PLU and press Enter…")
        self._scan_input.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        self._scan_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 2px solid {_BORDER}; border-radius: 8px;
                padding: 0 14px; font-size: 16px;
            }}
            QLineEdit:focus {{ border-color: {_BLUE}; }}
        """)
        self._scan_input.returnPressed.connect(self._on_scan_enter)
        bottom.addWidget(self._scan_input, stretch=1)

        browse_btn = QPushButton("🔍 Browse")
        browse_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        browse_btn.setFixedWidth(110)
        browse_btn.setStyleSheet(f"""
            QPushButton {{ background: #1a3a5c; color: {_BLUE}; border: 1px solid {_BLUE};
                           border-radius: 8px; font-size: 14px; font-weight: bold; }}
            QPushButton:hover {{ background: #1e4a70; }}
        """)
        browse_btn.clicked.connect(self._open_lookup)
        bottom.addWidget(browse_btn)

        void_btn = QPushButton("⌦ Void Item")
        void_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        void_btn.setFixedWidth(130)
        void_btn.setStyleSheet(f"""
            QPushButton {{ background: #3a1010; color: {_RED}; border: 1px solid #5a2020;
                           border-radius: 8px; font-size: 14px; font-weight: bold; }}
            QPushButton:hover {{ background: #5a1818; }}
        """)
        void_btn.clicked.connect(self._void_selected)
        bottom.addWidget(void_btn)

        clear_btn = QPushButton("✕ Clear")
        clear_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        clear_btn.setFixedWidth(100)
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: #2a1a0a; color: {_ORANGE}; border: 1px solid #4a3010;
                           border-radius: 8px; font-size: 14px; font-weight: bold; }}
            QPushButton:hover {{ background: #4a2a10; }}
        """)
        clear_btn.clicked.connect(self._clear_sale)
        bottom.addWidget(clear_btn)

        lay.addLayout(bottom)

        QShortcut(QKeySequence("Delete"), self, self._void_selected)
        QShortcut(QKeySequence("Escape"), self, self._clear_sale)
        QShortcut(QKeySequence("F1"),     self, self._pay_cash)
        QShortcut(QKeySequence("F2"),     self, self._pay_eftpos)
        return w

    def _build_right(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(340)
        w.setStyleSheet(f"background: {_CARD_BG};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # ── Pending numpad input display ──────────────────────────────
        self._pending_display = QLabel("0")
        self._pending_display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pending_display.setFixedHeight(52)
        self._pending_display.setStyleSheet(f"""
            QLabel {{
                background: {_DARK_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 6px;
                font-size: 24px; font-weight: bold;
                padding: 0 14px;
            }}
        """)
        lay.addWidget(self._pending_display)

        self._pending_hint = QLabel("Type qty then × QTY, then scan")
        self._pending_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_DIM};")
        lay.addWidget(self._pending_hint)

        # ── Numpad ────────────────────────────────────────────────────
        self._numpad = Numpad(self)
        self._numpad.digit_pressed.connect(self._numpad_digit)
        self._numpad.backspace_pressed.connect(self._numpad_backspace)
        self._numpad.clear_pressed.connect(self._numpad_clear)
        self._numpad.qty_pressed.connect(self._numpad_set_qty)
        self._numpad.enter_pressed.connect(self._numpad_enter)
        lay.addWidget(self._numpad, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Weigh button (shown only when scale is enabled) ───────────
        self._weigh_btn = QPushButton("⚖  Weigh")
        self._weigh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._weigh_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        self._weigh_btn.setStyleSheet(f"""
            QPushButton {{ background: #1a3a1a; color: #4CAF50;
                           border: 1px solid #2a5a2a; border-radius: 8px;
                           font-size: 16px; font-weight: bold; }}
            QPushButton:hover   {{ background: #1f4820; }}
            QPushButton:pressed {{ background: #0f2810; }}
            QPushButton:disabled {{ background: #141e1a; color: #3a5a3a;
                                    border-color: #1a2a1a; }}
        """)
        self._weigh_btn.clicked.connect(self._on_weigh)
        self._weigh_btn.setVisible(scale.is_enabled())
        lay.addWidget(self._weigh_btn)

        lay.addWidget(_make_sep())

        # ── Totals card ───────────────────────────────────────────────
        self._items_lbl    = QLabel("Items: 0")
        self._subtotal_lbl = QLabel("Subtotal (ex. GST):  $0.00")
        self._gst_lbl      = QLabel("GST included:        $0.00")
        for lbl in (self._items_lbl, self._subtotal_lbl, self._gst_lbl):
            lbl.setStyleSheet(f"font-size: 13px; color: {_DIM};")
            lay.addWidget(lbl)

        self._total_lbl = QLabel("$0.00")
        self._total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._total_lbl.setStyleSheet(
            f"font-size: 44px; font-weight: bold; color: {_TEXT};"
        )
        lay.addWidget(self._total_lbl)

        lay.addWidget(_make_sep())

        # ── Payment buttons ───────────────────────────────────────────
        cash_btn = QPushButton("CASH  [F1]")
        cash_btn.setFixedHeight(TOUCH_BTN_HEIGHT_LG)
        cash_btn.setStyleSheet(f"""
            QPushButton {{ background: #2e7d32; color: white; border: none;
                           border-radius: 10px; font-size: 22px; font-weight: bold; }}
            QPushButton:hover {{ background: #388e3c; }}
            QPushButton:pressed {{ background: #1b5e20; }}
        """)
        cash_btn.clicked.connect(self._pay_cash)
        lay.addWidget(cash_btn)

        eftpos_btn = QPushButton("EFTPOS  [F2]")
        eftpos_btn.setFixedHeight(TOUCH_BTN_HEIGHT_LG)
        eftpos_btn.setStyleSheet(f"""
            QPushButton {{ background: #1565c0; color: white; border: none;
                           border-radius: 10px; font-size: 22px; font-weight: bold; }}
            QPushButton:hover {{ background: #1976d2; }}
            QPushButton:pressed {{ background: #0d47a1; }}
        """)
        eftpos_btn.clicked.connect(self._pay_eftpos)
        lay.addWidget(eftpos_btn)

        lay.addStretch()
        return w

    def _build_vsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet(f"color: {_BORDER};")
        return f

    # ── Clock & background workers ────────────────────────────────────

    def _start_clock(self):
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

        # Health-check every 10 s
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start(10_000)
        self._check_health()

    def _tick_clock(self):
        from PyQt6.QtCore import QDateTime
        self._clock_lbl.setText(QDateTime.currentDateTime().toString("hh:mm:ss"))

    def _check_health(self):
        def _run():
            ok = bop.check_health()
            self._health_signal.emit(ok)
        threading.Thread(target=_run, daemon=True).start()

    def _set_online_status(self, online: bool):
        if online:
            self._status_dot.setStyleSheet(f"font-size: 14px; color: {_GREEN};")
            self._status_lbl.setText("BackOfficePro")
            self._status_lbl.setStyleSheet(f"font-size: 12px; color: {_GREEN};")
        else:
            self._status_dot.setStyleSheet(f"font-size: 14px; color: {_RED};")
            self._status_lbl.setText("Offline")
            self._status_lbl.setStyleSheet(f"font-size: 12px; color: {_RED};")

    def _start_sync_worker(self):
        """Set up periodic sync timer in main thread; run first sync in background."""
        from database.connection import get_connection
        conn = get_connection()
        try:
            mins_row = conn.execute(
                "SELECT value FROM settings WHERE key='cache_sync_mins'"
            ).fetchone()
            interval_ms = int(mins_row['value'] if mins_row else 5) * 60 * 1000
        finally:
            conn.close()

        # Timer stays in the main thread — only the work runs in background threads
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._background_sync)
        self._sync_timer.start(interval_ms)

        # Initial sync
        threading.Thread(target=self._background_sync, daemon=True).start()

    def _background_sync(self):
        threading.Thread(target=self._flush_sync_queue, daemon=True).start()
        threading.Thread(target=self._sync_products_and_store, daemon=True).start()

    def _sync_products_and_store(self):
        from database.connection import get_connection
        n = bop.sync_product_cache()
        conn = get_connection()
        try:
            rows = conn.execute("SELECT barcode FROM product_cache WHERE active=1").fetchall()
            barcodes = [r['barcode'] for r in rows]
        finally:
            conn.close()
        if barcodes:
            bop.sync_images(barcodes)
        store = bop.get_store_info()
        if store and store.get('store_name'):
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('store_name', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (store['store_name'],)
                )
                conn.commit()
            finally:
                conn.close()
            self._store_signal.emit(store['store_name'])
        bundles = bop.fetch_bundles()
        if bundles is not None:
            self._bundles = bundles
            logging.info(f"[sync] Loaded {len(bundles)} bundle(s)")

    def _flush_sync_queue(self):
        pending = txn_model.get_pending_sync()
        for txn in pending:
            sale_data = {
                'reference':      txn['reference'],
                'sale_date':      txn['sale_date'],
                'operator':       txn['operator'],
                'payment_method': txn['payment_method'],
                'subtotal':       txn['subtotal'],
                'gst_amount':     txn['gst_amount'],
                'total':          txn['total'],
                'items': [
                    {
                        'barcode':     i['barcode'],
                        'description': i['description'],
                        'qty':         i['qty'],
                        'unit_price':  i['unit_price'],
                        'line_total':  i['line_total'],
                        'tax_rate':    i['tax_rate'],
                    }
                    for i in txn['items']
                    if not str(i.get('barcode', '')).startswith('__BUNDLE_')
                ],
            }
            result = bop.post_sale(sale_data)
            if result and result.get('ok'):
                txn_model.mark_synced(txn['id'])
                logging.info(f"[sync] {txn['reference']} synced to BackOfficePro")
            else:
                txn_model.mark_sync_failed(
                    txn['queue_id'],
                    str(result) if result else "No response"
                )

    def _refresh_store_name(self):
        from database.connection import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='store_name'"
            ).fetchone()
            name = row['value'] if row and row['value'] else APP_NAME
            self._store_lbl.setText(name)
        finally:
            conn.close()

    def _set_store_name(self, name: str):
        self._store_lbl.setText(name)

    # ── Numpad handling ───────────────────────────────────────────────

    def _numpad_digit(self, d: str):
        if d == '.' and '.' in self._numpad_buf:
            return
        self._numpad_buf += d
        self._pending_display.setText(self._numpad_buf or "0")
        self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_ORANGE};")

    def _numpad_backspace(self):
        self._numpad_buf = self._numpad_buf[:-1]
        self._pending_display.setText(self._numpad_buf or "0")
        if not self._numpad_buf:
            self._clear_pending_state()

    def _numpad_clear(self):
        self._numpad_buf = ""
        self._pending_qty = 0
        self._clear_pending_state()

    def _numpad_set_qty(self):
        """× QTY — store current buffer as pending quantity."""
        try:
            qty = float(self._numpad_buf) if self._numpad_buf else 0
        except ValueError:
            qty = 0
        if qty <= 0:
            self._numpad_clear()
            return
        self._pending_qty = qty
        self._numpad_buf = ""
        self._pending_display.setText(f"QTY: {qty:g}  ×  scan…")
        self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_GREEN};")
        self._scan_input.setFocus()

    def _numpad_enter(self):
        """↵ PLU/Enter — treat buffer as barcode/PLU and look up."""
        text = self._numpad_buf.strip()
        self._numpad_buf = ""
        self._clear_pending_state()
        if text:
            self._lookup_and_add(text)
        self._scan_input.setFocus()

    def _clear_pending_state(self):
        self._pending_qty = 0
        self._numpad_buf = ""
        self._pending_display.setText("0")
        self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_DIM};")

    def _on_weigh(self):
        """Read weight from scale and set it as pending quantity."""
        self._weigh_btn.setEnabled(False)
        self._weigh_btn.setText("Reading…")

        def _do_read():
            weight = scale.read_weight()
            self._scale_result_signal.emit(weight if weight is not None else -1.0)

        threading.Thread(target=_do_read, daemon=True).start()

    def _on_scale_result(self, weight: float):
        self._weigh_btn.setEnabled(True)
        self._weigh_btn.setText("⚖  Weigh")
        if weight <= 0:
            self._pending_display.setText("Scale error")
            self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_RED};")
            QTimer.singleShot(2000, self._clear_pending_state)
            return
        self._pending_qty  = weight
        self._numpad_buf   = ""
        self._pending_display.setText(f"{weight:.3f} kg — scan…")
        self._pending_hint.setStyleSheet(f"font-size: 11px; color: {_GREEN};")
        self._scan_input.setFocus()

    # ── Basket logic ──────────────────────────────────────────────────

    def _on_scan_enter(self):
        text = self._scan_input.text().strip()
        self._scan_input.clear()
        if text:
            self._lookup_and_add(text)

    def _lookup_and_add(self, text: str):
        # ── Random-weight barcode (EAN-13 starting with 2) ────────────
        weight_info = decode_weight_barcode(text)
        if weight_info:
            plu        = weight_info['plu']
            weight_kg  = weight_info['weight_kg']
            product = product_model.get_by_plu(plu)
            if product is None:
                product = product_model.get_by_barcode(text)
            if product:
                self._clear_pending_state()
                self._add_to_basket(product, weight_kg)
                self._scan_input.setFocus()
                return
            # barcode not in cache — fall through to normal not-found handling

        # ── Normal PLU / barcode lookup ───────────────────────────────
        product = None
        if text.isdigit() and len(text) <= 6:
            product = product_model.get_by_plu(text)
        if product is None:
            product = product_model.get_by_barcode(text)

        if product is None:
            self._flash_not_found(text)
            return

        qty = self._pending_qty if self._pending_qty > 0 else 1
        self._clear_pending_state()
        self._add_to_basket(product, qty)
        self._scan_input.setFocus()

    def _add_to_basket(self, product: dict, qty: float):
        barcode = product['barcode']
        for item in self._basket:
            if item['barcode'] == barcode:
                item['qty'] = round(item['qty'] + qty, 3)
                item['line_total'] = round(item['qty'] * item['unit_price'], 2)
                self._refresh_basket_table()
                return

        self._basket.append({
            'barcode':      barcode,
            'description':  product['description'],
            'qty':          qty,
            'unit_price':   float(product['sell_price']),
            'tax_rate':     float(product.get('tax_rate', 10.0)),
            'line_total':   round(qty * float(product['sell_price']), 2),
            'price_reason': '',
        })
        self._refresh_basket_table()

    def _void_selected(self):
        row = self._basket_table.currentRow()
        if row < 0 or row >= len(self._basket):
            return
        item = self._basket[row]
        if item.get('is_bundle_discount'):
            return
        reply = QMessageBox.question(
            self, "Void Item",
            f"Remove  {item['qty']:g} × {item['description']}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._basket.pop(row)
            self._refresh_basket_table()
        self._scan_input.setFocus()

    def _clear_sale(self):
        if not self._basket:
            return
        reply = QMessageBox.question(
            self, "Clear Sale",
            "Clear the current sale?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._basket.clear()
            self._clear_pending_state()
            self._refresh_basket_table()
        self._scan_input.setFocus()

    def _apply_bundles(self):
        """
        Recalculate bundle discounts using a greedy largest-first approach.

        Bundles are evaluated largest required_qty first. Each bundle consumes
        units from a shared pool, so a 24-unit case deal prevents the same units
        being double-counted by a 6-unit deal. Remaining units after the large
        deal feed into smaller tiers (e.g. 30 units = 1 case + 1 six-pack).
        """
        self._basket = [i for i in self._basket if not i.get('is_bundle_discount')]

        # Largest required_qty first — higher tiers get first pick of units
        sorted_bundles = sorted(self._bundles, key=lambda b: int(b['required_qty']), reverse=True)

        # Pool of remaining scans per barcode not yet consumed by a bundle
        available = {}
        for item in self._basket:
            available[item['barcode']] = available.get(item['barcode'], 0.0) + item['qty']

        for bundle in sorted_bundles:
            eligible_map = {e['barcode']: int(e.get('unit_qty', 1)) for e in bundle['eligible']}
            required = int(bundle['required_qty'])

            # Total available base units for this bundle from the remaining pool
            total_units = sum(
                available.get(bc, 0.0) * uq
                for bc, uq in eligible_map.items()
            )

            complete = int(total_units // required)
            if complete <= 0:
                continue

            # Calculate normal price and consume units from pool
            remaining = float(complete * required)
            normal_total = 0.0
            for item in self._basket:
                if remaining <= 0:
                    break
                bc = item['barcode']
                if bc not in eligible_map:
                    continue
                uq = eligible_map[bc]
                avail_units = available.get(bc, 0.0) * uq
                take_units = min(avail_units, remaining)
                take_scans = take_units / uq
                normal_total += take_scans * item['unit_price']
                remaining -= take_units
                available[bc] = available.get(bc, 0.0) - take_scans  # consume from pool

            bundle_total = complete * float(bundle['price'])
            discount = round(bundle_total - normal_total, 2)  # negative = savings

            if discount >= 0:
                continue

            self._basket.append({
                'barcode':            f"__BUNDLE_{bundle['id']}__",
                'description':        f"🎁  {bundle['name']}  ({complete} × {required} units)",
                'qty':                1,
                'unit_price':         discount,
                'tax_rate':           10.0,
                'line_total':         discount,
                'price_reason':       '',
                'is_bundle_discount': True,
            })

    def _refresh_basket_table(self):
        self._apply_bundles()
        self._basket_table.setRowCount(0)
        for item in self._basket:
            r = self._basket_table.rowCount()
            self._basket_table.insertRow(r)
            self._basket_table.setRowHeight(r, BASKET_ROW_HEIGHT)

            # ── Bundle discount row (synthetic) ───────────────────────
            if item.get('is_bundle_discount'):
                for col in range(5):
                    cell = QTableWidgetItem()
                    cell.setBackground(QColor("#0d2010"))
                    self._basket_table.setItem(r, col, cell)
                desc_item = QTableWidgetItem(item['description'])
                desc_item.setForeground(QColor(_GREEN))
                desc_item.setBackground(QColor("#0d2010"))
                desc_item.setFont(QFont("", -1, QFont.Weight.Bold))
                self._basket_table.setItem(r, 1, desc_item)
                amt = QTableWidgetItem(f"−{currency(abs(item['line_total']))}")
                amt.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                amt.setForeground(QColor(_GREEN))
                amt.setBackground(QColor("#0d2010"))
                amt.setFont(QFont("", -1, QFont.Weight.Bold))
                self._basket_table.setItem(r, 3, amt)
                continue

            qty_btn = QPushButton(f"{item['qty']:g}  ▾")
            qty_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            qty_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_CARD_BG}; color: {_TEXT};
                    border: 1px solid {_BORDER}; border-radius: 4px;
                    font-size: 14px; font-weight: bold; padding: 0 6px;
                }}
                QPushButton:hover {{ border-color: {_BLUE}; color: {_BLUE}; }}
                QPushButton:pressed {{ background: #152030; }}
            """)
            qty_btn.clicked.connect(lambda _, row=r: self._show_qty_menu(row))
            self._basket_table.setCellWidget(r, 0, qty_btn)

            desc_item = QTableWidgetItem(item['description'])
            desc_item.setToolTip(item['barcode'])
            self._basket_table.setItem(r, 1, desc_item)

            reason = item.get('price_reason', '')
            price_label = (f"{currency(item['unit_price'])}  [{reason}]  ▾"
                           if reason else f"{currency(item['unit_price'])}  ▾")
            price_btn = QPushButton(price_label)
            price_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            price_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_CARD_BG};
                    color: {_ORANGE if reason else _TEXT};
                    border: 1px solid {_BORDER}; border-radius: 4px;
                    font-size: 13px; padding: 0 6px; text-align: right;
                }}
                QPushButton:hover {{ border-color: {_BLUE}; }}
                QPushButton:pressed {{ background: #152030; }}
            """)
            price_btn.clicked.connect(lambda _, row=r: self._show_price_menu(row))
            self._basket_table.setCellWidget(r, 2, price_btn)

            total_item = QTableWidgetItem(currency(item['line_total']))
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            total_item.setForeground(QColor(_TEXT))
            self._basket_table.setItem(r, 3, total_item)

            del_btn = QPushButton("✕")
            del_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_RED}; border: none; "
                f"font-size: 16px; font-weight: bold; }}"
                f"QPushButton:hover {{ color: #ff6060; }}"
            )
            del_btn.clicked.connect(lambda _, idx=r: self._void_row(idx))
            self._basket_table.setCellWidget(r, 4, del_btn)

        # Scroll to last item
        if self._basket:
            self._basket_table.scrollToBottom()

        self._refresh_totals()

    def _show_price_menu(self, row: int):
        if row < 0 or row >= len(self._basket):
            return
        if self._basket[row].get('is_bundle_discount'):
            return

        _MENU_SS = f"""
            QMenu {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px; padding: 4px 0;
            }}
            QMenu::item {{ padding: 12px 24px; font-size: 15px; min-width: 200px; }}
            QMenu::item:selected {{ background: {_BLUE}; color: white; border-radius: 4px; }}
            QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 8px; }}
        """

        menu = QMenu(self)
        menu.setStyleSheet(_MENU_SS)

        a_change = menu.addAction("Change Price…")
        menu.addSeparator()
        a_od = menu.addAction("OD — Out of Date")
        a_cp = menu.addAction("CP — Clearance Price")
        a_bb = menu.addAction("BB — Bulk Buy")

        chosen = menu.exec(QCursor.pos())
        if chosen == a_change:
            self._open_price_dialog(row, '')
        elif chosen == a_od:
            self._open_price_dialog(row, 'OD')
        elif chosen == a_cp:
            self._open_price_dialog(row, 'CP')
        elif chosen == a_bb:
            self._open_price_dialog(row, 'BB')

        self._scan_input.setFocus()

    def _open_price_dialog(self, row: int, reason: str):
        if row < 0 or row >= len(self._basket):
            return
        item = self._basket[row]
        dlg = _PriceChangeDialog(item, reason, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        adj_qty    = dlg.accepted_qty
        new_price  = dlg.accepted_price
        new_reason = dlg.accepted_reason

        if adj_qty >= item['qty']:
            # Reprice the whole line
            item['unit_price']   = new_price
            item['line_total']   = round(item['qty'] * new_price, 2)
            item['price_reason'] = new_reason
        else:
            # Split: reduce original line, insert new repriced line below it
            import copy
            new_item = copy.copy(item)
            new_item['qty']          = round(adj_qty, 3)
            new_item['unit_price']   = new_price
            new_item['line_total']   = round(adj_qty * new_price, 2)
            new_item['price_reason'] = new_reason

            item['qty']        = round(item['qty'] - adj_qty, 3)
            item['line_total'] = round(item['qty'] * item['unit_price'], 2)

            self._basket.insert(row + 1, new_item)

        self._refresh_basket_table()

    def _show_qty_menu(self, row: int):
        if row < 0 or row >= len(self._basket):
            return
        if self._basket[row].get('is_bundle_discount'):
            return

        # XX = current numpad buffer, or None if empty
        try:
            xx = float(self._numpad_buf) if self._numpad_buf.strip() else None
        except ValueError:
            xx = None

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 4px 0;
            }}
            QMenu::item {{
                padding: 12px 24px; font-size: 15px; min-width: 180px;
            }}
            QMenu::item:selected {{
                background: {_BLUE}; color: white; border-radius: 4px;
            }}
            QMenu::separator {{
                height: 1px; background: {_BORDER}; margin: 4px 8px;
            }}
        """)

        a_plus1   = menu.addAction("+1  (add one)")
        a_plusxx  = menu.addAction(f"+{xx:g}  (add {xx:g})" if xx else "+  …  (enter qty first)")
        menu.addSeparator()
        a_minus1  = menu.addAction("−1  (remove one)")
        a_minusxx = menu.addAction(f"−{xx:g}  (remove {xx:g})" if xx else "−  …  (enter qty first)")
        menu.addSeparator()
        a_void    = menu.addAction("✕  Void Line")

        if xx is None:
            a_plusxx.setEnabled(False)
            a_minusxx.setEnabled(False)

        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            pass
        elif chosen == a_plus1:
            self._adjust_basket_qty(row, 1)
        elif chosen == a_plusxx and xx is not None:
            self._adjust_basket_qty(row, xx)
        elif chosen == a_minus1:
            self._adjust_basket_qty(row, -1)
        elif chosen == a_minusxx and xx is not None:
            self._adjust_basket_qty(row, -xx)
        elif chosen == a_void:
            self._void_row(row)

        self._scan_input.setFocus()

    def _adjust_basket_qty(self, row: int, delta: float):
        if row < 0 or row >= len(self._basket):
            return
        if self._basket[row].get('is_bundle_discount'):
            return
        item = self._basket[row]
        new_qty = round(item['qty'] + delta, 3)
        if new_qty <= 0:
            self._basket.pop(row)
        else:
            item['qty']        = new_qty
            item['line_total'] = round(new_qty * item['unit_price'], 2)
        self._refresh_basket_table()

    def _void_row(self, row: int):
        if 0 <= row < len(self._basket):
            if self._basket[row].get('is_bundle_discount'):
                return
            self._basket.pop(row)
            self._refresh_basket_table()
        self._scan_input.setFocus()

    def _open_history(self):
        from views.transaction_history import TransactionHistory
        dlg = TransactionHistory(self)
        dlg.exec()
        self._scan_input.setFocus()

    def _open_settings(self):
        from views.settings_screen import SettingsScreen
        dlg = SettingsScreen(self)
        dlg.exec()
        self._scan_input.setFocus()

    def _refresh_totals(self):
        total = round(sum(i['line_total'] for i in self._basket), 2)
        gst = round(sum(
            gst_from_total(i['line_total'], i.get('tax_rate', 10.0))
            for i in self._basket
            if i.get('tax_rate', 0) > 0
        ), 2)
        subtotal = round(total - gst, 2)
        n = sum(int(i['qty']) if i['qty'] == int(i['qty']) else 1 for i in self._basket)
        self._items_lbl.setText(f"Items: {len(self._basket)}  ({n} units)")
        self._subtotal_lbl.setText(f"Subtotal (ex. GST):  {currency(subtotal)}")
        self._gst_lbl.setText(f"GST included:        {currency(gst)}")
        self._total_lbl.setText(currency(total))
        color = _TEXT if total > 0 else _DIM
        self._total_lbl.setStyleSheet(
            f"font-size: 44px; font-weight: bold; color: {color};"
        )

    def _flash_not_found(self, text: str):
        self._scan_input.setStyleSheet(f"""
            QLineEdit {{
                background: #3a1010; color: {_RED};
                border: 2px solid {_RED}; border-radius: 8px;
                padding: 0 14px; font-size: 16px;
            }}
        """)
        self._scan_input.setPlaceholderText(f"Not found: {text}")
        QTimer.singleShot(1500, self._reset_scan_style)

    def _reset_scan_style(self):
        self._scan_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 2px solid {_BORDER}; border-radius: 8px;
                padding: 0 14px; font-size: 16px;
            }}
            QLineEdit:focus {{ border-color: {_BLUE}; }}
        """)
        self._scan_input.setPlaceholderText("Scan barcode or type PLU and press Enter…")
        self._scan_input.setFocus()

    def _open_lookup(self):
        from views.lookup_view import LookupView
        qty = self._pending_qty if self._pending_qty > 0 else 1.0
        dlg = LookupView(on_add=self._add_to_basket, pending_qty=qty, parent=self)
        dlg.showFullScreen()
        dlg.exec()
        self._clear_pending_state()
        self._scan_input.setFocus()

    # ── Payment ───────────────────────────────────────────────────────

    def _total(self) -> float:
        return round(sum(i['line_total'] for i in self._basket), 2)

    def _pay_cash(self):
        if not self._basket:
            return
        total = self._total()
        dlg = _CashPaymentDialog(total, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._scan_input.setFocus()
            return
        tendered = dlg.accepted_tendered
        change   = round(tendered - total, 2)
        drawer.open_drawer()
        self._complete_sale('CASH', tendered, change)

    def _pay_eftpos(self):
        if not self._basket:
            return
        total   = self._total()
        txn_ref = f"POS-{id(self) & 0xFFFF:04X}"   # lightweight pre-ref for terminal

        dlg = _EFTPOSDialog(total, txn_ref, self)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        result   = dlg.get_result()

        if accepted and result and result.approved:
            self._complete_sale('EFTPOS', total, 0.0)
        else:
            msg = ""
            if result:
                if result.declined:
                    msg = f"EFTPOS Declined  —  {result.message or 'Card declined'}"
                elif result.error:
                    msg = f"EFTPOS Error  —  {result.message or 'Terminal error'}"
            if msg:
                QMessageBox.warning(self, "EFTPOS", msg)
            self._scan_input.setFocus()

    def _complete_sale(self, method: str, tendered: float, change: float):
        items = list(self._basket)
        total = self._total()

        txn = txn_model.create(
            operator      = self._operator.get('username', 'unknown'),
            shift_id      = self._shift_id,
            items         = items,
            payment_method= method,
            total         = total,
            tendered      = tendered,
            change_given  = change,
        )

        printer.print_receipt(txn)
        self._show_sale_complete(txn)
        self._basket.clear()
        self._clear_pending_state()
        self._refresh_basket_table()
        # Non-blocking sync attempt
        threading.Thread(target=self._flush_sync_queue, daemon=True).start()

    def _show_sale_complete(self, txn: dict):
        """Show the sale-complete overlay for 3 seconds."""
        if self._overlay:
            self._overlay.hide()
            self._overlay.deleteLater()
        overlay = _SaleCompleteOverlay(txn, self.centralWidget())
        overlay.setGeometry(self.centralWidget().rect())
        overlay.show()
        overlay.raise_()
        self._overlay = overlay

        def _dismiss():
            if self._overlay:
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
            self._scan_input.setFocus()

        QTimer.singleShot(3000, _dismiss)

    # ── Keyboard passthrough to scan input ────────────────────────────

    def keyPressEvent(self, event):
        """Redirect keyboard input to scan bar so hardware scanners always work."""
        if self.focusWidget() is not self._scan_input:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Scanner fired Enter while focus was elsewhere — submit whatever is in the bar
                if self._scan_input.text().strip():
                    self._on_scan_enter()
                return
            if event.text() and event.text().isprintable():
                self._scan_input.setFocus()
                self._scan_input.setText(self._scan_input.text() + event.text())
                return
        super().keyPressEvent(event)
