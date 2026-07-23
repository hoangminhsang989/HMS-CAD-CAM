"""Application service for deterministic Z-Level calculation and publication."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid5

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.parallel.safety import validate_parallel_candidate_safety
from hms_cadcam.cam.cam3d.parallel.safety_models import ParallelSafetyReport, ParallelSafetyStatus
from hms_cadcam.cam.cam3d.zlevel.geometry import (
    build_machining_frame,
    calculate_region_bounds,
    plan_level_schedule,
    trace_z_level,
)
from hms_cadcam.cam.cam3d.zlevel.models import (
    ContactResolver,
    ProgressCallback,
    ZLevelFinishingError,
    ZLevelArtifactLifecycle,
    ZLevelArtifactStatus,
    ZLevelFinishingParameters,
    ZLevelMachiningFrame,
    ZLevelPreview,
    ZLevelProgress,
    ZLevelProgressPhase,
    ZLevelStatistics,
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_KEY,
)
from hms_cadcam.cam.domain.ids import OperationId, ToolpathArtifactId
from hms_cadcam.cam.domain.operation import (
    ArtifactStatus,
    ComputationToken,
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryInputRole,
    Operation,
    OperationFamily,
    ToolReferenceStatus,
    ValidationDiagnostic,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.tooling import (
    BallEndGeometry,
    HolderDefinition,
    ToolAssembly,
    ToolDefinition,
    ToolFamily,
)
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, LengthUnit
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import ToolpathArtifactMetadata
from hms_cadcam.cam.toolpath import FeedMode, MotionClass, Pose, ToolpathArtifact, ToolpathBuilder
from hms_cadcam.cam.toolpath.validation import publish_toolpath

logger = logging.getLogger(__name__)
_ARTIFACT_NAMESPACE = UUID("d7cf7ce0-07ad-4f34-aec3-cf6eea4a5c1f")


@dataclass(frozen=True, slots=True)
class ZLevelFinishingInputs:
    operation: Operation
    context: Cam3DCalculationContext
    parameters: ZLevelFinishingParameters
    assembly: ToolAssembly
    tool: ToolDefinition
    holder: HolderDefinition | None
    tool_radius: float
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class ZLevelFinishingCandidate:
    artifact: ToolpathArtifact
    preview: ZLevelPreview


@dataclass(frozen=True, slots=True)
class ZLevelFinishingComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    preview: ZLevelPreview | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    metadata: ToolpathArtifactMetadata | None = None
    safety_report: ParallelSafetyReport | None = None
    lifecycle: ZLevelArtifactLifecycle | None = None


class ZLevelFinishingGenerator:
    """Controller-neutral fixed-axis ball-end Z-Level generator."""

    def resolve_inputs(
        self,
        operation: Operation,
        context: Cam3DCalculationContext,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        holder: HolderDefinition | None = None,
    ) -> ZLevelFinishingInputs:
        try:
            parameters = ZLevelFinishingParameters.from_operation_parameters(operation.parameters)
        except Exception as error:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_PARAMETERS, "Tham số Z-Level không hợp lệ.") from error
        if operation.family is not OperationFamily.MILLING or not operation.enabled:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_PARAMETERS, "Z-Level chỉ áp dụng cho nguyên công phay đang bật.")
        if not isinstance(context, Cam3DCalculationContext):
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_NO_GEOMETRY, "Z-Level cần CAM 3D context hiện hành.")
        zone = context.machining_zone
        if parameters.zone_id != zone.zone_id:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_NO_GEOMETRY, "Nguyên công Z-Level tham chiếu machining zone khác.")
        if operation.setup_id != context.setup_id or operation.setup_id != zone.setup_id:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_WORKPLANE, "Z-Level dùng Setup khác với geometry context.")
        selected = {
            item.geometry.reference_id
            for item in zone.part_surfaces.selection.surfaces
        }
        persisted = {
            item.reference.reference_id
            for item in operation.geometry_inputs
            if item.role is GeometryInputRole.DRIVE_GEOMETRY
        }
        if not selected or not persisted:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_NO_GEOMETRY, "Z-Level cần một hoặc nhiều mặt BRep đã chọn và persist.")
        if selected != persisted:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_FACE_REFERENCE, "Face reference Z-Level không khớp selection hiện hành.")
        if assembly is None or operation.tool_assembly.assess(assembly) is not ToolReferenceStatus.VALID:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Tool Assembly Z-Level thiếu, stale hoặc không tương thích.")
        if tool is None or tool.tool_id != assembly.tool_id:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Tool Definition Z-Level không tồn tại.")
        if tool.family is not ToolFamily.BALL_END_MILL or not isinstance(tool.cutting_geometry, BallEndGeometry):
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_UNSUPPORTED_TOOL, "Z-Level chỉ hỗ trợ ball-end Tool; không fallback sang Parallel.")
        if tool.unit is not LengthUnit.MM or assembly.unit is not LengthUnit.MM:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Z-Level chỉ hỗ trợ Tool/Assembly MM.")
        if (
            tool.revision != assembly.expected_tool_revision
            or tool.content_fingerprint != assembly.expected_tool_fingerprint
            or context.tool_definition_fingerprint != tool.content_fingerprint
        ):
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Tool snapshot Z-Level đã stale.")
        assembly_fp = ContentFingerprint.from_payload(assembly.to_dict())
        if context.tool_assembly_fingerprint not in {assembly_fp, assembly.content_fingerprint}:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Tool Assembly snapshot Z-Level đã stale.")
        if holder is not None and assembly.holder_id is not None and (
            holder.holder_id != assembly.holder_id
            or holder.revision != assembly.expected_holder_revision
            or holder.content_fingerprint != assembly.expected_holder_fingerprint
        ):
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOOL, "Holder snapshot Z-Level không khớp Assembly.")
        if context.tolerance_policy.contact_tolerance <= 0.0 or parameters.tolerance_mm < context.tolerance_policy.contact_tolerance:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_TOLERANCE, "Tolerance Z-Level nhỏ hơn contract tolerance của CAM 3D.")
        safe = context.safe_motion_policy
        if safe.clearance_z is None or safe.retract_z is None or safe.clearance_z < safe.retract_z:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_PARAMETERS, "Z-Level thiếu retract/clearance an toàn.")
        frame = build_machining_frame(context, parameters)
        input_fingerprint = DependencyFingerprint.from_payload(
            {
                "algorithm": "hms_z_level_implicit_ball_center",
                "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
                "strategy_key": Z_LEVEL_FINISHING_STRATEGY_KEY,
                "strategy_payload_version": operation.parameters.strategy_version,
                "operation": {
                    "id": str(operation.operation_id),
                    "revision": operation.revision.to_dict(),
                    "parameters": operation.parameters.to_dict(),
                    "geometry_inputs": [item.to_dict() for item in operation.geometry_inputs],
                },
                "context": context.fingerprint.to_dict(),
                "machining_frame": frame.to_dict(),
                "tool": tool.to_dict(),
                "assembly": assembly.to_dict(),
                "holder": holder.to_dict() if holder is not None else None,
            }
        )
        return ZLevelFinishingInputs(
            operation,
            context,
            parameters,
            assembly,
            tool,
            holder,
            tool.cutting_geometry.diameter.value / 2.0,
            input_fingerprint,
        )

    def begin(self, inputs: ZLevelFinishingInputs) -> tuple[ZLevelFinishingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(inputs, operation=replace(inputs.operation, artifact_state=state)), token

    def generate(
        self,
        inputs: ZLevelFinishingInputs,
        *,
        cancellation: Callable[[], bool] | None = None,
        progress: ProgressCallback | None = None,
        contact_resolver: ContactResolver | None = None,
    ) -> ZLevelFinishingCandidate:
        operation = inputs.operation
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_GENERATION_FAILED, "Z-Level generation cần computation token hiện hành.")
        frame = build_machining_frame(inputs.context, inputs.parameters)
        _emit(progress, operation.operation_id, ZLevelProgressPhase.VALIDATION, 1, 1)
        _checkpoint(cancellation)
        bounds = calculate_region_bounds(
            inputs.context.calculation_mesh,
            frame,
            inputs.context,
            tool_radius_mm=inputs.tool_radius,
            allowance_mm=inputs.parameters.surface_allowance_mm,
        )
        _emit(progress, operation.operation_id, ZLevelProgressPhase.BOUNDS, 1, 1)
        schedule = plan_level_schedule(
            inputs.parameters.top_level,
            inputs.parameters.bottom_level,
            inputs.parameters.stepdown_mm,
            tolerance=inputs.parameters.tolerance_mm,
        )
        if schedule.top_level > bounds.w_max + inputs.parameters.tolerance_mm or schedule.bottom_level < bounds.w_min - inputs.parameters.tolerance_mm:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_INVALID_BOUNDS, "Top/bottom tool-center level nằm ngoài machining envelope.")
        _emit(progress, operation.operation_id, ZLevelProgressPhase.LEVEL_SCHEDULE, len(schedule.levels), len(schedule.levels))
        preview = trace_z_level(
            inputs.context,
            frame,
            bounds,
            schedule,
            inputs.parameters,
            tool_radius_mm=inputs.tool_radius,
            cancellation=cancellation,
            contact_resolver=contact_resolver,
        )
        if not preview.passes:
            raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_UNRESOLVED_ROOT, "Z-Level không tạo được contour hợp lệ trong trimmed topology.")
        _emit(progress, operation.operation_id, ZLevelProgressPhase.CONTOUR_GRAPH, preview.statistics.contour_count, preview.statistics.contour_count)
        artifact = _build_candidate_toolpath(inputs, token, preview, safety_report=None, cancellation=cancellation)
        return ZLevelFinishingCandidate(artifact, preview)


def calculate_and_publish_z_level_finishing(
    project_root: Path,
    operation: Operation,
    context: Cam3DCalculationContext,
    *,
    assembly: ToolAssembly | None,
    tool: ToolDefinition | None,
    holder: HolderDefinition | None = None,
    artifact_store: ToolpathArtifactStore | None = None,
    cancellation: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
    current_operation: Callable[[], Operation] | None = None,
    contact_resolver: ContactResolver | None = None,
    computing_callback: Callable[[Operation], bool] | None = None,
) -> ZLevelFinishingComputeResult:
    generator = ZLevelFinishingGenerator()
    computing: ZLevelFinishingInputs | None = None
    token: ComputationToken | None = None
    try:
        inputs = generator.resolve_inputs(operation, context, assembly=assembly, tool=tool, holder=holder)
        computing, token = generator.begin(inputs)
        if computing_callback is not None and not computing_callback(computing.operation):
            diagnostic = ValidationDiagnostic(DiagnosticSeverity.WARNING, DiagnosticCode.Z_LEVEL_SUPERSEDED, "Tính toán Z-Level đã bị superseded trước khi bắt đầu.")
            return ZLevelFinishingComputeResult(
                operation,
                None,
                None,
                False,
                (diagnostic,),
                lifecycle=_lifecycle(
                    computing,
                    ZLevelArtifactStatus.STALE,
                    superseded=True,
                ),
            )
        candidate = generator.generate(
            computing,
            cancellation=cancellation,
            progress=progress,
            contact_resolver=contact_resolver,
        )
        _emit(progress, operation.operation_id, ZLevelProgressPhase.SAFETY, 0, 1)
        safety = validate_parallel_candidate_safety(
            operation=computing.operation,
            context=computing.context,
            tool=computing.tool,
            assembly=computing.assembly,
            holder=computing.holder,
            artifact=candidate.artifact,
            preview=candidate.preview,  # shared Stage 8A.2.2 structural contract
            cancellation=cancellation,
        )
        _emit(progress, operation.operation_id, ZLevelProgressPhase.SAFETY, 1, 1)
        if safety.status is not ParallelSafetyStatus.SAFE:
            diagnostics = tuple(item.to_validation_diagnostic() for item in safety.diagnostics)
            if not diagnostics:
                diagnostics = (ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.Z_LEVEL_SAFETY_UNKNOWN, "Safety pipeline Z-Level không chứng minh được SAFE."),)
            failed = _failed_operation(operation, computing, token, diagnostics[0])
            return ZLevelFinishingComputeResult(
                failed,
                None,
                None,
                False,
                diagnostics,
                safety_report=safety,
                lifecycle=_lifecycle(
                    computing,
                    ZLevelArtifactStatus.UNSAFE,
                    safety=safety,
                ),
            )
        safe_artifact = _build_candidate_toolpath(computing, token, candidate.preview, safety_report=safety, cancellation=cancellation)
        _checkpoint(cancellation)
        live = current_operation() if current_operation is not None else computing.operation
        published = publish_toolpath(live, safe_artifact, token, computing.input_fingerprint)
        if not published.accepted or published.artifact is None:
            diagnostic = ValidationDiagnostic(DiagnosticSeverity.WARNING, DiagnosticCode.Z_LEVEL_STALE_ARTIFACT, "Z-Level result đã stale trước khi publish.")
            return ZLevelFinishingComputeResult(
                published.operation,
                None,
                None,
                False,
                (diagnostic,),
                safety_report=safety,
                lifecycle=_lifecycle(
                    computing,
                    ZLevelArtifactStatus.STALE,
                    safety=safety,
                    superseded=True,
                ),
            )
        _checkpoint(cancellation)
        metadata = (artifact_store or ToolpathArtifactStore()).publish(project_root, published.artifact)
        limitation = ValidationDiagnostic(
            DiagnosticSeverity.WARNING,
            DiagnosticCode.Z_LEVEL_FOUNDATION_LIMITATION,
            "Z-Level là foundation cố định ba trục; chưa phải chứng nhận production-safe hoặc machine-ready clearance.",
        )
        warned = replace(published.operation, diagnostics=(*published.operation.diagnostics, limitation))
        return ZLevelFinishingComputeResult(
            warned,
            published.artifact,
            candidate.preview,
            True,
            (limitation,),
            metadata,
            safety,
            _lifecycle(
                computing,
                ZLevelArtifactStatus.READY,
                artifact=published.artifact,
                safety=safety,
            ),
        )
    except ZLevelFinishingError as error:
        failed = _failed_operation(operation, computing, token, error.diagnostic)
        lifecycle = (
            _lifecycle(
                computing,
                ZLevelArtifactStatus.CANCELLED
                if error.code is DiagnosticCode.Z_LEVEL_CANCELLED
                else ZLevelArtifactStatus.FAILED,
            )
            if computing is not None
            else None
        )
        return ZLevelFinishingComputeResult(
            failed,
            None,
            None,
            False,
            (error.diagnostic,),
            lifecycle=lifecycle,
        )
    except ToolpathArtifactStoreError:
        diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.Z_LEVEL_GENERATION_FAILED, "Không thể publish artifact Z-Level một cách nguyên tử.")
        failed = _failed_operation(operation, computing, token, diagnostic)
        return ZLevelFinishingComputeResult(
            failed,
            None,
            None,
            False,
            (diagnostic,),
            lifecycle=(
                _lifecycle(computing, ZLevelArtifactStatus.FAILED)
                if computing is not None
                else None
            ),
        )


def _build_candidate_toolpath(
    inputs: ZLevelFinishingInputs,
    token: ComputationToken,
    preview: ZLevelPreview,
    *,
    safety_report: ParallelSafetyReport | None,
    cancellation: Callable[[], bool] | None,
) -> ToolpathArtifact:
    artifact_id = ToolpathArtifactId(
        uuid5(_ARTIFACT_NAMESPACE, f"{inputs.operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}")
    )
    builder = ToolpathBuilder(
        artifact_id=artifact_id,
        operation_id=inputs.operation.operation_id,
        operation_revision=inputs.operation.revision,
        computation_token=token,
        input_fingerprint=inputs.input_fingerprint,
        unit=LengthUnit.MM,
        setup_id=inputs.context.setup_id,
        setup_revision=inputs.context.machining_zone.setup_revision,
        wcs_fingerprint=ContentFingerprint.from_payload(inputs.context.machining_zone.wcs.to_dict()),
        tool_assembly_id=inputs.assembly.assembly_id,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(inputs.assembly.to_dict()),
    )
    first = preview.passes[0].segments[0].points[0].tool_center_point
    first_setup = _setup_point(first, inputs.context.machining_zone.wcs)
    axis = Vector3(0.0, 0.0, 1.0)
    builder.set_initial_pose(Pose(Point3(first_setup.x, first_setup.y, inputs.parameters.clearance_z_mm, LengthUnit.MM), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    builder.marker(
        "z_level.safety.contract",
        "Validated Z-Level safety contract" if safety_report is not None else "Z-Level candidate awaiting safety validation",
        metadata=(
            ("strategy_key", Z_LEVEL_FINISHING_STRATEGY_KEY),
            ("algorithm_version", str(Z_LEVEL_FINISHING_ALGORITHM_VERSION)),
            ("strategy_payload_version", str(inputs.operation.parameters.strategy_version)),
            ("safety_status", safety_report.status.value if safety_report is not None else "candidate"),
            ("safety_report_fingerprint", safety_report.fingerprint.digest if safety_report is not None else "pending"),
            ("machine_ready_clearance_verified", "false"),
        ),
        provenance="z_level.safety.contract",
    )
    feed = FeedRate(inputs.parameters.feed_rate_mm_per_minute, FeedUnit.MM_PER_MINUTE)
    tolerance = inputs.parameters.tolerance_mm
    for level_pass in preview.passes:
        for contour in level_pass.segments:
            _checkpoint(cancellation)
            points = tuple(_setup_point(item.tool_center_point, inputs.context.machining_zone.wcs) for item in contour.points)
            start, end = points[0], points[-1]
            prefix = f"parallel.pass.{contour.pass_index}.segment.{contour.segment_index}"
            builder.marker(
                "z_level.contour",
                "Z-Level contour theo tool-center implicit field",
                metadata=(
                    ("level_index", str(contour.pass_index)),
                    ("level", f"{contour.level:.12g}"),
                    ("region_id", contour.region_id),
                    ("loop_type", contour.loop_type.value),
                    ("orientation", contour.orientation.value),
                ),
                provenance=f"{prefix}.marker",
            )
            clearance = Pose(Point3(start.x, start.y, inputs.parameters.clearance_z_mm, LengthUnit.MM), axis)
            retract = Pose(Point3(start.x, start.y, inputs.parameters.retract_z_mm, LengthUnit.MM), axis)
            contact = Pose(start, axis)
            _rapid_if_needed(builder, clearance, MotionClass.NON_CUTTING, f"{prefix}.position.clearance", tolerance)
            _rapid_if_needed(builder, retract, MotionClass.RETRACT, f"{prefix}.position.retract", tolerance)
            _linear_if_needed(builder, contact, feed, MotionClass.LINK, f"{prefix}.approach", tolerance)
            for point_index, point in enumerate(points[1:], start=1):
                evidence = contour.points[point_index]
                builder.linear_to(
                    Pose(point, axis),
                    feed,
                    motion_class=MotionClass.CUTTING,
                    engagement=(
                        ("strategy", Z_LEVEL_FINISHING_STRATEGY_KEY),
                        ("level_index", str(contour.pass_index)),
                        ("region_id", contour.region_id),
                        ("source_surface_ids", ",".join(map(str, evidence.source_surface_ids))),
                        ("contact_level", f"{evidence.requested_level:.12g}"),
                    ),
                    provenance=f"{prefix}.cut.{point_index}",
                )
            if contour.closed and not _same_point(points[-1], points[0], tolerance):
                builder.linear_to(Pose(points[0], axis), feed, motion_class=MotionClass.CUTTING, provenance=f"{prefix}.cut.close")
            _linear_if_needed(builder, Pose(Point3(end.x, end.y, inputs.parameters.retract_z_mm, LengthUnit.MM), axis), feed, MotionClass.RETRACT, f"{prefix}.retract", tolerance)
            _rapid_if_needed(builder, Pose(Point3(end.x, end.y, inputs.parameters.clearance_z_mm, LengthUnit.MM), axis), MotionClass.RETRACT, f"{prefix}.clearance", tolerance)
    return builder.finalize()


def z_level_artifact_has_safe_contract(artifact: ToolpathArtifact) -> bool:
    """Return true only for a complete artifact with the current SAFE marker."""
    if artifact.completion_status.value != "complete":
        return False
    for event in artifact.events:
        if getattr(event, "semantic_key", None) != "z_level.safety.contract":
            continue
        metadata = dict(getattr(event, "metadata", ()))
        return (
            metadata.get("strategy_key") == Z_LEVEL_FINISHING_STRATEGY_KEY
            and metadata.get("algorithm_version") == str(Z_LEVEL_FINISHING_ALGORITHM_VERSION)
            and metadata.get("safety_status") == ParallelSafetyStatus.SAFE.value
            and metadata.get("machine_ready_clearance_verified") == "false"
            and metadata.get("safety_report_fingerprint", "pending") != "pending"
        )
    return False


def _lifecycle(
    inputs: ZLevelFinishingInputs,
    status: ZLevelArtifactStatus,
    *,
    artifact: ToolpathArtifact | None = None,
    safety: ParallelSafetyReport | None = None,
    superseded: bool = False,
) -> ZLevelArtifactLifecycle:
    return ZLevelArtifactLifecycle(
        status,
        inputs.operation.revision,
        inputs.input_fingerprint,
        artifact.artifact_fingerprint if artifact is not None else None,
        safety.status.value if safety is not None else "unknown",
        safety.fingerprint.digest if safety is not None else None,
        superseded,
    )


def _setup_point(point: Point3, wcs: WcsFrame) -> Point3:
    delta = Vector3(point.x - wcs.origin.x, point.y - wcs.origin.y, point.z - wcs.origin.z)
    return Point3(delta.dot(wcs.x_axis), delta.dot(wcs.y_axis), delta.dot(wcs.z_axis), LengthUnit.MM)


def _same_point(first: Point3, second: Point3, tolerance: float) -> bool:
    return math.dist((first.x, first.y, first.z), (second.x, second.y, second.z)) <= tolerance


def _rapid_if_needed(builder: ToolpathBuilder, target: Pose, motion_class: MotionClass, provenance: str, tolerance: float) -> None:
    current = builder.current_pose
    if current is None or not _same_point(current.position, target.position, tolerance):
        builder.rapid_to(target, motion_class=motion_class, provenance=provenance)


def _linear_if_needed(builder: ToolpathBuilder, target: Pose, feed: FeedRate, motion_class: MotionClass, provenance: str, tolerance: float) -> None:
    current = builder.current_pose
    if current is None or not _same_point(current.position, target.position, tolerance):
        builder.linear_to(target, feed, motion_class=motion_class, provenance=provenance)


def _emit(callback: ProgressCallback | None, operation_id: OperationId, phase: ZLevelProgressPhase, processed: int, total: int) -> None:
    if callback is not None:
        callback(ZLevelProgress(operation_id, phase, processed, total))


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise ZLevelFinishingError(DiagnosticCode.Z_LEVEL_CANCELLED, "Tính toán Z-Level đã bị hủy.")


def _failed_operation(original: Operation, computing: ZLevelFinishingInputs | None, token: ComputationToken | None, diagnostic: ValidationDiagnostic) -> Operation:
    if computing is None or token is None:
        return replace(original, diagnostics=(*original.diagnostics, diagnostic))
    state, accepted = computing.operation.artifact_state.fail(token, (diagnostic,))
    if not accepted:
        return replace(computing.operation, diagnostics=(*computing.operation.diagnostics, diagnostic))
    return replace(computing.operation, artifact_state=state, diagnostics=(*computing.operation.diagnostics, diagnostic))
