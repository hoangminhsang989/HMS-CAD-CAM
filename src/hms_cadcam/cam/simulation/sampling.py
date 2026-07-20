"""Deterministic toolpath sampling for simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.toolpath.events import (
    ArcMove, AnyToolpathEvent, CoolantState, CoolantStateEvent, LinearMove,
    MotionClass, RapidMove, SpindleState, SpindleStateEvent,
)
from hms_cadcam.cam.toolpath.geometry import Pose, distance, validate_arc, same_pose
from hms_cadcam.cam.toolpath.model import ToolpathArtifact
from .coordinates import pose_to_world
from .model import SimulationIssueCode, SimulationSamplingPolicy


class SimulationSamplingError(RuntimeError):
    """Fail-closed sampling error carrying a catalog code."""

    def __init__(self, code: SimulationIssueCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SampleProvenance:
    event_index: int
    event_id: str
    local_sample_index: int
    motion_class: MotionClass
    spindle_state: SpindleState
    coolant_state: CoolantState


@dataclass(frozen=True, slots=True)
class SimulationSample:
    index: int
    setup_pose: Pose
    world_pose: Pose
    provenance: tuple[SampleProvenance, ...]


@dataclass(frozen=True, slots=True)
class SampledSegment:
    event_index: int
    event_id: str
    event_kind: str
    motion_class: MotionClass
    spindle_state: SpindleState
    coolant_state: CoolantState
    sample_indices: tuple[int, ...]
    segment_index: int = -1


@dataclass(frozen=True, slots=True)
class SamplingOutput:
    samples: tuple[SimulationSample, ...]
    segments: tuple[SampledSegment, ...]


def _cancelled(cancellation: Callable[[], bool] | None) -> bool:
    return bool(cancellation is not None and cancellation())


def _line_pose(start: Pose, end: Pose, fraction: float) -> Pose:
    return Pose(Point3(start.position.x + (end.position.x - start.position.x) * fraction, start.position.y + (end.position.y - start.position.y) * fraction, start.position.z + (end.position.z - start.position.z) * fraction, start.position.unit), start.tool_axis)


def _arc_pose(event: ArcMove, fraction: float) -> Pose:
    radius, u, v = validate_arc(event.start, event.end, event.center, event.plane_normal, event.sweep_radians)
    angle = event.sweep_radians * fraction
    return Pose(Point3(event.center.x + radius * (u.x * math.cos(angle) + v.x * math.sin(angle)), event.center.y + radius * (u.y * math.cos(angle) + v.y * math.sin(angle)), event.center.z + radius * (u.z * math.cos(angle) + v.z * math.sin(angle)), event.center.unit), event.start.tool_axis)


def _arc_count(event: ArcMove, policy: SimulationSamplingPolicy) -> int:
    radius = distance(event.start.position, event.center)
    if policy.chord_tolerance >= radius:
        chord_angle = math.pi
    else:
        chord_angle = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - policy.chord_tolerance / radius)))
    max_angle = min(policy.max_arc_angle, chord_angle)
    return max(1, math.ceil(abs(event.sweep_radians) / max_angle))


SamplingProgress = Callable[[int, int], None]


def sample_toolpath(
    *,
    artifact: ToolpathArtifact,
    wcs: WcsFrame,
    policy: SimulationSamplingPolicy,
    cancellation: Callable[[], bool] | None = None,
    progress: SamplingProgress | None = None,
) -> SamplingOutput:
    """Sample every movement with exact endpoints and deterministic deduplication."""
    if artifact.unit is not wcs.origin.unit:
        raise SimulationSamplingError(SimulationIssueCode.UNIT_MISMATCH, "Toolpath and WCS units differ")
    movement_count = sum(
        isinstance(event, (RapidMove, LinearMove, ArcMove))
        for event in artifact.events
    )
    processed_movements = 0
    if progress is not None:
        progress(0, movement_count)
    samples: list[SimulationSample] = [SimulationSample(0, artifact.initial_pose, pose_to_world(artifact.initial_pose, wcs), ())]
    segments: list[SampledSegment] = []
    spindle = SpindleState.OFF
    coolant = CoolantState.OFF
    checks = 0
    current = artifact.initial_pose

    def append_pose(pose: Pose, provenance: SampleProvenance) -> int:
        nonlocal checks
        checks += 1
        if checks % policy.cancellation_check_interval == 0 and _cancelled(cancellation):
            raise SimulationSamplingError(SimulationIssueCode.CANCELLED, "Simulation sampling cancelled")
        if checks % policy.cancellation_check_interval == 0 and progress is not None:
            progress(processed_movements, movement_count)
        world = pose_to_world(pose, wcs)
        previous = samples[-1]
        if same_pose(previous.world_pose, world, policy.geometric_tolerance):
            merged = previous.provenance + (provenance,)
            samples[-1] = SimulationSample(previous.index, previous.setup_pose, previous.world_pose, tuple(sorted(set(merged), key=lambda item: (item.event_index, item.local_sample_index, item.event_id))))
            return previous.index
        if len(samples) >= policy.maximum_samples:
            raise SimulationSamplingError(SimulationIssueCode.SAMPLE_LIMIT, "Simulation sample limit exceeded")
        if (len(samples) + 1) * 256 > policy.memory_budget_bytes:
            raise SimulationSamplingError(SimulationIssueCode.SAMPLE_LIMIT, "Simulation sampling memory budget exceeded")
        index = len(samples)
        samples.append(SimulationSample(index, pose, world, (provenance,)))
        return index

    for event_index, event in enumerate(artifact.events):
        if isinstance(event, SpindleStateEvent):
            spindle = event.state
            continue
        if isinstance(event, CoolantStateEvent):
            coolant = event.state
            continue
        if not isinstance(event, (RapidMove, LinearMove, ArcMove)):
            continue
        if not same_pose(current, event.start, policy.geometric_tolerance):
            raise SimulationSamplingError(SimulationIssueCode.INVALID_MOTION, "Toolpath movement is discontinuous")
        if any(abs(first - second) > policy.geometric_tolerance for first, second in zip(
            (event.start.tool_axis.x, event.start.tool_axis.y, event.start.tool_axis.z),
            (event.end.tool_axis.x, event.end.tool_axis.y, event.end.tool_axis.z), strict=True,
        )):
            raise SimulationSamplingError(SimulationIssueCode.INVALID_MOTION, "Changing tool axis is unsupported in simulation v1")
        count = max(1, math.ceil(event.length / policy.max_linear_step)) if isinstance(event, (RapidMove, LinearMove)) else _arc_count(event, policy)
        start_provenance = SampleProvenance(event_index, str(event.event_id), 0, event.motion_class, spindle, coolant)
        indices = [append_pose(event.start, start_provenance)]
        for local in range(1, count + 1):
            fraction = local / count
            pose = event.end if local == count else (_arc_pose(event, fraction) if isinstance(event, ArcMove) else _line_pose(event.start, event.end, fraction))
            provenance = SampleProvenance(event_index, str(event.event_id), local, event.motion_class, spindle, coolant)
            indices.append(append_pose(pose, provenance))
        segments.append(SampledSegment(event_index, str(event.event_id), event.kind.value, event.motion_class, spindle, coolant, tuple(indices), len(segments)))
        current = event.end
        processed_movements += 1
        if progress is not None:
            progress(processed_movements, movement_count)
    if _cancelled(cancellation):
        raise SimulationSamplingError(SimulationIssueCode.CANCELLED, "Simulation sampling cancelled")
    return SamplingOutput(tuple(samples), tuple(segments))
