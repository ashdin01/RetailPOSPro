"""
Widget tests for the POS header's BackOfficePro status indicator
(views/pos_screen.py: _set_online_status / _set_pending_count / _refresh_status_display)
and for _flush_sync_queue()'s interaction with the sync-retry model layer.

These guard against the "green while every sale silently fails to sync" bug:
the online/offline health check and the sync-queue depth are independent
signals, and the header must reflect BOTH, not just the health check.

api.backoffice_client and models.transaction are mocked — no real HTTP or DB
sync side effects beyond what each test sets up via the test_db fixture.
"""
import pytest
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication

import api.backoffice_client as bop
import models.transaction as txn_model


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


class TestStatusIndicator:
    def test_offline_shows_red_regardless_of_pending(self, pos_screen):
        pos_screen._set_pending_count(3)
        pos_screen._set_online_status(False)
        assert pos_screen._status_lbl.text() == "Offline"
        assert "#f44336" in pos_screen._status_dot.styleSheet()

    def test_online_with_no_pending_shows_green(self, pos_screen):
        pos_screen._set_pending_count(0)
        pos_screen._set_online_status(True)
        assert pos_screen._status_lbl.text() == "BackOfficePro"
        assert "#4CAF50" in pos_screen._status_dot.styleSheet()

    def test_online_with_pending_shows_amber_not_green(self, pos_screen):
        """The bug this guards against: a reachable server (health check OK)
        with a bad API key must not read as fully healthy — sales are still
        failing to sync."""
        pos_screen._set_online_status(True)
        pos_screen._set_pending_count(2)
        assert pos_screen._status_lbl.text() == "BackOfficePro — 2 pending"
        assert "#FF9800" in pos_screen._status_dot.styleSheet()

    def test_pending_count_updates_independently_of_health_signal_order(self, pos_screen):
        # Pending count can arrive before or after the health result — either
        # order must land on the same amber state.
        pos_screen._set_pending_count(1)
        pos_screen._set_online_status(True)
        assert "pending" in pos_screen._status_lbl.text()


class TestFlushSyncQueue:
    def test_successful_post_marks_synced(self, pos_screen, monkeypatch):
        result = txn_model.create(
            "alice", None,
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
            "CASH", 3.5, 3.5, 0.0,
        )
        monkeypatch.setattr(bop, 'post_sale', lambda data: {'ok': True})
        pos_screen._flush_sync_queue()
        assert not any(p['id'] == result['id'] for p in txn_model.get_pending_sync())

    def test_failed_post_schedules_backoff_not_permanent_removal(self, pos_screen, monkeypatch):
        result = txn_model.create(
            "alice", None,
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
            "CASH", 3.5, 3.5, 0.0,
        )
        monkeypatch.setattr(bop, 'post_sale', lambda data: None)
        pos_screen._flush_sync_queue()
        # Not immediately pending again (it's backed off)...
        assert not any(p['id'] == result['id'] for p in txn_model.get_pending_sync())
        # ...but still counted as unsynced, and still in the queue for a future retry.
        assert txn_model.get_pending_sync_count() == 1

    def test_refund_transaction_posts_to_post_refund_not_post_sale(self, pos_screen, monkeypatch):
        sale = txn_model.create(
            "alice", None,
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
            "CASH", 3.5, 3.5, 0.0,
        )
        txn_model.mark_synced(sale['id'])
        refund = txn_model.create_refund(
            "alice", None, sale['reference'],
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
        )

        sale_calls, refund_calls = [], []
        monkeypatch.setattr(bop, 'post_sale', lambda data: sale_calls.append(data) or {'ok': True})
        monkeypatch.setattr(bop, 'post_refund', lambda data: refund_calls.append(data) or {'ok': True})
        pos_screen._flush_sync_queue()

        assert len(refund_calls) == 1
        assert sale_calls == []
        payload = refund_calls[0]
        assert payload['reference'] == refund['reference']
        assert payload['original_reference'] == sale['reference']
        # Wire format is positive-magnitude even though it's stored negative locally.
        assert payload['total'] == 3.5
        assert payload['lines'][0]['qty'] == 1.0
        assert payload['lines'][0]['line_total'] == 3.5

    def test_refund_marked_synced_on_success(self, pos_screen, monkeypatch):
        sale = txn_model.create(
            "alice", None,
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
            "CASH", 3.5, 3.5, 0.0,
        )
        refund = txn_model.create_refund(
            "alice", None, sale['reference'],
            [{'barcode': '123', 'description': 'Milk', 'qty': 1,
              'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5}],
        )
        monkeypatch.setattr(bop, 'post_sale', lambda data: {'ok': True})
        monkeypatch.setattr(bop, 'post_refund', lambda data: {'ok': True})
        pos_screen._flush_sync_queue()
        assert not any(p['id'] == refund['id'] for p in txn_model.get_pending_sync())
