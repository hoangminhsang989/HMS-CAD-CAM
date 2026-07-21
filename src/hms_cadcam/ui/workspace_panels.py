"""Panel hosts that recompose existing Stage 9A workflow widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.operation_manager import OperationManagerPanel


class OperationManagerHost(OperationManagerPanel):
    """Compatibility name for the production Stage 9A.3 panel."""


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
