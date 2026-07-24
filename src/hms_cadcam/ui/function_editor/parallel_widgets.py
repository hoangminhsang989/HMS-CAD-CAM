"""Focused Qt widgets for Parallel progress and structured safety diagnostics."""

from __future__ import annotations

import math

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.cam3d.parallel import ParallelProgress, ParallelSafetyReport
from hms_cadcam.cam.cam3d.zlevel import ZLevelProgress
from hms_cadcam.cam.domain import ValidationDiagnostic
from hms_cadcam.ui.localization import (
    display_value,
    translate_progress_phase,
    translate_status,
    ui_text,
)
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY, CAMPopupMetrics


class ParallelCalculationProgressWidget(QWidget):
    """Non-modal, cancellable phase summary for the active editor page."""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ParallelCalculationProgress")
        self.setAccessibleName("Tiến độ tính toán Gia công tinh song song")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(9, 7, 9, 7)
        self._layout.setSpacing(5)
        row = QHBoxLayout()
        self.phase = QLabel("Giai đoạn: Kiểm tra hợp lệ")
        self.phase.setObjectName("ParallelProgressPhase")
        row.addWidget(self.phase, 1)
        self.percentage = QLabel("Tổng thể: 0%")
        self.percentage.setObjectName("ParallelProgressPercentage")
        row.addWidget(self.percentage)
        self.cancel_button = QPushButton("Hủy tính toán")
        self.cancel_button.setAccessibleName(
            "Hủy tính toán Gia công tinh song song"
        )
        self.cancel_button.clicked.connect(self.cancel_requested)
        row.addWidget(self.cancel_button)
        self._layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self._layout.addWidget(self.progress)
        self.detail = QLabel("Hạng mục: 0 / 0")
        self.detail.setObjectName("ParallelProgressDetail")
        self._layout.addWidget(self.detail)
        self.setVisible(False)

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        """Keep progress legible without consuming the scrolling content area."""
        self._layout.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.content_margin,
            metrics.row_spacing,
        )
        self._layout.setSpacing(metrics.row_spacing)
        self.setMinimumHeight(metrics.toolbar_height * 3)
        self.cancel_button.setMinimumHeight(metrics.button_height)

    def set_active(self, active: bool) -> None:
        """Show a deterministic initial state or hide after worker cleanup."""
        self.setVisible(active)
        self.cancel_button.setEnabled(active)
        if active:
            self.phase.setText("Giai đoạn: Kiểm tra hợp lệ")
            self.percentage.setText("Tổng thể: 0%")
            self.progress.setValue(0)
            self.detail.setText(
                "Hạng mục: 0 / 0 · Đang chuẩn bị ảnh chụp trạng thái đã áp dụng bất biến"
            )

    def update_progress(self, value: ParallelProgress | ZLevelProgress) -> None:
        """Render one monotonic worker report with text, not color alone."""
        if not isinstance(value, (ParallelProgress, ZLevelProgress)):
            return
        label = translate_progress_phase(value.phase)
        self.phase.setText(f"Giai đoạn: {label}")
        self.percentage.setText(f"Tổng thể: {value.percentage:.0f}%")
        self.progress.setValue(round(value.percentage))
        self.detail.setText(
            f"Hạng mục: {value.processed} / {value.total}"
        )
        self.setVisible(True)


class ParallelDirectionPreviewWidget(QWidget):
    """Session-only U/V/W direction overlay with no CAD or OCP ownership."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ParallelDirectionPreview")
        self.setAccessibleName("Xem trước hướng U V W của Gia công tinh song song")
        self.setAccessibleDescription(
            "U là hướng lượt cắt, V là hướng bước ngang và W là trục Tool"
        )
        self._angle_degrees = 0.0
        self.setFixedHeight(72)

    def set_angle(self, value: object) -> None:
        """Update a finite draft angle without starting a calculation."""
        try:
            angle = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if not math.isfinite(angle):
            return
        self._angle_degrees = angle % 360.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#edf3f7"))
        origin_x = max(72, self.width() // 2)
        origin_y = self.height() // 2 + 8
        radius = min(92, max(42, self.width() // 5))
        vertical_radius = min(24, max(16, self.height() // 3))
        angle = math.radians(-self._angle_degrees)
        ux, uy = math.cos(angle), math.sin(angle)
        vx, vy = -uy, ux
        axes = (
            (ux, uy, QColor("#c63d4f"), "U · lượt cắt"),
            (vx, vy, QColor("#288c51"), "V · bước ngang"),
            (0.55, -0.78, QColor("#356fbd"), "W · trục Tool"),
        )
        for x_value, y_value, color, label in axes:
            end_x = origin_x + x_value * radius
            end_y = origin_y + y_value * vertical_radius
            painter.setPen(QPen(color, 3))
            painter.drawLine(origin_x, origin_y, int(end_x), int(end_y))
            painter.drawText(int(end_x + 5), int(end_y - 3), label)
        painter.setPen(QColor("#314657"))
        painter.drawText(8, 16, f"Xem trước hướng · {self._angle_degrees:g}°")
        painter.end()


class ParallelSafetyDiagnosticsDialog(QDialog):
    """Structured diagnostic table; raw exceptions/JSON never enter the view."""

    def __init__(
        self,
        report: ParallelSafetyReport | None,
        operation_diagnostics: tuple[ValidationDiagnostic, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ParallelSafetyDiagnosticsDialog")
        self.setWindowTitle("Chi tiết an toàn Gia công tinh song song")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._root = QVBoxLayout(self)
        status = "Chưa tính toán" if report is None else translate_status(
            report.status.value.upper()
        )
        scope = (
            "không khả dụng"
            if report is None
            else display_value(report.safety_scope, "safety_scope")
        )
        self.summary = QLabel(
            f"Trạng thái an toàn: {status} · Phạm vi: {scope} · "
            "Khoảng hở sẵn sàng cho máy: Chưa xác minh"
        )
        self.summary.setWordWrap(True)
        if report is not None:
            self.summary.setToolTip(
                f"Mã phạm vi nội bộ: {report.safety_scope}"
            )
        self._root.addWidget(self.summary)
        headers = (
            "Mã",
            "Mức độ",
            "Lượt cắt",
            "Đoạn",
            "Chuyển động",
            "Thành phần",
            "Hình học",
            "Khoảng cách gần nhất",
            "Độ xuyên",
            "Số lần xuất hiện",
            "Thông báo",
        )
        self.table = QTableWidget(0, len(headers))
        self.table.setObjectName("ParallelSafetyDiagnosticsTable")
        self.table.setHorizontalHeaderLabels(headers)
        for column, text in enumerate(headers):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setToolTip(text)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setWordWrap(True)
        self.table.setSortingEnabled(False)
        rows = self._rows(report, operation_diagnostics)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row_index, column, item)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(72)
        header.setStretchLastSection(False)
        fixed_widths = {
            1: 76,
            2: 72,
            3: 80,
            4: 72,
            5: 106,
            7: 132,
            8: 112,
            9: 126,
        }
        for column, width in fixed_widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            text_width = header.fontMetrics().horizontalAdvance(headers[column]) + 20
            self.table.setColumnWidth(column, max(width, text_width))
        for column in (0, 6, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        vertical = self.table.verticalHeader()
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.copy_action = QAction("Sao chép ô đã chọn", self.table)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.triggered.connect(self.copy_selected_value)
        self.table.addAction(self.copy_action)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.table.resizeRowsToContents()
        self._root.addWidget(self.table, 1)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self._root.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)
        defaults = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
        self.apply_density(defaults, QRect(0, 0, 1600, 900))

    def apply_density(self, metrics: CAMPopupMetrics, available: QRect) -> None:
        """Keep the wide diagnostics table within the active work area."""
        self._root.setContentsMargins(*(metrics.child_margin,) * 4)
        self._root.setSpacing(metrics.row_spacing)
        self.setMinimumSize(
            min(720, available.width()), min(360, available.height())
        )
        self.setMaximumSize(available.size())
        width = min(metrics.diagnostics_size.width(), available.width())
        height = min(metrics.diagnostics_size.height(), available.height())
        self.resize(width, height)
        self.table.verticalHeader().setMinimumSectionSize(metrics.table_row_height)
        self.close_button.setMinimumHeight(metrics.button_height)

    def copy_selected_value(self) -> bool:
        """Copy the complete current cell, including long geometry identities."""
        item = self.table.currentItem()
        if item is None:
            return False
        QApplication.clipboard().setText(item.text())
        return True

    @staticmethod
    def _rows(
        report: ParallelSafetyReport | None,
        diagnostics: tuple[ValidationDiagnostic, ...],
    ) -> tuple[tuple[str, ...], ...]:
        if report is not None:
            return tuple(
                (
                    item.code.value,
                    translate_status(item.severity.value.upper()),
                    "" if item.pass_index is None else str(item.pass_index),
                    "" if item.segment_index is None else str(item.segment_index),
                    "" if item.motion_index is None else str(item.motion_index),
                    (
                        ""
                        if item.tool_component is None
                        else display_value(item.tool_component, "safety_component")
                    ),
                    (
                        display_value(item.geometry_source, "geometry_source")
                        + (
                            " · "
                            f"{display_value('geometry_reference', 'geometry_source')}:"
                            f"{item.face_id.value}"
                            if item.face_id is not None
                            else ""
                        )
                    ),
                    "" if item.closest_distance_mm is None else f"{item.closest_distance_mm:g}",
                    "" if item.penetration_depth_mm is None else f"{item.penetration_depth_mm:g}",
                    str(item.occurrence_count),
                    ui_text(item.message),
                )
                for item in report.diagnostics
            )
        rows = []
        for item in diagnostics:
            evidence = dict(item.context)
            if not item.code.value.startswith("parallel"):
                continue
            rows.append(
                (
                    item.code.value,
                    translate_status(item.severity.value.upper()),
                    evidence.get("pass_index", ""),
                    evidence.get("segment_index", ""),
                    evidence.get("motion_index", ""),
                    display_value(
                        evidence.get("tool_component", ""), "safety_component"
                    ),
                    display_value(
                        evidence.get("geometry_source", ""), "geometry_source"
                    ),
                    evidence.get("closest_distance_mm", ""),
                    evidence.get("penetration_depth_mm", ""),
                    evidence.get("occurrence_count", "1"),
                    ui_text(item.message),
                )
            )
        return tuple(rows)


__all__ = [
    "ParallelCalculationProgressWidget",
    "ParallelDirectionPreviewWidget",
    "ParallelSafetyDiagnosticsDialog",
]
