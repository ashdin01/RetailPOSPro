"""
Tests for database/schema.py version-gated migrations.

A "v1 database" is one created before migrations were version-tracked —
it has schema_version=1 and is missing the columns added in v2 and v3.
These tests verify that setup() detects the version and applies only the
missing migrations, then updates schema_version to 3.
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
        assert int(row['value']) == 3

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
        assert int(row['value']) == 3

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


class TestMigrationIdempotency:
    def test_setup_twice_does_not_fail(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        setup(conn)  # second call on an already-v3 DB — must not raise
        row = conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()
        conn.close()
        assert int(row['value']) == 3

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
        assert int(row['value']) == 3
