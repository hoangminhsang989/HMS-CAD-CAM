from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox

from hms_cadcam.ui.production_activation_dialog import ProductionActivationDecisionDialog, ProductionDecisionView


def test_owner_decision_dialog_has_no_default_approval_and_phase2_is_blocked(qtbot) -> None:
    view = ProductionDecisionView("FANUC-SHL", "FANUC ROBODRILL α-D21MiB", "FANUC 31i-B", "d0aa", "1160", "fanuc-shl.original", "fanuc-shl.r233-g40", "PASS", "PASS — unexpected=0", "HMS managed immutable backup", "ROLLBACK_READY", "Không phát hiện drift", "section #56; 3 → 4; thêm G40")
    dialog = ProductionActivationDecisionDialog(view); qtbot.addWidget(dialog)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert dialog.selected_decision() == "NOT_DECIDED"
    assert not save.isEnabled()
    assert "CHƯA ĐƯỢC PHÉP KÍCH HOẠT" in dialog.phase2_status.text()
    dialog.defer_activation.click()
    assert dialog.selected_decision() == "DEFER_ACTIVATION"
    assert save.isEnabled()
