"""
Transaction history dialog.
Full-screen, opened from the POS header.
Supports date-range filtering and tap-to-view line items.
"""
from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget,
    QLineEdit, QFrame, QAbstractItemView, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

import models.transaction as txn_model
from utils.format import currency

_DARK_BG  = "#1a2332"
_CARD_BG  = "#1e2a38"
_BORDER   = "#2a3a4a"
_TEXT     = "#e6edf3"
_DIM      = "#8b949e"
_GREEN    = "#4CAF50"
_BLUE     = "#2196F3"
_ORANGE   = "#FF9800"
_RED      = "#f44336"

_ROW_H    = 52    # transaction list row height
_DET_ROW  = 44    # detail line row height


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {_BORDER};")
    return f


def _lbl(text, size=13, color=_TEXT, bold=False) -> QLabel:
    l = QLabel(text)
    w = "bold" if bold else "normal"
    l.setStyleSheet(f"font-size: {size}px; font-weight: {w}; color: {color}; background: transparent;")
    return l


# ── Date-range quick-select helpers ──────────────────────────────────────────

_RANGES = [
    ("Today",      0,   0),
    ("Yesterday",  1,   1),
    ("This Week",  6,   0),
    ("This Month", 29,  0),
    ("Last 90",    89,  0),
]


# ── Transaction detail dialog ─────────────────────────────────────────────────

class _DetailDialog(QDialog):
    def __init__(self, txn: dict, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"Transaction {txn['reference']}")
        self.setMinimumSize(680, 520)
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        self._txn = txn
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        # ── Header ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        ref_lbl = _lbl(self._txn['reference'], size=18, bold=True)
        hdr.addWidget(ref_lbl)
        hdr.addStretch()
        close_btn = QPushButton("✕  Close")
        close_btn.setFixedHeight(38)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           font-size: 13px; padding: 0 16px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        close_btn.clicked.connect(self.accept)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # ── Meta row ──────────────────────────────────────────────────
        meta = QHBoxLayout()
        meta.setSpacing(28)
        meta.addWidget(_lbl(self._txn['sale_date'], color=_DIM))
        meta.addWidget(_lbl(f"👤 {self._txn['operator']}", color=_DIM))
        pmt_color = _GREEN if self._txn['payment_method'] == 'CASH' else _BLUE
        meta.addWidget(_lbl(self._txn['payment_method'], color=pmt_color, bold=True))
        sync_text = "✓ Synced" if self._txn.get('synced') else "⏳ Pending"
        sync_color = _GREEN if self._txn.get('synced') else _ORANGE
        meta.addWidget(_lbl(sync_text, color=sync_color))
        meta.addStretch()
        lay.addLayout(meta)

        lay.addWidget(_sep())

        # ── Line items table ──────────────────────────────────────────
        lines = txn_model.get_lines(self._txn['id'])

        tbl = QTableWidget()
        tbl.setColumnCount(4)
        tbl.setHorizontalHeaderLabels(["Description", "Qty", "Unit Price", "Line Total"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(1, 80)
        tbl.setColumnWidth(2, 110)
        tbl.setColumnWidth(3, 110)
        tbl.verticalHeader().setVisible(False)
        tbl.verticalHeader().setDefaultSectionSize(_DET_ROW)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setAlternatingRowColors(True)
        tbl.setStyleSheet(f"""
            QTableWidget {{
                background: {_CARD_BG}; alternate-background-color: #1b2738;
                gridline-color: {_BORDER}; font-size: 14px; border: none;
            }}
            QHeaderView::section {{
                background: #152030; color: {_DIM};
                font-size: 12px; font-weight: bold; padding: 6px;
                border: none; border-bottom: 1px solid {_BORDER};
            }}
        """)

        tbl.setRowCount(len(lines))
        for r, line in enumerate(lines):
            desc_item = QTableWidgetItem(line['description'])
            qty_item  = QTableWidgetItem(f"{line['qty']:g}")
            up_item   = QTableWidgetItem(currency(line['unit_price']))
            lt_item   = QTableWidgetItem(currency(line['line_total']))
            for item in (qty_item, up_item, lt_item):
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            tbl.setItem(r, 0, desc_item)
            tbl.setItem(r, 1, qty_item)
            tbl.setItem(r, 2, up_item)
            tbl.setItem(r, 3, lt_item)

        lay.addWidget(tbl, stretch=1)

        lay.addWidget(_sep())

        # ── Totals footer ─────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.addStretch()
        totals = QVBoxLayout()
        totals.setSpacing(4)

        def _row(label, value, big=False, color=_TEXT):
            row = QHBoxLayout()
            ll = _lbl(label, size=15 if big else 13, color=_DIM)
            vl = _lbl(value, size=20 if big else 13, color=color, bold=big)
            vl.setAlignment(Qt.AlignmentFlag.AlignRight)
            vl.setMinimumWidth(120)
            row.addWidget(ll)
            row.addWidget(vl)
            return row

        subtotal  = self._txn.get('subtotal', 0)
        gst       = self._txn.get('gst_amount', 0)
        total     = self._txn.get('total', 0)
        tendered  = self._txn.get('tendered', 0)
        change    = self._txn.get('change_given', 0)

        totals.addLayout(_row("Subtotal (ex. GST)", currency(subtotal)))
        totals.addLayout(_row("GST included", currency(gst)))
        totals.addLayout(_row("TOTAL", currency(total), big=True, color=_GREEN))
        totals.addSpacing(6)
        totals.addLayout(_row("Tendered", currency(tendered)))
        if change > 0:
            totals.addLayout(_row("Change", currency(change), color=_ORANGE))

        footer.addLayout(totals)
        lay.addLayout(footer)


# ── Transaction history main dialog ──────────────────────────────────────────

class TransactionHistory(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Transaction History")
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")

        self._date_from = date.today()
        self._date_to   = date.today()
        self._active_range = "Today"
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._load)

        self._build()
        self.showFullScreen()
        QTimer.singleShot(0, self._load)

    # ── Build ─────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_header())
        lay.addWidget(self._build_filter_bar())
        lay.addWidget(self._build_summary_bar())
        lay.addWidget(self._build_table(), stretch=1)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setObjectName("thBar")
        bar.setStyleSheet(
            f"QWidget#thBar {{ background: {_CARD_BG}; border-bottom: 1px solid {_BORDER}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)

        title = _lbl("Transaction History", size=18, bold=True)
        lay.addWidget(title)
        lay.addStretch()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search reference or operator…")
        self._search_input.setFixedHeight(36)
        self._search_input.setFixedWidth(300)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_DARK_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 6px;
                padding: 0 12px; font-size: 14px;
            }}
            QLineEdit:focus {{ border-color: {_BLUE}; }}
        """)
        self._search_input.textChanged.connect(lambda: self._search_timer.start(300))
        lay.addWidget(self._search_input)

        lay.addSpacing(16)

        close_btn = QPushButton("✕  Close")
        close_btn.setFixedHeight(38)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           font-size: 13px; padding: 0 16px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        return bar

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("thFilter")
        bar.setFixedHeight(54)
        bar.setStyleSheet(
            f"QWidget#thFilter {{ background: {_CARD_BG}; border-bottom: 1px solid {_BORDER}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(8)

        self._range_btns: dict[str, QPushButton] = {}
        for label, back_days, fwd_days in _RANGES:
            btn = QPushButton(label)
            btn.setFixedHeight(36)
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _, l=label, b=back_days, f=fwd_days: self._set_range(l, b, f)
            )
            lay.addWidget(btn)
            self._range_btns[label] = btn

        lay.addStretch()

        # Date range labels
        self._date_lbl = _lbl("", size=12, color=_DIM)
        lay.addWidget(self._date_lbl)

        self._style_range_btns()
        return bar

    def _build_summary_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("thSummary")
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            f"QWidget#thSummary {{ background: #141e2b; border-bottom: 1px solid {_BORDER}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(32)

        self._count_lbl   = _lbl("", size=12, color=_DIM)
        self._revenue_lbl = _lbl("", size=12, color=_DIM)
        self._cash_lbl    = _lbl("", size=12, color=_DIM)
        self._eftpos_lbl  = _lbl("", size=12, color=_DIM)

        for lbl in (self._count_lbl, self._revenue_lbl, self._cash_lbl, self._eftpos_lbl):
            lay.addWidget(lbl)
        lay.addStretch()
        return bar

    def _build_table(self) -> QWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Reference", "Operator", "Payment", "Total", "●"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 160)
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(3, 100)
        self._table.setColumnWidth(4, 110)
        self._table.setColumnWidth(5, 44)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_CARD_BG}; alternate-background-color: #1b2738;
                gridline-color: {_BORDER}; font-size: 14px; border: none;
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
        self._table.cellDoubleClicked.connect(self._on_row_tapped)
        self._table.itemActivated.connect(
            lambda item: self._on_row_tapped(item.row(), item.column())
        )
        # Single tap on touch screens
        self._table.cellPressed.connect(self._on_row_tapped)
        return self._table

    # ── Filtering ─────────────────────────────────────────────────────

    def _set_range(self, label: str, back_days: int, fwd_days: int):
        today = date.today()
        self._date_from    = today - timedelta(days=back_days)
        self._date_to      = today + timedelta(days=fwd_days)
        self._active_range = label
        self._style_range_btns()
        self._load()

    def _style_range_btns(self):
        for label, btn in self._range_btns.items():
            active = label == self._active_range
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{ background: {_BLUE}; color: white;
                                   border: none; border-radius: 6px;
                                   font-size: 13px; font-weight: bold;
                                   padding: 0 16px; }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{ background: {_CARD_BG}; color: {_DIM};
                                   border: 1px solid {_BORDER}; border-radius: 6px;
                                   font-size: 13px; padding: 0 16px; }}
                    QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
                """)

    # ── Data load ─────────────────────────────────────────────────────

    def _load(self):
        df = self._date_from.isoformat()
        dt = self._date_to.isoformat()
        search = self._search_input.text()

        self._date_lbl.setText(
            f"{df}" if df == dt else f"{df}  →  {dt}"
        )

        txns    = txn_model.get_history(df, dt, search)
        summary = txn_model.get_period_summary(df, dt)
        self._populate_table(txns)
        self._populate_summary(summary)

    def _populate_table(self, txns: list):
        self._txns = txns
        self._table.setRowCount(len(txns))
        for r, txn in enumerate(txns):
            # Date/time: use created_at if available, else sale_date
            ts = txn.get('created_at') or txn['sale_date']
            if 'T' in str(ts):
                dt_str = str(ts).replace('T', '  ')[:18]
            else:
                dt_str = str(ts)[:16]

            pmt_color = _GREEN if txn['payment_method'] == 'CASH' else _BLUE

            def _cell(text, align=Qt.AlignmentFlag.AlignLeft, color=_TEXT):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(QColor(color))
                return item

            self._table.setItem(r, 0, _cell(dt_str))
            self._table.setItem(r, 1, _cell(txn['reference']))
            self._table.setItem(r, 2, _cell(txn['operator']))
            self._table.setItem(r, 3, _cell(
                txn['payment_method'], color=pmt_color
            ))
            self._table.setItem(r, 4, _cell(
                currency(txn['total']),
                align=Qt.AlignmentFlag.AlignRight,
            ))
            dot = QTableWidgetItem("●")
            dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setForeground(QColor(_GREEN if txn.get('synced') else _ORANGE))
            dot.setToolTip("Synced to BackOfficePro" if txn.get('synced') else "Pending sync")
            self._table.setItem(r, 5, dot)

        if not txns:
            self._table.setRowCount(1)
            no_data = QTableWidgetItem("No transactions found for this period")
            no_data.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            no_data.setForeground(QColor(_DIM))
            self._table.setItem(0, 0, no_data)
            self._table.setSpan(0, 0, 1, 6)

    def _populate_summary(self, summary: dict):
        self._count_lbl.setText(f"Transactions: {summary['count']}")
        self._revenue_lbl.setText(f"Total revenue: {currency(summary['revenue'])}")
        cash  = next((b['amt'] for b in summary['breakdown'] if b['payment_method']=='CASH'),  0)
        eftpos= next((b['amt'] for b in summary['breakdown'] if b['payment_method']=='EFTPOS'), 0)
        self._cash_lbl.setText(f"Cash: {currency(cash)}")
        self._eftpos_lbl.setText(f"EFTPOS: {currency(eftpos)}")

    # ── Row tap ───────────────────────────────────────────────────────

    def _on_row_tapped(self, row: int, col: int):
        if not hasattr(self, '_txns') or row >= len(self._txns):
            return
        txn = self._txns[row]
        dlg = _DetailDialog(txn, self)
        dlg.exec()

    # ── Keyboard ──────────────────────────────────────────────────────

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(e)
