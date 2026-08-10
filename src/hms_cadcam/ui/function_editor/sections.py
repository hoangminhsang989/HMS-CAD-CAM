"""Collapsible section widget for the Stage 9A.4 Function Editor."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
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
from hms_cadcam.ui.localization import ui_text
from hms_cadcam.ui.ui_tokens import CAMPopupMetrics


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
        localized_title = ui_text(definition.title)
        self.setAccessibleName(f"Phần {localized_title}")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("FunctionEditorSectionHeader")
        header_layout = QHBoxLayout(header)
        self._header_layout = header_layout
        header_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        header_layout.setContentsMargins(7, 5, 5, 5)
        header_layout.setSpacing(5)
        self.toggle = QToolButton()
        self.toggle.setObjectName("FunctionEditorSectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(f"Mở hoặc thu gọn {localized_title}")
        self.toggle.setToolTip(f"Mở hoặc thu gọn phần {localized_title}")
        self.toggle.toggled.connect(self._toggle_changed)
        header_layout.addWidget(self.toggle)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title_label = QLabel(localized_title.upper())
        self.title_label.setObjectName("FunctionEditorSectionTitle")
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.summary_label = QLabel(ui_text(definition.summary))
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
        self.reset_button.setAccessibleName(f"Đặt lại phần {localized_title}")
        self.reset_button.setToolTip("Trả phần về ảnh chụp trạng thái đã áp dụng")
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(definition.section_id)
        )
        header_layout.addWidget(self.reset_button)
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setAccessibleName(f"Trợ giúp phần {localized_title}")
        self.help_button.setToolTip("Mở trợ giúp ngắn cho phần")
        self.help_button.clicked.connect(
            lambda: self.help_requested.emit(definition.section_id)
        )
        header_layout.addWidget(self.help_button)
        root.addWidget(header)

        self.body = QWidget()
        self.body.setObjectName("FunctionEditorSectionBody")
        self.body_layout = QGridLayout(self.body)
        self.body_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.body_layout.setContentsMargins(0, 2, 0, 5)
        self.body_layout.setSpacing(1)
        self._fields: list[FunctionEditorFieldWidget] = []
        self._field_columns = 1
        self._one_screen_compact = False
        root.addWidget(self.body)
        self.set_expanded(definition.default_expanded, emit=False)

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        """Apply the shared compact header/body density to this accordion."""
        self._header_layout.setContentsMargins(
            metrics.content_margin,
            metrics.row_spacing,
            metrics.row_spacing,
            metrics.row_spacing,
        )
        self._header_layout.setSpacing(metrics.row_spacing)
        self.body_layout.setContentsMargins(
            0, metrics.row_spacing, 0, metrics.row_spacing
        )
        self.body_layout.setSpacing(1)
        for button in (self.toggle, self.reset_button, self.help_button):
            button.setMinimumHeight(metrics.compact_button_height)

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
        self._fields.append(field)
        self._rebuild_field_grid()

    def insert_field(self, index: int, field: FunctionEditorFieldWidget) -> None:
        """Insert a lazily-created field at its deterministic schema position."""
        self._fields.insert(index, field)
        self._rebuild_field_grid()

    def set_field_columns(self, columns: int) -> None:
        """Reflow compact summary tiles without changing schema order."""
        normalized = 2 if columns >= 2 else 1
        if normalized == self._field_columns:
            return
        self._field_columns = normalized
        self._rebuild_field_grid()

    def set_one_screen_compact(self, compact: bool) -> None:
        """Remove explanatory header height while Basic is fully visible."""
        self._one_screen_compact = compact
        self.summary_label.setVisible(bool(self.definition.summary) and not compact)
        self.setToolTip(ui_text(self.definition.summary))
        self.updateGeometry()

    @property
    def field_columns(self) -> int:
        return self._field_columns

    def _rebuild_field_grid(self) -> None:
        previous_columns = max(2, self.body_layout.columnCount())
        previous_rows = self.body_layout.rowCount()
        while self.body_layout.count():
            self.body_layout.takeAt(0)
        for column in range(previous_columns):
            self.body_layout.setColumnStretch(column, 0)
            self.body_layout.setColumnMinimumWidth(column, 0)
        for row in range(previous_rows):
            self.body_layout.setRowStretch(row, 0)
            self.body_layout.setRowMinimumHeight(row, 0)
        for index, field in enumerate(self._fields):
            row, column = divmod(index, self._field_columns)
            self.body_layout.addWidget(field, row, column)
        for column in range(self._field_columns):
            self.body_layout.setColumnStretch(column, 1)
        self.body.updateGeometry()
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Return the real header/body hint; compact mode must never crop it."""
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Allow horizontal field reflow while retaining the real body height."""
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

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
