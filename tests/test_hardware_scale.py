"""
Tests for hardware.scale._parse_weight.

Key properties verified:
 - MT-SICS kg and gram responses are parsed correctly
 - Explicit kg/gram unit suffixes are always respected (no heuristic override)
 - The unitless heuristic (> 5000 → grams) only fires when there is no suffix
 - Explicit kg must NOT be divided by 1000 even when the value exceeds 5000
"""
import pytest
from hardware.scale import _parse_weight


class TestParseWeightMTSICS:
    def test_stable_kg(self):
        assert _parse_weight("S S +   1.234 kg") == pytest.approx(1.234)

    def test_unstable_kg(self):
        assert _parse_weight("S D +   0.625 kg") == pytest.approx(0.625)

    def test_stable_grams(self):
        # Some MT-SICS scales can be configured to output grams
        assert _parse_weight("S S +   1234 g") == pytest.approx(1.234)

    def test_negative_value_returns_absolute(self):
        # Scale tared below zero — return absolute value
        assert _parse_weight("S S -   0.010 kg") == pytest.approx(0.010)

    def test_zero_weight_returns_zero(self):
        assert _parse_weight("S S +   0.000 kg") == pytest.approx(0.0)


class TestParseWeightExplicitUnit:
    def test_kg_lowercase(self):
        assert _parse_weight("1.234 kg") == pytest.approx(1.234)

    def test_kg_uppercase(self):
        assert _parse_weight("+001.234KG") == pytest.approx(1.234)

    def test_kg_large_value_not_divided(self):
        # Explicit 'kg' must be trusted regardless of magnitude.
        # This was the bug: values > 5000 with an explicit unit were incorrectly
        # divided by 1000 by the unitless heuristic fallback.
        assert _parse_weight("5001.000 kg") == pytest.approx(5001.0)

    def test_grams_lowercase(self):
        assert _parse_weight("1234 g") == pytest.approx(1.234)

    def test_grams_uppercase(self):
        assert _parse_weight("+001234G") == pytest.approx(1.234)

    def test_grams_with_decimal(self):
        assert _parse_weight("625.0 g") == pytest.approx(0.625)


class TestParseWeightUnitlessHeuristic:
    def test_small_decimal_treated_as_kg(self):
        # 1.234 — clearly kg for a retail scale
        assert _parse_weight("1.234") == pytest.approx(1.234)

    def test_large_value_treated_as_grams(self):
        # 5001 — likely grams (5.001 kg)
        assert _parse_weight("5001.0") == pytest.approx(5.001)

    def test_boundary_at_threshold_not_converted(self):
        # 5000.0 is NOT > 5000 so it is returned as-is (5000.0 "kg")
        assert _parse_weight("5000.0") == pytest.approx(5000.0)

    def test_boundary_above_threshold_converted(self):
        # 5000.1 > 5000 → treated as grams → 5.0001 kg
        assert _parse_weight("5000.1") == pytest.approx(5000.1 / 1000.0)


class TestParseWeightEdgeCases:
    def test_empty_string_returns_none(self):
        assert _parse_weight("") is None

    def test_no_number_returns_none(self):
        assert _parse_weight("S I") is None

    def test_status_only_returns_none(self):
        # MT-SICS "Scale at minimum" response
        assert _parse_weight("S +") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_weight("   ") is None
