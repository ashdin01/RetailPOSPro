"""
Widget tests for views/refund_dialog.py — the "Refund" flow opened from a
SALE's TransactionDetailDialog. Cash only; per-line qty is capped locally at
what get_refunded_qty_by_barcode says remains un-refunded.
"""
import pytest
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog

import hardware.printer as printer
import models.transaction as txn_model
from views.refund_dialog import RefundDialog

_TWO_LINES = [
    {'barcode': '111', 'description': 'Milk', 'qty': 2,
     'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 7.0},
    {'barcode': '222', 'description': 'Bread', 'qty': 1,
     'unit_price': 4.0, 'tax_rate': 10.0, 'line_total': 4.0},
]


@pytest.fixture()
def sale(test_db):
    return txn_model.create("alice", None, _TWO_LINES, "CASH", 11.0, 11.0, 0.0)


@pytest.fixture(autouse=True)
def _no_real_printing(monkeypatch):
    monkeypatch.setattr(printer, 'print_receipt', lambda txn: None)


class TestRefundDialogBuild:
    def test_shows_one_row_per_refundable_line(self, qtbot, sale):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        assert dlg._table.rowCount() == 2

    def test_default_qty_is_full_remaining(self, qtbot, sale):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        assert dlg._spinboxes[0].value() == pytest.approx(2.0)
        assert dlg._spinboxes[1].value() == pytest.approx(1.0)

    def test_spinbox_max_capped_at_remaining(self, qtbot, sale):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        dlg._spinboxes[0].setValue(999)
        assert dlg._spinboxes[0].value() == pytest.approx(2.0)

    def test_already_refunded_line_excluded_from_table(self, qtbot, sale):
        txn_model.create_refund("alice", None, sale['reference'], [_TWO_LINES[0]])  # refunds the Milk line fully
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        assert dlg._table.rowCount() == 1
        assert dlg._refundable[0]['barcode'] == '222'

    def test_nothing_refundable_disables_confirm(self, qtbot, sale):
        txn_model.create_refund("alice", None, sale['reference'], _TWO_LINES)  # refund everything
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        confirm_btn = next(b for b in dlg.findChildren(QPushButton) if b.text() == 'Confirm Refund')
        assert not confirm_btn.isEnabled()


class TestRefundDialogConfirm:
    def test_confirm_with_zero_qty_shows_warning_and_does_not_create_refund(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        for spin in dlg._spinboxes.values():
            spin.setValue(0)
        warned = []
        monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: warned.append(a))
        dlg._confirm()
        assert len(warned) == 1
        assert txn_model.get_refunded_qty_by_barcode(sale['reference']) == {}

    def test_confirm_declined_does_not_create_refund(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **k: QMessageBox.StandardButton.No)
        dlg._confirm()
        assert txn_model.get_refunded_qty_by_barcode(sale['reference']) == {}

    def test_confirm_accepted_creates_refund_and_accepts_dialog(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)

        with patch.object(dlg, 'accept') as mock_accept:
            dlg._confirm()
        mock_accept.assert_called_once()

        refunded = txn_model.get_refunded_qty_by_barcode(sale['reference'])
        assert refunded['111'] == pytest.approx(2.0)
        assert refunded['222'] == pytest.approx(1.0)

    def test_partial_qty_selection_only_refunds_that_amount(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        dlg._spinboxes[0].setValue(1)   # only 1 of the 2 Milk
        dlg._spinboxes[1].setValue(0)   # none of the Bread
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
        dlg._confirm()

        refunded = txn_model.get_refunded_qty_by_barcode(sale['reference'])
        assert refunded['111'] == pytest.approx(1.0)
        assert '222' not in refunded

    def test_confirm_prints_receipt(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **k: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
        printed = []
        monkeypatch.setattr(printer, 'print_receipt', lambda txn: printed.append(txn))
        dlg._confirm()
        assert len(printed) == 1
        assert printed[0]['transaction_type'] == 'REFUND'

    def test_create_refund_exception_shows_warning_and_does_not_accept(self, qtbot, sale, monkeypatch):
        dlg = RefundDialog(sale, operator='alice', shift_id=None)
        qtbot.addWidget(dlg)
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **k: QMessageBox.StandardButton.Yes)
        warned = []
        monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: warned.append(a))
        monkeypatch.setattr(
            txn_model, 'create_refund',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db boom')),
        )
        with patch.object(dlg, 'accept') as mock_accept:
            dlg._confirm()
        mock_accept.assert_not_called()
        assert len(warned) == 1
