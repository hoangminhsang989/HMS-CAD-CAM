"""Vietnamese-first Phase-1 production activation decision dialog."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class ProductionDecisionView:
    post_name: str
    machine: str
    controller: str
    current_sha256: str
    candidate_sha256: str
    current_revision: str
    candidate_revision: str
    validation_result: str
    regression_result: str
    backup_plan: str
    rollback_plan: str
    drift_status: str
    diff_summary: str


class ProductionActivationDecisionDialog(QDialog):
    """Collect an explicit Phase-1 owner choice; approval has no default."""

    def __init__(self, view: ProductionDecisionView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProductionActivationDecisionDialog")
        self.setWindowTitle("Chuẩn bị kích hoạt Post sản xuất")
        layout = QVBoxLayout(self)
        phase = QLabel("GIAI ĐOẠN 1 — CHUẨN BỊ KÍCH HOẠT")
        phase.setObjectName("ProductionActivationPhase")
        layout.addWidget(phase)
        form = QFormLayout()
        for label, value in (
            ("Tên Post", view.post_name), ("Máy", view.machine),
            ("Bộ điều khiển", view.controller), ("SHA hiện tại", view.current_sha256),
            ("SHA candidate", view.candidate_sha256), ("Revision hiện tại", view.current_revision),
            ("Revision mới", view.candidate_revision), ("Kết quả validation", view.validation_result),
            ("Kết quả regression", view.regression_result), ("Backup dự kiến", view.backup_plan),
            ("Rollback dự kiến", view.rollback_plan), ("Tình trạng thay đổi ngoài HMS", view.drift_status),
            ("Tóm tắt thay đổi chính xác", view.diff_summary),
        ):
            field = QLabel(value); field.setTextInteractionFlags(field.textInteractionFlags())
            form.addRow(label, field)
        layout.addLayout(form)
        self.decision_group = QButtonGroup(self)
        self.approve_window = QRadioButton("Phê duyệt cửa sổ kích hoạt")
        self.defer_activation = QRadioButton("Hoãn")
        self.reject_activation = QRadioButton("Từ chối")
        for button in (self.approve_window, self.defer_activation, self.reject_activation):
            self.decision_group.addButton(button); layout.addWidget(button)
        self.phase2_status = QLabel("GIAI ĐOẠN 2 — CHƯA ĐƯỢC PHÉP KÍCH HOẠT")
        self.phase2_status.setObjectName("ProductionActivationPhase2Status")
        layout.addWidget(self.phase2_status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Lưu quyết định")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.decision_group.buttonClicked.connect(lambda _button: self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True))
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_decision(self) -> str:
        if self.approve_window.isChecked():
            return "APPROVE_ACTIVATION_WINDOW"
        if self.defer_activation.isChecked():
            return "DEFER_ACTIVATION"
        if self.reject_activation.isChecked():
            return "REJECT_ACTIVATION"
        return "NOT_DECIDED"


__all__ = ["ProductionActivationDecisionDialog", "ProductionDecisionView"]
