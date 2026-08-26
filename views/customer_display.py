"""
Customer-facing display — shown fullscreen on a second monitor.

Mirrors the running basket (items + price as they're scanned) while a sale
is in progress. When the basket is empty for `idle_secs`, it switches to a
looping slideshow of images/video from a configurable ads folder so the
screen advertises rather than sitting blank between customers.
"""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame,
    QStackedWidget,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap

from utils.format import currency

_VIDEO_EXTS = {'.mp4', '.mov', '.webm', '.avi', '.mkv'}
_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

_IMAGE_DISPLAY_MS = 8000
_VIDEO_MAX_MS = 120_000  # safety cap if a video never reports end-of-media
_NO_ADS_RETRY_MS = 30_000

_BG = "#0d1b2a"
_TEXT = "#e6edf3"
_GREEN = "#4caf50"


class CustomerDisplay(QMainWindow):
    def __init__(self, ads_dir: str = '', idle_secs: int = 20, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customer Display")
        self.setStyleSheet(f"QMainWindow {{ background: {_BG}; }}")

        self._ads_dir = ads_dir or ''
        self._idle_secs = max(5, idle_secs)
        self._ad_files: list[str] = []
        self._ad_index = -1
        self._media_player = None
        self._video_widget = None

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._basket_page = self._build_basket_page()
        self._ad_page = self._build_ad_page()
        self._stack.addWidget(self._basket_page)
        self._stack.addWidget(self._ad_page)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._enter_ad_mode)

        self._ad_advance_timer = QTimer(self)
        self._ad_advance_timer.setSingleShot(True)
        self._ad_advance_timer.timeout.connect(self._show_next_ad)

        # Starts idle (empty basket) until the POS pushes the first update.
        self._idle_timer.start(self._idle_secs * 1000)

    # ── UI construction ─────────────────────────────────────────────────

    def _build_basket_page(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(f"background: {_BG};")
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.setSpacing(16)

        header = QLabel("Your Order")
        header.setStyleSheet(f"font-size: 34px; font-weight: bold; color: {_TEXT};")
        outer.addWidget(header)

        self._items_layout = QVBoxLayout()
        self._items_layout.setSpacing(8)
        self._items_layout.addStretch()
        items_container = QWidget()
        items_container.setStyleSheet("background: transparent;")
        items_container.setLayout(self._items_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(items_container)
        outer.addWidget(scroll, stretch=1)

        self._empty_label = QLabel("Welcome!")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(f"font-size: 28px; color: {_TEXT};")

        self._total_label = QLabel(currency(0))
        self._total_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._total_label.setStyleSheet(f"font-size: 52px; font-weight: bold; color: {_GREEN};")
        outer.addWidget(self._total_label)

        return widget

    def _build_ad_page(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background: black;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self._ad_stack = QStackedWidget()
        self._ad_image_label = QLabel()
        self._ad_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ad_image_label.setStyleSheet("background: black;")
        self._ad_stack.addWidget(self._ad_image_label)
        layout.addWidget(self._ad_stack)

        return widget

    def _ensure_video_widget(self):
        """Create the QMediaPlayer/QVideoWidget lazily so environments
        without a working multimedia backend only fail when a video is
        actually about to play, not at startup."""
        if self._video_widget is not None:
            return
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PyQt6.QtMultimediaWidgets import QVideoWidget

        self._video_widget = QVideoWidget()
        self._ad_stack.addWidget(self._video_widget)

        self._media_player = QMediaPlayer(self)
        audio_out = QAudioOutput(self)
        audio_out.setMuted(True)
        self._media_player.setAudioOutput(audio_out)
        self._media_player.setVideoOutput(self._video_widget)
        self._media_player.mediaStatusChanged.connect(self._on_media_status)

    # ── Basket updates (pushed from POSScreen) ──────────────────────────

    def show_basket(self, items: list, subtotal: float, gst: float, total: float):
        while self._items_layout.count() > 1:  # leave the trailing stretch
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not items:
            self._items_layout.insertWidget(0, self._empty_label)
        else:
            for it in items:
                if it.get('is_bundle_discount'):
                    label_text = it.get('description', 'Discount')
                    amount_text = f"−{currency(abs(it.get('line_total', 0.0)))}"
                else:
                    qty = it.get('qty', 1)
                    label_text = f"{qty:g} × {it.get('description', '')}"
                    amount_text = currency(qty * it.get('unit_price', 0.0))
                row = QLabel(f"{label_text}    {amount_text}")
                row.setStyleSheet(f"font-size: 22px; color: {_TEXT};")
                self._items_layout.insertWidget(self._items_layout.count() - 1, row)

        self._total_label.setText(currency(total))

        if items:
            self._enter_basket_mode()
        else:
            self._idle_timer.start(self._idle_secs * 1000)

    # ── Mode switching ───────────────────────────────────────────────────

    def _enter_basket_mode(self):
        self._idle_timer.stop()
        self._stop_ad_playback()
        self._stack.setCurrentWidget(self._basket_page)

    def _enter_ad_mode(self):
        self._stack.setCurrentWidget(self._ad_page)
        self._load_ad_files()
        self._show_next_ad()

    def _stop_ad_playback(self):
        self._ad_advance_timer.stop()
        if self._media_player is not None:
            self._media_player.stop()

    # ── Ad slideshow ──────────────────────────────────────────────────────

    def _load_ad_files(self):
        if not self._ads_dir or not os.path.isdir(self._ads_dir):
            self._ad_files = []
            return
        exts = _IMAGE_EXTS | _VIDEO_EXTS
        self._ad_files = sorted(
            f for f in os.listdir(self._ads_dir)
            if os.path.splitext(f)[1].lower() in exts
        )

    def _show_next_ad(self):
        if not self._ad_files:
            self._ad_image_label.setPixmap(QPixmap())
            self._ad_stack.setCurrentWidget(self._ad_image_label)
            self._ad_advance_timer.start(_NO_ADS_RETRY_MS)
            return

        self._ad_index = (self._ad_index + 1) % len(self._ad_files)
        path = os.path.join(self._ads_dir, self._ad_files[self._ad_index])
        ext = os.path.splitext(path)[1].lower()
        if ext in _VIDEO_EXTS:
            self._play_video(path)
        else:
            self._show_image(path)

    def _show_image(self, path: str):
        if self._media_player is not None:
            self._media_player.stop()
        self._ad_stack.setCurrentWidget(self._ad_image_label)
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            target = self._ad_image_label.size()
            if target.width() <= 0 or target.height() <= 0:
                target = self.size()
            pixmap = pixmap.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._ad_image_label.setPixmap(pixmap)
        self._ad_advance_timer.start(_IMAGE_DISPLAY_MS)

    def _play_video(self, path: str):
        try:
            self._ensure_video_widget()
        except Exception:
            # No usable multimedia backend on this machine — skip this file.
            self._show_next_ad()
            return
        from PyQt6.QtCore import QUrl
        self._ad_stack.setCurrentWidget(self._video_widget)
        self._media_player.setSource(QUrl.fromLocalFile(path))
        self._media_player.play()
        self._ad_advance_timer.start(_VIDEO_MAX_MS)  # safety net

    def _on_media_status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._show_next_ad()
