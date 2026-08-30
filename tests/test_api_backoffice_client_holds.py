"""
Tests for the hold/resume-sale methods in api/backoffice_client.py.

Mocks requests.get/requests.post directly, matching the conventions in
test_api_url_cache.py. Focus: URL/header construction, and resume_hold's
three-way (data, error_code) mapping from HTTP status, since the UI depends
on distinguishing 'NOT_FOUND' / 'INVALID_STATUS' / 'OFFLINE'.
"""
import pytest
from unittest.mock import patch
import api.backoffice_client as bop


@pytest.fixture(autouse=True)
def reset_caches():
    bop._cached_url = None
    bop._cached_api_key = None
    yield
    bop._cached_url = None
    bop._cached_api_key = None


class TestCreateHold:
    def test_success_returns_json(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {'id': 1, 'reference': 'HLD-00001'}
            result = bop.create_hold('POS-001', 'alice', [{'barcode': '123'}],
                                      subtotal=10.0, gst_amount=1.0, total=11.0)
        assert result == {'id': 1, 'reference': 'HLD-00001'}
        assert mock_post.call_args[0][0].endswith('/api/v1/held-sales')

    def test_sends_api_key_and_payload(self, test_db):
        with patch('utils.credentials.get_credential', return_value='secret'), \
             patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 201
            mock_post.return_value.json.return_value = {'reference': 'HLD-00001'}
            bop.create_hold('POS-001', 'alice', [{'barcode': '123'}],
                             subtotal=10.0, gst_amount=1.0, total=11.0, note='milk run')
        kwargs = mock_post.call_args[1]
        assert kwargs['headers'] == {'X-API-Key': 'secret'}
        payload = kwargs['json']
        assert payload['terminal_id'] == 'POS-001'
        assert payload['operator'] == 'alice'
        assert payload['note'] == 'milk run'
        assert payload['items'] == [{'barcode': '123'}]

    def test_failure_returns_none(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 500
            mock_post.return_value.text = 'boom'
            result = bop.create_hold('POS-001', 'alice', [], subtotal=0, gst_amount=0, total=0)
        assert result is None

    def test_exception_returns_none(self, test_db):
        with patch('requests.post', side_effect=Exception('down')):
            result = bop.create_hold('POS-001', 'alice', [], subtotal=0, gst_amount=0, total=0)
        assert result is None


class TestListOpenHolds:
    def test_success_returns_list(self, test_db):
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = [{'reference': 'HLD-00001'}]
            result = bop.list_open_holds()
        assert result == [{'reference': 'HLD-00001'}]
        assert mock_get.call_args[0][0].endswith('/api/v1/held-sales')

    def test_offline_returns_none_not_empty_list(self, test_db):
        with patch('requests.get', side_effect=Exception('down')):
            result = bop.list_open_holds()
        assert result is None  # distinct from [] = "no open holds"


class TestGetHold:
    def test_success_returns_json(self, test_db):
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'reference': 'HLD-00001', 'lines': []}
            result = bop.get_hold('HLD-00001')
        assert result == {'reference': 'HLD-00001', 'lines': []}
        assert mock_get.call_args[0][0].endswith('/api/v1/held-sales/HLD-00001')

    def test_not_found_returns_none(self, test_db):
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 404
            result = bop.get_hold('HLD-99999')
        assert result is None


class TestResumeHold:
    def test_success_returns_data_and_none_error(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {'reference': 'HLD-00001', 'lines': []}
            data, error = bop.resume_hold('HLD-00001', 'POS-002')
        assert data == {'reference': 'HLD-00001', 'lines': []}
        assert error is None
        assert mock_post.call_args[1]['json'] == {'terminal_id': 'POS-002'}

    def test_not_found_maps_404(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 404
            data, error = bop.resume_hold('HLD-BOGUS', 'POS-002')
        assert data is None
        assert error == 'NOT_FOUND'

    def test_already_resumed_maps_409(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 409
            data, error = bop.resume_hold('HLD-00001', 'POS-002')
        assert data is None
        assert error == 'INVALID_STATUS'

    def test_network_failure_maps_offline(self, test_db):
        with patch('requests.post', side_effect=Exception('down')):
            data, error = bop.resume_hold('HLD-00001', 'POS-002')
        assert data is None
        assert error == 'OFFLINE'

    def test_other_http_error_maps_error(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 500
            mock_post.return_value.text = 'boom'
            data, error = bop.resume_hold('HLD-00001', 'POS-002')
        assert data is None
        assert error == 'ERROR'


class TestVoidHold:
    def test_success_returns_true(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            assert bop.void_hold('HLD-00001') is True

    def test_failure_returns_false(self, test_db):
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 409
            assert bop.void_hold('HLD-00001') is False

    def test_exception_returns_false(self, test_db):
        with patch('requests.post', side_effect=Exception('down')):
            assert bop.void_hold('HLD-00001') is False
