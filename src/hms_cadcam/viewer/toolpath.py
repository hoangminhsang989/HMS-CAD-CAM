"""Native-free lifecycle registry for CAM toolpath presentations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContentFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    OperationId,
    Point3,
    ReamingCoolantMode,
    ReamingRetractPolicy,
    SpindleDirection,
    SpindleSpeed,
    TappingHand,
    TappingSynchronizationPolicy,
    ToolpathArtifactId,
)
from hms_cadcam.cam.toolpath import (
    ArcMove,
    Bounds3,
    CoolantState,
    CoolantStateEvent,
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
    strategy_version: int | None = None
    visible: bool = True
    highlighted: bool = False
    thread_hand: TappingHand | None = None
    tapping_mode: TappingSynchronizationPolicy | None = None
    hole_count: int = 0
    nominal_diameter: Length | None = None
    pitch: Length | None = None
    spindle_speed: SpindleSpeed | None = None
    depth: Length | None = None
    pre_hole_diameter: Length | None = None
    stock_per_side: Length | None = None
    feed_per_revolution: FeedRate | None = None
    feed_per_minute: FeedRate | None = None
    top_z: Length | None = None
    final_depth: Length | None = None
    retract_height: Length | None = None
    clearance_height: Length | None = None
    dwell_seconds: float | None = None
    spindle_direction: SpindleDirection | None = None
    retract_policy: ReamingRetractPolicy | None = None
    coolant_mode: ReamingCoolantMode | None = None
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
                "drill.", "ream.", "tap.",
            )):
                annotations.append(ToolpathAnnotation(
                    current_position, "dwell", event.duration_seconds,
                ))
            elif (
                isinstance(event, SpindleStateEvent)
                and event.provenance.startswith("ream.")
                and event.provenance.endswith(".spindle.begin")
            ):
                annotations.append(ToolpathAnnotation(
                    current_position, "spindle_begin",
                ))
            elif (
                isinstance(event, CoolantStateEvent)
                and event.provenance.startswith("ream.")
                and event.provenance.endswith(".coolant.begin")
            ):
                annotations.append(ToolpathAnnotation(
                    current_position, "coolant_begin",
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
                    "drill.hole_complete", "ream.hole_complete",
                    "tap.hole_complete",
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
            elif isinstance(event, MarkerEvent) and event.semantic_key in {
                "ream.process_begin", "ream.process_end",
            }:
                annotations.append(ToolpathAnnotation(
                    current_position,
                    (
                        "process_begin"
                        if event.semantic_key.endswith("_begin")
                        else "process_end"
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
        reaming = (
            _reaming_metadata(artifact, pass_count)
            if strategy_key == "reaming_v1"
            else _ReamingPresentationMetadata()
        )
        return cls(
            operation_id=artifact.source_operation_id,
            artifact_id=artifact.artifact_id,
            artifact_fingerprint=fingerprint.digest,
            strategy_key=strategy_key,
            strategy_version=1 if strategy_key == "reaming_v1" else None,
            pass_count=pass_count,
            bounds=artifact.bounds,
            artifact_status=ArtifactStatus.VALID,
            segments=segments,
            annotations=tuple(annotations),
            thread_hand=tapping.hand,
            tapping_mode=tapping.mode,
            hole_count=max(tapping.hole_count, reaming.hole_count),
            nominal_diameter=(
                tapping.nominal_diameter or reaming.nominal_diameter
            ),
            pitch=tapping.pitch,
            spindle_speed=tapping.spindle_speed or reaming.spindle_speed,
            depth=tapping.depth,
            pre_hole_diameter=reaming.pre_hole_diameter,
            stock_per_side=reaming.stock_per_side,
            feed_per_revolution=reaming.feed_per_revolution,
            feed_per_minute=reaming.feed_per_minute,
            top_z=reaming.top_z,
            final_depth=reaming.final_depth,
            retract_height=reaming.retract_height,
            clearance_height=reaming.clearance_height,
            dwell_seconds=reaming.dwell_seconds,
            spindle_direction=reaming.spindle_direction,
            retract_policy=reaming.retract_policy,
            coolant_mode=reaming.coolant_mode,
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
class _ReamingPresentationMetadata:
    hole_count: int = 0
    nominal_diameter: Length | None = None
    pre_hole_diameter: Length | None = None
    stock_per_side: Length | None = None
    feed_per_revolution: FeedRate | None = None
    feed_per_minute: FeedRate | None = None
    spindle_speed: SpindleSpeed | None = None
    top_z: Length | None = None
    final_depth: Length | None = None
    retract_height: Length | None = None
    clearance_height: Length | None = None
    dwell_seconds: float | None = None
    spindle_direction: SpindleDirection | None = None
    retract_policy: ReamingRetractPolicy | None = None
    coolant_mode: ReamingCoolantMode | None = None


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
    is_reaming = provenance.startswith("ream.")
    is_tapping = provenance.startswith("tap.")
    if is_reaming:
        if provenance.endswith(".approach"):
            return "reaming_approach"
        if provenance.endswith(".descent"):
            return "reaming_descent"
        if provenance.endswith(".controlled_retract"):
            return "controlled_retract"
        if provenance.endswith(".final_retract"):
            return "final_retract"
        if provenance.endswith(".rapid"):
            return "rapid"
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
        "ream": "reaming_v1",
        "tap": "tapping_v1",
    }
    matches = tuple(known[prefix] for prefix in sorted(prefixes) if prefix in known)
    return matches[0] if len(matches) == 1 else "unknown"


def _pass_count(artifact: ToolpathArtifact) -> int:
    if any(event.provenance.startswith("ream.") for event in artifact.events):
        return sum(
            isinstance(event, MarkerEvent)
            and event.semantic_key == "ream.hole_complete"
            for event in artifact.events
        )
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


def _reaming_metadata(
    artifact: ToolpathArtifact,
    pass_count: int,
) -> _ReamingPresentationMetadata:
    begin = tuple(
        event for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "ream.process_begin"
    )
    end = tuple(
        event for event in artifact.events
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "ream.process_end"
    )
    if pass_count <= 0 or len(begin) != pass_count or len(end) != pass_count:
        raise ValueError("Reaming process markers are incomplete")
    payloads = tuple(dict(event.metadata) for event in (*begin, *end))
    first = payloads[0]
    if any(payload != first for payload in payloads[1:]):
        raise ValueError("Reaming process metadata is inconsistent")
    required_fields = {
        "clearance_height", "coolant", "dwell_seconds",
        "feed_per_revolution", "feed_unit", "final_depth", "format",
        "length_unit", "metadata_version", "nominal_diameter",
        "pre_hole_diameter", "retract_policy", "retract_height", "rpm",
        "spindle_direction", "stock_per_side", "strategy_key",
        "strategy_version", "top_z",
    }
    if set(first) != required_fields:
        raise ValueError("Reaming process metadata fields are unsupported")
    if first.get("format") != "hms_reaming_process_v1":
        raise ValueError("Reaming process metadata format is unsupported")
    if first.get("metadata_version") != "1":
        raise ValueError("Reaming presentation metadata version is unsupported")
    if (
        first.get("strategy_key") != "reaming_v1"
        or first.get("strategy_version") != "1"
    ):
        raise ValueError("Reaming strategy metadata is unsupported")
    try:
        unit = LengthUnit(first["length_unit"])
        if unit not in {LengthUnit.MM, LengthUnit.INCH}:
            raise ValueError("unknown Reaming length unit")
        expected_feed_unit = (
            FeedUnit.MM_PER_REVOLUTION
            if unit is LengthUnit.MM
            else FeedUnit.INCH_PER_REVOLUTION
        )
        expected_minute_unit = (
            FeedUnit.MM_PER_MINUTE
            if unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        feed_unit = FeedUnit(first["feed_unit"])
        if feed_unit is not expected_feed_unit:
            raise ValueError("Reaming feed basis or unit is invalid")
        nominal = Length(float(first["nominal_diameter"]), unit)
        pre_hole = Length(float(first["pre_hole_diameter"]), unit)
        stock = Length(float(first["stock_per_side"]), unit)
        feed_per_revolution = FeedRate(
            float(first["feed_per_revolution"]), feed_unit
        )
        spindle_speed = SpindleSpeed(float(first["rpm"]))
        top_z = Length(float(first["top_z"]), unit)
        final_depth = Length(float(first["final_depth"]), unit)
        retract_height = Length(float(first["retract_height"]), unit)
        clearance_height = Length(float(first["clearance_height"]), unit)
        dwell_seconds = float(first["dwell_seconds"])
        direction = SpindleDirection(first["spindle_direction"])
        retract_policy = ReamingRetractPolicy(first["retract_policy"])
        coolant_mode = ReamingCoolantMode(first["coolant"])
        feed_per_minute = FeedRate(
            feed_per_revolution.value * spindle_speed.value,
            expected_minute_unit,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Reaming presentation metadata is invalid") from error
    if (
        nominal.value <= 0.0
        or pre_hole.value <= 0.0
        or pre_hole.value >= nominal.value
        or stock.value <= 0.0
        or not math.isclose(
            stock.value,
            (nominal.value - pre_hole.value) / 2.0,
            rel_tol=0.0,
            abs_tol=1.0e-8,
        )
        or final_depth.value >= top_z.value
        or retract_height.value <= top_z.value
        or clearance_height.value <= retract_height.value
        or not math.isfinite(dwell_seconds)
        or dwell_seconds < 0.0
        or retract_policy is not ReamingRetractPolicy.CONTROLLED_FEED
    ):
        raise ValueError("Reaming presentation metadata is unsafe")
    _validate_reaming_event_stream(
        artifact,
        pass_count,
        unit=unit,
        feed_per_revolution=feed_per_revolution,
        spindle_speed=spindle_speed,
        final_depth=final_depth.value,
        retract_height=retract_height.value,
        clearance_height=clearance_height.value,
        dwell_seconds=dwell_seconds,
        direction=direction,
        coolant_mode=coolant_mode,
    )
    return _ReamingPresentationMetadata(
        hole_count=pass_count,
        nominal_diameter=nominal,
        pre_hole_diameter=pre_hole,
        stock_per_side=stock,
        feed_per_revolution=feed_per_revolution,
        feed_per_minute=feed_per_minute,
        spindle_speed=spindle_speed,
        top_z=top_z,
        final_depth=final_depth,
        retract_height=retract_height,
        clearance_height=clearance_height,
        dwell_seconds=dwell_seconds,
        spindle_direction=direction,
        retract_policy=retract_policy,
        coolant_mode=coolant_mode,
    )


def _validate_reaming_event_stream(
    artifact: ToolpathArtifact,
    pass_count: int,
    *,
    unit: LengthUnit,
    feed_per_revolution: FeedRate,
    spindle_speed: SpindleSpeed,
    final_depth: float,
    retract_height: float,
    clearance_height: float,
    dwell_seconds: float,
    direction: SpindleDirection,
    coolant_mode: ReamingCoolantMode,
) -> None:
    pattern = re.compile(r"^ream\.hole\.(\d+)\.(.+)$")
    grouped: dict[int, list[tuple[str, object]]] = {}
    hole_order: list[int] = []
    for event in artifact.events:
        if not event.provenance.startswith("ream."):
            continue
        match = pattern.fullmatch(event.provenance)
        if match is None:
            raise ValueError("Reaming event provenance is unsupported")
        hole_index = int(match.group(1))
        if not hole_order or hole_order[-1] != hole_index:
            hole_order.append(hole_index)
        grouped.setdefault(hole_index, []).append((match.group(2), event))
    if set(grouped) != set(range(pass_count)):
        raise ValueError("Reaming hole event indices are incomplete")
    if tuple(hole_order) != tuple(range(pass_count)):
        raise ValueError("Reaming canonical hole order is invalid")
    expected_spindle = (
        SpindleState.CLOCKWISE
        if direction is SpindleDirection.CLOCKWISE
        else SpindleState.COUNTERCLOCKWISE
    )
    expected_coolant = {
        ReamingCoolantMode.OFF: CoolantState.OFF,
        ReamingCoolantMode.FLOOD: CoolantState.FLOOD,
        ReamingCoolantMode.MIST: CoolantState.MIST,
        ReamingCoolantMode.THROUGH_TOOL: CoolantState.THROUGH_TOOL,
    }[coolant_mode]
    for hole_index in range(pass_count):
        entries = grouped[hole_index]
        expected_suffixes = [
            "rapid", "approach", "process.begin", "spindle.begin",
        ]
        if coolant_mode is not ReamingCoolantMode.OFF:
            expected_suffixes.append("coolant.begin")
        expected_suffixes.append("descent")
        if dwell_seconds > 0.0:
            expected_suffixes.append("dwell")
        expected_suffixes.extend((
            "controlled_retract", "complete", "final_retract",
        ))
        if coolant_mode is not ReamingCoolantMode.OFF:
            expected_suffixes.append("coolant.end")
        expected_suffixes.extend(("spindle.end", "process.end"))
        if [suffix for suffix, _event in entries] != expected_suffixes:
            raise ValueError("Reaming event ordering is unsafe or incomplete")
        events = dict(entries)
        rapid = events["rapid"]
        approach = events["approach"]
        descent = events["descent"]
        retract = events["controlled_retract"]
        final_rapid = events["final_retract"]
        if (
            not isinstance(rapid, RapidMove)
            or not isinstance(approach, RapidMove)
            or not isinstance(descent, LinearMove)
            or not isinstance(retract, LinearMove)
            or not isinstance(final_rapid, RapidMove)
            or rapid.motion_class is not MotionClass.NON_CUTTING
            or approach.motion_class is not MotionClass.LINK
            or descent.motion_class is not MotionClass.CUTTING
            or retract.motion_class is not MotionClass.RETRACT
            or final_rapid.motion_class is not MotionClass.NON_CUTTING
            or descent.feed_rate != feed_per_revolution
            or retract.feed_rate != feed_per_revolution
        ):
            raise ValueError("Reaming movement classification is invalid")
        movements = (rapid, approach, descent, retract, final_rapid)
        if any(
            move.start.position.unit is not unit
            or move.end.position.unit is not unit
            for move in movements
        ):
            raise ValueError("Reaming movement unit is invalid")
        if any(
            isinstance(move, RapidMove)
            and move.start.position.z < retract_height - 1.0e-8
            for move in movements
        ):
            raise ValueError("Reaming rapid movement starts below retract height")
        x, y = approach.end.position.x, approach.end.position.y
        positions_match = all(
            math.isclose(value, expected, rel_tol=0.0, abs_tol=1.0e-8)
            for value, expected in (
                (rapid.end.position.x, x), (rapid.end.position.y, y),
                (rapid.end.position.z, clearance_height),
                (approach.start.position.x, x),
                (approach.start.position.y, y),
                (approach.start.position.z, clearance_height),
                (approach.end.position.z, retract_height),
                (descent.start.position.x, x), (descent.start.position.y, y),
                (descent.start.position.z, retract_height),
                (descent.end.position.x, x), (descent.end.position.y, y),
                (descent.end.position.z, final_depth),
                (retract.start.position.x, x), (retract.start.position.y, y),
                (retract.start.position.z, final_depth),
                (retract.end.position.x, x), (retract.end.position.y, y),
                (retract.end.position.z, retract_height),
                (final_rapid.start.position.x, x),
                (final_rapid.start.position.y, y),
                (final_rapid.start.position.z, retract_height),
                (final_rapid.end.position.x, x),
                (final_rapid.end.position.y, y),
                (final_rapid.end.position.z, clearance_height),
            )
        )
        if not positions_match:
            raise ValueError("Reaming movement geometry conflicts with metadata")
        process_begin = events["process.begin"]
        complete = events["complete"]
        process_end = events["process.end"]
        spindle_begin = events["spindle.begin"]
        spindle_end = events["spindle.end"]
        if (
            not isinstance(process_begin, MarkerEvent)
            or process_begin.semantic_key != "ream.process_begin"
            or not isinstance(complete, MarkerEvent)
            or complete.semantic_key != "ream.hole_complete"
            or not isinstance(process_end, MarkerEvent)
            or process_end.semantic_key != "ream.process_end"
            or not isinstance(spindle_begin, SpindleStateEvent)
            or spindle_begin.state is not expected_spindle
            or spindle_begin.speed != spindle_speed
            or not isinstance(spindle_end, SpindleStateEvent)
            or spindle_end.state is not SpindleState.OFF
        ):
            raise ValueError("Reaming process state sequence is invalid")
        if dwell_seconds > 0.0:
            dwell = events["dwell"]
            if (
                not isinstance(dwell, DwellEvent)
                or not math.isclose(
                    dwell.duration_seconds,
                    dwell_seconds,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            ):
                raise ValueError("Reaming dwell event conflicts with metadata")
        if coolant_mode is not ReamingCoolantMode.OFF:
            coolant_begin = events["coolant.begin"]
            coolant_end = events["coolant.end"]
            if (
                not isinstance(coolant_begin, CoolantStateEvent)
                or coolant_begin.state is not expected_coolant
                or not isinstance(coolant_end, CoolantStateEvent)
                or coolant_end.state is not CoolantState.OFF
            ):
                raise ValueError("Reaming coolant state sequence is invalid")
