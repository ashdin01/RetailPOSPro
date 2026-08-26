"""Widget regression tests for CustomerDisplay (views/customer_display.py).

Requires pytest-qt (installed) and a live display (DISPLAY=:0).
"""
from views.customer_display import CustomerDisplay


def _make(qtbot, ads_dir='', idle_secs=20):
    widget = CustomerDisplay(ads_dir=ads_dir, idle_secs=idle_secs)
    qtbot.addWidget(widget)
    return widget


class TestInitialState:
    def test_starts_on_basket_page(self, qtbot):
        w = _make(qtbot)
        assert w._stack.currentWidget() is w._basket_page

    def test_idle_timer_running_from_start(self, qtbot):
        w = _make(qtbot, idle_secs=20)
        assert w._idle_timer.isActive()


class TestBasketDisplay:
    def test_show_basket_renders_items_and_total(self, qtbot):
        w = _make(qtbot)
        items = [
            {'barcode': '111', 'description': 'Milk 2L', 'qty': 2, 'unit_price': 3.50, 'tax_rate': 0},
        ]
        w.show_basket(items, subtotal=7.00, gst=0.0, total=7.00)

        assert w._stack.currentWidget() is w._basket_page
        assert "$7.00" in w._total_label.text()
        # one item row inserted before the trailing stretch
        assert w._items_layout.count() == 2

    def test_show_basket_stops_idle_timer_while_items_present(self, qtbot):
        w = _make(qtbot)
        w.show_basket(
            [{'barcode': '1', 'description': 'X', 'qty': 1, 'unit_price': 1.0}],
            subtotal=1.0, gst=0.0, total=1.0,
        )
        assert not w._idle_timer.isActive()

    def test_empty_basket_restarts_idle_timer(self, qtbot):
        w = _make(qtbot)
        w.show_basket(
            [{'barcode': '1', 'description': 'X', 'qty': 1, 'unit_price': 1.0}],
            subtotal=1.0, gst=0.0, total=1.0,
        )
        w.show_basket([], subtotal=0.0, gst=0.0, total=0.0)
        assert w._idle_timer.isActive()

    def test_bundle_discount_row_shows_negative_amount(self, qtbot):
        w = _make(qtbot)
        items = [{
            'barcode': 'BUNDLE-DISCOUNT', 'description': '2 for $5 deal',
            'is_bundle_discount': True, 'line_total': 1.50,
        }]
        w.show_basket(items, subtotal=3.50, gst=0.0, total=3.50)
        row = w._items_layout.itemAt(0).widget()
        assert "−$1.50" in row.text()


class TestAdSlideshow:
    def test_enter_ad_mode_switches_stack(self, qtbot, tmp_path):
        w = _make(qtbot, ads_dir=str(tmp_path))
        w._enter_ad_mode()
        assert w._stack.currentWidget() is w._ad_page

    def test_no_ads_dir_does_not_crash_and_schedules_retry(self, qtbot):
        w = _make(qtbot, ads_dir='')
        w._enter_ad_mode()
        assert w._ad_files == []
        assert w._ad_advance_timer.isActive()

    def test_loads_only_recognised_media_files(self, qtbot, tmp_path):
        (tmp_path / "promo1.jpg").write_bytes(b"fake")
        (tmp_path / "promo2.png").write_bytes(b"fake")
        (tmp_path / "clip.mp4").write_bytes(b"fake")
        (tmp_path / "notes.txt").write_bytes(b"fake")
        w = _make(qtbot, ads_dir=str(tmp_path))

        w._load_ad_files()

        assert w._ad_files == ["clip.mp4", "promo1.jpg", "promo2.png"]

    def test_show_next_ad_cycles_through_files(self, qtbot, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"fake")
        (tmp_path / "b.jpg").write_bytes(b"fake")
        w = _make(qtbot, ads_dir=str(tmp_path))
        w._load_ad_files()

        w._show_next_ad()
        first = w._ad_files[w._ad_index]
        w._show_next_ad()
        second = w._ad_files[w._ad_index]
        w._show_next_ad()
        third = w._ad_files[w._ad_index]

        assert first != second
        assert third == first  # wraps back around after 2 files

    def test_returning_to_basket_stops_ad_playback(self, qtbot, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"fake")
        w = _make(qtbot, ads_dir=str(tmp_path))
        w._enter_ad_mode()
        assert w._ad_advance_timer.isActive()

        w.show_basket(
            [{'barcode': '1', 'description': 'X', 'qty': 1, 'unit_price': 1.0}],
            subtotal=1.0, gst=0.0, total=1.0,
        )
        assert not w._ad_advance_timer.isActive()
        assert w._stack.currentWidget() is w._basket_page
