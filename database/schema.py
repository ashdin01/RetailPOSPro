SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('store_name',       'My Store'),
    ('backoffice_url',   'http://localhost:5050'),
    ('terminal_id',      'POS-001'),
    ('gst_rate',         '10.0'),
    ('currency',         'AUD'),
    ('receipt_header',   ''),
    ('receipt_footer',   'Thank you for shopping with us!'),
    ('cache_sync_mins',  '5'),
    ('scale_enabled',    '0'),
    ('scale_port',       ''),
    ('scale_baud',       '9600'),
    ('scale_protocol',   'auto'),
    ('eftpos_enabled',   '0'),
    ('eftpos_protocol',  'manual'),
    ('eftpos_host',      'localhost'),
    ('eftpos_port',      '4443'),
    ('eftpos_username',  ''),
    ('eftpos_password',      ''),
    ('eftpos_merchant',      '00'),
    ('expected_float',       '300.00'),
    ('backoffice_api_key',   ''),
    ('backoffice_ssl_cert',  ''),
    ('customer_display_ads_dir',    ''),
    ('customer_display_idle_secs',  '20'),
    ('printer_enabled',   '0'),
    ('printer_protocol',  'manual'),
    ('printer_host',      ''),
    ('printer_port',      '9100'),
    ('schema_version',       '6');

CREATE TABLE IF NOT EXISTS operators (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE,
    full_name  TEXT,
    pin        TEXT,
    role       TEXT NOT NULL DEFAULT 'CASHIER',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO operators (username, full_name, role)
    VALUES ('admin', 'Administrator', 'ADMIN');

CREATE TABLE IF NOT EXISTS shifts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id       INTEGER NOT NULL REFERENCES operators(id),
    operator_name     TEXT NOT NULL,
    opened_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at         DATETIME,
    float_open          REAL DEFAULT 0,
    float_open_variance REAL DEFAULT 0,
    float_close         REAL DEFAULT 0,
    cash_sales        REAL DEFAULT 0,
    eftpos_sales      REAL DEFAULT 0,
    account_sales     REAL DEFAULT 0,
    total_sales       REAL DEFAULT 0,
    transaction_count INTEGER DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reference       TEXT NOT NULL UNIQUE,
    shift_id        INTEGER REFERENCES shifts(id),
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
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    transaction_type   TEXT NOT NULL DEFAULT 'SALE',
    refund_of_reference TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_date   ON transactions(sale_date);
CREATE INDEX IF NOT EXISTS idx_transactions_synced ON transactions(synced);
CREATE INDEX IF NOT EXISTS idx_transactions_shift  ON transactions(shift_id);

CREATE TABLE IF NOT EXISTS transaction_lines (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    barcode        TEXT NOT NULL,
    description    TEXT NOT NULL,
    qty            REAL NOT NULL,
    unit_price     REAL NOT NULL,
    tax_rate       REAL NOT NULL DEFAULT 10.0,
    line_total     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lines_transaction ON transaction_lines(transaction_id);
CREATE INDEX IF NOT EXISTS idx_lines_barcode     ON transaction_lines(barcode);

-- Local mirror of BackOfficePro products, refreshed periodically via API
CREATE TABLE IF NOT EXISTS product_cache (
    barcode      TEXT PRIMARY KEY,
    plu          TEXT,
    description  TEXT NOT NULL,
    brand        TEXT DEFAULT '',
    dept_name    TEXT DEFAULT '',
    group_name   TEXT DEFAULT '',
    unit         TEXT DEFAULT 'EA',
    sell_price   REAL NOT NULL DEFAULT 0,
    tax_rate     REAL NOT NULL DEFAULT 10.0,
    active       INTEGER NOT NULL DEFAULT 1,
    cached_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Sales queued for sync to BackOfficePro when the API is reachable
CREATE TABLE IF NOT EXISTS sync_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    queued_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    next_retry_at   DATETIME,
    last_attempt_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_txn ON sync_queue(transaction_id);
"""


def _run_migrations(conn):
    """Apply any pending schema migrations in version order."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key='schema_version'"
    ).fetchone()
    version = int(row['value']) if row and row['value'] else 1

    if version < 2:
        pc_cols = {r[1] for r in conn.execute("PRAGMA table_info(product_cache)").fetchall()}
        if 'group_name' not in pc_cols:
            conn.execute("ALTER TABLE product_cache ADD COLUMN group_name TEXT DEFAULT ''")
        conn.execute("UPDATE settings SET value='2' WHERE key='schema_version'")
        conn.commit()
        version = 2

    if version < 3:
        sh_cols = {r[1] for r in conn.execute("PRAGMA table_info(shifts)").fetchall()}
        if 'float_open_variance' not in sh_cols:
            conn.execute("ALTER TABLE shifts ADD COLUMN float_open_variance REAL DEFAULT 0")
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('expected_float', '300.00')"
        )
        conn.execute("UPDATE settings SET value='3' WHERE key='schema_version'")
        conn.commit()
        version = 3

    if version < 4:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('printer_enabled', '0')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('printer_protocol', 'manual')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('printer_host', '')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('printer_port', '9100')")
        conn.execute("UPDATE settings SET value='4' WHERE key='schema_version'")
        conn.commit()
        version = 4

    if version < 5:
        sq_cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_queue)").fetchall()}
        if 'next_retry_at' not in sq_cols:
            conn.execute("ALTER TABLE sync_queue ADD COLUMN next_retry_at DATETIME")
        if 'last_attempt_at' not in sq_cols:
            conn.execute("ALTER TABLE sync_queue ADD COLUMN last_attempt_at DATETIME")
        # Previously-abandoned rows (attempts >= 5 under the old hard cutoff) become
        # retry-eligible again immediately — nothing should stay stuck forever.
        conn.execute("UPDATE sync_queue SET next_retry_at = NULL")
        conn.execute("UPDATE settings SET value='5' WHERE key='schema_version'")
        conn.commit()
        version = 5

    if version < 6:
        t_cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if 'transaction_type' not in t_cols:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN transaction_type TEXT NOT NULL DEFAULT 'SALE'"
            )
        if 'refund_of_reference' not in t_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN refund_of_reference TEXT")
        conn.execute("UPDATE settings SET value='6' WHERE key='schema_version'")
        conn.commit()


def setup(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    _run_migrations(conn)
