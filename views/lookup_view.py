"""
Touchscreen product browser / lookup dialog.
Full-screen QDialog with department filter, group sub-filter, live search, and image tiles.
"""
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QScrollArea,
    QWidget, QLabel, QPushButton, QLineEdit, QGridLayout, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

from database.connection import get_connection
import api.backoffice_client as bop
from utils.format import currency

_DARK_BG = "#1a2332"
_CARD_BG = "#1e2a38"
_TILE_BG = "#243040"
_BORDER  = "#2a3a4a"
_TEXT    = "#e6edf3"
_DIM     = "#8b949e"
_GREEN   = "#4CAF50"
_BLUE    = "#2196F3"
_ORANGE  = "#FF9800"

TILE_W   = 200
TILE_H   = 245
IMG_SIZE = 148
TILE_GAP = 10
DEPT_H   = 44


class _Tile(QWidget):
    """Product image tile — tap to add to sale."""
    add_requested = pyqtSignal(dict)

    def __init__(self, product: dict, parent=None):
        super().__init__(parent)
        self._product = product
        self._hovered = False
        self.setObjectName("productTile")
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build()
        self._try_load_image()
        self._apply_style()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(2)

        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(IMG_SIZE, IMG_SIZE)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent; border: none;")
        self._show_placeholder()
        lay.addWidget(self._img_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addSpacing(1)

        desc = self._product.get('description', '')
        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)
        self._desc_lbl.setFixedHeight(42)
        self._desc_lbl.setStyleSheet(f"font-size: 13px; color: {_TEXT}; background: transparent; border: none;")
        lay.addWidget(self._desc_lbl)

        price = float(self._product.get('sell_price', 0))
        self._price_lbl = QLabel(currency(price))
        self._price_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._price_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {_GREEN}; background: transparent; border: none;"
        )
        lay.addWidget(self._price_lbl)

    def _show_placeholder(self):
        pix = QPixmap(IMG_SIZE, IMG_SIZE)
        dept  = self._product.get('dept_name') or ''
        h_val = abs(hash(dept)) % 360
        pix.fill(QColor.fromHsv(h_val, 65, 85))
        painter = QPainter(pix)
        painter.setPen(QColor(220, 220, 220))
        f = QFont()
        f.setPointSize(30)
        f.setBold(True)
        painter.setFont(f)
        initial = (self._product.get('description') or '?')[0].upper()
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, initial)
        painter.end()
        self._img_lbl.setPixmap(pix)

    def set_image(self, pix: QPixmap):
        if not pix.isNull():
            scaled = pix.scaled(
                IMG_SIZE, IMG_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)

    def _try_load_image(self):
        path = bop.get_image_path(self._product.get('barcode', ''))
        if path:
            pix = QPixmap(path)
            if not pix.isNull():
                self.set_image(pix)

    def _apply_style(self):
        bg  = "#2a3a50" if self._hovered else _TILE_BG
        brd = _BLUE     if self._hovered else _BORDER
        bw  = "2px"     if self._hovered else "1px"
        self.setStyleSheet(
            f"QWidget#productTile {{ background: {bg}; border: {bw} solid {brd}; border-radius: 10px; }}"
        )

    def enterEvent(self, e):
        self._hovered = True
        self._apply_style()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._apply_style()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.add_requested.emit(self._product)
        super().mousePressEvent(e)


class LookupView(QDialog):
    """
    Full-screen product browser.

    Parameters
    ----------
    on_add : callable(product: dict, qty: float)
        Called when a tile is tapped. Dialog stays open for multi-add.
    pending_qty : float
        Quantity to use per tap (from POS numpad ×QTY).
    """

    def __init__(self, on_add, pending_qty: float = 1.0, parent=None):
        super().__init__(parent)
        self._on_add       = on_add
        self._pending_qty  = max(1.0, float(pending_qty))
        self._dept_filter  = ''
        self._group_filter = ''
        self._search_term  = ''
        self._tile_count   = 0
        self._populated    = False

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self.setWindowTitle("Browse Products")
        self.setModal(True)
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        self._build()

    # ── Build ──────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_header())
        lay.addWidget(self._build_dept_bar())
        lay.addWidget(self._build_group_bar())
        lay.addWidget(self._build_search_bar())
        lay.addWidget(self._build_grid_area(), stretch=1)
        lay.addWidget(self._build_status_bar())
        lay.addWidget(self._build_keyboard())

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("lhBar")
        bar.setFixedHeight(56)
        bar.setStyleSheet(f"QWidget#lhBar {{ background: {_CARD_BG}; border-bottom: 1px solid {_BORDER}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)

        title = QLabel("Browse Products")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {_TEXT}; background: transparent;")
        lay.addWidget(title)
        lay.addStretch()

        self._qty_lbl = QLabel()
        self._qty_lbl.setStyleSheet(f"font-size: 13px; color: {_ORANGE}; background: transparent;")
        self._refresh_qty_label()
        lay.addWidget(self._qty_lbl)
        lay.addSpacing(24)

        done_btn = QPushButton("  ✓  Done  ")
        done_btn.setFixedHeight(38)
        done_btn.setStyleSheet(f"""
            QPushButton {{ background: {_GREEN}; color: white; border: none;
                           border-radius: 6px; font-size: 14px; font-weight: bold;
                           padding: 0 20px; }}
            QPushButton:hover {{ background: #45a049; }}
            QPushButton:pressed {{ background: #2d7031; }}
        """)
        done_btn.clicked.connect(self.accept)
        lay.addWidget(done_btn)
        return bar

    def _build_dept_bar(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("lhDeptBar")
        wrapper.setFixedHeight(DEPT_H + 14)
        wrapper.setStyleSheet(
            f"QWidget#lhDeptBar {{ background: {_CARD_BG}; border-bottom: 1px solid {_BORDER}; }}"
        )
        outer = QHBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_CARD_BG}; border: none; }}
            QScrollBar:horizontal {{ background: {_CARD_BG}; height: 4px; }}
            QScrollBar::handle:horizontal {{ background: {_BORDER}; border-radius: 2px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

        self._dept_widget = QWidget()
        self._dept_widget.setStyleSheet(f"background: {_CARD_BG};")
        self._dept_layout = QHBoxLayout(self._dept_widget)
        self._dept_layout.setContentsMargins(10, 7, 10, 7)
        self._dept_layout.setSpacing(6)
        self._dept_layout.addStretch()
        scroll.setWidget(self._dept_widget)
        outer.addWidget(scroll)
        return wrapper

    def _build_group_bar(self) -> QWidget:
        _ACCENT = "#0d47a1"   # slightly darker blue to visually distinguish from dept bar
        self._group_bar = QWidget()
        self._group_bar.setObjectName("lhGroupBar")
        self._group_bar.setFixedHeight(DEPT_H + 14)
        self._group_bar.setStyleSheet(
            f"QWidget#lhGroupBar {{ background: {_ACCENT}; border-bottom: 1px solid {_BORDER}; }}"
        )
        self._group_bar.hide()   # hidden until a department with groups is selected

        outer = QHBoxLayout(self._group_bar)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_ACCENT}; border: none; }}
            QScrollBar:horizontal {{ background: {_ACCENT}; height: 4px; }}
            QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.3); border-radius: 2px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

        self._group_widget = QWidget()
        self._group_widget.setStyleSheet(f"background: {_ACCENT};")
        self._group_layout = QHBoxLayout(self._group_widget)
        self._group_layout.setContentsMargins(10, 7, 10, 7)
        self._group_layout.setSpacing(6)
        self._group_layout.addStretch()
        scroll.setWidget(self._group_widget)
        outer.addWidget(scroll)
        return self._group_bar

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("lhSearch")
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"QWidget#lhSearch {{ background: {_DARK_BG}; }}")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 7, 16, 7)
        lay.setSpacing(8)

        icon = QLabel("🔍")
        icon.setStyleSheet("font-size: 18px; background: transparent;")
        lay.addWidget(icon)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search by name, barcode, or brand…")
        self._search_input.setFixedHeight(36)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 0 14px; font-size: 15px;
            }}
            QLineEdit:focus {{ border-color: {_BLUE}; }}
        """)
        self._search_input.textChanged.connect(self._on_search)
        lay.addWidget(self._search_input, stretch=1)
        return bar

    def _build_grid_area(self) -> QScrollArea:
        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._grid_scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_DARK_BG}; border: none; }}
            QScrollBar:vertical {{
                background: {_CARD_BG}; width: 24px; border-radius: 12px; margin: 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER}; border-radius: 10px; min-height: 60px; margin: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #3a4a5a;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self._grid_content = QWidget()
        self._grid_content.setStyleSheet(f"background: {_DARK_BG};")
        self._grid_layout = QGridLayout(self._grid_content)
        self._grid_layout.setSpacing(TILE_GAP)
        self._grid_layout.setContentsMargins(16, 16, 16, 16)
        self._grid_scroll.setWidget(self._grid_content)
        return self._grid_scroll

    def _build_status_bar(self) -> QLabel:
        self._status_lbl = QLabel("Loading…")
        self._status_lbl.setObjectName("lhStatus")
        self._status_lbl.setFixedHeight(24)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            f"QLabel#lhStatus {{ font-size: 11px; color: {_DIM}; "
            f"background: {_CARD_BG}; border-top: 1px solid {_BORDER}; }}"
        )
        return self._status_lbl

    # ── Show / populate ────────────────────────────────────────────────

    def _build_keyboard(self) -> QWidget:
        from views.widgets.keyboard import VirtualKeyboard
        self._kb = VirtualKeyboard(self)
        self._kb.done_pressed.connect(self._grid_content.setFocus)
        self._kb.close_pressed.connect(self._grid_content.setFocus)
        return self._kb

    def showEvent(self, e):
        super().showEvent(e)
        if not self._populated:
            self._populated = True
            self._kb.set_target(self._search_input)
            self._kb.hide_preview()
            self._search_input.setFocus()
            self._populate_dept_buttons()
            QTimer.singleShot(0, self._apply_filter)

    # ── Department buttons ─────────────────────────────────────────────

    def _populate_dept_buttons(self):
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT dept_name FROM product_cache "
                "WHERE active=1 AND dept_name IS NOT NULL AND dept_name != '' "
                "ORDER BY dept_name"
            ).fetchall()
            depts = [r['dept_name'] for r in rows]
        finally:
            conn.close()

        while self._dept_layout.count() > 1:
            item = self._dept_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._dept_layout.insertWidget(0, self._make_dept_btn("All", ''))
        for i, dept in enumerate(depts, start=1):
            self._dept_layout.insertWidget(i, self._make_dept_btn(dept, dept))

    def _make_dept_btn(self, label: str, dept: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(DEPT_H)
        btn.setCheckable(True)
        btn.setChecked(dept == self._dept_filter)
        self._style_dept_btn(btn, dept == self._dept_filter)
        btn.clicked.connect(lambda _, d=dept, b=btn: self._on_dept_clicked(d, b))
        return btn

    @staticmethod
    def _style_dept_btn(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_BLUE}; color: white; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 0 16px; min-width: 60px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_CARD_BG}; color: {_DIM};
                    border: 1px solid {_BORDER}; border-radius: 6px;
                    font-size: 13px; padding: 0 16px; min-width: 60px;
                }}
                QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
            """)

    def _on_dept_clicked(self, dept: str, clicked_btn: QPushButton):
        self._dept_filter  = dept
        self._group_filter = ''
        for i in range(self._dept_layout.count()):
            item = self._dept_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QPushButton):
                b = item.widget()
                active = b is clicked_btn
                b.setChecked(active)
                self._style_dept_btn(b, active)
        if dept:
            self._populate_group_bar()
        else:
            self._group_bar.hide()
        self._apply_filter()

    # ── Group buttons ──────────────────────────────────────────────────

    def _populate_group_bar(self):
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT group_name FROM product_cache "
                "WHERE active=1 AND dept_name=? AND group_name IS NOT NULL AND group_name != '' "
                "ORDER BY group_name",
                (self._dept_filter,)
            ).fetchall()
            groups = [r['group_name'] for r in rows]
        finally:
            conn.close()

        while self._group_layout.count() > 1:
            item = self._group_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not groups:
            self._group_bar.hide()
            return

        self._group_layout.insertWidget(0, self._make_group_btn(f"All {self._dept_filter}", ''))
        for i, group in enumerate(groups, start=1):
            self._group_layout.insertWidget(i, self._make_group_btn(group, group))

        self._group_bar.show()

    def _make_group_btn(self, label: str, group: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(DEPT_H)
        btn.setCheckable(True)
        btn.setChecked(group == self._group_filter)
        self._style_group_btn(btn, group == self._group_filter)
        btn.clicked.connect(lambda _, g=group, b=btn: self._on_group_clicked(g, b))
        return btn

    @staticmethod
    def _style_group_btn(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet("""
                QPushButton {
                    background: white; color: #0d47a1; border: none;
                    border-radius: 6px; font-size: 13px; font-weight: bold;
                    padding: 0 16px; min-width: 60px;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.85);
                    border: 1px solid rgba(255,255,255,0.25); border-radius: 6px;
                    font-size: 13px; padding: 0 16px; min-width: 60px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.22);
                    border-color: rgba(255,255,255,0.5);
                }
            """)

    def _on_group_clicked(self, group: str, clicked_btn: QPushButton):
        self._group_filter = group
        for i in range(self._group_layout.count()):
            item = self._group_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), QPushButton):
                b = item.widget()
                active = b is clicked_btn
                b.setChecked(active)
                self._style_group_btn(b, active)
        self._apply_filter()

    # ── Search ─────────────────────────────────────────────────────────

    def _on_search(self, text: str):
        self._search_term = text
        self._search_timer.start(250)

    # ── Product grid ───────────────────────────────────────────────────

    def _apply_filter(self):
        products = self._fetch_products()
        self._tile_count = len(products)
        self._rebuild_grid(products)
        self._set_status(f"{self._tile_count} product(s)  •  tap to add ×{self._pending_qty:g}", _DIM)

    def _fetch_products(self) -> list:
        clauses = ["active = 1"]
        params: list = []

        if self._dept_filter:
            clauses.append("dept_name = ?")
            params.append(self._dept_filter)

        if self._group_filter:
            clauses.append("group_name = ?")
            params.append(self._group_filter)

        if self._search_term.strip():
            for word in self._search_term.strip().split():
                like = f"%{word}%"
                clauses.append(
                    "(description LIKE ? OR barcode LIKE ? OR brand LIKE ? OR plu LIKE ?)"
                )
                params.extend([like, like, like, like])

        where = " AND ".join(clauses)
        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM product_cache WHERE {where} ORDER BY description LIMIT 400",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _cols(self) -> int:
        screen = QApplication.primaryScreen()
        w = screen.availableGeometry().width() if screen else 1280
        avail = w - 32 - 12
        return max(2, avail // (TILE_W + TILE_GAP))

    def _rebuild_grid(self, products: list):
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not products:
            empty = QLabel("No products found  •  try adjusting your search or department filter")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"font-size: 15px; color: {_DIM}; background: transparent;")
            self._grid_layout.addWidget(empty, 0, 0)
            return

        cols = self._cols()
        for idx, product in enumerate(products):
            tile = _Tile(product, self._grid_content)
            tile.add_requested.connect(self._on_product_tapped)
            self._grid_layout.addWidget(tile, idx // cols, idx % cols)

        self._grid_layout.setRowStretch(len(products) // cols + 1, 1)
        self._grid_layout.setColumnStretch(cols, 1)

    def _on_product_tapped(self, product: dict):
        self._on_add(product, self._pending_qty)
        self.accept()

    def _set_status(self, msg: str, color: str):
        self._status_lbl.setStyleSheet(
            f"QLabel#lhStatus {{ font-size: 11px; color: {color}; "
            f"background: {_CARD_BG}; border-top: 1px solid {_BORDER}; }}"
        )
        self._status_lbl.setText(msg)

    def _refresh_qty_label(self):
        if self._pending_qty != 1.0:
            self._qty_lbl.setText(f"QTY mode: ×{self._pending_qty:g}")
        else:
            self._qty_lbl.setText("Tap a product to add ×1")

    # ── Keyboard ───────────────────────────────────────────────────────

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.accept()
        elif (
            self.focusWidget() is not self._search_input
            and e.text()
            and e.text().isprintable()
        ):
            self._search_input.setFocus()
            self._search_input.setText(self._search_input.text() + e.text())
        else:
            super().keyPressEvent(e)
