"""
Widget tests for views/transaction_history.py's three-state sync indicator
(synced / pending / stuck) and the manual retry actions (per-row "Retry Sync"
in the detail dialog, and "Retry All Failed" in the header).

Before this, the dot only ever showed synced/pending — a sale that had given
up retrying forever looked identical to one that was still trying normally.
"""
import pytest
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication

import models.transaction as txn_model
from views.transaction_history import TransactionHistory, TransactionDetailDialog, _sync_state

_ITEMS = [{
    'barcode': '123', 'description': 'Milk', 'qty': 1,
    'unit_price': 3.5, 'tax_rate': 10.0, 'line_total': 3.5,
}]


def _queue_id(transaction_id):
    from database.connection import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM sync_queue WHERE transaction_id=?", (transaction_id,)
    ).fetchone()
    conn.close()
    return row["id"]


class TestSyncState:
    def test_synced(self):
        state, color, _ = _sync_state({'synced': 1})
        assert state == 'synced' and color == '#4CAF50'

    def test_pending_below_stuck_threshold(self):
        state, color, _ = _sync_state({'synced': 0, 'sync_attempts': 1})
        assert state == 'pending' and color == '#FF9800'

    def test_stuck_at_threshold(self):
        state, color, tip = _sync_state({
            'synced': 0, 'sync_attempts': txn_model.SYNC_STUCK_ATTEMPTS,
            'sync_last_error': '401 unauthorized',
        })
        assert state == 'stuck' and color == '#f44336'
        assert '401 unauthorized' in tip

    def test_no_queue_row_defaults_to_pending(self):
        # LEFT JOIN with no sync_queue row (e.g. never queued) — attempts is None.
        state, _, _ = _sync_state({'synced': 0, 'sync_attempts': None})
        assert state == 'pending'


@pytest.fixture()
def history(qtbot, test_db):
    dlg = TransactionHistory()
    qtbot.addWidget(dlg)
    QApplication.processEvents()
    return dlg


class TestTransactionHistoryDots:
    def test_stuck_transaction_shows_red_dot(self, history):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        qid = _queue_id(result['id'])
        for _ in range(txn_model.SYNC_STUCK_ATTEMPTS):
            txn_model.mark_sync_failed(qid, "boom")
        history._load()
        row = next(i for i, t in enumerate(history._txns) if t['id'] == result['id'])
        dot = history._table.item(row, 5)
        assert dot.foreground().color().name() == '#f44336'
        assert 'boom' in dot.toolTip()

    def test_freshly_pending_transaction_shows_orange_dot(self, history):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        history._load()
        row = next(i for i, t in enumerate(history._txns) if t['id'] == result['id'])
        dot = history._table.item(row, 5)
        assert dot.foreground().color().name() == '#ff9800'

    def test_synced_transaction_shows_green_dot(self, history):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        txn_model.mark_synced(result['id'])
        history._load()
        row = next(i for i, t in enumerate(history._txns) if t['id'] == result['id'])
        dot = history._table.item(row, 5)
        assert dot.foreground().color().name() == '#4caf50'

    def test_row_0_renders_correctly_after_a_prior_empty_result(self, history):
        """Regression test: an empty _load() spans row 0 across all 6 columns
        to show the "No transactions found" placeholder. The next _load()
        that actually has rows must fully clear that span — otherwise row 0's
        Reference/Operator/Payment/Total cells silently render blank even
        though the underlying transaction data (and click-to-open) is fine."""
        # Empty result first — sets the 6-column span on row 0.
        history._date_from = history._date_to = __import__('datetime').date(2000, 1, 1)
        history._load()
        assert history._table.item(0, 0).text() == "No transactions found for this period"

        # Now a real result — must not leave row 0 visually blank.
        history._date_from = history._date_to = __import__('datetime').date.today()
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        history._load()

        assert history._table.rowSpan(0, 0) == 1
        assert history._table.columnSpan(0, 0) == 1
        assert history._table.item(0, 1).text() == result['reference']
        assert history._table.item(0, 2).text() == "alice"
        assert history._table.item(0, 3).text() == "CASH"


class TestRetryActions:
    def test_retry_all_requeues_backed_off_sales(self, history, monkeypatch):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        txn_model.mark_sync_failed(_queue_id(result['id']), "boom")
        assert not any(p['id'] == result['id'] for p in txn_model.get_pending_sync())

        monkeypatch.setattr(
            'views.transaction_history.QMessageBox.information',
            lambda *a, **k: None,
        )
        history._retry_all()
        assert any(p['id'] == result['id'] for p in txn_model.get_pending_sync())

    def test_retry_all_with_nothing_pending_shows_message_and_does_not_error(self, history, monkeypatch):
        calls = []
        monkeypatch.setattr(
            'views.transaction_history.QMessageBox.information',
            lambda *a, **k: calls.append(a),
        )
        history._retry_all()
        assert len(calls) == 1

    def test_detail_dialog_retry_button_hidden_when_synced(self, qtbot, test_db):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        txn_model.mark_synced(result['id'])
        txn = next(h for h in txn_model.get_history(
            __import__('datetime').date.today().isoformat(),
            __import__('datetime').date.today().isoformat(),
        ) if h['id'] == result['id'])
        dlg = TransactionDetailDialog(txn)
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        retry_buttons = [b for b in dlg.findChildren(QPushButton) if 'Retry' in b.text()]
        assert retry_buttons == []

    def test_detail_dialog_retry_button_calls_force_retry(self, qtbot, test_db, monkeypatch):
        result = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        txn_model.mark_sync_failed(_queue_id(result['id']), "boom")
        txn = next(h for h in txn_model.get_history(
            __import__('datetime').date.today().isoformat(),
            __import__('datetime').date.today().isoformat(),
        ) if h['id'] == result['id'])

        monkeypatch.setattr(
            'views.transaction_history.QMessageBox.information',
            lambda *a, **k: None,
        )
        changed = []
        dlg = TransactionDetailDialog(txn, on_change=lambda: changed.append(True))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        retry_btn = next(b for b in dlg.findChildren(QPushButton) if 'Retry' in b.text())
        retry_btn.click()

        assert changed == [True]
        assert any(p['id'] == result['id'] for p in txn_model.get_pending_sync())


def _reload_txn(txn_id):
    today = __import__('datetime').date.today().isoformat()
    return next(h for h in txn_model.get_history(today, today) if h['id'] == txn_id)


class TestRefundButtonVisibility:
    def test_cash_sale_with_remaining_qty_shows_refund_button(self, qtbot, test_db):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        dlg = TransactionDetailDialog(_reload_txn(sale['id']))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        assert any('Refund' in b.text() for b in dlg.findChildren(QPushButton))

    def test_eftpos_sale_shows_manual_note_not_button(self, qtbot, test_db):
        sale = txn_model.create("alice", None, _ITEMS, "EFTPOS", 3.5, 3.5, 0.0)
        dlg = TransactionDetailDialog(_reload_txn(sale['id']))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        assert not any('Refund' in b.text() for b in dlg.findChildren(QPushButton))

    def test_fully_refunded_sale_shows_no_refund_button(self, qtbot, test_db):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        txn_model.create_refund("alice", None, sale['reference'], _ITEMS)
        dlg = TransactionDetailDialog(_reload_txn(sale['id']))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        assert not any('Refund' in b.text() for b in dlg.findChildren(QPushButton))

    def test_refund_transaction_itself_shows_no_refund_section(self, qtbot, test_db):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        refund = txn_model.create_refund("alice", None, sale['reference'], _ITEMS)
        dlg = TransactionDetailDialog(_reload_txn(refund['id']))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QPushButton
        assert not any('Refund' in b.text() for b in dlg.findChildren(QPushButton))

    def test_refund_transaction_shows_refund_label_in_meta_row(self, qtbot, test_db):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        refund = txn_model.create_refund("alice", None, sale['reference'], _ITEMS)
        dlg = TransactionDetailDialog(_reload_txn(refund['id']))
        qtbot.addWidget(dlg)
        from PyQt6.QtWidgets import QLabel
        assert any(l.text() == "REFUND" for l in dlg.findChildren(QLabel))

    def test_clicking_refund_opens_refund_dialog_with_operator_and_shift(self, qtbot, test_db, monkeypatch):
        sale = txn_model.create("alice", None, _ITEMS, "CASH", 3.5, 3.5, 0.0)
        dlg = TransactionDetailDialog(_reload_txn(sale['id']), operator='alice', shift_id=7)
        qtbot.addWidget(dlg)

        mock_refund_dlg = MagicMock()
        mock_refund_dlg.exec.return_value = 0   # QDialog.DialogCode.Rejected
        with patch('views.refund_dialog.RefundDialog', return_value=mock_refund_dlg) as mock_cls:
            from PyQt6.QtWidgets import QPushButton
            refund_btn = next(b for b in dlg.findChildren(QPushButton) if 'Refund' in b.text())
            refund_btn.click()

        args, kwargs = mock_cls.call_args
        assert args[0]['id'] == sale['id']
        assert kwargs['operator'] == 'alice'
        assert kwargs['shift_id'] == 7
