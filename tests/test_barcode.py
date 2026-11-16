

class TestRealScaleLabels:
    """Exact labels printed by the store's weigh-and-label scale (price in cents embedded)."""
    CFG = {'scale_label_value_type': 'price', 'scale_label_check': '1'}

    def test_real_labels_decode_to_price(self):
        from utils.barcode import decode_scale_label
        for code, plu, price in [("0210334013915", "10334", 13.91),
                                 ("0206837010322", "6837", 10.32),
                                 ("0200132003508", "132", 3.50)]:
            assert decode_scale_label(code, self.CFG) == {'plu': plu, 'weight_kg': None, 'price': price}

    def test_salami_label_prices_back_to_printed_total(self):
        from views.pos_screen import POSScreen
        from utils.barcode import decode_scale_label
        label = decode_scale_label("0210334013915", self.CFG)
        qty = POSScreen._scale_label_qty({'sell_price': 36.99}, label)
        assert qty == 0.376 and round(qty * 36.99, 2) == 13.91

import utils.barcode as MOD


class TestEncodeScaleLabel:
    """Encoder must reproduce the labels the store's scale actually prints."""
    CFG = {'scale_label_value_type': 'price', 'scale_label_check': '1'}
    REAL = [("10334", 13.91, "0210334013915"),
            ("6837", 10.32, "0206837010322"),
            ("132", 3.50, "0200132003508")]

    def test_reproduces_real_labels(self):
        for plu, price, code in self.REAL:
            assert MOD.encode_scale_label(plu, price=price, cfg=self.CFG) == code

    def test_round_trip(self):
        for plu, price, code in self.REAL:
            r = MOD.decode_scale_label(code, self.CFG)
            assert r == {'plu': plu, 'weight_kg': None, 'price': price}

    def test_weight_mode(self):
        code = MOD.encode_scale_label("10334", weight_kg=0.376, cfg={'scale_label_value_type': 'weight'})
        assert MOD.decode_scale_label(code, {'scale_label_value_type': 'weight'})['weight_kg'] == 0.376

    def test_price_for_weight_matches_label(self):
        assert MOD.price_for_weight(0.376, 36.99) == 13.91
        assert MOD.price_for_weight(0.430, 23.99) == 10.32

    def test_rejects_bad_input(self):
        import pytest
        with pytest.raises(ValueError):
            MOD.encode_scale_label("1234567", price=1, cfg=self.CFG)       # PLU too long
        with pytest.raises(ValueError):
            MOD.encode_scale_label("1", price=1000.00, cfg=self.CFG)       # 100000 cents > 5 digits
        with pytest.raises(ValueError):
            MOD.encode_scale_label("1", cfg=self.CFG)                      # no price
