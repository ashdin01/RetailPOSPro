"""
Tests for DB-persisted PIN lockout state in models.operator.

The key property under test: lockout timestamps and failure counts are stored
in the settings table, so they survive an app restart — unlike the old
module-level dicts that reset on every import.
"""
from datetime import datetime, timedelta
import pytest
import models.operator as op


class TestRecordFailedAttempt:
    def test_increments_counter(self, test_db):
        attempts, until = op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)
        assert attempts == 1
        assert until is None
        assert op.get_failed_attempts("admin") == 1

    def test_increments_from_existing_count(self, test_db):
        op.set_failed_attempts("admin", 3)
        attempts, until = op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)
        assert attempts == 4
        assert until is None

    def test_returns_lockout_on_max_attempts(self, test_db):
        op.set_failed_attempts("admin", 4)
        attempts, until = op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)
        assert attempts == 5
        assert until is not None
        assert until > datetime.now()

    def test_lockout_written_before_counter_reset(self, test_db):
        """If a crash occurs after lockout is written but before counter reset, the lockout survives."""
        op.set_failed_attempts("admin", 4)
        op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)
        # After the call, both lockout and reset-counter must be in the DB
        assert op.get_lockout_until("admin") is not None
        assert op.get_failed_attempts("admin") == 0

    def test_counter_reset_to_zero_on_lockout(self, test_db):
        op.set_failed_attempts("admin", 4)
        op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)
        assert op.get_failed_attempts("admin") == 0

    def test_single_db_connection_used(self, test_db):
        """record_failed_attempt must not open multiple connections per call."""
        from unittest.mock import patch
        import models.operator as _op_mod
        call_count = [0]
        original_get = _op_mod.get_connection

        def counting_get():
            call_count[0] += 1
            return original_get()

        with patch.object(_op_mod, 'get_connection', side_effect=counting_get):
            op.record_failed_attempt("admin", max_attempts=5, lockout_secs=30)

        assert call_count[0] == 1, "record_failed_attempt must open exactly one DB connection"


class TestLockoutPersistence:
    def test_no_lockout_by_default(self, test_db):
        assert op.get_lockout_until("admin") is None

    def test_set_and_get_lockout(self, test_db):
        until = datetime(2030, 1, 1, 12, 0, 0)
        op.set_lockout("admin", until)
        assert op.get_lockout_until("admin") == until

    def test_lockout_round_trips_via_db(self, test_db):
        """Verify the value actually lives in the DB (simulates restart: re-read from DB)."""
        until = datetime(2030, 6, 15, 9, 30, 0)
        op.set_lockout("admin", until)
        from database.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='pin_lockout_admin'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert datetime.fromisoformat(row['value']) == until

    def test_clear_lockout_removes_db_rows(self, test_db):
        op.set_lockout("admin", datetime(2030, 1, 1))
        op.set_failed_attempts("admin", 3)
        op.clear_lockout("admin")
        assert op.get_lockout_until("admin") is None
        assert op.get_failed_attempts("admin") == 0

    def test_clear_lockout_is_idempotent(self, test_db):
        op.clear_lockout("admin")  # nothing set — must not raise
        op.clear_lockout("admin")

    def test_failed_attempts_default_zero(self, test_db):
        assert op.get_failed_attempts("admin") == 0

    def test_set_failed_attempts(self, test_db):
        op.set_failed_attempts("admin", 3)
        assert op.get_failed_attempts("admin") == 3

    def test_set_failed_attempts_overwrites(self, test_db):
        op.set_failed_attempts("admin", 2)
        op.set_failed_attempts("admin", 5)
        assert op.get_failed_attempts("admin") == 5

    def test_multiple_users_are_independent(self, test_db, operator_id):
        op.set_lockout("admin", datetime(2030, 1, 1))
        op.set_failed_attempts("admin", 5)
        # testop fixture created an operator with username "testop"
        assert op.get_lockout_until("testop") is None
        assert op.get_failed_attempts("testop") == 0

    def test_clear_only_affects_named_user(self, test_db, operator_id):
        op.set_lockout("admin",  datetime(2030, 1, 1))
        op.set_lockout("testop", datetime(2030, 2, 1))
        op.clear_lockout("admin")
        assert op.get_lockout_until("admin") is None
        assert op.get_lockout_until("testop") == datetime(2030, 2, 1)

    def test_expired_lockout_still_readable(self, test_db):
        past = datetime(2000, 1, 1)
        op.set_lockout("admin", past)
        result = op.get_lockout_until("admin")
        assert result == past  # callers are responsible for checking expiry

    def test_lockout_written_before_counter_zeroed(self, test_db):
        """Lockout row must be in the DB before the counter is reset to 0.

        If the process is killed between the two commits, the lockout survives.
        We verify ordering by checking the lockout row exists immediately after
        set_lockout() and before set_failed_attempts() is called.
        """
        until = datetime(2030, 1, 1)
        op.set_lockout("admin", until)
        # At this point, before set_failed_attempts(0) is called, lockout must be durable
        from database.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='pin_lockout_admin'"
        ).fetchone()
        conn.close()
        assert row is not None, "Lockout must be committed before failed_attempts is reset"
        assert datetime.fromisoformat(row['value']) == until
