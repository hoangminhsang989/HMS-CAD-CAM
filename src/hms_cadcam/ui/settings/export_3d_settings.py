"""Transactional General Settings page for persistent 3D export defaults."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cad.export_models import (
    EXPORT_CAPABILITIES,
    ExportFormatId,
    ExportProfile,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.ui.i18n import TranslationService, UiLanguage, translation_service
from hms_cadcam.ui.settings.export_defaults import (
    PERSISTED_EXPORT_FORMATS,
    ExportDefaultsPersistenceError,
    ExportDefaultsSettingsService,
    factory_export_profiles,
)


def _tr(source: str) -> str:
    return translation_service().translate(source)


class Export3dSettingsPage(QWidget):
    """Compact working-copy editor; persistence occurs only through Apply."""

    dirty_changed = Signal(bool)

    def __init__(
        self,
        settings_service: ExportDefaultsSettingsService,
        *,
        translation: TranslationService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Settings3DExportPage")
        self._settings_service = settings_service
        self._translation = translation or translation_service()
        snapshot = settings_service.load()
        self._baseline = dict(snapshot.profiles)
        self._working = dict(snapshot.profiles)
        self._issues = snapshot.issues
        self._current_format: ExportFormatId | None = None
        self._setting_controls = False
        self._dirty = bool(self._issues)
        self._build_ui()
        self._select_initial_format()
        self.retranslate_ui(self._translation.language)

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def profiles(self) -> dict[ExportFormatId, ExportProfile]:
        self._store_current_controls()
        return dict(self._working)

    @property
    def selected_format(self) -> ExportFormatId:
        return ExportFormatId(str(self.format_combo.currentData()))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.heading = QLabel(self)
        self.heading.setObjectName("Settings3DExportHeading")
        self.heading.setStyleSheet("font-weight: 600;")
        self.description = QLabel(self)
        self.description.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addWidget(self.description)

        self.format_combo = QComboBox(self)
        self.format_combo.setObjectName("ExportDefaultsFormatCombo")
        for capability in EXPORT_CAPABILITIES.values():
            self.format_combo.addItem(capability.label, capability.format_id.value)
        self.standard_combo = QComboBox(self)
        self.standard_combo.setObjectName("ExportDefaultsStandardCombo")
        self.unit_value = QLabel(self)
        self.unit_value.setObjectName("ExportDefaultsUnitPolicy")
        self.reason_label = QLabel(self)
        self.reason_label.setObjectName("ExportDefaultsCapabilityReason")
        self.reason_label.setWordWrap(True)

        self.format_label = QLabel(self)
        self.standard_label = QLabel(self)
        self.unit_label = QLabel(self)
        self.availability_label = QLabel(self)
        basic = QFormLayout()
        basic.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        basic.addRow(self.format_label, self.format_combo)
        basic.addRow(self.standard_label, self.standard_combo)
        basic.addRow(self.unit_label, self.unit_value)
        basic.addRow(self.availability_label, self.reason_label)
        layout.addLayout(basic)

        self.advanced_group = QGroupBox(self)
        self.advanced_group.setObjectName("ExportDefaultsAdvancedGroup")
        advanced = QFormLayout(self.advanced_group)
        self.encoding_combo = QComboBox(self.advanced_group)
        self.encoding_combo.setObjectName("ExportDefaultsStlEncoding")
        self.encoding_combo.addItem("Binary", StlEncoding.BINARY.value)
        self.encoding_combo.addItem("ASCII", StlEncoding.ASCII.value)
        self.linear_deflection = QDoubleSpinBox(self.advanced_group)
        self.linear_deflection.setObjectName("ExportDefaultsLinearDeflection")
        self.linear_deflection.setDecimals(6)
        self.linear_deflection.setRange(0.000001, 1_000_000.0)
        self.linear_deflection.setSingleStep(0.01)
        self.angular_deflection = QDoubleSpinBox(self.advanced_group)
        self.angular_deflection.setObjectName("ExportDefaultsAngularDeflection")
        self.angular_deflection.setDecimals(6)
        self.angular_deflection.setRange(0.000001, 3.141592)
        self.angular_deflection.setSingleStep(0.05)
        self.relative_mesh = QCheckBox(self.advanced_group)
        self.relative_mesh.setObjectName("ExportDefaultsRelativeMesh")
        self.encoding_label = QLabel(self.advanced_group)
        self.linear_label = QLabel(self.advanced_group)
        self.angular_label = QLabel(self.advanced_group)
        advanced.addRow(self.encoding_label, self.encoding_combo)
        advanced.addRow(self.linear_label, self.linear_deflection)
        advanced.addRow(self.angular_label, self.angular_deflection)
        advanced.addRow("", self.relative_mesh)
        layout.addWidget(self.advanced_group)

        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        self.reset_all_button = QPushButton(self)
        self.reset_all_button.setObjectName("ResetAllExportDefaultsButton")
        self.reset_all_button.clicked.connect(self.reset_all)
        reset_row.addWidget(self.reset_all_button)
        layout.addLayout(reset_row)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("ExportDefaultsStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self.standard_combo.currentIndexChanged.connect(self._controls_changed)
        self.encoding_combo.currentIndexChanged.connect(self._controls_changed)
        self.linear_deflection.valueChanged.connect(self._controls_changed)
        self.angular_deflection.valueChanged.connect(self._controls_changed)
        self.relative_mesh.toggled.connect(self._controls_changed)

    def _select_initial_format(self) -> None:
        index = self.format_combo.findData(ExportFormatId.STEP.value)
        self.format_combo.setCurrentIndex(max(index, 0))
        self._format_changed()

    def _format_changed(self) -> None:
        self._store_current_controls()
        format_id = self.selected_format
        self._current_format = format_id
        capability = EXPORT_CAPABILITIES[format_id]
        editable = format_id in PERSISTED_EXPORT_FORMATS and capability.available
        profile = self._working.get(format_id)
        self._setting_controls = True
        try:
            self.standard_combo.clear()
            for standard in capability.standards:
                self.standard_combo.addItem(standard, standard)
            if profile is not None and profile.standard is not None:
                self.standard_combo.setCurrentIndex(
                    max(0, self.standard_combo.findData(profile.standard))
                )
            if profile is not None and profile.stl_encoding is not None:
                self.encoding_combo.setCurrentIndex(
                    max(0, self.encoding_combo.findData(profile.stl_encoding.value))
                )
            if profile is not None and profile.mesh_options is not None:
                self.linear_deflection.setValue(profile.mesh_options.linear_deflection)
                self.angular_deflection.setValue(profile.mesh_options.angular_deflection)
                self.relative_mesh.setChecked(profile.mesh_options.relative)
        finally:
            self._setting_controls = False
        is_stl = format_id is ExportFormatId.STL
        self.advanced_group.setVisible(is_stl and editable)
        self.standard_combo.setVisible(not is_stl)
        self.standard_label.setVisible(not is_stl)
        self.standard_combo.setEnabled(editable and bool(capability.standards))
        self.unit_value.setEnabled(False)
        self._refresh_capability_state()

    def _refresh_capability_state(self) -> None:
        capability = EXPORT_CAPABILITIES[self.selected_format]
        if capability.available and self.selected_format in PERSISTED_EXPORT_FORMATS:
            self.reason_label.setText(_tr("Available") + f" · {capability.backend}")
        else:
            reason = capability.unavailable_reason or _tr(
                "This format is not available"
            )
            self.reason_label.setText(_tr(reason))

    def _controls_changed(self, *_value: object) -> None:
        if self._setting_controls:
            return
        self._store_current_controls()
        self._update_dirty()

    def _store_current_controls(self) -> None:
        format_id = self._current_format
        if format_id not in PERSISTED_EXPORT_FORMATS:
            return
        stored = self._working.get(format_id, factory_export_profiles()[format_id])
        if format_id is ExportFormatId.STL:
            mesh = StlMeshOptions(
                self.linear_deflection.value(),
                self.angular_deflection.value(),
                self.relative_mesh.isChecked(),
            )
            profile = ExportProfile(
                format_id,
                tolerance=mesh.linear_deflection,
                stl_encoding=StlEncoding(str(self.encoding_combo.currentData())),
                mesh_options=mesh,
                overwrite_policy=stored.overwrite_policy,
            )
        else:
            standard = (
                str(self.standard_combo.currentData())
                if self.standard_combo.currentData() is not None
                else None
            )
            profile = ExportProfile(
                format_id,
                standard=standard,
                overwrite_policy=stored.overwrite_policy,
            )
        self._working[format_id] = profile

    def _update_dirty(self) -> None:
        dirty = self._working != self._baseline or bool(self._issues)
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)

    def reset_current(self) -> None:
        """Reset only the selected supported format in the working copy."""

        format_id = self.selected_format
        if format_id not in PERSISTED_EXPORT_FORMATS:
            return
        self._working[format_id] = factory_export_profiles()[format_id]
        self._current_format = None
        self._format_changed()
        self.status_label.setText(_tr("Factory default restored for this format"))
        self._update_dirty()

    def reset_all(self) -> None:
        """Reset all four working profiles without writing QSettings."""

        self._working = factory_export_profiles()
        self._current_format = None
        self._format_changed()
        self.status_label.setText(_tr("Factory defaults restored for all formats"))
        self._update_dirty()

    def apply(self) -> bool:
        """Commit the current working copy and keep the page open."""

        self._store_current_controls()
        try:
            self._settings_service.apply(self._working)
        except (ExportDefaultsPersistenceError, TypeError, ValueError) as error:
            self.status_label.setText(f"{_tr('3D Export settings could not be saved')}: {error}")
            return False
        self._baseline = dict(self._working)
        self._issues = ()
        self.status_label.setText(_tr("3D Export settings applied"))
        self._update_dirty()
        return True

    def retranslate_ui(self, language: object = None) -> None:
        if language is not None:
            UiLanguage.coerce(language)
        self.heading.setText(_tr("Persistent 3D Export Defaults"))
        self.description.setText(
            _tr(
                "These profiles seed 3D Export, Export Selected Objects, and 3D Save As."
            )
        )
        self.format_label.setText(_tr("Format"))
        self.standard_label.setText(_tr("Version / standard"))
        self.unit_label.setText(_tr("Unit policy"))
        self.unit_value.setText(_tr("Model units (fixed)"))
        self.availability_label.setText(_tr("Availability"))
        self.advanced_group.setTitle(_tr("Advanced"))
        self.encoding_label.setText(_tr("STL encoding"))
        self.linear_label.setText(_tr("Linear deflection"))
        self.angular_label.setText(_tr("Angular deflection"))
        self.relative_mesh.setText(_tr("Relative mesh tolerance"))
        self.encoding_combo.setItemText(0, _tr("Binary"))
        self.encoding_combo.setItemText(1, _tr("ASCII"))
        self.reset_all_button.setText(_tr("Reset all export defaults"))
        for index in range(self.format_combo.count()):
            format_id = ExportFormatId(str(self.format_combo.itemData(index)))
            capability = EXPORT_CAPABILITIES[format_id]
            suffix = "" if capability.available else f" · {_tr('Unavailable')}"
            self.format_combo.setItemText(index, f"{capability.label}{suffix}")
        self._refresh_capability_state()
        if self._issues:
            formats = ", ".join(issue.format_id.value.upper() for issue in self._issues)
            self.status_label.setText(
                f"{_tr('3D Export settings are corrupted')}: {formats}. "
                + _tr("Safe factory defaults are shown; Apply to replace the invalid values.")
            )


__all__ = ["Export3dSettingsPage"]
