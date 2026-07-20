"""Two-phase broad/narrow collision analysis with fail-closed semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Callable

from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.ids import SimulationResultId
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath.geometry import Bounds3, Pose
from hms_cadcam.cam.toolpath.model import ToolpathArtifact
from hms_cadcam.cam.toolpath.events import MotionClass, SpindleState
from .coordinates import apply_affine_point, transform_bounds
from .envelope import EnvelopePrimitive, ToolEnvelope
from .model import (
    SimulationIssue, SimulationIssueCategory, SimulationIssueCode,
    SimulationRequest, SimulationResult, SimulationStatistics,
    SimulationStatus,
)
from .sampling import SamplingOutput, SampledSegment, SimulationSamplingError


class CollisionTargetKind(StrEnum):
    STOCK = "stock"
    FIXTURE = "fixture"


@dataclass(frozen=True, slots=True)
class CollisionTarget:
    entity_id: str
    kind: CollisionTargetKind
    bounds: Bounds3
    geometry: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id or not isinstance(self.kind, CollisionTargetKind) or not isinstance(self.bounds, Bounds3):
            raise ValueError("Collision target is invalid")


@dataclass(frozen=True, slots=True)
class CollisionScene:
    stock: CollisionTarget | None
    fixtures: tuple[CollisionTarget, ...] = ()

    def __post_init__(self) -> None:
        if self.stock is not None and self.stock.kind is not CollisionTargetKind.STOCK:
            raise ValueError("Scene stock target kind is invalid")
        if any(item.kind is not CollisionTargetKind.FIXTURE for item in self.fixtures):
            raise ValueError("Scene fixture target kind is invalid")
        if len({item.entity_id for item in self.fixtures}) != len(self.fixtures):
            raise ValueError("Fixture target IDs must be unique")


@dataclass(frozen=True, slots=True)
class CollisionEvidence:
    exact: bool
    distance: float | None = None
    entity_fingerprint: str | None = None


class CollisionBackend(Protocol):
    def broad_overlap(self, target: CollisionTarget, primitive_bounds: Bounds3) -> bool: ...
    def narrow_intersects(self, target: CollisionTarget, primitive: EnvelopePrimitive, pose: Pose, tolerance: float) -> CollisionEvidence | None: ...


def aabb_overlap(first: Bounds3, second: Bounds3, tolerance: float = 0.0) -> bool:
    return all(a <= b + tolerance for a, b in zip((first.minimum.x, first.minimum.y, first.minimum.z), (second.maximum.x, second.maximum.y, second.maximum.z), strict=True)) and all(a >= b - tolerance for a, b in zip((first.maximum.x, first.maximum.y, first.maximum.z), (second.minimum.x, second.minimum.y, second.minimum.z), strict=True))


class InMemoryAabbBackend:
    """Deterministic test/headless backend; AABB overlap is explicitly exact for its targets."""

    def broad_overlap(self, target: CollisionTarget, primitive_bounds: Bounds3) -> bool:
        return aabb_overlap(target.bounds, primitive_bounds)

    def narrow_intersects(self, target: CollisionTarget, primitive: EnvelopePrimitive, pose: Pose, tolerance: float) -> CollisionEvidence | None:
        bounds = primitive_bounds(primitive, pose, tolerance)
        return CollisionEvidence(True, 0.0, target.entity_id) if aabb_overlap(target.bounds, bounds, tolerance) else None


def primitive_bounds(primitive: EnvelopePrimitive, pose: Pose, tolerance: float = 0.0) -> Bounds3:
    """Conservative world AABB for one fixed-axis envelope primitive."""
    axis = pose.tool_axis
    points = []
    for axial in (primitive.axial_start, primitive.axial_end):
        center = Point3(pose.position.x + axis.x * axial, pose.position.y + axis.y * axial, pose.position.z + axis.z * axial, pose.position.unit)
        radius = primitive.radius + tolerance
        points.extend((Point3(center.x - radius, center.y - radius, center.z - radius, center.unit), Point3(center.x + radius, center.y + radius, center.z + radius, center.unit)))
    return Bounds3.from_points(tuple(points))


def _issue(*, request: SimulationRequest, code: SimulationIssueCode, category: SimulationIssueCategory, severity: DiagnosticSeverity, message: str, segment: SampledSegment | None = None, sample_index: int | None = None, point: Point3 | None = None, target: CollisionTarget | None = None, extra: tuple[tuple[str, str], ...] = ()) -> SimulationIssue:
    return SimulationIssue(severity=severity, category=category, code=code, message_key=message, operation_id=request.operation_id, artifact_id=request.artifact_id, segment_index=(segment.segment_index if segment and segment.segment_index >= 0 else segment.event_index if segment else None), event_index=segment.event_index if segment else None, sample_index=sample_index, world_point=point, bounds=target.bounds if target else None, involved_entities=tuple(item for item in (target.entity_id if target else "",) if item) + tuple(value for key, value in extra if key == "envelope"), evidence=extra)


def _groups(envelope: ToolEnvelope) -> tuple[tuple[str, tuple[EnvelopePrimitive, ...]], ...]:
    return (("cutter", envelope.cutter), ("shank", envelope.shank), ("holder", envelope.holder))


def run_collision_analysis(*, request: SimulationRequest, artifact: ToolpathArtifact, sampling: SamplingOutput, envelope: ToolEnvelope, scene: CollisionScene, backend: CollisionBackend, result_id: SimulationResultId, cancellation: Callable[[], bool] | None = None) -> SimulationResult:
    if scene.stock is None:
        raise SimulationSamplingError(SimulationIssueCode.STOCK_MISSING, "Collision scene has no stock")
    issues: list[SimulationIssue] = []
    collision_count = warning_count = error_count = 0
    all_points = tuple(sample.world_pose.position for sample in sampling.samples)
    bounds = Bounds3.from_points(all_points)
    if artifact.unit is not envelope.unit or scene.stock.bounds.minimum.unit is not envelope.unit:
        raise SimulationSamplingError(SimulationIssueCode.UNIT_MISMATCH, "Collision scene unit mismatch")

    def add_issue(issue: SimulationIssue) -> None:
        if len(issues) >= request.sampling_policy.maximum_issues:
            raise SimulationSamplingError(SimulationIssueCode.SAMPLE_LIMIT, "Simulation issue limit exceeded")
        issues.append(issue)

    for segment in sampling.segments:
        if cancellation is not None and cancellation():
            raise SimulationSamplingError(SimulationIssueCode.CANCELLED, "Collision analysis cancelled")
        segment_samples = tuple(sampling.samples[index] for index in segment.sample_indices)
        if segment.motion_class is not MotionClass.CUTTING and request.safe_height is not None:
            for sample in segment_samples:
                if sample.setup_pose.position.z < request.safe_height - request.sampling_policy.geometric_tolerance:
                    add_issue(_issue(request=request, code=SimulationIssueCode.RAPID_BELOW_SAFE, category=SimulationIssueCategory.CLEARANCE_WARNING, severity=DiagnosticSeverity.WARNING, message="rapid.below_safe", segment=segment, sample_index=sample.index, point=sample.world_pose.position, extra=(("safe_height", str(request.safe_height)),)))
                    warning_count += 1
                    break
        for segment_sample_index, sample in enumerate(segment_samples):
            for label, primitives in _groups(envelope):
                for primitive in primitives:
                    p_bounds = primitive_bounds(primitive, sample.world_pose, request.sampling_policy.geometric_tolerance)
                    if segment_sample_index > 0:
                        previous_bounds = primitive_bounds(primitive, segment_samples[segment_sample_index - 1].world_pose, request.sampling_policy.geometric_tolerance)
                        p_bounds = Bounds3.union((previous_bounds, p_bounds))
                    targets = (scene.stock, *scene.fixtures)
                    for target in targets:
                        if target is None or not backend.broad_overlap(target, p_bounds):
                            continue
                        evidence = backend.narrow_intersects(target, primitive, sample.world_pose, request.sampling_policy.geometric_tolerance)
                        if evidence is None:
                            add_issue(_issue(request=request, code=SimulationIssueCode.FAILED, category=SimulationIssueCategory.CLEARANCE_WARNING, severity=DiagnosticSeverity.WARNING, message="clearance.unproven", segment=segment, sample_index=sample.index, point=sample.world_pose.position, target=target, extra=(("envelope", label), ("proof", "none"))))
                            warning_count += 1
                            continue
                        code: SimulationIssueCode | None = None
                        category = SimulationIssueCategory.COLLISION
                        severity = DiagnosticSeverity.ERROR
                        if target.kind is CollisionTargetKind.FIXTURE:
                            code = SimulationIssueCode.TOOL_FIXTURE_COLLISION if label == "cutter" else (SimulationIssueCode.SHANK_FIXTURE_COLLISION if label == "shank" else SimulationIssueCode.HOLDER_FIXTURE_COLLISION)
                        elif label == "shank":
                            code = SimulationIssueCode.SHANK_STOCK_COLLISION
                        elif label == "holder":
                            code = SimulationIssueCode.HOLDER_STOCK_COLLISION
                        elif segment.motion_class is MotionClass.CUTTING:
                            continue
                        elif segment.spindle_state is SpindleState.OFF:
                            code = SimulationIssueCode.GOUGE_DETECTED
                            category = SimulationIssueCategory.COLLISION
                        else:
                            code = SimulationIssueCode.GOUGE_DETECTED
                            category = SimulationIssueCategory.GOUGE
                        if code is not None:
                            add_issue(_issue(request=request, code=code, category=category, severity=severity, message=code.value.replace("sim.", ""), segment=segment, sample_index=sample.index, point=sample.world_pose.position, target=target, extra=(("envelope", label), ("proof", "exact"))))
                            collision_count += 1
                            error_count += 1
    status = SimulationStatus.FAIL if error_count or collision_count else (SimulationStatus.WARN if warning_count else SimulationStatus.PASS)
    stats = SimulationStatistics(len(sampling.samples), len(sampling.segments), collision_count, warning_count, error_count, bounds)
    return SimulationResult.create(result_id=result_id, request=request, status=status, issues=tuple(issues), statistics=stats)
