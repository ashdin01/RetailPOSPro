import pytest
import models.shift as shift
from database.connection import get_connection


class TestOpenShift:
    def test_returns_integer_id(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        assert isinstance(sid, int) and sid > 0

    def test_status_is_open(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        conn = get_connection()
        row = conn.execute("SELECT status FROM shifts WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["status"] == "OPEN"

    def test_stores_float_and_variance(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op", float_open=350.0, float_open_variance=50.0)
        conn = get_connection()
        row = conn.execute("SELECT float_open, float_open_variance FROM shifts WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["float_open"] == 350.0
        assert row["float_open_variance"] == 50.0

    def test_default_float_zero(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        conn = get_connection()
        row = conn.execute("SELECT float_open FROM shifts WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["float_open"] == 0.0


class TestGetOpenShift:
    def test_returns_open_shift(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        result = shift.get_open_shift(operator_id)
        assert result is not None
        assert result["id"] == sid

    def test_no_shift_returns_none(self, test_db, operator_id):
        assert shift.get_open_shift(operator_id) is None

    def test_closed_shift_not_returned(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        shift.close_shift(sid)
        assert shift.get_open_shift(operator_id) is None

    def test_returns_most_recent(self, test_db, operator_id):
        # Close first shift, open second — get_open_shift returns the second
        sid1 = shift.open_shift(operator_id, "Test Op")
        shift.close_shift(sid1)
        sid2 = shift.open_shift(operator_id, "Test Op")
        result = shift.get_open_shift(operator_id)
        assert result["id"] == sid2


class TestCloseShift:
    def test_status_becomes_closed(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        shift.close_shift(sid)
        conn = get_connection()
        row = conn.execute("SELECT status FROM shifts WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["status"] == "CLOSED"

    def test_stores_float_close(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        shift.close_shift(sid, float_close=295.0)
        conn = get_connection()
        row = conn.execute("SELECT float_close FROM shifts WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["float_close"] == 295.0

    def test_returns_totals_dict(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        result = shift.close_shift(sid)
        assert "total_sales" in result
        assert "transaction_count" in result
        assert "cash_sales" in result
        assert "eftpos_sales" in result

    def test_close_nonexistent_shift_raises(self, test_db):
        with pytest.raises(ValueError, match="not found or already closed"):
            shift.close_shift(9999)

    def test_close_already_closed_shift_raises(self, test_db, operator_id):
        sid = shift.open_shift(operator_id, "Test Op")
        shift.close_shift(sid)
        with pytest.raises(ValueError, match="not found or already closed"):
            shift.close_shift(sid)  # second close must fail

    def test_totals_reflect_transactions(self, test_db, operator_id):
        import models.transaction as txn
        sid = shift.open_shift(operator_id, "Test Op")
        items = [{"barcode": "123", "description": "Item", "qty": 1.0,
                  "unit_price": 10.0, "tax_rate": 10.0, "line_total": 10.0}]
        txn.create("testop", sid, items, "CASH", 10.0, 10.0, 0.0)
        result = shift.close_shift(sid)
        assert result["total_sales"] == 10.0
        assert result["transaction_count"] == 1
        assert result["cash_sales"] == 10.0
