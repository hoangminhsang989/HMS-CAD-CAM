"""Production Post Processor workflow for the CAM workspace (7D.2.3).

The widget is deliberately a presentation/controller layer.  It consumes an
immutable :class:`PostSourceSnapshot`, builds a typed :class:`PostRequest`,
and delegates persistence to ``ProjectService``.  No UI field mutates CAM
domain state until the complete draft has passed validation and is applied.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.cam.domain import ArtifactStatus, ContentFingerprint, Length, LengthUnit
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.post import (
    ControllerToolBinding,
    CutterCompensationPolicy,
    ExportOverwritePolicy,
    ExportTarget,
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCExportDiagnostic,
    NCExportRequest,
    NCExportSourceSnapshot,
    PostDiagnostic,
    PostDiagnosticCode,
    PostRequest,
    PostResult,
    PostResultStatus,
    ProductionProgramContext,
    SimulationGateMode,
    SimulationGatePolicy,
    build_post_input_fingerprint,
    robodrill_21i_definition,
    robodrill_21i_profile,
    validate_post_source,
)
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.simulation import SimulationStatus
from hms_cadcam.cam.toolpath.model import ToolpathCompletionStatus
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.ui.localization import (
    LocalizedComboBox,
    localize_widget_tree,
    translate_status,
    ui_text,
)

logger = logging.getLogger(__name__)


class PostGenerationStatus(StrEnum):
    MISSING = "missing"
    VALIDATING = "validating"
    GENERATING = "generating"
    CURRENT = "current"
    STALE = "stale"
    FAILED = "failed"


class ManagedArtifactUiStatus(StrEnum):
    MISSING = "missing"
    CURRENT = "current"
    STALE = "stale"
    TAMPERED = "tampered"
    FAILED = "failed"


class ExternalExportUiStatus(StrEnum):
    NEVER_EXPORTED = "never_exported"
    EXPORTED = "exported"
    OUTDATED = "outdated"
    FAILED = "failed"


class PostProgressPhase(StrEnum):
    VALIDATING = "validating"
    GENERATING = "generating"
    VALIDATING_OUTPUT = "validating_output"
    WRITING = "writing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class PostPanelDraft:
    """User-editable Post fields, independent from the domain snapshot."""

    profile_key: str
    file_name: str
    program_identity: str | None
    safe_z: float | None
    work_offset: str
    tool_station: int
    length_offset: int
    diameter_offset: int | None
    tool_comment: str
    cutter_compensation: CutterCompensationPolicy
    simulation_gate: SimulationGateMode
    overwrite_policy: ExportOverwritePolicy
    target_directory: Path | None = None
    target_kind: ExportTarget = ExportTarget.FILESYSTEM_DIRECTORY
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported Post panel draft version")
        if not isinstance(self.profile_key, str) or not self.profile_key:
            raise ValueError("Profile is required")
        if not isinstance(self.file_name, str):
            raise ValueError("Filename must be text")
        if self.safe_z is not None and (not isinstance(self.safe_z, (int, float)) or not math.isfinite(float(self.safe_z))):
            raise ValueError("Safe Z must be finite")
        if self.work_offset not in {"G54"}:
            raise ValueError("Profile v1 supports G54 only")
        for value, name in ((self.tool_station, "T"), (self.length_offset, "H")):
            if type(value) is not int or not 1 <= value <= 9999:
                raise ValueError(f"{name} offset is out of range")
        if self.diameter_offset is not None and (type(self.diameter_offset) is not int or not 1 <= self.diameter_offset <= 9999):
            raise ValueError("D offset is out of range")
        try:
            object.__setattr__(self, "cutter_compensation", CutterCompensationPolicy(self.cutter_compensation))
            object.__setattr__(self, "simulation_gate", SimulationGateMode(self.simulation_gate))
            object.__setattr__(self, "overwrite_policy", ExportOverwritePolicy(self.overwrite_policy))
        except (TypeError, ValueError) as error:
            raise ValueError("Post policy is invalid") from error
        if self.target_directory is not None and not isinstance(self.target_directory, Path):
            raise ValueError("Target directory must be a pathlib.Path")
        try:
            object.__setattr__(self, "target_kind", ExportTarget(self.target_kind))
        except (TypeError, ValueError) as error:
            raise ValueError("Export target is invalid") from error


@dataclass(frozen=True, slots=True)
class PostPanelState:
    """Read-only projection shown in the panel and useful for tests/logging."""

    project_id: object | None = None
    operation_id: object | None = None
    operation_name: str = ""
    strategy_key: str = ""
    operation_status: str = "MISSING"
    artifact_id: object | None = None
    artifact_status: str = "MISSING"
    artifact_fingerprint: str | None = None
    simulation_status: str = "MISSING"
    simulation_fingerprint: str | None = None
    profile_key: str = "robodrill_fanuc_21i_worknc_expanded_v1"
    profile_version: int = 1
    machine: str = ""
    tool: str = ""
    holder: str = ""
    binding_fingerprint: str | None = None
    post_status: PostGenerationStatus = PostGenerationStatus.MISSING
    managed_status: ManagedArtifactUiStatus = ManagedArtifactUiStatus.MISSING
    external_status: ExternalExportUiStatus = ExternalExportUiStatus.NEVER_EXPORTED
    output_checksum: str | None = None
    output_bytes: int = 0


class _PostWorker(QObject):
    completed = Signal(object)

    def __init__(self, runtime, request: PostRequest, source: PostSourceSnapshot, current_source: Callable[[], PostSourceSnapshot], epoch: int):
        super().__init__()
        self._runtime = runtime
        self._request = request
        self._source = source
        self._current_source = current_source
        self._epoch = epoch

    def run(self) -> None:
        try:
            execution = self._runtime.post(
                self._request,
                self._source,
                current_source=self._current_source,
            )
        except Exception as error:  # worker boundary: convert to a stable result
            execution = error
        self.completed.emit((self._epoch, execution))


def sanitize_post_filename(value: str, *, extension: str = ".fn") -> str:
    """Normalize one UI filename and reject traversal/device/double extension."""
    if not isinstance(value, str):
        raise ValueError("Filename must be text")
    name = value.strip()
    if not name:
        raise ValueError("Filename is required")
    if any(ord(char) < 32 or char in '/\\' for char in name):
        raise ValueError("Filename contains an invalid character")
    if name in {".", ".."} or ".." in name:
        raise ValueError("Filename traversal is not allowed")
    stem, suffix = Path(name).stem, Path(name).suffix.casefold()
    if suffix and suffix != extension.casefold():
        raise ValueError("Filename extension is not supported")
    if suffix == extension.casefold() and Path(stem).suffix.casefold() in {".nc", ".gcode", ".tap", extension.casefold()}:
        raise ValueError("Double extension is not allowed")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if stem.rstrip(" .").casefold() in reserved:
        raise ValueError("Windows device name is not allowed")
    if not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", name):
        raise ValueError("Filename contains unsupported characters")
    return name if suffix == extension.casefold() else name + extension


def _tool_radius(source: PostSourceSnapshot) -> float:
    if source.tool is None:
        return 0.0
    dimensions = getattr(source.tool.cutting_geometry, "dimensions", ())
    if not dimensions:
        return 0.0
    return max(float(dimensions[0].value) / 2.0, 0.0)


def build_production_post_request(source: PostSourceSnapshot, draft: PostPanelDraft) -> PostRequest:
    """Build and validate a production request from an applied UI draft."""
    profile = robodrill_21i_profile()
    if draft.profile_key != profile.profile_key:
        raise ValueError("Unknown production profile")
    filename = sanitize_post_filename(draft.file_name, extension=profile.allowed_extensions[0])
    if draft.safe_z is None or draft.safe_z <= 0.0:
        raise ValueError("Safe Z must be explicit and positive")
    if source.artifact.unit not in profile.supported_units:
        raise ValueError("Profile does not support the Toolpath unit")
    if source.setup.work_offset.name.upper() != "PRIMARY" or source.setup.work_offset.numeric_slot != 1 or draft.work_offset != "G54":
        raise ValueError("Production profile v1 supports G54 only")
    use_legacy = draft.cutter_compensation is CutterCompensationPolicy.LEGACY_WORKNC_LEFT
    if use_legacy and source.operation.strategy_key != "contour_2d":
        raise ValueError("Legacy G41 is supported for contour milling only")
    if use_legacy and draft.diameter_offset is None:
        raise ValueError("Legacy G41 requires a D offset")
    binding = ControllerToolBinding(
        source.assembly.content_fingerprint,
        draft.tool_station,
        draft.length_offset,
        draft.diameter_offset if use_legacy else None,
        draft.tool_comment or source.tool.name if source.tool else draft.tool_comment,
    )
    unit = source.artifact.unit
    context = ProductionProgramContext(
        filename,
        Length(float(draft.safe_z), unit),
        binding,
        Length(_tool_radius(source), unit),
        Length(0.0, unit),
        Length(0.0, unit),
        use_legacy,
        draft.program_identity,
    )
    return PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        robodrill_21i_definition(),
        simulation_gate_policy=SimulationGatePolicy(draft.simulation_gate),
        program_context=context,
    )


class PostProcessorPanel(QWidget):
    """Production Post panel with explicit Generate/Save/Export boundaries."""

    message = Signal(str)
    draft_changed = Signal(object)
    state_changed = Signal(object)
    progress_changed = Signal(object)

    def __init__(self, service: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CamPostProcessorPanel")
        self._service = service
        self._source: PostSourceSnapshot | None = None
        self._operation_id = None
        self._operation_name = ""
        self._generation: int | None = None
        self._request_epoch = 0
        self._draft: PostPanelDraft | None = None
        self._applied: PostPanelDraft | None = None
        self._request: PostRequest | None = None
        self._result: PostResult | None = None
        self._post_stale_hint = False
        self._last_export = None
        self._active_thread: QThread | None = None
        self._active_worker: _PostWorker | None = None
        self.state = PostPanelState()
        self._build_ui()
        self._set_enabled(False)
        localize_widget_tree(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Post Processor · Production Workflow 7D.2.3")
        title.setObjectName("PostPanelTitle")
        root.addWidget(title)
        self.status_label = QLabel("MISSING")
        root.addWidget(self.status_label)

        source_group = QGroupBox("Operation / provenance")
        source_form = QFormLayout(source_group)
        self.project_value = QLabel("—")
        self.operation_value = QLabel("—")
        self.setup_value = QLabel("—")
        self.source_value = QLabel("—")
        self.simulation_value = QLabel("—")
        self.machine_value = QLabel("—")
        self.tool_value = QLabel("—")
        for label, widget in (("Project", self.project_value), ("Job / Setup", self.setup_value), ("Operation", self.operation_value), ("ToolpathArtifact", self.source_value), ("Simulation", self.simulation_value), ("Machine", self.machine_value), ("Tool / Holder", self.tool_value)):
            source_form.addRow(label, widget)
        root.addWidget(source_group)

        profile_group = QGroupBox("Production profile")
        profile_form = QFormLayout(profile_group)
        self.profile_combo = LocalizedComboBox()
        profile = robodrill_21i_profile()
        self.profile_combo.addItem(f"{profile.profile_key} v{profile.profile_version}", profile.profile_key)
        self.profile_combo.setToolTip("Profile immutable; canonical production output is separate from dummy output")
        profile_form.addRow("Profile", self.profile_combo)
        self.profile_info = QLabel(f"{profile.controller_family} {profile.controller_model} · {profile.machine_family} · MM / G54 / XYZ · {profile.allowed_extensions[0]} · CRLF")
        self.profile_info.setWordWrap(True)
        profile_form.addRow("Hợp đồng", self.profile_info)
        root.addWidget(profile_group)

        binding_group = QGroupBox("Controller Tool Binding")
        binding_form = QFormLayout(binding_group)
        self.tool_station_spin = QSpinBox(); self.tool_station_spin.setRange(1, 9999)
        self.length_offset_spin = QSpinBox(); self.length_offset_spin.setRange(1, 9999)
        self.diameter_offset_spin = QSpinBox(); self.diameter_offset_spin.setRange(1, 9999)
        self.diameter_offset_spin.setSpecialValueText("(none)")
        self.tool_comment_edit = QLineEdit()
        binding_form.addRow("T station", self.tool_station_spin)
        binding_form.addRow("H length offset", self.length_offset_spin)
        binding_form.addRow("D diameter offset", self.diameter_offset_spin)
        binding_form.addRow("Comment", self.tool_comment_edit)
        self.binding_fingerprint = QLabel("—")
        binding_form.addRow("Fingerprint", self.binding_fingerprint)
        root.addWidget(binding_group)

        context_group = QGroupBox("Program context")
        context_form = QFormLayout(context_group)
        self.filename_edit = QLineEdit("PROGRAM.fn")
        self.identity_edit = QLineEdit()
        self.safe_z_spin = QDoubleSpinBox(); self.safe_z_spin.setRange(-1_000_000.0, 1_000_000.0); self.safe_z_spin.setDecimals(4); self.safe_z_spin.setSpecialValueText("(missing)"); self.safe_z_spin.setValue(0.0)
        self.work_offset_combo = LocalizedComboBox(); self.work_offset_combo.addItem("G54", "G54")
        self.cutter_combo = LocalizedComboBox()
        for value, label in ((CutterCompensationPolicy.DISABLED, "DISABLED"), (CutterCompensationPolicy.LEGACY_WORKNC_LEFT, "LEGACY_WORKNC_LEFT (G41)"), (CutterCompensationPolicy.FROM_PROGRAM_IR_ONLY, "FROM_PROGRAM_IR_ONLY")):
            self.cutter_combo.addItem(label, value)
        self.gate_combo = LocalizedComboBox()
        for value, label in ((SimulationGateMode.REQUIRE_PASS, "REQUIRE_PASS"), (SimulationGateMode.ALLOW_WARN, "ALLOW_WARN"), (SimulationGateMode.OPTIONAL, "OPTIONAL")):
            self.gate_combo.addItem(label, value)
        self.overwrite_combo = LocalizedComboBox()
        for value, label in ((ExportOverwritePolicy.FAIL_IF_EXISTS, "FAIL_IF_EXISTS"), (ExportOverwritePolicy.REPLACE_IF_SAME_ARTIFACT, "REPLACE_IF_SAME_ARTIFACT"), (ExportOverwritePolicy.REPLACE_EXPLICIT, "REPLACE_EXPLICIT")):
            self.overwrite_combo.addItem(label, value)
        context_form.addRow("Filename", self.filename_edit)
        context_form.addRow("File/comment metadata", self.identity_edit)
        context_form.addRow("Safe Z (MM)", self.safe_z_spin)
        context_form.addRow("Work offset", self.work_offset_combo)
        context_form.addRow("Cutter compensation", self.cutter_combo)
        context_form.addRow("Simulation gate", self.gate_combo)
        context_form.addRow("Overwrite", self.overwrite_combo)
        self.target_kind_combo = LocalizedComboBox()
        self.target_kind_combo.addItem("Local / mapped / UNC filesystem", ExportTarget.FILESYSTEM_DIRECTORY)
        self.target_kind_combo.addItem("Data-server directory", ExportTarget.DATA_SERVER_DIRECTORY)
        context_form.addRow("External target type", self.target_kind_combo)
        self.target_edit = QLineEdit(); self.target_edit.setPlaceholderText("External local/mapped/UNC directory (optional)")
        browse = QPushButton("Browse…"); browse.clicked.connect(self._browse_target)
        target_row = QHBoxLayout(); target_row.addWidget(self.target_edit); target_row.addWidget(browse)
        context_form.addRow("External target", target_row)
        root.addWidget(context_group)

        apply_row = QHBoxLayout()
        self.apply_button = QPushButton("Apply draft")
        self.reset_button = QPushButton("Reset draft")
        apply_row.addWidget(self.apply_button); apply_row.addWidget(self.reset_button)
        root.addLayout(apply_row)
        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Validate")
        self.generate_button = QPushButton("Generate Post")
        self.preview_button = QPushButton("Preview")
        self.save_button = QPushButton("Save Managed Artifact")
        self.export_button = QPushButton("Export")
        for button in (self.validate_button, self.generate_button, self.preview_button, self.save_button, self.export_button):
            action_row.addWidget(button)
        root.addLayout(action_row)
        second_row = QHBoxLayout()
        self.export_details_button = QPushButton("Show Export Details")
        self.clear_post_button = QPushButton("Clear Post Result")
        self.clear_managed_button = QPushButton("Clear Managed Artifact")
        for button in (self.export_details_button, self.clear_post_button, self.clear_managed_button):
            second_row.addWidget(button)
        root.addLayout(second_row)

        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True); self.preview.setObjectName("ProductionNcPreview")
        root.addWidget(self.preview, 2)
        self.metadata_label = QLabel("Preview metadata: —"); self.metadata_label.setWordWrap(True)
        root.addWidget(self.metadata_label)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Diagnostics"))
        from_filter = LocalizedComboBox()
        for label in ("ALL", "ERROR", "WARNING", "INFO"):
            from_filter.addItem(label, label)
        self.diagnostic_filter = from_filter
        filter_row.addWidget(from_filter)
        filter_row.addStretch(1)
        root.addLayout(filter_row)
        self.diagnostics = QTableWidget(0, 6); self.diagnostics.setHorizontalHeaderLabels(["Severity", "Code", "Message", "Record", "Evidence", "Source"]); self.diagnostics.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.diagnostics, 1)
        self._diagnostic_values: tuple[object, ...] = ()

        self.apply_button.clicked.connect(self.apply_draft)
        self.reset_button.clicked.connect(self.reset_draft)
        self.validate_button.clicked.connect(self.validate_request)
        self.generate_button.clicked.connect(self.generate)
        self.preview_button.clicked.connect(self.show_preview)
        self.save_button.clicked.connect(self.save_managed_artifact)
        self.export_button.clicked.connect(self.export_external)
        self.export_details_button.clicked.connect(self.show_export_details)
        self.clear_post_button.clicked.connect(self.clear_post_result)
        self.clear_managed_button.clicked.connect(lambda: self.clear_managed_artifact())
        self.diagnostic_filter.currentIndexChanged.connect(self._render_diagnostics)
        for widget in (self.filename_edit, self.identity_edit, self.tool_comment_edit, self.target_edit):
            widget.textChanged.connect(self._draft_edited)
        for widget in (self.tool_station_spin, self.length_offset_spin, self.diameter_offset_spin, self.safe_z_spin):
            widget.valueChanged.connect(self._draft_edited)
        for widget in (self.profile_combo, self.work_offset_combo, self.cutter_combo, self.gate_combo, self.overwrite_combo, self.target_kind_combo):
            widget.currentIndexChanged.connect(self._draft_edited)

    def _set_enabled(self, enabled: bool) -> None:
        for widget in (self.profile_combo, self.tool_station_spin, self.length_offset_spin, self.diameter_offset_spin, self.tool_comment_edit, self.filename_edit, self.identity_edit, self.safe_z_spin, self.work_offset_combo, self.cutter_combo, self.gate_combo, self.overwrite_combo, self.target_edit, self.apply_button, self.reset_button, self.validate_button, self.generate_button, self.preview_button, self.save_button, self.export_button, self.export_details_button, self.clear_post_button, self.clear_managed_button):
            widget.setEnabled(enabled)
        if enabled:
            self._update_action_enabled()

    def bind_project(self, session: object) -> None:
        self._cancel_worker()
        self._request_epoch += 1
        self._source = None; self._operation_id = None; self._generation = None
        self._operation_name = ""
        self._draft = None; self._applied = None; self._request = None; self._result = None; self._last_export = None
        self._post_stale_hint = False
        self.state = PostPanelState()
        self.preview.clear(); self._show_diagnostics(())
        self._set_enabled(False)

    def set_operation(self, operation_id: object, *, generation: int | None = None, operation_name: str | None = None) -> None:
        previous_operation_id = self._operation_id
        previous_result = self._result
        previous_source = self._source
        self._cancel_worker()
        self._request_epoch += 1
        self._operation_id = operation_id
        self._operation_name = operation_name or ""
        self._generation = generation
        try:
            source = self._service.capture_post_source(operation_id)
        except Exception as error:
            self._source = None; self._draft = None; self._applied = None; self._request = None; self._result = None
            self._set_enabled(False); self._set_status(PostGenerationStatus.MISSING)
            self._show_diagnostics(())
            self.message.emit(f"Nguồn Post không khả dụng: {ui_text(error)}")
            return
        self._source = source
        self._post_stale_hint = bool(
            previous_result is not None
            and previous_operation_id == operation_id
            and previous_source is not None
            and (
                source.artifact.artifact_id != previous_result.artifact_id
                or source.artifact.artifact_fingerprint != previous_result.artifact_fingerprint
            )
        )
        self._draft = self._default_draft(source)
        self._applied = self._draft
        self._apply_widgets(self._draft)
        self._set_enabled(True)
        self._refresh_state()

    def clear_operation(self) -> None:
        self.bind_project(None)

    def _default_draft(self, source: PostSourceSnapshot) -> PostPanelDraft:
        name = source.operation.strategy_key.replace("_v1", "").replace("_", "-").upper() or "PROGRAM"
        tool_name = source.tool.name if source.tool is not None else "Tool"
        return PostPanelDraft(robodrill_21i_profile().profile_key, f"{name}.fn", None, None, "G54", 1, 1, 1, tool_name, CutterCompensationPolicy.DISABLED, SimulationGateMode.REQUIRE_PASS, ExportOverwritePolicy.FAIL_IF_EXISTS)

    def _draft_from_widgets(self) -> PostPanelDraft:
        target = Path(self.target_edit.text().strip()) if self.target_edit.text().strip() else None
        diameter = self.diameter_offset_spin.value() or None
        return PostPanelDraft(
            str(self.profile_combo.currentData()), self.filename_edit.text(), self.identity_edit.text().strip() or None,
            None if self.safe_z_spin.value() == 0.0 else self.safe_z_spin.value(), str(self.work_offset_combo.currentData()),
            self.tool_station_spin.value(), self.length_offset_spin.value(), diameter, self.tool_comment_edit.text(),
            CutterCompensationPolicy(self.cutter_combo.currentData()), SimulationGateMode(self.gate_combo.currentData()), ExportOverwritePolicy(self.overwrite_combo.currentData()), target, ExportTarget(self.target_kind_combo.currentData()),
        )

    def _apply_widgets(self, draft: PostPanelDraft) -> None:
        for combo, value in ((self.profile_combo, draft.profile_key), (self.work_offset_combo, draft.work_offset), (self.cutter_combo, draft.cutter_compensation), (self.gate_combo, draft.simulation_gate), (self.overwrite_combo, draft.overwrite_policy), (self.target_kind_combo, draft.target_kind)):
            index = combo.findData(value); combo.setCurrentIndex(max(index, 0))
        self.filename_edit.setText(draft.file_name); self.identity_edit.setText(draft.program_identity or "")
        self.safe_z_spin.setValue(draft.safe_z or 0.0); self.tool_station_spin.setValue(draft.tool_station); self.length_offset_spin.setValue(draft.length_offset); self.diameter_offset_spin.setValue(draft.diameter_offset or 0); self.tool_comment_edit.setText(draft.tool_comment); self.target_edit.setText(str(draft.target_directory) if draft.target_directory else "")
        self._update_binding_display(draft)

    def _draft_edited(self, *_args) -> None:
        if self._source is None:
            return
        try:
            self._draft = self._draft_from_widgets()
            self._update_binding_display(self._draft)
            self.draft_changed.emit(self._draft)
            self._update_action_enabled()
        except ValueError as error:
            self.message.emit(f"Tham số Post không hợp lệ: {ui_text(error)}")

    def reset_draft(self) -> None:
        if self._applied is not None:
            self._draft = self._applied
            self._apply_widgets(self._draft)

    def apply_draft(self) -> bool:
        if self._source is None:
            return False
        try:
            draft = self._draft_from_widgets()
            request = build_production_post_request(self._source, draft)
        except Exception as error:
            self.message.emit(f"Bản nháp Post không hợp lệ: {ui_text(error)}")
            return False
        old = self._applied
        self._applied = draft
        self._draft = draft
        self._request = request
        self._result = None
        self._service.post_service.mark_stale(self._operation_id)
        self._service.nc_export_service.mark_operation_stale(self._operation_id)
        self._update_binding_display(draft)
        self._refresh_state()
        self.draft_changed.emit(draft)
        self.message.emit("Đã áp dụng toàn vẹn bản nháp Post; cần tạo lại kết quả.")
        self._update_action_enabled()
        return True

    def _ensure_request(self) -> PostRequest | None:
        if self._source is None or self._applied is None:
            return None
        try:
            self._request = build_production_post_request(self._source, self._applied)
            return self._request
        except Exception as error:
            self.message.emit(f"Yêu cầu Post không hợp lệ: {ui_text(error)}")
            return None

    def validate_request(self) -> tuple[PostDiagnostic, ...]:
        request = self._ensure_request()
        source = self._source
        if request is None or source is None:
            return ()
        diagnostics = list(validate_post_source(source, request.simulation_gate_policy))
        if source.operation.strategy_key == "tapping_v1":
            diagnostics.append(PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.fanuc.tapping_unsupported", source.operation.operation_id, source.artifact.artifact_id))
        try:
            from hms_cadcam.cam.post.fanuc_robodrill_21i import FanucRobodrill21iAdapter
            diagnostics.extend(FanucRobodrill21iAdapter(request.post_definition).validate_request(request))
        except Exception as error:
            self.message.emit(f"Kiểm tra Post thất bại: {ui_text(error)}")
        self._show_diagnostics(tuple(sorted(set(diagnostics), key=lambda item: (item.severity.value, item.code.value, item.message_key))))
        self._set_status(PostGenerationStatus.CURRENT if not any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics) else PostGenerationStatus.FAILED)
        return tuple(diagnostics)

    def generate(self) -> None:
        request = self._ensure_request()
        source = self._source
        if request is None or source is None or self._active_thread is not None:
            return
        if self._generation is not None and self._generation != self._service.cam_generation:
            self._set_status(PostGenerationStatus.STALE)
            return
        self._set_status(PostGenerationStatus.VALIDATING)
        self.progress_changed.emit(PostProgressPhase.VALIDATING)
        self._set_status(PostGenerationStatus.GENERATING)
        self.progress_changed.emit(PostProgressPhase.GENERATING)
        thread = QThread(self)
        operation_id = source.operation.operation_id
        worker = _PostWorker(
            self._service.post_service,
            request,
            source,
            lambda operation_id=operation_id: self._service.capture_post_source(operation_id),
            self._request_epoch,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._generation_completed)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._worker_finished)
        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _worker_finished(self) -> None:
        thread = self._active_thread
        self._active_thread = None
        self._active_worker = None
        if thread is not None:
            thread.deleteLater()
        self._update_action_enabled()

    def _generation_completed(self, execution: object) -> None:
        if isinstance(execution, tuple) and len(execution) == 2:
            epoch, execution = execution
            if epoch != self._request_epoch:
                return
        if isinstance(execution, Exception):
            self.message.emit(f"Tạo Post thất bại: {ui_text(execution)}")
            self._set_status(PostGenerationStatus.FAILED)
            self.progress_changed.emit(PostProgressPhase.FAILED)
            return
        if not getattr(execution, "accepted", False) or getattr(execution, "result", None) is None:
            diagnostics = getattr(execution, "diagnostics", ())
            self._show_diagnostics(diagnostics)
            stale = getattr(execution, "status", None) is PostResultStatus.STALE
            self._set_status(PostGenerationStatus.STALE if stale else PostGenerationStatus.FAILED)
            self.progress_changed.emit(PostProgressPhase.STALE if stale else PostProgressPhase.FAILED)
            return
        if self._generation is not None and self._generation != self._service.cam_generation:
            self._set_status(PostGenerationStatus.STALE)
            self.progress_changed.emit(PostProgressPhase.STALE)
            return
        self._result = execution.result
        self._post_stale_hint = False
        self._refresh_state()
        self._show_diagnostics(execution.diagnostics)
        self._set_status(PostGenerationStatus.CURRENT)
        self.progress_changed.emit(PostProgressPhase.COMPLETED)
        self.show_preview()
        self.message.emit("Kết quả Post sản xuất đã công bố; chưa ghi tệp.")

    def generate_sync(self) -> PostResult | None:
        request = self._ensure_request(); source = self._source
        if request is None or source is None:
            return None
        if self._generation is not None and self._generation != self._service.cam_generation:
            self._set_status(PostGenerationStatus.STALE); return None
        self._set_status(PostGenerationStatus.VALIDATING); self.progress_changed.emit(PostProgressPhase.VALIDATING)
        self._set_status(PostGenerationStatus.GENERATING); self.progress_changed.emit(PostProgressPhase.GENERATING)
        try:
            operation_id = source.operation.operation_id
            execution = self._service.post_service.post(request, source, current_source=lambda: self._service.capture_post_source(operation_id))
        except Exception as error:
            self.message.emit(f"Tạo Post thất bại: {ui_text(error)}"); self._set_status(PostGenerationStatus.FAILED); return None
        if not execution.accepted or execution.result is None:
            self._show_diagnostics(execution.diagnostics); self._set_status(PostGenerationStatus.STALE if execution.status is PostResultStatus.STALE else PostGenerationStatus.FAILED); self.progress_changed.emit(PostProgressPhase.STALE if execution.status is PostResultStatus.STALE else PostProgressPhase.FAILED); return None
        if self._generation is not None and self._generation != self._service.cam_generation:
            self._set_status(PostGenerationStatus.STALE); self.progress_changed.emit(PostProgressPhase.STALE); return None
        self._result = execution.result
        self._post_stale_hint = False
        self._refresh_state()
        self._show_diagnostics(execution.diagnostics)
        self._set_status(PostGenerationStatus.CURRENT); self.progress_changed.emit(PostProgressPhase.COMPLETED)
        self.show_preview()
        self.message.emit("Kết quả Post sản xuất đã công bố; chưa ghi tệp.")
        return self._result

    def show_preview(self) -> None:
        if self._result is None:
            return
        text = self._result.canonical_text
        self.preview.setPlainText(text)
        payload = text.encode("utf-8")
        newline = "CRLF" if "\r\n" in text and "\n" not in text.replace("\r\n", "") else "LF"
        checksum = hashlib.sha256(payload).hexdigest()
        self.metadata_label.setText(
            f"Cấu hình {(self._request.post_definition.production_profile.profile_key if self._request and self._request.post_definition.production_profile else '—')} · "
            f"{len(text.splitlines())} dòng · {len(payload)} byte · {newline} · "
            f"UTF-8 · SHA-256 {checksum} · CHƯA CHỨNG NHẬN / CẦN RÀ SOÁT"
        )

    def _build_export(self, target: ExportTarget, directory: Path | None = None) -> tuple[NCExportRequest, NCExportSourceSnapshot] | None:
        if self._source is None or self._result is None or self._request is None or self._applied is None:
            return None
        if self._result.status is not PostResultStatus.PUBLISHED:
            return None
        return (
            NCExportRequest(self._source.project_id, self._source.operation.operation_id, self._source.artifact.artifact_id, self._result.result_id, self._request.program_context.file_name, target, self._applied.overwrite_policy, False, target_directory=directory),
            NCExportSourceSnapshot(self._service.cam_generation, self._request, self._result, self._source),
        )

    def save_managed_artifact(self) -> object | None:
        if self._result is None or self._set_status_if_stale():
            return None
        export = self._build_export(ExportTarget.PROJECT_MANAGED)
        if export is None:
            return None
        self.progress_changed.emit(PostProgressPhase.WRITING)
        try:
            execution = self._service.export_nc(
                export[0],
                export[1],
                current_source=self._current_export_source,
            )
        except Exception as error:
            self.message.emit(f"Xuất kết quả được quản lý thất bại: {ui_text(error)}"); self._set_status(PostGenerationStatus.FAILED); return None
        self._last_export = execution
        self.progress_changed.emit(PostProgressPhase.VERIFYING)
        self._show_export_diagnostics(execution.diagnostics)
        self._refresh_state()
        return execution.result

    def export_external(self) -> object | None:
        if self._source is None or self._result is None or self._applied is None:
            return None
        directory = self._applied.target_directory
        if directory is None:
            directory_text = self.target_edit.text().strip()
            directory = Path(directory_text) if directory_text else None
        if directory is None:
            self.message.emit("Chọn thư mục cục bộ/ánh xạ/UNC trước khi xuất."); return None
        if self.parent() is not None:
            answer = QMessageBox.question(
                self,
                "Xác nhận xuất",
                f"Xuất chính xác nội dung sản xuất đến:\n{directory}\n\n"
                "CHƯA ĐƯỢC CHỨNG NHẬN CHO MÁY",
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return None
        export = self._build_export(self._applied.target_kind, directory)
        if export is None:
            return None
        try:
            execution = self._service.export_nc(
                export[0],
                export[1],
                current_source=self._current_export_source,
            )
        except Exception as error:
            self.message.emit(f"Xuất ra ngoài thất bại: {ui_text(error)}"); return None
        self._last_export = execution; self._show_export_diagnostics(execution.diagnostics); self._refresh_state(); return execution.result

    def _current_export_source(self) -> NCExportSourceSnapshot:
        """Re-capture source/result for NCExportService's pre/post-write guard."""
        if self._request is None or self._operation_id is None:
            raise ProjectError("Post export source is unavailable")
        source = self._service.capture_post_source(self._operation_id)
        result = self._service.post_service.current(self._request)
        if result is None:
            raise ProjectError("Post result is no longer current")
        return NCExportSourceSnapshot(self._service.cam_generation, self._request, result, source)

    def show_export_details(self) -> None:
        if self._last_export is None:
            self.message.emit("Chưa có kết quả xuất ra ngoài hoặc được quản lý."); return
        result = self._last_export.result
        if result is None:
            self.message.emit("Xuất thất bại; kết quả được quản lý vẫn được giữ nếu có."); return
        self.message.emit(
            f"Xuất {translate_status(result.status)}: "
            f"{result.project_managed_relative_path} · "
            f"{result.byte_length} byte · SHA-256 {result.sha256}"
        )

    def clear_post_result(self) -> None:
        if self._operation_id is None:
            return
        self._service.post_service.mark_stale(self._operation_id)
        self._result = None; self.preview.clear(); self._show_diagnostics(())
        self._set_status(PostGenerationStatus.MISSING); self._refresh_state()

    def clear_managed_artifact(self, confirm: bool | None = None) -> None:
        if self._operation_id is None or not hasattr(self._service, "current_project") or self._service.current_project is None:
            return
        if confirm is None:
            answer = QMessageBox.question(
                self,
                "Xóa kết quả được quản lý",
                "Chỉ xóa kết quả NC do dự án quản lý và tệp kèm?",
            )
            confirm = answer is QMessageBox.StandardButton.Yes
        if not confirm:
            return
        try:
            project = self._service.current_project
            self._service.nc_export_service.clear_managed_artifact(project.root_path, project.manifest.project_id, self._operation_id)
            self._refresh_state()
        except Exception as error:
            self.message.emit(f"Xóa kết quả được quản lý thất bại: {ui_text(error)}")

    def _set_status_if_stale(self) -> bool:
        if self._generation is not None and self._generation != self._service.cam_generation:
            self._set_status(PostGenerationStatus.STALE); return True
        return False

    def _browse_target(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất")
        if directory:
            self.target_edit.setText(directory)

    def _update_binding_display(self, draft: PostPanelDraft) -> None:
        if self._source is None:
            self.binding_fingerprint.setText("—"); return
        binding = ControllerToolBinding(self._source.assembly.content_fingerprint, draft.tool_station, draft.length_offset, draft.diameter_offset if draft.cutter_compensation is CutterCompensationPolicy.LEGACY_WORKNC_LEFT else None, draft.tool_comment or "Tool")
        self.binding_fingerprint.setText(binding.fingerprint.digest)
        self.diameter_offset_spin.setEnabled(draft.cutter_compensation is CutterCompensationPolicy.LEGACY_WORKNC_LEFT)

    def _refresh_state(self) -> None:
        source = self._source
        if source is None:
            return
        result = self._result
        if result is None and self._request is not None:
            candidate = self._service.post_service.current(self._request)
            try:
                if candidate is not None and candidate.input_fingerprint == build_post_input_fingerprint(self._request, source):
                    result = candidate; self._result = candidate
            except Exception:
                result = None
        managed = next((entry for entry in self._service.nc_export_service.artifacts() if entry.operation_id == source.operation.operation_id), None)
        status = ManagedArtifactUiStatus.MISSING if managed is None else ManagedArtifactUiStatus(managed.status.value)
        post_status = PostGenerationStatus.STALE if self._post_stale_hint else (PostGenerationStatus.CURRENT if result is not None and result.status is PostResultStatus.PUBLISHED else (self.state.post_status if self.state.post_status in {PostGenerationStatus.GENERATING, PostGenerationStatus.VALIDATING} else PostGenerationStatus.MISSING))
        sim = source.simulation_result
        sim_status = "MISSING" if sim is None else (
            "STALE" if sim.operation_id != source.operation.operation_id
            or sim.artifact_id != source.artifact.artifact_id
            or sim.artifact_fingerprint != source.artifact.artifact_fingerprint
            else sim.status.value.upper()
        )
        profile = robodrill_21i_profile()
        self.state = PostPanelState(source.project_id, source.operation.operation_id, self._operation_name, source.operation.strategy_key, source.operation.artifact_state.status.value.upper(), source.artifact.artifact_id, source.artifact.completion_status.value.upper(), source.artifact.artifact_fingerprint.digest if source.artifact.artifact_fingerprint else None, sim_status, sim.result_fingerprint.digest if sim else None, profile.profile_key, profile.profile_version, source.machine.name if source.machine else "MISSING", source.tool.name if source.tool else "MISSING", source.holder.name if source.holder else "MISSING", self.binding_fingerprint.text() if self.binding_fingerprint.text() != "—" else None, post_status, status, ExternalExportUiStatus.EXPORTED if self._last_export and getattr(self._last_export, "accepted", False) else ExternalExportUiStatus.NEVER_EXPORTED, result.output_checksum if result else None, len(result.canonical_text.encode("utf-8")) if result else 0)
        self.project_value.setText(str(source.project_id)); self.setup_value.setText(source.setup.name); self.operation_value.setText(f"{self._operation_name or 'Nguyên công'} · {source.operation.operation_id} · {source.operation.strategy_key}"); self.source_value.setText(f"{source.artifact.artifact_id} · {translate_status(source.artifact.completion_status.value.upper())}\n{source.artifact.artifact_fingerprint.digest if source.artifact.artifact_fingerprint else '—'}"); self.simulation_value.setText(f"{translate_status(sim_status)} · {sim.result_fingerprint.digest if sim else '—'}"); self.machine_value.setText(self.state.machine); self.tool_value.setText(f"{self.state.tool} / {self.state.holder}"); self.status_label.setText(f"Post {translate_status(post_status.value.upper())} · Được quản lý {translate_status(status.value.upper())} · Bên ngoài {translate_status(self.state.external_status.value.upper())}")
        self.state_changed.emit(self.state)
        self._update_action_enabled()
        localize_widget_tree(self)

    def _set_status(self, status: PostGenerationStatus) -> None:
        self.state = replace(self.state, post_status=status)
        self.status_label.setText(
            f"Post {translate_status(status.value.upper())} · Được quản lý "
            f"{translate_status(self.state.managed_status.value.upper())} · "
            f"Bên ngoài {translate_status(self.state.external_status.value.upper())}"
        )
        self.state_changed.emit(self.state)
        self._update_action_enabled()

    def _update_action_enabled(self) -> None:
        """Apply fail-closed action gating without mutating domain state."""
        ready = self._source is not None and self._applied is not None
        draft = self._draft
        safe = draft is not None and draft.safe_z is not None and draft.safe_z > 0.0
        supported = ready and self._source is not None and self._source.operation.strategy_key != "tapping_v1"
        current = self._result is not None and self.state.post_status is PostGenerationStatus.CURRENT
        gate_ready = False
        if supported and safe and draft == self._applied and self._source is not None:
            try:
                request = build_production_post_request(self._source, draft)
                gate_ready = not any(item.severity is DiagnosticSeverity.ERROR for item in validate_post_source(self._source, request.simulation_gate_policy))
            except Exception:
                gate_ready = False
        self.validate_button.setEnabled(bool(ready))
        self.generate_button.setEnabled(bool(supported and safe and gate_ready and self._active_thread is None))
        self.preview_button.setEnabled(bool(current))
        self.save_button.setEnabled(bool(current))
        self.export_button.setEnabled(bool(current))

    def _show_diagnostics(self, diagnostics: tuple[PostDiagnostic, ...]) -> None:
        self._diagnostic_values = tuple(diagnostics)
        self._render_diagnostics()

    def _render_diagnostics(self) -> None:
        selected = str(self.diagnostic_filter.currentData()).casefold() if hasattr(self, "diagnostic_filter") else "all"
        self.diagnostics.setRowCount(0)
        for diagnostic in self._diagnostic_values:
            if selected != "all" and diagnostic.severity.value.casefold() != selected:
                continue
            row = self.diagnostics.rowCount(); self.diagnostics.insertRow(row)
            values = (translate_status(diagnostic.severity.value.upper()), diagnostic.code.value, ui_text(diagnostic.message_key), str(getattr(diagnostic, "record_index", None) or ""), "; ".join(f"{key}={value}" for key, value in diagnostic.evidence), str(getattr(diagnostic, "operation_id", None) or "Xuất NC"))
            for column, value in enumerate(values): self.diagnostics.setItem(row, column, QTableWidgetItem(value))
        localize_widget_tree(self.diagnostics)

    def _show_export_diagnostics(self, diagnostics: tuple[NCExportDiagnostic, ...]) -> None:
        self._diagnostic_values = tuple(diagnostics)
        self._render_diagnostics()

    def _cancel_worker(self) -> None:
        if self._active_thread is not None:
            self._active_thread.quit(); self._active_thread.wait(1000)
            self._active_thread = None; self._active_worker = None


__all__ = [
    "ExternalExportUiStatus", "ManagedArtifactUiStatus", "PostGenerationStatus", "PostPanelDraft", "PostPanelState", "PostProgressPhase", "PostProcessorPanel", "build_production_post_request", "sanitize_post_filename",
]
