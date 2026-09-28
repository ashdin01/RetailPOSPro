"""
Widget tests for views/eod_dialog.py's Float Count section — the notes/coins
denomination tally used to count the till at end of day.

This used to be a QLineEdit per denomination (keyboard required, no on-screen
input at all on a touchscreen-only POS). It's now a −/+ tap-target stepper,
same pattern as the refund-quantity stepper in views/refund_dialog.py, with
auto-repeat on hold so a large count doesn't need dozens of individual taps.
The $ subtotal for a denomination also highlights bold green once it has a
count, so a glance at the panel shows which denominations have been entered.
"""
import pytest
from PyQt6.QtWidgets import QApplication

import models.shift as shift_model
from views.eod_dialog import EODDialog, _SUBTOTAL_STYLE_ZERO, _SUBTOTAL_STYLE_COUNTED


@pytest.fixture()
def open_shift(test_db, operator_id):
    return shift_model.open_shift(operator_id, "Test Operator", float_open=100.0)


@pytest.fixture()
def eod(qtbot, open_shift, operator_id):
    dlg = EODDialog(operator_id=operator_id)
    qtbot.addWidget(dlg)
    QApplication.processEvents()
    return dlg


class TestDenomStepper:
    def test_all_denominations_start_at_zero(self, eod):
        assert all(count == 0 for count in eod._denom_counts.values())
        assert all(lbl.text() == "0" for lbl in eod._denom_count_lbls.values())

    def test_plus_tap_increments_count_and_label(self, eod):
        eod._change_denom_count(500, 1)   # $5 note
        assert eod._denom_counts[500] == 1
        assert eod._denom_count_lbls[500].text() == "1"

    def test_minus_tap_floors_at_zero(self, eod):
        eod._change_denom_count(500, -1)
        assert eod._denom_counts[500] == 0
        assert eod._denom_count_lbls[500].text() == "0"

    def test_buttons_have_auto_repeat_for_large_counts(self, eod):
        for btn in list(eod._denom_minus_btns.values()) + list(eod._denom_plus_btns.values()):
            assert btn.autoRepeat() is True

    def test_actual_button_click_updates_count(self, eod):
        """End-to-end through the real widget, not just the handler, since
        that's what a finger taps on the real screen."""
        plus_btn = eod._denom_plus_btns[500]
        plus_btn.click()
        plus_btn.click()
        assert eod._denom_counts[500] == 2
        assert eod._denom_count_lbls[500].text() == "2"

    def test_minus_button_disabled_state_not_forced_but_floor_holds(self, eod):
        """The stepper doesn't disable at zero (unlike the refund dialog,
        where the bound is a real ceiling) — a note/coin count has no upper
        limit and 0 is just the resting state, not a hard floor to guard
        against visually. Clicking minus at 0 must simply no-op, not error."""
        minus_btn = eod._denom_minus_btns[500]
        minus_btn.click()
        assert eod._denom_counts[500] == 0


class TestFloatTotal:
    def test_counting_notes_and_coins_sums_correctly(self, eod):
        eod._change_denom_count(5000, 1)   # 1 x $50
        eod._change_denom_count(1000, 2)   # 2 x $10
        eod._change_denom_count(200, 3)    # 3 x $2
        assert eod._float_counted == pytest.approx(76.00)
        assert eod._counted_lbl.text() == "Counted: $76.00"

    def test_difference_reflects_expected_vs_counted(self, eod):
        # Opening float $100, no sales in this test_db, so expected == 100.
        eod._change_denom_count(5000, 2)   # $100 counted
        assert "Difference: +$0.00" in eod._diff_lbl.text()

    def test_subtotal_label_text_updates(self, eod):
        eod._change_denom_count(2000, 3)   # 3 x $20
        assert eod._denom_labels[2000].text() == "$60.00"


class TestSubtotalHighlight:
    def test_zero_count_subtotal_is_dim_not_bold(self, eod):
        style = eod._denom_labels[500].styleSheet()
        assert style == _SUBTOTAL_STYLE_ZERO
        assert "bold" not in style or "font-weight: normal" in style

    def test_nonzero_count_subtotal_is_bold_green(self, eod):
        eod._change_denom_count(500, 1)
        style = eod._denom_labels[500].styleSheet()
        assert style == _SUBTOTAL_STYLE_COUNTED
        assert "#4CAF50" in style
        assert "font-weight: bold" in style

    def test_reverts_to_dim_when_counted_back_to_zero(self, eod):
        eod._change_denom_count(500, 1)
        eod._change_denom_count(500, -1)
        assert eod._denom_labels[500].styleSheet() == _SUBTOTAL_STYLE_ZERO

    def test_only_counted_denominations_are_highlighted(self, eod):
        eod._change_denom_count(500, 1)
        assert eod._denom_labels[500].styleSheet() == _SUBTOTAL_STYLE_COUNTED
        assert eod._denom_labels[1000].styleSheet() == _SUBTOTAL_STYLE_ZERO
