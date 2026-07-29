"""Concrete Qt controls for the WP2B-B CAM 3D editor binding."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorField,
    Cam3DEditorReadiness,
)
from hms_cadcam.cam.domain import DiagnosticSeverity
from hms_cadcam.ui.cam3d_editor_binding import (
    DIAGNOSTIC_SOURCE_KEYS,
    Cam3DEditorRenderState,
)
from hms_cadcam.ui.localization import ui_text


_NUMERIC_FIELDS: tuple[tuple[Cam3DEditorField, str, str], ...] = (
    (Cam3DEditorField.TOLERANCE_MM, "Cam3DNumeric_tolerance_mm", "Tolerance"),
    (
        Cam3DEditorField.ALLOWANCE_MM,
        "Cam3DNumeric_allowance_mm",
        "Surface Allowance",
    ),
    (
        Cam3DEditorField.CLEARANCE_Z_MM,
        "Cam3DNumeric_clearance_z_mm",
        "Clearance Z",
    ),
    (
        Cam3DEditorField.RETRACT_Z_MM,
        "Cam3DNumeric_retract_z_mm",
        "Retract Z",
    ),
    (
        Cam3DEditorField.APPROACH_DISTANCE_MM,
        "Cam3DNumeric_approach_distance_mm",
        "Approach Distance",
    ),
    (
        Cam3DEditorField.LINK_CLEARANCE_MM,
        "Cam3DNumeric_link_clearance_mm",
        "Link Clearance",
    ),
)

_READINESS_SOURCE_KEYS = {
    Cam3DEditorReadiness.READ_ONLY: "READ_ONLY",
    Cam3DEditorReadiness.EMPTY: "EMPTY",
    Cam3DEditorReadiness.STALE: "STALE",
    Cam3DEditorReadiness.INVALID: "INVALID",
    Cam3DEditorReadiness.PARTIAL: "PARTIAL",
    Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING: "READY_FOR_EDITOR_BINDING",
}

_SEVERITY_SOURCE_KEYS = {
    DiagnosticSeverity.ERROR: "ERROR",
    DiagnosticSeverity.WARNING: "WARNING",
    DiagnosticSeverity.INFO: "INFO",
}


class Cam3DEditorWidget(QWidget):
    """Presentation-only editor controls carrying typed intent signals."""

    tool_assembly_changed = Signal(object)
    tool_profile_changed = Signal(object)
    numeric_field_changed = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Cam3DEditorWidget")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._last_state: Cam3DEditorRenderState | None = None
        self._numeric: dict[Cam3DEditorField, QLineEdit] = {}
        self._field_labels: dict[Cam3DEditorField, QLabel] = {}
        self._section_widgets: dict[str, QWidget] = {}
        self._build()
        self.tool_assembly_combo.currentIndexChanged.connect(
            self._assembly_changed
        )
        self.tool_profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.retranslate_ui()

        for control in self.mutation_controls:
            control.setEnabled(False)
    @property
    def mutation_controls(self) -> tuple[QWidget, ...]:
        """Return the exact eight mutation controls in deterministic tab order."""

        return (
            self.tool_assembly_combo,
            self.tool_profile_combo,
            *(self._numeric[field] for field, _name, _label in _NUMERIC_FIELDS),
        )

    def section_widget(self, key: str) -> QWidget:
        """Return one concrete editor section for composition by the WP1 panel."""

        try:
            return self._section_widgets[key]
        except KeyError as exc:
            raise KeyError(f"unknown CAM 3D editor section: {key}") from exc

    def numeric_control(self, field: Cam3DEditorField) -> QLineEdit:
        """Return one typed numeric control."""

        if not isinstance(field, Cam3DEditorField):
            raise TypeError("field must be Cam3DEditorField")
        return self._numeric[field]

    def _build(self) -> None:
        self.tool_assembly_combo = QComboBox(self)
        self.tool_assembly_combo.setObjectName("Cam3DToolAssemblyCombo")
        self.tool_profile_combo = QComboBox(self)
        self.tool_profile_combo.setObjectName("Cam3DToolProfileCombo")

        tool_section = QWidget(self)
        tool_section.setObjectName("Cam3DEditorSection_tool")
        tool_layout = QFormLayout(tool_section)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.tool_assembly_label = QLabel(tool_section)
        self.tool_profile_label = QLabel(tool_section)
        tool_layout.addRow(self.tool_assembly_label, self.tool_assembly_combo)
        tool_layout.addRow(self.tool_profile_label, self.tool_profile_combo)
        self._section_widgets["tool"] = tool_section

        for field, object_name, _label_source in _NUMERIC_FIELDS:
            edit = QLineEdit(self)
            edit.setObjectName(object_name)
            edit.setProperty("field", field)
            edit.setPlaceholderText("mm")
            edit.setClearButtonEnabled(True)
            edit.editingFinished.connect(
                lambda field=field, edit=edit: self.numeric_field_changed.emit(
                    field,
                    edit.text(),
                )
            )
            self._numeric[field] = edit

        self._section_widgets["tolerance"] = self._single_numeric_section(
            "tolerance",
            Cam3DEditorField.TOLERANCE_MM,
        )
        self._section_widgets["allowance"] = self._single_numeric_section(
            "allowance",
            Cam3DEditorField.ALLOWANCE_MM,
        )

        safe_section = QWidget(self)
        safe_section.setObjectName("Cam3DEditorSection_safe_motion")
        safe_layout = QFormLayout(safe_section)
        safe_layout.setContentsMargins(0, 0, 0, 0)
        safe_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        for field in (
            Cam3DEditorField.CLEARANCE_Z_MM,
            Cam3DEditorField.RETRACT_Z_MM,
            Cam3DEditorField.APPROACH_DISTANCE_MM,
            Cam3DEditorField.LINK_CLEARANCE_MM,
        ):
            label = QLabel(safe_section)
            self._field_labels[field] = label
            safe_layout.addRow(label, self._numeric_row(field, safe_section))
        self._section_widgets["safe_motion"] = safe_section

        diagnostics_section = QWidget(self)
        diagnostics_section.setObjectName("Cam3DEditorSection_diagnostics")
        diagnostics_layout = QVBoxLayout(diagnostics_section)
        diagnostics_layout.setContentsMargins(0, 0, 0, 0)
        readiness_row = QHBoxLayout()
        self.readiness_caption = QLabel(diagnostics_section)
        self.readiness_value = QLabel(diagnostics_section)
        self.readiness_value.setObjectName("Cam3DEditorReadinessValue")
        self.readiness_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        readiness_row.addWidget(self.readiness_caption)
        readiness_row.addWidget(self.readiness_value, 1)
        diagnostics_layout.addLayout(readiness_row)
        self.diagnostics_list = QListWidget(diagnostics_section)
        self.diagnostics_list.setObjectName("Cam3DEditorDiagnosticsList")
        self.diagnostics_list.setSelectionMode(
            QListWidget.SelectionMode.NoSelection
        )
        diagnostics_layout.addWidget(self.diagnostics_list)
        self._section_widgets["diagnostics"] = diagnostics_section

        controls = self.mutation_controls
        for first, second in zip(controls, controls[1:]):
            QWidget.setTabOrder(first, second)

    def _single_numeric_section(
        self,
        key: str,
        field: Cam3DEditorField,
    ) -> QWidget:
        section = QWidget(self)
        section.setObjectName(f"Cam3DEditorSection_{key}")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._numeric_row(field, section))
        return section

    def _numeric_row(
        self,
        field: Cam3DEditorField,
        parent: QWidget,
    ) -> QWidget:
        row = QWidget(parent)
        row.setObjectName(f"Cam3DNumericRow_{field.value}")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._numeric[field], 1)
        unit = QLabel("mm", row)
        unit.setObjectName(f"Cam3DUnit_{field.value}")
        unit.setAccessibleName("mm")
        layout.addWidget(unit)
        return row

    def _assembly_changed(self, index: int) -> None:
        self.tool_assembly_changed.emit(
            self.tool_assembly_combo.itemData(index) if index >= 0 else None
        )

    def _profile_changed(self, index: int) -> None:
        self.tool_profile_changed.emit(
            self.tool_profile_combo.itemData(index) if index >= 0 else None
        )

    def set_render_state(self, state: Cam3DEditorRenderState) -> None:
        """Render one immutable projection without emitting mutation intents."""

        if not isinstance(state, Cam3DEditorRenderState):
            raise TypeError("state must be Cam3DEditorRenderState")
        self._last_state = state
        with (
            QSignalBlocker(self.tool_assembly_combo),
            QSignalBlocker(self.tool_profile_combo),
        ):
            self.tool_assembly_combo.clear()
            self.tool_assembly_combo.addItem(
                ui_text("Select Tool Assembly"),
                None,
            )
            for option in state.tool_options:
                self.tool_assembly_combo.addItem(option.label, option.choice)
                item = self.tool_assembly_combo.model().item(
                    self.tool_assembly_combo.count() - 1
                )
                if item is not None:
                    item.setEnabled(option.selectable or option.current)
            self._select_combo_choice(
                self.tool_assembly_combo,
                state.selected_tool_assembly,
            )

            self.tool_profile_combo.clear()
            self.tool_profile_combo.addItem(
                ui_text("Select Tool Profile"),
                None,
            )
            for option in state.profile_options:
                self.tool_profile_combo.addItem(option.label, option.choice)
                item = self.tool_profile_combo.model().item(
                    self.tool_profile_combo.count() - 1
                )
                if item is not None:
                    item.setEnabled(option.selectable or option.current)
            self._select_combo_choice(
                self.tool_profile_combo,
                state.selected_tool_profile,
            )

        values = state.parameters
        field_diagnostics = dict(state.field_diagnostics)
        for field, edit in self._numeric.items():
            value = getattr(values, field.value)
            with QSignalBlocker(edit):
                edit.setText("" if value is None else format(value, ".15g"))
            edit.setEnabled(state.editable)
            edit.setProperty(
                "diagnosticCount",
                len(field_diagnostics.get(field, ())),
            )
        self.tool_assembly_combo.setEnabled(state.editable)
        self.tool_profile_combo.setEnabled(
            state.editable
            and state.selected_tool_assembly is not None
            and bool(state.profile_options)
        )
        self._render_diagnostics(state)

    @staticmethod
    def _select_combo_choice(combo: QComboBox, choice: object) -> None:
        if choice is None:
            combo.setCurrentIndex(0)
            return
        for index in range(combo.count()):
            if combo.itemData(index) == choice:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    def _render_diagnostics(self, state: Cam3DEditorRenderState) -> None:
        readiness_source = _READINESS_SOURCE_KEYS[state.readiness]
        self.readiness_value.setText(ui_text(readiness_source))
        self.readiness_value.setAccessibleName(
            f"{ui_text('Editor readiness')}: {ui_text(readiness_source)}"
        )
        self.diagnostics_list.clear()
        for diagnostic in state.diagnostics:
            source = DIAGNOSTIC_SOURCE_KEYS[diagnostic.code]
            params = dict(diagnostic.parameters)
            try:
                message = ui_text(source).format(**params)
            except (KeyError, ValueError):
                message = ui_text(source)
            severity = ui_text(_SEVERITY_SOURCE_KEYS[diagnostic.severity])
            text = f"{severity}: {message}"
            item = QListWidgetItem(text, self.diagnostics_list)
            item.setData(Qt.ItemDataRole.UserRole, diagnostic)
            item.setData(
                int(Qt.ItemDataRole.UserRole) + 1,
                diagnostic.field,
            )
            item.setToolTip(text)

    def retranslate_ui(self) -> None:
        """Retranslate labels and rerender the last immutable projection."""

        self.tool_assembly_label.setText(ui_text("Tool Assembly"))
        self.tool_profile_label.setText(ui_text("Tool Profile"))
        self.tool_assembly_combo.setAccessibleName(ui_text("Tool Assembly"))
        self.tool_assembly_combo.setAccessibleDescription(
            ui_text("Select a Tool Assembly for the CAM 3D editor")
        )
        self.tool_profile_combo.setAccessibleName(ui_text("Tool Profile"))
        self.tool_profile_combo.setAccessibleDescription(
            ui_text("Select a Tool Profile owned by the selected Tool Assembly")
        )
        labels = {field: source for field, _name, source in _NUMERIC_FIELDS}
        for field, edit in self._numeric.items():
            source = labels[field]
            label = self._field_labels.get(field)
            if label is not None:
                label.setText(ui_text(source))
            edit.setAccessibleName(ui_text(source))
            edit.setAccessibleDescription(ui_text(f"{source} in mm"))
        self.readiness_caption.setText(f"{ui_text('Editor readiness')}:")
        self.diagnostics_list.setAccessibleName(ui_text("CAM 3D diagnostics"))
        self.diagnostics_list.setAccessibleDescription(
            ui_text("Structured CAM 3D editor diagnostics")
        )
        if self._last_state is not None:
            self.set_render_state(self._last_state)


__all__ = ["Cam3DEditorWidget"]
