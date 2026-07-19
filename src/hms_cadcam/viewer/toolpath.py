"""Native-free lifecycle registry for CAM toolpath presentations."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContentFingerprint,
    Length,
    LengthUnit,
    OperationId,
    Point3,
    SpindleSpeed,
    TappingHand,
    TappingSynchronizationPolicy,
    ToolpathArtifactId,
)
from hms_cadcam.cam.toolpath import (
    ArcMove,
    Bounds3,
    DwellEvent,
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    SpindleState,
    SpindleStateEvent,
    ToolpathArtifact,
    ToolpathStatistics,
)


@dataclass(frozen=True, slots=True)
class ToolpathSegment:
    start: Point3
    end: Point3
    motion_class: MotionClass
    curved: bool = False
    semantic: str = "motion"


@dataclass(frozen=True, slots=True)
class ToolpathAnnotation:
    """Zero-displacement semantic point derived from controller-neutral IR."""

    position: Point3
    semantic: str
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ToolpathPresentation:
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: str
    strategy_key: str
    pass_count: int
    bounds: Bounds3
    artifact_status: ArtifactStatus
    segments: tuple[ToolpathSegment, ...]
    annotations: tuple[ToolpathAnnotation, ...] = ()
    visible: bool = True
    highlighted: bool = False
    thread_hand: TappingHand | None = None
    tapping_mode: TappingSynchronizationPolicy | None = None
    hole_count: int = 0
    nominal_diameter: Length | None = None
    pitch: Length | None = None
    spindle_speed: SpindleSpeed | None = None
    depth: Length | None = None
    statistics: ToolpathStatistics | None = None

    @classmethod
    def from_artifact(cls, artifact: ToolpathArtifact) -> "ToolpathPresentation":
        strategy_key = _strategy_key(artifact)
        segments = tuple(ToolpathSegment(event.start.position, event.end.position,
            event.motion_class, isinstance(event, ArcMove), _semantic(event.provenance, event.motion_class)) for event in artifact.events
            if isinstance(event, (RapidMove, LinearMove, ArcMove)))
        annotations: list[ToolpathAnnotation] = []
        current_position = artifact.initial_pose.position
        for event in artifact.events:
            if isinstance(event, (RapidMove, LinearMove, ArcMove)):
                current_position = event.end.position
            elif isinstance(event, DwellEvent) and event.provenance.startswith((
                "drill.", "tap.",
            )):
                annotations.append(ToolpathAnnotation(
                    current_position, "dwell", event.duration_seconds,
                ))
            elif (
                isinstance(event, SpindleStateEvent)
                and event.provenance.startswith("tap.")
                and event.provenance.endswith(".spindle.reversal")
            ):
                annotations.append(ToolpathAnnotation(
                    current_position, "spindle_reversal",
                ))
            elif (
                isinstance(event, MarkerEvent)
                and event.semantic_key in {
                    "drill.hole_complete", "tap.hole_complete",
                }
            ):
                annotations.append(ToolpathAnnotation(
                    current_position, "hole_complete",
                ))
            elif isinstance(event, MarkerEvent) and event.semantic_key in {
                "tap.synchronization_begin", "tap.synchronization_end",
            }:
                annotations.append(ToolpathAnnotation(
                    current_position,
                    (
                        "synchronization_begin"
                        if event.semantic_key.endswith("_begin")
                        else "synchronization_end"
                    ),
                ))
        fingerprint = artifact.artifact_fingerprint
        assert fingerprint is not None
        pass_count = _pass_count(artifact)
        tapping = (
            _tapping_metadata(artifact, pass_count)
            if strategy_key == "tapping_v1"
            else _TappingPresentationMetadata()
        )
        return cls(
            operation_id=artifact.source_operation_id,
            artifact_id=artifact.artifact_id,
            artifact_fingerprint=fingerprint.digest,
            strategy_key=strategy_key,
            pass_count=pass_count,
            bounds=artifact.bounds,
            artifact_status=ArtifactStatus.VALID,
            segments=segments,
            annotations=tuple(annotations),
            thread_hand=tapping.hand,
            tapping_mode=tapping.mode,
            hole_count=tapping.hole_count,
            nominal_diameter=tapping.nominal_diameter,
            pitch=tapping.pitch,
            spindle_speed=tapping.spindle_speed,
            depth=tapping.depth,
            statistics=artifact.statistics,
        )


@dataclass(frozen=True, slots=True)
class _TappingPresentationMetadata:
    hand: TappingHand | None = None
    mode: TappingSynchronizationPolicy | None = None
    hole_count: int = 0
    nominal_diameter: Length | None = None
    pitch: Length | None = None
    spindle_speed: SpindleSpeed | None = None
    depth: Length | None = None


@dataclass(frozen=True, slots=True)
class ToolpathDisplayRequest:
    """Session-only identity used to reject an obsolete display callback."""

    operation_id: OperationId
    project_generation: int
    sequence: int


class ToolpathPresentationRegistry:
    """Session-only presentations keyed away from CAD/XCAF selection identity."""

    def __init__(self) -> None:
        self._generation: int | None = None
        self._items: dict[OperationId, ToolpathPresentation] = {}
        self._requests: dict[OperationId, int] = {}
        self._sequence = 0

    @property
    def presentations(self) -> tuple[ToolpathPresentation, ...]:
        return tuple(self._items[key] for key in sorted(self._items, key=str))

    def bind_project(self, generation: int | None) -> None:
        if generation != self._generation:
            self.clear()
            self._generation = generation

    def request_display(
        self,
        operation_id: OperationId,
        *,
        generation: int,
    ) -> ToolpathDisplayRequest | None:
        """Register the newest asynchronous display request for one operation."""
        if generation != self._generation:
            return None
        self._sequence += 1
        self._requests[operation_id] = self._sequence
        return ToolpathDisplayRequest(operation_id, generation, self._sequence)

    def display(
        self,
        artifact: ToolpathArtifact,
        *,
        generation: int,
        request: ToolpathDisplayRequest | None = None,
        operation_exists: bool = True,
        operation_enabled: bool = True,
        expected_strategy_key: str | None = None,
        expected_artifact_fingerprint: ContentFingerprint | str | None = None,
    ) -> bool:
        if (
            generation != self._generation
            or not operation_exists
            or not operation_enabled
        ):
            return False
        operation_id = artifact.source_operation_id
        if request is not None and (
            request.operation_id != operation_id
            or request.project_generation != generation
            or self._requests.get(operation_id) != request.sequence
        ):
            return False
        fingerprint = artifact.artifact_fingerprint
        assert fingerprint is not None
        if isinstance(expected_artifact_fingerprint, ContentFingerprint):
            expected_digest = expected_artifact_fingerprint.digest
        else:
            expected_digest = expected_artifact_fingerprint
        if expected_digest is not None and fingerprint.digest != expected_digest:
            return False
        candidate = ToolpathPresentation.from_artifact(artifact)
        if (
            expected_strategy_key is not None
            and candidate.strategy_key != expected_strategy_key
        ):
            return False
        previous = self._items.get(operation_id)
        if previous is not None:
            candidate = replace(
                candidate,
                visible=previous.visible,
                highlighted=previous.highlighted,
            )
        if request is None:
            self._sequence += 1
            self._requests[operation_id] = self._sequence
        self._items[operation_id] = candidate
        return True

    def select(self, operation_id: OperationId | None) -> None:
        self._items = {
            key: replace(value, highlighted=key == operation_id)
            for key, value in self._items.items()
        }

    def set_visible(self, operation_id: OperationId, visible: bool) -> None:
        value = self._items[operation_id]
        self._items[operation_id] = replace(value, visible=visible)

    def remove(self, operation_id: OperationId) -> None:
        """Remove one operation and invalidate callbacks issued for it."""
        self._sequence += 1
        self._requests[operation_id] = self._sequence
        self._items.pop(operation_id, None)

    def clear(self) -> None:
        self._sequence += 1
        self._items.clear()
        self._requests.clear()


def _semantic(provenance: str, motion_class: MotionClass) -> str:
    is_pocket = provenance.startswith("pocket.")
    is_drilling = provenance.startswith("drill.")
    is_tapping = provenance.startswith("tap.")
    if is_tapping:
        if provenance.endswith(".approach"):
            return "approach"
        if provenance.endswith(".descent"):
            return "synchronized_descent"
        if provenance.endswith(".synchronized_retract"):
            return "synchronized_retract"
        if provenance.endswith(".final_retract"):
            return "final_retract"
        if provenance.endswith(".rapid"):
            return "rapid"
    if is_drilling:
        if provenance.endswith(".approach"):
            return "approach"
        if provenance.endswith(".resume"):
            return "peck_resume"
        if provenance.endswith(".plunge"):
            return "plunge"
        if provenance.endswith(".retract"):
            return "retract"
        if motion_class is MotionClass.NON_CUTTING:
            return "rapid"
    if "lead_in" in provenance:
        return "lead_in"
    if "lead_out" in provenance:
        return "lead_out"
    if "plunge" in provenance or "approach" in provenance:
        return "plunge" if is_pocket else "plunge_link"
    if is_pocket and motion_class is MotionClass.CUTTING:
        return "pocket_cutting"
    return {
        MotionClass.NON_CUTTING: "rapid",
        MotionClass.CUTTING: "cutting",
        MotionClass.LINK: "link",
        MotionClass.RETRACT: "retract",
    }[motion_class]


def _strategy_key(artifact: ToolpathArtifact) -> str:
    prefixes = {event.provenance.split(".", 1)[0] for event in artifact.events
                if getattr(event, "provenance", "")}
    known = {
        "contour": "contour_2d",
        "drill": "drilling_v1",
        "facing": "facing_2_5d",
        "pocket": "pocket_2_5d",
        "tap": "tapping_v1",
    }
    matches = tuple(known[prefix] for prefix in sorted(prefixes) if prefix in known)
    return matches[0] if len(matches) == 1 else "unknown"


def _pass_count(artifact: ToolpathArtifact) -> int:
    if any(event.provenance.startswith("tap.") for event in artifact.events):
        return sum(
            isinstance(event, MarkerEvent)
            and event.semantic_key == "tap.hole_complete"
            for event in artifact.events
        )
    if any(event.provenance.startswith("drill.") for event in artifact.events):
        return sum(
            isinstance(event, MarkerEvent)
            and event.semantic_key == "drill.hole_complete"
            for event in artifact.events
        )
    patterns = (
        re.compile(r"^pocket\.depth\.(\d+)\.loop\.(\d+)\.segment\."),
        re.compile(r"^contour\.pass\.(\d+)\."),
        re.compile(r"^facing\.level\.(\d+)\.lane\.(\d+)\."),
    )
    for pattern in patterns:
        passes = {
            match.groups()
            for event in artifact.events
            if getattr(event, "motion_class", None) is MotionClass.CUTTING
            if (match := pattern.match(event.provenance)) is not None
        }
        if passes:
            return len(passes)
    return sum(1 for event in artifact.events
               if getattr(event, "motion_class", None) is MotionClass.CUTTING)


def _tapping_metadata(
    artifact: ToolpathArtifact,
    pass_count: int,
) -> _TappingPresentationMetadata:
    begin = tuple(
        event for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "tap.synchronization_begin"
    )
    end = tuple(
        event for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "tap.synchronization_end"
    )
    if not begin or len(begin) != len(end) or len(begin) != pass_count:
        raise ValueError("Tapping synchronization markers are incomplete")
    payloads = tuple(dict(event.metadata) for event in (*begin, *end))
    first = payloads[0]
    if any(payload != first for payload in payloads[1:]):
        raise ValueError("Tapping synchronization metadata is inconsistent")
    if first.get("format") != "hms_tapping_sync_v1":
        raise ValueError("Tapping synchronization metadata format is unsupported")
    if first.get("metadata_version", "1") != "1":
        raise ValueError("Tapping presentation metadata version is unsupported")
    try:
        unit = LengthUnit(first["pitch_unit"])
        if unit is LengthUnit.UNKNOWN:
            raise ValueError("unknown tapping metadata unit")
        pitch = Length(float(first["pitch"]), unit)
        if pitch.value <= 0.0:
            raise ValueError("invalid tapping pitch")
        mode = TappingSynchronizationPolicy(first["policy"])
        nominal_diameter = (
            Length(float(first["nominal_diameter"]), unit)
            if "nominal_diameter" in first
            else None
        )
        depth = (
            Length(float(first["thread_depth"]), unit)
            if "thread_depth" in first
            else None
        )
        if nominal_diameter is not None and nominal_diameter.value <= 0.0:
            raise ValueError("invalid tapping nominal diameter")
        if depth is not None and depth.value <= 0.0:
            raise ValueError("invalid tapping depth")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Tapping presentation metadata is invalid") from error
    cutting_events = tuple(
        event for event in artifact.events
        if isinstance(event, SpindleStateEvent)
        and event.provenance.endswith(".spindle.cutting")
    )
    reversal_events = tuple(
        event for event in artifact.events
        if isinstance(event, SpindleStateEvent)
        and event.provenance.endswith(".spindle.reversal")
    )
    if (
        len(cutting_events) != pass_count
        or len(reversal_events) != pass_count
    ):
        raise ValueError("Tapping spindle process events are incomplete")
    cutting = cutting_events[0]
    try:
        hand = (
            TappingHand(first["hand"])
            if "hand" in first
            else (
                TappingHand.RIGHT_HAND_TAP
                if cutting.state is SpindleState.CLOCKWISE
                else TappingHand.LEFT_HAND_TAP
            )
        )
        spindle_speed = (
            SpindleSpeed(float(first["rpm"]))
            if "rpm" in first
            else cutting.speed
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Tapping hand or spindle metadata is invalid") from error
    if spindle_speed is None:
        raise ValueError("Tapping spindle process state is missing")
    expected_cutting_state = (
        SpindleState.CLOCKWISE
        if hand is TappingHand.RIGHT_HAND_TAP
        else SpindleState.COUNTERCLOCKWISE
    )
    expected_reversal_state = (
        SpindleState.COUNTERCLOCKWISE
        if hand is TappingHand.RIGHT_HAND_TAP
        else SpindleState.CLOCKWISE
    )
    if any(
        event.state is not expected_cutting_state
        or event.speed != spindle_speed
        for event in cutting_events
    ) or any(
        event.state is not expected_reversal_state
        or event.speed != spindle_speed
        for event in reversal_events
    ):
        raise ValueError("Tapping spindle metadata conflicts with the event stream")
    return _TappingPresentationMetadata(
        hand,
        mode,
        pass_count,
        nominal_diameter,
        pitch,
        spindle_speed,
        depth,
    )
