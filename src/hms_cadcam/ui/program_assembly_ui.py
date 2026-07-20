"""Multi-operation production Program Assembly workflow (Stage 7D.3.2).

The panel owns only transient UI drafts and immutable assembly snapshots.  It
does not persist CAM domain data, format NC by itself, or write files without
an explicit Save/Export action.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from hms_cadcam.cam.domain import ArtifactStatus, LengthUnit, OperationId
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.post import (
    ControllerToolBinding,
    CutterCompensationPolicy,
    ExportOverwritePolicy,
    ExportTarget,
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCAssemblyExportRequest,
    NCAssemblyExportSourceSnapshot,
    NCExportDiagnostic,
    ProgramAssemblyContext,
    ProgramAssemblyDiagnostic,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyOperationInput,
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyService,
    ProgramAssemblyStatus,
    SimulationGateMode,
    SimulationGatePolicy,
    build_assembly_input_fingerprint,
    robodrill_21i_definition,
    robodrill_21i_profile,
    validate_assembly_request,
)
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.ui.post_ui import (
    ExternalExportUiStatus,
    ManagedArtifactUiStatus,
    PostPanelDraft,
    build_production_post_request,
    sanitize_post_filename,
)


logger = logging.getLogger(__name__)

_OPERATION_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 80
_DIAGNOSTIC_ROLE = int(Qt.ItemDataRole.UserRole) + 81
_METADATA_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_SUPPORTED_STRATEGIES = {
    "facing_2_5d",
    "contour_2d",
    "pocket_2_5d",
    "drilling_v1",
    "reaming_v1",
    "boring_v1",
}


class ProgramAssemblyUiStatus(StrEnum):
    MISSING = "missing"
    DRAFT = "draft"
    INVALID = "invalid"
    VALID = "valid"
    GENERATING = "generating"
    CURRENT = "current"
    STALE = "stale"
    FAILED = "failed"


class ProgramAssemblyProgressPhase(StrEnum):
    MISSING = "missing"
    VALIDATING = "validating"
    GENERATING_SECTIONS = "generating_sections"
    ASSEMBLING = "assembling"
    VALIDATING_OUTPUT = "validating_output"
    CURRENT = "current"
    STALE = "stale"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AssemblySharedDraft:
    """Applied program-wide and export policy edited by the panel."""

    profile_key: str
    file_name: str
    global_metadata: tuple[tuple[str, str], ...]
    work_offset: str
    simulation_gate: SimulationGateMode
    overwrite_policy: ExportOverwritePolicy
    target_kind: ExportTarget
    target_directory: Path | None = None
    create_target_directory: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported assembly UI draft version")
        if not isinstance(self.profile_key, str) or not self.profile_key:
            raise ValueError("Production profile is required")
        if not isinstance(self.file_name, str):
            raise ValueError("Output filename must be text")
        if not isinstance(self.global_metadata, tuple):
            raise ValueError("Global metadata must be immutable")
        if self.work_offset != "G54":
            raise ValueError("Production profile v1 supports G54 only")
        object.__setattr__(self, "simulation_gate", SimulationGateMode(self.simulation_gate))
        object.__setattr__(self, "overwrite_policy", ExportOverwritePolicy(self.overwrite_policy))
        object.__setattr__(self, "target_kind", ExportTarget(self.target_kind))
        if self.target_directory is not None and not isinstance(self.target_directory, Path):
            raise ValueError("Export destination must be a pathlib.Path")
        if type(self.create_target_directory) is not bool:
            raise ValueError("Create-directory policy is invalid")

    @property
    def semantic_identity(self) -> tuple[object, ...]:
        return (
            self.profile_key,
            self.file_name.casefold(),
            self.global_metadata,
            self.work_offset,
            self.simulation_gate,
        )

    @property
    def export_identity(self) -> tuple[object, ...]:
        return (
            self.overwrite_policy,
            self.target_kind,
            self.target_directory,
            self.create_target_directory,
        )


@dataclass(frozen=True, slots=True)
class AssemblyOperationDraft:
    """Applied controller binding and section context for one operation ID."""

    operation_id: OperationId
    tool_station: int
    length_offset: int
    diameter_offset: int | None
    safe_z: float | None
    cutter_compensation: CutterCompensationPolicy
    tool_comment: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not isinstance(self.operation_id, OperationId):
            raise ValueError("Assembly operation draft identity is invalid")
        for value, name in (
            (self.tool_station, "T"),
            (self.length_offset, "H"),
        ):
            if type(value) is not int or not 1 <= value <= 9999:
                raise ValueError(f"{name} offset is out of range")
        if self.diameter_offset is not None and (
            type(self.diameter_offset) is not int
            or not 1 <= self.diameter_offset <= 9999
        ):
            raise ValueError("D offset is out of range")
        if self.safe_z is not None and (
            not isinstance(self.safe_z, (int, float))
            or not float("-inf") < float(self.safe_z) < float("inf")
        ):
            raise ValueError("Safe Z must be finite")
        object.__setattr__(
            self,
            "cutter_compensation",
            CutterCompensationPolicy(self.cutter_compensation),
        )
        if not isinstance(self.tool_comment, str):
            raise ValueError("Tool comment must be text")


@dataclass(frozen=True, slots=True)
class SectionNavigation:
    operation_id: OperationId
    section_index: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ProgramAssemblyPanelState:
    project_id: object | None = None
    job_id: object | None = None
    setup_id: object | None = None
    machine_id: object | None = None
    profile_key: str = "robodrill_fanuc_21i_worknc_expanded_v1"
    work_offset: str = "G54"
    simulation_gate: str = "REQUIRE_PASS"
    assembly_status: ProgramAssemblyUiStatus = ProgramAssemblyUiStatus.MISSING
    progress_phase: ProgramAssemblyProgressPhase = ProgramAssemblyProgressPhase.MISSING
    managed_status: ManagedArtifactUiStatus = ManagedArtifactUiStatus.MISSING
    external_status: ExternalExportUiStatus = ExternalExportUiStatus.NEVER_EXPORTED
    operation_count: int = 0
    section_count: int = 0
    tool_change_count: int = 0
    pass_count: int = 0
    warn_count: int = 0
    optional_missing_count: int = 0
    fail_count: int = 0
    stale_simulation_count: int = 0
    line_count: int = 0
    byte_count: int = 0
    checksum: str | None = None
    assembly_fingerprint: str | None = None


class _AssemblyWorker(QObject):
    completed = Signal(object)

    def __init__(
        self,
        runtime: ProgramAssemblyService,
        request: ProgramAssemblyRequest,
        epoch: int,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._request = request
        self._epoch = epoch

    def run(self) -> None:
        try:
            execution = self._runtime.assemble(self._request)
        except Exception as error:  # worker boundary
            execution = error
        self.completed.emit((self._epoch, self._request, execution))


def parse_global_metadata(value: str) -> tuple[tuple[str, str], ...]:
    """Parse ``key=value`` pairs separated by semicolons without guessing keys."""
    if not isinstance(value, str):
        raise ValueError("Global metadata must be text")
    if not value.strip():
        return ()
    result: list[tuple[str, str]] = []
    for raw_item in value.split(";"):
        if "=" not in raw_item:
            raise ValueError("Global metadata requires key=value pairs")
        key, item_value = (part.strip() for part in raw_item.split("=", 1))
        key = key.casefold()
        if _METADATA_KEY.fullmatch(key) is None:
            raise ValueError(f"Global metadata key is invalid: {key or '(empty)'}")
        if not item_value:
            raise ValueError(f"Global metadata value is missing: {key}")
        result.append((key, item_value))
    if len({key for key, _ in result}) != len(result):
        raise ValueError("Global metadata keys must be unique")
    return tuple(sorted(result))


def _diagnostic(
    code: ProgramAssemblyDiagnosticCode,
    message_key: str,
    *,
    operation_id: OperationId | None = None,
    section_index: int | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    evidence: tuple[tuple[str, str], ...] = (),
) -> ProgramAssemblyDiagnostic:
    return ProgramAssemblyDiagnostic(
        severity,
        code,
        message_key,
        operation_id=operation_id,
        section_index=section_index,
        evidence=evidence,
    )


def _sort_diagnostics(
    values: tuple[ProgramAssemblyDiagnostic, ...]
    | list[ProgramAssemblyDiagnostic],
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    severity_order = {
        DiagnosticSeverity.ERROR: 0,
        DiagnosticSeverity.WARNING: 1,
        DiagnosticSeverity.INFO: 2,
    }
    return tuple(
        sorted(
            set(values),
            key=lambda item: (
                severity_order[item.severity],
                item.section_index if item.section_index is not None else -1,
                item.record_index if item.record_index is not None else -1,
                item.code.value,
                str(item.operation_id) if item.operation_id is not None else "",
                item.message_key,
                item.evidence,
            ),
        )
    )


class ProgramAssemblyPanel(QWidget):
    """Explicit-order multi-operation production workflow."""

    message = Signal(str)
    state_changed = Signal(object)
    progress_changed = Signal(object)

    def __init__(
        self,
        service: object,
        parent: QWidget | None = None,
        *,
        assembly_service: ProgramAssemblyService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CamProgramAssemblyPanel")
        self._service = service
        self._assembly_service = assembly_service or ProgramAssemblyService()
        self._project_id: object | None = None
        self._project_root: Path | None = None
        self._project_name = ""
        self._generation: int | None = None
        self._selected_operation_id: OperationId | None = None
        self._selected_operation_name = ""
        self._operation_ids: list[OperationId] = []
        self._operation_names: dict[OperationId, str] = {}
        self._sources: dict[OperationId, PostSourceSnapshot | None] = {}
        self._operation_drafts: dict[OperationId, AssemblyOperationDraft] = {}
        self._applied_shared = self._default_shared_draft()
        self._shared_widget_dirty = False
        self._operation_widget_dirty = False
        self._widget_guard = False
        self._validated_request: ProgramAssemblyRequest | None = None
        self._validated_fingerprint = None
        self._result: ProgramAssemblyResult | None = None
        self._result_stale = False
        self._managed_entry: NCArtifactManifestEntry | None = None
        self._last_export = None
        self._external_status = ExternalExportUiStatus.NEVER_EXPORTED
        self._diagnostic_values: tuple[object, ...] = ()
        self._navigation: tuple[SectionNavigation, ...] = ()
        self._request_epoch = 0
        self._active_thread: QThread | None = None
        self._active_worker: _AssemblyWorker | None = None
        self.state = ProgramAssemblyPanelState()
        self._build_ui()
        self._apply_shared_widgets(self._applied_shared)
        self._set_project_enabled(False)
        self._refresh_projection()

    @staticmethod
    def _default_shared_draft() -> AssemblySharedDraft:
        profile = robodrill_21i_profile()
        return AssemblySharedDraft(
            profile.profile_key,
            "PROGRAM.fn",
            (),
            "G54",
            SimulationGateMode.REQUIRE_PASS,
            ExportOverwritePolicy.FAIL_IF_EXISTS,
            ExportTarget.FILESYSTEM_DIRECTORY,
        )

    @property
    def ordered_operation_ids(self) -> tuple[OperationId, ...]:
        return tuple(self._operation_ids)

    @property
    def result(self) -> ProgramAssemblyResult | None:
        return self._result

    @property
    def preview_source_text(self) -> str:
        """Return canonical text, retaining CRLF independently of Qt rendering."""
        return self._result.canonical_text if self._result is not None else ""

    @property
    def navigation(self) -> tuple[SectionNavigation, ...]:
        return self._navigation

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Program Assembly · Production Workflow 7D.3.2")
        title.setObjectName("ProgramAssemblyTitle")
        root.addWidget(title)
        self.status_label = QLabel("MISSING")
        self.status_label.setObjectName("ProgramAssemblyStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        source_group = QGroupBox("Project / shared production context")
        source_form = QFormLayout(source_group)
        self.project_value = QLabel("—")
        self.job_setup_value = QLabel("—")
        self.machine_value = QLabel("—")
        self.profile_value = QLabel("—")
        self.simulation_summary = QLabel("PASS 0 · WARN 0 · OPTIONAL MISSING 0 · FAIL 0 · STALE/MALFORMED 0")
        self.simulation_summary.setWordWrap(True)
        self.artifact_summary = QLabel("Assembly — · Managed — · External —")
        self.artifact_summary.setWordWrap(True)
        for label, widget in (
            ("Project", self.project_value),
            ("Job / Setup", self.job_setup_value),
            ("Machine", self.machine_value),
            ("Profile / WCS", self.profile_value),
            ("Simulation gates", self.simulation_summary),
            ("Artifacts", self.artifact_summary),
        ):
            source_form.addRow(label, widget)
        root.addWidget(source_group)

        context_group = QGroupBox("Shared program context")
        context_form = QFormLayout(context_group)
        self.filename_edit = QLineEdit("PROGRAM.fn")
        self.filename_edit.setObjectName("AssemblyFilename")
        self.metadata_edit = QLineEdit()
        self.metadata_edit.setPlaceholderText("customer=HMS; part=...")
        self.profile_combo = QComboBox()
        profile = robodrill_21i_profile()
        self.profile_combo.addItem(
            f"{profile.profile_key} v{profile.profile_version}", profile.profile_key
        )
        self.work_offset_combo = QComboBox()
        self.work_offset_combo.addItem("G54", "G54")
        self.gate_combo = QComboBox()
        for mode in (
            SimulationGateMode.REQUIRE_PASS,
            SimulationGateMode.ALLOW_WARN,
            SimulationGateMode.OPTIONAL,
        ):
            self.gate_combo.addItem(mode.value.upper(), mode)
        self.overwrite_combo = QComboBox()
        for policy in (
            ExportOverwritePolicy.FAIL_IF_EXISTS,
            ExportOverwritePolicy.REPLACE_IF_SAME_ARTIFACT,
            ExportOverwritePolicy.REPLACE_EXPLICIT,
        ):
            self.overwrite_combo.addItem(policy.value.upper(), policy)
        self.target_kind_combo = QComboBox()
        self.target_kind_combo.addItem(
            "Local / mapped / UNC", ExportTarget.FILESYSTEM_DIRECTORY
        )
        self.target_kind_combo.addItem(
            "Data-server directory", ExportTarget.DATA_SERVER_DIRECTORY
        )
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("External destination")
        browse_button = QPushButton("Browse…")
        browse_button.setObjectName("AssemblyBrowseTarget")
        browse_button.clicked.connect(self._browse_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit)
        target_row.addWidget(browse_button)
        self.create_target_check = QCheckBox("Create destination if missing")
        self.apply_context_button = QPushButton("Apply Context")
        self.apply_context_button.setObjectName("AssemblyApplyContext")
        context_form.addRow("Output filename", self.filename_edit)
        context_form.addRow("Global comment metadata", self.metadata_edit)
        context_form.addRow("Production profile", self.profile_combo)
        context_form.addRow("Work offset", self.work_offset_combo)
        context_form.addRow("Simulation gate", self.gate_combo)
        context_form.addRow("Overwrite policy", self.overwrite_combo)
        context_form.addRow("Target type", self.target_kind_combo)
        context_form.addRow("Destination", target_row)
        context_form.addRow("", self.create_target_check)
        context_form.addRow("", self.apply_context_button)
        root.addWidget(context_group)

        operation_group = QGroupBox("Explicit ordered operation list")
        operation_layout = QVBoxLayout(operation_group)
        operation_actions = QHBoxLayout()
        self.add_button = QPushButton("Add Selected Operation")
        self.remove_button = QPushButton("Remove Operation")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.clear_list_button = QPushButton("Clear List")
        for name, button in (
            ("AssemblyAddSelected", self.add_button),
            ("AssemblyRemove", self.remove_button),
            ("AssemblyMoveUp", self.move_up_button),
            ("AssemblyMoveDown", self.move_down_button),
            ("AssemblyClearList", self.clear_list_button),
        ):
            button.setObjectName(name)
            operation_actions.addWidget(button)
        operation_layout.addLayout(operation_actions)
        headers = (
            "Order",
            "Operation",
            "Strategy",
            "Operation status",
            "ToolpathArtifact",
            "Simulation",
            "T",
            "H",
            "D",
            "Safe Z",
            "Compensation",
            "Spindle/RPM",
            "Est. lines",
            "Compatibility",
        )
        self.operation_table = QTableWidget(0, len(headers))
        self.operation_table.setObjectName("AssemblyOperationList")
        self.operation_table.setHorizontalHeaderLabels(list(headers))
        self.operation_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.operation_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.operation_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        operation_layout.addWidget(self.operation_table, 2)
        root.addWidget(operation_group, 2)

        section_group = QGroupBox("Selected operation section context")
        section_form = QFormLayout(section_group)
        self.selected_operation_value = QLabel("—")
        self.tool_station_spin = QSpinBox()
        self.tool_station_spin.setRange(1, 9999)
        self.length_offset_spin = QSpinBox()
        self.length_offset_spin.setRange(1, 9999)
        self.diameter_offset_spin = QSpinBox()
        self.diameter_offset_spin.setRange(0, 9999)
        self.diameter_offset_spin.setSpecialValueText("(none)")
        self.safe_z_spin = QDoubleSpinBox()
        self.safe_z_spin.setRange(-1_000_000.0, 1_000_000.0)
        self.safe_z_spin.setDecimals(4)
        self.safe_z_spin.setSpecialValueText("(missing)")
        self.compensation_combo = QComboBox()
        for policy, label in (
            (CutterCompensationPolicy.DISABLED, "DISABLED"),
            (
                CutterCompensationPolicy.LEGACY_WORKNC_LEFT,
                "LEGACY_WORKNC_LEFT (G41)",
            ),
            (CutterCompensationPolicy.FROM_PROGRAM_IR_ONLY, "FROM_PROGRAM_IR_ONLY"),
        ):
            self.compensation_combo.addItem(label, policy)
        self.tool_comment_edit = QLineEdit()
        section_buttons = QHBoxLayout()
        self.apply_operation_button = QPushButton("Apply Operation")
        self.reset_operation_button = QPushButton("Reset Operation")
        self.equalize_offsets_button = QPushButton("Set T = H = D")
        for name, button in (
            ("AssemblyApplyOperation", self.apply_operation_button),
            ("AssemblyResetOperation", self.reset_operation_button),
            ("AssemblyEqualizeOffsets", self.equalize_offsets_button),
        ):
            button.setObjectName(name)
            section_buttons.addWidget(button)
        section_form.addRow("Operation", self.selected_operation_value)
        section_form.addRow("T station", self.tool_station_spin)
        section_form.addRow("H length offset", self.length_offset_spin)
        section_form.addRow("D diameter offset", self.diameter_offset_spin)
        section_form.addRow("Safe Z (MM)", self.safe_z_spin)
        section_form.addRow("Cutter compensation", self.compensation_combo)
        section_form.addRow("Tool comment", self.tool_comment_edit)
        section_form.addRow("", section_buttons)
        root.addWidget(section_group)

        workflow_row = QHBoxLayout()
        self.validate_button = QPushButton("Validate Assembly")
        self.generate_button = QPushButton("Generate Assembly")
        self.preview_button = QPushButton("Preview")
        self.save_button = QPushButton("Save Managed Artifact")
        self.export_button = QPushButton("Export")
        for name, button in (
            ("AssemblyValidate", self.validate_button),
            ("AssemblyGenerate", self.generate_button),
            ("AssemblyPreview", self.preview_button),
            ("AssemblySaveManaged", self.save_button),
            ("AssemblyExport", self.export_button),
        ):
            button.setObjectName(name)
            workflow_row.addWidget(button)
        root.addLayout(workflow_row)
        lifecycle_row = QHBoxLayout()
        self.show_diagnostics_button = QPushButton("Show Diagnostics")
        self.clear_result_button = QPushButton("Clear Assembly Result")
        self.clear_managed_button = QPushButton("Clear Managed Artifact")
        for name, button in (
            ("AssemblyShowDiagnostics", self.show_diagnostics_button),
            ("AssemblyClearResult", self.clear_result_button),
            ("AssemblyClearManaged", self.clear_managed_button),
        ):
            button.setObjectName(name)
            lifecycle_row.addWidget(button)
        root.addLayout(lifecycle_row)

        navigation_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search exact preview")
        self.search_button = QPushButton("Find Next")
        self.copy_checksum_button = QPushButton("Copy Checksum")
        self.section_combo = QComboBox()
        self.jump_section_button = QPushButton("Jump to Section")
        for widget in (
            self.search_edit,
            self.search_button,
            self.copy_checksum_button,
            self.section_combo,
            self.jump_section_button,
        ):
            navigation_row.addWidget(widget)
        root.addLayout(navigation_row)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("AssemblyNcPreview")
        self.preview.setReadOnly(True)
        root.addWidget(self.preview, 2)
        self.metadata_label = QLabel("Preview metadata: —")
        self.metadata_label.setWordWrap(True)
        root.addWidget(self.metadata_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Diagnostics"))
        self.diagnostic_filter = QComboBox()
        self.diagnostic_filter.addItems(["ALL", "ERROR", "WARNING", "INFO"])
        filter_row.addWidget(self.diagnostic_filter)
        filter_row.addStretch(1)
        root.addLayout(filter_row)
        diagnostic_headers = (
            "Severity",
            "Code",
            "Operation",
            "Section",
            "Record",
            "Message",
            "Evidence",
        )
        self.diagnostics = QTableWidget(0, len(diagnostic_headers))
        self.diagnostics.setObjectName("AssemblyDiagnostics")
        self.diagnostics.setHorizontalHeaderLabels(list(diagnostic_headers))
        self.diagnostics.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.diagnostics.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self.diagnostics, 1)

        self.apply_context_button.clicked.connect(self.apply_shared_draft)
        self.add_button.clicked.connect(self.add_selected_operation)
        self.remove_button.clicked.connect(self.remove_selected_operation)
        self.move_up_button.clicked.connect(lambda: self.move_selected_operation(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selected_operation(1))
        self.clear_list_button.clicked.connect(self.clear_operation_list)
        self.operation_table.itemSelectionChanged.connect(self._operation_selection_changed)
        self.apply_operation_button.clicked.connect(self.apply_operation_draft)
        self.reset_operation_button.clicked.connect(self.reset_operation_draft)
        self.equalize_offsets_button.clicked.connect(self.equalize_offsets)
        self.validate_button.clicked.connect(self.validate_assembly)
        self.generate_button.clicked.connect(self.generate)
        self.preview_button.clicked.connect(self.show_preview)
        self.save_button.clicked.connect(self.save_managed_artifact)
        self.export_button.clicked.connect(lambda: self.export_external())
        self.show_diagnostics_button.clicked.connect(self.show_diagnostics)
        self.clear_result_button.clicked.connect(self.clear_assembly_result)
        self.clear_managed_button.clicked.connect(
            lambda: self.clear_managed_artifact()
        )
        self.search_button.clicked.connect(self.find_next)
        self.copy_checksum_button.clicked.connect(self.copy_checksum)
        self.jump_section_button.clicked.connect(self.jump_to_selected_section)
        self.diagnostic_filter.currentIndexChanged.connect(self._render_diagnostics)
        self.diagnostics.itemSelectionChanged.connect(self._diagnostic_selection_changed)
        for widget in (self.filename_edit, self.metadata_edit, self.target_edit):
            widget.textChanged.connect(self._shared_draft_edited)
        for widget in (
            self.profile_combo,
            self.work_offset_combo,
            self.gate_combo,
            self.overwrite_combo,
            self.target_kind_combo,
        ):
            widget.currentIndexChanged.connect(self._shared_draft_edited)
        self.create_target_check.toggled.connect(self._shared_draft_edited)
        for widget in (
            self.tool_station_spin,
            self.length_offset_spin,
            self.diameter_offset_spin,
            self.safe_z_spin,
        ):
            widget.valueChanged.connect(self._operation_draft_edited)
        self.compensation_combo.currentIndexChanged.connect(
            self._operation_draft_edited
        )
        self.tool_comment_edit.textChanged.connect(self._operation_draft_edited)

    def bind_project(self, session: object) -> None:
        """Invalidate callbacks and clear runtime drafts on project switch/open."""
        self._cancel_worker()
        self._request_epoch += 1
        self._assembly_service.invalidate_all()
        self._project_id = None
        self._project_root = None
        self._project_name = ""
        self._generation = None
        self._selected_operation_id = None
        self._selected_operation_name = ""
        self._operation_ids.clear()
        self._operation_names.clear()
        self._sources.clear()
        self._operation_drafts.clear()
        self._validated_request = None
        self._validated_fingerprint = None
        self._result = None
        self._result_stale = False
        self._last_export = None
        self._external_status = ExternalExportUiStatus.NEVER_EXPORTED
        self._navigation = ()
        self.state = replace(
            self.state,
            assembly_status=ProgramAssemblyUiStatus.MISSING,
            progress_phase=ProgramAssemblyProgressPhase.MISSING,
        )
        self.preview.clear()
        self.section_combo.clear()
        self._set_diagnostics(())
        manifest = getattr(session, "manifest", None)
        root_path = getattr(session, "root_path", None)
        project_id = getattr(manifest, "project_id", None)
        if project_id is None or not isinstance(root_path, Path):
            self._managed_entry = None
            self._set_project_enabled(False)
            self._set_status(ProgramAssemblyUiStatus.MISSING)
            self._refresh_projection()
            return
        self._project_id = project_id
        self._project_root = root_path
        self._project_name = str(getattr(manifest, "project_name", root_path.stem))
        try:
            self._generation = int(self._service.cam_generation)
        except (AttributeError, ProjectError, RuntimeError, TypeError, ValueError):
            self._generation = None
        self._applied_shared = self._default_shared_draft()
        self._apply_shared_widgets(self._applied_shared)
        self._set_project_enabled(True)
        self._set_status(ProgramAssemblyUiStatus.MISSING)
        self._refresh_managed_entry()
        self._refresh_projection()

    def set_selected_operation(
        self,
        operation_id: OperationId,
        *,
        operation_name: str | None = None,
    ) -> None:
        """Track CAM-tree selection without mutating the assembly order."""
        if not isinstance(operation_id, OperationId):
            self.clear_selected_operation()
            return
        self._selected_operation_id = operation_id
        self._selected_operation_name = operation_name or ""
        self._update_action_enabled()

    def clear_selected_operation(self) -> None:
        self._selected_operation_id = None
        self._selected_operation_name = ""
        self._update_action_enabled()

    def refresh_sources(self) -> None:
        """Re-capture current sources by typed ID and classify stale lifecycle state."""
        if self._project_id is None:
            return
        for operation_id in self._operation_ids:
            try:
                self._sources[operation_id] = self._service.capture_post_source(
                    operation_id
                )
            except Exception:
                self._sources[operation_id] = None
        if self._result is not None and not self._result_stale:
            request, diagnostics = self._capture_request()
            if (
                request is None
                or any(
                    item.severity is DiagnosticSeverity.ERROR
                    for item in diagnostics
                )
                or build_assembly_input_fingerprint(request)
                != self._result.input_fingerprint
            ):
                self._invalidate_result("assembly.source_changed")
        if self._validated_request is not None:
            request, diagnostics = self._capture_request()
            if (
                request is None
                or any(
                    item.severity is DiagnosticSeverity.ERROR
                    for item in diagnostics
                )
                or build_assembly_input_fingerprint(request)
                != self._validated_fingerprint
            ):
                self._validated_request = None
                self._validated_fingerprint = None
                if self._result is None:
                    self._set_status(ProgramAssemblyUiStatus.DRAFT)
        self._refresh_managed_entry()
        self._render_operations()
        self._refresh_projection()

    def add_selected_operation(self) -> bool:
        operation_id = self._selected_operation_id
        if operation_id is None or self._project_id is None:
            return False
        if operation_id in self._operation_ids:
            self._set_diagnostics(
                (
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.DUPLICATE_OPERATION,
                        "assembly.duplicate_operation",
                        operation_id=operation_id,
                    ),
                )
            )
            self.message.emit("Operation đã có trong Program Assembly; không thêm trùng.")
            return False
        location = self._operation_location(operation_id)
        if location is None:
            self._set_diagnostics(
                (
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.OPERATION_MISSING,
                        "assembly.operation_missing",
                        operation_id=operation_id,
                    ),
                )
            )
            return False
        job, setup, operation = location
        if not operation.enabled:
            self._set_diagnostics(
                (
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.OPERATION_DISABLED,
                        "assembly.operation_disabled",
                        operation_id=operation_id,
                    ),
                )
            )
            return False
        if operation.artifact_state.status is not ArtifactStatus.VALID:
            self._set_diagnostics(
                (
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.ARTIFACT_STALE,
                        "assembly.artifact_not_current",
                        operation_id=operation_id,
                        evidence=(("status", operation.artifact_state.status.value),),
                    ),
                )
            )
            return False
        try:
            source = self._service.capture_post_source(operation_id)
        except Exception as error:
            self._set_diagnostics(
                (
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.ARTIFACT_MISSING,
                        "assembly.artifact_missing",
                        operation_id=operation_id,
                        evidence=(("error", _evidence_text(error)),),
                    ),
                )
            )
            return False
        if self._operation_ids:
            anchor = self._operation_location(self._operation_ids[0])
            anchor_source = self._sources.get(self._operation_ids[0])
            if anchor is None or anchor_source is None:
                self._set_diagnostics(
                    (
                        _diagnostic(
                            ProgramAssemblyDiagnosticCode.OPERATION_MISSING,
                            "assembly.anchor_operation_invalid",
                            operation_id=self._operation_ids[0],
                        ),
                    )
                )
                return False
            anchor_job, anchor_setup, _ = anchor
            if job.job_id != anchor_job.job_id:
                self._set_diagnostics(
                    (
                        _diagnostic(
                            ProgramAssemblyDiagnosticCode.SETUP_MISMATCH,
                            "assembly.job_mismatch",
                            operation_id=operation_id,
                        ),
                    )
                )
                return False
            if setup.setup_id != anchor_setup.setup_id:
                self._set_diagnostics(
                    (
                        _diagnostic(
                            ProgramAssemblyDiagnosticCode.SETUP_MISMATCH,
                            "assembly.setup_mismatch",
                            operation_id=operation_id,
                        ),
                    )
                )
                return False
            if (
                source.machine is None
                or anchor_source.machine is None
                or source.machine.machine_id != anchor_source.machine.machine_id
                or source.machine.content_fingerprint
                != anchor_source.machine.content_fingerprint
            ):
                self._set_diagnostics(
                    (
                        _diagnostic(
                            ProgramAssemblyDiagnosticCode.MACHINE_MISMATCH,
                            "assembly.machine_mismatch",
                            operation_id=operation_id,
                        ),
                    )
                )
                return False
        self._operation_ids.append(operation_id)
        self._operation_names[operation_id] = (
            self._selected_operation_name
            or str(getattr(operation, "strategy_key", "Operation"))
        )
        self._sources[operation_id] = source
        tool_name = source.tool.name if source.tool is not None else "Tool"
        self._operation_drafts[operation_id] = AssemblyOperationDraft(
            operation_id,
            1,
            1,
            1,
            None,
            CutterCompensationPolicy.DISABLED,
            tool_name,
        )
        self._invalidate_result("assembly.order_changed")
        self._set_status(ProgramAssemblyUiStatus.DRAFT)
        self._set_diagnostics(())
        self._render_operations(select_operation_id=operation_id)
        self._refresh_projection()
        self.message.emit("Đã thêm operation theo explicit order; chưa Generate/Export.")
        return True

    def remove_selected_operation(self) -> bool:
        operation_id = self._selected_assembly_operation_id()
        if operation_id is None:
            return False
        self._operation_ids.remove(operation_id)
        self._operation_names.pop(operation_id, None)
        self._sources.pop(operation_id, None)
        self._operation_drafts.pop(operation_id, None)
        self._invalidate_result("assembly.order_changed")
        self._set_status(
            ProgramAssemblyUiStatus.DRAFT
            if self._operation_ids
            else ProgramAssemblyUiStatus.MISSING
        )
        self._render_operations()
        self._refresh_projection()
        return True

    def move_selected_operation(self, delta: int) -> bool:
        if delta not in {-1, 1}:
            raise ValueError("Assembly move must be atomic by one row")
        operation_id = self._selected_assembly_operation_id()
        if operation_id is None:
            return False
        index = self._operation_ids.index(operation_id)
        target = index + delta
        if not 0 <= target < len(self._operation_ids):
            return False
        changed = list(self._operation_ids)
        changed[index], changed[target] = changed[target], changed[index]
        self._operation_ids = changed
        self._invalidate_result("assembly.order_changed")
        self._set_status(ProgramAssemblyUiStatus.DRAFT)
        self._render_operations(select_operation_id=operation_id)
        self._refresh_projection()
        return True

    def clear_operation_list(self) -> None:
        if not self._operation_ids:
            return
        self._invalidate_result("assembly.order_changed")
        self._operation_ids.clear()
        self._operation_names.clear()
        self._sources.clear()
        self._operation_drafts.clear()
        self._set_status(ProgramAssemblyUiStatus.MISSING)
        self._render_operations()
        self._refresh_projection()

    def apply_shared_draft(self) -> bool:
        try:
            draft = self._shared_draft_from_widgets()
            sanitize_post_filename(draft.file_name)
            ProgramAssemblyContext(
                sanitize_post_filename(draft.file_name),
                draft.global_metadata,
            )
        except Exception as error:
            self.message.emit(f"Invalid assembly context draft: {error}")
            self._shared_widget_dirty = True
            self._update_action_enabled()
            return False
        previous = self._applied_shared
        self._applied_shared = replace(
            draft, file_name=sanitize_post_filename(draft.file_name)
        )
        self._apply_shared_widgets(self._applied_shared)
        if previous.semantic_identity != self._applied_shared.semantic_identity:
            self._invalidate_result("assembly.context_changed")
        elif previous.export_identity != self._applied_shared.export_identity:
            if self._external_status is ExternalExportUiStatus.EXPORTED:
                self._external_status = ExternalExportUiStatus.OUTDATED
        self._shared_widget_dirty = False
        self._refresh_projection()
        self.message.emit("Assembly context đã Apply atomically; không tự Generate/Export.")
        return True

    def _shared_draft_from_widgets(self) -> AssemblySharedDraft:
        destination = self.target_edit.text().strip()
        return AssemblySharedDraft(
            str(self.profile_combo.currentData()),
            self.filename_edit.text(),
            parse_global_metadata(self.metadata_edit.text()),
            str(self.work_offset_combo.currentData()),
            SimulationGateMode(self.gate_combo.currentData()),
            ExportOverwritePolicy(self.overwrite_combo.currentData()),
            ExportTarget(self.target_kind_combo.currentData()),
            Path(destination) if destination else None,
            self.create_target_check.isChecked(),
        )

    def _apply_shared_widgets(self, draft: AssemblySharedDraft) -> None:
        self._widget_guard = True
        try:
            for combo, value in (
                (self.profile_combo, draft.profile_key),
                (self.work_offset_combo, draft.work_offset),
                (self.gate_combo, draft.simulation_gate),
                (self.overwrite_combo, draft.overwrite_policy),
                (self.target_kind_combo, draft.target_kind),
            ):
                combo.setCurrentIndex(max(combo.findData(value), 0))
            self.filename_edit.setText(draft.file_name)
            self.metadata_edit.setText(
                "; ".join(f"{key}={value}" for key, value in draft.global_metadata)
            )
            self.target_edit.setText(
                str(draft.target_directory) if draft.target_directory else ""
            )
            self.create_target_check.setChecked(draft.create_target_directory)
            self._shared_widget_dirty = False
        finally:
            self._widget_guard = False

    def _shared_draft_edited(self, *_args: object) -> None:
        if self._widget_guard or self._project_id is None:
            return
        try:
            self._shared_widget_dirty = (
                self._shared_draft_from_widgets() != self._applied_shared
            )
        except (TypeError, ValueError):
            self._shared_widget_dirty = True
        self._update_action_enabled()

    def _operation_selection_changed(self) -> None:
        operation_id = self._selected_assembly_operation_id()
        self._load_operation_widgets(operation_id)
        if operation_id is not None and self._result is not None:
            self.jump_to_operation(operation_id)
        self._update_action_enabled()

    def _selected_assembly_operation_id(self) -> OperationId | None:
        row = self.operation_table.currentRow()
        if row < 0:
            return None
        item = self.operation_table.item(row, 0)
        value = item.data(_OPERATION_ID_ROLE) if item is not None else None
        try:
            return OperationId.parse(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def _load_operation_widgets(self, operation_id: OperationId | None) -> None:
        self._widget_guard = True
        try:
            if operation_id is None or operation_id not in self._operation_drafts:
                self.selected_operation_value.setText("—")
                self._operation_widget_dirty = False
                return
            draft = self._operation_drafts[operation_id]
            self.selected_operation_value.setText(
                f"{self._operation_names.get(operation_id, 'Operation')} · {operation_id}"
            )
            self.tool_station_spin.setValue(draft.tool_station)
            self.length_offset_spin.setValue(draft.length_offset)
            self.diameter_offset_spin.setValue(draft.diameter_offset or 0)
            self.safe_z_spin.setValue(draft.safe_z or 0.0)
            self.compensation_combo.setCurrentIndex(
                max(
                    self.compensation_combo.findData(draft.cutter_compensation),
                    0,
                )
            )
            self.tool_comment_edit.setText(draft.tool_comment)
            self._operation_widget_dirty = False
        finally:
            self._widget_guard = False

    def _operation_draft_from_widgets(self) -> AssemblyOperationDraft:
        operation_id = self._selected_assembly_operation_id()
        if operation_id is None:
            raise ValueError("Select one assembly operation")
        safe_z = self.safe_z_spin.value()
        return AssemblyOperationDraft(
            operation_id,
            self.tool_station_spin.value(),
            self.length_offset_spin.value(),
            self.diameter_offset_spin.value() or None,
            None if safe_z == 0.0 else safe_z,
            CutterCompensationPolicy(self.compensation_combo.currentData()),
            self.tool_comment_edit.text(),
        )

    def _operation_draft_edited(self, *_args: object) -> None:
        if self._widget_guard:
            return
        operation_id = self._selected_assembly_operation_id()
        if operation_id is None:
            return
        try:
            self._operation_widget_dirty = (
                self._operation_draft_from_widgets()
                != self._operation_drafts[operation_id]
            )
        except (KeyError, TypeError, ValueError):
            self._operation_widget_dirty = True
        self._update_action_enabled()

    def apply_operation_draft(self) -> bool:
        operation_id = self._selected_assembly_operation_id()
        source = self._sources.get(operation_id) if operation_id is not None else None
        try:
            draft = self._operation_draft_from_widgets()
            if draft.safe_z is None or draft.safe_z <= 0.0:
                raise ValueError("Safe Z must be explicit and positive")
            if (
                draft.cutter_compensation
                is CutterCompensationPolicy.LEGACY_WORKNC_LEFT
                and draft.diameter_offset is None
            ):
                raise ValueError("G41 requires a D offset")
            if (
                draft.cutter_compensation
                is CutterCompensationPolicy.LEGACY_WORKNC_LEFT
                and (source is None or source.operation.strategy_key != "contour_2d")
            ):
                raise ValueError("G41 policy is supported for Contour only")
            if source is None:
                raise ValueError("Operation source is missing or stale")
            self._production_context(source, draft)
        except Exception as error:
            self.message.emit(f"Invalid operation assembly draft: {error}")
            self._operation_widget_dirty = True
            self._update_action_enabled()
            return False
        assert operation_id is not None
        previous = self._operation_drafts[operation_id]
        self._operation_drafts[operation_id] = draft
        self._operation_widget_dirty = False
        if previous != draft:
            self._invalidate_result("assembly.operation_context_changed")
        self._render_operations(select_operation_id=operation_id)
        self._refresh_projection()
        self.message.emit("Operation context đã Apply atomically; không tự Generate/Export.")
        return True

    def reset_operation_draft(self) -> None:
        self._load_operation_widgets(self._selected_assembly_operation_id())
        self._update_action_enabled()

    def equalize_offsets(self) -> None:
        value = self.tool_station_spin.value()
        self.length_offset_spin.setValue(value)
        self.diameter_offset_spin.setValue(value)

    def _production_context(
        self,
        source: PostSourceSnapshot,
        operation_draft: AssemblyOperationDraft,
    ):
        shared = self._applied_shared
        post_draft = PostPanelDraft(
            shared.profile_key,
            shared.file_name,
            None,
            operation_draft.safe_z,
            shared.work_offset,
            operation_draft.tool_station,
            operation_draft.length_offset,
            operation_draft.diameter_offset,
            operation_draft.tool_comment,
            operation_draft.cutter_compensation,
            shared.simulation_gate,
            shared.overwrite_policy,
            shared.target_directory,
            shared.target_kind,
        )
        return build_production_post_request(source, post_draft).program_context

    def _capture_request(
        self,
    ) -> tuple[
        ProgramAssemblyRequest | None,
        tuple[ProgramAssemblyDiagnostic, ...],
    ]:
        diagnostics: list[ProgramAssemblyDiagnostic] = []
        if self._project_id is None or self._generation is None:
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.project_missing",
                )
            )
            return None, _sort_diagnostics(diagnostics)
        if not self._operation_ids:
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.EMPTY,
                    "assembly.empty",
                )
            )
            return None, _sort_diagnostics(diagnostics)
        if self._shared_widget_dirty:
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.context_draft_not_applied",
                )
            )
        if self._operation_widget_dirty:
            operation_id = self._selected_assembly_operation_id()
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.operation_draft_not_applied",
                    operation_id=operation_id,
                )
            )
        shared = self._applied_shared
        profile = robodrill_21i_profile()
        try:
            filename = sanitize_post_filename(
                shared.file_name, extension=profile.allowed_extensions[0]
            )
            context = ProgramAssemblyContext(
                filename,
                shared.global_metadata,
            )
        except Exception as error:
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.filename_invalid",
                    evidence=(("error", _evidence_text(error)),),
                )
            )
            return None, _sort_diagnostics(diagnostics)
        operation_inputs: list[ProgramAssemblyOperationInput] = []
        base_job = None
        base_setup = None
        base_machine = None
        binding_entries: list[tuple[OperationId, int, str, object]] = []
        for index, operation_id in enumerate(self._operation_ids):
            location = self._operation_location(operation_id)
            if location is None:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.OPERATION_MISSING,
                        "assembly.operation_missing",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
                continue
            job, setup, operation = location
            if base_job is None:
                base_job, base_setup = job, setup
            elif job.job_id != base_job.job_id:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.SETUP_MISMATCH,
                        "assembly.job_mismatch",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
            if base_setup is not None and setup.setup_id != base_setup.setup_id:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.SETUP_MISMATCH,
                        "assembly.setup_mismatch",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
            if not operation.enabled:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.OPERATION_DISABLED,
                        "assembly.operation_disabled",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
            if operation.artifact_state.status is not ArtifactStatus.VALID:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.ARTIFACT_STALE,
                        "assembly.artifact_not_current",
                        operation_id=operation_id,
                        section_index=index,
                        evidence=(("status", operation.artifact_state.status.value),),
                    )
                )
            try:
                source = self._service.capture_post_source(operation_id)
                self._sources[operation_id] = source
            except Exception as error:
                self._sources[operation_id] = None
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.ARTIFACT_MISSING,
                        "assembly.artifact_missing",
                        operation_id=operation_id,
                        section_index=index,
                        evidence=(("error", _evidence_text(error)),),
                    )
                )
                continue
            if base_machine is None:
                base_machine = source.machine
            elif (
                source.machine is None
                or base_machine.machine_id != source.machine.machine_id
                or base_machine.content_fingerprint
                != source.machine.content_fingerprint
            ):
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.MACHINE_MISMATCH,
                        "assembly.machine_mismatch",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
            operation_draft = self._operation_drafts.get(operation_id)
            if operation_draft is None:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.TOOL_BINDING_MISSING,
                        "assembly.operation_context_missing",
                        operation_id=operation_id,
                        section_index=index,
                    )
                )
                continue
            try:
                production_context = self._production_context(
                    source, operation_draft
                )
                if production_context is None:
                    raise ValueError("Production context is missing")
            except Exception as error:
                if source.artifact.unit is not LengthUnit.MM:
                    code = ProgramAssemblyDiagnosticCode.UNIT_MISMATCH
                elif (
                    source.setup.work_offset.name.upper() != "PRIMARY"
                    or source.setup.work_offset.numeric_slot != 1
                ):
                    code = ProgramAssemblyDiagnosticCode.WORK_OFFSET_MISMATCH
                elif operation_draft.safe_z is None or operation_draft.safe_z <= 0.0:
                    code = ProgramAssemblyDiagnosticCode.SAFE_Z_INVALID
                elif operation_draft.cutter_compensation is CutterCompensationPolicy.LEGACY_WORKNC_LEFT:
                    code = ProgramAssemblyDiagnosticCode.COMPENSATION_INVALID
                else:
                    code = ProgramAssemblyDiagnosticCode.TOOL_BINDING_MISSING
                diagnostics.append(
                    _diagnostic(
                        code,
                        "assembly.operation_context_invalid",
                        operation_id=operation_id,
                        section_index=index,
                        evidence=(("error", _evidence_text(error)),),
                    )
                )
                continue
            binding: ControllerToolBinding = production_context.tool_binding
            binding_entries.extend(
                [
                    (
                        operation_id,
                        binding.tool_station,
                        "T",
                        source.assembly.content_fingerprint,
                    ),
                    (
                        operation_id,
                        binding.length_offset,
                        "H",
                        source.assembly.content_fingerprint,
                    ),
                ]
            )
            if binding.diameter_offset is not None:
                binding_entries.append(
                    (
                        operation_id,
                        binding.diameter_offset,
                        "D",
                        source.assembly.content_fingerprint,
                    )
                )
            operation_inputs.append(
                ProgramAssemblyOperationInput(
                    operation_id=operation_id,
                    order_index=index,
                    artifact_id=source.artifact.artifact_id,
                    artifact_fingerprint=source.artifact.artifact_fingerprint,
                    tool_assembly_fingerprint=source.assembly.content_fingerprint,
                    tool_binding=binding,
                    source_snapshot=source,
                    simulation_result=source.simulation_result,
                    program_context=production_context,
                    cutter_compensation_policy=operation_draft.cutter_compensation,
                    display_metadata=(
                        ("name", self._operation_names.get(operation_id, "Operation")),
                        ("strategy", source.operation.strategy_key),
                    ),
                )
            )
        by_address: dict[tuple[str, int], tuple[object, OperationId]] = {}
        for operation_id, number, address_kind, tool_fingerprint in binding_entries:
            key = (address_kind, number)
            previous = by_address.get(key)
            if previous is not None and previous[0] != tool_fingerprint:
                diagnostics.append(
                    _diagnostic(
                        ProgramAssemblyDiagnosticCode.TOOL_BINDING_CONFLICT,
                        "assembly.tool_binding_conflict",
                        operation_id=operation_id,
                        section_index=self._operation_ids.index(operation_id),
                        evidence=(
                            ("address_kind", address_kind),
                            ("address", str(number)),
                            ("other_operation", str(previous[1])),
                        ),
                    )
                )
            else:
                by_address[key] = (tool_fingerprint, operation_id)
        if (
            diagnostics
            or base_job is None
            or base_setup is None
            or base_machine is None
            or len(operation_inputs) != len(self._operation_ids)
        ):
            return None, _sort_diagnostics(diagnostics)
        try:
            request = ProgramAssemblyRequest(
                project_id=self._project_id,
                project_generation=self._generation,
                job_id=base_job.job_id,
                setup_id=base_setup.setup_id,
                machine_id=base_machine.machine_id,
                machine_fingerprint=base_machine.content_fingerprint,
                post_definition=robodrill_21i_definition(),
                shared_context=context,
                operations=tuple(operation_inputs),
                simulation_gate_policy=SimulationGatePolicy(
                    shared.simulation_gate
                ),
            )
        except Exception as error:
            diagnostics.append(
                _diagnostic(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.invalid_request",
                    evidence=(("error", _evidence_text(error)),),
                )
            )
            return None, _sort_diagnostics(diagnostics)
        return request, _sort_diagnostics(diagnostics)

    def validate_assembly(self) -> tuple[ProgramAssemblyDiagnostic, ...]:
        """Capture and validate only; never lower, format, publish, or write."""
        self._set_status(ProgramAssemblyUiStatus.VALID)
        self._set_progress(ProgramAssemblyProgressPhase.VALIDATING)
        request, diagnostics = self._capture_request()
        values = list(diagnostics)
        if request is not None:
            values.extend(validate_assembly_request(request))
        result = _sort_diagnostics(values)
        self._set_diagnostics(result)
        if request is None or any(
            item.severity is DiagnosticSeverity.ERROR for item in result
        ):
            self._validated_request = None
            self._validated_fingerprint = None
            self._set_status(ProgramAssemblyUiStatus.INVALID)
            self._set_progress(ProgramAssemblyProgressPhase.FAILED)
        else:
            self._validated_request = request
            self._validated_fingerprint = build_assembly_input_fingerprint(request)
            if (
                self._result is not None
                and not self._result_stale
                and self._result.input_fingerprint == self._validated_fingerprint
            ):
                self._set_status(ProgramAssemblyUiStatus.CURRENT)
            else:
                self._set_status(ProgramAssemblyUiStatus.VALID)
            self._set_progress(ProgramAssemblyProgressPhase.CURRENT)
        self._render_operations()
        self._refresh_projection()
        return result

    def generate(self) -> None:
        request = self._validated_current_request()
        if request is None or self._active_thread is not None:
            return
        self._set_status(ProgramAssemblyUiStatus.GENERATING)
        self._set_progress(ProgramAssemblyProgressPhase.GENERATING_SECTIONS)
        thread = QThread(self)
        worker = _AssemblyWorker(self._assembly_service, request, self._request_epoch)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._generation_completed)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._worker_finished)
        self._active_thread = thread
        self._active_worker = worker
        self._update_action_enabled()
        thread.start()

    def generate_sync(self) -> ProgramAssemblyResult | None:
        request = self._validated_current_request()
        if request is None:
            return None
        self._set_status(ProgramAssemblyUiStatus.GENERATING)
        self._set_progress(ProgramAssemblyProgressPhase.GENERATING_SECTIONS)
        try:
            execution = self._assembly_service.assemble(request)
        except Exception as error:
            self._handle_generation_result(request, error)
            return None
        return self._handle_generation_result(request, execution)

    def _validated_current_request(self) -> ProgramAssemblyRequest | None:
        request = self._validated_request
        if (
            request is None
            or self._validated_fingerprint is None
            or self._shared_widget_dirty
            or self._operation_widget_dirty
        ):
            return None
        try:
            generation_current = self._generation == self._service.cam_generation
        except (AttributeError, ProjectError, RuntimeError):
            generation_current = False
        if not generation_current:
            self._validated_request = None
            self._validated_fingerprint = None
            self._set_status(ProgramAssemblyUiStatus.STALE)
            self._set_progress(ProgramAssemblyProgressPhase.STALE)
            return None
        current, diagnostics = self._capture_request()
        if (
            current is None
            or any(
                item.severity is DiagnosticSeverity.ERROR
                for item in diagnostics
            )
            or build_assembly_input_fingerprint(current)
            != self._validated_fingerprint
        ):
            self._validated_request = None
            self._validated_fingerprint = None
            self._set_status(ProgramAssemblyUiStatus.STALE)
            self._set_diagnostics(diagnostics)
            return None
        return current

    def _generation_completed(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        epoch, request, execution = payload
        if epoch != self._request_epoch or not isinstance(
            request, ProgramAssemblyRequest
        ):
            return
        current = self._validated_current_request()
        if (
            current is None
            or tuple(item.operation_id for item in current.operations)
            != tuple(item.operation_id for item in request.operations)
            or build_assembly_input_fingerprint(current)
            != build_assembly_input_fingerprint(request)
        ):
            self._set_status(ProgramAssemblyUiStatus.STALE)
            self._set_progress(ProgramAssemblyProgressPhase.STALE)
            return
        self._handle_generation_result(request, execution)

    def _handle_generation_result(
        self, request: ProgramAssemblyRequest, execution: object
    ) -> ProgramAssemblyResult | None:
        self._set_progress(ProgramAssemblyProgressPhase.ASSEMBLING)
        if isinstance(execution, Exception):
            diagnostic = _diagnostic(
                ProgramAssemblyDiagnosticCode.FAILED,
                "assembly.worker_failed",
                evidence=(("error", _evidence_text(execution)),),
            )
            self._set_diagnostics((diagnostic,))
            self._retain_current_or_fail(request)
            return None
        diagnostics = tuple(getattr(execution, "diagnostics", ()))
        if not getattr(execution, "accepted", False) or getattr(
            execution, "result", None
        ) is None:
            self._set_diagnostics(diagnostics)
            if getattr(execution, "status", None) is ProgramAssemblyStatus.STALE:
                self._set_status(ProgramAssemblyUiStatus.STALE)
                self._set_progress(ProgramAssemblyProgressPhase.STALE)
            else:
                self._retain_current_or_fail(request)
            self._render_operations()
            self._refresh_projection()
            return None
        if (
            self._generation is None
            or self._generation != getattr(self._service, "cam_generation", None)
        ):
            self._set_status(ProgramAssemblyUiStatus.STALE)
            self._set_progress(ProgramAssemblyProgressPhase.STALE)
            return None
        result = execution.result
        self._set_progress(ProgramAssemblyProgressPhase.VALIDATING_OUTPUT)
        self._result = result
        self._result_stale = False
        self._set_diagnostics(diagnostics)
        self._set_status(ProgramAssemblyUiStatus.CURRENT)
        self._set_progress(ProgramAssemblyProgressPhase.CURRENT)
        self.show_preview()
        self._refresh_managed_entry()
        self._render_operations()
        self._refresh_projection()
        self.message.emit("ProgramAssemblyResult đã publish; chưa ghi file.")
        return result

    def _retain_current_or_fail(self, request: ProgramAssemblyRequest) -> None:
        if (
            self._result is not None
            and not self._result_stale
            and self._result.input_fingerprint
            == build_assembly_input_fingerprint(request)
        ):
            self._set_status(ProgramAssemblyUiStatus.CURRENT)
        else:
            self._set_status(ProgramAssemblyUiStatus.FAILED)
        self._set_progress(ProgramAssemblyProgressPhase.FAILED)

    def _worker_finished(self) -> None:
        thread = self._active_thread
        self._active_thread = None
        self._active_worker = None
        if thread is not None:
            thread.deleteLater()
        self._update_action_enabled()

    def show_preview(self) -> None:
        if self._result is None:
            return
        text = self._result.canonical_text
        self.preview.setPlainText(text)
        self._navigation = self._build_navigation(self._result)
        self.section_combo.clear()
        for item in self._navigation:
            name = self._operation_names.get(item.operation_id, "Operation")
            self.section_combo.addItem(
                f"{item.section_index + 1}. {name} · lines {item.start_line}-{item.end_line}",
                str(item.operation_id),
            )
        profile = self._result.plan
        stats = self._result.statistics
        self.metadata_label.setText(
            " · ".join(
                (
                    f"Profile {profile.production_profile_id} v{profile.production_profile_version}",
                    f"operations {stats.operation_count}",
                    f"sections {stats.section_count}",
                    f"tool changes {stats.tool_change_count}",
                    f"lines {stats.line_count}",
                    f"bytes {stats.byte_length}",
                    "CRLF",
                    "UTF-8 / ASCII-compatible",
                    f"SHA-256 {self._result.output_checksum}",
                    "validation PASSED",
                    f"assembly {self._result.result_fingerprint.digest if self._result.result_fingerprint else '—'}",
                    "ordered provenance "
                    + ",".join(str(value) for value in self._result.ordered_operation_ids),
                    "NOT CERTIFIED / REVIEW REQUIRED",
                )
            )
        )
        self._refresh_projection()

    @staticmethod
    def _build_navigation(
        result: ProgramAssemblyResult,
    ) -> tuple[SectionNavigation, ...]:
        lines = result.canonical_text.splitlines()
        starts: list[int] = []
        for section in result.plan.sections:
            marker = (
                f"(OPERATION={section.operation_id},SECTION={section.order_index})"
            )
            try:
                starts.append(lines.index(marker))
            except ValueError:
                starts.append(-1)
        values: list[SectionNavigation] = []
        for index, (section, start) in enumerate(zip(result.plan.sections, starts)):
            if start < 0:
                continue
            next_start = starts[index + 1] if index + 1 < len(starts) else len(lines) - 3
            values.append(
                SectionNavigation(
                    section.operation_id,
                    section.order_index,
                    start + 1,
                    max(start + 1, next_start),
                )
            )
        return tuple(values)

    def jump_to_operation(self, operation_id: OperationId) -> bool:
        item = next(
            (value for value in self._navigation if value.operation_id == operation_id),
            None,
        )
        if item is None:
            return False
        index = self.section_combo.findData(str(operation_id))
        if index >= 0:
            self.section_combo.setCurrentIndex(index)
        self._jump_to_line(item.start_line)
        return True

    def jump_to_selected_section(self) -> bool:
        value = self.section_combo.currentData()
        try:
            operation_id = OperationId.parse(value) if isinstance(value, str) else None
        except ValueError:
            operation_id = None
        return self.jump_to_operation(operation_id) if operation_id is not None else False

    def _jump_to_line(self, one_based_line: int) -> None:
        block = self.preview.document().findBlockByNumber(max(one_based_line - 1, 0))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self.preview.setTextCursor(cursor)
        self.preview.centerCursor()

    def find_next(self) -> bool:
        query = self.search_edit.text()
        if not query:
            return False
        if self.preview.find(query):
            return True
        cursor = self.preview.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.preview.setTextCursor(cursor)
        return self.preview.find(query)

    def copy_checksum(self) -> None:
        if self._result is not None:
            QApplication.clipboard().setText(self._result.output_checksum)

    def _build_export(
        self, target: ExportTarget, directory: Path | None = None
    ) -> tuple[NCAssemblyExportRequest, NCAssemblyExportSourceSnapshot] | None:
        if self._result is None or self._result_stale:
            return None
        request = self._validated_current_request()
        if request is None:
            return None
        export_request = NCAssemblyExportRequest(
            request.project_id,
            self._result.result_id,
            request.shared_context.file_name,
            target,
            self._applied_shared.overwrite_policy,
            self._applied_shared.create_target_directory,
            target_directory=directory,
        )
        return (
            export_request,
            NCAssemblyExportSourceSnapshot(
                request.project_generation, request, self._result
            ),
        )

    def save_managed_artifact(self) -> object | None:
        export = self._build_export(ExportTarget.PROJECT_MANAGED)
        if export is None:
            return None
        try:
            execution = self._service.export_assembly_nc(
                export[0], export[1], current_source=self._current_export_source
            )
        except Exception as error:
            self.message.emit(f"Managed assembly save failed: {error}")
            self._set_status(ProgramAssemblyUiStatus.FAILED)
            return None
        self._last_export = execution
        self._show_export_diagnostics(execution.diagnostics)
        self._refresh_managed_entry()
        self._refresh_projection()
        return execution.result

    def export_external(self, *, confirm: bool | None = None) -> object | None:
        destination = self._applied_shared.target_directory
        if destination is None:
            value = self.target_edit.text().strip()
            destination = Path(value) if value else None
        if destination is None:
            self.message.emit("Chọn destination local/mapped/UNC trước khi Export.")
            return None
        export = self._build_export(self._applied_shared.target_kind, destination)
        if export is None or self._result is None:
            return None
        if confirm is None and self.parent() is not None:
            stats = self._result.statistics
            summary = (
                f"Project: {self._project_name}\n"
                f"Job/Setup: {self.state.job_id} / {self.state.setup_id}\n"
                f"Operations: {', '.join(self._operation_names.get(value, str(value)) for value in self._operation_ids)}\n"
                f"Profile: {self.state.profile_key}\n"
                f"Tools/sections: {len({draft.tool_station for draft in self._operation_drafts.values()})} / {stats.section_count}\n"
                f"Destination: {destination}\n"
                f"Filename: {self._result.plan.shared_context.file_name}\n"
                f"Bytes/SHA-256: {stats.byte_length} / {self._result.output_checksum}\n"
                f"Overwrite: {self._applied_shared.overwrite_policy.value}\n"
                f"Simulation: {self.simulation_summary.text()}\n\n"
                "NOT MACHINE CERTIFIED"
            )
            answer = QMessageBox.question(self, "Confirm assembly export", summary)
            confirm = answer is QMessageBox.StandardButton.Yes
        if confirm is False:
            return None
        try:
            execution = self._service.export_assembly_nc(
                export[0], export[1], current_source=self._current_export_source
            )
        except Exception as error:
            self._external_status = ExternalExportUiStatus.FAILED
            self.message.emit(f"External assembly export failed: {error}")
            self._refresh_projection()
            return None
        self._last_export = execution
        self._external_status = (
            ExternalExportUiStatus.EXPORTED
            if execution.accepted
            else ExternalExportUiStatus.FAILED
        )
        self._show_export_diagnostics(execution.diagnostics)
        self._refresh_managed_entry()
        self._refresh_projection()
        return execution.result

    def _current_export_source(self) -> NCAssemblyExportSourceSnapshot:
        if self._result is None or self._result_stale:
            raise ProjectError("Assembly result is no longer current")
        request, diagnostics = self._capture_request()
        if request is None or any(
            item.severity is DiagnosticSeverity.ERROR for item in diagnostics
        ):
            raise ProjectError("Assembly source is no longer valid")
        if build_assembly_input_fingerprint(request) != self._result.input_fingerprint:
            raise ProjectError("Assembly source changed before export")
        return NCAssemblyExportSourceSnapshot(
            request.project_generation, request, self._result
        )

    def clear_assembly_result(self) -> None:
        if self._result is None:
            return
        self._assembly_service.invalidate_all()
        self._result = None
        self._result_stale = False
        self._navigation = ()
        self.preview.clear()
        self.section_combo.clear()
        self.metadata_label.setText("Preview metadata: —")
        self._set_status(
            ProgramAssemblyUiStatus.DRAFT
            if self._operation_ids
            else ProgramAssemblyUiStatus.MISSING
        )
        self._refresh_projection()

    def clear_managed_artifact(self, confirm: bool | None = None) -> None:
        entry = self._managed_entry
        if (
            entry is None
            or entry.assembly_result_id is None
            or self._project_root is None
            or self._project_id is None
        ):
            return
        if confirm is None:
            answer = QMessageBox.question(
                self,
                "Clear managed assembly artifact",
                "Delete only this project-managed assembly NC artifact and sidecar?",
            )
            confirm = answer is QMessageBox.StandardButton.Yes
        if not confirm:
            return
        try:
            self._service.nc_export_service.clear_managed_assembly_artifact(
                self._project_root,
                self._project_id,
                entry.assembly_result_id,
            )
        except Exception as error:
            self.message.emit(f"Clear managed assembly artifact failed: {error}")
            return
        self._managed_entry = None
        self._refresh_managed_entry()
        self._refresh_projection()

    def show_diagnostics(self) -> None:
        self.diagnostics.setFocus()
        self.message.emit(
            f"Assembly diagnostics: {len(self._diagnostic_values)} item(s)."
        )

    def _set_diagnostics(self, diagnostics: tuple[object, ...]) -> None:
        assembly = tuple(
            item for item in diagnostics if isinstance(item, ProgramAssemblyDiagnostic)
        )
        exports = tuple(
            item for item in diagnostics if isinstance(item, NCExportDiagnostic)
        )
        self._diagnostic_values = (*_sort_diagnostics(assembly), *exports)
        self._render_diagnostics()

    def _show_export_diagnostics(
        self, diagnostics: tuple[NCExportDiagnostic, ...]
    ) -> None:
        if diagnostics:
            self._set_diagnostics(tuple(diagnostics))

    def _render_diagnostics(self) -> None:
        selected = self.diagnostic_filter.currentText().casefold()
        self.diagnostics.setRowCount(0)
        for diagnostic in self._diagnostic_values:
            severity = getattr(diagnostic, "severity", DiagnosticSeverity.ERROR)
            if selected != "all" and severity.value.casefold() != selected:
                continue
            row = self.diagnostics.rowCount()
            self.diagnostics.insertRow(row)
            operation_id = getattr(diagnostic, "operation_id", None)
            section_index = getattr(diagnostic, "section_index", None)
            record_index = getattr(diagnostic, "record_index", None)
            evidence = getattr(diagnostic, "evidence", ())
            values = (
                severity.value.upper(),
                getattr(getattr(diagnostic, "code", None), "value", "export.unknown"),
                str(operation_id or ""),
                "" if section_index is None else str(section_index + 1),
                "" if record_index is None else str(record_index),
                str(getattr(diagnostic, "message_key", "")),
                "; ".join(f"{key}={value}" for key, value in evidence),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(_DIAGNOSTIC_ROLE, diagnostic)
                self.diagnostics.setItem(row, column, item)

    def _diagnostic_selection_changed(self) -> None:
        row = self.diagnostics.currentRow()
        item = self.diagnostics.item(row, 0) if row >= 0 else None
        diagnostic = item.data(_DIAGNOSTIC_ROLE) if item is not None else None
        if not isinstance(diagnostic, ProgramAssemblyDiagnostic):
            return
        if diagnostic.operation_id is not None:
            self._select_operation_row(diagnostic.operation_id)
            self.jump_to_operation(diagnostic.operation_id)
        if diagnostic.section_index is not None:
            navigation = next(
                (
                    value
                    for value in self._navigation
                    if value.section_index == diagnostic.section_index
                ),
                None,
            )
            if navigation is not None:
                line = navigation.start_line + (diagnostic.record_index or 0)
                self._jump_to_line(min(line, navigation.end_line))

    def _operation_location(self, operation_id: OperationId):
        try:
            snapshot = self._service.cam_snapshot
        except Exception:
            return None
        for job in getattr(snapshot, "jobs", ()):
            for setup in getattr(job, "setups", ()):
                for operation in getattr(setup.operation_tree, "operations", ()):
                    if operation.operation_id == operation_id:
                        return job, setup, operation
        return None

    def _render_operations(
        self, *, select_operation_id: OperationId | None = None
    ) -> None:
        selected = select_operation_id or self._selected_assembly_operation_id()
        diagnostics_by_operation: dict[OperationId, list[str]] = {}
        for diagnostic in self._diagnostic_values:
            operation_id = getattr(diagnostic, "operation_id", None)
            if isinstance(operation_id, OperationId):
                diagnostics_by_operation.setdefault(operation_id, []).append(
                    getattr(diagnostic.code, "value", str(diagnostic.code))
                )
        tool_counts: dict[object, int] = {}
        for source in self._sources.values():
            if source is not None:
                fingerprint = source.assembly.content_fingerprint
                tool_counts[fingerprint] = tool_counts.get(fingerprint, 0) + 1
        self._widget_guard = True
        try:
            self.operation_table.setRowCount(0)
            for index, operation_id in enumerate(self._operation_ids):
                row = self.operation_table.rowCount()
                self.operation_table.insertRow(row)
                source = self._sources.get(operation_id)
                draft = self._operation_drafts.get(operation_id)
                if source is None:
                    operation_status = "MISSING/DELETED"
                    artifact_status = "MISSING/STALE"
                    strategy = "—"
                    simulation = "MISSING"
                    rpm = "—"
                    estimate = "—"
                else:
                    operation_status = (
                        ("ENABLED" if source.operation.enabled else "DISABLED")
                        + " · "
                        + source.operation.artifact_state.status.value.upper()
                    )
                    artifact_status = (
                        source.artifact.completion_status.value.upper()
                        + " · "
                        + (
                            source.artifact.artifact_fingerprint.digest[:12]
                            if source.artifact.artifact_fingerprint is not None
                            else "NO FP"
                        )
                    )
                    strategy = source.operation.strategy_key
                    simulation = self._simulation_status(source)
                    rpm = _spindle_summary(source)
                    estimate = str(len(source.artifact.events) + 12)
                errors = diagnostics_by_operation.get(operation_id, [])
                if errors:
                    compatibility = " · ".join(sorted(set(errors)))
                elif source is not None and tool_counts.get(
                    source.assembly.content_fingerprint, 0
                ) > 1:
                    compatibility = "OK · SHARED TOOL / SEPARATE SECTION"
                elif source is not None and source.operation.strategy_key == "tapping_v1":
                    compatibility = "assembly.unsupported_tapping"
                else:
                    compatibility = "OK"
                values = (
                    str(index + 1),
                    self._operation_names.get(operation_id, "Operation"),
                    strategy,
                    operation_status,
                    artifact_status,
                    simulation,
                    str(draft.tool_station) if draft else "—",
                    str(draft.length_offset) if draft else "—",
                    (
                        str(draft.diameter_offset)
                        if draft and draft.diameter_offset is not None
                        else "—"
                    ),
                    (
                        f"{draft.safe_z:.4f}"
                        if draft and draft.safe_z is not None
                        else "MISSING"
                    ),
                    draft.cutter_compensation.value if draft else "—",
                    rpm,
                    estimate,
                    compatibility,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(_OPERATION_ID_ROLE, str(operation_id))
                    self.operation_table.setItem(row, column, item)
        finally:
            self._widget_guard = False
        if selected is not None:
            self._select_operation_row(selected)
        elif self._operation_ids:
            self.operation_table.selectRow(0)
        else:
            self._load_operation_widgets(None)

    def _select_operation_row(self, operation_id: OperationId) -> None:
        for row in range(self.operation_table.rowCount()):
            item = self.operation_table.item(row, 0)
            if item is not None and item.data(_OPERATION_ID_ROLE) == str(operation_id):
                self.operation_table.selectRow(row)
                return

    @staticmethod
    def _simulation_status(source: PostSourceSnapshot) -> str:
        result = source.simulation_result
        if result is None:
            return "MISSING"
        if (
            result.operation_id != source.operation.operation_id
            or result.artifact_id != source.artifact.artifact_id
            or result.artifact_fingerprint != source.artifact.artifact_fingerprint
        ):
            return "STALE/MALFORMED"
        return result.status.value.upper()

    def _simulation_counts(self) -> tuple[int, int, int, int, int]:
        passed = warned = missing = failed = stale = 0
        for operation_id in self._operation_ids:
            source = self._sources.get(operation_id)
            if source is None:
                stale += 1
                continue
            status = self._simulation_status(source)
            if status == "PASS":
                passed += 1
            elif status == "WARN":
                warned += 1
            elif status == "FAIL":
                failed += 1
            elif status == "MISSING":
                missing += 1
            else:
                stale += 1
        return passed, warned, missing, failed, stale

    def _invalidate_result(
        self, reason: str, *, mark_managed: bool = True
    ) -> None:
        self._validated_request = None
        self._validated_fingerprint = None
        if self._result is not None:
            self._result_stale = True
            self._set_status(ProgramAssemblyUiStatus.STALE)
            stale = _diagnostic(
                ProgramAssemblyDiagnosticCode.STALE,
                reason,
                severity=DiagnosticSeverity.WARNING,
            )
            current = tuple(
                item
                for item in self._diagnostic_values
                if isinstance(item, ProgramAssemblyDiagnostic)
            )
            self._set_diagnostics((*current, stale))
        if self._external_status is ExternalExportUiStatus.EXPORTED:
            self._external_status = ExternalExportUiStatus.OUTDATED
        for operation_id in tuple(self._operation_ids):
            self._assembly_service.mark_operation_stale(operation_id)
            if mark_managed:
                try:
                    self._service.nc_export_service.mark_operation_stale(
                        operation_id
                    )
                except Exception:
                    logger.warning(
                        "Không thể đánh dấu managed assembly artifact stale",
                        exc_info=True,
                    )
        self._refresh_managed_entry()
        self._update_action_enabled()

    def _refresh_managed_entry(self) -> None:
        self._managed_entry = None
        if self._project_id is None:
            return
        try:
            entries = tuple(
                item
                for item in self._service.nc_export_service.artifacts()
                if item.assembly_result_id is not None
            )
        except Exception:
            return
        if self._result is not None:
            self._managed_entry = next(
                (
                    item
                    for item in entries
                    if item.assembly_result_id == self._result.result_id
                ),
                None,
            )
        if self._managed_entry is None and self._operation_ids:
            ordered = tuple(self._operation_ids)
            self._managed_entry = next(
                (
                    item
                    for item in entries
                    if item.assembly_operation_ids == ordered
                ),
                None,
            )
        if self._managed_entry is None and entries:
            self._managed_entry = sorted(
                entries, key=lambda item: (item.output_relative_path.casefold(), str(item.artifact_id))
            )[0]

    def _managed_status(self) -> ManagedArtifactUiStatus:
        if self._managed_entry is None:
            return ManagedArtifactUiStatus.MISSING
        return ManagedArtifactUiStatus(self._managed_entry.status.value)

    def _refresh_projection(self) -> None:
        passed, warned, missing, failed, stale = self._simulation_counts()
        managed = self._managed_status()
        result = self._result
        stats = result.statistics if result is not None else None
        job_id = setup_id = machine_id = None
        if self._operation_ids:
            location = self._operation_location(self._operation_ids[0])
            source = self._sources.get(self._operation_ids[0])
            if location is not None:
                job_id = location[0].job_id
                setup_id = location[1].setup_id
            if source is not None and source.machine is not None:
                machine_id = source.machine.machine_id
        self.state = ProgramAssemblyPanelState(
            project_id=self._project_id,
            job_id=job_id,
            setup_id=setup_id,
            machine_id=machine_id,
            profile_key=self._applied_shared.profile_key,
            work_offset=self._applied_shared.work_offset,
            simulation_gate=self._applied_shared.simulation_gate.value.upper(),
            assembly_status=self.state.assembly_status,
            progress_phase=self.state.progress_phase,
            managed_status=managed,
            external_status=self._external_status,
            operation_count=len(self._operation_ids),
            section_count=stats.section_count if stats else 0,
            tool_change_count=stats.tool_change_count if stats else 0,
            pass_count=passed,
            warn_count=warned,
            optional_missing_count=missing,
            fail_count=failed,
            stale_simulation_count=stale,
            line_count=stats.line_count if stats else 0,
            byte_count=stats.byte_length if stats else (
                self._managed_entry.byte_length if self._managed_entry else 0
            ),
            checksum=(
                result.output_checksum
                if result is not None
                else self._managed_entry.sha256 if self._managed_entry else None
            ),
            assembly_fingerprint=(
                result.result_fingerprint.digest
                if result is not None and result.result_fingerprint is not None
                else (
                    self._managed_entry.assembly_result_fingerprint.digest
                    if self._managed_entry is not None
                    and self._managed_entry.assembly_result_fingerprint is not None
                    else None
                )
            ),
        )
        self.project_value.setText(
            f"{self._project_name} · {self._project_id}"
            if self._project_id is not None
            else "—"
        )
        self.job_setup_value.setText(f"{job_id or '—'} / {setup_id or '—'}")
        self.machine_value.setText(str(machine_id or "—"))
        self.profile_value.setText(
            f"{self.state.profile_key} · MM · G54 · ABSOLUTE · XY · .fn · CRLF · UTF-8"
        )
        self.simulation_summary.setText(
            f"PASS {passed} · WARN {warned} · OPTIONAL MISSING {missing} · FAIL {failed} · STALE/MALFORMED {stale}"
        )
        self.artifact_summary.setText(
            f"Assembly {self.state.assembly_status.value.upper()} · "
            f"Managed {managed.value.upper()} · "
            f"External {self._external_status.value.upper()} · "
            f"operations {len(self._operation_ids)} · sections {self.state.section_count} · "
            f"tool changes {self.state.tool_change_count} · bytes {self.state.byte_count} · "
            f"SHA-256 {self.state.checksum or '—'}"
        )
        self.status_label.setText(
            f"Program {self.state.assembly_status.value.upper()} · "
            f"Phase {self.state.progress_phase.value.upper()} · "
            f"Managed {managed.value.upper()} · External {self._external_status.value.upper()} · "
            "NOT CERTIFIED / REVIEW REQUIRED"
        )
        self.state_changed.emit(self.state)
        self._update_action_enabled()

    def _set_status(self, status: ProgramAssemblyUiStatus) -> None:
        self.state = replace(self.state, assembly_status=status)
        self._refresh_projection()
        self._update_action_enabled()

    def _set_progress(self, phase: ProgramAssemblyProgressPhase) -> None:
        self.state = replace(self.state, progress_phase=phase)
        self._refresh_projection()
        self.progress_changed.emit(phase)
        self.state_changed.emit(self.state)

    def _set_project_enabled(self, enabled: bool) -> None:
        for widget in (
            self.filename_edit,
            self.metadata_edit,
            self.profile_combo,
            self.work_offset_combo,
            self.gate_combo,
            self.overwrite_combo,
            self.target_kind_combo,
            self.target_edit,
            self.create_target_check,
            self.apply_context_button,
        ):
            widget.setEnabled(enabled)
        self._update_action_enabled()

    def _update_action_enabled(self) -> None:
        project = self._project_id is not None
        selected_row = self._selected_assembly_operation_id()
        index = (
            self._operation_ids.index(selected_row)
            if selected_row in self._operation_ids
            else -1
        )
        current = (
            self._result is not None
            and not self._result_stale
            and self.state.assembly_status is ProgramAssemblyUiStatus.CURRENT
            and not self._shared_widget_dirty
            and not self._operation_widget_dirty
        )
        validated = (
            self._validated_request is not None
            and self._validated_fingerprint is not None
            and self.state.assembly_status
            in {ProgramAssemblyUiStatus.VALID, ProgramAssemblyUiStatus.CURRENT}
            and not self._shared_widget_dirty
            and not self._operation_widget_dirty
        )
        self.add_button.setEnabled(project and self._selected_operation_id is not None)
        self.remove_button.setEnabled(index >= 0)
        self.move_up_button.setEnabled(index > 0)
        self.move_down_button.setEnabled(0 <= index < len(self._operation_ids) - 1)
        self.clear_list_button.setEnabled(bool(self._operation_ids))
        for widget in (
            self.tool_station_spin,
            self.length_offset_spin,
            self.diameter_offset_spin,
            self.safe_z_spin,
            self.compensation_combo,
            self.tool_comment_edit,
            self.apply_operation_button,
            self.reset_operation_button,
            self.equalize_offsets_button,
        ):
            widget.setEnabled(index >= 0)
        self.validate_button.setEnabled(
            project and bool(self._operation_ids) and self._active_thread is None
        )
        self.generate_button.setEnabled(
            bool(validated and self._active_thread is None)
        )
        self.preview_button.setEnabled(current)
        self.save_button.setEnabled(current)
        self.export_button.setEnabled(current)
        self.clear_result_button.setEnabled(self._result is not None)
        self.clear_managed_button.setEnabled(self._managed_entry is not None)
        self.search_button.setEnabled(current)
        self.copy_checksum_button.setEnabled(current)
        self.section_combo.setEnabled(current)
        self.jump_section_button.setEnabled(current)

    def _browse_target(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select assembly export directory"
        )
        if directory:
            self.target_edit.setText(directory)

    def _cancel_worker(self) -> None:
        if self._active_thread is None:
            return
        self._set_progress(ProgramAssemblyProgressPhase.CANCELLED)
        self._active_thread.quit()
        self._active_thread.wait(1000)
        self._active_thread = None
        self._active_worker = None


def _spindle_summary(source: PostSourceSnapshot) -> str:
    speeds: list[str] = []
    for event in source.artifact.events:
        speed = getattr(event, "speed", None)
        value = getattr(speed, "value", None)
        state = getattr(getattr(event, "state", None), "value", None)
        if value is not None and state not in {None, "off"}:
            speeds.append(f"{state.upper()} {float(value):g}")
    return ", ".join(dict.fromkeys(speeds)) if speeds else "—"


def _evidence_text(error: object) -> str:
    return (" ".join(str(error).split()) or type(error).__name__)[:256]


__all__ = [
    "AssemblyOperationDraft",
    "AssemblySharedDraft",
    "ProgramAssemblyPanel",
    "ProgramAssemblyPanelState",
    "ProgramAssemblyProgressPhase",
    "ProgramAssemblyUiStatus",
    "SectionNavigation",
    "parse_global_metadata",
]
