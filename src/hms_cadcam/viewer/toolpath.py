"""Native-free lifecycle registry for CAM toolpath presentations."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hms_cadcam.cam.domain import ArtifactStatus, OperationId, Point3, ToolpathArtifactId
from hms_cadcam.cam.toolpath import (
    ArcMove,
    Bounds3,
    DwellEvent,
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    ToolpathArtifact,
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

    @classmethod
    def from_artifact(cls, artifact: ToolpathArtifact) -> "ToolpathPresentation":
        segments = tuple(ToolpathSegment(event.start.position, event.end.position,
            event.motion_class, isinstance(event, ArcMove), _semantic(event.provenance, event.motion_class)) for event in artifact.events
            if isinstance(event, (RapidMove, LinearMove, ArcMove)))
        annotations: list[ToolpathAnnotation] = []
        current_position = artifact.initial_pose.position
        for event in artifact.events:
            if isinstance(event, (RapidMove, LinearMove, ArcMove)):
                current_position = event.end.position
            elif isinstance(event, DwellEvent) and event.provenance.startswith("drill."):
                annotations.append(ToolpathAnnotation(
                    current_position, "dwell", event.duration_seconds,
                ))
            elif (
                isinstance(event, MarkerEvent)
                and event.semantic_key == "drill.hole_complete"
            ):
                annotations.append(ToolpathAnnotation(
                    current_position, "hole_complete",
                ))
        fingerprint = artifact.artifact_fingerprint
        assert fingerprint is not None
        return cls(
            artifact.source_operation_id,
            artifact.artifact_id,
            fingerprint.digest,
            _strategy_key(artifact),
            _pass_count(artifact),
            artifact.bounds,
            ArtifactStatus.VALID,
            segments,
            tuple(annotations),
        )


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
    ) -> bool:
        if generation != self._generation:
            return False
        operation_id = artifact.source_operation_id
        if request is not None and (
            request.operation_id != operation_id
            or request.project_generation != generation
            or self._requests.get(operation_id) != request.sequence
        ):
            return False
        candidate = ToolpathPresentation.from_artifact(artifact)
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
    }
    matches = tuple(known[prefix] for prefix in sorted(prefixes) if prefix in known)
    return matches[0] if len(matches) == 1 else "unknown"


def _pass_count(artifact: ToolpathArtifact) -> int:
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
