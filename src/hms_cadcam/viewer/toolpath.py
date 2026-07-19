"""Native-free lifecycle registry for CAM toolpath presentations."""

from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.cam.domain import OperationId, Point3
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, MotionClass, RapidMove, ToolpathArtifact


@dataclass(frozen=True, slots=True)
class ToolpathSegment:
    start: Point3
    end: Point3
    motion_class: MotionClass
    curved: bool = False


@dataclass(frozen=True, slots=True)
class ToolpathPresentation:
    operation_id: OperationId
    artifact_fingerprint: str
    segments: tuple[ToolpathSegment, ...]
    visible: bool = True
    highlighted: bool = False

    @classmethod
    def from_artifact(cls, artifact: ToolpathArtifact) -> "ToolpathPresentation":
        segments = tuple(ToolpathSegment(event.start.position, event.end.position,
            event.motion_class, isinstance(event, ArcMove)) for event in artifact.events
            if isinstance(event, (RapidMove, LinearMove, ArcMove)))
        return cls(artifact.source_operation_id, artifact.artifact_fingerprint.digest, segments)


class ToolpathPresentationRegistry:
    """Session-only presentations keyed away from CAD/XCAF selection identity."""

    def __init__(self) -> None:
        self._generation: int | None = None
        self._items: dict[OperationId, ToolpathPresentation] = {}

    @property
    def presentations(self) -> tuple[ToolpathPresentation, ...]:
        return tuple(self._items[key] for key in sorted(self._items, key=str))

    def bind_project(self, generation: int | None) -> None:
        if generation != self._generation:
            self._items.clear()
            self._generation = generation

    def display(self, artifact: ToolpathArtifact, *, generation: int) -> bool:
        if generation != self._generation:
            return False
        self._items[artifact.source_operation_id] = ToolpathPresentation.from_artifact(artifact)
        return True

    def select(self, operation_id: OperationId | None) -> None:
        self._items = {key: ToolpathPresentation(value.operation_id, value.artifact_fingerprint,
            value.segments, value.visible, key == operation_id) for key, value in self._items.items()}

    def set_visible(self, operation_id: OperationId, visible: bool) -> None:
        value = self._items[operation_id]
        self._items[operation_id] = ToolpathPresentation(value.operation_id, value.artifact_fingerprint,
                                                          value.segments, visible, value.highlighted)

    def clear(self) -> None:
        self._items.clear()
