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
    ('eftpos_password',  ''),
    ('eftpos_merchant',   '00'),
    ('expected_float',   '300.00'),
    ('schema_version',   '1');

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
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
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
    last_error     TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_txn ON sync_queue(transaction_id);
"""


def setup(conn):
    conn.executescript(SCHEMA)
    conn.commit()

    # ── Migrations for existing databases ─────────────────────────────────────
    pc_cols = {row[1] for row in conn.execute("PRAGMA table_info(product_cache)").fetchall()}
    if 'group_name' not in pc_cols:
        conn.execute("ALTER TABLE product_cache ADD COLUMN group_name TEXT DEFAULT ''")

    sh_cols = {row[1] for row in conn.execute("PRAGMA table_info(shifts)").fetchall()}
    if 'float_open_variance' not in sh_cols:
        conn.execute("ALTER TABLE shifts ADD COLUMN float_open_variance REAL DEFAULT 0")

    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('expected_float', '300.00')"
    )
    conn.commit()
