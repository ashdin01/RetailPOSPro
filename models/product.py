"""
Product lookup: local cache first, then BackOfficePro API on miss.
"""
import logging
from database.connection import get_connection
import api.backoffice_client as bop


def get_by_barcode(barcode: str) -> dict | None:
    """Return product dict or None. Hits cache first, then live API."""
    barcode = barcode.strip()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM product_cache WHERE barcode = ? AND active = 1",
            (barcode,)
        ).fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()

    # Cache miss — try the live API
    product = bop.get_product(barcode)
    if product:
        _cache_product(product)
    return product


def get_by_plu(plu: str) -> dict | None:
    """Return product dict for a PLU code, or None."""
    plu = plu.strip()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM product_cache WHERE plu = ? AND active = 1",
            (plu,)
        ).fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()

    product = bop.get_product_by_plu(plu)
    if product:
        _cache_product(product)
    return product


def search(term: str) -> list:
    """Search local cache by description, barcode, or PLU."""
    words = [w.strip() for w in term.strip().split() if w.strip()]
    if not words:
        return []
    conn = get_connection()
    try:
        clauses = []
        params = []
        for word in words:
            like = f"%{word}%"
            clauses.append(
                "(description LIKE ? OR barcode LIKE ? OR plu LIKE ? OR brand LIKE ?)"
            )
            params.extend([like, like, like, like])
        where = " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT * FROM product_cache WHERE {where} AND active=1 "
            f"ORDER BY description LIMIT 50",
            params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _cache_product(product: dict):
    """Write a single API-fetched product into the local cache."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO product_cache
                (barcode, plu, description, brand, dept_name, unit, sell_price, tax_rate, active)
            VALUES
                (:barcode, :plu, :description, :brand, :dept_name, :unit, :sell_price, :tax_rate, 1)
            ON CONFLICT(barcode) DO UPDATE SET
                plu=excluded.plu, description=excluded.description,
                brand=excluded.brand, dept_name=excluded.dept_name,
                unit=excluded.unit, sell_price=excluded.sell_price,
                tax_rate=excluded.tax_rate, active=1,
                cached_at=CURRENT_TIMESTAMP
        """, {
            'barcode':     product.get('barcode', ''),
            'plu':         product.get('plu') or '',
            'description': product.get('description', ''),
            'brand':       product.get('brand') or '',
            'dept_name':   product.get('dept_name') or '',
            'unit':        product.get('unit') or 'EA',
            'sell_price':  float(product.get('sell_price', 0)),
            'tax_rate':    float(product.get('tax_rate', 10.0)),
        })
        conn.commit()
    except Exception as e:
        logging.warning(f"[product cache] write failed: {e}")
    finally:
        conn.close()
