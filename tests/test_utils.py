from utils.format import currency, gst_from_total
from utils.barcode import decode_weight_barcode


class TestCurrencyFormat:
    def test_basic(self):
        assert currency(1234.56) == "$1,234.56"

    def test_zero(self):
        assert currency(0) == "$0.00"

    def test_thousands(self):
        assert currency(10000.0) == "$10,000.00"

    def test_cents_only(self):
        assert currency(0.05) == "$0.05"

    def test_negative(self):
        assert currency(-9.99) == "$-9.99"


class TestGstFromTotal:
    def test_standard_rate(self):
        # $11.00 inclusive of 10% GST → $1.00 GST
        assert gst_from_total(11.00) == 1.00

    def test_zero_amount(self):
        assert gst_from_total(0.0) == 0.0

    def test_custom_rate(self):
        # $105 at 5% → $5.00 GST
        assert gst_from_total(105.0, rate=5.0) == 5.00

    def test_rounds_to_cents(self):
        result = gst_from_total(1.99)
        assert result == round(1.99 * 10 / 110, 2)

    def test_returns_float(self):
        assert isinstance(gst_from_total(10.0), float)


class TestDecodeWeightBarcode:
    # Format: 2 D PPPPP WWWWW C  (13 digits)
    # Indices: 0 1  2-6   7-11 12

    def test_valid_barcode(self):
        # zone=1, PLU=00123, weight=01500g=1.5kg, check=0
        barcode = "2100123015000"
        result = decode_weight_barcode(barcode)
        assert result is not None
        assert result["plu"] == "123"
        assert result["weight_kg"] == 1.5

    def test_non_weight_prefix(self):
        assert decode_weight_barcode("9300675009657") is None

    def test_wrong_length(self):
        assert decode_weight_barcode("21001230150") is None

    def test_zero_weight_is_invalid(self):
        # weight digits 00000 → 0 kg
        assert decode_weight_barcode("2100123000000") is None

    def test_non_digit_character(self):
        assert decode_weight_barcode("210012301500A") is None

    def test_plu_leading_zeros_stripped(self):
        # PLU 00001 → '1'
        barcode = "2100001005000"
        result = decode_weight_barcode(barcode)
        assert result is not None
        assert result["plu"] == "1"

    def test_all_zero_plu_becomes_zero(self):
        # PLU 00000 → '0' (fallback from or '0')
        barcode = "2100000005000"
        result = decode_weight_barcode(barcode)
        assert result is not None
        assert result["plu"] == "0"

    def test_small_weight(self):
        # 250g = 0.25 kg
        barcode = "2100005002500"
        result = decode_weight_barcode(barcode)
        assert result is not None
        assert result["weight_kg"] == 0.25


# ── Configurable scale-label decoding ────────────────────────────────────────
from utils.barcode import decode_scale_label, ean13_check_ok


def _ean13(body12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


class TestDecodeScaleLabel:
    def test_default_layout_matches_legacy_weight_decoder(self):
        code = _ean13("210014000625")  # 2 D=1 PLU=00140 W=00625
        assert decode_scale_label(code, {}) == {'plu': '140', 'weight_kg': 0.625, 'price': None}

    def test_price_label(self):
        cfg = {'scale_label_plu_start': '1', 'scale_label_plu_len': '6',
               'scale_label_value_start': '7', 'scale_label_value_len': '5',
               'scale_label_value_type': 'price'}
        code = _ean13("200014000845")  # PLU 000140, $8.45
        assert decode_scale_label(code, cfg) == {'plu': '140', 'weight_kg': None, 'price': 8.45}

    def test_multiple_prefixes(self):
        cfg = {'scale_label_prefixes': '20,21'}
        assert decode_scale_label(_ean13("210014000625"), cfg) is not None
        assert decode_scale_label(_ean13("220014000625"), cfg) is None

    def test_upca_style_02_prefix_label_decodes_by_default(self):
        # UPC-A 2PPPPPVVVVVC arrives from the scanner as 13 digits with a leading 0
        from_ = {}
        code = "0200140006953"
        r = decode_scale_label(code, from_)
        assert r == {'plu': '140', 'weight_kg': 0.695, 'price': None}

    def test_normal_barcode_not_decoded(self):
        assert decode_scale_label("9300675009657", {}) is None

    def test_disabled(self):
        assert decode_scale_label(_ean13("210014000625"), {'scale_label_enabled': '0'}) is None

    def test_zero_value_rejected(self):
        assert decode_scale_label(_ean13("210014000000"), {}) is None

    def test_check_digit_enforced_when_enabled(self):
        good = _ean13("210014000625")
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        assert decode_scale_label(bad, {}) is not None
        assert decode_scale_label(bad, {'scale_label_check': '1'}) is None
        assert decode_scale_label(good, {'scale_label_check': '1'}) is not None

    def test_out_of_range_config_rejected(self):
        assert decode_scale_label(_ean13("210014000625"), {'scale_label_value_start': '9'}) is None
        assert decode_scale_label(_ean13("210014000625"), {'scale_label_plu_len': 'x'}) is None

    def test_ean13_check_ok(self):
        assert ean13_check_ok(_ean13("400638133393"))
