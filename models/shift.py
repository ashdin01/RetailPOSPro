from database.connection import get_connection


def open_shift(operator_id: int, operator_name: str,
               float_open: float = 0, float_open_variance: float = 0) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO shifts "
            "(operator_id, operator_name, float_open, float_open_variance, status) "
            "VALUES (?, ?, ?, ?, 'OPEN')",
            (operator_id, operator_name, float_open, float_open_variance)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_open_shift(operator_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM shifts WHERE operator_id=? AND status='OPEN' "
            "ORDER BY opened_at DESC LIMIT 1",
            (operator_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def close_shift(shift_id: int, float_close: float = 0) -> dict:
    conn = get_connection()
    try:
        totals = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN payment_method='CASH'    THEN total ELSE 0 END), 0) cash_sales,
                COALESCE(SUM(CASE WHEN payment_method='EFTPOS'  THEN total ELSE 0 END), 0) eftpos_sales,
                COALESCE(SUM(CASE WHEN payment_method='ACCOUNT' THEN total ELSE 0 END), 0) account_sales,
                COALESCE(SUM(total), 0) total_sales,
                COUNT(*) transaction_count
            FROM transactions
            WHERE shift_id=? AND status='COMPLETED'
        """, (shift_id,)).fetchone()

        cur = conn.execute("""
            UPDATE shifts SET
                status='CLOSED', closed_at=CURRENT_TIMESTAMP,
                float_close=?,
                cash_sales=?, eftpos_sales=?, account_sales=?,
                total_sales=?, transaction_count=?
            WHERE id=? AND status='OPEN'
        """, (
            float_close,
            totals['cash_sales'], totals['eftpos_sales'], totals['account_sales'],
            totals['total_sales'], totals['transaction_count'],
            shift_id
        ))

        if cur.rowcount == 0:
            raise ValueError(f"Shift {shift_id} not found or already closed")

        conn.commit()
        return dict(totals)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
