"""
Tests for hardware.eftpos public API and EFTPOSResult behaviour.

Linkly network calls are not exercised here; only the manual-mode paths and
the EFTPOSResult class properties are tested.
"""
import pytest
from hardware.eftpos import EFTPOSResult, process_purchase, settle_terminal
from unittest.mock import patch


class TestSettingsCache:
    def setup_method(self):
        import hardware.eftpos as eftpos
        eftpos._cached_settings = None

    def teardown_method(self):
        import hardware.eftpos as eftpos
        eftpos._cached_settings = None

    def test_settings_cached_after_first_read(self, test_db):
        import hardware.eftpos as eftpos
        with patch('utils.credentials.get_credential', return_value=None):
            s1 = eftpos._settings()
            s2 = eftpos._settings()
        assert s1 is s2

    def test_invalidate_clears_cache(self, test_db):
        import hardware.eftpos as eftpos
        with patch('utils.credentials.get_credential', return_value=None):
            eftpos._settings()
        eftpos.invalidate_settings_cache()
        assert eftpos._cached_settings is None

    def test_re_read_after_invalidate(self, test_db):
        import hardware.eftpos as eftpos
        from database.connection import get_connection
        with patch('utils.credentials.get_credential', return_value=None):
            eftpos._settings()
            eftpos.invalidate_settings_cache()
            conn = get_connection()
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('eftpos_host', 'newhost') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            conn.commit()
            conn.close()
            s = eftpos._settings()
        assert s.get('eftpos_host') == 'newhost'


class TestEFTPOSResult:
    def test_approved_property(self):
        assert EFTPOSResult(EFTPOSResult.APPROVED).approved is True
        assert EFTPOSResult(EFTPOSResult.DECLINED).approved is False

    def test_declined_property(self):
        assert EFTPOSResult(EFTPOSResult.DECLINED).declined is True
        assert EFTPOSResult(EFTPOSResult.APPROVED).declined is False

    def test_cancelled_property(self):
        assert EFTPOSResult(EFTPOSResult.CANCELLED).cancelled is True

    def test_error_property(self):
        assert EFTPOSResult(EFTPOSResult.ERROR).error is True

    def test_manual_required_is_not_approved(self):
        r = EFTPOSResult('manual_required')
        assert r.approved is False

    def test_manual_required_is_not_declined(self):
        r = EFTPOSResult('manual_required')
        assert r.declined is False

    def test_manual_required_status_preserved(self):
        r = EFTPOSResult('manual_required', message="test")
        assert r.status == 'manual_required'
        assert r.message == "test"

    def test_repr_contains_status(self):
        r = EFTPOSResult(EFTPOSResult.APPROVED, response_code='00', message='OK')
        assert 'approved' in repr(r)

    def test_code_75_is_declined_not_cancelled(self):
        from hardware.eftpos import _LinklyClient
        r = _LinklyClient._parse_response(
            {'ResponseCode': '75', 'ResponseText': 'PIN tries exceeded', 'Success': False}
        )
        assert r.declined is True, "Code 75 (PIN tries exceeded) must be DECLINED"
        assert r.cancelled is False

    def test_code_17_is_cancelled(self):
        from hardware.eftpos import _LinklyClient
        r = _LinklyClient._parse_response(
            {'ResponseCode': '17', 'ResponseText': 'Cancelled', 'Success': False}
        )
        assert r.cancelled is True

    def test_code_00_is_approved(self):
        from hardware.eftpos import _LinklyClient
        r = _LinklyClient._parse_response(
            {'ResponseCode': '00', 'ResponseText': 'Approved', 'Success': True, 'RRN': 'ABC123'}
        )
        assert r.approved is True


class TestProcessPurchaseManual:
    def test_returns_manual_required_status(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = process_purchase(1000, 'test-ref')
        assert result.status == 'manual_required'

    def test_manual_result_is_not_approved(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = process_purchase(1000)
        assert result.approved is False

    def test_manual_result_is_not_error(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = process_purchase(500)
        assert result.error is False

    def test_unknown_protocol_returns_error(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'foobar', 'eftpos_password': ''}):
            result = process_purchase(1000)
        assert result.error is True
        assert 'foobar' in result.message


class TestSettleTerminalManual:
    def test_returns_manual_required_status(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = settle_terminal()
        assert result.status == 'manual_required'

    def test_manual_result_is_not_approved(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = settle_terminal()
        assert result.approved is False

    def test_manual_result_has_message(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'manual', 'eftpos_password': ''}):
            result = settle_terminal()
        assert result.message  # non-empty instructions for the cashier

    def test_unknown_protocol_returns_error(self, test_db):
        with patch('hardware.eftpos._settings',
                   return_value={'eftpos_protocol': 'foobar', 'eftpos_password': ''}):
            result = settle_terminal()
        assert result.error is True
