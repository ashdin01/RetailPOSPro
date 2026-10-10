"""POS scan of a weigh-and-label barcode: weight labels use the embedded
weight, price labels convert the embedded price to kg at the sell price."""
import pytest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication

import api.backoffice_client as bop
from utils.barcode import SCALE_LABEL_DEFAULTS

PRODUCT = {'barcode': '9300000140140', 'description': 'Seedless Watermelon',
           'sell_price': 2.50, 'tax_rate': 0.0, 'plu': '140'}


@pytest.fixture(autouse=True)
def _mock_background_sync():
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


def _scan(screen, code, cfg):
    with patch('views.pos_screen.decode_scale_label',
               side_effect=lambda t: __import__('utils.barcode', fromlist=['x'])
               .decode_scale_label(t, {**SCALE_LABEL_DEFAULTS, **cfg})), \
         patch('views.pos_screen.product_model.get_by_plu',
               side_effect=lambda p: PRODUCT if p == '140' else None), \
         patch('views.pos_screen.product_model.get_by_barcode', return_value=None):
        screen._scan_input.setText(code)
        screen._on_scan_enter()


def test_weight_label_adds_embedded_weight(pos_screen):
    _scan(pos_screen, '2100140006251', {})  # 0.625 kg
    assert pos_screen._basket[0]['qty'] == pytest.approx(0.625)
    assert pos_screen._basket[0]['unit_price'] == 2.50


def test_price_label_converts_price_to_weight(pos_screen):
    cfg = {'scale_label_plu_start': '1', 'scale_label_plu_len': '6',
           'scale_label_value_start': '7', 'scale_label_value_len': '5',
           'scale_label_value_type': 'price'}
    _scan(pos_screen, '2000140010001', cfg)  # $10.00 @ $2.50/kg = 4 kg
    line = pos_screen._basket[0]
    assert line['qty'] == pytest.approx(4.0)
    assert round(line['qty'] * line['unit_price'], 2) == 10.00
