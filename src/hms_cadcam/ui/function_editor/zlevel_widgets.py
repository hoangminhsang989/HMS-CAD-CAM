"""Focused Qt diagnostics for the Z-Level production editor."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.cam3d.zlevel import ZLevelSafetyReport
from hms_cadcam.cam.domain import ValidationDiagnostic
from hms_cadcam.ui.localization import translate_status, ui_text
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY, CAMPopupMetrics


class ZLevelSafetyDiagnosticsDialog(QDialog):
    """Structured Vietnamese safety details without exception or JSON dumps."""

    def __init__(
        self,
        report: ZLevelSafetyReport | None,
        operation_diagnostics: tuple[ValidationDiagnostic, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ZLevelSafetyDiagnosticsDialog")
        self.setWindowTitle(
            ui_text("Chi tiết safety · Gia công tinh theo cao độ Z")
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._root = QVBoxLayout(self)
        status = (
            "Chưa tính"
            if report is None
            else translate_status(report.status.value.upper())
        )
        scope = (
            "Chưa có báo cáo"
            if report is None
            else f"{len(report.safety_scope)} mục phạm vi"
        )
        self.summary = QLabel(
            ui_text(
                f"Trạng thái safety: {status} · Phạm vi: {scope} · "
                "Khoảng hở machine-ready: Chưa xác minh"
            )
        )
        self.summary.setWordWrap(True)
        self._root.addWidget(self.summary)
        headers = ("Mã chẩn đoán", "Mức độ", "Thông báo")
        self.table = QTableWidget(0, len(headers))
        self.table.setObjectName("ZLevelSafetyDiagnosticsTable")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        rows = self._rows(report, operation_diagnostics)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row_index, column, item)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeRowsToContents()
        self._root.addWidget(self.table, 1)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self._root.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.apply_density(
            CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900)),
            QRect(0, 0, 1600, 900),
        )

    @staticmethod
    def _rows(
        report: ZLevelSafetyReport | None,
        diagnostics: tuple[ValidationDiagnostic, ...],
    ) -> tuple[tuple[str, str, str], ...]:
        if report is not None:
            return tuple(
                (
                    item.code.value,
                    translate_status(item.severity.value.upper()),
                    ui_text(item.message),
                )
                for item in report.diagnostics
            )
        return tuple(
            (
                item.code.value,
                translate_status(item.severity.value.upper()),
                ui_text(item.message),
            )
            for item in diagnostics
            if item.code.value.startswith("z_level.")
        )

    def apply_density(self, metrics: CAMPopupMetrics, available: QRect) -> None:
        """Keep the child popup inside the active monitor work area."""
        self._root.setContentsMargins(*(metrics.child_margin,) * 4)
        self._root.setSpacing(metrics.row_spacing)
        self.setMaximumSize(available.size())
        self.resize(
            min(metrics.diagnostics_size.width(), available.width()),
            min(metrics.diagnostics_size.height(), available.height()),
        )
        self.close_button.setMinimumHeight(metrics.button_height)


__all__ = ["ZLevelSafetyDiagnosticsDialog"]
