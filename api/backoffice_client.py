"""
HTTP client for the BackOfficePro API (api_server.py on port 5050).
All methods return None / empty list on failure — callers fall back to local cache.
"""
import logging
import os
import requests
from database.connection import get_connection

_IMAGES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'images'
)

_TIMEOUT = 3  # seconds


def _api_url() -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='backoffice_url'"
        ).fetchone()
        return (row['value'] or 'http://localhost:5050').rstrip('/')
    finally:
        conn.close()


def check_health() -> bool:
    try:
        r = requests.get(f"{_api_url()}/api/v1/health", timeout=_TIMEOUT)
        return r.status_code == 200 and r.json().get('status') == 'ok'
    except Exception:
        return False


def get_product(barcode: str) -> dict | None:
    """Fetch a single product by barcode or PLU. Returns None if not found or offline."""
    try:
        r = requests.get(
            f"{_api_url()}/api/v1/products/{barcode}",
            timeout=_TIMEOUT
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning(f"[BOP API] get_product({barcode}): {e}")
    return None


def get_product_by_plu(plu: str) -> dict | None:
    """Fetch a product by PLU number."""
    try:
        r = requests.get(
            f"{_api_url()}/api/v1/products/plu/{plu}",
            timeout=_TIMEOUT
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning(f"[BOP API] get_product_by_plu({plu}): {e}")
    return None


def list_products(search: str = '', limit: int = 500, offset: int = 0) -> list:
    """Fetch product list for cache sync. Returns [] on failure."""
    try:
        params: dict = {'limit': limit, 'offset': offset}
        if search:
            params['search'] = search
        r = requests.get(
            f"{_api_url()}/api/v1/products",
            params=params,
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning(f"[BOP API] list_products: {e}")
    return []


def post_sale(sale_data: dict) -> dict | None:
    """
    Post a completed sale to BackOfficePro.
    BackOfficePro will reduce stock and write to sales_daily.
    Returns the response dict on success, None on failure.
    """
    try:
        r = requests.post(
            f"{_api_url()}/api/v1/pos/sale",
            json=sale_data,
            timeout=_TIMEOUT
        )
        if r.status_code == 200:
            return r.json()
        logging.warning(f"[BOP API] post_sale HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logging.warning(f"[BOP API] post_sale: {e}")
    return None


def get_store_info() -> dict | None:
    """Fetch store settings from BackOfficePro (name, address, phone, gst_rate)."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/store", timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning(f"[BOP API] get_store_info: {e}")
    return None


def get_image_path(barcode: str) -> str | None:
    """Return local cached image path for a barcode, or None if not cached."""
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        p = os.path.join(_IMAGES_DIR, f"{barcode}.{ext}")
        if os.path.exists(p):
            return p
    return None


def sync_images(barcodes: list) -> int:
    """
    Download images from BackOfficePro for any barcode that doesn't have a
    local cached copy. Safe to call repeatedly — skips already-cached files.
    Returns count of newly downloaded images.
    """
    os.makedirs(_IMAGES_DIR, exist_ok=True)
    base = _api_url()
    downloaded = 0
    for barcode in barcodes:
        if get_image_path(barcode):
            continue  # already cached
        try:
            r = requests.get(
                f"{base}/api/v1/products/{barcode}/image",
                timeout=5, stream=True
            )
            if r.status_code == 200:
                ct = r.headers.get('Content-Type', 'image/jpeg')
                ext = 'jpg' if 'jpeg' in ct else ct.split('/')[-1]
                dest = os.path.join(_IMAGES_DIR, f"{barcode}.{ext}")
                with open(dest, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                downloaded += 1
        except Exception as e:
            logging.debug(f"[BOP API] sync_images({barcode}): {e}")
    if downloaded:
        logging.info(f"[BOP API] Downloaded {downloaded} product image(s)")
    return downloaded


def fetch_bundles() -> list | None:
    """
    Fetch active bundles from BackOfficePro.
    Returns list of bundles on success, None if offline/error (distinct from [] = no bundles).
    Each bundle: {id, name, required_qty, price, eligible: [{barcode, unit_qty}]}
    """
    try:
        r = requests.get(f"{_api_url()}/api/v1/bundles", timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        logging.warning(f"[BOP API] fetch_bundles HTTP {r.status_code}")
    except Exception as e:
        logging.warning(f"[BOP API] fetch_bundles: {e}")
    return None


def sync_product_cache() -> int:
    """
    Pull all active products from BackOfficePro and refresh the local cache.
    Returns the number of products written. Returns 0 if API is unreachable.
    """
    products = list_products(limit=5000)
    if not products:
        return 0

    conn = get_connection()
    try:
        conn.execute("DELETE FROM product_cache")
        conn.executemany("""
            INSERT INTO product_cache
                (barcode, plu, description, brand, dept_name, group_name, unit, sell_price, tax_rate, active)
            VALUES
                (:barcode, :plu, :description, :brand, :dept_name,
                 COALESCE(:group_name, ''), :unit, :sell_price, :tax_rate, 1)
        """, products)
        conn.commit()
        logging.info(f"[BOP API] Product cache synced: {len(products)} products")
        return len(products)
    except Exception as e:
        logging.error(f"[BOP API] sync_product_cache write failed: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()
