"""Collapsible section widget for the Stage 9A.4 Function Editor."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.function_editor.fields import FunctionEditorFieldWidget
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorSection,
)


class FunctionEditorSectionWidget(QFrame):
    """Keyboard-operable accordion with summary, help and diagnostic badge."""

    expanded_changed = Signal(str, bool)
    reset_requested = Signal(str)
    help_requested = Signal(str)

    def __init__(
        self,
        definition: FunctionEditorSection,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setObjectName(f"FunctionEditorSection_{definition.section_id}")
        self.setAccessibleName(f"Section {definition.title}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("FunctionEditorSectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        header_layout.setContentsMargins(7, 5, 5, 5)
        header_layout.setSpacing(5)
        self.toggle = QToolButton()
        self.toggle.setObjectName("FunctionEditorSectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(f"Mở hoặc thu gọn {definition.title}")
        self.toggle.setToolTip(f"Mở hoặc thu gọn section {definition.title}")
        self.toggle.toggled.connect(self._toggle_changed)
        header_layout.addWidget(self.toggle)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title_label = QLabel(definition.title.upper())
        self.title_label.setObjectName("FunctionEditorSectionTitle")
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.summary_label = QLabel(definition.summary)
        self.summary_label.setObjectName("FunctionEditorSectionSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.summary_label.setVisible(bool(definition.summary))
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.summary_label)
        header_layout.addLayout(title_box, 1)
        self.badge = QLabel()
        self.badge.setObjectName("FunctionEditorSectionBadge")
        self.badge.setVisible(False)
        header_layout.addWidget(self.badge)
        self.reset_button = QToolButton()
        self.reset_button.setText("↶")
        self.reset_button.setAccessibleName(f"Reset section {definition.title}")
        self.reset_button.setToolTip("Trả section về snapshot đã Apply")
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(definition.section_id)
        )
        header_layout.addWidget(self.reset_button)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setAccessibleName(f"Trợ giúp section {definition.title}")
        self.help_button.setToolTip("Mở trợ giúp ngắn cho section")
        self.help_button.clicked.connect(
            lambda: self.help_requested.emit(definition.section_id)
        )
        header_layout.addWidget(self.help_button)
        root.addWidget(header)

        self.body = QWidget()
        self.body.setObjectName("FunctionEditorSectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.body_layout.setContentsMargins(0, 2, 0, 5)
        self.body_layout.setSpacing(1)
        root.addWidget(self.body)
        self.set_expanded(definition.default_expanded, emit=False)

    @property
    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, expanded: bool, *, emit: bool = True) -> None:
        """Set expansion state while keeping icon/text/accessibility synchronized."""
        self.toggle.blockSignals(not emit)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self._render_expansion(expanded)

    def _toggle_changed(self, expanded: bool) -> None:
        self._render_expansion(expanded)
        self.expanded_changed.emit(self.definition.section_id, expanded)

    def _render_expansion(self, expanded: bool) -> None:
        self.toggle.setText("▾" if expanded else "▸")
        self.toggle.setAccessibleDescription(
            "Đang mở" if expanded else "Đang thu gọn"
        )
        self.body.setVisible(expanded)

    def add_field(self, field: FunctionEditorFieldWidget) -> None:
        """Append one applicable field widget."""
        self.body_layout.addWidget(field)

    def insert_field(self, index: int, field: FunctionEditorFieldWidget) -> None:
        """Insert a lazily-created field at its deterministic schema position."""
        self.body_layout.insertWidget(index, field)

    def set_diagnostics(
        self, diagnostics: tuple[FunctionEditorDiagnostic, ...]
    ) -> None:
        """Aggregate error/warning counts with text and symbols."""
        errors = sum(
            item.severity is FunctionEditorDiagnosticSeverity.ERROR
            for item in diagnostics
        )
        warnings = sum(
            item.severity is FunctionEditorDiagnosticSeverity.WARNING
            for item in diagnostics
        )
        parts = []
        if errors:
            parts.append(f"● {errors} lỗi")
        if warnings:
            parts.append(f"▲ {warnings} cảnh báo")
        self.badge.setText(" · ".join(parts))
        self.badge.setVisible(bool(parts))
        self.badge.setAccessibleName(self.badge.text())
        self.setProperty("validation", "error" if errors else "warning" if warnings else "ok")
        self.style().unpolish(self)
        self.style().polish(self)

    def expand_relevant(
        self, diagnostics: tuple[FunctionEditorDiagnostic, ...]
    ) -> None:
        """Open required/error sections without turning Expand Relevant into Expand All."""
        if diagnostics:
            self.set_expanded(True)
