import pytest
import database.connection as _conn_mod
from database.schema import setup


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Patch DATABASE_PATH to a fresh temp DB and initialise the schema."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_conn_mod, "DATABASE_PATH", db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup(conn)
    conn.close()
    return db_path


@pytest.fixture
def db_conn(test_db):
    """Open connection to the test DB; closed after the test."""
    from database.connection import get_connection
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def operator_id(test_db):
    """Insert a non-admin test operator and return its id."""
    from database.connection import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO operators (username, full_name, role) VALUES (?, ?, ?)",
        ("testop", "Test Operator", "CASHIER"),
    )
    conn.commit()
    op_id = cur.lastrowid
    conn.close()
    return op_id
