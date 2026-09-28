"""
Refund dialog — opened from a SALE's TransactionDetailDialog "Refund" button.

Cash only: hardware/eftpos.py's Linkly integration has no "Refund" transaction
type implemented (only Purchase and Settlement), so an EFTPOS-paid sale never
reaches this dialog — TransactionDetailDialog shows a manual-refund note
instead. See models.transaction.create_refund for why refunds are stored as
negative-signed transactions rather than a new status value.

Per-line refund quantities are capped locally at what get_refunded_qty_by_barcode
says remains un-refunded, purely so staff can't even try to over-refund from the
UI — BackOfficePro independently re-validates against its own stock_movements
ledger regardless when the refund syncs, and rejects (409) anything that slips
through.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDoubleSpinBox, QMessageBox,
)
from PyQt6.QtCore import Qt

import models.transaction as txn_model
import hardware.printer as printer
from utils.format import currency

_DARK_BG  = "#1a2332"
_CARD_BG  = "#1e2a38"
_BORDER   = "#2a3a4a"
_TEXT     = "#e6edf3"
_DIM      = "#8b949e"
_GREEN    = "#4CAF50"
_RED      = "#f44336"

_ROW_H = 48


class RefundDialog(QDialog):
    def __init__(self, original_txn: dict, parent=None, operator=None, shift_id=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"Refund {original_txn['reference']}")
        self.setMinimumSize(620, 480)
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        self._txn      = original_txn
        self._operator = operator or 'unknown'
        self._shift_id = shift_id
        self._spinboxes: dict[int, QDoubleSpinBox] = {}   # row -> qty spinbox

        lines = txn_model.get_lines(original_txn['id'])
        refunded = txn_model.get_refunded_qty_by_barcode(original_txn['reference'])
        self._refundable = [
            {**l, 'remaining': l['qty'] - refunded.get(l['barcode'], 0.0)}
            for l in lines
        ]
        self._refundable = [l for l in self._refundable if l['remaining'] > 1e-9]

        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel(f"Refund — {self._txn['reference']}")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {_TEXT};")
        lay.addWidget(title)

        hint = QLabel("Set the quantity to refund for each item, then confirm. Cash only.")
        hint.setStyleSheet(f"font-size: 13px; color: {_DIM};")
        lay.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Description", "Sold", "Remaining", "Unit Price", "Refund Qty"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 100)
        self._table.setColumnWidth(4, 110)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_CARD_BG}; gridline-color: {_BORDER};
                font-size: 14px; border: none;
            }}
            QHeaderView::section {{
                background: #152030; color: {_DIM};
                font-size: 12px; font-weight: bold; padding: 6px;
                border: none; border-bottom: 1px solid {_BORDER};
            }}
        """)

        self._table.setRowCount(len(self._refundable))
        for r, line in enumerate(self._refundable):
            self._table.setItem(r, 0, QTableWidgetItem(line['description']))
            sold_item = QTableWidgetItem(f"{line['qty']:g}")
            rem_item  = QTableWidgetItem(f"{line['remaining']:g}")
            up_item   = QTableWidgetItem(currency(line['unit_price']))
            for item in (sold_item, rem_item, up_item):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(r, 1, sold_item)
            self._table.setItem(r, 2, rem_item)
            self._table.setItem(r, 3, up_item)

            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setMinimum(0.0)
            spin.setMaximum(line['remaining'])
            spin.setValue(line['remaining'])   # default: refund everything left on this line
            spin.setSingleStep(1.0)
            spin.setStyleSheet(f"""
                QDoubleSpinBox {{ background: {_DARK_BG}; color: {_TEXT};
                                   border: 1px solid {_BORDER}; border-radius: 4px;
                                   padding: 2px 6px; }}
            """)
            self._table.setCellWidget(r, 4, spin)
            self._spinboxes[r] = spin

        lay.addWidget(self._table, stretch=1)

        if not self._refundable:
            lay.addWidget(QLabel("Nothing left to refund on this sale."))

        self._total_lbl = QLabel()
        self._total_lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {_RED};")
        lay.addWidget(self._total_lbl)
        for spin in self._spinboxes.values():
            spin.valueChanged.connect(self._refresh_total)
        self._refresh_total()

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 6px;
                           font-size: 13px; padding: 0 16px; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton("Confirm Refund")
        confirm_btn.setFixedHeight(38)
        confirm_btn.setEnabled(bool(self._refundable))
        confirm_btn.setStyleSheet(f"""
            QPushButton {{ background: {_RED}; color: white; border: none;
                           border-radius: 6px; font-size: 13px; font-weight: bold;
                           padding: 0 20px; }}
            QPushButton:disabled {{ background: {_BORDER}; color: {_DIM}; }}
        """)
        confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(confirm_btn)
        lay.addLayout(btn_row)

    def _selected_lines(self) -> list:
        result = []
        for r, line in enumerate(self._refundable):
            qty = self._spinboxes[r].value()
            if qty <= 0:
                continue
            unit_price = line['unit_price']
            line_total = round(qty * unit_price, 2)
            result.append({
                'barcode':     line['barcode'],
                'description': line['description'],
                'qty':         qty,
                'unit_price':  unit_price,
                'tax_rate':    line['tax_rate'],
                'line_total':  line_total,
            })
        return result

    def _refresh_total(self):
        total = sum(l['line_total'] for l in self._selected_lines())
        self._total_lbl.setText(f"Refund total: {currency(total)}")

    def _confirm(self):
        lines = self._selected_lines()
        if not lines:
            QMessageBox.warning(self, "Nothing selected",
                                "Set a quantity greater than zero on at least one line.")
            return

        total = sum(l['line_total'] for l in lines)
        reply = QMessageBox.question(
            self, "Confirm Refund",
            f"Refund {currency(total)} cash against {self._txn['reference']}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            refund = txn_model.create_refund(
                self._operator, self._shift_id, self._txn['reference'], lines
            )
        except Exception as e:
            QMessageBox.warning(self, "Refund Failed", f"Could not record the refund:\n{e}")
            return

        printer.print_receipt(refund)
        QMessageBox.information(
            self, "Refund Recorded",
            f"{refund['reference']} — {currency(abs(refund['total']))} refunded."
        )
        self.accept()
