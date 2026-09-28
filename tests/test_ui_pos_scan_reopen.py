"""
Widget tests for scanning a printed receipt barcode back in at the POS scan
bar (views/pos_screen.py: _RECEIPT_REFERENCE_RE, _lookup_and_add, _reopen_by_scan).

Scanning a sale's or refund's own reference must reopen its detail view
instead of being treated as a product barcode lookup, without colliding with
the existing 'HLD-' hold-ticket-resume prefix or ordinary numeric/PLU scans.
"""
import pytest
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication

import api.backoffice_client as bop
import models.transaction as txn_model
from views.pos_screen import _RECEIPT_REFERENCE_RE

_ITEMS = [{
    'barcode': '123', 'description': 'Milk', 'qty': 1,
    'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5,
}]


@pytest.fixture(autouse=True)
def _mock_background_sync():
    with patch.object(bop, 'sync_product_cache', return_value=0), \
         patch.object(bop, 'get_store_info', return_value=None), \
         patch.object(bop, 'fetch_bundles', return_value=[]), \
         patch.object(bop, 'check_health', return_value=False):
        yield


@pytest.fixture()
def pos_screen(qtbot, test_db):
    from views.pos_screen import POSScreen
    widget = POSScreen(operator={'id': 1, 'username': 'alice', 'full_name': 'Alice'})
    qtbot.addWidget(widget)
    widget.show()
    QApplication.processEvents()
    return widget


class TestReceiptReferenceRegex:
    @pytest.mark.parametrize("ref", [
        "POS-001-20260928-0001",
        "RFD-POS-001-20260928-0001",
        "TILL2-20260928-0042",
    ])
    def test_matches_sale_and_refund_reference_shapes(self, ref):
        assert _RECEIPT_REFERENCE_RE.match(ref)

    @pytest.mark.parametrize("text", [
        "HLD-00001",             # hold ticket — handled by its own earlier check
        "9300675009657",         # ordinary EAN-13 product barcode
        "123456",                 # PLU
        "TEMP-000001",            # temp barcode, no date-shaped segments
        "POS-001-2026092-0001",   # date segment too short
        "POS-001-20260928-001",   # seq segment too short
    ])
    def test_does_not_match_non_receipt_text(self, text):
        assert not _RECEIPT_REFERENCE_RE.match(text)


class TestScanToReopen:
    def test_scanning_a_sale_reference_opens_detail_dialog_not_basket(self, pos_screen, monkeypatch):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)

        opened = []
        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = None
        with patch('views.transaction_history.TransactionDetailDialog',
                   return_value=mock_dlg) as mock_cls:
            pos_screen._scan_input.setText(sale['reference'])
            pos_screen._on_scan_enter()

        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][0]['id'] == sale['id']
        mock_dlg.exec.assert_called_once()
        assert pos_screen._basket == []   # never treated as a product scan

    def test_scanning_a_refund_reference_opens_detail_dialog(self, pos_screen):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        refund = txn_model.create_refund("alice", None, sale['reference'], _ITEMS)

        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = None
        with patch('views.transaction_history.TransactionDetailDialog',
                   return_value=mock_dlg) as mock_cls:
            pos_screen._scan_input.setText(refund['reference'])
            pos_screen._on_scan_enter()

        assert mock_cls.call_args[0][0]['id'] == refund['id']

    def test_unknown_receipt_shaped_reference_flashes_not_found(self, pos_screen):
        with patch.object(pos_screen, '_flash_not_found') as mock_flash:
            pos_screen._scan_input.setText('POS-001-20200101-9999')
            pos_screen._on_scan_enter()
        mock_flash.assert_called_once()
        assert 'POS-001-20200101-9999' in mock_flash.call_args[0][0]

    def test_hold_ticket_scan_still_takes_priority_over_receipt_regex(self, pos_screen, monkeypatch):
        """'HLD-00001' doesn't match the receipt regex anyway, but this guards
        the ordering assumption explicitly: hold-ticket dispatch must still
        run, not get shadowed by the newer check."""
        with patch.object(pos_screen, '_resume_by_scan') as mock_resume, \
             patch('views.transaction_history.TransactionDetailDialog') as mock_detail:
            pos_screen._scan_input.setText('HLD-00001')
            pos_screen._on_scan_enter()
        mock_resume.assert_called_once_with('HLD-00001')
        mock_detail.assert_not_called()

    def test_reopens_operator_and_shift_passed_through(self, pos_screen):
        """Refund creation needs an operator/shift_id — confirm they flow
        from POSScreen into the dialog rather than being lost."""
        pos_screen._shift_id = 42
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)

        mock_dlg = MagicMock()
        mock_dlg.exec.return_value = None
        with patch('views.transaction_history.TransactionDetailDialog',
                   return_value=mock_dlg) as mock_cls:
            pos_screen._scan_input.setText(sale['reference'])
            pos_screen._on_scan_enter()

        _, kwargs = mock_cls.call_args
        assert kwargs['operator'] == 'alice'
        assert kwargs['shift_id'] == 42
