"""
Tests for hardware.printer public API.

Mirrors test_hardware_eftpos.py's TestSettingsCache pattern. Real ESC/POS
network calls are not exercised; only settings-cache behavior and the
'manual'-protocol no-op paths are tested (must never attempt a socket
connection when printing is disabled/manual).
"""
import pytest
from unittest.mock import patch, MagicMock
import hardware.printer as printer


class TestSettingsCache:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_settings_cached_after_first_read(self, test_db):
        s1 = printer._settings()
        s2 = printer._settings()
        assert s1 is s2

    def test_invalidate_clears_cache(self, test_db):
        printer._settings()
        printer.invalidate_settings_cache()
        assert printer._cached_settings is None

    def test_re_read_after_invalidate(self, test_db):
        from database.connection import get_connection
        printer._settings()
        printer.invalidate_settings_cache()
        conn = get_connection()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('printer_host', 'newhost') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        conn.close()
        s = printer._settings()
        assert s.get('printer_host') == 'newhost'

    def test_is_enabled_reflects_setting(self, test_db):
        from database.connection import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('printer_enabled', '1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        conn.close()
        printer.invalidate_settings_cache()
        assert printer.is_enabled() is True

    def test_get_protocol_defaults_to_manual(self, test_db):
        assert printer.get_protocol() == 'manual'


class TestPrintReceiptManual:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_manual_protocol_does_not_attempt_connection(self, test_db):
        with patch('hardware.printer._get_escpos_client') as mock_client:
            printer.print_receipt({'reference': 'T-001', 'items': [], 'total': 0})
        mock_client.assert_not_called()

    def test_disabled_does_not_attempt_connection_even_if_protocol_escpos(self, test_db):
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '0', 'printer_protocol': 'escpos'}), \
             patch('hardware.printer._get_escpos_client') as mock_client:
            printer.print_receipt({'reference': 'T-001', 'items': [], 'total': 0})
        mock_client.assert_not_called()

    def test_manual_protocol_does_not_raise(self, test_db):
        printer.print_receipt({'reference': 'T-001', 'items': [], 'total': 0})  # must not raise


class TestPrintHoldTicketManual:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_manual_protocol_does_not_attempt_connection(self, test_db):
        with patch('hardware.printer._get_escpos_client') as mock_client:
            printer.print_hold_ticket('HLD-00001', [], 0)
        mock_client.assert_not_called()

    def test_manual_protocol_does_not_raise(self, test_db):
        printer.print_hold_ticket('HLD-00001', [{'qty': 1, 'description': 'Milk'}], 5.0)


class TestPrintHoldTicketEscposEnabled:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_calls_barcode_with_reference(self, test_db):
        mock_p = MagicMock()
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                 'printer_host': '10.0.0.5', 'printer_port': '9100'}), \
             patch('hardware.printer._get_escpos_client', return_value=mock_p):
            printer.print_hold_ticket('HLD-00001', [{'qty': 1, 'description': 'Milk'}], 5.0)
        mock_p.barcode.assert_called_once()
        assert mock_p.barcode.call_args[0][0] == 'HLD-00001'
        assert mock_p.barcode.call_args[0][1] == 'CODE128'
        mock_p.cut.assert_called_once()

    def test_printer_failure_is_caught_not_raised(self, test_db):
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                 'printer_host': '10.0.0.5', 'printer_port': '9100'}), \
             patch('hardware.printer._get_escpos_client', side_effect=Exception('unreachable')):
            printer.print_hold_ticket('HLD-00001', [], 0)  # must not raise


class TestConnectionTest:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_manual_protocol_returns_true_without_connecting(self, test_db):
        with patch('hardware.printer._get_escpos_client') as mock_client:
            ok, msg = printer.test_connection()
        assert ok is True
        mock_client.assert_not_called()

    def test_escpos_success(self, test_db):
        mock_p = MagicMock()
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                 'printer_host': '10.0.0.5', 'printer_port': '9100'}), \
             patch('hardware.printer._get_escpos_client', return_value=mock_p):
            ok, msg = printer.test_connection()
        assert ok is True

    def test_escpos_failure_returns_false_with_message(self, test_db):
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                 'printer_host': '10.0.0.5', 'printer_port': '9100'}), \
             patch('hardware.printer._get_escpos_client', side_effect=Exception('refused')):
            ok, msg = printer.test_connection()
        assert ok is False
        assert 'refused' in msg
