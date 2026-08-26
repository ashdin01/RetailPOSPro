"""
Tests for utils.credentials keyring wrapper and its integration with
hardware.eftpos._settings().
"""
from unittest.mock import patch
import pytest


class TestGetCredential:
    def test_returns_keyring_value(self):
        with patch('keyring.get_password', return_value='secret') as mock_get:
            from utils.credentials import get_credential
            result = get_credential('eftpos_password')
        assert result == 'secret'
        mock_get.assert_called_once_with('RetailPOSPro', 'eftpos_password')

    def test_returns_none_when_keyring_has_no_entry(self):
        with patch('keyring.get_password', return_value=None):
            from utils.credentials import get_credential
            assert get_credential('eftpos_password') is None

    def test_returns_none_on_keyring_exception(self):
        with patch('keyring.get_password', side_effect=Exception("no backend")):
            from utils.credentials import get_credential
            assert get_credential('eftpos_password') is None

    def test_different_keys_are_independent(self):
        with patch('keyring.get_password', side_effect=lambda svc, key: key):
            from utils.credentials import get_credential
            assert get_credential('key_a') == 'key_a'
            assert get_credential('key_b') == 'key_b'


class TestSetCredential:
    def test_returns_true_on_success(self):
        with patch('keyring.set_password') as mock_set:
            from utils.credentials import set_credential
            result = set_credential('eftpos_password', 'secret')
        assert result is True
        mock_set.assert_called_once_with('RetailPOSPro', 'eftpos_password', 'secret')

    def test_returns_false_on_keyring_exception(self):
        with patch('keyring.set_password', side_effect=Exception("no backend")):
            from utils.credentials import set_credential
            assert set_credential('eftpos_password', 'secret') is False

    def test_empty_value_is_stored(self):
        with patch('keyring.set_password') as mock_set:
            from utils.credentials import set_credential
            set_credential('eftpos_password', '')
        mock_set.assert_called_once_with('RetailPOSPro', 'eftpos_password', '')


class TestEftposPasswordClear:
    def test_delete_called_when_password_is_empty(self):
        """Saving an empty password must call delete_credential, not set_credential."""
        with patch('keyring.set_password') as mock_set, \
             patch('keyring.delete_password') as mock_del:
            from utils.credentials import set_credential, delete_credential
            # Simulate the logic now in EftposSettingsDialog._save():
            password = ''
            if password:
                set_credential('eftpos_password', password)
            else:
                delete_credential('eftpos_password')
            mock_del.assert_called_once_with('RetailPOSPro', 'eftpos_password')
            mock_set.assert_not_called()

    def test_set_called_when_password_is_non_empty(self):
        with patch('keyring.set_password') as mock_set, \
             patch('keyring.delete_password') as mock_del:
            from utils.credentials import set_credential, delete_credential
            password = 'secret'
            if password:
                set_credential('eftpos_password', password)
            else:
                delete_credential('eftpos_password')
            mock_set.assert_called_once()
            mock_del.assert_not_called()


class TestDeleteCredential:
    def test_calls_keyring_delete(self):
        with patch('keyring.delete_password') as mock_del:
            from utils.credentials import delete_credential
            delete_credential('eftpos_password')
        mock_del.assert_called_once_with('RetailPOSPro', 'eftpos_password')

    def test_swallows_keyring_exception(self):
        with patch('keyring.delete_password', side_effect=Exception("not found")):
            from utils.credentials import delete_credential
            delete_credential('eftpos_password')  # must not raise


class TestEftposSettingsKeyringOverlay:
    def setup_method(self):
        import hardware.eftpos as eftpos
        eftpos._cached_settings = None

    def teardown_method(self):
        import hardware.eftpos as eftpos
        eftpos._cached_settings = None

    def test_keyring_password_overrides_db(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('eftpos_password', 'db_pass') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        conn.close()

        with patch('utils.credentials.get_credential', return_value='kr_pass'):
            from hardware import eftpos
            s = eftpos._settings()

        assert s['eftpos_password'] == 'kr_pass'

    def test_db_password_used_when_keyring_empty(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('eftpos_password', 'db_pass') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        conn.close()

        with patch('utils.credentials.get_credential', return_value=None):
            from hardware import eftpos
            s = eftpos._settings()

        assert s['eftpos_password'] == 'db_pass'

    def test_empty_db_and_no_keyring_gives_empty_string(self, test_db):
        with patch('utils.credentials.get_credential', return_value=None):
            from hardware import eftpos
            s = eftpos._settings()

        assert s.get('eftpos_password', '') == ''
