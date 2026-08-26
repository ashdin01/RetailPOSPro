from datetime import date
from database.connection import get_connection


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
            SELECT t.*, sq.id AS queue_id
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
    """Return transactions queued for BackOfficePro sync."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT t.*, sq.id as queue_id
            FROM sync_queue sq
            JOIN transactions t ON t.id = sq.transaction_id
            WHERE sq.attempts < 5
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


def mark_synced(transaction_id: int):
    conn = get_connection()
    try:
        conn.execute("UPDATE transactions SET synced=1 WHERE id=?", (transaction_id,))
        conn.execute("DELETE FROM sync_queue WHERE transaction_id=?", (transaction_id,))
        conn.commit()
    finally:
        conn.close()


def mark_sync_failed(queue_id: int, error: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sync_queue SET attempts=attempts+1, last_error=? WHERE id=?",
            (error[:500], queue_id)
        )
        conn.commit()
    finally:
        conn.close()
