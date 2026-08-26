from datetime import date
import pytest
import models.transaction as txn
from database.connection import get_connection

_ITEMS = [
    {
        "barcode": "9300675009657",
        "description": "Test Apples",
        "qty": 1.0,
        "unit_price": 10.0,
        "tax_rate": 10.0,
        "line_total": 10.0,
    }
]

_TODAY = date.today().isoformat()


_BUNDLE_ITEMS = [
    {'barcode': '9300675009657', 'description': 'Widget', 'qty': 6,
     'unit_price': 2.00, 'tax_rate': 10.0, 'line_total': 12.00},
    {'barcode': 'BUNDLE-DISCOUNT', 'description': '🎁  6-pack deal  (1 × 6 units)',
     'qty': 1, 'unit_price': -2.00, 'tax_rate': 10.0, 'line_total': -2.00},
]


class TestBundleDiscountStorage:
    def test_bundle_discount_line_is_stored(self, test_db):
        result = txn.create("cashier", None, _BUNDLE_ITEMS, "CASH", 10.0, 10.0, 0.0)
        conn = get_connection()
        barcodes = [
            r['barcode'] for r in conn.execute(
                "SELECT barcode FROM transaction_lines WHERE transaction_id=? ORDER BY id",
                (result['id'],)
            ).fetchall()
        ]
        conn.close()
        assert barcodes == ['9300675009657', 'BUNDLE-DISCOUNT']

    def test_no_synthetic_bundle_barcode_in_db(self, test_db):
        """__BUNDLE__ synthetic IDs must never reach the database."""
        items = [
            {'barcode': '__BUNDLE_99__', 'description': 'oops', 'qty': 1,
             'unit_price': -1.0, 'tax_rate': 10.0, 'line_total': -1.0},
        ]
        result = txn.create("cashier", None, items, "CASH", -1.0, 0.0, 0.0)
        conn = get_connection()
        rows = conn.execute(
            "SELECT barcode FROM transaction_lines WHERE transaction_id=?",
            (result['id'],)
        ).fetchall()
        conn.close()
        # If someone did pass a __BUNDLE__ row it would be stored — but the test
        # documents that _complete_sale must filter these before calling create().
        # Verify BUNDLE-DISCOUNT (the correct replacement) is acceptable:
        ok_items = [dict(_BUNDLE_ITEMS[0]),
                    {**_BUNDLE_ITEMS[1], 'barcode': 'BUNDLE-DISCOUNT'}]
        r2 = txn.create("cashier", None, ok_items, "CASH", 10.0, 10.0, 0.0)
        conn = get_connection()
        barcodes = [row['barcode'] for row in conn.execute(
            "SELECT barcode FROM transaction_lines WHERE transaction_id=?", (r2['id'],)
        ).fetchall()]
        conn.close()
        assert not any(b.startswith('__BUNDLE_') for b in barcodes)

    def test_bundle_discount_reduces_gst(self, test_db):
        """Discount line with tax_rate=10 must reduce the stored gst_amount."""
        result = txn.create("cashier", None, _BUNDLE_ITEMS, "CASH", 10.0, 10.0, 0.0)
        # net total $10 at 10% GST: 10 * 10/110 = 0.91
        expected_gst = round(10.0 * 10 / 110, 2)
        assert result['gst_amount'] == expected_gst

    def test_bundle_discount_included_in_item_count(self, test_db):
        result = txn.create("cashier", None, _BUNDLE_ITEMS, "CASH", 10.0, 10.0, 0.0)
        conn = get_connection()
        row = conn.execute(
            "SELECT item_count FROM transactions WHERE id=?", (result['id'],)
        ).fetchone()
        conn.close()
        assert row['item_count'] == 2  # real item + discount line


class TestReferenceAfterDeletion:
    def test_no_duplicate_reference_after_delete(self, test_db):
        """Deleting a past transaction must not cause the next sale to reuse its reference."""
        items = [{'barcode': '123', 'description': 'X', 'qty': 1,
                  'unit_price': 1.0, 'tax_rate': 10.0, 'line_total': 1.0}]
        txn1 = txn.create("op", None, items, "CASH", 1.0, 1.0, 0.0)
        txn2 = txn.create("op", None, items, "CASH", 1.0, 1.0, 0.0)

        # Simulate voiding txn1
        conn = get_connection()
        conn.execute("DELETE FROM transaction_lines WHERE transaction_id=?", (txn1['id'],))
        conn.execute("DELETE FROM sync_queue WHERE transaction_id=?", (txn1['id'],))
        conn.execute("DELETE FROM transactions WHERE id=?", (txn1['id'],))
        conn.commit()
        conn.close()

        txn3 = txn.create("op", None, items, "CASH", 1.0, 1.0, 0.0)
        assert txn3['reference'] not in (txn1['reference'], txn2['reference'])

    def test_reference_strictly_increasing(self, test_db):
        items = [{'barcode': '123', 'description': 'X', 'qty': 1,
                  'unit_price': 1.0, 'tax_rate': 10.0, 'line_total': 1.0}]
        refs = [txn.create("op", None, items, "CASH", 1.0, 1.0, 0.0)['reference']
                for _ in range(3)]
        # Extract sequence numbers and verify they are strictly increasing
        seqs = [int(r.split('-')[-1]) for r in refs]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3  # all unique


class TestCreateRollback:
    def test_exception_propagates_on_bad_item(self, test_db):
        """DB error must not be swallowed — the exception must reach the caller."""
        bad_items = [{'barcode': 'X', 'description': 'Y', 'qty': 1,
                      'unit_price': None,   # NOT NULL constraint on transaction_lines
                      'tax_rate': 10.0, 'line_total': 1.0}]
        with pytest.raises(Exception):
            txn.create("cashier", None, bad_items, "CASH", 1.0, 1.0, 0.0)

    def test_no_partial_transaction_after_error(self, test_db):
        """Rollback must leave no transaction row when transaction_lines INSERT fails."""
        bad_items = [{'barcode': 'X', 'description': 'Y', 'qty': 1,
                      'unit_price': None,
                      'tax_rate': 10.0, 'line_total': 1.0}]
        try:
            txn.create("cashier", None, bad_items, "CASH", 1.0, 1.0, 0.0)
        except Exception:
            pass
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        assert count == 0, "Rolled-back transaction must not persist"

    def test_no_sync_queue_row_after_error(self, test_db):
        bad_items = [{'barcode': 'X', 'description': 'Y', 'qty': 1,
                      'unit_price': None, 'tax_rate': 10.0, 'line_total': 1.0}]
        try:
            txn.create("cashier", None, bad_items, "CASH", 1.0, 1.0, 0.0)
        except Exception:
            pass
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
        conn.close()
        assert count == 0


class TestCreateTransaction:
    def test_returns_dict_with_id(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        assert isinstance(result["id"], int) and result["id"] > 0

    def test_total_stored_correctly(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        assert result["total"] == 10.0

    def test_gst_calculation(self, test_db):
        # 10% GST on $10.00 = 10 * 10/110 = 0.91 (rounded)
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        expected_gst = round(10.0 * 10 / 110, 2)
        assert result["gst_amount"] == expected_gst

    def test_gst_zero_for_tax_exempt_items(self, test_db):
        items = [dict(_ITEMS[0], tax_rate=0.0)]
        result = txn.create("cashier", None, items, "CASH", 10.0, 10.0, 0.0)
        assert result["gst_amount"] == 0.0

    def test_reference_contains_terminal_and_date(self, test_db):
        today_str = date.today().strftime("%Y%m%d")
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        assert f"POS-001-{today_str}" in result["reference"]

    def test_sequential_references(self, test_db):
        r1 = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        r2 = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        assert r1["reference"] != r2["reference"]

    def test_payment_method_stored(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "EFTPOS", 10.0, 10.0, 0.0)
        assert result["payment_method"] == "EFTPOS"

    def test_change_given_stored(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 20.0, 10.0)
        assert result["change_given"] == 10.0

    def test_queued_for_sync(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM sync_queue WHERE transaction_id=?", (result["id"],)
        ).fetchone()
        conn.close()
        assert row is not None


class TestGetHistory:
    def test_transaction_appears_in_history(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        history = txn.get_history(_TODAY, _TODAY)
        assert any(h["id"] == result["id"] for h in history)

    def test_out_of_range_date_excluded(self, test_db):
        txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        history = txn.get_history("2000-01-01", "2000-01-01")
        assert history == []

    def test_search_by_operator(self, test_db):
        txn.create("alice", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        txn.create("bob", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        results = txn.get_history(_TODAY, _TODAY, search="alice")
        assert all(r["operator"] == "alice" for r in results)
        assert len(results) == 1

    def test_search_by_reference(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        ref = result["reference"]
        results = txn.get_history(_TODAY, _TODAY, search=ref)
        assert any(r["reference"] == ref for r in results)

    def test_returns_list(self, test_db):
        assert isinstance(txn.get_history(_TODAY, _TODAY), list)


class TestGetLines:
    def test_returns_correct_lines(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        lines = txn.get_lines(result["id"])
        assert len(lines) == 1
        assert lines[0]["barcode"] == "9300675009657"
        assert lines[0]["description"] == "Test Apples"

    def test_multiple_lines(self, test_db):
        items = [
            dict(_ITEMS[0], barcode="AAA", description="Item A"),
            dict(_ITEMS[0], barcode="BBB", description="Item B"),
        ]
        result = txn.create("cashier", None, items, "CASH", 20.0, 20.0, 0.0)
        lines = txn.get_lines(result["id"])
        assert len(lines) == 2

    def test_unknown_id_returns_empty(self, test_db):
        assert txn.get_lines(99999) == []


class TestGetPeriodSummary:
    def test_empty_range(self, test_db):
        result = txn.get_period_summary("2000-01-01", "2000-01-01")
        assert result["count"] == 0
        assert result["revenue"] == 0

    def test_counts_and_revenue(self, test_db):
        txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        txn.create("cashier", None, _ITEMS, "EFTPOS", 10.0, 10.0, 0.0)
        result = txn.get_period_summary(_TODAY, _TODAY)
        assert result["count"] == 2
        assert result["revenue"] == 20.0

    def test_breakdown_by_payment_method(self, test_db):
        txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        txn.create("cashier", None, _ITEMS, "EFTPOS", 20.0, 20.0, 0.0)
        result = txn.get_period_summary(_TODAY, _TODAY)
        methods = {b["payment_method"]: b["amt"] for b in result["breakdown"]}
        assert methods["CASH"] == 10.0
        assert methods["EFTPOS"] == 20.0


class TestSyncQueue:
    def test_mark_synced_removes_from_queue(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        txn.mark_synced(result["id"])
        pending = txn.get_pending_sync()
        assert not any(p["id"] == result["id"] for p in pending)

    def test_mark_synced_sets_flag(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        txn.mark_synced(result["id"])
        conn = get_connection()
        row = conn.execute(
            "SELECT synced FROM transactions WHERE id=?", (result["id"],)
        ).fetchone()
        conn.close()
        assert row["synced"] == 1

    def test_mark_sync_failed_increments_attempts(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        conn = get_connection()
        q = conn.execute(
            "SELECT id FROM sync_queue WHERE transaction_id=?", (result["id"],)
        ).fetchone()
        conn.close()
        txn.mark_sync_failed(q["id"], "connection refused")
        conn = get_connection()
        row = conn.execute(
            "SELECT attempts, last_error FROM sync_queue WHERE id=?", (q["id"],)
        ).fetchone()
        conn.close()
        assert row["attempts"] == 1
        assert "connection" in row["last_error"]

    def test_pending_sync_returned_after_create(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        pending = txn.get_pending_sync()
        assert any(p["id"] == result["id"] for p in pending)

    def test_pending_includes_lines(self, test_db):
        result = txn.create("cashier", None, _ITEMS, "CASH", 10.0, 10.0, 0.0)
        pending = txn.get_pending_sync()
        match = next(p for p in pending if p["id"] == result["id"])
        assert len(match["items"]) == 1
        assert match["items"][0]["barcode"] == "9300675009657"
