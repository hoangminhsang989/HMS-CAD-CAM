"""Validation, generation, progress and atomic publish for Parallel Finishing."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid5

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.models import Cam3DSafeTransitionPolicy
from hms_cadcam.cam.cam3d.parallel.geometry import (
    build_machining_frame,
    calculate_region_bounds,
    intersect_parallel_passes,
    plan_pass_positions,
)
from hms_cadcam.cam.cam3d.parallel.models import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ContactResolver,
    ParallelFinishingError,
    ParallelFinishingParameters,
    ParallelPreview,
    ParallelProgress,
    ParallelProgressPhase,
    ParallelStatistics,
    ParallelNormalSource,
    ProgressCallback,
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
_ARTIFACT_NAMESPACE = UUID("cc68421e-c09e-4c55-87da-83bf4afe5369")


@dataclass(frozen=True, slots=True)
class ParallelFinishingInputs:
    """Validated immutable snapshots safe to pass to a background worker."""

    operation: Operation
    context: Cam3DCalculationContext
    parameters: ParallelFinishingParameters
    assembly: ToolAssembly
    tool: ToolDefinition
    tool_radius: float
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class ParallelFinishingCandidate:
    """Complete in-memory result; partial paths are never exposed."""

    artifact: ToolpathArtifact
    preview: ParallelPreview


@dataclass(frozen=True, slots=True)
class ParallelFinishingComputeResult:
    """Application-boundary result after optional atomic artifact publishing."""

    operation: Operation
    artifact: ToolpathArtifact | None
    preview: ParallelPreview | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    metadata: ToolpathArtifactMetadata | None = None


class ParallelFinishingGenerator:
    """Controller-neutral ball-end strategy built on Stage 8A.1 mesh/context."""

    def resolve_inputs(
        self,
        operation: Operation,
        context: Cam3DCalculationContext,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
    ) -> ParallelFinishingInputs:
        """Validate current domain snapshots and calculate a deterministic input hash."""
        try:
            parameters = ParallelFinishingParameters.from_operation_parameters(
                operation.parameters
            )
        except Exception as error:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_PARAMETERS,
                str(error) or "Parallel operation parameters are invalid.",
            ) from error
        if operation.family is not OperationFamily.MILLING:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_PARAMETERS,
                "Parallel Finishing must be a MILLING operation.",
            )
        if not operation.enabled:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_PARAMETERS,
                "Parallel Finishing operation is disabled.",
            )
        if not isinstance(context, Cam3DCalculationContext):
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_NO_GEOMETRY,
                "Parallel Finishing requires a current CAM 3D context.",
            )
        zone = context.machining_zone
        if parameters.zone_id != zone.zone_id:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_NO_GEOMETRY,
                "Parallel operation references another machining zone.",
            )
        if operation.setup_id != context.setup_id or context.setup_id != zone.setup_id:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_WORKPLANE,
                "Parallel operation/context use different Setups.",
            )
        if context.geometry_snapshot.zone.fingerprint != zone.fingerprint:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_NO_GEOMETRY,
                "Parallel machining-zone snapshot is stale.",
            )
        selected = {
            item.geometry.reference_id for item in zone.part_surfaces.selection.surfaces
        }
        persisted = {
            item.reference.reference_id
            for item in operation.geometry_inputs
            if item.role is GeometryInputRole.DRIVE_GEOMETRY
        }
        if not selected or not persisted:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_NO_GEOMETRY,
                "Parallel Finishing requires one or more persisted machining faces.",
            )
        if persisted != selected:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_MISSING_FACE,
                "Persisted Parallel machining faces do not match the current zone.",
            )
        protected = tuple(
            item
            for surface_set in (zone.check_surfaces, zone.fixture_surfaces)
            if surface_set is not None
            for item in surface_set.selection.surfaces
        )
        if protected:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_UNSUPPORTED_PROTECTIVE_GEOMETRY,
                "Stage 8A.2.1 cannot verify Check/Fixture surface clearance.",
            )
        if any(
            value != 0.0
            for value in (
                zone.allowance.axial,
                zone.allowance.check_surface_clearance,
                zone.allowance.boundary_offset,
            )
        ):
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_UNSUPPORTED_ALLOWANCE,
                "Stage 8A.2.1 supports part-normal surface allowance only.",
            )
        tool_status = operation.tool_assembly.assess(assembly)
        if tool_status is ToolReferenceStatus.MISSING:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "Parallel Tool Assembly is missing.",
            )
        if tool_status is not ToolReferenceStatus.VALID or assembly is None:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "Parallel Tool Assembly is stale or uses an incompatible unit.",
            )
        if tool is None or tool.tool_id != assembly.tool_id:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "Parallel Tool Definition is missing.",
            )
        if (
            tool.revision != assembly.expected_tool_revision
            or tool.content_fingerprint != assembly.expected_tool_fingerprint
            or tool.unit is not LengthUnit.MM
            or assembly.unit is not LengthUnit.MM
        ):
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "Parallel tool snapshot is stale or not expressed in MM.",
            )
        if tool.family is not ToolFamily.BALL_END_MILL or not isinstance(
            tool.cutting_geometry, BallEndGeometry
        ):
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_UNSUPPORTED_TOOL_GEOMETRY,
                "UNSUPPORTED_TOOL_GEOMETRY: Stage 8A.2.1 supports ball-end mills only.",
            )
        diameter = tool.cutting_geometry.diameter
        if diameter.unit is not LengthUnit.MM or diameter.value <= 0.0:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "Parallel ball-end diameter is invalid.",
            )
        assembly_fingerprint = ContentFingerprint.from_payload(assembly.to_dict())
        if context.tool_definition_fingerprint != tool.content_fingerprint:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "CAM 3D context uses a stale Tool Definition.",
            )
        if context.tool_assembly_fingerprint not in {
            assembly_fingerprint,
            assembly.content_fingerprint,
        }:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOOL,
                "CAM 3D context uses a stale Tool Assembly.",
            )
        tolerance = context.tolerance_policy.contact_tolerance
        if tolerance <= 0.0 or parameters.maximum_segment_length_mm < tolerance:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_TOLERANCE,
                "Parallel discretization settings conflict with CAM 3D tolerance.",
            )
        safe = context.safe_motion_policy
        if (
            safe.clearance_z is None
            or safe.retract_z is None
            or safe.clearance_z < safe.retract_z
            or safe.link_clearance < 0.0
            or safe.transition_policy
            is not Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID
        ):
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_INVALID_CLEARANCE,
                "Parallel clearance/retract values are missing or unsafe.",
            )
        input_fingerprint = DependencyFingerprint.from_payload(
            {
                "algorithm": "hms_parallel_finishing_mesh_plane",
                "algorithm_version": PARALLEL_FINISHING_ALGORITHM_VERSION,
                "operation": {
                    "id": str(operation.operation_id),
                    "revision": operation.revision.to_dict(),
                    "enabled": operation.enabled,
                    "parameters": operation.parameters.to_dict(),
                    "geometry_inputs": [item.to_dict() for item in operation.geometry_inputs],
                },
                "context": context.fingerprint.to_dict(),
                "tool": tool.to_dict(),
                "assembly": assembly.to_dict(),
            }
        )
        return ParallelFinishingInputs(
            operation,
            context,
            parameters,
            assembly,
            tool,
            diameter.value / 2.0,
            input_fingerprint,
        )

    def begin(
        self, inputs: ParallelFinishingInputs
    ) -> tuple[ParallelFinishingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(
            inputs, operation=replace(inputs.operation, artifact_state=state)
        ), token

    def generate(
        self,
        inputs: ParallelFinishingInputs,
        *,
        cancellation: Callable[[], bool] | None = None,
        progress: ProgressCallback | None = None,
        contact_resolver: ContactResolver | None = None,
    ) -> ParallelFinishingCandidate:
        """Generate one complete candidate without mutating project or source geometry."""
        operation = inputs.operation
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_ARTIFACT_GENERATION_FAILED,
                "Parallel generation requires a current computation token.",
            )
        builder: ToolpathBuilder | None = None
        try:
            _emit(progress, operation.operation_id, ParallelProgressPhase.VALIDATION, 0, 1)
            _checkpoint(cancellation)
            _emit(progress, operation.operation_id, ParallelProgressPhase.VALIDATION, 1, 1)
            _emit(progress, operation.operation_id, ParallelProgressPhase.FRAME_BOUNDS, 0, 2)
            frame = build_machining_frame(
                inputs.context.machining_zone,
                inputs.parameters.direction_angle_degrees,
                epsilon=inputs.context.tolerance_policy.calculation_epsilon,
            )
            _emit(progress, operation.operation_id, ParallelProgressPhase.FRAME_BOUNDS, 1, 2)
            bounds = calculate_region_bounds(
                inputs.context.calculation_mesh,
                frame,
                inputs.context.machining_zone,
                padding=inputs.context.tolerance_policy.contact_tolerance,
            )
            _emit(progress, operation.operation_id, ParallelProgressPhase.FRAME_BOUNDS, 2, 2)
            _checkpoint(cancellation)
            _emit(progress, operation.operation_id, ParallelProgressPhase.PASS_GENERATION, 0, 1)
            positions = plan_pass_positions(
                bounds,
                inputs.parameters.stepover_mm,
                tolerance=inputs.context.tolerance_policy.contact_tolerance,
            )
            _emit(progress, operation.operation_id, ParallelProgressPhase.PASS_GENERATION, 1, 1)
            _emit(
                progress,
                operation.operation_id,
                ParallelProgressPhase.INTERSECTION,
                0,
                len(positions),
            )
            intersection = intersect_parallel_passes(
                inputs.context,
                frame,
                bounds,
                positions,
                inputs.parameters,
                tool_radius=inputs.tool_radius,
                cancellation=cancellation,
                pass_progress=lambda processed, total: _emit(
                    progress,
                    operation.operation_id,
                    ParallelProgressPhase.INTERSECTION,
                    processed,
                    total,
                ),
                discretization_progress=lambda processed, total: _emit(
                    progress,
                    operation.operation_id,
                    ParallelProgressPhase.DISCRETIZATION,
                    processed,
                    total,
                ),
                contact_resolver=contact_resolver,
            )
            _emit(progress, operation.operation_id, ParallelProgressPhase.ORDERING_LINKING, 0, 1)
            _validate_tool_center_clearance(inputs.context, intersection.passes)
            _emit(progress, operation.operation_id, ParallelProgressPhase.ORDERING_LINKING, 1, 1)
            _checkpoint(cancellation)
            _emit(progress, operation.operation_id, ParallelProgressPhase.IR_BUILD, 0, 1)
            builder = _builder(inputs, token)
            artifact = _build_toolpath(
                builder,
                inputs,
                intersection.passes,
                cancellation=cancellation,
            )
            _emit(progress, operation.operation_id, ParallelProgressPhase.IR_BUILD, 1, 1)
            statistics = ParallelStatistics(
                len(positions),
                sum(bool(item.segments) for item in intersection.passes),
                sum(len(item.segments) for item in intersection.passes),
                sum(
                    len(segment.points)
                    for item in intersection.passes
                    for segment in item.segments
                ),
                len(artifact.events),
            )
            preview = ParallelPreview(
                frame,
                bounds,
                positions,
                intersection.passes,
                intersection.raw_segment_count,
                intersection.clipped_segment_count,
                statistics,
            )
            _checkpoint(cancellation)
            _emit(progress, operation.operation_id, ParallelProgressPhase.FINALIZATION, 1, 1)
            return ParallelFinishingCandidate(artifact, preview)
        except ParallelFinishingError:
            if builder is not None:
                _abort_open_builder(builder)
            raise
        except Exception as error:
            if builder is not None:
                _abort_open_builder(builder)
            logger.exception("Unexpected Parallel Finishing generation failure")
            raise ParallelFinishingError(
                DiagnosticCode.PARALLEL_ARTIFACT_GENERATION_FAILED,
                str(error) or "Parallel Finishing artifact generation failed.",
            ) from error


def calculate_and_publish_parallel_finishing(
    project_root: Path,
    operation: Operation,
    context: Cam3DCalculationContext,
    *,
    assembly: ToolAssembly | None,
    tool: ToolDefinition | None,
    artifact_store: ToolpathArtifactStore | None = None,
    cancellation: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
    current_operation: Callable[[], Operation] | None = None,
    contact_resolver: ContactResolver | None = None,
) -> ParallelFinishingComputeResult:
    """Compute and atomically publish a complete artifact after final stale checks."""
    generator = ParallelFinishingGenerator()
    computing: ParallelFinishingInputs | None = None
    token: ComputationToken | None = None
    try:
        inputs = generator.resolve_inputs(
            operation, context, assembly=assembly, tool=tool
        )
        computing, token = generator.begin(inputs)
        candidate = generator.generate(
            computing,
            cancellation=cancellation,
            progress=progress,
            contact_resolver=contact_resolver,
        )
        _checkpoint(cancellation)
        live = current_operation() if current_operation is not None else computing.operation
        published = publish_toolpath(
            live,
            candidate.artifact,
            token,
            computing.input_fingerprint,
        )
        if not published.accepted or published.artifact is None:
            diagnostic = ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                DiagnosticCode.PARALLEL_STALE_RESULT,
                "Parallel result became stale before artifact publish.",
            )
            return ParallelFinishingComputeResult(
                published.operation, None, None, False, (diagnostic,)
            )
        _checkpoint(cancellation)
        store = artifact_store or ToolpathArtifactStore()
        metadata = store.publish(project_root, published.artifact)
        limitations = [
            ValidationDiagnostic(
                DiagnosticSeverity.WARNING,
                DiagnosticCode.PARALLEL_FOUNDATION_LIMITATION,
                "Foundation result is not universally gouge- or collision-certified.",
            )
        ]
        if any(
            point.normal_source is ParallelNormalSource.MESH_FACET
            for pass_value in candidate.preview.passes
            for segment in pass_value.segments
            for point in segment.points
        ):
            limitations.append(
                ValidationDiagnostic(
                    DiagnosticSeverity.WARNING,
                    DiagnosticCode.PARALLEL_MESH_NORMAL_APPROXIMATION,
                    "Tool-center normals use mesh facets, not original BRep differentials.",
                )
            )
        warned_operation = replace(
            published.operation,
            diagnostics=(*published.operation.diagnostics, *limitations),
        )
        return ParallelFinishingComputeResult(
            warned_operation,
            published.artifact,
            candidate.preview,
            True,
            tuple(limitations),
            metadata=metadata,
        )
    except ParallelFinishingError as error:
        failed = _failed_operation(operation, computing, token, error.diagnostic)
        return ParallelFinishingComputeResult(
            failed, None, None, False, (error.diagnostic,)
        )
    except ToolpathArtifactStoreError:
        diagnostic = ValidationDiagnostic(
            DiagnosticSeverity.ERROR,
            DiagnosticCode.PARALLEL_ARTIFACT_GENERATION_FAILED,
            "Parallel toolpath file could not be published atomically.",
        )
        failed = _failed_operation(operation, computing, token, diagnostic)
        return ParallelFinishingComputeResult(failed, None, None, False, (diagnostic,))


def _builder(inputs: ParallelFinishingInputs, token: ComputationToken) -> ToolpathBuilder:
    operation = inputs.operation
    artifact_uuid = uuid5(
        _ARTIFACT_NAMESPACE,
        f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}",
    )
    return ToolpathBuilder(
        artifact_id=ToolpathArtifactId(artifact_uuid),
        operation_id=operation.operation_id,
        operation_revision=operation.revision,
        computation_token=token,
        input_fingerprint=inputs.input_fingerprint,
        unit=LengthUnit.MM,
        setup_id=inputs.context.setup_id,
        setup_revision=inputs.context.machining_zone.setup_revision,
        wcs_fingerprint=ContentFingerprint.from_payload(
            inputs.context.machining_zone.wcs.to_dict()
        ),
        tool_assembly_id=inputs.assembly.assembly_id,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(
            inputs.assembly.to_dict()
        ),
    )


def _build_toolpath(
    builder: ToolpathBuilder,
    inputs: ParallelFinishingInputs,
    passes,
    *,
    cancellation: Callable[[], bool] | None,
) -> ToolpathArtifact:
    safe = inputs.context.safe_motion_policy
    assert safe.clearance_z is not None and safe.retract_z is not None
    first_segment = next(
        segment for item in passes for segment in item.segments
    )
    first_point = _setup_point(
        first_segment.points[0].tool_center_point,
        inputs.context.machining_zone.wcs,
    )
    tool_axis = Vector3(0.0, 0.0, 1.0)
    builder.set_initial_pose(
        Pose(
            Point3(first_point.x, first_point.y, safe.clearance_z, LengthUnit.MM),
            tool_axis,
        )
    )
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    feed = FeedRate(inputs.parameters.feed_rate_mm_per_minute, FeedUnit.MM_PER_MINUTE)
    tolerance = inputs.context.tolerance_policy.contact_tolerance
    for pass_value in passes:
        for segment in pass_value.segments:
            _checkpoint(cancellation)
            setup_points = tuple(
                _setup_point(point.tool_center_point, inputs.context.machining_zone.wcs)
                for point in segment.points
            )
            start = setup_points[0]
            prefix = f"parallel.pass.{segment.pass_index}.segment.{segment.segment_index}"
            builder.marker(
                "parallel.segment",
                "Conservative retract-linked ball-center segment",
                metadata=(
                    ("contact_semantics", "ball_center_from_mesh_contact"),
                    ("pass_index", str(segment.pass_index)),
                    ("segment_index", str(segment.segment_index)),
                ),
                provenance=f"{prefix}.marker",
            )
            clearance = Pose(
                Point3(start.x, start.y, safe.clearance_z, LengthUnit.MM), tool_axis
            )
            _rapid_if_needed(
                builder,
                clearance,
                MotionClass.NON_CUTTING,
                f"{prefix}.position.clearance",
                tolerance,
            )
            retract = Pose(
                Point3(start.x, start.y, safe.retract_z, LengthUnit.MM), tool_axis
            )
            _rapid_if_needed(
                builder,
                retract,
                MotionClass.RETRACT,
                f"{prefix}.position.retract",
                tolerance,
            )
            approach_z = min(safe.retract_z, start.z + safe.approach_distance)
            approach = Pose(
                Point3(start.x, start.y, approach_z, LengthUnit.MM), tool_axis
            )
            _linear_if_needed(
                builder,
                approach,
                feed,
                MotionClass.LINK,
                f"{prefix}.approach",
                tolerance,
            )
            _linear_if_needed(
                builder,
                Pose(start, tool_axis),
                feed,
                MotionClass.LINK,
                f"{prefix}.contact",
                tolerance,
            )
            for point_index, point in enumerate(setup_points[1:], start=1):
                sources = ",".join(
                    str(item)
                    for item in segment.points[point_index].source_surface_ids
                )
                builder.linear_to(
                    Pose(point, tool_axis),
                    feed,
                    motion_class=MotionClass.CUTTING,
                    engagement=(
                        ("contact_semantics", "ball_center_from_mesh_contact"),
                        ("pass_index", str(segment.pass_index)),
                        ("segment_index", str(segment.segment_index)),
                        ("source_surface_ids", sources),
                    ),
                    provenance=f"{prefix}.cut.{point_index}",
                )
            end = setup_points[-1]
            retract_end = Pose(
                Point3(end.x, end.y, safe.retract_z, LengthUnit.MM), tool_axis
            )
            _linear_if_needed(
                builder,
                retract_end,
                feed,
                MotionClass.RETRACT,
                f"{prefix}.retract",
                tolerance,
            )
            clearance_end = Pose(
                Point3(end.x, end.y, safe.clearance_z, LengthUnit.MM), tool_axis
            )
            _rapid_if_needed(
                builder,
                clearance_end,
                MotionClass.RETRACT,
                f"{prefix}.clearance",
                tolerance,
            )
    return builder.finalize()


def _validate_tool_center_clearance(context: Cam3DCalculationContext, passes) -> None:
    safe = context.safe_motion_policy
    assert safe.clearance_z is not None and safe.retract_z is not None
    center_heights = tuple(
        _setup_point(point.tool_center_point, context.machining_zone.wcs).z
        for item in passes
        for segment in item.segments
        for point in segment.points
    )
    if not center_heights:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_ALL_PASSES_EMPTY,
            "Parallel tool-center result is empty.",
        )
    required = max(center_heights) + context.tolerance_policy.contact_tolerance
    if safe.retract_z <= required or safe.clearance_z < safe.retract_z:
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_INVALID_CLEARANCE,
            "Parallel retract/clearance must be above every ball-center sample.",
        )


def _setup_point(point: Point3, wcs: WcsFrame) -> Point3:
    delta = Vector3(
        point.x - wcs.origin.x,
        point.y - wcs.origin.y,
        point.z - wcs.origin.z,
    )
    return Point3(
        delta.dot(wcs.x_axis),
        delta.dot(wcs.y_axis),
        delta.dot(wcs.z_axis),
        LengthUnit.MM,
    )


def _same_point(first: Point3, second: Point3, tolerance: float) -> bool:
    return math.dist(
        (first.x, first.y, first.z), (second.x, second.y, second.z)
    ) <= tolerance


def _rapid_if_needed(
    builder: ToolpathBuilder,
    target: Pose,
    motion_class: MotionClass,
    provenance: str,
    tolerance: float,
) -> None:
    current = builder.current_pose
    if current is None or not _same_point(current.position, target.position, tolerance):
        builder.rapid_to(target, motion_class=motion_class, provenance=provenance)


def _linear_if_needed(
    builder: ToolpathBuilder,
    target: Pose,
    feed: FeedRate,
    motion_class: MotionClass,
    provenance: str,
    tolerance: float,
) -> None:
    current = builder.current_pose
    if current is None or not _same_point(current.position, target.position, tolerance):
        builder.linear_to(
            target, feed, motion_class=motion_class, provenance=provenance
        )


def _emit(
    callback: ProgressCallback | None,
    operation_id: OperationId,
    phase: ParallelProgressPhase,
    processed: int,
    total: int,
) -> None:
    if callback is not None:
        callback(ParallelProgress(operation_id, phase, processed, total))


def _checkpoint(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise ParallelFinishingError(
            DiagnosticCode.PARALLEL_CANCELLED,
            "Parallel Finishing calculation was cancelled.",
        )


def _abort_open_builder(builder: ToolpathBuilder) -> None:
    try:
        builder.abort()
    except Exception:
        logger.debug("Parallel builder was already terminal", exc_info=True)


def _failed_operation(
    original: Operation,
    computing: ParallelFinishingInputs | None,
    token: ComputationToken | None,
    diagnostic: ValidationDiagnostic,
) -> Operation:
    if computing is None or token is None:
        return replace(original, diagnostics=(*original.diagnostics, diagnostic))
    operation = computing.operation
    state, accepted = operation.artifact_state.fail(token, (diagnostic,))
    if not accepted:
        return replace(operation, diagnostics=(*operation.diagnostics, diagnostic))
    return replace(
        operation,
        artifact_state=state,
        diagnostics=(*operation.diagnostics, diagnostic),
    )
