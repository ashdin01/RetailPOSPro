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

# When running on the same machine as BackOfficePro, fall back to its image
# store so images show immediately without waiting for a sync download.
_BOP_IMAGES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'BackOfficePro', 'data', 'images'
)

_TIMEOUT = 3  # seconds

_cached_url:      str | None = None
_cached_api_key:  str | None = None
_cached_ssl_cert: str | None = None   # path to PEM cert, or '' = default verify


def _ssl_verify():
    """Return the SSL verify argument for requests: cert path, True, or False."""
    global _cached_ssl_cert
    if _cached_ssl_cert is None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='backoffice_ssl_cert'"
            ).fetchone()
            _cached_ssl_cert = (row['value'] if row else '') or ''
        finally:
            conn.close()
    path = _cached_ssl_cert.strip()
    if not path:
        return True
    import os
    expanded = os.path.expanduser(path)
    return expanded if os.path.exists(expanded) else True


def invalidate_ssl_cert_cache():
    global _cached_ssl_cert
    _cached_ssl_cert = None


def _api_url() -> str:
    global _cached_url
    if _cached_url is None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='backoffice_url'"
            ).fetchone()
            _cached_url = ((row['value'] if row else None) or 'http://localhost:5050').rstrip('/')
        finally:
            conn.close()
    return _cached_url


def invalidate_url_cache():
    """Clear the cached API URL so the next call re-reads it from settings."""
    global _cached_url
    _cached_url = None


def _api_key() -> str:
    global _cached_api_key
    if _cached_api_key is None:
        from utils.credentials import get_credential
        key = get_credential('backoffice_api_key')
        if key is None:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key='backoffice_api_key'"
                ).fetchone()
                key = (row['value'] if row else None) or ''
            finally:
                conn.close()
        _cached_api_key = key
    return _cached_api_key


def invalidate_api_key_cache():
    """Clear the cached API key so the next call re-reads it from keyring/settings."""
    global _cached_api_key
    _cached_api_key = None


def prime_api_key(key: str):
    """Inject a key directly into the cache — used during connection tests so the
    test always exercises exactly the key the user just typed, without relying on
    a keyring round-trip that may briefly fail."""
    global _cached_api_key
    _cached_api_key = key


def _api_headers() -> dict:
    """Return the request headers required by BackOfficePro."""
    return {'X-API-Key': _api_key()}


def check_health() -> bool:
    """Check reachability — exempt from API key auth."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/health",
                         timeout=_TIMEOUT, verify=_ssl_verify())
        return r.status_code == 200 and r.json().get('status') == 'ok'
    except Exception:
        return False


def check_api_key() -> bool:
    """Return True if the configured API key is accepted (HTTP 200 on an authenticated endpoint)."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/store",
                         headers=_api_headers(), timeout=_TIMEOUT,
                         verify=_ssl_verify())
        return r.status_code == 200
    except Exception:
        return False


def get_product(barcode: str) -> dict | None:
    """Fetch a single product by barcode or PLU. Returns None if not found or offline."""
    try:
        r = requests.get(
            f"{_api_url()}/api/v1/products/{barcode}",
            headers=_api_headers(),
            timeout=_TIMEOUT,
            verify=_ssl_verify(),
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning("[BOP API] get_product(%s): %s", barcode, e)
    return None


def get_product_by_plu(plu: str) -> dict | None:
    """Fetch a product by PLU number."""
    try:
        r = requests.get(
            f"{_api_url()}/api/v1/products/plu/{plu}",
            headers=_api_headers(),
            timeout=_TIMEOUT,
            verify=_ssl_verify(),
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning("[BOP API] get_product_by_plu(%s): %s", plu, e)
    return None


def list_products(search: str = '', limit: int = 500, offset: int = 0) -> list:
    """Fetch product list for cache sync. Returns [] on failure."""
    try:
        params: dict = {'limit': limit, 'offset': offset}
        if search:
            params['search'] = search
        r = requests.get(
            f"{_api_url()}/api/v1/products",
            headers=_api_headers(),
            params=params,
            timeout=10,
            verify=_ssl_verify(),
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning("[BOP API] list_products: %s", e)
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
            headers=_api_headers(),
            json=sale_data,
            timeout=_TIMEOUT,
            verify=_ssl_verify(),
        )
        if r.status_code == 200:
            return r.json()
        logging.warning("[BOP API] post_sale HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logging.warning("[BOP API] post_sale: %s", e)
    return None


def create_hold(terminal_id: str, operator: str, items: list, *,
                 subtotal: float, gst_amount: float, total: float, note: str = '') -> dict | None:
    """Suspend a sale on BackOfficePro. Returns {'id', 'reference'} on success, None on failure."""
    try:
        r = requests.post(
            f"{_api_url()}/api/v1/held-sales",
            headers=_api_headers(),
            json={'terminal_id': terminal_id, 'operator': operator, 'note': note,
                  'subtotal': subtotal, 'gst_amount': gst_amount, 'total': total, 'items': items},
            timeout=_TIMEOUT, verify=_ssl_verify(),
        )
        if r.status_code == 201:
            return r.json()
        logging.warning("[BOP API] create_hold HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logging.warning("[BOP API] create_hold: %s", e)
    return None


def list_open_holds() -> list | None:
    """Returns list of open holds, or None if offline (distinct from [] = none open)."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/held-sales",
                          headers=_api_headers(), timeout=_TIMEOUT, verify=_ssl_verify())
        if r.status_code == 200:
            return r.json()
        logging.warning("[BOP API] list_open_holds HTTP %s", r.status_code)
    except Exception as e:
        logging.warning("[BOP API] list_open_holds: %s", e)
    return None


def get_hold(reference: str) -> dict | None:
    """Fetch a held sale's full basket by reference. Returns None if not found or offline."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/held-sales/{reference}",
                          headers=_api_headers(), timeout=_TIMEOUT, verify=_ssl_verify())
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning("[BOP API] get_hold(%s): %s", reference, e)
    return None


def resume_hold(reference: str, terminal_id: str) -> tuple:
    """
    Atomically claim a held sale for this terminal.
    Returns (hold_dict, None) on success, (None, error_code) on failure —
    error_code is 'NOT_FOUND' / 'INVALID_STATUS' / 'OFFLINE' so the caller can
    show a specific message (e.g. 'already resumed elsewhere' vs 'not found').
    """
    try:
        r = requests.post(
            f"{_api_url()}/api/v1/held-sales/{reference}/resume",
            headers=_api_headers(), json={'terminal_id': terminal_id},
            timeout=_TIMEOUT, verify=_ssl_verify(),
        )
        if r.status_code == 200:
            return r.json(), None
        if r.status_code == 404:
            return None, 'NOT_FOUND'
        if r.status_code == 409:
            return None, 'INVALID_STATUS'
        logging.warning("[BOP API] resume_hold HTTP %s: %s", r.status_code, r.text[:200])
        return None, 'ERROR'
    except Exception as e:
        logging.warning("[BOP API] resume_hold(%s): %s", reference, e)
        return None, 'OFFLINE'


def void_hold(reference: str) -> bool:
    """Cancel a held sale outright. Returns True on success."""
    try:
        r = requests.post(f"{_api_url()}/api/v1/held-sales/{reference}/void",
                           headers=_api_headers(), timeout=_TIMEOUT, verify=_ssl_verify())
        return r.status_code == 200
    except Exception as e:
        logging.warning("[BOP API] void_hold(%s): %s", reference, e)
        return False


def get_store_info() -> dict | None:
    """Fetch store settings from BackOfficePro (name, address, phone, gst_rate)."""
    try:
        r = requests.get(f"{_api_url()}/api/v1/store",
                         headers=_api_headers(), timeout=_TIMEOUT,
                         verify=_ssl_verify())
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning("[BOP API] get_store_info: %s", e)
    return None


def get_image_path(barcode: str) -> str | None:
    """Return a path to the product image, or None if not found.

    Checks the local sync cache first, then falls back to BackOfficePro's
    image directory when both apps run on the same machine.
    """
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        p = os.path.join(_IMAGES_DIR, f"{barcode}.{ext}")
        if os.path.exists(p):
            return p
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        p = os.path.normpath(os.path.join(_BOP_IMAGES_DIR, f"{barcode}.{ext}"))
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
                headers=_api_headers(),
                timeout=5, stream=True,
                verify=_ssl_verify(),
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
            logging.debug("[BOP API] sync_images(%s): %s", barcode, e)
    if downloaded:
        logging.info("[BOP API] Downloaded %d product image(s)", downloaded)
    return downloaded


def fetch_bundles() -> list | None:
    """
    Fetch active bundles from BackOfficePro.
    Returns list of bundles on success, None if offline/error (distinct from [] = no bundles).
    Each bundle: {id, name, required_qty, price, eligible: [{barcode, unit_qty}]}
    """
    try:
        r = requests.get(f"{_api_url()}/api/v1/bundles",
                         headers=_api_headers(), timeout=_TIMEOUT,
                         verify=_ssl_verify())
        if r.status_code == 200:
            return r.json()
        logging.warning("[BOP API] fetch_bundles HTTP %s", r.status_code)
    except Exception as e:
        logging.warning("[BOP API] fetch_bundles: %s", e)
    return None


def sync_product_cache() -> int:
    """
    Pull all active products from BackOfficePro and refresh the local cache.
    Paginates until the API returns a short page. Returns 0 if unreachable.
    """
    _PAGE = 500
    products: list = []
    offset = 0
    while True:
        page = list_products(limit=_PAGE, offset=offset)
        if not page:
            break
        products.extend(page)
        if len(page) < _PAGE:
            break  # reached the last (or only) page
        offset += _PAGE

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
        logging.info("[BOP API] Product cache synced: %d products", len(products))
        return len(products)
    except Exception as e:
        logging.error("[BOP API] sync_product_cache write failed: %s", e)
        conn.rollback()
        return 0
    finally:
        conn.close()
