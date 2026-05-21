"""
On-screen QWERTY keyboard for touch use.

Usage
-----
    kb = VirtualKeyboard(some_parent_widget)
    kb.show_for(a_line_edit)   # positions at bottom of parent, shows
    kb.hide_keyboard()         # hide

All buttons use Qt::NoFocus so the target QLineEdit keeps focus throughout.
Listens for FocusOut on the target and auto-hides when focus moves elsewhere.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QApplication,
    QSizePolicy, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QEvent

_BG      = "#141e2b"
_KEY_BG  = "#2c3e52"
_KEY_HOV = "#3a5068"
_KEY_PRE = "#1a2a3a"
_SPEC_BG = "#1e2e40"   # shift / mode keys
_ENTER_BG= "#163820"
_DEL_BG  = "#2a1a1a"
_DONE_BG = "#1b4d23"
_BORDER  = "#3a5068"
_TEXT    = "#e6edf3"
_DIM     = "#8b949e"
_GREEN   = "#4CAF50"
_BLUE    = "#2196F3"
_ORANGE  = "#FF9800"
_RED     = "#f44336"

_KEY_H   = 54    # key height px
_GAP     = 5     # gap between keys in a row
_ROW_GAP = 5     # gap between rows
_MARGIN  = 8
_KB_H    = 44 + 4 * (_KEY_H + _ROW_GAP) + _MARGIN * 2   # ≈ 294 px

_ALPHA = [
    list('QWERTYUIOP'),
    list('ASDFGHJKL'),
    list('ZXCVBNM'),
]
_NUM = [
    list('1234567890'),
    list('@#$%&*()+'),
    list('!-/\\=_.,?'),
]


def _btn(label, *, h=_KEY_H, bg=_KEY_BG, color=_TEXT, bold=False,
         border=_BORDER, min_w=0, fixed_w=0) -> QPushButton:
    b = QPushButton(label)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setFixedHeight(h)
    if fixed_w:
        b.setFixedWidth(fixed_w)
    elif min_w:
        b.setMinimumWidth(min_w)
    b.setSizePolicy(
        QSizePolicy.Policy.Fixed if fixed_w else QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Fixed,
    )
    weight = "bold" if bold else "normal"
    b.setStyleSheet(f"""
        QPushButton {{
            background: {bg}; color: {color};
            border: 1px solid {border}; border-radius: 6px;
            font-size: 16px; font-weight: {weight};
        }}
        QPushButton:pressed {{ background: {_KEY_PRE}; border-color: {_BLUE}; }}
    """)
    return b


class _FocusWatcher(QObject):
    """Event filter that calls back when the watched widget loses focus."""
    def __init__(self, target: QLineEdit, on_focus_out, parent=None):
        super().__init__(parent)
        self._target = target
        self._cb     = on_focus_out
        target.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._target and event.type() == QEvent.Type.FocusOut:
            # Only hide if focus moved outside the keyboard tree
            self._cb()
        return False

    def remove(self):
        if self._target:
            self._target.removeEventFilter(self)
            self._target = None


class VirtualKeyboard(QWidget):
    """
    Full-width on-screen keyboard overlay.  Positioned at the bottom of its
    parent widget.  Does not steal focus from the active QLineEdit.
    """
    done_pressed   = pyqtSignal()   # Done / Enter tapped
    close_pressed  = pyqtSignal()   # ✕ tapped

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._target : QLineEdit | None = None
        self._watcher: _FocusWatcher | None = None
        self._shift  = False
        self._caps   = False
        self._mode   = 'alpha'   # 'alpha' | 'num'
        self._ignore_focus_out = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(_KB_H)
        self.setStyleSheet(
            f"VirtualKeyboard {{ background: {_BG}; border-top: 2px solid {_BORDER}; }}"
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        self._root.setSpacing(_ROW_GAP)

        self._preview = self._build_preview()
        self._root.addWidget(self._preview)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_lay = QVBoxLayout(self._rows_widget)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(_ROW_GAP)
        self._root.addWidget(self._rows_widget)

        self._build_rows()
        # Start hidden — caller decides whether to embed (always visible)
        # or use show_for() as a floating overlay
        self.hide()

    # ── Public API ─────────────────────────────────────────────────────

    def show_for(self, target: QLineEdit):
        """Attach to target, position at bottom of parent, and show (overlay mode)."""
        self._detach()
        self._target = target
        self._refresh_preview()
        target.textChanged.connect(self._refresh_preview)
        self._watcher = _FocusWatcher(target, self._on_target_focus_out, self)
        self._reposition()
        self.show()
        self.raise_()

    def set_target(self, target: QLineEdit):
        """Attach to target and show (embedded layout mode — no repositioning)."""
        self._detach()
        self._target = target
        self._refresh_preview()
        target.textChanged.connect(self._refresh_preview)
        self.show()

    def hide_preview(self):
        """Hide the preview bar — use when the target input field is already visible."""
        self._preview.hide()

    def hide_keyboard(self):
        self._detach()
        self.hide()

    # ── Internals ──────────────────────────────────────────────────────

    def _detach(self):
        if self._target:
            try:
                self._target.textChanged.disconnect(self._refresh_preview)
            except Exception:
                pass
        if self._watcher:
            self._watcher.remove()
            self._watcher = None
        self._target = None

    def _on_target_focus_out(self):
        if self._ignore_focus_out:
            return
        # Hide only if focus moved somewhere outside this keyboard
        new_focus = QApplication.focusWidget()
        if new_focus is not None and self._is_child(new_focus):
            return
        self.hide_keyboard()

    def _is_child(self, w: QWidget) -> bool:
        p = w
        while p:
            if p is self:
                return True
            p = p.parent()
        return False

    def _reposition(self):
        p = self.parent()
        if p:
            self.setGeometry(0, p.height() - _KB_H, p.width(), _KB_H)

    def resizeEvent(self, e):
        super().resizeEvent(e)

    # ── Preview bar ────────────────────────────────────────────────────

    def _build_preview(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(36)
        w.setStyleSheet(f"background: #0e1620; border-radius: 6px; border: 1px solid {_BORDER};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(8)

        self._preview_lbl = QLabel("")
        self._preview_lbl.setStyleSheet(f"font-size: 15px; color: {_TEXT}; background: transparent; border: none;")
        self._preview_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._preview_lbl)

        close_btn = _btn("✕", fixed_w=36, h=28, bg="transparent", color=_DIM, border="transparent")
        close_btn.clicked.connect(self._on_close)
        lay.addWidget(close_btn)
        return w

    def _refresh_preview(self):
        if self._target:
            text = self._target.text()
            placeholder = self._target.placeholderText()
            if text:
                self._preview_lbl.setText(text + "│")
                self._preview_lbl.setStyleSheet(
                    f"font-size: 15px; color: {_TEXT}; background: transparent; border: none;"
                )
            else:
                self._preview_lbl.setText(placeholder)
                self._preview_lbl.setStyleSheet(
                    f"font-size: 15px; color: {_DIM}; background: transparent; border: none;"
                )

    # ── Key rows ───────────────────────────────────────────────────────

    def _build_rows(self):
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = _NUM if self._mode == 'num' else _ALPHA
        for i, keys in enumerate(rows):
            self._rows_lay.addWidget(self._build_letter_row(keys, i))
        self._rows_lay.addWidget(self._build_bottom_row())

    def _build_letter_row(self, keys: list, row_idx: int) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_GAP)

        alpha = (self._mode == 'alpha')

        if row_idx == 1:
            lay.addSpacing(22)
        elif row_idx == 2:
            if alpha:
                sb = _btn("⇧", fixed_w=88, bold=True, bg=_SPEC_BG,
                          color=_ORANGE if self._shift else _DIM)
                sb.clicked.connect(self._on_shift)
                lay.addWidget(sb)
            else:
                lay.addSpacing(22)

        for key in keys:
            if alpha:
                label = key.upper() if (self._shift or self._caps) else key.lower()
            else:
                label = key
            b = _btn(label)
            b.clicked.connect(lambda _, k=key: self._on_char(k))
            lay.addWidget(b)

        if row_idx == 0:
            lay.addSpacing(6)
            del_b = _btn("⌫", fixed_w=80, bold=True, bg=_DEL_BG, color=_RED,
                         border="#5a2a2a")
            del_b.clicked.connect(self._on_backspace)
            lay.addWidget(del_b)
        elif row_idx == 1:
            lay.addSpacing(22)
            ent_b = _btn("⏎ Enter", fixed_w=110, bold=True, bg=_ENTER_BG,
                         color=_GREEN, border="#2a5a30")
            ent_b.clicked.connect(self._on_enter)
            lay.addWidget(ent_b)
        elif row_idx == 2 and alpha:
            sr = _btn("⇧", fixed_w=66, bold=True, bg=_SPEC_BG,
                      color=_ORANGE if self._shift else _DIM)
            sr.clicked.connect(self._on_shift)
            lay.addWidget(sr)

        return w

    def _build_bottom_row(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_GAP)

        lbl = "ABC" if self._mode == 'num' else "123"
        mode_b = _btn(lbl, fixed_w=80, bold=True, bg=_SPEC_BG, color=_ORANGE)
        mode_b.clicked.connect(self._on_mode)
        lay.addWidget(mode_b)

        dash_b = _btn("-", fixed_w=52)
        dash_b.clicked.connect(lambda: self._insert('-'))
        lay.addWidget(dash_b)

        sp_b = _btn("space", bg=_KEY_BG)
        sp_b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sp_b.clicked.connect(lambda: self._insert(' '))
        lay.addWidget(sp_b, stretch=1)

        dot_b = _btn(".", fixed_w=52)
        dot_b.clicked.connect(lambda: self._insert('.'))
        lay.addWidget(dot_b)

        done_b = _btn("Done ✓", fixed_w=110, bold=True, bg=_DONE_BG,
                      color=_GREEN, border="#2a6030")
        done_b.clicked.connect(self._on_done)
        lay.addWidget(done_b)

        return w

    # ── Key actions ────────────────────────────────────────────────────

    def _on_char(self, key: str):
        if self._mode == 'alpha':
            ch = key.upper() if (self._shift or self._caps) else key.lower()
            if self._shift and not self._caps:
                self._shift = False
                self._build_rows()  # redraw to reset shift highlight
        else:
            ch = key
        self._insert(ch)

    def _on_shift(self):
        if self._shift and not self._caps:
            # second tap → caps lock
            self._caps  = True
            self._shift = False
        elif self._caps:
            self._caps  = False
            self._shift = False
        else:
            self._shift = True
            self._caps  = False
        self._build_rows()

    def _on_mode(self):
        self._mode = 'num' if self._mode == 'alpha' else 'alpha'
        self._build_rows()

    def _on_backspace(self):
        if not self._target:
            return
        self._ignore_focus_out = True
        pos  = self._target.cursorPosition()
        text = self._target.text()
        if pos > 0:
            self._target.setText(text[:pos - 1] + text[pos:])
            self._target.setCursorPosition(pos - 1)
        self._ignore_focus_out = False

    def _on_enter(self):
        if self._target:
            self._target.returnPressed.emit()
        self.done_pressed.emit()

    def _on_done(self):
        if self._target:
            self._target.returnPressed.emit()
        self.done_pressed.emit()
        self.hide_keyboard()

    def _on_close(self):
        self.close_pressed.emit()
        self.hide_keyboard()

    def _insert(self, ch: str):
        if self._target:
            self._ignore_focus_out = True
            self._target.insert(ch)
            self._ignore_focus_out = False
