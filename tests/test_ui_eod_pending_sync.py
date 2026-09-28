"""
Widget test for views/eod_dialog.py's pending-sync warning: closing the shift
while sales are still sitting unsynced in sync_queue must interrupt staff
with a confirmation, rather than letting the day close silently on top of
sales that never made it to BackOfficePro.
"""
import pytest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication, QMessageBox

import models.shift as shift_model
import models.transaction as txn_model
from views.eod_dialog import EODDialog

_ITEMS = [{
    'barcode': '123', 'description': 'Milk', 'qty': 1,
    'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5,
}]


@pytest.fixture()
def open_shift(test_db, operator_id):
    return shift_model.open_shift(operator_id, "Test Operator", float_open=100.0)


@pytest.fixture()
def eod(qtbot, open_shift, operator_id):
    dlg = EODDialog(operator_id=operator_id)
    qtbot.addWidget(dlg)
    QApplication.processEvents()
    return dlg


@pytest.fixture(autouse=True)
def _no_blocking_info_dialogs(monkeypatch):
    """_close_shift() ends with a real QMessageBox.information("Shift Closed")
    on success — under headless Qt that blocks forever waiting for a click
    that will never come. Every test here goes through _close_shift, so
    stub it out globally rather than per-test."""
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)


class TestPendingSyncWarning:
    def test_no_warning_when_everything_synced(self, eod, monkeypatch):
        asked = []
        monkeypatch.setattr(
            QMessageBox, 'question',
            lambda *a, **k: (asked.append(a) or QMessageBox.StandardButton.Yes)
        )
        with patch.object(eod, '_print_report'):
            eod._close_shift()
        # Only the final "close shift?" confirmation should have fired —
        # no separate pending-sync prompt.
        assert len(asked) == 1
        assert "Close the current shift" in asked[0][2]

    def test_warns_and_blocks_close_when_sales_unsynced(self, eod, monkeypatch):
        txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)

        prompts = []

        def _question(*args, **kwargs):
            prompts.append(args[2])
            # Decline both prompts — proves the unsynced warning alone is
            # enough to stop the close.
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, 'question', _question)
        eod._close_shift()

        assert len(prompts) == 1
        assert "1 sale hasn't synced" in prompts[0]
        # Shift must still be open — the close was correctly aborted.
        assert shift_model.get_open_shift(eod._operator_id) is not None

    def test_can_proceed_past_warning(self, eod, monkeypatch):
        txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        monkeypatch.setattr(
            QMessageBox, 'question',
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        with patch.object(eod, '_print_report'):
            eod._close_shift()
        assert shift_model.get_open_shift(eod._operator_id) is None
