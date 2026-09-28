"""
Tests for database/schema.py version-gated migrations.

A "v1 database" is one created before migrations were version-tracked —
it has schema_version=1 and is missing the columns/settings added in v2, v3,
v4 and v5. These tests verify that setup() detects the version and applies
only the missing migrations, then updates schema_version to 5.
"""
import sqlite3
import pytest
import database.connection as _conn_mod
from database.schema import setup


def _make_v1_db(tmp_path) -> str:
    """Return path to a minimal pre-migration (v1) database."""
    db_path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT OR IGNORE INTO settings (key, value) VALUES ('schema_version', '1');

        -- product_cache WITHOUT group_name (added in v2)
        CREATE TABLE IF NOT EXISTS product_cache (
            barcode      TEXT PRIMARY KEY,
            plu          TEXT,
            description  TEXT NOT NULL,
            brand        TEXT DEFAULT '',
            dept_name    TEXT DEFAULT '',
            unit         TEXT DEFAULT 'EA',
            sell_price   REAL NOT NULL DEFAULT 0,
            tax_rate     REAL NOT NULL DEFAULT 10.0,
            active       INTEGER NOT NULL DEFAULT 1,
            cached_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- shifts WITHOUT float_open_variance (added in v3)
        CREATE TABLE IF NOT EXISTS shifts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id   INTEGER NOT NULL,
            operator_name TEXT NOT NULL,
            opened_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at     DATETIME,
            float_open    REAL DEFAULT 0,
            float_close   REAL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'OPEN'
        );

        CREATE TABLE IF NOT EXISTS operators (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL UNIQUE,
            full_name  TEXT,
            pin        TEXT,
            role       TEXT NOT NULL DEFAULT 'CASHIER',
            active     INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def v1_db(tmp_path, monkeypatch):
    db_path = _make_v1_db(tmp_path)
    monkeypatch.setattr(_conn_mod, "DATABASE_PATH", db_path)
    yield db_path


class TestNewDatabaseVersion:
    def test_new_db_has_schema_version_3(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert int(row['value']) == 6

    def test_new_db_has_group_name_column(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(product_cache)").fetchall()}
        conn.close()
        assert 'group_name' in cols

    def test_new_db_has_float_open_variance_column(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shifts)").fetchall()}
        conn.close()
        assert 'float_open_variance' in cols

    def test_new_db_has_expected_float_setting(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='expected_float'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_new_db_has_printer_settings(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'printer_%'"
        ).fetchall()}
        conn.close()
        assert rows == {
            'printer_enabled': '0', 'printer_protocol': 'manual',
            'printer_host': '', 'printer_port': '9100',
        }


class TestMigrateFromV1:
    def test_migrates_to_schema_version_3(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert int(row['value']) == 6

    def test_adds_group_name_column(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(product_cache)").fetchall()}
        conn.close()
        assert 'group_name' in cols

    def test_adds_float_open_variance_column(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shifts)").fetchall()}
        conn.close()
        assert 'float_open_variance' in cols

    def test_adds_expected_float_setting(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='expected_float'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['value'] == '300.00'

    def test_adds_printer_settings(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        rows = {r['key']: r['value'] for r in conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'printer_%'"
        ).fetchall()}
        conn.close()
        assert rows == {
            'printer_enabled': '0', 'printer_protocol': 'manual',
            'printer_host': '', 'printer_port': '9100',
        }

    def test_existing_data_survives_migration(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO operators (username, role) VALUES ('cashier1', 'CASHIER')"
        )
        conn.commit()
        conn.close()

        conn = get_connection()
        setup(conn)
        conn.close()

        conn = get_connection()
        row = conn.execute(
            "SELECT username FROM operators WHERE username='cashier1'"
        ).fetchone()
        conn.close()
        assert row is not None


def _make_v4_db(tmp_path) -> str:
    """Return path to a v4 database — has sync_queue, but without the
    next_retry_at/last_attempt_at columns added in v5 — with one sale stuck
    in the old permanent-give-up state (attempts >= 5, the old hard cutoff)."""
    db_path = str(tmp_path / "v4.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO settings (key, value) VALUES ('schema_version', '4');

        CREATE TABLE transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            reference       TEXT NOT NULL UNIQUE,
            shift_id        INTEGER,
            operator        TEXT NOT NULL,
            sale_date       TEXT NOT NULL,
            payment_method  TEXT NOT NULL,
            subtotal        REAL NOT NULL DEFAULT 0,
            gst_amount      REAL NOT NULL DEFAULT 0,
            total           REAL NOT NULL DEFAULT 0,
            tendered        REAL NOT NULL DEFAULT 0,
            change_given    REAL NOT NULL DEFAULT 0,
            item_count      INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'COMPLETED',
            synced          INTEGER NOT NULL DEFAULT 0,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- sync_queue WITHOUT next_retry_at / last_attempt_at (added in v5)
        CREATE TABLE sync_queue (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            queued_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            attempts       INTEGER NOT NULL DEFAULT 0,
            last_error     TEXT
        );

        INSERT INTO transactions
            (reference, operator, sale_date, payment_method, total, synced)
        VALUES ('POS-001-20260927-0001', 'admin', '2026-09-27', 'CASH', 455.05, 0);

        -- Simulates a sale abandoned under the old attempts<5 cutoff.
        INSERT INTO sync_queue (transaction_id, attempts, last_error)
        VALUES (1, 7, '401 Unauthorized');
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def v4_db(tmp_path, monkeypatch):
    db_path = _make_v4_db(tmp_path)
    monkeypatch.setattr(_conn_mod, "DATABASE_PATH", db_path)
    yield db_path


class TestMigrateFromV4:
    def test_adds_backoff_columns(self, v4_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_queue)").fetchall()}
        conn.close()
        assert {'next_retry_at', 'last_attempt_at'} <= cols

    def test_previously_abandoned_sale_becomes_retry_eligible(self, v4_db):
        """The core promise of this migration: a sale stuck under the old
        hard 5-attempt cutoff must sync again automatically after upgrade,
        with no manual DB intervention."""
        from database.connection import get_connection
        import models.transaction as txn

        conn = get_connection()
        setup(conn)
        conn.close()

        pending = txn.get_pending_sync()
        assert any(p['reference'] == 'POS-001-20260927-0001' for p in pending)

    def test_attempts_and_last_error_preserved(self, v4_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT attempts, last_error FROM sync_queue WHERE transaction_id=1"
        ).fetchone()
        conn.close()
        assert row['attempts'] == 7
        assert row['last_error'] == '401 Unauthorized'


def _make_v5_db(tmp_path) -> str:
    """Return path to a v5 database — has transactions, but without the
    transaction_type/refund_of_reference columns added in v6."""
    db_path = str(tmp_path / "v5.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO settings (key, value) VALUES ('schema_version', '5');

        -- transactions WITHOUT transaction_type / refund_of_reference (added in v6)
        CREATE TABLE transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            reference       TEXT NOT NULL UNIQUE,
            shift_id        INTEGER,
            operator        TEXT NOT NULL,
            sale_date       TEXT NOT NULL,
            payment_method  TEXT NOT NULL,
            subtotal        REAL NOT NULL DEFAULT 0,
            gst_amount      REAL NOT NULL DEFAULT 0,
            total           REAL NOT NULL DEFAULT 0,
            tendered        REAL NOT NULL DEFAULT 0,
            change_given    REAL NOT NULL DEFAULT 0,
            item_count      INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'COMPLETED',
            synced          INTEGER NOT NULL DEFAULT 0,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE sync_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id  INTEGER NOT NULL REFERENCES transactions(id),
            queued_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            attempts        INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            next_retry_at   DATETIME,
            last_attempt_at DATETIME
        );

        INSERT INTO transactions
            (reference, operator, sale_date, payment_method, total, synced)
        VALUES ('POS-001-20260927-0002', 'admin', '2026-09-27', 'CASH', 5.50, 1);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def v5_db(tmp_path, monkeypatch):
    db_path = _make_v5_db(tmp_path)
    monkeypatch.setattr(_conn_mod, "DATABASE_PATH", db_path)
    yield db_path


class TestMigrateFromV5:
    def test_adds_refund_columns(self, v5_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        conn.close()
        assert {'transaction_type', 'refund_of_reference'} <= cols

    def test_existing_rows_default_to_sale_type(self, v5_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT transaction_type, refund_of_reference FROM transactions "
            "WHERE reference='POS-001-20260927-0002'"
        ).fetchone()
        conn.close()
        assert row['transaction_type'] == 'SALE'
        assert row['refund_of_reference'] is None

    def test_new_transaction_helpers_work_after_migration(self, v5_db):
        """The migration alone isn't the point — models.transaction's new
        functions must actually work against a freshly-migrated DB."""
        from database.connection import get_connection
        import models.transaction as txn

        conn = get_connection()
        setup(conn)
        conn.close()

        sale = txn.get_by_reference('POS-001-20260927-0002')
        assert sale is not None
        refund = txn.create_refund(
            "cashier", None, sale["reference"],
            [{"barcode": "X", "description": "Test", "qty": 1.0,
              "unit_price": 5.50, "tax_rate": 10.0, "line_total": 5.50}],
        )
        assert refund["transaction_type"] == "REFUND"
        assert refund["total"] == -5.50


class TestMigrationIdempotency:
    def test_setup_twice_does_not_fail(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)  # second call on an already-v3 DB — must not raise
        row = conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert int(row['value']) == 6

    def test_v1_migration_twice_does_not_fail(self, v1_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)
        conn.close()
        conn = get_connection()
        setup(conn)  # second run on now-v3 DB
        row = conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert int(row['value']) == 6
