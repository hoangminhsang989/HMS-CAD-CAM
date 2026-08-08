"""Compact versioned CAD export dialog and request-owned worker controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cad.export_models import (
    ExportCapability,
    ExportEntityKind,
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
    ExportSelectionRef,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.cad.export_service import CadExportService, ExportRequest, ExportResult
from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localized_dialogs import QFileDialog
from hms_cadcam.ui.project_worker import ProjectTask
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode


def _tr(source: str) -> str:
    return translation_service().translate(source)


_SELECTION_KINDS = {
    SelectionMode.SOLID: ExportEntityKind.SOLID,
    SelectionMode.FACE: ExportEntityKind.FACE,
    SelectionMode.WIRE: ExportEntityKind.WIRE,
    SelectionMode.EDGE: ExportEntityKind.EDGE,
}


@dataclass(frozen=True, slots=True)
class _ExportOwnership:
    project_identity: object | None
    document_id: CadDocumentId


class CadExportProfileDialog(QDialog):
    """Basic-first profile editor; Advanced contains only STL writer options."""

    def __init__(
        self,
        capabilities: tuple[ExportCapability, ...],
        profiles: dict[ExportFormatId, ExportProfile],
        *,
        initial_format: ExportFormatId = ExportFormatId.STEP,
        stl_tessellation_applicable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CadExportProfileDialog")
        self._capabilities = {item.format_id: item for item in capabilities}
        self._profiles = dict(profiles)
        self._current_format: ExportFormatId | None = None
        self._stl_tessellation_applicable = stl_tessellation_applicable

        self.format_combo = QComboBox(self)
        self.format_combo.setObjectName("CadExportFormatCombo")
        for capability in capabilities:
            suffix = "" if capability.available else f" — {_tr('Unavailable')}"
            self.format_combo.addItem(
                f"{capability.label}{suffix}", capability.format_id.value
            )
        self.standard_combo = QComboBox(self)
        self.standard_combo.setObjectName("CadExportStandardCombo")
        self.reason_label = QLabel(self)
        self.reason_label.setObjectName("CadExportCapabilityReason")
        self.reason_label.setWordWrap(True)
        self.format_label = QLabel(self)
        self.standard_label = QLabel(self)
        self.availability_label = QLabel(self)

        basic = QFormLayout()
        basic.addRow(self.format_label, self.format_combo)
        basic.addRow(self.standard_label, self.standard_combo)
        basic.addRow(self.availability_label, self.reason_label)

        self.advanced_group = QGroupBox(_tr("Advanced"), self)
        advanced = QFormLayout(self.advanced_group)
        self._advanced_layout = advanced
        self.encoding_combo = QComboBox(self.advanced_group)
        self.encoding_combo.addItem(_tr("Binary"), StlEncoding.BINARY.value)
        self.encoding_combo.addItem(_tr("ASCII"), StlEncoding.ASCII.value)
        self.linear_deflection = QDoubleSpinBox(self.advanced_group)
        self.linear_deflection.setDecimals(6)
        self.linear_deflection.setRange(0.000001, 1_000_000.0)
        self.linear_deflection.setSingleStep(0.05)
        self.angular_deflection = QDoubleSpinBox(self.advanced_group)
        self.angular_deflection.setDecimals(6)
        self.angular_deflection.setRange(0.000001, 3.141592)
        self.angular_deflection.setSingleStep(0.1)
        self.relative_mesh = QCheckBox(_tr("Relative mesh tolerance"), self.advanced_group)
        self.mesh_applicability_label = QLabel(self.advanced_group)
        self.mesh_applicability_label.setWordWrap(True)
        self.encoding_label = QLabel(self.advanced_group)
        self.linear_label = QLabel(self.advanced_group)
        self.angular_label = QLabel(self.advanced_group)
        advanced.addRow(self.encoding_label, self.encoding_combo)
        advanced.addRow(self.linear_label, self.linear_deflection)
        advanced.addRow(self.angular_label, self.angular_deflection)
        advanced.addRow("", self.relative_mesh)
        advanced.addRow(self.mesh_applicability_label)

        self.validation_label = QLabel(self)
        self.validation_label.setObjectName("CadExportValidation")
        self.validation_label.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self._accept_validated)
        self.buttons.rejected.connect(self.reject)
        translation_service().language_changed.connect(self._on_language_changed)

        layout = QVBoxLayout(self)
        layout.addLayout(basic)
        layout.addWidget(self.advanced_group)
        layout.addWidget(self.validation_label)
        layout.addWidget(self.buttons)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        index = self.format_combo.findData(initial_format.value)
        self.format_combo.blockSignals(True)
        self.format_combo.setCurrentIndex(max(index, 0))
        self.format_combo.blockSignals(False)
        self._format_changed()
        self.retranslate()

    @property
    def selected_format(self) -> ExportFormatId:
        return ExportFormatId(str(self.format_combo.currentData()))

    def profile(self) -> ExportProfile:
        """Return the currently visible, typed profile or raise validation error."""
        profile = self._profile_from_controls(self.selected_format)
        self._remember_profile(self.selected_format, profile)
        return profile

    @property
    def profiles(self) -> dict[ExportFormatId, ExportProfile]:
        return dict(self._profiles)

    def retranslate(self) -> None:
        """Refresh text without reconstructing or losing the current profile."""
        self.setWindowTitle(_tr("3D Export Profile"))
        self.advanced_group.setTitle(_tr("Advanced"))
        self.format_label.setText(_tr("Format"))
        self.standard_label.setText(_tr("Version / standard"))
        self.availability_label.setText(_tr("Availability"))
        self.encoding_label.setText(_tr("STL encoding"))
        self.linear_label.setText(_tr("Linear deflection"))
        self.angular_label.setText(_tr("Angular deflection"))
        self.relative_mesh.setText(_tr("Relative mesh tolerance"))
        self.mesh_applicability_label.setText(
            _tr(
                "Existing mesh is re-encoded without remeshing; tessellation "
                "settings are not applicable."
            )
        )
        self.encoding_combo.setItemText(0, _tr("Binary"))
        self.encoding_combo.setItemText(1, _tr("ASCII"))
        for index in range(self.format_combo.count()):
            format_id = ExportFormatId(str(self.format_combo.itemData(index)))
            capability = self._capabilities[format_id]
            suffix = "" if capability.available else f" — {_tr('Unavailable')}"
            self.format_combo.setItemText(index, f"{capability.label}{suffix}")
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setText(_tr("Export 3D file"))
        self._refresh_capability_state()

    @Slot(object)
    def _on_language_changed(self, _language: object) -> None:
        self.retranslate()

    @Slot()
    def _format_changed(self) -> None:
        if self._current_format is not None:
            try:
                self._remember_profile(
                    self._current_format,
                    self._profile_from_controls(self._current_format),
                )
            except (TypeError, ValueError):
                pass
        selected = self.selected_format
        self._current_format = selected
        profile = self._profiles.get(selected, ExportProfile.default_for(selected))
        self.standard_combo.clear()
        capability = self._capabilities[selected]
        for standard in capability.standards:
            self.standard_combo.addItem(standard, standard)
        if profile.standard is not None:
            self.standard_combo.setCurrentIndex(
                max(self.standard_combo.findData(profile.standard), 0)
            )
        if profile.mesh_options is not None:
            self.linear_deflection.setValue(profile.mesh_options.linear_deflection)
            self.angular_deflection.setValue(profile.mesh_options.angular_deflection)
            self.relative_mesh.setChecked(profile.mesh_options.relative)
        if profile.stl_encoding is not None:
            self.encoding_combo.setCurrentIndex(
                max(self.encoding_combo.findData(profile.stl_encoding.value), 0)
            )
        self.advanced_group.setVisible(selected is ExportFormatId.STL)
        tessellation_visible = (
            selected is ExportFormatId.STL and self._stl_tessellation_applicable
        )
        self._advanced_layout.setRowVisible(
            self.linear_deflection, tessellation_visible
        )
        self._advanced_layout.setRowVisible(
            self.angular_deflection, tessellation_visible
        )
        self._advanced_layout.setRowVisible(self.relative_mesh, tessellation_visible)
        self._advanced_layout.setRowVisible(
            self.mesh_applicability_label,
            selected is ExportFormatId.STL and not tessellation_visible,
        )
        self.standard_combo.setEnabled(bool(capability.standards))
        self._refresh_capability_state()

    def _refresh_capability_state(self) -> None:
        capability = self._capabilities[self.selected_format]
        if capability.available:
            self.reason_label.setText(
                _tr("Available") + f" — {capability.backend}"
            )
        else:
            reason = capability.unavailable_reason or _tr("Unavailable")
            self.reason_label.setText(_tr(reason))
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(capability.available)

    def _profile_from_controls(self, format_id: ExportFormatId) -> ExportProfile:
        capability = self._capabilities[format_id]
        stored = self._profiles.get(format_id, ExportProfile.default_for(format_id))
        standard = (
            str(self.standard_combo.currentData())
            if capability.standards and self.standard_combo.currentData() is not None
            else None
        )
        if format_id is ExportFormatId.STL:
            encoding = StlEncoding(str(self.encoding_combo.currentData()))
            if not self._stl_tessellation_applicable:
                return ExportProfile(
                    format_id,
                    stl_encoding=encoding,
                    overwrite_policy=stored.overwrite_policy,
                )
            mesh = StlMeshOptions(
                self.linear_deflection.value(),
                self.angular_deflection.value(),
                self.relative_mesh.isChecked(),
            )
            return ExportProfile(
                format_id,
                tolerance=mesh.linear_deflection,
                stl_encoding=encoding,
                mesh_options=mesh,
                overwrite_policy=stored.overwrite_policy,
            )
        return ExportProfile(
            format_id,
            standard=standard,
            overwrite_policy=stored.overwrite_policy,
        )

    def _remember_profile(
        self,
        format_id: ExportFormatId,
        effective: ExportProfile,
    ) -> None:
        if (
            format_id is ExportFormatId.STL
            and not self._stl_tessellation_applicable
        ):
            stored = self._profiles.get(
                format_id,
                ExportProfile.default_for(format_id),
            )
            if stored.mesh_options is None:
                stored = ExportProfile.default_for(
                    format_id,
                    overwrite_policy=stored.overwrite_policy,
                )
            self._profiles[format_id] = replace(
                stored,
                stl_encoding=effective.stl_encoding,
            )
            return
        self._profiles[format_id] = effective

    @Slot()
    def _accept_validated(self) -> None:
        try:
            self.profile()
        except (TypeError, ValueError) as error:
            self.validation_label.setText(f"{_tr('Validation error')}: {error}")
            return
        self.validation_label.clear()
        self.accept()


class CadExportUiController(QObject):
    """Own export actions, profile state, and one request-owned native worker."""

    message = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        window: QWidget,
        service: CadExportService,
        project_service: ProjectService,
        document_id: Callable[[], CadDocumentId | None],
        geometry_kind: Callable[[], CadGeometryKind | None],
        selection: Callable[[], tuple[SelectionMetadata, ...]],
        operation_available: Callable[[], bool],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._service = service
        self._project_service = project_service
        self._document_id = document_id
        self._geometry_kind = geometry_kind
        self._selection = selection
        self._operation_available = operation_available
        self._profiles = {
            item.format_id: ExportProfile.default_for(item.format_id)
            for item in service.capabilities()
        }
        self._thread_pool = QThreadPool.globalInstance()
        self._active_task: ProjectTask | None = None
        self.actions = {
            "export_3d": QAction(_tr("3D Export"), self),
            "export_selected": QAction(_tr("Export Selected Objects"), self),
        }
        self.actions["export_3d"].setObjectName("CadExport3dAction")
        self.actions["export_selected"].setObjectName("CadExportSelectedAction")
        self.actions["export_3d"].triggered.connect(self.export_document)
        self.actions["export_selected"].triggered.connect(self.export_selected)
        translation_service().language_changed.connect(self._on_language_changed)
        self.refresh_action_states()

    @property
    def profiles(self) -> dict[ExportFormatId, ExportProfile]:
        return dict(self._profiles)

    def retranslate(self) -> None:
        self.actions["export_3d"].setText(_tr("3D Export"))
        self.actions["export_selected"].setText(_tr("Export Selected Objects"))

    @Slot(object)
    def _on_language_changed(self, _language: object) -> None:
        self.retranslate()

    @Slot()
    def refresh_action_states(self) -> None:
        has_document = self._document_id() is not None
        idle = self._active_task is None and self._operation_available()
        self.actions["export_3d"].setEnabled(has_document and idle)
        self.actions["export_selected"].setEnabled(
            has_document and bool(self._selection()) and idle
        )

    @Slot()
    def export_document(self) -> None:
        self._interactive_export(selected=False)

    @Slot()
    def export_selected(self) -> None:
        if not self._selection():
            self._show_failure(_tr("No valid CAD selection is available for export."))
            return
        self._interactive_export(selected=True)

    def route_save_as(self, target: Path) -> bool:
        """Route a non-HMS Save As target to versioned CAD export."""
        from hms_cadcam.cad.export_models import capability_for_path

        capability = capability_for_path(target)
        if capability is None:
            self._show_failure(_tr("Unsupported CAD export extension."))
            return False
        runtime = self._service.capability(capability.format_id)
        if not runtime.available:
            self._show_failure(_tr(runtime.unavailable_reason or "Unavailable"))
            return False
        profile = self._request_profile(capability.format_id)
        if profile is None:
            return False
        return self._start_export(target, profile, selected=False)

    def _interactive_export(self, *, selected: bool) -> None:
        profile = self._request_profile(ExportFormatId.STEP)
        if profile is None:
            return
        capability = self._service.capability(profile.format_id)
        workspace = self._project_service.current_workspace
        parent = (
            Path.cwd()
            if workspace is None
            else workspace.suggested_save_directory
        )
        name = "model" if workspace is None else workspace.display_name
        suggestion = parent / f"{name}{capability.extensions[0]}"
        file_filter = (
            f"{capability.label} "
            f"({' '.join('*' + item for item in capability.extensions)})"
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self._window,
            _tr("3D Export"),
            str(suggestion),
            file_filter,
        )
        if not chosen:
            return
        target = Path(chosen)
        if not target.suffix:
            target = target.with_suffix(capability.extensions[0])
        elif target.suffix.casefold() not in capability.extensions:
            self._show_failure(
                _tr("Export profile format does not match the destination extension.")
            )
            return
        self._start_export(target, profile, selected=selected)

    def _request_profile(
        self, initial_format: ExportFormatId
    ) -> ExportProfile | None:
        dialog = CadExportProfileDialog(
            self._service.capabilities(),
            self._profiles,
            initial_format=initial_format,
            stl_tessellation_applicable=(
                self._geometry_kind() is not CadGeometryKind.MESH
            ),
            parent=self._window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        profile = dialog.profile()
        self._profiles.update(dialog.profiles)
        return profile

    def _start_export(
        self,
        target: Path,
        profile: ExportProfile,
        *,
        selected: bool,
    ) -> bool:
        document_id = self._document_id()
        if (
            document_id is None
            or self._active_task is not None
            or not self._operation_available()
        ):
            return False
        try:
            selections = self._selection_refs(document_id) if selected else ()
        except ValueError as error:
            self._show_failure(str(error))
            return False
        request_profile = replace(
            profile,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXISTING,
        )
        request = ExportRequest(
            document_id,
            target,
            request_profile,
            selections,
            ExportOverwritePolicy.REPLACE_EXISTING,
        )
        ownership = self._capture_ownership(document_id)
        if not self._operation_available() or not self._ownership_matches(ownership):
            return False
        task = ProjectTask(lambda: self._service.export(request))
        self._active_task = task
        task.signals.succeeded.connect(
            lambda value: self._export_succeeded(task, ownership, value)
        )
        task.signals.failed.connect(
            lambda error: self._export_failed(task, ownership, error)
        )
        task.signals.finished.connect(lambda: self._export_finished(task))
        self.busy_changed.emit(True)
        self.refresh_action_states()
        self.message.emit(_tr("Exporting 3D data…"))
        self._thread_pool.start(task)
        return True

    def _selection_refs(
        self, document_id: CadDocumentId
    ) -> tuple[ExportSelectionRef, ...]:
        current = self._selection()
        if not current:
            raise ValueError(_tr("No valid CAD selection is available for export."))
        refs: list[ExportSelectionRef] = []
        for item in current:
            kind = _SELECTION_KINDS.get(item.topology)
            if item.document_id != document_id or kind is None:
                raise ValueError(
                    _tr("The CAD selection is stale or its geometry kind is unsupported.")
                )
            refs.append(
                ExportSelectionRef(
                    document_id,
                    item.selection_id,
                    kind,
                    item.object_id,
                )
            )
        return tuple(refs)

    def _capture_ownership(self, document_id: CadDocumentId) -> _ExportOwnership:
        workspace = self._project_service.current_workspace
        return _ExportOwnership(
            None if workspace is None else workspace.identity,
            document_id,
        )

    def _ownership_matches(self, ownership: _ExportOwnership) -> bool:
        workspace = self._project_service.current_workspace
        project_identity = None if workspace is None else workspace.identity
        return (
            project_identity == ownership.project_identity
            and self._document_id() == ownership.document_id
        )

    def _export_succeeded(
        self,
        task: ProjectTask,
        ownership: _ExportOwnership,
        value: object,
    ) -> None:
        if self._active_task is not task or not self._ownership_matches(ownership):
            return
        if not isinstance(value, ExportResult):
            self._show_failure(_tr("Export failed"))
            return
        if value.success:
            self.message.emit(
                f"{_tr('3D export completed')}: {value.target_path} "
                f"({value.bytes_written} bytes, SHA-256 {value.sha256})"
            )
            return
        assert value.failure is not None
        self._show_failure(
            f"{_tr('Export failed')} [{value.failure.code.value}]: "
            f"{_tr(value.failure.message)}"
        )

    def _export_failed(
        self,
        task: ProjectTask,
        ownership: _ExportOwnership,
        error: object,
    ) -> None:
        if self._active_task is not task or not self._ownership_matches(ownership):
            return
        self._show_failure(f"{_tr('Export failed')}: {error}")

    def _export_finished(self, task: ProjectTask) -> None:
        if self._active_task is not task:
            return
        self._active_task = None
        self.busy_changed.emit(False)
        self.refresh_action_states()

    def _show_failure(self, text: str) -> None:
        self.message.emit(text)
        QMessageBox.warning(self._window, _tr("3D Export"), text)
