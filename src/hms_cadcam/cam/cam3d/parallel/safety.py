"""Complete fail-closed safety validation for Parallel Finishing candidates."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from hms_cadcam.cam.cam3d.context import Cam3DCalculationContext
from hms_cadcam.cam.cam3d.models import MachiningBoundary3DKind
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.ids import GeometryReferenceId
from hms_cadcam.cam.domain.operation import DiagnosticCode, DiagnosticSeverity, Operation
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.tooling import (
    BallEndGeometry,
    HolderDefinition,
    ToolAssembly,
    ToolDefinition,
)
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.simulation.envelope import (
    EnvelopePrimitiveKind,
    ToolEnvelope,
    build_tool_envelope,
)
from hms_cadcam.cam.toolpath.events import (
    ArcMove,
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
)
from hms_cadcam.cam.toolpath.model import ToolpathArtifact, ToolpathCompletionStatus

from .collision import (
    ParallelCollisionPrimitive,
    ParallelCollisionTriangle,
    ParallelPrimitiveKind,
    closest_point_on_triangle,
    segment_triangle_distance,
    swept_axis_triangle_distance,
    swept_primitive_bounds,
    triangle_bounds,
)
from .models import PARALLEL_FINISHING_ALGORITHM_VERSION, ParallelPathPoint, ParallelPreview
from .safety_models import (
    ParallelGeometrySource,
    ParallelSafetyDiagnostic,
    ParallelSafetyMotion,
    ParallelSafetyPolicy,
    ParallelSafetyReport,
    ParallelSafetyStatistics,
    ParallelSafetyStatus,
    ParallelToolComponent,
    aggregate_parallel_safety_diagnostics,
    parallel_clearance_is_satisfied,
)

_PROVENANCE = re.compile(
    r"(?:parallel|z_level)\.pass\.(?P<pass>\d+)\.segment\.(?P<segment>\d+)\.(?P<action>.+)"
)


@dataclass(frozen=True, slots=True)
class ParallelToolAssemblySafetyModel:
    """Safety-ready primitives derived only from immutable tooling snapshots."""

    primitives: tuple[ParallelCollisionPrimitive, ...]
    ball_radius_mm: float
    holder_state: str
    fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        if not self.primitives or any(
            not isinstance(item, ParallelCollisionPrimitive) for item in self.primitives
        ):
            raise CamValidationError("Parallel safety tool primitives are invalid")
        if not math.isfinite(self.ball_radius_mm) or self.ball_radius_mm <= 0.0:
            raise CamValidationError("Parallel safety ball radius is invalid")
        if self.holder_state not in {"geometry_faithful", "declared_absent"}:
            raise CamValidationError("Parallel safety holder state is invalid")
        if not isinstance(self.fingerprint, ContentFingerprint):
            raise CamValidationError("Parallel safety tool fingerprint is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "ball_radius_mm": self.ball_radius_mm,
            "holder_state": self.holder_state,
            "primitives": [item.to_dict() for item in self.primitives],
            "fingerprint": self.fingerprint.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _Motion:
    event_index: int
    start: Point3
    end: Point3
    motion: ParallelSafetyMotion
    provenance: str
    pass_index: int | None
    segment_index: int | None
    action: str
    expected_start: ParallelPathPoint | None
    expected_end: ParallelPathPoint | None


@dataclass(slots=True)
class _Counters:
    motions: int = 0
    broad_scans: int = 0
    candidates: int = 0
    narrow: int = 0
    subdivisions: int = 0


def build_parallel_safety_policy(
    context: Cam3DCalculationContext,
    *,
    tool_radius_mm: float,
) -> ParallelSafetyPolicy:
    """Derive internal margins without changing the persisted strategy payload."""
    if not isinstance(context, Cam3DCalculationContext):
        raise CamValidationError("Parallel safety context is invalid")
    if not math.isfinite(tool_radius_mm) or tool_radius_mm <= 0.0:
        raise CamValidationError("Parallel safety tool radius is invalid")
    tolerance = context.tolerance_policy
    numeric = tolerance.calculation_epsilon
    contact = tolerance.contact_tolerance
    gouge = max(numeric * 4.0, tolerance.chordal_tolerance)
    clearance = max(contact, numeric * 8.0)
    maximum_step = max(
        contact,
        min(tool_radius_mm, max(1.0, tolerance.chordal_tolerance * 4.0)),
    )
    return ParallelSafetyPolicy(
        numeric,
        contact,
        gouge,
        clearance,
        clearance,
        clearance,
        max(contact, numeric * 4.0),
        maximum_step,
    )


def build_parallel_tool_assembly_model(
    *,
    tool: ToolDefinition,
    assembly: ToolAssembly,
    holder: HolderDefinition | None,
) -> ParallelToolAssemblySafetyModel:
    """Build ball/cutting/shank/holder primitives from authoritative geometry."""
    if not isinstance(tool.cutting_geometry, BallEndGeometry):
        raise CamValidationError("Parallel safety requires ball-end geometry")
    if (
        tool.tool_id != assembly.tool_id
        or tool.revision != assembly.expected_tool_revision
        or tool.content_fingerprint != assembly.expected_tool_fingerprint
        or tool.unit is not assembly.unit
    ):
        raise CamValidationError("Parallel safety tool snapshot is stale or mismatched")
    if assembly.holder_id is None and holder is not None:
        raise CamValidationError("Parallel safety received an unreferenced holder snapshot")
    if assembly.holder_id is not None and (
        holder is None
        or holder.holder_id != assembly.holder_id
        or holder.revision != assembly.expected_holder_revision
        or holder.content_fingerprint != assembly.expected_holder_fingerprint
        or holder.unit is not assembly.unit
    ):
        raise CamValidationError("Parallel safety holder snapshot is missing or mismatched")
    radius = tool.cutting_geometry.diameter.value / 2.0
    envelope = build_tool_envelope(
        tool=tool,
        assembly=assembly,
        holder=holder,
        require_holder=False,
    )
    primitives = _collision_primitives(envelope, radius)
    holder_state = "geometry_faithful" if assembly.holder_id is not None else "declared_absent"
    payload = {
        "algorithm_version": PARALLEL_FINISHING_ALGORITHM_VERSION,
        "tool": tool.to_dict(),
        "assembly": assembly.to_dict(),
        "holder": holder.to_dict() if holder is not None else None,
        "primitives": [item.to_dict() for item in primitives],
        "holder_state": holder_state,
    }
    return ParallelToolAssemblySafetyModel(
        primitives,
        radius,
        holder_state,
        ContentFingerprint.from_payload(payload),
    )


def validate_parallel_candidate_safety(
    *,
    operation: Operation,
    context: Cam3DCalculationContext,
    tool: ToolDefinition,
    assembly: ToolAssembly,
    holder: HolderDefinition | None,
    artifact: ToolpathArtifact,
    preview: ParallelPreview,
    cancellation: Callable[[], bool] | None = None,
    policy: ParallelSafetyPolicy | None = None,
) -> ParallelSafetyReport:
    """Validate topology, every full motion sweep and the complete tool assembly."""
    calculation_id = str(artifact.computation_token.value)
    diagnostics: list[ParallelSafetyDiagnostic] = []
    collision_diagnostics: tuple[ParallelSafetyDiagnostic, ...] = ()
    counters = _Counters()
    active_policy: ParallelSafetyPolicy | None = policy
    triangles: tuple[ParallelCollisionTriangle, ...] = ()
    tool_model: ParallelToolAssemblySafetyModel | None = None
    try:
        if artifact.completion_status is not ToolpathCompletionStatus.COMPLETE:
            diagnostics.append(
                _diagnostic(
                    operation,
                    ParallelSafetyStatus.UNKNOWN,
                    DiagnosticCode.PARALLEL_SAFETY_STALE_ARTIFACT,
                    "Parallel candidate is not a complete Toolpath IR artifact.",
                )
            )
            return _report(
                operation,
                calculation_id,
                active_policy,
                diagnostics,
                counters,
                (),
                assembly,
                tool_model,
                holder,
            )
        _cancel(cancellation)
        tool_model = build_parallel_tool_assembly_model(
            tool=tool,
            assembly=assembly,
            holder=holder,
        )
        active_policy = active_policy or build_parallel_safety_policy(
            context, tool_radius_mm=tool_model.ball_radius_mm
        )
        triangles = _collision_triangles(context)
        declared_sources = {
            item.geometry.reference_id for item in context.machining_zone.all_surfaces()
        }
        meshed_sources = {item.face_id for item in triangles}
        missing_sources = tuple(sorted(declared_sources - meshed_sources, key=str))
        if missing_sources:
            diagnostics.append(
                _diagnostic(
                    operation,
                    ParallelSafetyStatus.UNKNOWN,
                    DiagnosticCode.PARALLEL_SAFETY_UNKNOWN,
                    "Declared machining/protected geometry is missing from the safety mesh.",
                    debug=(("missing_face_ids", ",".join(map(str, missing_sources))),),
                )
            )
            return _report(
                operation,
                calculation_id,
                active_policy,
                diagnostics,
                counters,
                triangles,
                assembly,
                tool_model,
                holder,
            )
        if len(triangles) > active_policy.maximum_protected_triangles:
            diagnostics.append(
                _limit_diagnostic(
                    operation,
                    "Protected triangle limit exceeded before broad phase.",
                    active_policy,
                )
            )
            return _report(
                operation,
                calculation_id,
                active_policy,
                diagnostics,
                counters,
                triangles,
                assembly,
                tool_model,
                holder,
            )
        diagnostics.extend(
            _topology_findings(operation, preview, tool_model.ball_radius_mm, active_policy)
        )
        diagnostics.extend(
            _clearance_findings(
                operation,
                context,
                active_policy,
                tool_model.ball_radius_mm,
            )
        )
        if diagnostics:
            return _report(
                operation,
                calculation_id,
                active_policy,
                diagnostics,
                counters,
                triangles,
                assembly,
                tool_model,
                holder,
            )
        motions = _motions(artifact, preview, context.machining_zone.wcs)
        for motion in motions:
            findings_before_motion = len(collision_diagnostics)
            counters.motions += 1
            _cancel(cancellation)
            distance = _distance(motion.start, motion.end)
            subdivisions = max(1, math.ceil(distance / active_policy.maximum_validation_step_mm))
            if subdivisions > active_policy.maximum_swept_subdivisions:
                diagnostics.append(
                    _limit_diagnostic(
                        operation,
                        "Swept validation subdivision limit exceeded.",
                        active_policy,
                        motion=motion,
                    )
                )
                break
            counters.subdivisions += subdivisions
            motion_checks_before = counters.narrow
            for subdivision in range(subdivisions):
                ratio0, ratio1 = subdivision / subdivisions, (subdivision + 1) / subdivisions
                start = _lerp(motion.start, motion.end, ratio0)
                end = _lerp(motion.start, motion.end, ratio1)
                expected_start = _interpolate_expected(motion, ratio0)
                expected_end = _interpolate_expected(motion, ratio1)
                for primitive in tool_model.primitives:
                    margin = _margin(primitive.component, motion.motion, active_policy)
                    bounds = swept_primitive_bounds(
                        primitive,
                        start,
                        end,
                        context.machining_zone.tool_axis,
                        margin,
                    )
                    candidate_values: list[ParallelCollisionTriangle] = []
                    for triangle_offset, triangle in enumerate(triangles):
                        counters.broad_scans += 1
                        if triangle_offset % active_policy.cancellation_cadence == 0:
                            _cancel(cancellation)
                        if counters.broad_scans + counters.narrow > active_policy.maximum_total_checks:
                            diagnostics.append(
                                _limit_diagnostic(
                                    operation,
                                    "Total broad/narrow safety check limit exceeded.",
                                    active_policy,
                                    motion=motion,
                                    component=primitive.component,
                                )
                            )
                            break
                        if bounds.overlaps(triangle.bounds):
                            candidate_values.append(triangle)
                    candidates = tuple(candidate_values)
                    if diagnostics:
                        break
                    counters.candidates += len(candidates)
                    if counters.candidates > active_policy.maximum_collision_candidates:
                        diagnostics.append(
                            _limit_diagnostic(
                                operation,
                                "Collision candidate limit exceeded.",
                                active_policy,
                                motion=motion,
                                component=primitive.component,
                            )
                        )
                        break
                    for triangle in candidates:
                        counters.narrow += 1
                        if (
                            counters.narrow > active_policy.maximum_narrow_phase_checks
                            or counters.broad_scans + counters.narrow
                            > active_policy.maximum_total_checks
                            or counters.narrow - motion_checks_before
                            > active_policy.maximum_checks_per_motion
                        ):
                            diagnostics.append(
                                _limit_diagnostic(
                                    operation,
                                    "Narrow-phase safety check limit exceeded.",
                                    active_policy,
                                    motion=motion,
                                    component=primitive.component,
                                )
                            )
                            break
                        if counters.narrow % active_policy.cancellation_cadence == 0:
                            _cancel(cancellation)
                        distance_to_axis = swept_axis_triangle_distance(
                            primitive,
                            start,
                            end,
                            context.machining_zone.tool_axis,
                            triangle,
                        )
                        finding = _collision_finding(
                            operation=operation,
                            motion=motion,
                            primitive=primitive,
                            triangle=triangle,
                            distance_to_axis=distance_to_axis,
                            margin=margin,
                            policy=active_policy,
                            start=start,
                            end=end,
                            expected_start=expected_start,
                            expected_end=expected_end,
                            tool_axis=context.machining_zone.tool_axis,
                            sample_index=subdivision,
                            swept_interval=(ratio0, ratio1),
                        )
                        if finding is not None:
                            aggregated = aggregate_parallel_safety_diagnostics(
                                calculation_id,
                                (*collision_diagnostics, finding),
                            )
                            if len(aggregated) > active_policy.maximum_report_items:
                                diagnostics.append(
                                    _limit_diagnostic(
                                        operation,
                                        "Unique safety finding limit exceeded.",
                                        active_policy,
                                        motion=motion,
                                        component=primitive.component,
                                    )
                                )
                                break
                            collision_diagnostics = aggregated
                    if diagnostics:
                        break
                if diagnostics:
                    break
            if diagnostics:
                break
            if len(collision_diagnostics) > findings_before_motion:
                # Finish every subdivision of the first unsafe motion so repeated
                # samples aggregate quantitatively, then fail fast before later
                # motions add unrelated noise to the primary review finding.
                break
        _cancel(cancellation)
    except _ParallelSafetyCancelled:
        diagnostics.append(
            _diagnostic(
                operation,
                ParallelSafetyStatus.CANCELLED,
                DiagnosticCode.PARALLEL_SAFETY_CANCELLED,
                "Parallel safety validation was cancelled before publish.",
            )
        )
    except CamValidationError as error:
        code = (
            DiagnosticCode.PARALLEL_SAFETY_MISSING_HOLDER_GEOMETRY
            if assembly.holder_id is not None
            else DiagnosticCode.PARALLEL_SAFETY_MISSING_TOOL_GEOMETRY
        )
        diagnostics.append(
            _diagnostic(
                operation,
                ParallelSafetyStatus.UNKNOWN,
                code,
                str(error) or "Tool assembly geometry is incomplete.",
            )
        )
    except Exception as error:
        diagnostics.append(
            _diagnostic(
                operation,
                ParallelSafetyStatus.FAILED,
                DiagnosticCode.PARALLEL_SAFETY_FAILED,
                "Parallel safety validation failed at an internal boundary.",
                debug=(("error_type", type(error).__name__),),
            )
        )
    diagnostics.extend(collision_diagnostics)
    return _report(
        operation,
        calculation_id,
        active_policy,
        diagnostics,
        counters,
        triangles,
        assembly,
        tool_model,
        holder,
    )


def parallel_artifact_has_safe_contract(
    artifact: ToolpathArtifact,
    *,
    require_holder_verified: bool = False,
) -> bool:
    """Return true only for a current SAFE marker with explicit assembly scope."""
    if not isinstance(artifact, ToolpathArtifact):
        return False
    if type(require_holder_verified) is not bool:
        return False
    markers = tuple(
        event
        for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "parallel.safety.contract"
    )
    if len(markers) != 1:
        return False
    metadata = dict(markers[0].metadata)
    report_fingerprint = metadata.get("safety_report_fingerprint", "")
    scope_fingerprint = metadata.get("tool_assembly_fingerprint", "")
    artifact_assembly_fingerprint = metadata.get(
        "artifact_tool_assembly_fingerprint", ""
    )
    checked = {
        value
        for value in metadata.get("checked_components", "").split(",")
        if value and value != "none"
    }
    unverified = set(
        value
        for value in metadata.get("unverified_components", "").split(",")
        if value and value != "none"
    )
    holder_state = metadata.get("holder_state")
    safety_scope = metadata.get("safety_scope")
    holder_scope_valid = (
        holder_state == "geometry_faithful"
        and safety_scope == "declared_assembly_holder_verified"
        and checked == {"cutter", "holder", "shank"}
        and not unverified
    ) or (
        holder_state == "declared_absent"
        and safety_scope == "declared_assembly_holder_absent"
        and checked == {"cutter", "shank"}
        and unverified == {"holder"}
    )
    return bool(
        metadata.get("algorithm_version")
        == str(PARALLEL_FINISHING_ALGORITHM_VERSION)
        and metadata.get("safety_status") == ParallelSafetyStatus.SAFE.value
        and _is_sha256(report_fingerprint)
        and _is_sha256(scope_fingerprint)
        and artifact_assembly_fingerprint
        == artifact.tool_assembly_fingerprint.digest
        and holder_scope_valid
        and (not require_holder_verified or holder_state == "geometry_faithful")
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _collision_primitives(
    envelope: ToolEnvelope,
    ball_radius: float,
) -> tuple[ParallelCollisionPrimitive, ...]:
    values: list[ParallelCollisionPrimitive] = []
    groups = (
        (ParallelToolComponent.CUTTER, envelope.cutter),
        (ParallelToolComponent.SHANK, envelope.shank),
        (ParallelToolComponent.HOLDER, envelope.holder),
    )
    for component, primitives in groups:
        for primitive in primitives:
            kind = {
                EnvelopePrimitiveKind.BALL: ParallelPrimitiveKind.SPHERE,
                EnvelopePrimitiveKind.CYLINDER: ParallelPrimitiveKind.CYLINDER,
                EnvelopePrimitiveKind.FRUSTUM: ParallelPrimitiveKind.FRUSTUM,
            }[primitive.kind]
            start = primitive.axial_start - ball_radius
            end = primitive.axial_end - ball_radius
            if kind is ParallelPrimitiveKind.SPHERE:
                start = end = primitive.axial_end - ball_radius
            values.append(
                ParallelCollisionPrimitive(
                    kind,
                    component,
                    start,
                    end,
                    primitive.lower_radius,
                    primitive.upper_radius,
                    primitive.label,
                    primitive.support.value,
                )
            )
    return tuple(values)


def _collision_triangles(
    context: Cam3DCalculationContext,
) -> tuple[ParallelCollisionTriangle, ...]:
    zone = context.machining_zone
    machining = {
        item.geometry.reference_id for item in zone.part_surfaces.selection.surfaces
    }
    check = (
        {
            item.geometry.reference_id
            for item in zone.check_surfaces.selection.surfaces
        }
        if zone.check_surfaces is not None
        else set()
    )
    fixtures = (
        {
            item.geometry.reference_id
            for item in zone.fixture_surfaces.selection.surfaces
        }
        if zone.fixture_surfaces is not None
        else set()
    )
    values: list[ParallelCollisionTriangle] = []
    mesh = context.calculation_mesh
    for index, (indices, source) in enumerate(
        zip(mesh.triangle_indices, mesh.triangle_sources, strict=True)
    ):
        points = tuple(mesh.vertices[item] for item in indices)
        typed_points = (points[0], points[1], points[2])
        geometry_source = (
            ParallelGeometrySource.MACHINING_FACE
            if source in machining
            else ParallelGeometrySource.CHECK_SURFACE
            if source in check
            else ParallelGeometrySource.FIXTURE
            if source in fixtures
            else ParallelGeometrySource.PROTECTED_PART
        )
        values.append(
            ParallelCollisionTriangle(
                index,
                source,
                geometry_source,
                typed_points,
                triangle_bounds(typed_points),
            )
        )
    return tuple(values)


def _motions(
    artifact: ToolpathArtifact,
    preview: ParallelPreview,
    wcs: WcsFrame,
) -> tuple[_Motion, ...]:
    segment_map = {
        (segment.pass_index, segment.segment_index): segment
        for pass_value in preview.passes
        for segment in pass_value.segments
    }
    values: list[_Motion] = []
    for event_index, event in enumerate(artifact.events):
        if isinstance(event, ArcMove):
            raise CamValidationError("Parallel safety does not accept arc motion candidates")
        if not isinstance(event, (RapidMove, LinearMove)):
            continue
        match = _PROVENANCE.fullmatch(event.provenance)
        pass_index = int(match.group("pass")) if match else None
        segment_index = int(match.group("segment")) if match else None
        action = match.group("action") if match else event.provenance
        segment = segment_map.get((pass_index, segment_index))
        expected_start: ParallelPathPoint | None = None
        expected_end: ParallelPathPoint | None = None
        if segment is not None:
            if action == "cut.close":
                expected_start = segment.points[-1]
                expected_end = segment.points[0]
            elif action.startswith("cut."):
                point_index = int(action.rsplit(".", 1)[1])
                if not 1 <= point_index < len(segment.points):
                    raise CamValidationError("Parallel cut provenance is malformed")
                expected_start = segment.points[point_index - 1]
                expected_end = segment.points[point_index]
            elif action in {"approach", "contact"}:
                expected_start = expected_end = segment.points[0]
            elif action in {"retract", "clearance"}:
                expected_start = expected_end = segment.points[-1]
            elif action.startswith("link.direct."):
                direct_parts = action.split(".")
                if len(direct_parts) != 4:
                    raise CamValidationError("Direct-link provenance is malformed")
                previous_segment = segment_map.get(
                    (int(direct_parts[2]), int(direct_parts[3]))
                )
                if previous_segment is None:
                    raise CamValidationError("Direct-link predecessor is missing")
                expected_start = previous_segment.points[-1]
                expected_end = segment.points[0]
        values.append(
            _Motion(
                event_index,
                _world_point(event.start.position, wcs),
                _world_point(event.end.position, wcs),
                _motion_classification(event, action),
                event.provenance,
                pass_index,
                segment_index,
                action,
                expected_start,
                expected_end,
            )
        )
    return tuple(values)


def _motion_classification(
    event: RapidMove | LinearMove,
    action: str,
) -> ParallelSafetyMotion:
    if event.motion_class is MotionClass.CUTTING:
        return ParallelSafetyMotion.CUT
    if "approach" in action or action == "contact":
        return ParallelSafetyMotion.APPROACH
    if "retract" in action:
        return ParallelSafetyMotion.RETRACT
    if "clearance" in action:
        return ParallelSafetyMotion.CLEARANCE
    if isinstance(event, RapidMove):
        return ParallelSafetyMotion.RAPID
    return ParallelSafetyMotion.LINK


def _collision_finding(
    *,
    operation: Operation,
    motion: _Motion,
    primitive: ParallelCollisionPrimitive,
    triangle: ParallelCollisionTriangle,
    distance_to_axis: float,
    margin: float,
    policy: ParallelSafetyPolicy,
    start: Point3,
    end: Point3,
    expected_start: ParallelPathPoint | None,
    expected_end: ParallelPathPoint | None,
    tool_axis: Vector3,
    sample_index: int,
    swept_interval: tuple[float, float],
) -> ParallelSafetyDiagnostic | None:
    radius = primitive.radius_mm
    machining = triangle.geometry_source is ParallelGeometrySource.MACHINING_FACE
    expected_motion = motion.motion in {
        ParallelSafetyMotion.CUT,
        ParallelSafetyMotion.APPROACH,
        ParallelSafetyMotion.RETRACT,
        ParallelSafetyMotion.LINK,
    }
    expected_sources = {
        source
        for point in (expected_start, expected_end)
        if point is not None
        for source in point.source_surface_ids
    }
    expected_triangle = machining and triangle.face_id in expected_sources
    allowed_contact = (
        primitive.component is ParallelToolComponent.CUTTER
        and expected_motion
        and expected_triangle
    )
    if allowed_contact:
        assert expected_start is not None and expected_end is not None
        expected_distance = segment_triangle_distance(
            expected_start.contact_point,
            expected_end.contact_point,
            triangle.points,
        )
        expected_limit = policy.contact_tolerance_mm + policy.gouge_tolerance_mm
        if (
            distance_to_axis <= radius + policy.numeric_epsilon_mm
            and expected_distance > expected_limit
        ):
            # A cutter flank/sphere that is merely tangent to another point on
            # the same declared machining face is allowable surface contact,
            # not a gouge.  Only penetration beyond the conservative gouge
            # tolerance is unsafe.
            if distance_to_axis >= radius - policy.gouge_tolerance_mm:
                return None
            threshold = radius + policy.contact_tolerance_mm
            code = DiagnosticCode.PARALLEL_SAFETY_CUTTER_GOUGE
            message = (
                f"Unexpected cutter contact outside the declared contact zone on motion "
                f"{motion.event_index}."
            )
        else:
            threshold = radius - policy.gouge_tolerance_mm
            if distance_to_axis >= threshold:
                return None
            code = DiagnosticCode.PARALLEL_SAFETY_CUTTER_GOUGE
            message = (
                f"Cutter gouge on pass {motion.pass_index}, segment {motion.segment_index}; "
                f"penetration {radius - distance_to_axis:.6g} mm."
            )
    else:
        actual_clearance = distance_to_axis - radius
        if parallel_clearance_is_satisfied(actual_clearance, margin):
            return None
        code = _collision_code(primitive.component, motion.motion, machining)
        message = (
            f"{primitive.component.value.capitalize()} collision on motion "
            f"{motion.event_index}; clearance {distance_to_axis - radius:.6g} mm."
        )
    midpoint = _lerp(start, end, 0.5)
    center = Point3(
        midpoint.x
        + tool_axis.x * (primitive.axial_start_mm + primitive.axial_end_mm) * 0.5,
        midpoint.y
        + tool_axis.y * (primitive.axial_start_mm + primitive.axial_end_mm) * 0.5,
        midpoint.z
        + tool_axis.z * (primitive.axial_start_mm + primitive.axial_end_mm) * 0.5,
        LengthUnit.MM,
    )
    contact, _distance_value = closest_point_on_triangle(center, triangle.points)
    return ParallelSafetyDiagnostic(
        ParallelSafetyStatus.UNSAFE,
        code,
        DiagnosticSeverity.ERROR,
        message,
        operation.operation_id,
        motion.pass_index,
        motion.segment_index,
        motion.event_index,
        primitive.component,
        triangle.geometry_source,
        triangle.face_id,
        closest_distance_mm=distance_to_axis,
        penetration_depth_mm=max(0.0, radius - distance_to_axis),
        tolerance_mm=(policy.gouge_tolerance_mm if allowed_contact else margin),
        contact_point=contact,
        tool_position=midpoint,
        debug_metadata=(
            ("motion_class", motion.motion.value),
            ("primitive", primitive.label),
            ("primitive_support", primitive.approximation),
            ("provenance", motion.provenance),
            ("triangle_index", str(triangle.triangle_index)),
        ),
        occurrence_count=1,
        first_sample_index=sample_index,
        last_sample_index=sample_index,
        minimum_clearance_mm=distance_to_axis - radius,
        maximum_penetration_mm=max(0.0, radius - distance_to_axis),
        required_clearance_mm=(0.0 if allowed_contact else margin),
        swept_interval_start=swept_interval[0],
        swept_interval_end=swept_interval[1],
    )


def _collision_code(
    component: ParallelToolComponent,
    motion: ParallelSafetyMotion,
    machining: bool,
) -> DiagnosticCode:
    if component is ParallelToolComponent.SHANK:
        return DiagnosticCode.PARALLEL_SAFETY_SHANK_COLLISION
    if component is ParallelToolComponent.HOLDER:
        return DiagnosticCode.PARALLEL_SAFETY_HOLDER_COLLISION
    if motion in {ParallelSafetyMotion.RAPID, ParallelSafetyMotion.CLEARANCE}:
        return DiagnosticCode.PARALLEL_SAFETY_RAPID_COLLISION
    if motion is ParallelSafetyMotion.APPROACH:
        return DiagnosticCode.PARALLEL_SAFETY_APPROACH_COLLISION
    if motion is ParallelSafetyMotion.RETRACT:
        return DiagnosticCode.PARALLEL_SAFETY_RETRACT_COLLISION
    if motion in {ParallelSafetyMotion.LINK, ParallelSafetyMotion.REGION_TRANSITION}:
        return DiagnosticCode.PARALLEL_SAFETY_LINK_COLLISION
    if not machining:
        return DiagnosticCode.PARALLEL_SAFETY_PROTECTED_FACE_COLLISION
    return DiagnosticCode.PARALLEL_SAFETY_CUTTER_GOUGE


def _topology_findings(
    operation: Operation,
    preview: ParallelPreview,
    tool_radius: float,
    policy: ParallelSafetyPolicy,
) -> tuple[ParallelSafetyDiagnostic, ...]:
    findings: list[ParallelSafetyDiagnostic] = []
    for pass_value in preview.passes:
        for segment in pass_value.segments:
            for first, second in zip(segment.points, segment.points[1:]):
                contact_length = _distance(first.contact_point, second.contact_point)
                center_length = _distance(first.tool_center_point, second.tool_center_point)
                dot = max(-1.0, min(1.0, first.surface_normal.dot(second.surface_normal)))
                angle = math.acos(dot)
                if angle > math.pi / 3.0:
                    findings.append(
                        _diagnostic(
                            operation,
                            ParallelSafetyStatus.UNKNOWN,
                            DiagnosticCode.PARALLEL_SAFETY_SHARP_EDGE_AMBIGUITY,
                            "Parallel path crosses a sharp normal discontinuity.",
                            pass_index=segment.pass_index,
                            segment_index=segment.segment_index,
                        )
                    )
                    return tuple(findings)
                if angle > policy.numeric_epsilon_mm and contact_length > policy.numeric_epsilon_mm:
                    local_radius = contact_length / angle
                    concave = center_length + policy.gouge_tolerance_mm < contact_length
                    if concave and local_radius <= tool_radius + policy.gouge_tolerance_mm:
                        findings.append(
                            _diagnostic(
                                operation,
                                ParallelSafetyStatus.UNSAFE,
                                DiagnosticCode.PARALLEL_SAFETY_UNSUPPORTED_CURVATURE,
                                "Concave local radius is not accessible to the selected ball-end tool.",
                                pass_index=segment.pass_index,
                                segment_index=segment.segment_index,
                                debug=(("estimated_radius_mm", f"{local_radius:.12g}"),),
                            )
                        )
                        return tuple(findings)
    return tuple(findings)


def _clearance_findings(
    operation: Operation,
    context: Cam3DCalculationContext,
    policy: ParallelSafetyPolicy,
    ball_radius_mm: float,
) -> tuple[ParallelSafetyDiagnostic, ...]:
    safe = context.safe_motion_policy
    if safe.clearance_z is None or safe.retract_z is None:
        return (
            _diagnostic(
                operation,
                ParallelSafetyStatus.UNSAFE,
                DiagnosticCode.PARALLEL_SAFETY_INSUFFICIENT_CLEARANCE,
                "Clearance and retract planes are required for safety validation.",
            ),
        )
    maximum = max(
        _setup_point(point, context.machining_zone.wcs).z
        for point in context.calculation_mesh.vertices
    )
    required = maximum + ball_radius_mm + policy.rapid_clearance_mm
    if safe.retract_z <= required or safe.clearance_z < safe.retract_z:
        return (
            _diagnostic(
                operation,
                ParallelSafetyStatus.UNSAFE,
                DiagnosticCode.PARALLEL_SAFETY_INSUFFICIENT_CLEARANCE,
                f"Retract/clearance plane must exceed protected bounds ({required:.6g} mm).",
                debug=(("required_setup_z", f"{required:.12g}"),),
            ),
        )
    return ()


def _margin(
    component: ParallelToolComponent,
    motion: ParallelSafetyMotion,
    policy: ParallelSafetyPolicy,
) -> float:
    if component is ParallelToolComponent.SHANK:
        return policy.shank_clearance_mm
    if component is ParallelToolComponent.HOLDER:
        return policy.holder_clearance_mm
    if motion in {ParallelSafetyMotion.RAPID, ParallelSafetyMotion.CLEARANCE}:
        return policy.rapid_clearance_mm
    return policy.contact_tolerance_mm


def _interpolate_expected(
    motion: _Motion,
    ratio: float,
) -> ParallelPathPoint | None:
    if motion.expected_start is None or motion.expected_end is None:
        return None
    return motion.expected_start if ratio < 0.5 else motion.expected_end


def _diagnostic(
    operation: Operation,
    status: ParallelSafetyStatus,
    code: DiagnosticCode,
    message: str,
    *,
    pass_index: int | None = None,
    segment_index: int | None = None,
    motion_index: int | None = None,
    component: ParallelToolComponent | None = None,
    debug: tuple[tuple[str, str], ...] = (),
) -> ParallelSafetyDiagnostic:
    return ParallelSafetyDiagnostic(
        status,
        code,
        DiagnosticSeverity.ERROR,
        message,
        operation.operation_id,
        pass_index,
        segment_index,
        motion_index,
        component,
        debug_metadata=debug,
    )


def _limit_diagnostic(
    operation: Operation,
    message: str,
    policy: ParallelSafetyPolicy,
    *,
    motion: _Motion | None = None,
    component: ParallelToolComponent | None = None,
) -> ParallelSafetyDiagnostic:
    return _diagnostic(
        operation,
        ParallelSafetyStatus.UNKNOWN,
        DiagnosticCode.PARALLEL_SAFETY_LIMIT_EXCEEDED,
        message,
        pass_index=motion.pass_index if motion else None,
        segment_index=motion.segment_index if motion else None,
        motion_index=motion.event_index if motion else None,
        component=component,
        debug=(("maximum_total_checks", str(policy.maximum_total_checks)),),
    )


def _report(
    operation: Operation,
    calculation_id: str,
    policy: ParallelSafetyPolicy | None,
    diagnostics: list[ParallelSafetyDiagnostic],
    counters: _Counters,
    triangles: tuple[ParallelCollisionTriangle, ...],
    assembly: ToolAssembly,
    tool_model: ParallelToolAssemblySafetyModel | None,
    holder: HolderDefinition | None,
) -> ParallelSafetyReport:
    if policy is None:
        policy = ParallelSafetyPolicy(1.0e-9, 1.0e-6, 1.0e-6, 0.0, 0.0, 0.0, 0.0, 1.0)
    ordered = aggregate_parallel_safety_diagnostics(
        calculation_id,
        tuple(diagnostics),
    )
    statuses = {item.status for item in ordered}
    status = (
        ParallelSafetyStatus.FAILED
        if ParallelSafetyStatus.FAILED in statuses
        else ParallelSafetyStatus.CANCELLED
        if ParallelSafetyStatus.CANCELLED in statuses
        else ParallelSafetyStatus.UNSAFE
        if ParallelSafetyStatus.UNSAFE in statuses
        else ParallelSafetyStatus.UNKNOWN
        if ParallelSafetyStatus.UNKNOWN in statuses
        else ParallelSafetyStatus.SAFE
    )
    holder_state = (
        tool_model.holder_state
        if tool_model is not None
        else "declared_absent"
        if assembly.holder_id is None
        else "missing"
        if holder is None
        else "reference_invalid"
    )
    checked_components = (
        tuple({item.component for item in tool_model.primitives})
        if tool_model is not None
        else ()
    )
    unverified_components = tuple(
        component
        for component in ParallelToolComponent
        if component not in checked_components
    )
    safety_scope = (
        "declared_assembly_holder_verified"
        if holder_state == "geometry_faithful"
        else "declared_assembly_holder_absent"
        if holder_state == "declared_absent"
        else "incomplete_tool_assembly"
    )
    tool_assembly_fingerprint = (
        tool_model.fingerprint
        if tool_model is not None
        else ContentFingerprint.from_payload(
            {
                "assembly": assembly.to_dict(),
                "holder": holder.to_dict() if holder is not None else None,
                "holder_state": holder_state,
            }
        )
    )
    capabilities = (
        (
            "fixture_collision",
            "supported"
            if any(
                item.geometry_source is ParallelGeometrySource.FIXTURE
                for item in triangles
            )
            else "unavailable",
        ),
        (
            "holder_geometry",
            holder_state,
        ),
        (
            "machine_ready_clearance",
            "verified" if policy.machine_ready_clearance_verified else "unverified",
        ),
        ("stock_collision", "unavailable"),
        (
            "protected_geometry_collision",
            "supported" if _protected_count(triangles) else "unavailable",
        ),
        ("swept_motion", "complete_linear"),
    )
    return ParallelSafetyReport(
        status,
        operation.operation_id,
        calculation_id,
        PARALLEL_FINISHING_ALGORITHM_VERSION,
        policy,
        ordered,
        ParallelSafetyStatistics(
            counters.motions,
            _protected_count(triangles),
            counters.candidates,
            counters.narrow,
            counters.subdivisions,
        ),
        checked_components,
        unverified_components,
        holder_state,
        tool_assembly_fingerprint,
        safety_scope,
        capabilities,
    )


def _protected_count(triangles: tuple[ParallelCollisionTriangle, ...]) -> int:
    return sum(
        item.geometry_source is not ParallelGeometrySource.MACHINING_FACE
        for item in triangles
    )


def _world_point(point: Point3, wcs: WcsFrame) -> Point3:
    return Point3(
        wcs.origin.x
        + point.x * wcs.x_axis.x
        + point.y * wcs.y_axis.x
        + point.z * wcs.z_axis.x,
        wcs.origin.y
        + point.x * wcs.x_axis.y
        + point.y * wcs.y_axis.y
        + point.z * wcs.z_axis.y,
        wcs.origin.z
        + point.x * wcs.x_axis.z
        + point.y * wcs.y_axis.z
        + point.z * wcs.z_axis.z,
        LengthUnit.MM,
    )


def _setup_point(point: Point3, wcs: WcsFrame) -> Point3:
    delta = Vector3(point.x - wcs.origin.x, point.y - wcs.origin.y, point.z - wcs.origin.z)
    return Point3(
        delta.dot(wcs.x_axis),
        delta.dot(wcs.y_axis),
        delta.dot(wcs.z_axis),
        LengthUnit.MM,
    )


def _lerp(first: Point3, second: Point3, ratio: float) -> Point3:
    return Point3(
        first.x + (second.x - first.x) * ratio,
        first.y + (second.y - first.y) * ratio,
        first.z + (second.z - first.z) * ratio,
        LengthUnit.MM,
    )


def _distance(first: Point3, second: Point3) -> float:
    return math.dist((first.x, first.y, first.z), (second.x, second.y, second.z))


class _ParallelSafetyCancelled(RuntimeError):
    pass


def _cancel(cancellation: Callable[[], bool] | None) -> None:
    if cancellation is not None and cancellation():
        raise _ParallelSafetyCancelled
