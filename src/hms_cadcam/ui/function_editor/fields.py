"""Field row widgets for the Stage 9A.4 Function Editor."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorValueSource,
    PresentationValue,
)
from hms_cadcam.ui.localization import ui_text
from hms_cadcam.ui.ui_tokens import CAMPopupMetrics


_SOURCE_LABELS = {
    FunctionEditorValueSource.TOOL: "Tool",
    FunctionEditorValueSource.SETUP: "Setup",
    FunctionEditorValueSource.STOCK: "Stock",
    FunctionEditorValueSource.MACHINE: "Machine",
    FunctionEditorValueSource.PROFILE: "Profile",
    FunctionEditorValueSource.GEOMETRY: "Geometry",
    FunctionEditorValueSource.PROJECT: "Project",
    FunctionEditorValueSource.DEFAULT: "HMS Default",
    FunctionEditorValueSource.DERIVED: "Derived",
}

class FunctionEditorFieldWidget(QWidget):
    """Accessible field renderer with unit, provenance and inline diagnostics."""

    value_changed = Signal(str, object)
    reset_requested = Signal(str)
    help_requested = Signal(str)
    action_requested = Signal(str, str)

    def __init__(
        self,
        definition: FunctionEditorField,
        value: PresentationValue,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setObjectName(f"FunctionEditorField_{definition.field_id}")
        self.setAccessibleName(self._accessible_name())
        self.setToolTip(ui_text(definition.tooltip or definition.help_text))
        self._compact = False
        self._one_screen_compact = False
        self._raw_display_value = ""
        self._density_content_margin = 8
        self._density_row_spacing = 2

        self.layout_grid = QGridLayout(self)
        self.layout_grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.layout_grid.setContentsMargins(8, 5, 8, 5)
        self.layout_grid.setHorizontalSpacing(4)
        self.layout_grid.setVerticalSpacing(2)

        label_text = ui_text(definition.label) + (" *" if definition.required else "")
        self.label = QLabel(label_text)
        self.label.setObjectName("FunctionEditorFieldLabel")
        self.label.setWordWrap(True)
        self.label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.label.setAccessibleName(label_text)
        self.editor = self._build_editor()
        if isinstance(self.editor, (QLineEdit, QComboBox, QCheckBox)):
            self.label.setBuddy(self.editor)
        self.unit_label = QLabel(definition.unit)
        self.unit_label.setObjectName("FunctionEditorUnit")
        self.unit_label.setVisible(bool(definition.unit))
        self.unit_label.setAccessibleName(
            f"Đơn vị {definition.unit}" if definition.unit else ""
        )
        self.reset_button = QToolButton()
        self.reset_button.setText("↶")
        self.reset_button.setObjectName("FunctionEditorResetField")
        self.reset_button.setAccessibleName(
            f"Đặt lại trường {ui_text(definition.label)}"
        )
        self.reset_button.setToolTip("Trả trường về giá trị đã áp dụng gần nhất")
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(definition.field_id)
        )
        action_id = definition.action_id
        action_label = ui_text(definition.action_label)
        if (
            definition.field_id == "tool_assembly_id"
            and definition.kind is FunctionEditorFieldKind.CHOICE
            and definition.choices
            and not action_id
        ):
            action_id = "open_tool_selector"
            action_label = "Chọn…"
        self.action_button = QToolButton()
        self.action_button.setText(action_label)
        self.action_button.setObjectName("FunctionEditorFieldAction")
        self.action_button.setAccessibleName(
            f"{action_label} {ui_text(definition.label)}".strip()
        )
        self.action_button.setToolTip(
            ui_text(definition.tooltip or action_label)
        )
        self.action_button.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.action_button.setVisible(bool(action_id))
        self.action_button.clicked.connect(
            lambda: self.action_requested.emit(
                definition.field_id, action_id
            )
        )
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setObjectName("FunctionEditorFieldHelp")
        self.help_button.setAccessibleName(f"Trợ giúp {ui_text(definition.label)}")
        self.help_button.setToolTip("Mở trợ giúp ngắn cho trường này")
        for button in (self.reset_button, self.help_button):
            button.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
        self.help_button.clicked.connect(
            lambda: self.help_requested.emit(definition.field_id)
        )
        self.source_label = QLabel()
        self.source_label.setObjectName("FunctionEditorFieldSource")
        self.source_label.setWordWrap(True)
        source = ui_text(_SOURCE_LABELS.get(definition.source, ""))
        self.source_label.setText(f"Nguồn: {source}" if source else "")
        self.source_label.setVisible(bool(source))
        self.default_label = QLabel()
        self.default_label.setObjectName("FunctionEditorDefaultIndicator")
        self.default_label.setWordWrap(True)
        self.diagnostic_label = QLabel()
        self.diagnostic_label.setObjectName("FunctionEditorInlineDiagnostic")
        self.diagnostic_label.setWordWrap(True)
        self.diagnostic_label.setVisible(False)

        self.layout_grid.addWidget(self.label, 0, 0)
        self.layout_grid.addWidget(self.editor, 0, 1)
        self.layout_grid.addWidget(self.unit_label, 0, 2)
        self.layout_grid.addWidget(self.action_button, 0, 3)
        self.layout_grid.addWidget(self.reset_button, 0, 4)
        self.layout_grid.addWidget(self.help_button, 0, 5)
        self.layout_grid.addWidget(self.source_label, 1, 1, 1, 5)
        self.layout_grid.addWidget(self.default_label, 2, 1, 1, 5)
        self.layout_grid.addWidget(self.diagnostic_label, 3, 1, 1, 5)
        self.layout_grid.setColumnStretch(0, 0)
        self.layout_grid.setColumnStretch(1, 1)
        self.set_value(value)

    def _accessible_name(self) -> str:
        parts = [ui_text(self.definition.label)]
        if self.definition.unit:
            parts.append(self.definition.unit)
        if self.definition.required:
            parts.append("bắt buộc")
        source = ui_text(_SOURCE_LABELS.get(self.definition.source, ""))
        if source:
            parts.append(f"nguồn {source}")
        return ", ".join(parts)

    def _build_editor(self) -> QWidget:
        definition = self.definition
        if definition.kind is FunctionEditorFieldKind.CHECKBOX:
            editor = QCheckBox()
            editor.setObjectName(f"FunctionEditorInput_{definition.field_id}")
            editor.toggled.connect(self._emit_checkbox)
        elif definition.kind is FunctionEditorFieldKind.CHOICE:
            editor = QComboBox()
            editor.setObjectName(f"FunctionEditorInput_{definition.field_id}")
            editor.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            editor.setMinimumContentsLength(8)
            editor.setMinimumWidth(112)
            labels = dict(definition.choice_labels)
            for choice in definition.choices:
                editor.addItem(ui_text(labels.get(choice, str(choice))), choice)
            editor.currentIndexChanged.connect(self._emit_choice)
        elif definition.kind is FunctionEditorFieldKind.READ_ONLY:
            editor = QLabel()
            editor.setObjectName(f"FunctionEditorValue_{definition.field_id}")
            editor.setWordWrap(True)
            editor.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        else:
            editor = QLineEdit()
            editor.setObjectName(f"FunctionEditorInput_{definition.field_id}")
            editor.setClearButtonEnabled(definition.kind is FunctionEditorFieldKind.TEXT)
            editor.textEdited.connect(self._emit_text)
        editor.setAccessibleName(self._accessible_name())
        editor.setToolTip(ui_text(definition.tooltip or definition.help_text))
        editor.setSizePolicy(
            QSizePolicy.Policy.Ignored
            if definition.kind
            in {
                FunctionEditorFieldKind.READ_ONLY,
                FunctionEditorFieldKind.CHOICE,
            }
            else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
            if definition.kind is FunctionEditorFieldKind.READ_ONLY
            else QSizePolicy.Policy.Fixed,
        )
        return editor

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        """Stack label/control when this concrete field—not the page—is narrow."""
        super().resizeEvent(event)
        summary_compact = (
            self._one_screen_compact
            and self.definition.field_id.startswith("automatic_")
        )
        self.set_compact(summary_compact or event.size().width() < 360)

    def _emit_checkbox(self, value: bool) -> None:
        self.value_changed.emit(self.definition.field_id, value)
        self._update_default_indicator(value)

    def _emit_choice(self, index: int) -> None:
        assert isinstance(self.editor, QComboBox)
        value = self.editor.itemData(index)
        self.value_changed.emit(self.definition.field_id, value)
        self._update_default_indicator(value)

    def _emit_text(self, value: str) -> None:
        self.value_changed.emit(self.definition.field_id, value)
        self._update_default_indicator(value)

    def value(self) -> PresentationValue:
        """Return a presentation primitive; parsing belongs to draft validation."""
        if isinstance(self.editor, QCheckBox):
            return self.editor.isChecked()
        if isinstance(self.editor, QComboBox):
            return self.editor.currentData()
        return self.editor.text()  # type: ignore[union-attr]

    def set_value(self, value: PresentationValue) -> None:
        """Render one value without emitting an edit intent."""
        blocker = QSignalBlocker(self.editor)
        try:
            if isinstance(self.editor, QCheckBox):
                self.editor.setChecked(bool(value))
            elif isinstance(self.editor, QComboBox):
                index = self.editor.findData(value)
                if index < 0:
                    index = self.editor.findText(str(value))
                self.editor.setCurrentIndex(index)
            else:
                display = "" if value is None else str(value)
                localized = (
                    ui_text(display)
                    if self.definition.kind is FunctionEditorFieldKind.READ_ONLY
                    else display
                )
                if self.definition.kind is FunctionEditorFieldKind.READ_ONLY:
                    self._raw_display_value = localized
                    self.editor.setText(
                        self._compact_read_only_value(localized)
                        if self._one_screen_compact
                        else localized
                    )
                else:
                    self.editor.setText(localized)
        finally:
            del blocker
        self._update_default_indicator(value)

    def _update_default_indicator(self, value: PresentationValue) -> None:
        definition = self.definition
        if definition.default is not None and value == definition.default:
            label = ui_text(definition.default_label) or "Giá trị khuyến nghị"
            self.default_label.setText(f"Mặc định: {label}")
            self.default_label.setVisible(not self._one_screen_compact)
        else:
            self.default_label.clear()
            self.default_label.setVisible(False)

    def set_one_screen_compact(self, compact: bool) -> None:
        """Hide secondary provenance rows while Basic uses the one-screen grid.

        Provenance remains available through the field tooltip and accessible
        description; diagnostics are never hidden.
        """
        self._one_screen_compact = compact
        horizontal_margin = max(
            5,
            self._density_content_margin - 2
            if compact
            else self._density_content_margin,
        )
        summary_field = self.definition.field_id.startswith("automatic_")
        vertical_margin = (
            1
            if compact and summary_field
            else self._density_row_spacing
        )
        margins = self.layout_grid.contentsMargins()
        self.layout_grid.setContentsMargins(
            horizontal_margin,
            vertical_margin,
            horizontal_margin,
            vertical_margin,
        )
        self.layout_grid.setVerticalSpacing(
            1 if compact and summary_field else self._density_row_spacing
        )
        if isinstance(self.editor, QLabel):
            self.editor.setWordWrap(not compact)
            compact_height = self.editor.fontMetrics().lineSpacing() + (
                2 if summary_field else 8
            )
            self.editor.setMaximumHeight(
                compact_height if compact else 16_777_215
            )
            self.editor.setText(
                self._compact_read_only_value(self._raw_display_value)
                if compact
                else self._raw_display_value
            )
        self.reset_button.setVisible(not compact)
        self.help_button.setVisible(not compact)
        self.source_label.setVisible(bool(self.source_label.text()) and not compact)
        self.default_label.setVisible(
            bool(self.default_label.text()) and not compact
        )
        metadata = " · ".join(
            part
            for part in (self.source_label.text(), self.default_label.text())
            if part
        )
        base_tooltip = ui_text(
            self.definition.tooltip or self.definition.help_text
        )
        tooltip = "\n".join(part for part in (base_tooltip, metadata) if part)
        if self._raw_display_value and self._raw_display_value not in tooltip:
            tooltip = "\n".join(
                part for part in (tooltip, self._raw_display_value) if part
            )
        self.setToolTip(tooltip)
        self.editor.setToolTip(tooltip)
        self.set_compact(self._compact, force=True)
        self.updateGeometry()

    def _compact_read_only_value(self, value: str) -> str:
        compact = value.split(" · Nguồn:", 1)[0]
        if self.definition.field_id == "geometry_summary":
            parts = compact.split(" · ")
            if parts:
                parts[0] = parts[0].replace(
                    " bề mặt gia công", " đã chọn"
                )
                compact = " · ".join(parts[:2])
        return (
            compact.replace(" · Đã xác định", "")
            .replace(" · Cần xác nhận", "")
            .replace("Rút dao giữa các đoạn", "Rút dao")
        )

    def set_diagnostic(
        self, diagnostic: FunctionEditorDiagnostic | None
    ) -> None:
        """Show icon, text and accessibility description—not color alone."""
        if diagnostic is None:
            self.diagnostic_label.clear()
            self.diagnostic_label.setVisible(False)
            self.setAccessibleDescription("")
            self.editor.setProperty("validationState", "")
            self.editor.style().unpolish(self.editor)
            self.editor.style().polish(self.editor)
            return
        prefix = {
            FunctionEditorDiagnosticSeverity.ERROR: "● Lỗi",
            FunctionEditorDiagnosticSeverity.WARNING: "▲ Cảnh báo",
            FunctionEditorDiagnosticSeverity.INFO: "ℹ Thông tin",
        }[diagnostic.severity]
        self.diagnostic_label.setProperty("severity", diagnostic.severity.name.lower())
        self.editor.setProperty("validationState", diagnostic.severity.name.lower())
        message = ui_text(diagnostic.message)
        self.diagnostic_label.setText(f"{prefix}: {message}")
        self.diagnostic_label.setVisible(True)
        self.setAccessibleDescription(f"{prefix}: {message}")
        self.diagnostic_label.style().unpolish(self.diagnostic_label)
        self.diagnostic_label.style().polish(self.diagnostic_label)
        self.editor.style().unpolish(self.editor)
        self.editor.style().polish(self.editor)

    def set_compact(self, compact: bool, *, force: bool = False) -> None:
        """Reflow label/control vertically below 400 logical pixels."""
        if compact == self._compact and not force:
            return
        self._compact = compact
        self.label.setSizePolicy(
            QSizePolicy.Policy.Ignored
            if compact
            else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        widgets = (
            self.label,
            self.editor,
            self.unit_label,
            self.reset_button,
            self.action_button,
            self.help_button,
            self.source_label,
            self.default_label,
            self.diagnostic_label,
        )
        for widget in widgets:
            self.layout_grid.removeWidget(widget)
        if compact:
            self.layout_grid.addWidget(self.label, 0, 0, 1, 6)
            value_only = not any(
                not widget.isHidden()
                for widget in (
                    self.unit_label,
                    self.action_button,
                    self.reset_button,
                    self.help_button,
                )
            )
            separate_read_only_action = (
                self._one_screen_compact
                and self.definition.kind is FunctionEditorFieldKind.READ_ONLY
                and self.action_button.isVisible()
            )
            inline_geometry_action = (
                separate_read_only_action
                and self.definition.field_id == "geometry_summary"
            )
            if value_only:
                self.layout_grid.addWidget(self.editor, 1, 0, 1, 6)
                metadata_row = 2
            elif inline_geometry_action:
                self.layout_grid.addWidget(self.editor, 1, 0, 1, 4)
                self.layout_grid.addWidget(
                    self.action_button,
                    1,
                    4,
                    1,
                    2,
                    Qt.AlignmentFlag.AlignRight,
                )
                metadata_row = 2
            elif separate_read_only_action:
                self.layout_grid.addWidget(self.editor, 1, 0, 1, 6)
                self.layout_grid.addWidget(
                    self.action_button,
                    2,
                    0,
                    1,
                    6,
                    Qt.AlignmentFlag.AlignRight,
                )
                metadata_row = 3
            elif self._one_screen_compact:
                self.layout_grid.addWidget(self.editor, 1, 0, 1, 2)
                self.layout_grid.addWidget(self.unit_label, 1, 2)
                self.layout_grid.addWidget(self.action_button, 1, 3)
                self.layout_grid.addWidget(self.reset_button, 1, 4)
                self.layout_grid.addWidget(self.help_button, 1, 5)
                metadata_row = 2
            else:
                self.layout_grid.addWidget(self.editor, 1, 0, 1, 5)
                self.layout_grid.addWidget(self.unit_label, 1, 5)
                self.layout_grid.addWidget(
                    self.action_button,
                    2,
                    0,
                    1,
                    4,
                    Qt.AlignmentFlag.AlignRight,
                )
                self.layout_grid.addWidget(self.reset_button, 2, 4)
                self.layout_grid.addWidget(self.help_button, 2, 5)
                metadata_row = 3
            self.layout_grid.addWidget(
                self.source_label, metadata_row, 0, 1, 6
            )
            self.layout_grid.addWidget(
                self.default_label, metadata_row + 1, 0, 1, 6
            )
            self.layout_grid.addWidget(
                self.diagnostic_label, metadata_row + 2, 0, 1, 6
            )
        else:
            self.layout_grid.addWidget(self.label, 0, 0)
            self.layout_grid.addWidget(self.editor, 0, 1)
            self.layout_grid.addWidget(self.unit_label, 0, 2)
            self.layout_grid.addWidget(self.action_button, 0, 3)
            self.layout_grid.addWidget(self.reset_button, 0, 4)
            self.layout_grid.addWidget(self.help_button, 0, 5)
            self.layout_grid.addWidget(self.source_label, 1, 1, 1, 5)
            self.layout_grid.addWidget(self.default_label, 2, 1, 1, 5)
            self.layout_grid.addWidget(self.diagnostic_label, 3, 1, 1, 5)

    def apply_density(self, metrics: CAMPopupMetrics) -> None:
        """Apply the shared CAM row metrics without changing field semantics."""
        self._density_content_margin = metrics.content_margin
        self._density_row_spacing = metrics.row_spacing
        summary_field = self.definition.field_id.startswith("automatic_")
        horizontal_margin = max(
            5,
            metrics.content_margin - 2
            if self._one_screen_compact
            else metrics.content_margin,
        )
        self.layout_grid.setContentsMargins(
            horizontal_margin,
            1
            if self._one_screen_compact and summary_field
            else metrics.row_spacing,
            horizontal_margin,
            1
            if self._one_screen_compact and summary_field
            else metrics.row_spacing,
        )
        self.layout_grid.setHorizontalSpacing(metrics.label_spacing)
        self.layout_grid.setVerticalSpacing(
            1
            if self._one_screen_compact and summary_field
            else metrics.row_spacing
        )
        if isinstance(self.editor, (QLineEdit, QComboBox)):
            self.editor.setMinimumHeight(metrics.control_height)
        for button in (
            self.action_button,
            self.reset_button,
            self.help_button,
        ):
            button.setMinimumHeight(metrics.compact_button_height)
        if self.action_button.text():
            action_width = max(
                metrics.compact_button_height,
                self.action_button.fontMetrics().horizontalAdvance(
                    self.action_button.text()
                )
                + 18,
            )
            self.action_button.setMaximumWidth(action_width)

    def focus_editor(self) -> None:
        """Move keyboard focus to the concrete editor control."""
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(self.editor, QLineEdit):
            self.editor.selectAll()
