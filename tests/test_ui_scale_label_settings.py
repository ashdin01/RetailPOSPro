"""ScaleSettingsDialog: scale-label layout presets, live test field, save/load."""
from PyQt6.QtWidgets import QMessageBox

from views.settings_screen import ScaleSettingsDialog, _load_settings


def test_preset_fills_fields_and_test_decodes(qtbot, test_db, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg = ScaleSettingsDialog()
    qtbot.addWidget(dlg)
    dlg._label_preset_combo.setCurrentIndex(dlg._label_preset_combo.findData('2 PPPPPP VVVVV C'))
    dlg._label_type_combo.setCurrentIndex(dlg._label_type_combo.findData('price'))
    assert dlg._label_edits['scale_label_plu_len'].text() == '6'
    dlg._label_test_edit.setText('2000140010004')
    assert 'PLU 140' in dlg._label_test_result.text()
    assert '$10.00' in dlg._label_test_result.text()
    dlg._save()
    saved = _load_settings()
    assert saved['scale_label_value_type'] == 'price'
    assert saved['scale_label_plu_len'] == '6'

    dlg2 = ScaleSettingsDialog()
    qtbot.addWidget(dlg2)
    assert dlg2._label_preset_combo.currentData() == '2 PPPPPP VVVVV C'
    assert dlg2._label_type_combo.currentData() == 'price'
