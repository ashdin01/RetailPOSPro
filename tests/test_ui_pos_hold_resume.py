"""
Widget regression tests for the suspend/resume-sale feature in POSScreen
(views/pos_screen.py) and views/resume_sale_view.py.

api.backoffice_client is fully mocked — no real HTTP. BackOfficePro sync
calls that fire during POSScreen construction (_start_sync_worker) are
patched to no-ops so they can't race the test or hit a real network.

Requires pytest-qt (installed) and a live display (DISPLAY=:0).
"""
import pytest
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog

import api.backoffice_client as bop


@pytest.fixture(autouse=True)
def _mock_background_sync():
    """POSScreen._start_sync_worker() spawns background threads that call
    these on construction — stub them so tests never hit real HTTP."""
    with patch.object(bop, 'sync_product_cache', return_value=0), \
         patch.object(bop, 'get_store_info', return_value=None), \
         patch.object(bop, 'fetch_bundles', return_value=[]), \
         patch.object(bop, 'check_health', return_value=False):
        yield


@pytest.fixture()
def pos_screen(qtbot, test_db):
    from views.pos_screen import POSScreen
    widget = POSScreen(operator={'id': 1, 'username': 'alice', 'full_name': 'Alice'})
    qtbot.addWidget(widget)
    widget.show()
    QApplication.processEvents()
    return widget


class TestHoldSale:
    def test_hold_sale_clears_basket_on_success(self, pos_screen, monkeypatch):
        pos_screen._basket = [{
            'barcode': '123', 'description': 'Milk 2L', 'qty': 1,
            'unit_price': 3.50, 'tax_rate': 10.0, 'price_reason': '',
        }]
        pos_screen._refresh_basket_table()

        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **kw: QMessageBox.StandardButton.Yes)
        create_hold_spy = MagicMock(return_value={'id': 1, 'reference': 'HLD-00001'})
        monkeypatch.setattr(bop, 'create_hold', create_hold_spy)
        print_ticket_spy = MagicMock()
        monkeypatch.setattr('hardware.printer.print_hold_ticket', print_ticket_spy)

        pos_screen._hold_sale()

        create_hold_spy.assert_called_once()
        assert create_hold_spy.call_args[0][0] == pos_screen._terminal_id()
        assert create_hold_spy.call_args[0][1] == 'alice'
        print_ticket_spy.assert_called_once()
        assert print_ticket_spy.call_args[0][0] == 'HLD-00001'
        assert print_ticket_spy.call_args[0][2] == 3.50  # total
        assert pos_screen._basket == []
        assert pos_screen._overlay is not None

    def test_hold_sale_does_nothing_with_empty_basket(self, pos_screen, monkeypatch):
        create_hold_spy = MagicMock()
        monkeypatch.setattr(bop, 'create_hold', create_hold_spy)
        pos_screen._hold_sale()
        create_hold_spy.assert_not_called()

    def test_hold_sale_declined_confirmation_does_not_call_api(self, pos_screen, monkeypatch):
        pos_screen._basket = [{
            'barcode': '123', 'description': 'Milk 2L', 'qty': 1,
            'unit_price': 3.50, 'tax_rate': 10.0, 'price_reason': '',
        }]
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **kw: QMessageBox.StandardButton.No)
        create_hold_spy = MagicMock()
        monkeypatch.setattr(bop, 'create_hold', create_hold_spy)

        pos_screen._hold_sale()

        create_hold_spy.assert_not_called()
        assert len(pos_screen._basket) == 1  # untouched

    def test_hold_sale_offline_leaves_basket_untouched(self, pos_screen, monkeypatch):
        pos_screen._basket = [{
            'barcode': '123', 'description': 'Milk 2L', 'qty': 1,
            'unit_price': 3.50, 'tax_rate': 10.0, 'price_reason': '',
        }]
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **kw: QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(bop, 'create_hold', MagicMock(return_value=None))
        monkeypatch.setattr(QMessageBox, 'warning', MagicMock())

        pos_screen._hold_sale()

        assert len(pos_screen._basket) == 1  # not cleared on failure

    def test_hold_sale_excludes_bundle_discount_rows(self, pos_screen, monkeypatch):
        pos_screen._basket = [
            {'barcode': '123', 'description': 'Milk 2L', 'qty': 1,
             'unit_price': 3.50, 'tax_rate': 10.0, 'price_reason': ''},
            {'barcode': '__BUNDLE_1__', 'description': '🎁 Deal', 'qty': 1,
             'unit_price': -1.0, 'tax_rate': 10.0, 'price_reason': '',
             'is_bundle_discount': True},
        ]
        monkeypatch.setattr(QMessageBox, 'question',
                             lambda *a, **kw: QMessageBox.StandardButton.Yes)
        create_hold_spy = MagicMock(return_value={'id': 1, 'reference': 'HLD-00002'})
        monkeypatch.setattr(bop, 'create_hold', create_hold_spy)
        monkeypatch.setattr('hardware.printer.print_hold_ticket', MagicMock())

        pos_screen._hold_sale()

        sent_items = create_hold_spy.call_args[0][2]
        assert len(sent_items) == 1
        assert sent_items[0]['barcode'] == '123'


class TestScanToResume:
    def test_scanning_hold_reference_restores_basket(self, pos_screen, monkeypatch):
        mocked_hold = {
            'reference': 'HLD-00001',
            'lines': [
                {'barcode': '123', 'description': 'Milk 2L', 'qty': 2,
                 'unit_price': 3.50, 'tax_rate': 10.0, 'price_reason': ''},
            ],
        }
        monkeypatch.setattr(bop, 'resume_hold', MagicMock(return_value=(mocked_hold, None)))

        pos_screen._scan_input.setText('hld-00001')
        pos_screen._on_scan_enter()

        assert len(pos_screen._basket) == 1
        assert pos_screen._basket[0]['barcode'] == '123'
        assert pos_screen._basket[0]['qty'] == 2

    def test_scanning_unknown_reference_shows_not_found(self, pos_screen, monkeypatch):
        monkeypatch.setattr(bop, 'resume_hold', MagicMock(return_value=(None, 'NOT_FOUND')))

        pos_screen._scan_input.setText('HLD-99999')
        pos_screen._on_scan_enter()

        assert pos_screen._basket == []

    def test_scanning_reference_blocked_when_basket_not_empty(self, pos_screen, monkeypatch):
        pos_screen._basket = [{
            'barcode': '999', 'description': 'Existing item', 'qty': 1,
            'unit_price': 1.0, 'tax_rate': 10.0, 'price_reason': '',
        }]
        resume_spy = MagicMock()
        monkeypatch.setattr(bop, 'resume_hold', resume_spy)
        monkeypatch.setattr(QMessageBox, 'warning', MagicMock())

        pos_screen._scan_input.setText('HLD-00001')
        pos_screen._on_scan_enter()

        resume_spy.assert_not_called()
        assert len(pos_screen._basket) == 1
        assert pos_screen._basket[0]['barcode'] == '999'


class TestResumeSaleView:
    def test_open_resume_sale_blocked_when_basket_not_empty(self, pos_screen, monkeypatch):
        pos_screen._basket = [{
            'barcode': '999', 'description': 'Existing item', 'qty': 1,
            'unit_price': 1.0, 'tax_rate': 10.0, 'price_reason': '',
        }]
        monkeypatch.setattr(QMessageBox, 'warning', MagicMock())
        dialog_spy = MagicMock()
        monkeypatch.setattr('views.resume_sale_view.ResumeSaleView', dialog_spy)

        pos_screen._open_resume_sale()

        dialog_spy.assert_not_called()

    def test_resume_sale_view_lists_open_holds(self, qtbot, pos_screen, monkeypatch):
        from views.resume_sale_view import ResumeSaleView
        holds = [{
            'reference': 'HLD-00001', 'terminal_id': 'POS-002', 'operator': 'bob',
            'item_count': 3, 'total': 12.5, 'created_at': '2026-08-30 10:00:00',
        }]
        monkeypatch.setattr(bop, 'list_open_holds', MagicMock(return_value=holds))

        dlg = ResumeSaleView(pos_screen)
        qtbot.addWidget(dlg)
        dlg.show()
        qtbot.waitUntil(lambda: dlg._list.count() == 1, timeout=2000)

        assert 'HLD-00001' in dlg._list.item(0).text()

    def test_resume_sale_view_resumes_on_double_click(self, qtbot, pos_screen, monkeypatch):
        from views.resume_sale_view import ResumeSaleView
        from PyQt6.QtWidgets import QListWidgetItem
        from PyQt6.QtCore import Qt as _Qt

        mocked_hold = {'reference': 'HLD-00001', 'lines': []}
        monkeypatch.setattr(bop, 'list_open_holds', MagicMock(return_value=[]))
        monkeypatch.setattr(bop, 'resume_hold', MagicMock(return_value=(mocked_hold, None)))

        dlg = ResumeSaleView(pos_screen)
        qtbot.addWidget(dlg)
        dlg.show()

        item = QListWidgetItem("HLD-00001")
        item.setData(_Qt.ItemDataRole.UserRole, 'HLD-00001')
        dlg._list.addItem(item)
        dlg._on_item_activated(item)

        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.resumed_hold == mocked_hold
