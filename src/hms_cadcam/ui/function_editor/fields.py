"""Field row widgets for the Stage 9A.4 Function Editor."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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


_SOURCE_LABELS = {
    FunctionEditorValueSource.TOOL: "Tool",
    FunctionEditorValueSource.SETUP: "Setup",
    FunctionEditorValueSource.STOCK: "Stock",
    FunctionEditorValueSource.MACHINE: "Machine",
    FunctionEditorValueSource.PROFILE: "Profile",
    FunctionEditorValueSource.GEOMETRY: "Geometry",
    FunctionEditorValueSource.PROJECT: "Project",
    FunctionEditorValueSource.DEFAULT: "HMS Default",
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
        self.setToolTip(definition.tooltip or definition.help_text)
        self._compact = False

        self.layout_grid = QGridLayout(self)
        self.layout_grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.layout_grid.setContentsMargins(8, 5, 8, 5)
        self.layout_grid.setHorizontalSpacing(4)
        self.layout_grid.setVerticalSpacing(2)

        label_text = definition.label + (" *" if definition.required else "")
        self.label = QLabel(label_text)
        self.label.setObjectName("FunctionEditorFieldLabel")
        self.label.setWordWrap(True)
        self.label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
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
        self.reset_button.setAccessibleName(f"Reset field {definition.label}")
        self.reset_button.setToolTip("Trả field về giá trị đã Apply gần nhất")
        self.reset_button.clicked.connect(
            lambda: self.reset_requested.emit(definition.field_id)
        )
        self.action_button = QToolButton()
        self.action_button.setText(definition.action_label)
        self.action_button.setObjectName("FunctionEditorFieldAction")
        self.action_button.setAccessibleName(
            f"{definition.action_label} {definition.label}".strip()
        )
        self.action_button.setToolTip(definition.tooltip or definition.action_label)
        self.action_button.setVisible(bool(definition.action_id))
        self.action_button.clicked.connect(
            lambda: self.action_requested.emit(
                definition.field_id, definition.action_id
            )
        )
        self.help_button = QToolButton()
        self.help_button.setText("?")
        self.help_button.setObjectName("FunctionEditorFieldHelp")
        self.help_button.setAccessibleName(f"Trợ giúp {definition.label}")
        self.help_button.setToolTip("Mở trợ giúp ngắn cho field này")
        self.help_button.clicked.connect(
            lambda: self.help_requested.emit(definition.field_id)
        )
        self.source_label = QLabel()
        self.source_label.setObjectName("FunctionEditorFieldSource")
        self.source_label.setWordWrap(True)
        source = _SOURCE_LABELS.get(definition.source)
        self.source_label.setText(f"Nguồn: {source}" if source else "")
        self.source_label.setVisible(source is not None)
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
        self.layout_grid.setColumnStretch(1, 1)
        self.set_value(value)

    def _accessible_name(self) -> str:
        parts = [self.definition.label]
        if self.definition.unit:
            parts.append(self.definition.unit)
        if self.definition.required:
            parts.append("bắt buộc")
        source = _SOURCE_LABELS.get(self.definition.source)
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
            labels = dict(definition.choice_labels)
            for choice in definition.choices:
                editor.addItem(labels.get(choice, str(choice)), choice)
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
        editor.setToolTip(definition.tooltip or definition.help_text)
        editor.setSizePolicy(
            QSizePolicy.Policy.Ignored
            if definition.kind is FunctionEditorFieldKind.READ_ONLY
            else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
            if definition.kind is FunctionEditorFieldKind.READ_ONLY
            else QSizePolicy.Policy.Fixed,
        )
        return editor

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
                self.editor.setText("" if value is None else str(value))
        finally:
            del blocker
        self._update_default_indicator(value)

    def _update_default_indicator(self, value: PresentationValue) -> None:
        definition = self.definition
        if definition.default is not None and value == definition.default:
            label = definition.default_label or "Giá trị khuyến nghị"
            self.default_label.setText(f"Mặc định: {label}")
            self.default_label.setVisible(True)
        else:
            self.default_label.clear()
            self.default_label.setVisible(False)

    def set_diagnostic(
        self, diagnostic: FunctionEditorDiagnostic | None
    ) -> None:
        """Show icon, text and accessibility description—not color alone."""
        if diagnostic is None:
            self.diagnostic_label.clear()
            self.diagnostic_label.setVisible(False)
            self.setAccessibleDescription("")
            return
        prefix = {
            FunctionEditorDiagnosticSeverity.ERROR: "● Lỗi",
            FunctionEditorDiagnosticSeverity.WARNING: "▲ Cảnh báo",
            FunctionEditorDiagnosticSeverity.INFO: "ℹ Thông tin",
        }[diagnostic.severity]
        self.diagnostic_label.setProperty("severity", diagnostic.severity.name.lower())
        self.diagnostic_label.setText(f"{prefix}: {diagnostic.message}")
        self.diagnostic_label.setVisible(True)
        self.setAccessibleDescription(f"{prefix}: {diagnostic.message}")
        self.diagnostic_label.style().unpolish(self.diagnostic_label)
        self.diagnostic_label.style().polish(self.diagnostic_label)

    def set_compact(self, compact: bool) -> None:
        """Reflow label/control vertically below 400 logical pixels."""
        if compact == self._compact:
            return
        self._compact = compact
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
            self.layout_grid.addWidget(self.editor, 1, 0, 1, 2)
            self.layout_grid.addWidget(self.unit_label, 1, 2)
            self.layout_grid.addWidget(self.action_button, 1, 3)
            self.layout_grid.addWidget(self.reset_button, 1, 4)
            self.layout_grid.addWidget(self.help_button, 1, 5)
            self.layout_grid.addWidget(self.source_label, 2, 0, 1, 6)
            self.layout_grid.addWidget(self.default_label, 3, 0, 1, 6)
            self.layout_grid.addWidget(self.diagnostic_label, 4, 0, 1, 6)
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

    def focus_editor(self) -> None:
        """Move keyboard focus to the concrete editor control."""
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(self.editor, QLineEdit):
            self.editor.selectAll()
