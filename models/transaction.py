from datetime import date
from database.connection import get_connection

# Sync retry backoff: doubles each attempt, capped so a dead server or bad API
# key is retried at least every half hour — never excluded from the queue.
_SYNC_BACKOFF_CAP_MINUTES = 30

# Attempt count past which the UI calls a transaction "stuck" rather than
# "pending" — purely a display threshold; get_pending_sync() keeps retrying
# past this forever.
SYNC_STUCK_ATTEMPTS = 5


def _generate_reference(conn, terminal_id: str) -> str:
    """Return the next sequential reference for today using the given connection.

    Uses MAX over the numeric suffix rather than COUNT so that deleting a
    past transaction never causes a sequence number to be reused.
    """
    today  = date.today().strftime('%Y%m%d')
    prefix = f"{terminal_id}-{today}-"
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(reference, ?) AS INTEGER)) "
        "FROM transactions WHERE reference LIKE ?",
        (len(prefix) + 1, f"{prefix}%")
    ).fetchone()
    last_seq = row[0] if row and row[0] is not None else 0
    return f"{prefix}{last_seq + 1:04d}"


def create(
    operator: str,
    shift_id: int | None,
    items: list,           # [{'barcode', 'description', 'qty', 'unit_price', 'tax_rate', 'line_total'}]
    payment_method: str,
    total: float,
    tendered: float,
    change_given: float,
) -> dict:
    """
    Save a completed transaction.
    Returns the saved transaction dict including its auto-generated reference.
    Raises on DB error; nothing is committed if any step fails.
    """
    def _lt(item):
        return item.get('line_total', round(item['qty'] * item['unit_price'], 2))

    gst = sum(
        _lt(item) * item.get('tax_rate', 10.0) / (100 + item.get('tax_rate', 10.0))
        for item in items
        if item.get('tax_rate', 0) > 0
    )
    subtotal  = total - gst
    sale_date = date.today().isoformat()

    conn = get_connection()
    try:
        # Read terminal_id and generate reference in the same connection so the
        # COUNT and INSERT are part of one implicit transaction — prevents duplicate
        # references if two threads call create() concurrently.
        row = conn.execute(
            "SELECT value FROM settings WHERE key='terminal_id'"
        ).fetchone()
        terminal_id = (row['value'] or 'POS-001') if row else 'POS-001'
        reference = _generate_reference(conn, terminal_id)

        cur = conn.execute("""
            INSERT INTO transactions
                (reference, shift_id, operator, sale_date, payment_method,
                 subtotal, gst_amount, total, tendered, change_given, item_count, status, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', 0)
        """, (
            reference, shift_id, operator, sale_date, payment_method,
            round(subtotal, 2), round(gst, 2), round(total, 2),
            round(tendered, 2), round(change_given, 2), len(items)
        ))
        txn_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO transaction_lines
                (transaction_id, barcode, description, qty, unit_price, tax_rate, line_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (txn_id, i['barcode'], i['description'],
             i['qty'], i['unit_price'], i.get('tax_rate', 10.0), _lt(i))
            for i in items
        ])

        # Queue for BackOfficePro sync
        conn.execute(
            "INSERT INTO sync_queue (transaction_id) VALUES (?)",
            (txn_id,)
        )

        conn.commit()
        return {
            'id': txn_id, 'reference': reference, 'sale_date': sale_date,
            'operator': operator, 'payment_method': payment_method,
            'subtotal': round(subtotal, 2), 'gst_amount': round(gst, 2),
            'total': round(total, 2), 'tendered': round(tendered, 2),
            'change_given': round(change_given, 2), 'items': items,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_refund(
    operator: str,
    shift_id: int | None,
    original_reference: str,
    lines: list,   # [{'barcode', 'description', 'qty', 'unit_price', 'tax_rate', 'line_total'}] — all POSITIVE
) -> dict:
    """
    Save a cash refund against a prior sale.

    lines are the items/quantities being refunded, in the same positive-number
    shape as a normal sale line (what the Refund dialog shows staff) — this
    function stores them, and the transaction's own totals, as NEGATIVE: the
    standard POS refund-line convention. That sign is what lets EOD, shift
    close, and the Reports period summary net a refund against its original
    sale automatically via their existing SUM(total) — no new status value,
    no extra filtering to keep in sync across three call sites.

    Refunds are always CASH (see hardware/eftpos.py — no card-refund
    transaction type exists to automate one) and never carry a tendered/
    change amount — the transaction's own (negative) total is the refunded
    amount.

    Returns the saved refund transaction dict. Raises on DB error; nothing is
    committed if any step fails.
    """
    def _lt(item):
        return item.get('line_total', round(item['qty'] * item['unit_price'], 2))

    gst = sum(
        _lt(item) * item.get('tax_rate', 10.0) / (100 + item.get('tax_rate', 10.0))
        for item in lines
        if item.get('tax_rate', 0) > 0
    )
    total     = sum(_lt(item) for item in lines)
    subtotal  = total - gst
    sale_date = date.today().isoformat()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='terminal_id'"
        ).fetchone()
        terminal_id = (row['value'] or 'POS-001') if row else 'POS-001'
        # Reuses _generate_reference's {prefix}-{YYYYMMDD}-{seq} shape verbatim —
        # an "RFD-<terminal_id>" prefix keeps refund references in their own
        # sequence, distinguishable from sale references at a glance and by
        # the scan-to-reopen regex in views/pos_screen.py.
        reference = _generate_reference(conn, f"RFD-{terminal_id}")

        cur = conn.execute("""
            INSERT INTO transactions
                (reference, shift_id, operator, sale_date, payment_method,
                 subtotal, gst_amount, total, tendered, change_given, item_count,
                 status, synced, transaction_type, refund_of_reference)
            VALUES (?, ?, ?, ?, 'CASH', ?, ?, ?, 0, 0, ?, 'COMPLETED', 0, 'REFUND', ?)
        """, (
            reference, shift_id, operator, sale_date,
            round(-subtotal, 2), round(-gst, 2), round(-total, 2), len(lines),
            original_reference,
        ))
        txn_id = cur.lastrowid

        conn.executemany("""
            INSERT INTO transaction_lines
                (transaction_id, barcode, description, qty, unit_price, tax_rate, line_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (txn_id, i['barcode'], i['description'],
             -i['qty'], i['unit_price'], i.get('tax_rate', 10.0), round(-_lt(i), 2))
            for i in lines
        ])

        # Queue for BackOfficePro sync — _flush_sync_queue() routes REFUND
        # rows to /api/v1/pos/refund instead of /api/v1/pos/sale.
        conn.execute(
            "INSERT INTO sync_queue (transaction_id) VALUES (?)",
            (txn_id,)
        )

        conn.commit()
        return {
            'id': txn_id, 'reference': reference, 'sale_date': sale_date,
            'operator': operator, 'payment_method': 'CASH',
            'transaction_type': 'REFUND', 'refund_of_reference': original_reference,
            'subtotal': round(-subtotal, 2), 'gst_amount': round(-gst, 2),
            'total': round(-total, 2), 'tendered': 0.0, 'change_given': 0.0,
            'items': [
                dict(i, qty=-i['qty'], line_total=round(-_lt(i), 2))
                for i in lines
            ],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_by_reference(reference: str) -> dict | None:
    """Return a single transaction (sale or refund) by its exact reference,
    or None — used by the scan-to-reopen lookup in views/pos_screen.py."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT t.*, sq.id AS queue_id, sq.attempts AS sync_attempts,
                   sq.last_error AS sync_last_error, sq.next_retry_at AS sync_next_retry_at
            FROM transactions t
            LEFT JOIN sync_queue sq ON sq.transaction_id = t.id
            WHERE t.reference = ?
        """, (reference,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_refunded_qty_by_barcode(original_reference: str) -> dict:
    """Return {barcode: total qty already refunded} for prior REFUND
    transactions against original_reference. Used to cap a new refund's line
    quantities in the UI before it's even sent — BackOfficePro independently
    re-validates against its own ledger regardless, so this is a UX
    convenience, not the safety check."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT tl.barcode, SUM(-tl.qty) AS refunded_qty
            FROM transactions t
            JOIN transaction_lines tl ON tl.transaction_id = t.id
            WHERE t.refund_of_reference = ? AND t.transaction_type = 'REFUND'
            GROUP BY tl.barcode
        """, (original_reference,)).fetchall()
        return {r['barcode']: r['refunded_qty'] for r in rows}
    finally:
        conn.close()


def get_history(
    date_from: str,
    date_to:   str,
    search:    str = '',
    limit:     int = 300,
    offset:    int = 0,
) -> list:
    """
    Return transactions within [date_from, date_to] (YYYY-MM-DD, inclusive).
    Optionally filter by reference or operator name.
    Most-recent first.
    """
    clauses = ["t.sale_date BETWEEN ? AND ?"]
    params: list = [date_from, date_to]
    if search.strip():
        clauses.append("(t.reference LIKE ? OR t.operator LIKE ?)")
        like = f"%{search.strip()}%"
        params += [like, like]
    where = " AND ".join(clauses)
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT t.*, sq.id AS queue_id, sq.attempts AS sync_attempts,
                   sq.last_error AS sync_last_error, sq.next_retry_at AS sync_next_retry_at
            FROM transactions t
            LEFT JOIN sync_queue sq ON sq.transaction_id = t.id
            WHERE {where}
            ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_lines(transaction_id: int) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM transaction_lines WHERE transaction_id=? ORDER BY id",
            (transaction_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_period_summary(date_from: str, date_to: str) -> dict:
    """Return count, total revenue, and payment-method breakdown for a date range."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS count, COALESCE(SUM(total), 0) AS revenue
            FROM transactions
            WHERE sale_date BETWEEN ? AND ?
        """, (date_from, date_to)).fetchone()
        breakdown = conn.execute("""
            SELECT payment_method, COUNT(*) AS n, COALESCE(SUM(total), 0) AS amt
            FROM transactions
            WHERE sale_date BETWEEN ? AND ?
            GROUP BY payment_method
        """, (date_from, date_to)).fetchall()
        return {
            'count':     row['count'],
            'revenue':   row['revenue'],
            'breakdown': [dict(r) for r in breakdown],
        }
    finally:
        conn.close()


def get_pending_sync() -> list:
    """Return transactions queued for BackOfficePro sync that are due to be retried.

    No transaction is ever permanently excluded here — a repeatedly-failing sale
    just backs off to a slower retry cadence (see mark_sync_failed), it never
    drops out of this query for good.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT t.*, sq.id as queue_id
            FROM sync_queue sq
            JOIN transactions t ON t.id = sq.transaction_id
            WHERE sq.next_retry_at IS NULL OR sq.next_retry_at <= CURRENT_TIMESTAMP
            ORDER BY sq.queued_at
            LIMIT 20
        """).fetchall()
        result = []
        for row in rows:
            txn = dict(row)
            lines = conn.execute(
                "SELECT * FROM transaction_lines WHERE transaction_id=?",
                (txn['id'],)
            ).fetchall()
            txn['items'] = [dict(l) for l in lines]
            result.append(txn)
        return result
    finally:
        conn.close()


def get_pending_sync_count() -> int:
    """Return the total number of sales sitting in the sync queue right now,
    including ones currently backed off (not yet due for retry). Used to drive
    the header's "N pending" indicator — that indicator should reflect true
    unsynced sales, not just ones due this instant.
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM sync_queue").fetchone()
        return row['n'] if row else 0
    finally:
        conn.close()


def mark_synced(transaction_id: int):
    conn = get_connection()
    try:
        conn.execute("UPDATE transactions SET synced=1 WHERE id=?", (transaction_id,))
        conn.execute("DELETE FROM sync_queue WHERE transaction_id=?", (transaction_id,))
        conn.commit()
    finally:
        conn.close()


def mark_sync_failed(queue_id: int, error: str):
    """Record a failed sync attempt and schedule the next retry with backoff.

    Backoff doubles per attempt (1, 2, 4, ... minutes) capped at
    _SYNC_BACKOFF_CAP_MINUTES — the row is never excluded from
    get_pending_sync(), only slowed down.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT attempts FROM sync_queue WHERE id=?", (queue_id,)
        ).fetchone()
        attempts = (row['attempts'] if row else 0) + 1
        backoff_minutes = min(2 ** attempts, _SYNC_BACKOFF_CAP_MINUTES)
        conn.execute(
            """UPDATE sync_queue
               SET attempts=?, last_error=?, last_attempt_at=CURRENT_TIMESTAMP,
                   next_retry_at=datetime('now', ? || ' minutes')
               WHERE id=?""",
            (attempts, error[:500], f'+{backoff_minutes}', queue_id)
        )
        conn.commit()
    finally:
        conn.close()


def force_retry(transaction_id: int) -> bool:
    """Make a transaction's queued sale immediately eligible for retry.

    Clears next_retry_at (skipping any remaining backoff) but keeps attempts/
    last_error so the failure history isn't lost. Returns False if the
    transaction has nothing queued (already synced, or unknown id).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE sync_queue SET next_retry_at=NULL WHERE transaction_id=?",
            (transaction_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def force_retry_all() -> int:
    """Make every queued sale immediately eligible for retry. Returns the count affected."""
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE sync_queue SET next_retry_at=NULL")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
