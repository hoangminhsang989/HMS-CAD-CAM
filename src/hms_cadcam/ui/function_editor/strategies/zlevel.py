"""Production Function Editor binding for Z-Level Finishing Stage 8A.3.3."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
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
from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
    ZLevelAutomaticContext,
    ZLevelBoundaryPolicy,
    ZLevelFinishingParameters,
    ZLevelGeometryEvidence,
    ZLevelLinkingMode,
    ZLevelMachiningFrame,
    ZLevelOrientation,
    ZLevelSafetyReport,
    ZLevelSafetyStatus,
    resolve_z_level_automatic_contract,
    z_level_artifact_has_safe_contract,
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
    Operation,
    OperationCapability,
    OperationGeometryInput,
    OperationParameterSet,
    Setup,
    ToolAssembly,
    ToolAssemblyReference,
    ToolDefinition,
    ToolFamily,
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
from hms_cadcam.ui.localization import display_value, translate_status, ui_text


Z_LEVEL_EDITOR_ID = "z_level_finishing_production_8a3_3"
Z_LEVEL_EDITOR_STRATEGY_KEY = "z_level_finishing_3d_8a3_3"
Z_LEVEL_POST_GATE_REASON = (
    "Post sản xuất cho gia công tinh theo cao độ Z chưa được hỗ trợ"
)
Z_LEVEL_POST_FAIL_CLOSED_FOOTER = "Post sản xuất · bị chặn an toàn"
_DEFAULT_TOLERANCE_MM = 0.01


@dataclass(frozen=True, slots=True)
class ZLevelEditorContext:
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
    safety_report: ZLevelSafetyReport | None = None
    geometry_resolved: bool = True
    geometry_diagnostic: str = ""
    geometry_evidence: ZLevelGeometryEvidence | None = None

    def __post_init__(self) -> None:
        if self.operation.strategy_key != "z_level_finishing_3d":
            raise ValueError("Z-Level editor requires a Z-Level operation")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("Z-Level editor project identity is invalid")
        if self.operation.setup_id != self.setup.setup_id:
            raise ValueError("Z-Level operation belongs to another Setup")
        if not isinstance(self.job_id, CamJobId):
            raise TypeError("Z-Level job identity is invalid")
        if self.zone is not None and (
            self.zone.project_id != self.project_id
            or self.zone.setup_id != self.operation.setup_id
        ):
            raise ValueError("Z-Level machining zone belongs to another project/Setup")


@dataclass(slots=True)
class ZLevelEditorDraftContext:
    """Transient face selection with no Qt or OCP handles."""

    surfaces: tuple[CamSurfaceReference, ...]
    pending_input_ids: dict[str, GeometryInputId] | None = None
    geometry_evidence: ZLevelGeometryEvidence | None = None


@dataclass(frozen=True, slots=True)
class ZLevelOperationUpdate:
    """Validated atomic candidate for operation plus CAM 3D zone persistence."""

    operation_name: str
    operation: Operation
    parameters: ZLevelFinishingParameters
    zone: MachiningZone3D
    safe_motion_policy: Cam3DSafeMotionPolicy
    assembly: ToolAssembly
    tool: ToolDefinition
    holder: HolderDefinition | None
    machine: MachineDefinition
    automatic_contract: AutomaticParameterContract


@dataclass(frozen=True, slots=True)
class ZLevelSafetyPresentation:
    """Compact non-JSON safety state for the editor and Operation Manager."""

    status: str
    artifact_state: str
    report_hash: str
    checked_components: str
    unverified_components: str
    holder_state: str
    safety_scope: str
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
        raise ValueError(f"{field_id} phải là một số hữu hạn.")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_id} phải là một số hữu hạn.") from error
    if not math.isfinite(result):
        raise ValueError(f"{field_id} phải là một số hữu hạn.")
    return result


def _boolean(value: object, field_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_id} phải là giá trị đúng/sai.")
    return value


def _parameters(context: ZLevelEditorContext) -> ZLevelFinishingParameters:
    return ZLevelFinishingParameters.from_operation_parameters(
        context.operation.parameters
    )


def _stored_automatic_contract(
    context: ZLevelEditorContext,
) -> AutomaticParameterContract | None:
    raw = dict(context.operation.parameters.values).get(
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    )
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            "z_level.invalid_automatic_contract: Metadata tự động không hợp lệ."
        )
    try:
        return AutomaticParameterContract.from_json(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "z_level.invalid_automatic_contract: Metadata tự động bị hỏng."
        ) from error


def _surfaces(context: ZLevelEditorContext) -> tuple[CamSurfaceReference, ...]:
    if context.zone is None:
        return ()
    return context.zone.part_surfaces.selection.surfaces


def _assembly_resources(
    context: ZLevelEditorContext,
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
            (
                item
                for item in context.tool_definitions
                if item.tool_id == assembly.tool_id
            ),
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
    return assembly, tool, holder


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


def _tool_text(
    assembly: ToolAssembly | None,
    tool: ToolDefinition | None,
) -> str:
    if assembly is None:
        return "Thiếu Cụm Tool"
    if tool is None:
        return f"{assembly.name} · thiếu định nghĩa Tool"
    diameter = getattr(tool.cutting_geometry, "diameter", None)
    radius = diameter.value / 2.0 if diameter is not None else 0.0
    return (
        f"{tool.name} · D{diameter.value:g} mm · R{radius:g} mm · "
        f"chiều dài hữu dụng {tool.usable_length.value:g} mm"
        if diameter is not None
        else f"{tool.name} · hình học không được hỗ trợ"
    )


def _holder_text(
    assembly: ToolAssembly | None,
    holder: HolderDefinition | None,
) -> tuple[str, str, str]:
    if assembly is None:
        return "Thiếu Cụm Tool", "Không có", "Dao cắt, cán dao, Holder"
    if assembly.holder_id is None:
        return (
            "Chưa khai báo Holder · an toàn phải chặn",
            "Dao cắt, cán dao",
            "Holder",
        )
    if not _holder_reference_is_current(assembly, holder):
        return "Holder thiếu hoặc lỗi thời", "Không có", "Dao cắt, cán dao, Holder"
    return "Holder đã xác minh", "Dao cắt, cán dao, Holder", "Không có"


def _boundary_geometry_evidence(
    context: ZLevelEditorContext,
) -> ZLevelGeometryEvidence | None:
    if context.geometry_evidence is not None:
        return context.geometry_evidence
    zone = context.zone
    if zone is None:
        return None
    values = [
        point
        for boundary in (zone.boundary,)
        if boundary is not None
        for point in boundary.points
    ]
    if not values:
        top = _parameters(context).top_level
        bottom = _parameters(context).bottom_level
        return ZLevelGeometryEvidence(
            0.0,
            0.0,
            0.0,
            0.0,
            float(bottom),
            float(top),
            "Phạm vi cao độ đã lưu",
        )
    frame = context.setup.wcs
    projected = []
    for point in values:
        dx = point.x - frame.origin.x
        dy = point.y - frame.origin.y
        dz = point.z - frame.origin.z
        projected.append(
            (
                dx * frame.x_axis.x + dy * frame.x_axis.y + dz * frame.x_axis.z,
                dx * frame.y_axis.x + dy * frame.y_axis.y + dz * frame.y_axis.z,
                dx * frame.z_axis.x + dy * frame.z_axis.y + dz * frame.z_axis.z,
            )
        )
    return ZLevelGeometryEvidence(
        float(min(item[0] for item in projected)),
        float(max(item[0] for item in projected)),
        float(min(item[1] for item in projected)),
        float(max(item[1] for item in projected)),
        float(min(item[2] for item in projected)),
        float(max(item[2] for item in projected)),
        "Biên vùng gia công trong hệ tọa độ Thiết lập",
    )


def _automatic_context(
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext | None,
    values: Mapping[str, PresentationValue],
) -> ZLevelAutomaticContext:
    surfaces = draft.surfaces if draft is not None else _surfaces(context)
    identity = [item.identity_payload() for item in surfaces]
    assembly_id = str(
        values.get("tool_assembly_id", context.operation.tool_assembly.assembly_id)
    )
    _assembly, tool, holder = _assembly_resources(context, assembly_id)
    supported = bool(
        tool is not None
        and tool.family is ToolFamily.BALL_END_MILL
        and isinstance(tool.cutting_geometry, BallEndGeometry)
        and tool.unit is LengthUnit.MM
    )
    diameter = (
        tool.cutting_geometry.diameter.to(LengthUnit.MM).value
        if supported and tool is not None
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
    protected_count = 0
    if context.zone is not None:
        for surface_set in (
            context.zone.check_surfaces,
            context.zone.fixture_surfaces,
        ):
            if surface_set is not None:
                protected_count += len(surface_set.selection.surfaces)
    return ZLevelAutomaticContext(
        GeometryFingerprint.from_payload({"z_level_faces": identity}),
        DependencyFingerprint.from_payload({"z_level_faces": identity}),
        wcs_fingerprint(context.setup.wcs),
        (
            tool.content_fingerprint
            if tool is not None
            else ContentFingerprint.from_payload({"missing_z_level_tool": assembly_id})
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
        supported,
        protected_count,
    )


def _quality_profile(
    values: Mapping[str, PresentationValue],
    stored: AutomaticParameterContract | None,
) -> CamQualityProfile:
    raw = values.get(
        "quality_profile",
        (
            stored.quality_profile.value
            if stored is not None
            else CamQualityProfile.BALANCED.value
        ),
    )
    try:
        return CamQualityProfile(str(raw))
    except ValueError as error:
        raise ValueError(
            "z_level.invalid_quality: Hồ sơ chất lượng không hợp lệ."
        ) from error


def _resolve_automatic_contract(
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext | None,
    values: Mapping[str, PresentationValue],
) -> AutomaticParameterContract:
    stored = _stored_automatic_contract(context)
    parameters = _parameters(context)
    flags = {
        key: bool(values.get(toggle, False))
        for key, toggle in (
            ("top_level", "top_override_enabled"),
            ("bottom_level", "bottom_override_enabled"),
            ("stepdown_mm", "stepdown_override_enabled"),
            ("tolerance_mm", "tolerance_override_enabled"),
            ("surface_allowance_mm", "allowance_override_enabled"),
            ("orientation", "orientation_override_enabled"),
            ("boundary_policy", "boundary_override_enabled"),
            ("contour_ordering", "ordering_override_enabled"),
            ("linking_mode", "linking_override_enabled"),
            ("approach_retract_policy", "approach_override_enabled"),
            ("protected_geometry_scope", "protected_geometry_override_enabled"),
            ("safety_scope", "safety_scope_override_enabled"),
        )
    }
    overrides = {
        "top_level": values.get("top_level", parameters.top_level),
        "bottom_level": values.get("bottom_level", parameters.bottom_level),
        "stepdown_mm": values.get("stepdown_mm", parameters.stepdown_mm),
        "tolerance_mm": values.get("tolerance_mm", parameters.tolerance_mm),
        "surface_allowance_mm": values.get(
            "surface_allowance_mm", parameters.surface_allowance_mm
        ),
        "orientation": values.get("orientation", parameters.orientation.value),
        "boundary_policy": values.get(
            "boundary_policy", parameters.boundary_policy.value
        ),
        "contour_ordering": values.get(
            "contour_ordering", "top_down_nearest_safe"
        ),
        "linking_mode": values.get("linking_mode", parameters.linking_mode.value),
        "approach_retract_policy": values.get(
            "approach_retract_policy", "retract_then_rapid"
        ),
        "protected_geometry_scope": values.get(
            "protected_geometry_scope", "part_boundary_only"
        ),
        "safety_scope": values.get(
            "safety_scope", "declared_geometry_and_tool_assembly"
        ),
    }
    contract = resolve_z_level_automatic_contract(
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
        "z_level_finishing_3d",
        operation_override_keys=frozenset(
            key for key, enabled in flags.items() if enabled
        ),
        operation_id=str(context.operation.operation_id),
        holder_fingerprint=(
            holder.content_fingerprint if holder is not None else None
        ),
    )


def _override_value(
    stored: AutomaticParameterContract | None,
    key: str,
    fallback: object,
) -> object:
    if stored is None:
        return fallback
    try:
        value = stored.value(key).override_value
    except KeyError:
        return fallback
    return fallback if value is None else value


def _override_enabled(
    stored: AutomaticParameterContract | None,
    key: str,
) -> bool:
    if stored is None:
        return False
    try:
        return stored.value(key).mode is AutomaticParameterMode.MANUAL
    except KeyError:
        return False


def _automatic_summary(
    contract: AutomaticParameterContract,
    key: str,
    *,
    suffix: str = "",
) -> str:
    item = contract.value(key)
    mode = display_value(item.mode, "automatic_mode")
    status = display_value(item.status, "automatic_status")
    value = item.effective_value
    category = {
        "machining_frame": "z_level_machining_frame",
        "orientation": "z_level_orientation",
        "boundary_policy": "z_level_boundary_policy",
        "contour_ordering": "z_level_contour_ordering",
        "linking_mode": "z_level_linking_mode",
        "safety_scope": "z_level_safety_scope",
        "protected_geometry_scope": "z_level_protected_geometry_scope",
        "approach_retract_policy": "z_level_approach_retract_policy",
        "safety_sampling_policy": "z_level_safety_sampling_policy",
    }.get(key)
    if category is not None:
        rendered = display_value(value, category) + suffix
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        rendered = f"{float(value):g}{suffix}"
    else:
        rendered = f"{ui_text(str(value))}{suffix}"
    source = ui_text(item.source)
    reason = ui_text(item.reason)
    if isinstance(value, str) and value.endswith(" protected surface(s)"):
        source = source.replace("protected surface(s)", "bề mặt bảo vệ")
        rendered = value.removesuffix(" protected surface(s)") + " bề mặt bảo vệ"
    headline = [rendered]
    if mode != rendered:
        headline.append(mode)
    headline.append(status)
    return f"{' · '.join(headline)} · Nguồn: {source} · {reason}"


def _automatic_mode_counts(contract: AutomaticParameterContract) -> str:
    manual = sum(
        item.mode is AutomaticParameterMode.MANUAL for item in contract.values
    )
    return (
        f"{len(contract.values) - manual} tham số Tự động · "
        f"{manual} tham số Thủ công"
    )


def _safety_marker(artifact: ToolpathArtifact | None) -> dict[str, str]:
    if artifact is None:
        return {}
    markers = tuple(
        event
        for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "z_level.safety.contract"
    )
    return dict(markers[0].metadata) if len(markers) == 1 else {}


def z_level_safety_presentation(
    context: ZLevelEditorContext,
) -> ZLevelSafetyPresentation:
    """Resolve report/artifact evidence without claiming machine readiness."""
    report = context.safety_report
    marker = _safety_marker(context.artifact)
    status = "Chưa tính"
    if report is not None:
        status = {
            ZLevelSafetyStatus.SAFE: "SAFE · đã kiểm tra trong phạm vi khai báo",
            ZLevelSafetyStatus.UNSAFE: "UNSAFE",
            ZLevelSafetyStatus.UNKNOWN: "UNKNOWN",
            ZLevelSafetyStatus.CANCELLED: "Đã hủy",
            ZLevelSafetyStatus.FAILED: "Thất bại",
        }[report.status]
    elif context.operation.artifact_state.status is ArtifactStatus.COMPUTING:
        status = "Đang tính ứng viên"
    elif context.operation.artifact_state.status is ArtifactStatus.DIRTY:
        status = "STALE"
    elif context.operation.artifact_state.status is ArtifactStatus.FAILED:
        evidence = {
            key: value
            for item in context.operation.artifact_state.diagnostics
            for key, value in item.context
        }
        status = translate_status(evidence.get("safety_status", "FAILED"))
    elif (
        context.operation.artifact_state.status is ArtifactStatus.VALID
        and context.artifact is not None
        and z_level_artifact_has_safe_contract(context.artifact)
    ):
        status = "SAFE · đã kiểm tra trong phạm vi khai báo"
    assembly, _tool, holder = _assembly_resources(context)
    holder_state, checked, unverified = _holder_text(assembly, holder)
    if report is not None:
        checked = ", ".join(item.value for item in report.checked_components)
        unverified = (
            ", ".join(item.value for item in report.unverified_components)
            or "Không có"
        )
        holder_state = {
            "geometry_faithful": "Holder đã xác minh",
            "declared_absent": "Chưa khai báo Holder",
            "missing": "Thiếu Holder",
            "reference_invalid": "Holder stale",
        }.get(report.holder_state, report.holder_state)
    report_hash = (
        report.fingerprint.digest
        if report is not None
        else marker.get("safety_report_fingerprint", "")
    )
    diagnostics = report.diagnostics if report is not None else ()
    first = diagnostics[0] if diagnostics else None
    finding_counts = (
        "Chưa tính"
        if report is None
        else f"{len(diagnostics)} chẩn đoán · {report.statistics.collision_occurrences} va chạm"
    )
    diagnostic_summary = (
        "Không có phát hiện an toàn"
        if first is None
        else f"{first.code.value} · {ui_text(first.message)}"
    )
    safe_gate = bool(
        context.artifact is not None
        and context.operation.artifact_state.status is ArtifactStatus.VALID
        and context.artifact.operation_revision == context.operation.revision
        and z_level_artifact_has_safe_contract(context.artifact)
    )
    return ZLevelSafetyPresentation(
        status,
        translate_status(context.operation.artifact_state.status.value.upper()),
        report_hash,
        checked,
        unverified,
        holder_state,
        (
            "Hình học và Cụm Tool đã khai báo"
            if report is None
            else f"{len(report.safety_scope)} mục phạm vi · {ui_text(report.linking_decision)}"
        ),
        finding_counts,
        diagnostic_summary,
        (
            "Có thể mở · SẴN SÀNG + AN TOÀN · Thuật toán v2"
            if safe_gate
            else "Bị chặn · cần kết quả SẴN SÀNG + AN TOÀN của Thuật toán v2 hiện hành"
        ),
        f"Bị chặn · {Z_LEVEL_POST_GATE_REASON}",
    )


def z_level_applied_values(
    context: ZLevelEditorContext,
) -> dict[str, PresentationValue]:
    """Convert applied domain state into deterministic UI primitives."""
    parameters = _parameters(context)
    stored = _stored_automatic_contract(context)
    surfaces = _surfaces(context)
    assembly, tool, holder = _assembly_resources(context)
    holder_state, checked, unverified = _holder_text(assembly, holder)
    safety = z_level_safety_presentation(context)
    seed: dict[str, PresentationValue] = {
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "quality_profile": (
            stored.quality_profile.value
            if stored is not None
            else CamQualityProfile.BALANCED.value
        ),
        "top_override_enabled": _override_enabled(stored, "top_level"),
        "top_level": str(
            _override_value(stored, "top_level", parameters.top_level)
        ),
        "bottom_override_enabled": _override_enabled(stored, "bottom_level"),
        "bottom_level": str(
            _override_value(stored, "bottom_level", parameters.bottom_level)
        ),
        "stepdown_override_enabled": _override_enabled(stored, "stepdown_mm"),
        "stepdown_mm": str(
            _override_value(stored, "stepdown_mm", parameters.stepdown_mm)
        ),
        "tolerance_override_enabled": _override_enabled(stored, "tolerance_mm"),
        "tolerance_mm": str(
            _override_value(stored, "tolerance_mm", parameters.tolerance_mm)
        ),
        "allowance_override_enabled": _override_enabled(
            stored, "surface_allowance_mm"
        ),
        "surface_allowance_mm": str(
            _override_value(
                stored,
                "surface_allowance_mm",
                parameters.surface_allowance_mm,
            )
        ),
        "orientation_override_enabled": _override_enabled(stored, "orientation"),
        "orientation": str(
            _override_value(stored, "orientation", parameters.orientation.value)
        ),
        "boundary_override_enabled": _override_enabled(stored, "boundary_policy"),
        "boundary_policy": str(
            _override_value(
                stored, "boundary_policy", parameters.boundary_policy.value
            )
        ),
        "ordering_override_enabled": _override_enabled(
            stored, "contour_ordering"
        ),
        "contour_ordering": str(
            _override_value(
                stored, "contour_ordering", "top_down_nearest_safe"
            )
        ),
        "linking_override_enabled": _override_enabled(stored, "linking_mode"),
        "linking_mode": str(
            _override_value(stored, "linking_mode", parameters.linking_mode.value)
        ),
        "approach_override_enabled": _override_enabled(
            stored, "approach_retract_policy"
        ),
        "approach_retract_policy": str(
            _override_value(
                stored, "approach_retract_policy", "retract_then_rapid"
            )
        ),
        "protected_geometry_override_enabled": _override_enabled(
            stored, "protected_geometry_scope"
        ),
        "protected_geometry_scope": str(
            _override_value(
                stored, "protected_geometry_scope", "part_boundary_only"
            )
        ),
        "safety_scope_override_enabled": _override_enabled(stored, "safety_scope"),
        "safety_scope": str(
            _override_value(
                stored,
                "safety_scope",
                "declared_geometry_and_tool_assembly",
            )
        ),
    }
    automatic = _resolve_automatic_contract(context, None, seed)
    if stored is None:
        for key in (
            "top_level",
            "bottom_level",
            "stepdown_mm",
            "tolerance_mm",
            "surface_allowance_mm",
            "orientation",
            "boundary_policy",
            "contour_ordering",
            "linking_mode",
            "approach_retract_policy",
            "protected_geometry_scope",
            "safety_scope",
        ):
            seed[key] = str(automatic.value(key).effective_value)
    top = _number(automatic.value("top_level").effective_value, "top_level")
    bottom = _number(
        automatic.value("bottom_level").effective_value, "bottom_level"
    )
    stepdown = max(
        1.0e-12,
        _number(automatic.value("stepdown_mm").effective_value, "stepdown_mm"),
    )
    level_count = (
        0
        if top < bottom
        else max(1, int(math.ceil((top - bottom) / stepdown)) + 1)
    )
    geometry_state = (
        "Đã xác định"
        if context.geometry_resolved and surfaces
        else "STALE"
        if surfaces
        else "Chưa chọn"
    )
    machine_id = (
        ""
        if context.operation.machine_requirement is None
        else str(context.operation.machine_requirement.machine_id)
    )
    return {
        "operation_name": context.operation_name,
        "operation_type": "Gia công tinh theo cao độ Z",
        "enabled": context.operation.enabled,
        "geometry_summary": f"{len(surfaces)} bề mặt gia công · {geometry_state}",
        "selected_face_count": str(len(surfaces)),
        "selected_body_setup_summary": (
            f"Thiết lập: {context.setup.name} · {context.setup.work_offset.name}"
        ),
        "geometry_reference_summary": (
            ", ".join(str(item.geometry.reference_id.value)[:8] for item in surfaces)
            or "Không có"
        ),
        "reselect_geometry": "Chọn lại bề mặt từ vùng hiển thị CAD",
        "remove_geometry": "Loại các bề mặt đang chọn khỏi bản nháp",
        "clear_geometry": "Xóa lựa chọn bề mặt trong bản nháp",
        "tool_assembly_id": seed["tool_assembly_id"],
        "tool_details": _tool_text(assembly, tool),
        "holder_state": holder_state,
        "holder_scope": f"Đã kiểm tra: {checked} · Chưa xác minh: {unverified}",
        "quality_profile": seed["quality_profile"],
        "automatic_policy_summary": (
            "HMS tính từ hình học, Tool, Thiết lập và hồ sơ chất lượng; "
            "override chỉ nằm trong Nâng cao."
        ),
        "automatic_mode_counts": _automatic_mode_counts(automatic),
        "machining_frame_summary": _automatic_summary(
            automatic, "machining_frame"
        ),
        "top_level_summary": _automatic_summary(
            automatic, "top_level", suffix=" mm"
        ),
        "bottom_level_summary": _automatic_summary(
            automatic, "bottom_level", suffix=" mm"
        ),
        "stepdown_summary": _automatic_summary(
            automatic, "stepdown_mm", suffix=" mm"
        ),
        "estimated_level_count": str(level_count),
        "tolerance_summary": _automatic_summary(
            automatic, "tolerance_mm", suffix=" mm"
        ),
        "allowance_summary": _automatic_summary(
            automatic, "surface_allowance_mm", suffix=" mm"
        ),
        "orientation_summary": _automatic_summary(automatic, "orientation"),
        "linking_summary": _automatic_summary(automatic, "linking_mode"),
        "safety_scope_summary": _automatic_summary(automatic, "safety_scope"),
        "protected_geometry_summary": _automatic_summary(
            automatic, "protected_geometry_scope"
        ),
        "holder_summary": holder_state,
        "automatic_effective_hash": automatic.effective_fingerprint.digest[:12],
        **seed,
        "clearance_z_mm": str(parameters.clearance_z_mm),
        "retract_z_mm": str(parameters.retract_z_mm),
        "link_clearance_mm": str(parameters.link_clearance_mm),
        "feed_rate_mm_per_minute": str(parameters.feed_rate_mm_per_minute),
        "maximum_segment_length_mm": str(
            automatic.value("maximum_segment_length_mm").effective_value
        ),
        "normal_variation_limit": str(
            automatic.value("normal_variation_limit_degrees").effective_value
        ),
        "safety_sampling_policy": str(
            display_value(
                automatic.value("safety_sampling_policy").effective_value,
                "z_level_safety_sampling_policy",
            )
        ),
        "machine_id": machine_id,
        "capability_summary": (
            "Tool cầu · ba trục cố định · đường đồng mức theo cao độ Z · "
            "Toolpath IR · hợp đồng an toàn Stage 8A.3.2"
        ),
        "unsupported_summary": (
            "Không hỗ trợ Tool khác Tool cầu, đa trục, chứng nhận sẵn sàng chạy máy "
            "hoặc Post sản xuất."
        ),
        "calculation_status": safety.artifact_state,
        "safety_status": safety.status,
        "safety_algorithm_version": (
            f"v{context.safety_report.algorithm_version}"
            if context.safety_report is not None
            else "Yêu cầu v2"
        ),
        "checked_components": safety.checked_components,
        "unverified_components": safety.unverified_components,
        "machine_ready_clearance": "Chưa xác minh",
        "safety_finding_counts": safety.finding_counts,
        "safety_report_hash": (
            safety.report_hash[:12] if safety.report_hash else "Chưa tính"
        ),
        "diagnostic_summary": safety.diagnostic_summary,
        "simulation_gate": safety.simulation_gate,
        "post_gate": safety.post_gate,
        "summary": (
            f"{len(surfaces)} bề mặt · {level_count} lớp Z dự kiến · "
            f"cao độ {top:g} → {bottom:g} mm · bước xuống {stepdown:g} mm · "
            f"{safety.status}"
        ),
    }


def z_level_draft_derived_values(
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Recompute automatic summaries after every relevant draft edit."""
    automatic = _resolve_automatic_contract(context, draft, values)
    top = _number(automatic.value("top_level").effective_value, "top_level")
    bottom = _number(
        automatic.value("bottom_level").effective_value, "bottom_level"
    )
    stepdown = max(
        1.0e-12,
        _number(automatic.value("stepdown_mm").effective_value, "stepdown_mm"),
    )
    level_count = (
        0
        if top < bottom
        else max(1, int(math.ceil((top - bottom) / stepdown)) + 1)
    )
    assembly_id = str(values.get("tool_assembly_id", ""))
    assembly, tool, holder = _assembly_resources(context, assembly_id)
    holder_state, checked, unverified = _holder_text(assembly, holder)
    return {
        "tool_details": _tool_text(assembly, tool),
        "holder_state": holder_state,
        "holder_scope": f"Đã kiểm tra: {checked} · Chưa xác minh: {unverified}",
        "holder_summary": holder_state,
        "automatic_mode_counts": _automatic_mode_counts(automatic),
        "machining_frame_summary": _automatic_summary(
            automatic, "machining_frame"
        ),
        "top_level_summary": _automatic_summary(
            automatic, "top_level", suffix=" mm"
        ),
        "bottom_level_summary": _automatic_summary(
            automatic, "bottom_level", suffix=" mm"
        ),
        "stepdown_summary": _automatic_summary(
            automatic, "stepdown_mm", suffix=" mm"
        ),
        "estimated_level_count": str(level_count),
        "tolerance_summary": _automatic_summary(
            automatic, "tolerance_mm", suffix=" mm"
        ),
        "allowance_summary": _automatic_summary(
            automatic, "surface_allowance_mm", suffix=" mm"
        ),
        "orientation_summary": _automatic_summary(automatic, "orientation"),
        "linking_summary": _automatic_summary(automatic, "linking_mode"),
        "safety_scope_summary": _automatic_summary(automatic, "safety_scope"),
        "protected_geometry_summary": _automatic_summary(
            automatic, "protected_geometry_scope"
        ),
        "automatic_effective_hash": automatic.effective_fingerprint.digest[:12],
        "maximum_segment_length_mm": str(
            automatic.value("maximum_segment_length_mm").effective_value
        ),
        "normal_variation_limit": str(
            automatic.value("normal_variation_limit_degrees").effective_value
        ),
        "safety_sampling_policy": str(
            display_value(
                automatic.value("safety_sampling_policy").effective_value,
                "z_level_safety_sampling_policy",
            )
        ),
        "summary": (
            f"{len(draft.surfaces)} bề mặt · {level_count} lớp Z dự kiến · "
            f"cao độ {top:g} → {bottom:g} mm · bước xuống {stepdown:g} mm · "
            "chưa tính đường chạy dao"
        ),
    }


def _complete_values(
    context: ZLevelEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    complete = z_level_applied_values(context)
    complete.update(values)
    return complete


def _draft_surfaces(
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> tuple[CamSurfaceReference, ...]:
    count = int(_number(values["selected_face_count"], "selected_face_count"))
    candidates = draft.surfaces if count == len(draft.surfaces) else _surfaces(context)
    if count != len(candidates):
        raise ValueError(
            "z_level.invalid_face_reference: Lựa chọn bề mặt đã stale."
        )
    if not candidates:
        raise ValueError("z_level.no_geometry: Chưa chọn bề mặt gia công.")
    return candidates


def _geometry_inputs(
    existing: tuple[OperationGeometryInput, ...],
    surfaces: tuple[CamSurfaceReference, ...],
    draft: ZLevelEditorDraftContext,
) -> tuple[OperationGeometryInput, ...]:
    previous = {item.reference.reference_id: item for item in existing}
    pending = draft.pending_input_ids or {}
    result = []
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


def prepare_z_level_update(
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> ZLevelOperationUpdate:
    """Build one validated operation/zone pair without mutating project state."""
    complete = _complete_values(context, values)
    surfaces = _draft_surfaces(context, draft, complete)
    revisions = {item.geometry.expected_source_revision for item in surfaces}
    if len(revisions) != 1:
        raise ValueError(
            "z_level.invalid_face_reference: Các mặt dùng revision khác nhau."
        )
    assembly_id = _text(complete["tool_assembly_id"], "tool_assembly_id")
    assembly, tool, holder = _assembly_resources(context, assembly_id)
    if assembly is None or tool is None:
        raise ValueError("z_level.invalid_tool: Thiếu Tool Assembly/Definition.")
    if (
        tool.family is not ToolFamily.BALL_END_MILL
        or not isinstance(tool.cutting_geometry, BallEndGeometry)
        or tool.unit is not LengthUnit.MM
        or assembly.unit is not LengthUnit.MM
    ):
        raise ValueError(
            "z_level.unsupported_tool: Chỉ hỗ trợ ball-end Tool dùng đơn vị mm."
        )
    if assembly.holder_id is None or not _holder_reference_is_current(
        assembly, holder
    ):
        raise ValueError(
            "z_level.safety.invalid_holder: Holder thiếu, stale hoặc không hợp lệ."
        )
    machine_id = _text(complete["machine_id"], "machine_id")
    machine = next(
        (
            item
            for item in context.machine_definitions
            if str(item.machine_id) == machine_id
        ),
        None,
    )
    if (
        machine is None
        or machine.unit is not LengthUnit.MM
        or OperationCapability.MILLING not in machine.capabilities.operations
    ):
        raise ValueError(
            "z_level.invalid_workplane: Cần máy phay dùng đơn vị mm."
        )
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
            f"z_level.invalid_manual_override: {invalid_manual.validation.message}"
        )
    unresolved = next(
        (
            item
            for item in automatic.values
            if item.status
            in {
                AutomaticParameterStatus.UNRESOLVED,
                AutomaticParameterStatus.UNSUPPORTED,
            }
            and item.key
            in {
                "machining_frame",
                "top_level",
                "bottom_level",
                "stepdown_mm",
            }
        ),
        None,
    )
    if unresolved is not None:
        raise ValueError(
            f"z_level.automatic_unresolved: {unresolved.key} chưa được xác định."
        )
    top = _number(automatic.value("top_level").effective_value, "top_level")
    bottom = _number(
        automatic.value("bottom_level").effective_value, "bottom_level"
    )
    stepdown = _number(
        automatic.value("stepdown_mm").effective_value, "stepdown_mm"
    )
    tolerance_mm = _number(
        automatic.value("tolerance_mm").effective_value, "tolerance_mm"
    )
    allowance_mm = _number(
        automatic.value("surface_allowance_mm").effective_value,
        "surface_allowance_mm",
    )
    if top <= bottom:
        raise ValueError(
            "z_level.invalid_bounds: Cao độ trên phải lớn hơn cao độ dưới."
        )
    if stepdown <= 0.0:
        raise ValueError("z_level.invalid_stepdown: Bước xuống phải lớn hơn 0.")
    if tolerance_mm <= 0.0:
        raise ValueError("z_level.invalid_tolerance: Dung sai phải lớn hơn 0.")
    if allowance_mm < 0.0:
        raise ValueError("z_level.invalid_allowance: Lượng dư không được âm.")
    level_count = int(math.ceil((top - bottom) / stepdown)) + 1
    if level_count > 20_000:
        raise ValueError(
            "z_level.excessive_level_count: Số lớp Z vượt giới hạn bảo vệ 20.000."
        )
    frame = context.setup.wcs
    parameters = ZLevelFinishingParameters(
        _parameters(context).zone_id,
        top,
        bottom,
        stepdown,
        tolerance_mm,
        allowance_mm,
        ZLevelOrientation(
            _text(automatic.value("orientation").effective_value, "orientation")
        ),
        ZLevelBoundaryPolicy(
            _text(
                automatic.value("boundary_policy").effective_value,
                "boundary_policy",
            )
        ),
        ZLevelLinkingMode(
            _text(automatic.value("linking_mode").effective_value, "linking_mode")
        ),
        _number(complete["feed_rate_mm_per_minute"], "feed_rate_mm_per_minute"),
        _number(
            automatic.value("maximum_segment_length_mm").effective_value,
            "maximum_segment_length_mm",
        ),
        _number(complete["clearance_z_mm"], "clearance_z_mm"),
        _number(complete["retract_z_mm"], "retract_z_mm"),
        _number(complete["link_clearance_mm"], "link_clearance_mm"),
        str(context.setup.setup_id),
        ZLevelMachiningFrame(
            frame.origin,
            frame.x_axis,
            frame.y_axis,
            frame.z_axis,
        ),
    )
    if parameters.clearance_z_mm < parameters.retract_z_mm:
        raise ValueError(
            "z_level.invalid_parameters: Clearance phải cao hơn hoặc bằng retract."
        )
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
    zone = MachiningZone3D(
        parameters.zone_id,
        context.project_id,
        context.job_id,
        context.operation.setup_id,
        context.setup.revision,
        context.setup.wcs,
        PartSurfaceSet(selection),
        context.zone.check_surfaces if context.zone is not None else None,
        context.zone.fixture_surfaces if context.zone is not None else None,
        context.zone.boundary if context.zone is not None else None,
        context.setup.wcs.z_axis,
        context.setup.wcs.x_axis,
        bottom,
        top,
        tolerance,
        Cam3DStockAllowance(part_normal=allowance_mm),
        next(iter(revisions)),
        GeometryFingerprint.from_payload(
            {"z_level_faces": [item.identity_payload() for item in surfaces]}
        ),
    )
    safe_motion = Cam3DSafeMotionPolicy(
        context.operation.setup_id,
        context.setup.revision,
        wcs_fingerprint(context.setup.wcs),
        parameters.clearance_z_mm,
        parameters.retract_z_mm,
        2.0,
        parameters.link_clearance_mm,
        Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID,
        context.setup.wcs.z_axis,
    )
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    geometry_inputs = _geometry_inputs(
        context.operation.geometry_inputs, surfaces, draft
    )
    base = parameters.to_operation_parameters()
    operation_parameters = OperationParameterSet(
        base.strategy_key,
        base.strategy_version,
        base.values + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, automatic.to_json()),),
        base.schema_version,
    )
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(complete["enabled"], "enabled")
    differences = (
        operation_parameters != context.operation.parameters,
        geometry_inputs != context.operation.geometry_inputs,
        tool_reference != context.operation.tool_assembly,
        requirement != context.operation.machine_requirement,
        enabled != context.operation.enabled,
        zone != context.zone,
    )
    changed = context.operation
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
    return ZLevelOperationUpdate(
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


def z_level_validation_diagnostics(
    schema: FunctionEditorSchema,
    context: ZLevelEditorContext,
    draft: ZLevelEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> tuple[FunctionEditorDiagnostic, ...]:
    """Validate the draft fail-closed and retain precise Vietnamese messages."""
    diagnostics: list[FunctionEditorDiagnostic] = []
    try:
        prepare_z_level_update(context, draft, values)
    except (KeyError, TypeError, ValueError) as error:
        message = str(error) or "Bản nháp Z-Level không hợp lệ."
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
    diagnostics.extend(
        (
            FunctionEditorDiagnostic(
                "z_level.machine_clearance_unverified",
                "Khoảng hở sẵn sàng cho máy chưa được xác minh.",
                FunctionEditorDiagnosticSeverity.INFO,
                "machine_ready_clearance",
                "capability_safety",
            ),
            FunctionEditorDiagnostic(
                "z_level.post_unsupported",
                f"{Z_LEVEL_POST_GATE_REASON}.",
                FunctionEditorDiagnosticSeverity.WARNING,
                "post_gate",
                "capability_safety",
            ),
        )
    )
    return tuple(diagnostics)


def _diagnostic_code(message: str) -> str:
    prefix = message.split(":", 1)[0].strip()
    return (
        prefix.replace("_", "-")
        if prefix.startswith("z_level.")
        else "z_level.invalid_parameters"
    )


def _error_field(message: str) -> str:
    folded = message.casefold()
    mapping = (
        (("face", "bề mặt", "geometry"), "geometry_summary"),
        (("holder",), "holder_state"),
        (("tool", "dao"), "tool_assembly_id"),
        (("stepdown", "bước xuống"), "stepdown_mm"),
        (("tolerance", "dung sai"), "tolerance_mm"),
        (("allowance", "lượng dư"), "surface_allowance_mm"),
        (("top", "cao độ trên"), "top_level"),
        (("bottom", "cao độ dưới"), "bottom_level"),
        (("clearance",), "clearance_z_mm"),
        (("retract",), "retract_z_mm"),
        (("machine", "workplane", "máy"), "machine_id"),
    )
    return next(
        (field for tokens, field in mapping if any(token in folded for token in tokens)),
        "operation_name",
    )


def _minimum(
    code: str,
    message: str,
    value: float = 1.0e-12,
) -> FunctionEditorValidationRule:
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
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.ADVANCED,
    applicable_when: FunctionEditorApplicability | None = None,
    unit: str = "mm",
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.NUMBER,
        value,
        unit,
        FunctionEditorValueSource.USER,
        value,
        applicable_when=applicable_when,
        required=True,
        disclosure_level=level,
        validators=validators,
        order=order,
        binding_key=f"z_level.{field_id}",
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
        binding_key=f"z_level.{field_id}",
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
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.ADVANCED,
    applicable_when: FunctionEditorApplicability | None = None,
    tooltip: str = "",
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
        tooltip=tooltip,
        order=order,
        binding_key=f"z_level.{field_id}",
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
            "Bật để dùng giá trị Thủ công; tắt hoặc dùng Khôi phục tự động "
            "để HMS tính lại từ các dữ liệu phụ thuộc hiện hành."
        ),
        order=order,
        binding_key=f"z_level.{field_id}",
        conversion=FunctionEditorValueConversion.BOOLEAN,
    )


def _manual_applicability(toggle: str) -> FunctionEditorApplicability:
    return FunctionEditorApplicability(toggle, ApplicabilityOperator.TRUTHY)


def build_z_level_schema(context: ZLevelEditorContext) -> FunctionEditorSchema:
    """Build compact Basic and controlled Advanced tabs for Z-Level."""
    values = z_level_applied_values(context)
    assembly, _tool, _holder = _assembly_resources(context)
    tool_choices = tuple(str(item.assembly_id) for item in context.tool_assemblies)
    if not tool_choices:
        tool_choices = (str(context.operation.tool_assembly.assembly_id),)
    tool_labels = tuple(
        (str(item.assembly_id), item.name) for item in context.tool_assemblies
    ) or ((tool_choices[0], "Thiếu Tool Assembly"),)
    milling_machines = tuple(
        item
        for item in context.machine_definitions
        if item.unit is LengthUnit.MM
        and OperationCapability.MILLING in item.capabilities.operations
    )
    machine_choices = tuple(str(item.machine_id) for item in milling_machines) or (
        str(values["machine_id"]),
    )
    machine_labels = tuple(
        (str(item.machine_id), item.name) for item in milling_machines
    ) or ((str(values["machine_id"]), "Thiếu máy phay"),)
    sections = (
        FunctionEditorSection(
            "operation",
            "NGUYÊN CÔNG",
            (
                FunctionEditorField(
                    "operation_name",
                    "Tên nguyên công",
                    FunctionEditorFieldKind.TEXT,
                    values["operation_name"],
                    required=True,
                    disclosure_level=ParameterDisclosureLevel.ADVANCED,
                    order=10,
                    binding_key="node.name",
                    conversion=FunctionEditorValueConversion.TEXT,
                ),
                _read_only(
                    "operation_type",
                    "Loại nguyên công",
                    values["operation_type"],
                    order=20,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                FunctionEditorField(
                    "enabled",
                    "Áp dụng",
                    FunctionEditorFieldKind.CHECKBOX,
                    values["enabled"],
                    required=True,
                    disclosure_level=ParameterDisclosureLevel.ADVANCED,
                    order=30,
                    binding_key="operation.enabled",
                    conversion=FunctionEditorValueConversion.BOOLEAN,
                ),
            ),
            "Danh tính và trạng thái nguyên công.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=5,
        ),
        FunctionEditorSection(
            "geometry",
            "HÌNH HỌC",
            (
                _read_only(
                    "geometry_summary",
                    "Bề mặt gia công",
                    values["geometry_summary"],
                    order=10,
                    source=FunctionEditorValueSource.GEOMETRY,
                    action_id="select_z_level_faces",
                    action_label="Chọn / Thay đổi",
                ),
                _read_only(
                    "selected_face_count",
                    "Số mặt đã chọn",
                    values["selected_face_count"],
                    order=20,
                ),
                _read_only(
                    "selected_body_setup_summary",
                    "Thân / Thiết lập",
                    values["selected_body_setup_summary"],
                    order=30,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "geometry_reference_summary",
                    "ID mặt bền vững",
                    values["geometry_reference_summary"],
                    order=40,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "reselect_geometry",
                    "Chọn lại",
                    values["reselect_geometry"],
                    order=50,
                    level=ParameterDisclosureLevel.ADVANCED,
                    action_id="reselect_z_level_faces",
                    action_label="Chọn lại",
                    tooltip=(
                        "Chọn lại bề mặt gia công từ vùng hiển thị CAD."
                    ),
                ),
                _read_only(
                    "remove_geometry",
                    "Loại bề mặt",
                    values["remove_geometry"],
                    order=60,
                    level=ParameterDisclosureLevel.ADVANCED,
                    action_id="remove_z_level_faces",
                    action_label="Loại",
                    tooltip="Loại bề mặt đang chọn khỏi bản nháp.",
                ),
                _read_only(
                    "clear_geometry",
                    "Xóa lựa chọn",
                    values["clear_geometry"],
                    order=70,
                    level=ParameterDisclosureLevel.ADVANCED,
                    action_id="clear_z_level_faces",
                    action_label="Xóa",
                    tooltip="Xóa toàn bộ lựa chọn bề mặt trong bản nháp.",
                ),
            ),
            f"{values['selected_face_count']} mặt đã chọn.",
            order=10,
        ),
        FunctionEditorSection(
            "tool",
            "TOOL",
            (
                _choice(
                    "tool_assembly_id",
                    "Tool cầu",
                    values["tool_assembly_id"],
                    tool_choices,
                    tool_labels,
                    order=10,
                    level=ParameterDisclosureLevel.BASIC,
                ),
                _read_only(
                    "tool_details",
                    "Chi tiết Tool",
                    values["tool_details"],
                    order=20,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "holder_state",
                    "Trạng thái Holder",
                    values["holder_state"],
                    order=30,
                ),
                _read_only(
                    "holder_scope",
                    "Phạm vi Tool Assembly",
                    values["holder_scope"],
                    order=40,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            assembly.name if assembly is not None else "Cần Tool cầu hợp lệ.",
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
                    level=ParameterDisclosureLevel.BASIC,
                ),
            ),
            "Hồ sơ điều khiển bước xuống, dung sai và mật độ rời rạc.",
            order=25,
        ),
        FunctionEditorSection(
            "automatic_summary",
            "TÓM TẮT TÍNH TOÁN TỰ ĐỘNG",
            (
                _read_only(
                    "machining_frame_summary",
                    "Khung gia công",
                    values["machining_frame_summary"],
                    order=10,
                ),
                _read_only(
                    "top_level_summary",
                    "Cao độ trên",
                    values["top_level_summary"],
                    order=20,
                ),
                _read_only(
                    "bottom_level_summary",
                    "Cao độ dưới",
                    values["bottom_level_summary"],
                    order=30,
                ),
                _read_only(
                    "stepdown_summary",
                    "Bước xuống",
                    values["stepdown_summary"],
                    order=40,
                ),
                _read_only(
                    "estimated_level_count",
                    "Số lớp Z dự kiến",
                    values["estimated_level_count"],
                    order=50,
                ),
                _read_only(
                    "tolerance_summary",
                    "Dung sai",
                    values["tolerance_summary"],
                    order=60,
                ),
                _read_only(
                    "allowance_summary",
                    "Lượng dư",
                    values["allowance_summary"],
                    order=70,
                ),
                _read_only(
                    "orientation_summary",
                    "Chiều contour",
                    values["orientation_summary"],
                    order=80,
                ),
                _read_only(
                    "linking_summary",
                    "Liên kết",
                    values["linking_summary"],
                    order=90,
                ),
                _read_only(
                    "safety_scope_summary",
                    "Phạm vi an toàn",
                    values["safety_scope_summary"],
                    order=100,
                ),
                _read_only(
                    "holder_summary",
                    "Trạng thái Holder",
                    values["holder_summary"],
                    order=110,
                ),
                _read_only(
                    "protected_geometry_summary",
                    "Hình học bảo vệ",
                    values["protected_geometry_summary"],
                    order=115,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "automatic_mode_counts",
                    "Chế độ tham số",
                    values["automatic_mode_counts"],
                    order=120,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "automatic_effective_hash",
                    "Hash tham số hiệu lực",
                    values["automatic_effective_hash"],
                    order=130,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            values["automatic_policy_summary"],
            order=30,
        ),
        FunctionEditorSection(
            "levels",
            "PHẠM VI CAO ĐỘ",
            (
                _manual_checkbox(
                    "top_override_enabled",
                    "Tùy chỉnh cao độ trên",
                    values["top_override_enabled"],
                    order=10,
                ),
                _number_field(
                    "top_level",
                    "Cao độ trên",
                    values["top_level"],
                    order=20,
                    applicable_when=_manual_applicability("top_override_enabled"),
                ),
                _manual_checkbox(
                    "bottom_override_enabled",
                    "Tùy chỉnh cao độ dưới",
                    values["bottom_override_enabled"],
                    order=30,
                ),
                _number_field(
                    "bottom_level",
                    "Cao độ dưới",
                    values["bottom_level"],
                    order=40,
                    applicable_when=_manual_applicability(
                        "bottom_override_enabled"
                    ),
                ),
                _manual_checkbox(
                    "stepdown_override_enabled",
                    "Tùy chỉnh bước xuống",
                    values["stepdown_override_enabled"],
                    order=50,
                ),
                _number_field(
                    "stepdown_mm",
                    "Bước xuống tối đa",
                    values["stepdown_mm"],
                    order=60,
                    validators=(
                        _minimum(
                            "z_level.stepdown-positive",
                            "Bước xuống phải lớn hơn 0.",
                        ),
                    ),
                    applicable_when=_manual_applicability(
                        "stepdown_override_enabled"
                    ),
                ),
            ),
            "Giá trị Tự động theo hộp bao mặt đã chọn.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=40,
        ),
        FunctionEditorSection(
            "cut_parameters",
            "THAM SỐ CẮT",
            (
                _manual_checkbox(
                    "tolerance_override_enabled",
                    "Tùy chỉnh dung sai",
                    values["tolerance_override_enabled"],
                    order=10,
                ),
                _number_field(
                    "tolerance_mm",
                    "Dung sai",
                    values["tolerance_mm"],
                    order=20,
                    validators=(
                        _minimum(
                            "z_level.tolerance-positive",
                            "Dung sai phải lớn hơn 0.",
                        ),
                    ),
                    applicable_when=_manual_applicability(
                        "tolerance_override_enabled"
                    ),
                ),
                _manual_checkbox(
                    "allowance_override_enabled",
                    "Tùy chỉnh lượng dư",
                    values["allowance_override_enabled"],
                    order=30,
                ),
                _number_field(
                    "surface_allowance_mm",
                    "Lượng dư",
                    values["surface_allowance_mm"],
                    order=40,
                    validators=(
                        _minimum(
                            "z_level.allowance-nonnegative",
                            "Lượng dư không được âm.",
                            0.0,
                        ),
                    ),
                    applicable_when=_manual_applicability(
                        "allowance_override_enabled"
                    ),
                ),
                _number_field(
                    "feed_rate_mm_per_minute",
                    "Lượng chạy dao",
                    values["feed_rate_mm_per_minute"],
                    order=50,
                    validators=(
                        _minimum(
                            "z_level.feed-positive",
                            "Lượng chạy dao phải lớn hơn 0.",
                        ),
                    ),
                    unit="mm/min",
                ),
                _read_only(
                    "maximum_segment_length_mm",
                    "Độ dài đoạn tối đa",
                    values["maximum_segment_length_mm"],
                    order=60,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "normal_variation_limit",
                    "Giới hạn biến thiên pháp tuyến",
                    values["normal_variation_limit"],
                    order=70,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "safety_sampling_policy",
                    "Chính sách lấy mẫu safety",
                    values["safety_sampling_policy"],
                    order=80,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            ui_text("Giá trị hiệu lực tham gia hash artifact."),
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=50,
        ),
        FunctionEditorSection(
            "contours",
            ui_text("CONTOUR VÀ BIÊN"),
            (
                _manual_checkbox(
                    "orientation_override_enabled",
                    "Tùy chỉnh chiều contour",
                    values["orientation_override_enabled"],
                    order=10,
                ),
                _choice(
                    "orientation",
                    "Chiều contour",
                    values["orientation"],
                    tuple(item.value for item in ZLevelOrientation),
                    (
                        (ZLevelOrientation.AUTOMATIC.value, "Tự động"),
                        (
                            ZLevelOrientation.CLOCKWISE.value,
                            "Cùng chiều kim đồng hồ",
                        ),
                        (
                            ZLevelOrientation.COUNTER_CLOCKWISE.value,
                            "Ngược chiều kim đồng hồ",
                        ),
                    ),
                    order=20,
                    applicable_when=_manual_applicability(
                        "orientation_override_enabled"
                    ),
                    tooltip=(
                        "Hướng đường đồng mức theo cấu trúc liên kết hình học."
                    ),
                ),
                _manual_checkbox(
                    "boundary_override_enabled",
                    "Tùy chỉnh chính sách biên",
                    values["boundary_override_enabled"],
                    order=30,
                ),
                _choice(
                    "boundary_policy",
                    "Chính sách biên",
                    values["boundary_policy"],
                    tuple(item.value for item in ZLevelBoundaryPolicy),
                    tuple(
                        (item.value, "Biên mặt đã cắt xén")
                        for item in ZLevelBoundaryPolicy
                    ),
                    order=40,
                    applicable_when=_manual_applicability(
                        "boundary_override_enabled"
                    ),
                ),
                _manual_checkbox(
                    "ordering_override_enabled",
                    "Tùy chỉnh thứ tự contour",
                    values["ordering_override_enabled"],
                    order=50,
                ),
                _choice(
                    "contour_ordering",
                    "Thứ tự contour",
                    values["contour_ordering"],
                    ("top_down_nearest_safe", "top_down_lexicographic"),
                    (
                        ("top_down_nearest_safe", "Cao xuống thấp · gần nhất an toàn"),
                        ("top_down_lexicographic", "Cao xuống thấp · xác định"),
                    ),
                    order=60,
                    applicable_when=_manual_applicability(
                        "ordering_override_enabled"
                    ),
                ),
            ),
            (
                "Hướng đường đồng mức theo cấu trúc liên kết hình học. "
                "Không cho đường đồng mức vượt miền mặt đã cắt xén."
            ),
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=60,
        ),
        FunctionEditorSection(
            "linking",
            "LIÊN KẾT VÀ AN TOÀN",
            (
                _manual_checkbox(
                    "linking_override_enabled",
                    "Tùy chỉnh liên kết",
                    values["linking_override_enabled"],
                    order=10,
                ),
                _choice(
                    "linking_mode",
                    "Liên kết",
                    values["linking_mode"],
                    tuple(item.value for item in ZLevelLinkingMode),
                    (
                        (ZLevelLinkingMode.RETRACT_CLEARANCE.value, "Rút dao bảo thủ"),
                        (
                            ZLevelLinkingMode.CONSERVATIVE_DIRECT.value,
                            "Liên kết trực tiếp có kiểm tra",
                        ),
                    ),
                    order=20,
                    applicable_when=_manual_applicability(
                        "linking_override_enabled"
                    ),
                ),
                _manual_checkbox(
                    "approach_override_enabled",
                    "Tùy chỉnh tiếp cận/rút dao",
                    values["approach_override_enabled"],
                    order=30,
                ),
                _choice(
                    "approach_retract_policy",
                    "Tiếp cận / rút dao",
                    values["approach_retract_policy"],
                    ("retract_then_rapid",),
                    (("retract_then_rapid", "Rút dao rồi chạy nhanh"),),
                    order=40,
                    applicable_when=_manual_applicability(
                        "approach_override_enabled"
                    ),
                ),
                _number_field(
                    "clearance_z_mm",
                    "Cao độ an toàn",
                    values["clearance_z_mm"],
                    order=50,
                    validators=(
                        _cross(
                            FunctionEditorValidationKind.GREATER_THAN_FIELD,
                            "retract_z_mm",
                            "z_level.clearance-order",
                            "Clearance phải cao hơn retract.",
                        ),
                    ),
                ),
                _number_field(
                    "retract_z_mm",
                    "Cao độ rút dao",
                    values["retract_z_mm"],
                    order=60,
                ),
                _number_field(
                    "link_clearance_mm",
                    "Khoảng hở liên kết",
                    values["link_clearance_mm"],
                    order=70,
                    validators=(
                        _minimum(
                            "z_level.link-clearance-nonnegative",
                            "Khoảng hở liên kết không được âm.",
                            0.0,
                        ),
                    ),
                ),
                _manual_checkbox(
                    "protected_geometry_override_enabled",
                    "Tùy chỉnh hình học bảo vệ",
                    values["protected_geometry_override_enabled"],
                    order=80,
                ),
                _choice(
                    "protected_geometry_scope",
                    "Phạm vi hình học bảo vệ",
                    values["protected_geometry_scope"],
                    ("part_boundary_only", "declared_protected_geometry"),
                    (
                        ("part_boundary_only", "Chỉ biên part đã chọn"),
                        ("declared_protected_geometry", "Hình học bảo vệ đã khai báo"),
                    ),
                    order=90,
                    applicable_when=_manual_applicability(
                        "protected_geometry_override_enabled"
                    ),
                ),
                _manual_checkbox(
                    "safety_scope_override_enabled",
                    "Tùy chỉnh phạm vi an toàn",
                    values["safety_scope_override_enabled"],
                    order=100,
                ),
                _choice(
                    "safety_scope",
                    "Phạm vi an toàn",
                    values["safety_scope"],
                    ("declared_geometry_and_tool_assembly",),
                    (
                        (
                            "declared_geometry_and_tool_assembly",
                            "Hình học và Tool Assembly đã khai báo",
                        ),
                    ),
                    order=110,
                    applicable_when=_manual_applicability(
                        "safety_scope_override_enabled"
                    ),
                ),
            ),
            "UNKNOWN không bao giờ được đổi thành SAFE.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=70,
        ),
        FunctionEditorSection(
            "capability_safety",
            ui_text("KHẢ NĂNG VÀ SAFETY"),
            (
                _choice(
                    "machine_id",
                    "Máy / Thiết lập",
                    values["machine_id"],
                    machine_choices,
                    machine_labels,
                    order=10,
                ),
                _read_only(
                    "capability_summary",
                    "Được hỗ trợ",
                    values["capability_summary"],
                    order=20,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "unsupported_summary",
                    "Chưa hỗ trợ / xác minh",
                    values["unsupported_summary"],
                    order=30,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "calculation_status",
                    "Trạng thái tính toán",
                    values["calculation_status"],
                    order=40,
                ),
                _read_only(
                    "safety_status",
                    "Trạng thái safety",
                    values["safety_status"],
                    order=50,
                ),
                _read_only(
                    "safety_algorithm_version",
                    "Thuật toán safety",
                    values["safety_algorithm_version"],
                    order=60,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "checked_components",
                    "Thành phần đã kiểm tra",
                    values["checked_components"],
                    order=70,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "unverified_components",
                    "Thành phần chưa xác minh",
                    values["unverified_components"],
                    order=80,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "machine_ready_clearance",
                    "Khoảng hở machine-ready",
                    values["machine_ready_clearance"],
                    order=90,
                ),
                _read_only(
                    "safety_finding_counts",
                    "Số phát hiện safety",
                    values["safety_finding_counts"],
                    order=100,
                ),
                _read_only(
                    "safety_report_hash",
                    "Hash báo cáo safety",
                    values["safety_report_hash"],
                    order=110,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "diagnostic_summary",
                    "Chẩn đoán",
                    values["diagnostic_summary"],
                    order=120,
                    action_id="open_z_level_safety_details",
                    action_label="Mở chi tiết",
                ),
                _read_only(
                    "simulation_gate",
                    "Mô phỏng",
                    values["simulation_gate"],
                    order=130,
                    action_id=(
                        "open_z_level_simulation"
                        if str(values["simulation_gate"]).startswith("Có thể")
                        else ""
                    ),
                    action_label=(
                        "Mở"
                        if str(values["simulation_gate"]).startswith("Có thể")
                        else ""
                    ),
                ),
                _read_only(
                    "post_gate",
                    "Post",
                    values["post_gate"],
                    order=140,
                ),
            ),
            "Chỉ artifact READY + SAFE hiện hành mới mở được Mô phỏng.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=80,
        ),
        FunctionEditorSection(
            "summary",
            "TÓM TẮT",
            (
                _read_only(
                    "summary",
                    "Nguyên công",
                    values["summary"],
                    order=10,
                ),
            ),
            "Không phải chứng nhận an toàn sản xuất hoặc sẵn sàng chạy máy.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=90,
        ),
    )
    schema = FunctionEditorSchema(
        Z_LEVEL_EDITOR_ID,
        FunctionEditorStrategyKey(Z_LEVEL_EDITOR_STRATEGY_KEY),
        FunctionEditorSummary(
            context.operation_name,
            "Gia công tinh theo cao độ Z · algorithm v2 · payload v1",
            assembly.name if assembly is not None else "Thiếu Tool",
            f"{values['selected_face_count']} mặt",
            (
                f"{'ÁP DỤNG' if context.operation.enabled else 'ĐÃ TẮT'} · "
                f"{values['calculation_status']} · {values['safety_status']}"
            ),
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
    validate_z_level_schema_contract(schema)
    return schema


def validate_z_level_schema_contract(schema: FunctionEditorSchema) -> None:
    """Fail closed on missing bindings, empty Expert or version drift."""
    if any(not field.binding_key for field in schema.fields):
        raise ValueError("Z-Level editor fields must declare explicit bindings")
    if any(
        section.disclosure_level is ParameterDisclosureLevel.EXPERT
        for section in schema.sections
    ):
        raise ValueError("Z-Level v1 has no production Expert section")
    if (
        Z_LEVEL_FINISHING_ALGORITHM_VERSION != 2
        or Z_LEVEL_FINISHING_STRATEGY_VERSION != 1
    ):
        raise ValueError("Z-Level editor version contract is incompatible")


__all__ = [
    "Z_LEVEL_EDITOR_ID",
    "Z_LEVEL_EDITOR_STRATEGY_KEY",
    "Z_LEVEL_POST_FAIL_CLOSED_FOOTER",
    "Z_LEVEL_POST_GATE_REASON",
    "ZLevelEditorContext",
    "ZLevelEditorDraftContext",
    "ZLevelOperationUpdate",
    "ZLevelSafetyPresentation",
    "build_z_level_schema",
    "prepare_z_level_update",
    "validate_z_level_schema_contract",
    "z_level_applied_values",
    "z_level_draft_derived_values",
    "z_level_safety_presentation",
    "z_level_validation_diagnostics",
]
