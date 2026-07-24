"""Production Function Editor binding for Parallel Finishing Stage 8A.2.3."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Mapping
from uuid import UUID

from hms_cadcam.cam.application import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)

from hms_cadcam.cam.cam3d import (
    Cam3DSafeMotionPolicy,
    Cam3DSafeTransitionPolicy,
    Cam3DStockAllowance,
    Cam3DTolerancePolicy,
    CamSurfaceReference,
    CamSurfaceSelection,
    MachiningZone3D,
    PartSurfaceSet,
    wcs_fingerprint,
)
from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    PARALLEL_FINISHING_STRATEGY_VERSION,
    ParallelAutomaticContext,
    ParallelCutDirection,
    ParallelFinishingParameters,
    ParallelGeometryEvidence,
    ParallelLinkingMode,
    ParallelSafetyReport,
    ParallelSafetyStatus,
    parallel_artifact_has_safe_contract,
    resolve_parallel_automatic_contract,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BallEndGeometry,
    CamJobId,
    CamSurfaceSelectionId,
    ContentFingerprint,
    DependencyFingerprint,
    DirtyReason,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReferenceKind,
    HolderDefinition,
    LengthUnit,
    MachineDefinition,
    MachineRequirement,
    MachiningZone3DId,
    Operation,
    OperationParameterSet,
    OperationCapability,
    OperationGeometryInput,
    ToolAssembly,
    ToolAssemblyReference,
    ToolDefinition,
    ToolFamily,
    Vector3,
    Setup,
)
from hms_cadcam.cam.toolpath import MarkerEvent, ToolpathArtifact
from hms_cadcam.cam.tool_profile_integration import (
    apply_tool_profile_to_automatic_contract,
)
from hms_cadcam.ui.function_editor.model import (
    ApplicabilityOperator,
    FunctionEditorAction,
    FunctionEditorApplicability,
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorResetBehavior,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    FunctionEditorValidationKind,
    FunctionEditorValidationRule,
    FunctionEditorValueConversion,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema
from hms_cadcam.ui.localization import (
    display_value,
    display_value_list,
    translate_status,
    ui_text,
)


PARALLEL_EDITOR_ID = "parallel_finishing_production_8a2_3"
PARALLEL_EDITOR_STRATEGY_KEY = "parallel_finishing_3d_8a2_3"
_DEFAULT_TOLERANCE_MM = 0.01


@dataclass(frozen=True, slots=True)
class ParallelEditorContext:
    """Immutable native-free snapshot consumed by one editor session."""

    operation_name: str
    operation: Operation
    setup: Setup
    job_id: CamJobId
    project_id: UUID
    zone: MachiningZone3D | None
    tool_assemblies: tuple[ToolAssembly, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    holder_definitions: tuple[HolderDefinition, ...]
    machine_definitions: tuple[MachineDefinition, ...]
    artifact: ToolpathArtifact | None = None
    safety_report: ParallelSafetyReport | None = None
    geometry_resolved: bool = True
    geometry_diagnostic: str = ""
    geometry_evidence: ParallelGeometryEvidence | None = None

    def __post_init__(self) -> None:
        if self.operation.strategy_key != "parallel_finishing_3d":
            raise ValueError("Parallel editor requires a Parallel Finishing operation")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("Parallel editor project identity is invalid")
        setup_id = getattr(self.setup, "setup_id", None)
        if setup_id != self.operation.setup_id or not isinstance(self.job_id, CamJobId):
            raise ValueError("Parallel operation belongs to another Setup")
        if self.zone is not None and (
            self.zone.project_id != self.project_id
            or self.zone.setup_id != self.operation.setup_id
        ):
            raise ValueError("Parallel machining zone belongs to another project/Setup")
        if self.geometry_evidence is not None and not isinstance(
            self.geometry_evidence, ParallelGeometryEvidence
        ):
            raise TypeError("Parallel geometry evidence is invalid")


@dataclass(slots=True)
class ParallelEditorDraftContext:
    """Transient face selection; contains domain references but no OCP handles."""

    surfaces: tuple[CamSurfaceReference, ...]
    pending_input_ids: dict[str, GeometryInputId] | None = None
    geometry_evidence: ParallelGeometryEvidence | None = None


@dataclass(frozen=True, slots=True)
class ParallelOperationUpdate:
    """Validated atomic candidate for operation plus CAM 3D zone persistence."""

    operation_name: str
    operation: Operation
    parameters: ParallelFinishingParameters
    zone: MachiningZone3D
    safe_motion_policy: Cam3DSafeMotionPolicy
    assembly: ToolAssembly
    tool: ToolDefinition
    holder: HolderDefinition | None
    machine: MachineDefinition
    automatic_contract: AutomaticParameterContract


@dataclass(frozen=True, slots=True)
class ParallelSafetyPresentation:
    """Compact non-JSON safety state suitable for header, fields and dialogs."""

    status: str
    artifact_state: str
    report_hash: str
    checked_components: str
    unverified_components: str
    holder_state: str
    safety_scope: str
    machine_ready_clearance: str
    finding_counts: str
    diagnostic_summary: str
    simulation_gate: str
    post_gate: str


def _text(value: object, field_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_id} is required.")
    return value.strip()


def _number(value: object, field_id: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_id} must be a finite number.")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_id} must be a finite number.") from error
    if not math.isfinite(result):
        raise ValueError(f"{field_id} must be a finite number.")
    return result


def _boolean(value: object, field_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_id} must be boolean.")
    return value


def _parameters(context: ParallelEditorContext) -> ParallelFinishingParameters:
    return ParallelFinishingParameters.from_operation_parameters(
        context.operation.parameters
    )


def _stored_automatic_contract(
    context: ParallelEditorContext,
) -> AutomaticParameterContract | None:
    raw = dict(context.operation.parameters.values).get(
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    )
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("parallel.invalid_automatic_contract: Automatic CAM metadata is invalid.")
    try:
        return AutomaticParameterContract.from_json(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "parallel.invalid_automatic_contract: Automatic CAM metadata is malformed."
        ) from error


def _boundary_geometry_evidence(
    context: ParallelEditorContext,
) -> ParallelGeometryEvidence | None:
    boundary = context.zone.boundary if context.zone is not None else None
    if boundary is None or not boundary.points:
        return context.geometry_evidence
    frame = getattr(context.setup, "wcs")
    origin = frame.origin
    values: list[tuple[float, float]] = []
    for point in boundary.points:
        dx = point.x - origin.x
        dy = point.y - origin.y
        dz = point.z - origin.z
        values.append(
            (
                dx * frame.x_axis.x + dy * frame.x_axis.y + dz * frame.x_axis.z,
                dx * frame.y_axis.x + dy * frame.y_axis.y + dz * frame.y_axis.z,
            )
        )
    return ParallelGeometryEvidence(
        float(min(item[0] for item in values)),
        float(max(item[0] for item in values)),
        float(min(item[1] for item in values)),
        float(max(item[1] for item in values)),
        "Biên vùng gia công trong hệ tọa độ Thiết lập",
    )


def _automatic_context(
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext | None,
    values: Mapping[str, PresentationValue],
) -> ParallelAutomaticContext:
    surfaces = draft.surfaces if draft is not None else _surfaces(context)
    geometry_fingerprint = GeometryFingerprint.from_payload(
        {"parallel_faces": [item.identity_payload() for item in surfaces]}
    )
    selection_fingerprint = DependencyFingerprint.from_payload(
        {"parallel_faces": [item.identity_payload() for item in surfaces]}
    )
    assembly_id = str(
        values.get("tool_assembly_id", context.operation.tool_assembly.assembly_id)
    )
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    tool = (
        next(
            (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
            None,
        )
        if assembly is not None
        else None
    )
    holder = (
        next(
            (
                item
                for item in context.holder_definitions
                if assembly.holder_id is not None
                and item.holder_id == assembly.holder_id
            ),
            None,
        )
        if assembly is not None
        else None
    )
    tool_supported = bool(
        tool is not None
        and tool.family is ToolFamily.BALL_END_MILL
        and isinstance(tool.cutting_geometry, BallEndGeometry)
    )
    diameter = (
        tool.cutting_geometry.diameter.to(LengthUnit.MM).value
        if tool_supported and tool is not None
        else 1.0
    )
    tolerance = (
        context.zone.tolerance.chordal_tolerance
        if context.zone is not None
        else _DEFAULT_TOLERANCE_MM
    )
    allowance = (
        context.zone.allowance.part_normal if context.zone is not None else 0.0
    )
    if bool(values.get("tolerance_override_enabled", False)):
        try:
            candidate_tolerance = float(values.get("tolerance_mm"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            candidate_tolerance = tolerance
        if math.isfinite(candidate_tolerance) and candidate_tolerance > 0.0:
            tolerance = candidate_tolerance
    if bool(values.get("allowance_override_enabled", False)):
        try:
            candidate_allowance = float(values.get("surface_allowance_mm"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            candidate_allowance = allowance
        if math.isfinite(candidate_allowance) and candidate_allowance >= 0.0:
            allowance = candidate_allowance
    return ParallelAutomaticContext(
        geometry_fingerprint,
        selection_fingerprint,
        wcs_fingerprint(getattr(context.setup, "wcs")),
        (
            tool.content_fingerprint
            if tool is not None
            else ContentFingerprint.from_payload({"missing_parallel_tool": assembly_id})
        ),
        holder.content_fingerprint if holder is not None else None,
        float(diameter),
        float(tolerance),
        float(allowance),
        len(surfaces),
        (
            draft.geometry_evidence
            if draft is not None and draft.geometry_evidence is not None
            else _boundary_geometry_evidence(context)
        ),
        tool_supported,
    )


def _quality_profile(
    values: Mapping[str, PresentationValue],
    stored: AutomaticParameterContract | None,
) -> CamQualityProfile:
    raw = values.get(
        "quality_profile",
        stored.quality_profile.value if stored is not None else CamQualityProfile.BALANCED.value,
    )
    try:
        return CamQualityProfile(str(raw))
    except ValueError as error:
        raise ValueError("parallel.invalid_quality: Quality profile is invalid.") from error


def _resolve_automatic_contract(
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext | None,
    values: Mapping[str, PresentationValue],
) -> AutomaticParameterContract:
    stored = _stored_automatic_contract(context)
    parameters = _parameters(context)
    tolerance = (
        context.zone.tolerance.chordal_tolerance
        if context.zone is not None
        else _DEFAULT_TOLERANCE_MM
    )
    allowance = context.zone.allowance.part_normal if context.zone is not None else 0.0
    flags = {
        "direction_angle_degrees": bool(values.get("direction_override_enabled", False)),
        "stepover_mm": bool(values.get("stepover_override_enabled", False)),
        "tolerance_mm": bool(values.get("tolerance_override_enabled", False)),
        "surface_allowance_mm": bool(values.get("allowance_override_enabled", False)),
        "cut_direction": bool(values.get("ordering_override_enabled", False)),
    }
    direction_override = values.get(
        "direction_angle_degrees", parameters.direction_angle_degrees
    )
    if flags["direction_angle_degrees"]:
        direction_mode = str(values.get("direction_override_mode", "custom_angle"))
        if direction_mode == "axis_x":
            direction_override = 0.0
        elif direction_mode == "axis_y":
            direction_override = 90.0
    overrides = {
        "direction_angle_degrees": direction_override,
        "stepover_mm": values.get("stepover_mm", parameters.stepover_mm),
        "tolerance_mm": values.get("tolerance_mm", tolerance),
        "surface_allowance_mm": values.get("surface_allowance_mm", allowance),
        "cut_direction": values.get("cut_direction", parameters.cut_direction.value),
    }
    contract = resolve_parallel_automatic_contract(
        _automatic_context(context, draft, values),
        _quality_profile(values, stored),
        stored=stored,
        manual_flags=flags,
        override_values=overrides,
    )
    assembly_id = str(
        values.get("tool_assembly_id", context.operation.tool_assembly.assembly_id)
    )
    _assembly, tool, holder = _assembly_resources(context, assembly_id)
    if tool is None:
        return contract
    return apply_tool_profile_to_automatic_contract(
        contract,
        tool,
        "parallel_finishing_3d",
        operation_override_keys=frozenset(
            key for key, enabled in flags.items() if enabled
        ),
        operation_id=str(context.operation.operation_id),
        holder_fingerprint=(
            holder.content_fingerprint if holder is not None else None
        ),
    )


def _vn_number(value: object, suffix: str = "") -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = f"{float(value):g}".replace(".", ",")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _automatic_summary(
    contract: AutomaticParameterContract,
    key: str,
    *,
    suffix: str = "",
) -> str:
    value = contract.value(key)
    mode = display_value(value.mode, "automatic_mode")
    status = display_value(value.status, "automatic_status")
    display = _vn_number(value.effective_value, suffix)
    if key == "cut_direction":
        display = display_value(value.effective_value, "cut_direction")
    if key == "linking_mode":
        display = "Rút dao giữa các đoạn"
    source = _localized_policy_text(value.source)
    reason = _localized_policy_text(value.reason)
    return (
        f"{display} · {mode} · {status} · "
        f"Nguồn: {source} · {reason}"
    )


def _localized_policy_text(value: object) -> str:
    """Translate policy enum tokens while leaving user/domain text untouched."""
    text = ui_text(value)
    replacements = {
        "balanced": "Cân bằng",
        "fast": "Nhanh",
        "high": "Chất lượng cao",
        "standard": "Tiêu chuẩn",
        "dense": "Dày",
        "very_dense": "Rất dày",
    }
    for source, target in replacements.items():
        text = re.sub(rf"(?<![\w.-]){re.escape(source)}(?![\w.-])", target, text, flags=re.IGNORECASE)
    return text


def _automatic_mode_counts(contract: AutomaticParameterContract) -> str:
    manual = sum(
        item.mode is AutomaticParameterMode.MANUAL for item in contract.values
    )
    automatic = len(contract.values) - manual
    return f"{automatic} tham số tự động · {manual} tham số tùy chỉnh"


def _surfaces(context: ParallelEditorContext) -> tuple[CamSurfaceReference, ...]:
    if context.zone is not None:
        return context.zone.part_surfaces.selection.surfaces
    return ()


def _assembly_resources(
    context: ParallelEditorContext,
    assembly_id: str | None = None,
) -> tuple[ToolAssembly | None, ToolDefinition | None, HolderDefinition | None]:
    target = assembly_id or str(context.operation.tool_assembly.assembly_id)
    assembly = next(
        (
            item
            for item in context.tool_assemblies
            if str(item.assembly_id) == target
        ),
        None,
    )
    tool = (
        next(
            (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
            None,
        )
        if assembly is not None
        else None
    )
    holder = (
        next(
            (
                item
                for item in context.holder_definitions
                if assembly.holder_id is not None and item.holder_id == assembly.holder_id
            ),
            None,
        )
        if assembly is not None
        else None
    )
    return assembly, tool, holder


def _tool_text(
    assembly: ToolAssembly | None,
    tool: ToolDefinition | None,
) -> str:
    if assembly is None:
        return "Thiếu cụm Tool"
    if tool is None:
        return f"{assembly.name} · Thiếu định nghĩa Tool"
    geometry = tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    ball_radius = diameter.value / 2.0 if diameter is not None else 0.0
    cutting_length = getattr(geometry, "axial_cutting_length", tool.usable_length)
    shank_diameter = getattr(tool.shank, "diameter", None)
    shank_text = (
        "chưa xác định"
        if shank_diameter is None
        else f"{shank_diameter.value:g} {shank_diameter.unit.value}"
    )
    return (
        f"{tool.name} · "
        f"D{diameter.value:g} · R{ball_radius:g} · "
        f"chiều dài cắt {cutting_length.value:g} · cán dao {shank_text}"
        if diameter is not None
        else f"{tool.name} · hình học không được hỗ trợ"
    )


def _holder_text(
    assembly: ToolAssembly | None,
    holder: HolderDefinition | None,
) -> tuple[str, str, str, str]:
    if assembly is None:
        return (
            display_value("missing", "holder_state"),
            "Không có",
            display_value_list("cutter, shank, holder", "safety_component"),
            display_value("incomplete_tool_assembly", "safety_scope"),
        )
    if assembly.holder_id is None:
        return (
            "Chưa khai báo Holder · Đã kiểm tra Dao cắt và Cán dao · "
            "Holder chưa được xác minh",
            display_value_list("cutter, shank", "safety_component"),
            display_value("holder", "safety_component"),
            display_value("declared_assembly_holder_absent", "safety_scope"),
        )
    if holder is None:
        return (
            display_value("missing", "holder_state"),
            "Không có",
            display_value_list("cutter, shank, holder", "safety_component"),
            display_value("incomplete_tool_assembly", "safety_scope"),
        )
    valid = (
        holder.holder_id == assembly.holder_id
        and holder.revision == assembly.expected_holder_revision
        and holder.content_fingerprint == assembly.expected_holder_fingerprint
        and holder.unit is assembly.unit
    )
    if not valid:
        return (
            display_value("invalid", "holder_state"),
            "Không có",
            display_value_list("cutter, shank, holder", "safety_component"),
            display_value("incomplete_tool_assembly", "safety_scope"),
        )
    return (
        "Holder đã được xác minh · Đã kiểm tra Dao cắt, Cán dao và Holder",
        display_value_list("cutter, shank, holder", "safety_component"),
        "Không có",
        display_value("declared_assembly_holder_verified", "safety_scope"),
    )


def _holder_reference_is_current(
    assembly: ToolAssembly,
    holder: HolderDefinition | None,
) -> bool:
    return bool(
        assembly.holder_id is not None
        and holder is not None
        and holder.holder_id == assembly.holder_id
        and holder.revision == assembly.expected_holder_revision
        and holder.content_fingerprint == assembly.expected_holder_fingerprint
        and holder.unit is assembly.unit
    )


def _safety_marker(artifact: ToolpathArtifact | None) -> dict[str, str]:
    if artifact is None:
        return {}
    markers = tuple(
        event
        for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "parallel.safety.contract"
    )
    return dict(markers[0].metadata) if len(markers) == 1 else {}


def parallel_safety_presentation(
    context: ParallelEditorContext,
) -> ParallelSafetyPresentation:
    """Resolve runtime/report/artifact evidence without claiming machine safety."""
    report = context.safety_report
    marker = _safety_marker(context.artifact)
    status = "Not calculated"
    if report is not None:
        status = {
            ParallelSafetyStatus.SAFE: "Safe — verified within declared scope",
            ParallelSafetyStatus.UNSAFE: "Unsafe",
            ParallelSafetyStatus.UNKNOWN: "Unknown",
            ParallelSafetyStatus.CANCELLED: "Cancelled",
            ParallelSafetyStatus.FAILED: "Failed",
        }[report.status]
    elif context.operation.artifact_state.status is ArtifactStatus.COMPUTING:
        status = "Candidate"
    elif context.operation.artifact_state.status is ArtifactStatus.DIRTY:
        status = "Stale"
    elif context.operation.artifact_state.status is ArtifactStatus.FAILED:
        evidence = {
            key: value
            for item in context.operation.artifact_state.diagnostics
            for key, value in item.context
        }
        diagnostic_status = evidence.get("safety_status", "")
        status = {
            "unsafe": "Unsafe",
            "unknown": "Unknown",
            "cancelled": "Cancelled",
            "failed": "Failed",
        }.get(diagnostic_status, "Failed")
    elif (
        context.operation.artifact_state.status is ArtifactStatus.VALID
        and context.artifact is not None
        and parallel_artifact_has_safe_contract(context.artifact)
    ):
        status = "Safe — verified within declared scope"

    assembly, _tool, holder = _assembly_resources(context)
    holder_summary, checked, unverified, scope = _holder_text(assembly, holder)
    if report is not None:
        checked = display_value_list(report.checked_components, "safety_component")
        unverified = display_value_list(
            report.unverified_components, "safety_component"
        )
        scope = display_value(report.safety_scope, "safety_scope")
        holder_summary = {
            "geometry_faithful": (
                "Holder đã được xác minh · Đã kiểm tra Dao cắt, Cán dao và Holder"
            ),
            "declared_absent": (
                "Chưa khai báo Holder · Đã kiểm tra Dao cắt và Cán dao · "
                "Holder chưa được xác minh"
            ),
            "missing": display_value("missing", "holder_state"),
            "reference_invalid": display_value("invalid", "holder_state"),
        }[report.holder_state]
    elif marker:
        checked = display_value_list(
            marker.get("checked_components", checked), "safety_component"
        )
        unverified = display_value_list(
            marker.get("unverified_components", unverified), "safety_component"
        )
        scope = display_value(marker.get("safety_scope", scope), "safety_scope")
        holder_state = marker.get("holder_state")
        if holder_state == "geometry_faithful":
            holder_summary = (
                "Holder đã được xác minh · Đã kiểm tra Dao cắt, Cán dao và Holder"
            )
        elif holder_state == "declared_absent":
            holder_summary = (
                "Chưa khai báo Holder · Đã kiểm tra Dao cắt và Cán dao · "
                "Holder chưa được xác minh"
            )

    report_hash = (
        report.fingerprint.digest
        if report is not None
        else marker.get("safety_report_fingerprint", "")
    )
    diagnostics = report.diagnostics if report is not None else ()
    def occurrences(token: str) -> int:
        return sum(
            item.occurrence_count
            for item in diagnostics
            if token in item.code.value
        )

    finding_counts = (
        "Not calculated"
        if report is None
        else (
            f"chẩn đoán {len(diagnostics)} · lỗi ăn lẹm "
            f"{occurrences('gouge') + occurrences('protected_face_collision')} · "
            f"cán dao {occurrences('shank_collision')} · "
            f"Holder {occurrences('holder_collision')} · "
            f"liên kết/chạy nhanh "
            f"{occurrences('link_collision') + occurrences('rapid_collision')}"
        )
    )
    first = diagnostics[0] if diagnostics else None
    diagnostic_summary = (
        "No safety findings"
        if first is None
        else (
            f"{len(diagnostics)} phát hiện · {first.code.value} · "
            f"{ui_text(first.message)}"
        )
    )
    safe_gate = bool(
        context.artifact is not None
        and context.operation.artifact_state.status is ArtifactStatus.VALID
        and context.artifact.operation_revision == context.operation.revision
        and parallel_artifact_has_safe_contract(context.artifact)
    )
    simulation = (
        "Available · READY + SAFE v3"
        if safe_gate
        else "Blocked · requires current READY + SAFE algorithm v3 artifact"
    )
    post = (
        "Blocked · Parallel production Post capability is not available"
        if safe_gate
        else "Blocked · artifact/safety capability is insufficient"
    )
    return ParallelSafetyPresentation(
        ui_text(status),
        translate_status(context.operation.artifact_state.status.value.upper()),
        report_hash,
        checked,
        unverified,
        holder_summary,
        scope,
        "Chưa xác minh",
        finding_counts,
        diagnostic_summary,
        simulation,
        post,
    )


def parallel_applied_values(
    context: ParallelEditorContext,
) -> dict[str, PresentationValue]:
    """Convert applied domain state to deterministic presentation primitives."""
    parameters = _parameters(context)
    stored = _stored_automatic_contract(context)
    surfaces = _surfaces(context)
    assembly, tool, holder = _assembly_resources(context)
    holder_summary, checked, unverified, scope = _holder_text(assembly, holder)
    safety = parallel_safety_presentation(context)
    tolerance = (
        context.zone.tolerance
        if context.zone is not None
        else Cam3DTolerancePolicy(0.01, 0.2, 1.0e-8, 0.001, 0.001)
    )
    allowance = context.zone.allowance.part_normal if context.zone is not None else 0.0
    def override_value(key: str, fallback: object) -> object:
        if stored is None:
            return fallback
        try:
            value = stored.value(key).override_value
        except KeyError:
            return fallback
        return fallback if value is None else value

    def override_enabled(key: str) -> bool:
        if stored is None:
            return False
        try:
            return stored.value(key).mode is AutomaticParameterMode.MANUAL
        except KeyError:
            return False

    direction_override_value = override_value(
        "direction_angle_degrees", parameters.direction_angle_degrees
    )
    direction_override_mode = "auto"
    if override_enabled("direction_angle_degrees"):
        try:
            direction_number = float(direction_override_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            direction_number = math.nan
        direction_override_mode = (
            "axis_x"
            if math.isclose(direction_number, 0.0, abs_tol=1.0e-12)
            else "axis_y"
            if math.isclose(direction_number, 90.0, abs_tol=1.0e-12)
            else "custom_angle"
        )

    seed: dict[str, PresentationValue] = {
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "quality_profile": (
            stored.quality_profile.value
            if stored is not None
            else CamQualityProfile.BALANCED.value
        ),
        "direction_override_enabled": override_enabled("direction_angle_degrees"),
        "direction_override_mode": direction_override_mode,
        "direction_angle_degrees": str(direction_override_value),
        "stepover_override_enabled": override_enabled("stepover_mm"),
        "stepover_mm": str(override_value("stepover_mm", parameters.stepover_mm)),
        "tolerance_override_enabled": override_enabled("tolerance_mm"),
        "tolerance_mm": str(override_value("tolerance_mm", tolerance.chordal_tolerance)),
        "allowance_override_enabled": override_enabled("surface_allowance_mm"),
        "surface_allowance_mm": str(
            override_value("surface_allowance_mm", allowance)
        ),
        "ordering_override_enabled": override_enabled("cut_direction"),
        "cut_direction": str(
            override_value("cut_direction", parameters.cut_direction.value)
        ),
    }
    automatic = _resolve_automatic_contract(context, None, seed)
    if stored is None:
        # A first-time manual override starts from the current automatic result,
        # not from a legacy numeric value that Basic no longer presents.
        seed.update(
            {
                "direction_angle_degrees": str(
                    automatic.value("direction_angle_degrees").effective_value
                ),
                "stepover_mm": str(automatic.value("stepover_mm").effective_value),
                "tolerance_mm": str(automatic.value("tolerance_mm").effective_value),
                "surface_allowance_mm": str(
                    automatic.value("surface_allowance_mm").effective_value
                ),
                "cut_direction": str(
                    automatic.value("cut_direction").effective_value
                ),
            }
        )
    effective_direction = automatic.value("direction_angle_degrees").effective_value
    machine_id = (
        ""
        if context.operation.machine_requirement is None
        else str(context.operation.machine_requirement.machine_id)
    )
    direction = (
        context.zone.machining_direction
        if context.zone is not None and context.zone.machining_direction is not None
        else getattr(context.setup, "wcs").x_axis
    )
    geometry_state = (
        "RESOLVED"
        if context.geometry_resolved and surfaces
        else "STALE"
        if surfaces
        else "MISSING"
    )
    geometry_summary = (
        f"{len(surfaces)} bề mặt gia công · "
        f"{display_value(geometry_state, 'geometry_resolution')}"
    )
    if context.geometry_diagnostic:
        geometry_summary = f"{geometry_summary} · {context.geometry_diagnostic}"
    return {
        "operation_name": context.operation_name,
        "operation_type": "Parallel Finishing",
        "enabled": context.operation.enabled,
        "geometry_summary": geometry_summary,
        "selected_face_count": str(len(surfaces)),
        "selected_body_setup_summary": (
            f"Thiết lập: {getattr(context.setup, 'name', '')} · "
            f"{display_value(getattr(context.setup, 'work_offset').name, 'setup_role')}"
        ),
        "geometry_reference_summary": ", ".join(
            str(item.geometry.reference_id.value)[:8] for item in surfaces
        ) or "Không có",
        "reselect_geometry": "Replace the draft face selection from the viewport",
        "remove_geometry": "Remove currently selected viewport faces from the draft",
        "clear_geometry": "Clear the draft face selection",
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "tool_details": _tool_text(assembly, tool),
        "holder_state": holder_summary,
        "holder_scope": (
            f"{scope} · đã kiểm tra: {checked} · chưa xác minh: {unverified}"
        ),
        "quality_profile": seed["quality_profile"],
        "automatic_policy_summary": (
            "HMS tự tính theo hình học, dao, Thiết lập và hồ sơ chất lượng; "
            "chỉ bật Tùy chỉnh thủ công khi cần."
        ),
        "automatic_direction_summary": _automatic_summary(
            automatic, "direction_angle_degrees", suffix="°"
        ),
        "automatic_stepover_summary": _automatic_summary(
            automatic, "stepover_mm", suffix=" mm"
        ),
        "automatic_tolerance_summary": _automatic_summary(
            automatic, "tolerance_mm", suffix=" mm"
        ),
        "automatic_allowance_summary": _automatic_summary(
            automatic, "surface_allowance_mm", suffix=" mm"
        ),
        "automatic_ordering_summary": _automatic_summary(automatic, "cut_direction"),
        "automatic_linking_summary": _automatic_summary(automatic, "linking_mode"),
        "automatic_holder_summary": _automatic_summary(automatic, "holder_context"),
        "automatic_effective_hash": automatic.effective_fingerprint.digest[:12],
        "automatic_mode_counts": _automatic_mode_counts(automatic),
        "effective_direction_angle_degrees": str(effective_direction),
        "direction_mode": "Tự động theo chiều chính; có thể tùy chỉnh thủ công",
        "direction_override_enabled": seed["direction_override_enabled"],
        "direction_override_mode": seed["direction_override_mode"],
        "direction_angle_degrees": seed["direction_angle_degrees"],
        "direction_preview": f"U base ({direction.x:g}, {direction.y:g}, {direction.z:g}) · V=W×U · W=Setup Z",
        "workplane_summary": (
            f"{display_value(getattr(context.setup, 'work_offset').name, 'setup_role')} "
            "· ba trục cố định"
        ),
        "stepover_override_enabled": seed["stepover_override_enabled"],
        "stepover_mm": seed["stepover_mm"],
        "tolerance_override_enabled": seed["tolerance_override_enabled"],
        "tolerance_mm": seed["tolerance_mm"],
        "allowance_override_enabled": seed["allowance_override_enabled"],
        "surface_allowance_mm": seed["surface_allowance_mm"],
        "ordering_override_enabled": seed["ordering_override_enabled"],
        "cut_direction": seed["cut_direction"],
        "clearance_z_mm": str(parameters.clearance_z_mm),
        "retract_z_mm": str(parameters.retract_z_mm),
        "link_clearance_mm": str(parameters.link_clearance_mm),
        "linking_mode": "Rút dao giữa các đoạn",
        "conservative_linking_summary": "Retract between segments · horizontal rapid at clearance",
        "feed_rate_mm_per_minute": str(parameters.feed_rate_mm_per_minute),
        "maximum_segment_length_mm": str(parameters.maximum_segment_length_mm),
        "contact_tolerance_mm": str(tolerance.contact_tolerance),
        "internal_detection_threshold": "Derived safety minimum · not a certified machining clearance",
        "guardrail_summary": "20,000 passes · 25,000 points/curve · 100,000 points/result",
        "machine_id": machine_id,
        "capability_summary": "Ball-end · fixed 3-axis · selected trimmed BRep faces · one-way/zigzag · Toolpath IR · Simulation · safety validation",
        "unsupported_summary": "Flat/bull end, 5-axis, holder avoidance, rest machining, adaptive cusp, machine-ready clearance and production Post are unavailable",
        "calculation_status": safety.artifact_state,
        "safety_status": safety.status,
        "safety_algorithm_version": (
            f"v{context.safety_report.algorithm_version}"
            if context.safety_report is not None
            else "v3 required"
        ),
        "safety_scope": safety.safety_scope,
        "checked_components": safety.checked_components,
        "unverified_components": safety.unverified_components,
        "machine_ready_clearance": safety.machine_ready_clearance,
        "safety_finding_counts": safety.finding_counts,
        "safety_report_hash": safety.report_hash[:12] if safety.report_hash else "Not calculated",
        "diagnostic_summary": safety.diagnostic_summary,
        "simulation_gate": safety.simulation_gate,
        "post_gate": safety.post_gate,
        "summary": (
            f"{len(surfaces)} bề mặt · {_tool_text(assembly, tool)} · "
            f"hồ sơ {display_value(automatic.quality_profile, 'quality_profile')} · "
            f"góc {_vn_number(effective_direction, '°')} · "
            f"bước ngang {_vn_number(automatic.value('stepover_mm').effective_value, ' mm')} · "
            f"dung sai {_vn_number(automatic.value('tolerance_mm').effective_value, ' mm')} · "
            f"lượng dư {_vn_number(automatic.value('surface_allowance_mm').effective_value, ' mm')} · "
            f"{display_value(automatic.value('cut_direction').effective_value, 'cut_direction')} · "
            f"{safety.status} · khoảng hở sẵn sàng cho máy chưa được xác minh"
        ),
    }


def parallel_draft_derived_values(
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Recompute presentation-only automatic summaries after each draft edit."""
    automatic = _resolve_automatic_contract(context, draft, values)
    direction = automatic.value("direction_angle_degrees").effective_value
    stepover = automatic.value("stepover_mm").effective_value
    tolerance = automatic.value("tolerance_mm").effective_value
    allowance = automatic.value("surface_allowance_mm").effective_value
    ordering = automatic.value("cut_direction").effective_value
    assembly_id = str(values.get("tool_assembly_id", ""))
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    tool = (
        next(
            (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
            None,
        )
        if assembly is not None
        else None
    )
    holder = (
        next(
            (
                item
                for item in context.holder_definitions
                if assembly.holder_id is not None and item.holder_id == assembly.holder_id
            ),
            None,
        )
        if assembly is not None
        else None
    )
    holder_summary, checked, unverified, scope = _holder_text(assembly, holder)
    direction_override_mode = str(values.get("direction_override_mode", "auto"))
    if not bool(values.get("direction_override_enabled", False)):
        direction_override_mode = "auto"
    elif direction_override_mode == "auto":
        direction_override_mode = "custom_angle"
    return {
        "tool_details": _tool_text(assembly, tool),
        "holder_state": holder_summary,
        "holder_scope": (
            f"{scope} · đã kiểm tra: {checked} · chưa xác minh: {unverified}"
        ),
        "automatic_direction_summary": _automatic_summary(
            automatic, "direction_angle_degrees", suffix="°"
        ),
        "automatic_stepover_summary": _automatic_summary(
            automatic, "stepover_mm", suffix=" mm"
        ),
        "automatic_tolerance_summary": _automatic_summary(
            automatic, "tolerance_mm", suffix=" mm"
        ),
        "automatic_allowance_summary": _automatic_summary(
            automatic, "surface_allowance_mm", suffix=" mm"
        ),
        "automatic_ordering_summary": _automatic_summary(automatic, "cut_direction"),
        "automatic_linking_summary": _automatic_summary(automatic, "linking_mode"),
        "automatic_holder_summary": _automatic_summary(automatic, "holder_context"),
        "automatic_effective_hash": automatic.effective_fingerprint.digest[:12],
        "automatic_mode_counts": _automatic_mode_counts(automatic),
        "effective_direction_angle_degrees": str(direction),
        "direction_override_mode": direction_override_mode,
        "linking_mode": "Rút dao giữa các đoạn",
        "summary": (
            f"{len(draft.surfaces)} bề mặt · hồ sơ "
            f"{display_value(automatic.quality_profile, 'quality_profile')} · "
            f"góc {_vn_number(direction, '°')} · "
            f"bước ngang {_vn_number(stepover, ' mm')} · "
            f"dung sai {_vn_number(tolerance, ' mm')} · "
            f"lượng dư {_vn_number(allowance, ' mm')} · "
            f"{display_value(ordering, 'cut_direction')} · chưa tính toán"
        ),
    }


def _complete_values(
    context: ParallelEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    complete = parallel_applied_values(context)
    complete.update(values)
    return complete


def _draft_surfaces(
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> tuple[CamSurfaceReference, ...]:
    count = int(_number(values["selected_face_count"], "selected_face_count"))
    candidates = draft.surfaces if count == len(draft.surfaces) else _surfaces(context)
    if count != len(candidates):
        raise ValueError("parallel.missing_face: Face selection is stale; select faces again.")
    if not candidates:
        raise ValueError("parallel.no_geometry: Select at least one machining face.")
    return candidates


def _geometry_inputs(
    existing: tuple[OperationGeometryInput, ...],
    surfaces: tuple[CamSurfaceReference, ...],
    draft: ParallelEditorDraftContext,
) -> tuple[OperationGeometryInput, ...]:
    previous = {item.reference.reference_id: item for item in existing}
    pending = draft.pending_input_ids or {}
    result: list[OperationGeometryInput] = []
    for order, surface in enumerate(surfaces):
        old = previous.get(surface.geometry.reference_id)
        key = str(surface.geometry.reference_id)
        input_id = old.input_id if old is not None else pending.get(key)
        if input_id is None:
            input_id = GeometryInputId.new()
            pending[key] = input_id
        result.append(
            OperationGeometryInput(
                input_id,
                GeometryInputRole.DRIVE_GEOMETRY,
                surface.geometry,
                True,
                GeometryReferenceKind.FACE,
                order,
            )
        )
    draft.pending_input_ids = pending
    return tuple(result)


def prepare_parallel_update(
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> ParallelOperationUpdate:
    """Build an applied operation/zone pair without mutating project state."""
    complete = _complete_values(context, values)
    surfaces = _draft_surfaces(context, draft, complete)
    revisions = {item.geometry.expected_source_revision for item in surfaces}
    if len(revisions) != 1:
        raise ValueError("parallel.missing_face: Selected faces use different revisions.")
    assembly_id = _text(complete["tool_assembly_id"], "tool_assembly_id")
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    if assembly is None:
        raise ValueError("parallel.invalid_tool: Tool Assembly is missing.")
    tool = next(
        (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
        None,
    )
    if tool is None:
        raise ValueError("parallel.invalid_tool: Tool Definition is missing.")
    if tool.family is not ToolFamily.BALL_END_MILL or not isinstance(
        tool.cutting_geometry, BallEndGeometry
    ):
        raise ValueError(
            "parallel.unsupported_tool_geometry: UNSUPPORTED_TOOL_GEOMETRY — select a ball-end tool."
        )
    holder = next(
        (
            item
            for item in context.holder_definitions
            if assembly.holder_id is not None and item.holder_id == assembly.holder_id
        ),
        None,
    )
    if assembly.holder_id is not None and not _holder_reference_is_current(
        assembly, holder
    ):
        raise ValueError(
            "parallel.safety.missing_holder_geometry: Holder verification unavailable or stale."
        )
    machine_id = _text(complete["machine_id"], "machine_id")
    machine = next(
        (item for item in context.machine_definitions if str(item.machine_id) == machine_id),
        None,
    )
    if machine is None or OperationCapability.MILLING not in machine.capabilities.operations:
        raise ValueError("parallel.invalid_workplane: A compatible milling machine is required.")
    automatic = _resolve_automatic_contract(context, draft, complete)
    invalid_manual = next(
        (
            item
            for item in automatic.values
            if item.mode is AutomaticParameterMode.MANUAL
            and not item.validation.valid
        ),
        None,
    )
    if invalid_manual is not None:
        raise ValueError(
            f"parallel.invalid_manual_override: {invalid_manual.validation.message}"
        )
    zone_id = _parameters(context).zone_id
    parameters = ParallelFinishingParameters(
        zone_id,
        _number(automatic.value("stepover_mm").effective_value, "stepover_mm"),
        _number(
            automatic.value("direction_angle_degrees").effective_value,
            "direction_angle_degrees",
        ),
        ParallelCutDirection(
            _text(automatic.value("cut_direction").effective_value, "cut_direction")
        ),
        ParallelLinkingMode(
            _text(automatic.value("linking_mode").effective_value, "linking_mode")
        ),
        _number(complete["feed_rate_mm_per_minute"], "feed_rate_mm_per_minute"),
        _number(complete["maximum_segment_length_mm"], "maximum_segment_length_mm"),
        _number(complete["clearance_z_mm"], "clearance_z_mm"),
        _number(complete["retract_z_mm"], "retract_z_mm"),
        _number(complete["link_clearance_mm"], "link_clearance_mm"),
    )
    tolerance_mm = _number(
        automatic.value("tolerance_mm").effective_value, "tolerance_mm"
    )
    allowance_mm = _number(
        automatic.value("surface_allowance_mm").effective_value,
        "surface_allowance_mm",
    )
    if tolerance_mm <= 0.0:
        raise ValueError("parallel.invalid_tolerance: Tolerance must be greater than zero.")
    if allowance_mm < 0.0:
        raise ValueError("parallel.unsupported_allowance: Surface allowance cannot be negative.")
    current_tolerance = (
        context.zone.tolerance
        if context.zone is not None
        else Cam3DTolerancePolicy(0.01, 0.2, 1.0e-8, 0.001, 0.001)
    )
    tolerance = replace(current_tolerance, chordal_tolerance=tolerance_mm)
    selection = CamSurfaceSelection(
        (
            context.zone.part_surfaces.selection.selection_id
            if context.zone is not None
            else CamSurfaceSelectionId.new()
        ),
        context.project_id,
        next(iter(revisions)),
        surfaces,
    )
    setup = context.setup
    zone = MachiningZone3D(
        zone_id,
        context.project_id,
        context.job_id,
        context.operation.setup_id,
        getattr(setup, "revision"),
        getattr(setup, "wcs"),
        PartSurfaceSet(selection),
        context.zone.check_surfaces if context.zone is not None else None,
        context.zone.fixture_surfaces if context.zone is not None else None,
        context.zone.boundary if context.zone is not None else None,
        getattr(setup, "wcs").z_axis,
        getattr(setup, "wcs").x_axis,
        context.zone.minimum_height if context.zone is not None else None,
        context.zone.maximum_height if context.zone is not None else None,
        tolerance,
        Cam3DStockAllowance(part_normal=allowance_mm),
        next(iter(revisions)),
        GeometryFingerprint.from_payload(
            {"parallel_faces": [item.identity_payload() for item in surfaces]}
        ),
    )
    safe_motion = Cam3DSafeMotionPolicy(
        context.operation.setup_id,
        getattr(setup, "revision"),
        wcs_fingerprint(getattr(setup, "wcs")),
        parameters.clearance_z_mm,
        parameters.retract_z_mm,
        2.0,
        parameters.link_clearance_mm,
        Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID,
        getattr(setup, "wcs").z_axis,
    )
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    geometry_inputs = _geometry_inputs(context.operation.geometry_inputs, surfaces, draft)
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(complete["enabled"], "enabled")
    base_parameters = parameters.to_operation_parameters()
    operation_parameters = OperationParameterSet(
        base_parameters.strategy_key,
        base_parameters.strategy_version,
        base_parameters.values
        + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, automatic.to_json()),),
        base_parameters.schema_version,
    )
    changed = context.operation
    differences = (
        operation_parameters != context.operation.parameters,
        geometry_inputs != context.operation.geometry_inputs,
        tool_reference != context.operation.tool_assembly,
        requirement != context.operation.machine_requirement,
        enabled != context.operation.enabled,
        context.zone != zone,
    )
    if any(differences):
        reason = (
            DirtyReason.GEOMETRY_CHANGED
            if differences[1] or differences[5]
            else DirtyReason.TOOL_CHANGED
            if differences[2]
            else DirtyReason.MACHINE_CHANGED
            if differences[3]
            else DirtyReason.PARAMETERS_CHANGED
            if differences[0]
            else DirtyReason.UPSTREAM_CHANGED
        )
        changed = replace(
            context.operation,
            geometry_inputs=geometry_inputs,
            parameters=operation_parameters,
            tool_assembly=tool_reference,
            machine_requirement=requirement,
            enabled=enabled,
            revision=context.operation.revision.next(),
            artifact_state=context.operation.artifact_state.mark_dirty(reason),
            diagnostics=(),
        )
    return ParallelOperationUpdate(
        _text(complete["operation_name"], "operation_name"),
        changed,
        parameters,
        zone,
        safe_motion,
        assembly,
        tool,
        holder,
        machine,
        automatic,
    )


def parallel_validation_diagnostics(
    schema: FunctionEditorSchema,
    context: ParallelEditorContext,
    draft: ParallelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> tuple[FunctionEditorDiagnostic, ...]:
    """Return structured errors/warnings while preserving every draft value."""
    diagnostics: list[FunctionEditorDiagnostic] = []
    try:
        update = prepare_parallel_update(context, draft, values)
        estimated = None
        if context.zone is not None:
            estimated = max(1, int(10_000.0 / update.parameters.stepover_mm))
        if estimated is not None and estimated > 20_000:
            raise ValueError("parallel.size_limit: Estimated pass count exceeds guardrail.")
    except (KeyError, TypeError, ValueError) as error:
        message = str(error) or "Parallel draft is invalid."
        field_id = _error_field(message)
        diagnostics.append(
            FunctionEditorDiagnostic(
                _diagnostic_code(message),
                message,
                FunctionEditorDiagnosticSeverity.ERROR,
                field_id,
                schema.section_for_field(field_id).section_id if field_id else None,
            )
        )
    assembly, _tool, holder = _assembly_resources(context)
    if assembly is not None and assembly.holder_id is None:
        diagnostics.append(
            FunctionEditorDiagnostic(
                "parallel.holder_absent",
                "Holder not declared; cutter/shank are checked and holder remains unverified.",
                FunctionEditorDiagnosticSeverity.INFO,
                "holder_state",
                "tool",
            )
        )
    elif (
        assembly is not None
        and assembly.holder_id is not None
        and not _holder_reference_is_current(assembly, holder)
    ):
        diagnostics.append(
            FunctionEditorDiagnostic(
                "parallel.holder_unavailable",
                "Holder verification unavailable; Calculate cannot publish READY.",
                FunctionEditorDiagnosticSeverity.ERROR,
                "holder_state",
                "tool",
            )
        )
    diagnostics.extend(
        (
            FunctionEditorDiagnostic(
                "parallel.machine_clearance_unverified",
                "Khoảng hở sẵn sàng cho máy chưa được xác minh.",
                FunctionEditorDiagnosticSeverity.INFO,
                "machine_ready_clearance",
                "capability_safety",
            ),
            FunctionEditorDiagnostic(
                "parallel.post_unsupported",
                "Production Post is not available for Parallel Finishing.",
                FunctionEditorDiagnosticSeverity.WARNING,
                "post_gate",
                "capability_safety",
            ),
        )
    )
    return tuple(diagnostics)


def _diagnostic_code(message: str) -> str:
    prefix = message.split(":", 1)[0].strip()
    if prefix.startswith("parallel."):
        return prefix.replace("_", "-")
    return "parallel.invalid_parameters"


def _error_field(message: str) -> str:
    folded = message.casefold()
    mapping = (
        (("face", "geometry"), "geometry_summary"),
        (("holder",), "holder_state"),
        (("tool", "dao"), "tool_assembly_id"),
        (("stepover", "pass count", "bước ngang"), "stepover_mm"),
        (("tolerance", "dung sai"), "tolerance_mm"),
        (("allowance", "lượng dư"), "surface_allowance_mm"),
        (("clearance z",), "clearance_z_mm"),
        (("retract z",), "retract_z_mm"),
        (("link clearance",), "link_clearance_mm"),
        (("direction", "hướng chạy dao"), "direction_angle_degrees"),
        (("machine", "workplane"), "machine_id"),
    )
    return next(
        (field for tokens, field in mapping if any(token in folded for token in tokens)),
        "operation_name",
    )


def _minimum(code: str, message: str, value: float = 1.0e-12) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        FunctionEditorValidationKind.MINIMUM, value, message, code
    )


def _cross(
    kind: FunctionEditorValidationKind,
    operand: str,
    code: str,
    message: str,
) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(kind, operand, message, code)


def _number_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
    validators: tuple[FunctionEditorValidationRule, ...] = (),
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    tooltip: str = "",
    applicable_when: FunctionEditorApplicability | None = None,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.NUMBER,
        value,
        "mm" if "degrees" not in field_id else "deg",
        FunctionEditorValueSource.USER,
        value,
        applicable_when=applicable_when,
        required=True,
        disclosure_level=level,
        validators=validators,
        tooltip=tooltip,
        order=order,
        binding_key=f"parallel.{field_id}",
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def _read_only(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    source: FunctionEditorValueSource = FunctionEditorValueSource.DERIVED,
    action_id: str = "",
    action_label: str = "",
    tooltip: str = "",
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.READ_ONLY,
        value,
        source=source,
        disclosure_level=level,
        tooltip=tooltip,
        order=order,
        binding_key=f"parallel.{field_id}",
        action_id=action_id,
        action_label=action_label,
    )


def _choice(
    field_id: str,
    label: str,
    value: PresentationValue,
    choices: tuple[PresentationValue, ...],
    labels: tuple[tuple[PresentationValue, str], ...],
    *,
    order: int,
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    applicable_when: FunctionEditorApplicability | None = None,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHOICE,
        value,
        choices=choices,  # type: ignore[arg-type]
        choice_labels=labels,  # type: ignore[arg-type]
        applicable_when=applicable_when,
        required=True,
        disclosure_level=level,
        order=order,
        binding_key=f"parallel.{field_id}",
        conversion=FunctionEditorValueConversion.TEXT,
    )


def _manual_checkbox(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHECKBOX,
        value,
        source=FunctionEditorValueSource.USER,
        default=False,
        default_label="Tự động",
        required=True,
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        tooltip=(
            "Tắt để HMS tự tính lại từ hình học, dao, Thiết lập và hồ sơ chất lượng; "
            "giá trị thủ công trước đó vẫn được giữ."
        ),
        order=order,
        binding_key=f"parallel.{field_id}",
        conversion=FunctionEditorValueConversion.BOOLEAN,
    )


def _manual_applicability(toggle_field_id: str) -> FunctionEditorApplicability:
    return FunctionEditorApplicability(
        toggle_field_id, ApplicabilityOperator.TRUTHY
    )


def _equals_applicability(
    field_id: str, value: PresentationValue
) -> FunctionEditorApplicability:
    return FunctionEditorApplicability(
        field_id, ApplicabilityOperator.EQUALS, value
    )


def build_parallel_schema(context: ParallelEditorContext) -> FunctionEditorSchema:
    """Build the compact production schema over the unchanged Parallel domain."""
    values = parallel_applied_values(context)
    safety = parallel_safety_presentation(context)
    assembly, _tool, _holder = _assembly_resources(context)
    ball_assemblies = []
    for item in context.tool_assemblies:
        tool = next((value for value in context.tool_definitions if value.tool_id == item.tool_id), None)
        if tool is not None and tool.family is ToolFamily.BALL_END_MILL and isinstance(
            tool.cutting_geometry, BallEndGeometry
        ):
            ball_assemblies.append(item)
    tool_choices = tuple(str(item.assembly_id) for item in ball_assemblies) or (
        str(context.operation.tool_assembly.assembly_id),
    )
    tool_labels = tuple((str(item.assembly_id), item.name) for item in ball_assemblies) or (
        (str(context.operation.tool_assembly.assembly_id), "Missing/unsupported tool"),
    )
    milling_machines = tuple(
        item
        for item in context.machine_definitions
        if item.unit is LengthUnit.MM
        and OperationCapability.MILLING in item.capabilities.operations
    )
    machine_choices = tuple(str(item.machine_id) for item in milling_machines) or (
        str(values["machine_id"]),
    )
    machine_labels = tuple((str(item.machine_id), item.name) for item in milling_machines) or (
        (str(values["machine_id"]), "Missing machine"),
    )
    sections = (
        FunctionEditorSection(
            "operation",
            "OPERATION",
            (
                FunctionEditorField(
                    "operation_name", "Operation name", FunctionEditorFieldKind.TEXT,
                    values["operation_name"], required=True, order=10,
                    binding_key="node.name", conversion=FunctionEditorValueConversion.TEXT,
                ),
                _read_only("operation_type", "Operation type", values["operation_type"], order=20, level=ParameterDisclosureLevel.ADVANCED),
                FunctionEditorField(
                    "enabled", "Enabled", FunctionEditorFieldKind.CHECKBOX,
                    values["enabled"], required=True, order=30,
                    disclosure_level=ParameterDisclosureLevel.ADVANCED,
                    binding_key="operation.enabled", conversion=FunctionEditorValueConversion.BOOLEAN,
                ),
            ),
            "Parallel Finishing operation identity.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=5,
        ),
        FunctionEditorSection(
            "geometry",
            "GEOMETRY",
            (
                _read_only("geometry_summary", "Machining Faces", values["geometry_summary"], order=10, source=FunctionEditorValueSource.GEOMETRY, action_id="select_parallel_faces", action_label="Select / Add"),
                _read_only("selected_face_count", "Selected faces", values["selected_face_count"], order=20, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.GEOMETRY),
                _read_only("selected_body_setup_summary", "Body / Setup", values["selected_body_setup_summary"], order=30, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.SETUP),
                _read_only("reselect_geometry", "Reselect", values["reselect_geometry"], order=40, level=ParameterDisclosureLevel.ADVANCED, action_id="reselect_parallel_faces", action_label="Reselect"),
                _read_only("remove_geometry", "Remove", values["remove_geometry"], order=50, level=ParameterDisclosureLevel.ADVANCED, action_id="remove_parallel_faces", action_label="Remove"),
                _read_only("clear_geometry", "Selection", values["clear_geometry"], order=60, level=ParameterDisclosureLevel.ADVANCED, action_id="clear_parallel_faces", action_label="Clear"),
                _read_only("geometry_reference_summary", "Persistent face IDs", values["geometry_reference_summary"], order=70, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.GEOMETRY),
            ),
            f"{values['selected_face_count']} selected face(s).",
            order=10,
        ),
        FunctionEditorSection(
            "tool",
            "TOOL",
            (
                _choice("tool_assembly_id", "Ball-end tool", values["tool_assembly_id"], tool_choices, tool_labels, order=10),
                _read_only("tool_details", "Tool details", values["tool_details"], order=20, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.TOOL),
                _read_only("holder_state", "Holder State", values["holder_state"], order=30, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.TOOL),
                _read_only("holder_scope", "Holder scope", values["holder_scope"], order=40, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.TOOL),
            ),
            assembly.name if assembly is not None else "Ball-end tool required.",
            order=20,
        ),
        FunctionEditorSection(
            "quality",
            "CHẤT LƯỢNG",
            (
                _choice(
                    "quality_profile",
                    "Hồ sơ chất lượng",
                    values["quality_profile"],
                    (
                        CamQualityProfile.FAST.value,
                        CamQualityProfile.BALANCED.value,
                        CamQualityProfile.HIGH.value,
                    ),
                    (
                        (CamQualityProfile.FAST.value, "Nhanh"),
                        (CamQualityProfile.BALANCED.value, "Cân bằng"),
                        (CamQualityProfile.HIGH.value, "Chất lượng cao"),
                    ),
                    order=10,
                ),
            ),
            "Nhanh ưu tiên thời gian; Cân bằng là mặc định; Chất lượng cao siết bước ngang và dung sai.",
            order=25,
        ),
        FunctionEditorSection(
            "automatic_summary",
            "TÓM TẮT TÍNH TOÁN TỰ ĐỘNG",
            (
                _read_only("automatic_policy_summary", "Chính sách HMS", values["automatic_policy_summary"], order=10, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.DERIVED),
                _read_only("automatic_mode_counts", "Chế độ tham số", values["automatic_mode_counts"], order=15, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("automatic_direction_summary", "Hướng chạy dao", values["automatic_direction_summary"], order=20, source=FunctionEditorValueSource.GEOMETRY),
                _read_only("automatic_stepover_summary", "Bước ngang", values["automatic_stepover_summary"], order=30, source=FunctionEditorValueSource.DERIVED),
                _read_only("automatic_tolerance_summary", "Dung sai", values["automatic_tolerance_summary"], order=40, source=FunctionEditorValueSource.PROJECT),
                _read_only("automatic_allowance_summary", "Lượng dư", values["automatic_allowance_summary"], order=50, source=FunctionEditorValueSource.PROJECT),
                _read_only("automatic_ordering_summary", "Thứ tự cắt", values["automatic_ordering_summary"], order=60),
                _read_only("automatic_linking_summary", "Liên kết", values["automatic_linking_summary"], order=70),
                _read_only("automatic_holder_summary", "Holder và phạm vi", values["automatic_holder_summary"], order=80, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.TOOL),
                _read_only("effective_direction_angle_degrees", "Góc hiệu lực", values["effective_direction_angle_degrees"], order=90, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("automatic_effective_hash", "Mã tham số hiệu lực", values["automatic_effective_hash"], order=100, level=ParameterDisclosureLevel.ADVANCED),
            ),
            "Giá trị hiệu lực, nguồn và lý do được cập nhật ngay khi đổi hình học, dao hoặc chất lượng.",
            order=27,
        ),
        FunctionEditorSection(
            "direction",
            "DIRECTION",
            (
                _read_only("direction_mode", "Machining Direction", values["direction_mode"], order=10, level=ParameterDisclosureLevel.ADVANCED),
                _manual_checkbox("direction_override_enabled", "Tùy chỉnh hướng thủ công", values["direction_override_enabled"], order=20),
                _choice(
                    "direction_override_mode",
                    "Kiểu hướng thủ công",
                    values["direction_override_mode"],
                    ("auto", "axis_x", "axis_y", "custom_angle"),
                    (
                        ("auto", "Tự động"),
                        ("axis_x", "Theo trục X"),
                        ("axis_y", "Theo trục Y"),
                        ("custom_angle", "Góc tùy chỉnh"),
                    ),
                    order=30,
                    level=ParameterDisclosureLevel.ADVANCED,
                    applicable_when=_manual_applicability("direction_override_enabled"),
                ),
                _number_field("direction_angle_degrees", "Direction angle", values["direction_angle_degrees"], order=40, level=ParameterDisclosureLevel.ADVANCED, applicable_when=_equals_applicability("direction_override_mode", "custom_angle")),
                _read_only("direction_preview", "Direction preview", values["direction_preview"], order=50, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("workplane_summary", "Workplane / Setup", values["workplane_summary"], order=60, level=ParameterDisclosureLevel.ADVANCED, source=FunctionEditorValueSource.SETUP),
            ),
            "U pass direction · V stepover direction · W tool axis.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=30,
        ),
        FunctionEditorSection(
            "cut_parameters",
            "CUT PARAMETERS",
            (
                _manual_checkbox("stepover_override_enabled", "Tùy chỉnh bước ngang thủ công", values["stepover_override_enabled"], order=10),
                _number_field("stepover_mm", "Stepover", values["stepover_mm"], order=20, validators=(_minimum("parallel.stepover-positive", "Stepover must be > 0."),), level=ParameterDisclosureLevel.ADVANCED, applicable_when=_manual_applicability("stepover_override_enabled")),
                _manual_checkbox("tolerance_override_enabled", "Tùy chỉnh dung sai thủ công", values["tolerance_override_enabled"], order=30),
                _number_field("tolerance_mm", "Tolerance", values["tolerance_mm"], order=40, validators=(_minimum("parallel.tolerance-positive", "Tolerance must be > 0."),), level=ParameterDisclosureLevel.ADVANCED, tooltip="Surface/chordal tolerance; contact tolerance is shown separately.", applicable_when=_manual_applicability("tolerance_override_enabled")),
                _manual_checkbox("allowance_override_enabled", "Tùy chỉnh lượng dư thủ công", values["allowance_override_enabled"], order=50),
                _number_field("surface_allowance_mm", "Surface Allowance", values["surface_allowance_mm"], order=60, validators=(_minimum("parallel.allowance-nonnegative", "Surface allowance cannot be negative.", 0.0),), level=ParameterDisclosureLevel.ADVANCED, applicable_when=_manual_applicability("allowance_override_enabled")),
                _manual_checkbox("ordering_override_enabled", "Tùy chỉnh thứ tự cắt thủ công", values["ordering_override_enabled"], order=70),
                _choice("cut_direction", "Cut Ordering", values["cut_direction"], (ParallelCutDirection.ONE_WAY.value, ParallelCutDirection.ZIGZAG.value), ((ParallelCutDirection.ONE_WAY.value, "One-way"), (ParallelCutDirection.ZIGZAG.value, "Zigzag")), order=80, level=ParameterDisclosureLevel.ADVANCED, applicable_when=_manual_applicability("ordering_override_enabled")),
            ),
            "Foundation ball-center path parameters.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=40,
        ),
        FunctionEditorSection(
            "levels_linking",
            "LEVELS / LINKING",
            (
                _number_field("clearance_z_mm", "Clearance", values["clearance_z_mm"], order=10, validators=(_cross(FunctionEditorValidationKind.GREATER_THAN_FIELD, "retract_z_mm", "parallel.clearance-order", "Clearance must be above Retract."),)),
                _number_field("retract_z_mm", "Retract", values["retract_z_mm"], order=20),
                _number_field("link_clearance_mm", "Link clearance", values["link_clearance_mm"], order=30, validators=(_minimum("parallel.link-clearance-nonnegative", "Link clearance cannot be negative.", 0.0),)),
                _read_only("linking_mode", "Linking mode", values["linking_mode"], order=40, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("conservative_linking_summary", "Linking policy", values["conservative_linking_summary"], order=50),
            ),
            "Conservative retract-only linking.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=50,
        ),
        FunctionEditorSection(
            "advanced",
            "ADVANCED",
            (
                _number_field("feed_rate_mm_per_minute", "Feed rate", values["feed_rate_mm_per_minute"], order=10, validators=(_minimum("parallel.feed-positive", "Feed rate must be > 0."),), level=ParameterDisclosureLevel.ADVANCED),
                _number_field("maximum_segment_length_mm", "Maximum segment length", values["maximum_segment_length_mm"], order=20, validators=(_minimum("parallel.segment-positive", "Maximum segment length must be > 0."),), level=ParameterDisclosureLevel.ADVANCED),
                _read_only("contact_tolerance_mm", "Contact tolerance", values["contact_tolerance_mm"], order=30, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("internal_detection_threshold", "Internal detection threshold", values["internal_detection_threshold"], order=40, level=ParameterDisclosureLevel.ADVANCED, tooltip="Internal numerical/safety threshold; not a safe machining clearance."),
                _read_only("guardrail_summary", "Validation limits", values["guardrail_summary"], order=50, level=ParameterDisclosureLevel.ADVANCED),
            ),
            "Algorithm-used values and read-only safety limits.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=60,
        ),
        FunctionEditorSection(
            "capability_safety",
            "CAPABILITY AND SAFETY",
            (
                _choice("machine_id", "Machine / Setup", values["machine_id"], machine_choices, machine_labels, order=10, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("capability_summary", "Supported", values["capability_summary"], order=20),
                _read_only("unsupported_summary", "Not supported / verified", values["unsupported_summary"], order=30),
                _read_only("calculation_status", "Calculation Status", values["calculation_status"], order=40),
                _read_only("safety_status", "Safety Status", values["safety_status"], order=50),
                _read_only("safety_algorithm_version", "Safety Algorithm", values["safety_algorithm_version"], order=60),
                _read_only("safety_scope", "Safety Scope", values["safety_scope"], order=70),
                _read_only("checked_components", "Checked Components", values["checked_components"], order=80),
                _read_only("unverified_components", "Unverified Components", values["unverified_components"], order=90),
                _read_only("machine_ready_clearance", "Machine-ready Clearance", values["machine_ready_clearance"], order=100),
                _read_only("safety_finding_counts", "Safety counts", values["safety_finding_counts"], order=110),
                _read_only("safety_report_hash", "Safety report", values["safety_report_hash"], order=120, level=ParameterDisclosureLevel.ADVANCED),
                _read_only("diagnostic_summary", "Safety diagnostics", values["diagnostic_summary"], order=130, action_id="open_parallel_safety_details", action_label="Open Details"),
                _read_only(
                    "simulation_gate",
                    "Simulation",
                    values["simulation_gate"],
                    order=140,
                    action_id=(
                        "open_parallel_simulation"
                        if safety.simulation_gate.startswith("Available")
                        else ""
                    ),
                    action_label=(
                        "Open"
                        if safety.simulation_gate.startswith("Available")
                        else ""
                    ),
                ),
                _read_only("post_gate", "Post", values["post_gate"], order=150),
            ),
            f"{values['safety_status']} · khoảng hở sẵn sàng cho máy chưa được xác minh.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=70,
        ),
        FunctionEditorSection(
            "summary",
            "SUMMARY",
            (_read_only("summary", "Operation summary", values["summary"], order=10),),
            "Applied/draft summary without a production-safety claim.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=80,
        ),
    )
    schema = FunctionEditorSchema(
        PARALLEL_EDITOR_ID,
        FunctionEditorStrategyKey(PARALLEL_EDITOR_STRATEGY_KEY),
        FunctionEditorSummary(
            context.operation_name,
            "Parallel Finishing · algorithm v3 · payload v1",
            assembly.name if assembly is not None else "Missing tool",
            f"{values['selected_face_count']} face(s)",
            f"{'ENABLED' if context.operation.enabled else 'DISABLED'} · "
            f"{context.operation.artifact_state.status.value.upper()} · {safety.status}",
        ),
        sections,
        FunctionEditorFooter(
            (
                FunctionEditorAction.RESET_DRAFT,
                FunctionEditorAction.PREVIEW,
                FunctionEditorAction.VALIDATE,
                FunctionEditorAction.APPLY,
                FunctionEditorAction.SAVE_TOOL_PROFILE,
                FunctionEditorAction.CALCULATE,
                FunctionEditorAction.CLOSE,
            ),
            preview_supported=True,
            calculate_supported=True,
        ),
    )
    validate_parallel_schema_contract(schema)
    return schema


def validate_parallel_schema_contract(schema: FunctionEditorSchema) -> None:
    """Fail closed on missing bindings, empty Expert, or invented options."""
    if any(not field.binding_key for field in schema.fields):
        raise ValueError("Parallel editor fields must declare explicit bindings")
    if any(
        section.disclosure_level is ParameterDisclosureLevel.EXPERT
        for section in schema.sections
    ):
        raise ValueError("Parallel v1 has no production Expert section")
    linking = schema.field("linking_mode")
    if (
        linking.kind is not FunctionEditorFieldKind.READ_ONLY
        or linking.value != "Rút dao giữa các đoạn"
    ):
        raise ValueError("Parallel editor exposed an unsupported linking mode")
    if PARALLEL_FINISHING_ALGORITHM_VERSION != 3 or PARALLEL_FINISHING_STRATEGY_VERSION != 1:
        raise ValueError("Parallel editor version contract is incompatible")


__all__ = [
    "PARALLEL_EDITOR_ID",
    "PARALLEL_EDITOR_STRATEGY_KEY",
    "ParallelEditorContext",
    "ParallelEditorDraftContext",
    "ParallelOperationUpdate",
    "ParallelSafetyPresentation",
    "build_parallel_schema",
    "parallel_applied_values",
    "parallel_draft_derived_values",
    "parallel_safety_presentation",
    "parallel_validation_diagnostics",
    "prepare_parallel_update",
    "validate_parallel_schema_contract",
]
