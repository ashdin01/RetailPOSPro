"""
Tests for the api/backoffice_client URL cache.

The cache avoids opening a DB connection on every HTTP call. Tests verify:
 - URL is read from DB on the first call and cached thereafter
 - invalidate_url_cache() forces a re-read on the next call
 - The cache never leaks state between tests (fixture resets it)
"""
import pytest
from unittest.mock import patch
import api.backoffice_client as bop
from database.connection import get_connection


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset all module-level caches before and after every test."""
    bop._cached_url = None
    bop._cached_api_key = None
    yield
    bop._cached_url = None
    bop._cached_api_key = None


def _set_url(url: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('backoffice_url', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (url,)
    )
    conn.commit()
    conn.close()


class TestApiUrlCache:
    def test_reads_url_from_db_on_first_call(self, test_db):
        _set_url('http://test-host:9000')
        assert bop._api_url() == 'http://test-host:9000'

    def test_caches_after_first_read(self, test_db):
        _set_url('http://first:5050')
        bop._api_url()  # primes the cache

        # Change DB — cache must NOT be invalidated
        _set_url('http://second:6060')
        assert bop._api_url() == 'http://first:5050'

    def test_invalidate_forces_re_read(self, test_db):
        _set_url('http://old:5050')
        bop._api_url()                  # prime

        _set_url('http://new:6060')
        bop.invalidate_url_cache()

        assert bop._api_url() == 'http://new:6060'

    def test_trailing_slash_is_stripped(self, test_db):
        _set_url('http://host:5050/')
        assert not bop._api_url().endswith('/')

    def test_falls_back_to_default_when_not_set(self, test_db):
        # No backoffice_url in settings — default inserted by schema setup
        url = bop._api_url()
        assert url.startswith('http://')

    def test_cache_is_none_after_invalidate(self, test_db):
        _set_url('http://x:1')
        bop._api_url()
        bop.invalidate_url_cache()
        assert bop._cached_url is None

    def test_multiple_invalidate_calls_are_safe(self, test_db):
        bop.invalidate_url_cache()
        bop.invalidate_url_cache()  # must not raise
        _set_url('http://host:5050')
        assert bop._api_url() == 'http://host:5050'

    def test_api_url_returns_default_when_row_missing(self, test_db):
        """If backoffice_url is absent from settings, must return the default, not crash."""
        conn = get_connection()
        conn.execute("DELETE FROM settings WHERE key='backoffice_url'")
        conn.commit()
        conn.close()
        url = bop._api_url()
        assert url.startswith('http://')
        assert not url.endswith('/')


class TestApiKeyCache:
    def _set_key_in_db(self, key: str):
        conn = get_connection()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('backoffice_api_key', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key,)
        )
        conn.commit()
        conn.close()

    def test_reads_key_from_db_when_keyring_empty(self, test_db):
        self._set_key_in_db('test-key-123')
        with patch('utils.credentials.get_credential', return_value=None):
            assert bop._api_key() == 'test-key-123'

    def test_keyring_takes_precedence_over_db(self, test_db):
        self._set_key_in_db('db-key')
        with patch('utils.credentials.get_credential', return_value='kr-key'):
            assert bop._api_key() == 'kr-key'

    def test_returns_empty_string_when_not_configured(self, test_db):
        with patch('utils.credentials.get_credential', return_value=None):
            assert bop._api_key() == ''

    def test_key_is_cached(self, test_db):
        with patch('utils.credentials.get_credential', return_value='key1'):
            bop._api_key()  # prime
        with patch('utils.credentials.get_credential', return_value='key2'):
            assert bop._api_key() == 'key1'  # still cached

    def test_invalidate_forces_re_read(self, test_db):
        with patch('utils.credentials.get_credential', return_value='key1'):
            bop._api_key()
        bop.invalidate_api_key_cache()
        with patch('utils.credentials.get_credential', return_value='key2'):
            assert bop._api_key() == 'key2'

    def test_api_headers_includes_key(self, test_db):
        with patch('utils.credentials.get_credential', return_value='my-secret'):
            headers = bop._api_headers()
        assert headers == {'X-API-Key': 'my-secret'}

    def test_health_check_sends_no_key(self, test_db):
        """check_health is exempt from auth and must not send the API key header."""
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'status': 'ok'}
            bop.check_health()
        call_kwargs = mock_get.call_args
        sent_headers = call_kwargs[1].get('headers', {}) if call_kwargs[1] else {}
        assert 'X-API-Key' not in sent_headers

    def test_get_product_sends_api_key(self, test_db):
        """Authenticated endpoints must include the X-API-Key header."""
        with patch('utils.credentials.get_credential', return_value='secret'), \
             patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'barcode': '123'}
            bop.get_product('123')
        sent_headers = mock_get.call_args[1].get('headers', {})
        assert sent_headers.get('X-API-Key') == 'secret'

    def test_post_sale_sends_api_key(self, test_db):
        with patch('utils.credentials.get_credential', return_value='secret'), \
             patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {'ok': True}
            bop.post_sale({'reference': 'T-001'})
        sent_headers = mock_post.call_args[1].get('headers', {})
        assert sent_headers.get('X-API-Key') == 'secret'


class TestSyncProductCachePagination:
    def test_paginates_across_multiple_pages(self, test_db):
        """sync_product_cache must fetch all pages, not stop at the first 500."""
        from unittest.mock import patch

        _PAGE = 500
        page1 = [
            {'barcode': str(i), 'plu': str(i), 'description': f'P{i}',
             'brand': '', 'dept_name': '', 'group_name': '', 'unit': 'EA',
             'sell_price': 1.0, 'tax_rate': 10.0}
            for i in range(_PAGE)
        ]
        page2 = [
            {'barcode': str(i + _PAGE), 'plu': str(i + _PAGE), 'description': f'P{i + _PAGE}',
             'brand': '', 'dept_name': '', 'group_name': '', 'unit': 'EA',
             'sell_price': 1.0, 'tax_rate': 10.0}
            for i in range(100)
        ]

        def _mock_list(search='', limit=500, offset=0):
            all_products = page1 + page2
            return all_products[offset: offset + limit]

        with patch('api.backoffice_client.list_products', side_effect=_mock_list):
            count = bop.sync_product_cache()

        assert count == _PAGE + 100

    def test_single_page_still_works(self, test_db):
        from unittest.mock import patch
        products = [
            {'barcode': '1', 'plu': '1', 'description': 'X',
             'brand': '', 'dept_name': '', 'group_name': '', 'unit': 'EA',
             'sell_price': 1.0, 'tax_rate': 10.0}
        ]
        with patch('api.backoffice_client.list_products', return_value=products):
            count = bop.sync_product_cache()
        assert count == 1

    def test_empty_response_returns_zero(self, test_db):
        from unittest.mock import patch
        with patch('api.backoffice_client.list_products', return_value=[]):
            count = bop.sync_product_cache()
        assert count == 0
