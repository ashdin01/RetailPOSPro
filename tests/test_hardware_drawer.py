"""
Tests for hardware/drawer.py.

The drawer has no connection of its own — it opens via an ESC/POS pulse sent
through the receipt printer's existing network connection (RJ11/RJ12 cable
from printer to drawer), so these tests mirror test_hardware_printer.py's
mocking pattern rather than simulating a separate drawer protocol.
"""
from unittest.mock import patch, MagicMock
import hardware.drawer as drawer
import hardware.printer as printer


class TestOpenDrawerManualOrDisabled:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def test_manual_protocol_does_not_attempt_connection(self, test_db):
        with patch('hardware.printer.get_escpos_client') as mock_client:
            drawer.open_drawer()
        mock_client.assert_not_called()

    def test_disabled_does_not_attempt_connection_even_if_protocol_escpos(self, test_db):
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '0', 'printer_protocol': 'escpos'}), \
             patch('hardware.printer.get_escpos_client') as mock_client:
            drawer.open_drawer()
        mock_client.assert_not_called()

    def test_manual_protocol_does_not_raise(self, test_db):
        drawer.open_drawer()   # must not raise


class TestOpenDrawerEscposEnabled:
    def setup_method(self):
        printer._cached_settings = None

    def teardown_method(self):
        printer._cached_settings = None

    def _escpos_ctx(self, mock_p):
        return (
            patch('hardware.printer._settings',
                  return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                'printer_host': '10.0.0.5', 'printer_port': '9100'}),
            patch('hardware.printer.get_escpos_client', return_value=mock_p),
        )

    def test_sends_kick_pulse_on_default_pin(self, test_db):
        mock_p = MagicMock()
        c1, c2 = self._escpos_ctx(mock_p)
        with c1, c2:
            drawer.open_drawer()
        mock_p.cashdraw.assert_called_once_with(drawer._KICK_PIN)
        mock_p.close.assert_called_once()

    def test_reuses_the_printer_connection_not_a_separate_one(self, test_db):
        """The drawer has no connection of its own — confirm it goes through
        printer.get_escpos_client(), the same network socket printing uses."""
        mock_p = MagicMock()
        c1, c2 = self._escpos_ctx(mock_p)
        with c1, patch('hardware.printer.get_escpos_client',
                       return_value=mock_p) as mock_get_client:
            drawer.open_drawer()
        mock_get_client.assert_called_once()

    def test_kick_failure_is_caught_not_raised(self, test_db):
        with patch('hardware.printer._settings',
                   return_value={'printer_enabled': '1', 'printer_protocol': 'escpos',
                                 'printer_host': '10.0.0.5', 'printer_port': '9100'}), \
             patch('hardware.printer.get_escpos_client',
                   side_effect=Exception('unreachable')):
            drawer.open_drawer()   # must not raise

    def test_cashdraw_exception_is_caught_not_raised(self, test_db):
        mock_p = MagicMock()
        mock_p.cashdraw.side_effect = Exception('cash drawer error')
        c1, c2 = self._escpos_ctx(mock_p)
        with c1, c2:
            drawer.open_drawer()   # must not raise
