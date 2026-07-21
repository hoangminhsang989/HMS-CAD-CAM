"""Panel hosts that recompose existing Stage 9A.2 workflow widgets."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.operation_manager import OperationManagerPanel


class OperationManagerHost(OperationManagerPanel):
    """Compatibility name for the production Stage 9A.3 panel."""


class FunctionEditorHost(QWidget):
    """Sticky summary/footer and internal scroll for the existing editor."""

    collapse_requested = Signal()

    def __init__(
        self,
        editor: QWidget,
        tree: QTreeWidget,
        apply_callback: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FunctionEditorHost")
        self.setAccessibleName("Function Editor")
        self.editor = editor
        self._tree = tree

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header, self.collapse_button = _panel_header(
            "Function Editor", "Thu gọn Function Editor"
        )
        self.collapse_button.clicked.connect(self.collapse_requested)
        root.addWidget(header)

        summary_frame = QFrame()
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        summary_layout.setSpacing(2)
        self.selection_summary = QLabel("Chưa chọn operation")
        self.selection_summary.setObjectName("PanelTitle")
        self.selection_summary.setWordWrap(True)
        self.state_summary = QLabel("Chọn một node trong Operation Manager để chỉnh sửa.")
        self.state_summary.setObjectName("PanelSummary")
        self.state_summary.setWordWrap(True)
        summary_layout.addWidget(self.selection_summary)
        summary_layout.addWidget(self.state_summary)
        root.addWidget(summary_frame)

        form = editor.layout()
        if isinstance(form, QFormLayout):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            classic_apply = getattr(editor, "apply_button", None)
            if isinstance(classic_apply, QWidget):
                form.setRowVisible(classic_apply, False)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FunctionEditorScrollArea")
        self.scroll_area.setAccessibleName("Nội dung Function Editor")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setWidget(editor)
        root.addWidget(self.scroll_area, 1)

        footer = QFrame()
        footer.setObjectName("FunctionEditorFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        footer_layout.addStretch(1)
        self.apply_button = QPushButton("Áp dụng")
        self.apply_button.setObjectName("PrimaryPanelAction")
        self.apply_button.setAccessibleName("Áp dụng bản nháp operation")
        self.apply_button.setToolTip("Kiểm tra và áp dụng bản nháp hiện tại")
        self.apply_button.clicked.connect(apply_callback)
        footer_layout.addWidget(self.apply_button)
        self.close_button = QPushButton("Đóng")
        self.close_button.setAccessibleName("Đóng Function Editor")
        self.close_button.setToolTip("Ẩn Function Editor; có thể mở lại từ menu Hiển thị")
        self.close_button.clicked.connect(self.collapse_requested)
        footer_layout.addWidget(self.close_button)
        root.addWidget(footer)

        self._tree.itemSelectionChanged.connect(self.refresh_summary)
        self.refresh_summary()

    def refresh_summary(self) -> None:
        """Reflect the current typed tree selection without caching identity."""
        item = self._tree.currentItem()
        if item is None:
            self.selection_summary.setText("Chưa chọn operation")
            self.state_summary.setText(
                "Chọn một node trong Operation Manager để chỉnh sửa."
            )
            return
        self.selection_summary.setText(item.text(0) or "Selection")
        status = item.text(1).strip() or "Không có trạng thái"
        self.state_summary.setText(f"Trạng thái: {status}")


class DiagnosticsHost(QWidget):
    """Compact activity summary with an independently scrolling log."""

    collapse_requested = Signal()

    def __init__(
        self,
        output: QPlainTextEdit,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DiagnosticsHost")
        self.setAccessibleName("Diagnostics và tác vụ nền")
        self.output = output
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header, self.collapse_button = _panel_header(
            "Diagnostics & Activity", "Thu gọn Diagnostics"
        )
        self.collapse_button.clicked.connect(self.collapse_requested)
        root.addWidget(header)
        self.activity_label = QLabel("INFO · Không có tác vụ nền đang chạy")
        self.activity_label.setObjectName("DiagnosticSeverityInfo")
        self.activity_label.setAccessibleName("Trạng thái tác vụ nền")
        self.activity_label.setContentsMargins(8, 3, 8, 3)
        root.addWidget(self.activity_label)
        self.output.setAccessibleName("Nhật ký chẩn đoán HMS")
        root.addWidget(self.output, 1)

    def set_activity(self, text: str, *, severity: str = "info") -> None:
        """Update the visible worker/activity summary without touching selection."""
        normalized = severity.strip().lower()
        object_name = {
            "warning": "DiagnosticSeverityWarning",
            "error": "DiagnosticSeverityError",
        }.get(normalized, "DiagnosticSeverityInfo")
        prefix = {"warning": "CẢNH BÁO", "error": "LỖI"}.get(
            normalized, "INFO"
        )
        self.activity_label.setObjectName(object_name)
        self.activity_label.setText(f"{prefix} · {text}")
        self.activity_label.style().unpolish(self.activity_label)
        self.activity_label.style().polish(self.activity_label)


class SecondaryPanelHost(QWidget):
    """Closable scroll host for Simulation and Post/Program Assembly."""

    collapse_requested = Signal()

    def __init__(
        self,
        simulation_panel: QWidget,
        post_tabs: QTabWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SecondaryPanelHost")
        self.setAccessibleName("Panel quy trình phụ")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header, self.collapse_button = _panel_header(
            "Simulation / Post", "Thu gọn panel quy trình phụ"
        )
        self.collapse_button.clicked.connect(self.collapse_requested)
        root.addWidget(header)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SecondaryWorkflowTabs")
        self.tabs.setAccessibleName("Chọn Simulation hoặc Post")
        self.simulation_scroll = _scroll_host(simulation_panel, "SimulationScrollArea")
        self.post_scroll = _scroll_host(post_tabs, "PostScrollArea")
        self.tabs.addTab(self.simulation_scroll, "Simulation")
        self.tabs.addTab(self.post_scroll, "Post / Program Assembly")
        root.addWidget(self.tabs, 1)

    def select_simulation(self) -> None:
        """Select the existing Simulation panel without starting a run."""
        self.tabs.setCurrentWidget(self.simulation_scroll)

    def select_post(self) -> None:
        """Select the existing Post panel without generating or exporting."""
        self.tabs.setCurrentWidget(self.post_scroll)


def _panel_header(title: str, collapse_accessible_name: str) -> tuple[QFrame, QToolButton]:
    frame = QFrame()
    frame.setObjectName("PanelHeader")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(8, 5, 5, 5)
    label = QLabel(title)
    label.setObjectName("PanelTitle")
    layout.addWidget(label)
    layout.addStretch(1)
    button = QToolButton()
    button.setText("×")
    button.setAccessibleName(collapse_accessible_name)
    button.setToolTip(collapse_accessible_name)
    button.setAutoRaise(True)
    layout.addWidget(button)
    return frame, button


def _scroll_host(widget: QWidget, object_name: str) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName(object_name)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setWidget(widget)
    area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return area
