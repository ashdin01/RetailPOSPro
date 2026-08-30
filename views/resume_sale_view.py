"""
Full-screen dialog: browse open held sales and resume one, or scan its ticket.

Modeled on views/lookup_view.py's full-screen QDialog shape. Open holds are
fetched from BackOfficePro (the cross-terminal source of truth for holds) on
show, via a background thread + signal marshalling back to the GUI thread —
same pattern POSScreen uses for _check_health.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal
import threading

import api.backoffice_client as bop
from utils.format import currency

_DARK_BG = "#1a2332"
_CARD_BG = "#1e2a38"
_BORDER  = "#2a3a4a"
_TEXT    = "#e6edf3"
_DIM     = "#8b949e"
_GREEN   = "#4CAF50"
_BLUE    = "#2196F3"
_RED     = "#f44336"


class ResumeSaleView(QDialog):
    _holds_loaded = pyqtSignal(object)   # list | None -> _on_holds_loaded

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resumed_hold: dict | None = None
        self._terminal_id = parent._terminal_id() if parent else 'POS-001'
        self._holds_loaded.connect(self._on_holds_loaded)
        self.setWindowTitle("Resume Sale")
        self.setModal(True)
        self.setStyleSheet(f"QDialog, QWidget {{ background: {_DARK_BG}; color: {_TEXT}; }}")
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Resume Sale")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("✕ Close")
        close_btn.setFixedHeight(36)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_DIM};
                           border: 1px solid {_BORDER}; border-radius: 4px;
                           font-size: 13px; padding: 0 14px; }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_DIM}; }}
        """)
        close_btn.clicked.connect(self.reject)
        header.addWidget(close_btn)
        lay.addLayout(header)

        self._scan_edit = QLineEdit()
        self._scan_edit.setPlaceholderText("Scan hold ticket, or tap a row below…")
        self._scan_edit.setFixedHeight(44)
        self._scan_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {_CARD_BG}; color: {_TEXT};
                border: 2px solid {_BORDER}; border-radius: 8px;
                padding: 0 14px; font-size: 16px;
            }}
            QLineEdit:focus {{ border-color: {_BLUE}; }}
        """)
        self._scan_edit.returnPressed.connect(self._on_scan)
        lay.addWidget(self._scan_edit)

        self._status_lbl = QLabel("Loading open holds…")
        self._status_lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        lay.addWidget(self._status_lbl)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{ background: {_CARD_BG}; color: {_TEXT};
                           border: 1px solid {_BORDER}; border-radius: 8px;
                           font-size: 14px; }}
            QListWidget::item {{ padding: 10px; border-bottom: 1px solid {_BORDER}; }}
            QListWidget::item:selected {{ background: #1e4080; color: {_TEXT}; }}
        """)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        lay.addWidget(self._list, stretch=1)

    def showEvent(self, e):
        super().showEvent(e)
        self._scan_edit.setFocus()
        self._refresh()

    def _refresh(self):
        self._status_lbl.setText("Loading open holds…")
        def _run():
            holds = bop.list_open_holds()
            self._holds_loaded.emit(holds)
        threading.Thread(target=_run, daemon=True).start()

    def _on_holds_loaded(self, holds):
        self._list.clear()
        if holds is None:
            self._status_lbl.setText("Could not reach BackOfficePro — try again.")
            self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 13px;")
            return
        if not holds:
            self._status_lbl.setText("No open holds.")
            self._status_lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
            return
        self._status_lbl.setText(f"{len(holds)} open hold(s)")
        self._status_lbl.setStyleSheet(f"color: {_DIM}; font-size: 13px;")
        for h in holds:
            label = (f"{h['reference']}   {h['terminal_id']}   {h['operator']}   "
                     f"{h['item_count']} item(s)   {currency(h['total'])}   {h['created_at']}")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, h['reference'])
            self._list.addItem(item)

    def _on_scan(self):
        ref = self._scan_edit.text().strip()
        self._scan_edit.clear()
        if ref:
            self._resume(ref.upper())

    def _on_item_activated(self, item: QListWidgetItem):
        self._resume(item.data(Qt.ItemDataRole.UserRole))

    def _resume(self, reference: str):
        held, error = bop.resume_hold(reference, self._terminal_id)
        if error == 'NOT_FOUND':
            self._status_lbl.setText(f"'{reference}' not found — check the ticket and try again.")
            self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 13px;")
            return
        if error == 'INVALID_STATUS':
            self._status_lbl.setText(f"'{reference}' was already resumed or voided elsewhere.")
            self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 13px;")
            self._refresh()
            return
        if error or not held:
            self._status_lbl.setText("Could not reach BackOfficePro — try again.")
            self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 13px;")
            return
        self.resumed_hold = held
        self.accept()
