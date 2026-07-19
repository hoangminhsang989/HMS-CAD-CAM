"""Semantic, controller-neutral toolpath event model."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.ids import OperationId, ToolAssemblyId, ToolpathEventId
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, SpindleSpeed
from hms_cadcam.cam.toolpath.geometry import Bounds3, Pose, arc_bounds, distance, same_pose, validate_arc

_SEMANTIC_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")


class ToolpathEventKind(StrEnum):
    RAPID = "rapid"
    LINEAR = "linear"
    ARC = "arc"
    DWELL = "dwell"
    SPINDLE_STATE = "spindle_state"
    COOLANT_STATE = "coolant_state"
    TOOL_CONTEXT = "tool_context"
    FEED_MODE = "feed_mode"
    MARKER = "marker"


class MotionClass(StrEnum):
    CUTTING = "cutting"
    NON_CUTTING = "non_cutting"
    LINK = "link"
    RETRACT = "retract"


class SpindleState(StrEnum):
    OFF = "off"
    CLOCKWISE = "clockwise"
    COUNTERCLOCKWISE = "counterclockwise"


class CoolantState(StrEnum):
    OFF = "off"
    FLOOD = "flood"
    MIST = "mist"
    THROUGH_TOOL = "through_tool"


class FeedMode(StrEnum):
    UNITS_PER_MINUTE = "units_per_minute"
    UNITS_PER_REVOLUTION = "units_per_revolution"
    INVERSE_TIME = "inverse_time"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolpathEvent:
    event_id: ToolpathEventId
    sequence_index: int
    source_operation_id: OperationId
    provenance: str
    metadata: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[ToolpathEventKind]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, ToolpathEventId) or not isinstance(self.source_operation_id, OperationId):
            raise CamValidationError("Toolpath event identity is invalid")
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise CamValidationError("Toolpath event sequence is invalid")
        if not isinstance(self.provenance, str) or not _SEMANTIC_KEY.fullmatch(self.provenance):
            raise CamValidationError("Toolpath event provenance is invalid")
        if not isinstance(self.metadata, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or
                not all(isinstance(value, str) and value for value in item) for item in self.metadata):
            raise CamValidationError("Toolpath event metadata is invalid")
        normalized = tuple(sorted(self.metadata))
        if len({key for key, _ in normalized}) != len(normalized):
            raise CamInvariantError("Toolpath event metadata keys must be unique")
        object.__setattr__(self, "metadata", normalized)

    @property
    def bounds(self) -> Bounds3 | None:
        return None

    @property
    def length(self) -> float:
        return 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class RapidMove(ToolpathEvent):
    start: Pose
    end: Pose
    motion_class: MotionClass = MotionClass.NON_CUTTING
    rapid_rate: FeedRate | None = None
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.RAPID

    def __post_init__(self) -> None:
        super(RapidMove, self).__post_init__()
        _movement(self.start, self.end, self.motion_class)
        if self.motion_class is MotionClass.CUTTING:
            raise CamInvariantError("Rapid movement cannot be classified as cutting")
        if self.rapid_rate is not None and not isinstance(self.rapid_rate, FeedRate):
            raise CamValidationError("Rapid rate is invalid")

    @property
    def bounds(self) -> Bounds3:
        return Bounds3.from_points((self.start.position, self.end.position))

    @property
    def length(self) -> float:
        return distance(self.start.position, self.end.position)


@dataclass(frozen=True, slots=True, kw_only=True)
class LinearMove(ToolpathEvent):
    start: Pose
    end: Pose
    feed_rate: FeedRate
    motion_class: MotionClass = MotionClass.CUTTING
    engagement: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.LINEAR

    def __post_init__(self) -> None:
        super(LinearMove, self).__post_init__()
        _movement(self.start, self.end, self.motion_class)
        if not isinstance(self.feed_rate, FeedRate):
            raise CamValidationError("Linear feed is invalid")
        if not isinstance(self.engagement, tuple) or any(not isinstance(item, tuple) or len(item) != 2 or
                not all(isinstance(value, str) and value for value in item) for item in self.engagement):
            raise CamValidationError("Linear engagement metadata is invalid")
        object.__setattr__(self, "engagement", tuple(sorted(self.engagement)))

    @property
    def bounds(self) -> Bounds3:
        return Bounds3.from_points((self.start.position, self.end.position))

    @property
    def length(self) -> float:
        return distance(self.start.position, self.end.position)


@dataclass(frozen=True, slots=True, kw_only=True)
class ArcMove(ToolpathEvent):
    start: Pose
    end: Pose
    center: Point3
    plane_normal: Vector3
    sweep_radians: float
    feed_rate: FeedRate
    motion_class: MotionClass = MotionClass.CUTTING
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.ARC

    def __post_init__(self) -> None:
        super(ArcMove, self).__post_init__()
        if not isinstance(self.motion_class, MotionClass) or not isinstance(self.feed_rate, FeedRate):
            raise CamValidationError("Arc process data is invalid")
        radius, _u, _v = validate_arc(self.start, self.end, self.center, self.plane_normal, self.sweep_radians)
        object.__setattr__(self, "sweep_radians", float(self.sweep_radians))
        if radius <= 0.0:
            raise CamInvariantError("Arc radius must be positive")

    @property
    def bounds(self) -> Bounds3:
        return arc_bounds(self.start, self.end, self.center, self.plane_normal, self.sweep_radians)

    @property
    def length(self) -> float:
        return distance(self.start.position, self.center) * abs(self.sweep_radians)


def _movement(start: Pose, end: Pose, motion_class: MotionClass) -> None:
    if not isinstance(start, Pose) or not isinstance(end, Pose) or not isinstance(motion_class, MotionClass):
        raise CamValidationError("Movement payload is invalid")
    if same_pose(start, end):
        raise CamInvariantError("Zero-length movement is not allowed; use a marker")
    if start.position.unit is not end.position.unit:
        raise CamInvariantError("Movement poses require one unit")


@dataclass(frozen=True, slots=True, kw_only=True)
class DwellEvent(ToolpathEvent):
    duration_seconds: float
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.DWELL

    def __post_init__(self) -> None:
        super(DwellEvent, self).__post_init__()
        if isinstance(self.duration_seconds, bool) or not isinstance(self.duration_seconds, (int, float)):
            raise CamValidationError("Dwell duration is invalid")
        value = float(self.duration_seconds)
        if not math.isfinite(value) or value <= 0.0:
            raise CamValidationError("Dwell duration must be finite and positive")
        object.__setattr__(self, "duration_seconds", value)


@dataclass(frozen=True, slots=True, kw_only=True)
class SpindleStateEvent(ToolpathEvent):
    state: SpindleState
    speed: SpindleSpeed | None = None
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.SPINDLE_STATE

    def __post_init__(self) -> None:
        super(SpindleStateEvent, self).__post_init__()
        if not isinstance(self.state, SpindleState):
            raise CamValidationError("Spindle state is invalid")
        if self.state is SpindleState.OFF and self.speed is not None:
            raise CamInvariantError("Stopped spindle cannot carry speed")
        if self.state is not SpindleState.OFF and not isinstance(self.speed, SpindleSpeed):
            raise CamInvariantError("Running spindle requires speed")


@dataclass(frozen=True, slots=True, kw_only=True)
class CoolantStateEvent(ToolpathEvent):
    state: CoolantState
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.COOLANT_STATE

    def __post_init__(self) -> None:
        super(CoolantStateEvent, self).__post_init__()
        if not isinstance(self.state, CoolantState):
            raise CamValidationError("Coolant state is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class FeedModeEvent(ToolpathEvent):
    mode: FeedMode
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.FEED_MODE

    def __post_init__(self) -> None:
        super(FeedModeEvent, self).__post_init__()
        if not isinstance(self.mode, FeedMode):
            raise CamValidationError("Feed mode is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContextEvent(ToolpathEvent):
    tool_assembly_id: ToolAssemblyId
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.TOOL_CONTEXT

    def __post_init__(self) -> None:
        super(ToolContextEvent, self).__post_init__()
        if not isinstance(self.tool_assembly_id, ToolAssemblyId):
            raise CamValidationError("Tool context assembly is invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkerEvent(ToolpathEvent):
    semantic_key: str
    message: str | None = None
    kind: ClassVar[ToolpathEventKind] = ToolpathEventKind.MARKER

    def __post_init__(self) -> None:
        super(MarkerEvent, self).__post_init__()
        if not isinstance(self.semantic_key, str) or not _SEMANTIC_KEY.fullmatch(self.semantic_key):
            raise CamValidationError("Marker semantic key is invalid")
        if self.message is not None and (not isinstance(self.message, str) or not self.message.strip() or len(self.message) > 4096):
            raise CamValidationError("Marker message is invalid")


MovementEvent = RapidMove | LinearMove | ArcMove
AnyToolpathEvent = MovementEvent | DwellEvent | SpindleStateEvent | CoolantStateEvent | FeedModeEvent | ToolContextEvent | MarkerEvent
