"""
End of Day (EOD) dialog.

Three sections:
  1. Daily sales summary
  2. Float count (denomination-by-denomination cash count)
  3. EFTPOS settlement (Linkly auto-settle or manual instructions)

Bottom bar: Print Report | Close Shift | Back
"""
import datetime
import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QScrollArea,
    QFrame, QWidget, QMessageBox,
)

from config.settings import TOUCH_BTN_HEIGHT
from database.connection import get_connection
import models.shift as shift_model

# ── Australian denominations: (value_in_cents, display_label) ────────────────
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
_GROUP_STYLE = "QGroupBox { font-size: 13px; font-weight: bold; color: #8b949e; " \
               "border: 1px solid #2a3a4a; border-radius: 6px; margin-top: 8px; " \
               "padding-top: 14px; }"


# ── Background thread for EFTPOS settlement ───────────────────────────────────

class _SettleThread(QThread):
    done = pyqtSignal(object)   # emits EFTPOSResult

    def run(self):
        from hardware.eftpos import settle_terminal
        try:
            result = settle_terminal()
        except Exception as e:
            from hardware.eftpos import EFTPOSResult
            result = EFTPOSResult(EFTPOSResult.ERROR, message=str(e))
        self.done.emit(result)


# ── EOD Dialog ────────────────────────────────────────────────────────────────

class EODDialog(QDialog):
    def __init__(self, parent=None, operator_id: int | None = None):
        super().__init__(parent)
        self._operator_id = operator_id
        self.setWindowTitle("End of Day")
        self.setModal(True)
        self.setStyleSheet(_BASE_STYLE)

        self._totals   = {}
        self._shift    = None
        self._today    = datetime.date.today().isoformat()
        self._denom_edits  = {}   # cents_value → QLineEdit (count)
        self._denom_labels = {}   # cents_value → QLabel (subtotal)
        self._settle_result = None
        self._settle_thread = None

        self._load_data()
        self._build_ui()
        self.showFullScreen()

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_data(self):
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                                       AS txn_count,
                    COALESCE(SUM(subtotal),   0)                                   AS subtotal,
                    COALESCE(SUM(gst_amount), 0)                                   AS gst_amount,
                    COALESCE(SUM(total),      0)                                   AS total,
                    COALESCE(SUM(CASE WHEN payment_method='CASH'    THEN total ELSE 0 END), 0) AS cash,
                    COALESCE(SUM(CASE WHEN payment_method='EFTPOS'  THEN total ELSE 0 END), 0) AS eftpos,
                    COALESCE(SUM(CASE WHEN payment_method='ACCOUNT' THEN total ELSE 0 END), 0) AS account
                FROM transactions
                WHERE sale_date=? AND status='COMPLETED'
            """, (self._today,)).fetchone()
            self._totals = dict(row)

            if self._operator_id is not None:
                shift_row = conn.execute(
                    "SELECT * FROM shifts WHERE status='OPEN' AND operator_id=? "
                    "ORDER BY opened_at DESC LIMIT 1",
                    (self._operator_id,)
                ).fetchone()
            else:
                shift_row = conn.execute(
                    "SELECT * FROM shifts WHERE status='OPEN' ORDER BY opened_at DESC LIMIT 1"
                ).fetchone()
            self._shift = dict(shift_row) if shift_row else None

            store_row = conn.execute(
                "SELECT value FROM settings WHERE key='store_name'"
            ).fetchone()
            self._store_name = (store_row['value'] or 'My Store') if store_row else 'My Store'
        finally:
            conn.close()

    # ── UI construction ───────────────────────────────────────────────────────

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
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("End of Day")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        lay.addWidget(title)

        date_lbl = QLabel(
            datetime.date.today().strftime("%A %-d %B %Y")
        )
        date_lbl.setStyleSheet("font-size: 13px; color: #8b949e;")
        lay.addWidget(date_lbl)

        lay.addWidget(self._build_sales_section())
        lay.addWidget(self._build_float_section())
        lay.addWidget(self._build_eftpos_section())
        lay.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
        outer.addWidget(self._build_bottom_bar())

    def _row_lbl(self, text, bold=False, color='#e6edf3', size=14):
        lbl = QLabel(text)
        weight = 'bold' if bold else 'normal'
        lbl.setStyleSheet(f"font-size: {size}px; font-weight: {weight}; color: {color};")
        return lbl

    # ── Section 1: Sales summary ──────────────────────────────────────────────

    def _build_sales_section(self) -> QGroupBox:
        box = QGroupBox("Daily Sales Summary")
        box.setStyleSheet(_GROUP_STYLE)
        lay = QVBoxLayout(box)
        lay.setSpacing(6)

        t = self._totals

        def _row(label, value, color='#e6edf3', bold=False):
            row = QHBoxLayout()
            row.addWidget(self._row_lbl(label, color='#8b949e'))
            row.addStretch()
            row.addWidget(self._row_lbl(f"${value:,.2f}", bold=bold, color=color))
            return row

        lay.addLayout(_row("Cash",    t.get('cash',    0)))
        lay.addLayout(_row("EFTPOS",  t.get('eftpos',  0)))
        lay.addLayout(_row("Account", t.get('account', 0)))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a3a4a;")
        lay.addWidget(sep)

        lay.addLayout(_row("Total Sales", t.get('total', 0),
                           color='#4CAF50', bold=True))

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #2a3a4a;")
        lay.addWidget(sep2)

        ex_row = QHBoxLayout()
        ex_row.addWidget(self._row_lbl("Subtotal ex. GST", color='#8b949e', size=12))
        ex_row.addStretch()
        ex_row.addWidget(self._row_lbl(f"${t.get('subtotal', 0):,.2f}", size=12))
        gst_row = QHBoxLayout()
        gst_row.addWidget(self._row_lbl("GST", color='#8b949e', size=12))
        gst_row.addStretch()
        gst_row.addWidget(self._row_lbl(f"${t.get('gst_amount', 0):,.2f}", size=12))
        lay.addLayout(ex_row)
        lay.addLayout(gst_row)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("color: #2a3a4a;")
        lay.addWidget(sep3)

        txn_row = QHBoxLayout()
        txn_row.addWidget(self._row_lbl("Transactions", color='#8b949e'))
        txn_row.addStretch()
        txn_row.addWidget(self._row_lbl(str(t.get('txn_count', 0)), bold=True))
        lay.addLayout(txn_row)

        return box

    # ── Section 2: Float count ────────────────────────────────────────────────

    def _build_float_section(self) -> QGroupBox:
        box = QGroupBox("Float Count")
        box.setStyleSheet(_GROUP_STYLE)
        outer = QVBoxLayout(box)
        outer.setSpacing(8)

        # Opening float and expected
        float_open = self._shift['float_open'] if self._shift else 0.0
        cash_sales = self._totals.get('cash', 0.0)
        expected   = float_open + cash_sales

        info = QHBoxLayout()
        info.addWidget(self._row_lbl(
            f"Opening float: <b>${float_open:,.2f}</b>",
            color='#8b949e', size=13))
        info.addStretch()
        self._expected_lbl = self._row_lbl(
            f"Expected in drawer: <b>${expected:,.2f}</b>",
            color='#8b949e', size=13)
        self._expected_lbl.setTextFormat(Qt.TextFormat.RichText)
        info.addWidget(self._expected_lbl)
        outer.addLayout(info)
        self._expected_val = expected

        # Denominations side by side: notes (left) | coins (right)
        denom_row = QHBoxLayout()
        denom_row.setSpacing(16)
        denom_row.addWidget(self._build_denom_group("Notes", _NOTES))
        denom_row.addWidget(self._build_denom_group("Coins", _COINS))
        outer.addLayout(denom_row)

        # Totals
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a3a4a;")
        outer.addWidget(sep)

        totals_row = QHBoxLayout()
        self._counted_lbl = self._row_lbl("Counted: $0.00", bold=True, size=15)
        self._diff_lbl    = self._row_lbl(
            f"Difference: –${expected:,.2f}",
            color='#f44336', bold=True, size=15,
        )
        totals_row.addWidget(self._counted_lbl)
        totals_row.addStretch()
        totals_row.addWidget(self._diff_lbl)
        outer.addLayout(totals_row)

        return box

    def _build_denom_group(self, title: str, denoms: list) -> QGroupBox:
        grp = QGroupBox(title)
        grp.setStyleSheet(
            "QGroupBox { color: #8b949e; font-size: 12px; border: 1px solid #2a3a4a; "
            "border-radius: 4px; margin-top: 6px; padding-top: 12px; }"
        )
        grid = QGridLayout(grp)
        grid.setSpacing(6)

        for r, (cents, label) in enumerate(denoms):
            # Denomination label
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 14px; font-weight: bold; min-width: 44px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # Count field
            from PyQt6.QtWidgets import QLineEdit
            edit = QLineEdit("0")
            edit.setFixedHeight(TOUCH_BTN_HEIGHT)
            edit.setFixedWidth(80)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setStyleSheet(_EDIT_STYLE)
            edit.setValidator(QIntValidator(0, 9999, self))
            edit.textChanged.connect(self._update_float_total)
            self._denom_edits[cents] = edit

            # Subtotal label
            sub = QLabel("$0.00")
            sub.setStyleSheet("font-size: 13px; color: #8b949e; min-width: 72px;")
            sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._denom_labels[cents] = sub

            grid.addWidget(lbl,  r, 0)
            grid.addWidget(edit, r, 1)
            grid.addWidget(sub,  r, 2)

        return grp

    def _update_float_total(self):
        total_cents = 0
        for cents, edit in self._denom_edits.items():
            try:
                count = int(edit.text() or '0')
            except ValueError:
                count = 0
            sub = count * cents
            total_cents += sub
            lbl = self._denom_labels[cents]
            lbl.setText(f"${sub / 100:,.2f}")

        total = total_cents / 100
        self._float_counted = total

        diff = total - self._expected_val
        self._counted_lbl.setText(f"Counted: ${total:,.2f}")
        if diff >= 0:
            self._diff_lbl.setText(f"Difference: +${diff:,.2f}")
            self._diff_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #4CAF50;")
        else:
            self._diff_lbl.setText(f"Difference: –${abs(diff):,.2f}")
            self._diff_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #f44336;")

    # ── Section 3: EFTPOS settlement ──────────────────────────────────────────

    def _build_eftpos_section(self) -> QGroupBox:
        from hardware.eftpos import is_enabled, get_protocol
        box = QGroupBox("EFTPOS Settlement")
        box.setStyleSheet(_GROUP_STYLE)
        lay = QVBoxLayout(box)
        lay.setSpacing(10)

        if not is_enabled():
            lay.addWidget(self._row_lbl(
                "EFTPOS is disabled — no settlement required.",
                color='#8b949e', size=13))
            return box

        protocol = get_protocol()

        self._settle_status_lbl = QLabel("Not yet settled.")
        self._settle_status_lbl.setStyleSheet("font-size: 14px; color: #8b949e;")
        lay.addWidget(self._settle_status_lbl)

        if protocol == 'manual':
            instr = QLabel(
                "Please initiate settlement on your EFTPOS terminal now,\n"
                "then tap 'Confirm Settled' once the terminal confirms."
            )
            instr.setStyleSheet("font-size: 13px; color: #e6edf3;")
            instr.setWordWrap(True)
            lay.addWidget(instr)

            confirm_btn = QPushButton("Confirm Settled")
            confirm_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
            confirm_btn.setStyleSheet("""
                QPushButton { background: #2e7d32; color: white; border: none;
                              border-radius: 6px; font-size: 14px; font-weight: bold; }
                QPushButton:pressed { background: #388e3c; }
            """)
            confirm_btn.clicked.connect(self._confirm_manual_settle)
            btn_row = QHBoxLayout()
            btn_row.addWidget(confirm_btn)
            btn_row.addStretch()
            lay.addLayout(btn_row)

        else:  # linkly
            settle_btn = QPushButton("Settle EFTPOS Terminal")
            settle_btn.setObjectName("settle_btn")
            settle_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
            settle_btn.setStyleSheet("""
                QPushButton { background: #1565c0; color: white; border: none;
                              border-radius: 6px; font-size: 14px; font-weight: bold; }
                QPushButton:pressed { background: #1976d2; }
                QPushButton:disabled { background: #2a3a4a; color: #555; }
            """)
            settle_btn.clicked.connect(self._run_settle)
            btn_row = QHBoxLayout()
            btn_row.addWidget(settle_btn)
            btn_row.addStretch()
            lay.addLayout(btn_row)
            self._settle_btn = settle_btn

        return box

    def _run_settle(self):
        self._settle_btn.setEnabled(False)
        self._settle_btn.setText("Settling…")
        self._settle_status_lbl.setText("Communicating with terminal…")
        self._settle_status_lbl.setStyleSheet("font-size: 14px; color: #FF9800;")

        self._settle_thread = _SettleThread(self)
        self._settle_thread.done.connect(self._on_settle_done)
        self._settle_thread.start()

    def _on_settle_done(self, result):
        self._settle_result = result
        self._settle_btn.setText("Settle EFTPOS Terminal")
        if result.status in ('approved', 'manual_required'):
            self._settle_btn.setStyleSheet("""
                QPushButton { background: #2e7d32; color: white; border: none;
                              border-radius: 6px; font-size: 14px; font-weight: bold; }
            """)
            self._settle_status_lbl.setText(f"✓ Settled  {result.message}")
            self._settle_status_lbl.setStyleSheet("font-size: 14px; color: #4CAF50;")
        else:
            self._settle_btn.setEnabled(True)
            msg = result.message or result.status
            self._settle_status_lbl.setText(f"✗ {msg}")
            self._settle_status_lbl.setStyleSheet("font-size: 14px; color: #f44336;")

    def _confirm_manual_settle(self):
        from hardware.eftpos import EFTPOSResult
        self._settle_result = EFTPOSResult(
            'manual_required', message="Manually confirmed by operator"
        )
        self._settle_status_lbl.setText("✓ Settlement confirmed by operator")
        self._settle_status_lbl.setStyleSheet("font-size: 14px; color: #4CAF50;")

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background: #141e2b; border-top: 1px solid #2a3a4a;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(10)

        back_btn = QPushButton("← Back")
        back_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        back_btn.clicked.connect(self.accept)

        print_btn = QPushButton("🖨  Print Report")
        print_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        print_btn.setStyleSheet("""
            QPushButton { background: #1e2a38; color: #e6edf3; border: 1px solid #2a3a4a;
                          border-radius: 6px; font-size: 14px; }
            QPushButton:pressed { background: #253344; }
        """)
        print_btn.clicked.connect(self._print_report)

        close_btn = QPushButton("✓  Close Shift")
        close_btn.setFixedHeight(TOUCH_BTN_HEIGHT + 4)
        close_btn.setStyleSheet("""
            QPushButton { background: #1565c0; color: white; border: none;
                          border-radius: 6px; font-size: 15px; font-weight: bold; }
            QPushButton:pressed { background: #1976d2; }
        """)
        close_btn.clicked.connect(self._close_shift)

        row.addWidget(back_btn)
        row.addStretch()
        row.addWidget(print_btn)
        row.addWidget(close_btn)
        return bar

    # ── Report generation ─────────────────────────────────────────────────────

    def _build_report_text(self) -> str:
        t      = self._totals
        float_open    = self._shift['float_open'] if self._shift else 0.0
        float_counted = getattr(self, '_float_counted', 0.0)
        expected      = self._expected_val
        diff          = float_counted - expected

        settle_line = "Not settled"
        if self._settle_result:
            if self._settle_result.status in ('approved', 'manual_required'):
                ref = self._settle_result.reference
                settle_line = ("Settled" if not ref else f"Settled  Ref: {ref}")
            else:
                settle_line = f"Settlement {self._settle_result.status}: {self._settle_result.message}"

        date_str = datetime.date.today().strftime("%A %-d %B %Y")
        w = 36
        lines = [
            "=" * w,
            self._store_name.center(w),
            "END OF DAY REPORT".center(w),
            date_str.center(w),
            "=" * w,
            "",
            "DAILY SALES",
            "-" * w,
            f"{'Cash':<20}  ${t.get('cash',    0):>10,.2f}",
            f"{'EFTPOS':<20}  ${t.get('eftpos',  0):>10,.2f}",
            f"{'Account':<20}  ${t.get('account', 0):>10,.2f}",
            "-" * w,
            f"{'TOTAL SALES':<20}  ${t.get('total',    0):>10,.2f}",
            "",
            f"{'Subtotal ex. GST':<20}  ${t.get('subtotal',   0):>10,.2f}",
            f"{'GST':<20}  ${t.get('gst_amount', 0):>10,.2f}",
            f"{'Transactions':<20}  {t.get('txn_count', 0):>11}",
            "",
            "FLOAT COUNT",
            "-" * w,
            f"{'Opening float':<20}  ${float_open:>10,.2f}",
            f"{'Expected in drawer':<20}  ${expected:>10,.2f}",
            f"{'Counted':<20}  ${float_counted:>10,.2f}",
            f"{'Difference':<20}  ${diff:>+10,.2f}",
            "",
            "EFTPOS SETTLEMENT",
            "-" * w,
            settle_line,
            "",
            "=" * w,
        ]
        return "\n".join(lines)

    def _print_report(self):
        report_text = self._build_report_text()

        # Show on screen
        dlg = QDialog(self)
        dlg.setWindowTitle("EOD Report")
        dlg.setModal(True)
        dlg.setStyleSheet(_BASE_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)

        from PyQt6.QtWidgets import QTextEdit
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(report_text)
        txt.setStyleSheet("""
            QTextEdit { background: #111820; color: #e6edf3;
                        font-family: monospace; font-size: 13px;
                        border: 1px solid #2a3a4a; border-radius: 4px; }
        """)
        lay.addWidget(txt)

        btn_row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        close_btn.clicked.connect(dlg.accept)
        print_hw_btn = QPushButton("🖨  Send to Printer")
        print_hw_btn.setFixedHeight(TOUCH_BTN_HEIGHT)
        print_hw_btn.setStyleSheet("""
            QPushButton { background: #1565c0; color: white; border: none;
                          border-radius: 6px; font-size: 14px; font-weight: bold; }
        """)
        print_hw_btn.clicked.connect(lambda: self._send_to_printer(report_text, dlg))
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        btn_row.addWidget(print_hw_btn)
        lay.addLayout(btn_row)

        dlg.resize(480, 560)
        dlg.exec()

    def _send_to_printer(self, report_text: str, parent_dlg):
        from hardware import printer
        try:
            printer.print_receipt({'report': report_text,
                                   'reference': f"EOD-{self._today}"})
            QMessageBox.information(parent_dlg, "Printer", "Report sent to printer.")
        except Exception as e:
            QMessageBox.warning(parent_dlg, "Printer Error", str(e))

    # ── Close shift ───────────────────────────────────────────────────────────

    def _close_shift(self):
        if not self._shift:
            QMessageBox.warning(self, "No Open Shift",
                                "There is no open shift to close.")
            return

        float_counted = getattr(self, '_float_counted', 0.0)

        reply = QMessageBox.question(
            self, "Close Shift",
            f"Close the current shift?\n\n"
            f"Float counted: ${float_counted:,.2f}\n"
            f"Total sales:   ${self._totals.get('total', 0):,.2f}\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            shift_model.close_shift(self._shift['id'], float_close=float_counted)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not close shift:\n{e}")
            logging.exception("close_shift failed")
            return

        self._print_report()
        QMessageBox.information(self, "Shift Closed", "Shift has been closed.")
        self.accept()
