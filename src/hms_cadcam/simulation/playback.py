"""Controller-neutral timeline and playback state for simulation UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.toolpath.events import (
    ArcMove,
    LinearMove,
    MarkerEvent,
    RapidMove,
    ToolContextEvent,
)
from hms_cadcam.cam.toolpath.model import ToolpathArtifact


class PlaybackState(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class PlaybackEventKind(StrEnum):
    OPERATION = "operation"
    TOOL_CHANGE = "tool_change"
    RAPID = "rapid"
    CUTTING = "cutting"
    WARNING = "warning"
    COLLISION = "collision"
    GOUGE = "gouge"


@dataclass(frozen=True, slots=True)
class PlaybackEvent:
    index: int
    operation_id: str
    toolpath_event_index: int
    kind: PlaybackEventKind
    label: str


@dataclass(frozen=True, slots=True)
class Timeline:
    events: tuple[PlaybackEvent, ...]

    @classmethod
    def from_artifacts(cls, artifacts: tuple[ToolpathArtifact, ...]) -> "Timeline":
        values: list[PlaybackEvent] = []
        for artifact in artifacts:
            operation_id = str(artifact.source_operation_id)
            values.append(PlaybackEvent(len(values), operation_id, -1, PlaybackEventKind.OPERATION, operation_id))
            for event_index, event in enumerate(artifact.events):
                if isinstance(event, RapidMove):
                    kind = PlaybackEventKind.RAPID
                elif isinstance(event, (LinearMove, ArcMove)):
                    kind = PlaybackEventKind.CUTTING
                elif isinstance(event, ToolContextEvent):
                    kind = PlaybackEventKind.TOOL_CHANGE
                elif isinstance(event, MarkerEvent):
                    kind = PlaybackEventKind.WARNING
                else:
                    continue
                values.append(
                    PlaybackEvent(
                        len(values),
                        operation_id,
                        event_index,
                        kind,
                        f"{kind.value} · {event_index}",
                    )
                )
        return cls(tuple(values))


_SPEEDS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, float("inf"))


class PlaybackController:
    """Small deterministic cursor; it never initiates geometry computation."""

    def __init__(self, timeline: Timeline) -> None:
        if not isinstance(timeline, Timeline):
            raise CamValidationError("Simulation timeline is invalid")
        self.timeline = timeline
        self.state = PlaybackState.STOPPED
        self.cursor = 0
        self.speed = 1.0

    def set_speed(self, speed: float) -> None:
        if speed not in _SPEEDS:
            raise CamValidationError("Simulation playback speed is unsupported")
        self.speed = speed

    def play(self) -> None:
        self.state = PlaybackState.PLAYING

    def pause(self) -> None:
        self.state = PlaybackState.PAUSED

    def stop(self) -> None:
        self.state = PlaybackState.STOPPED
        self.cursor = 0

    def step(self, amount: int = 1) -> PlaybackEvent | None:
        if type(amount) is not int:
            raise CamValidationError("Simulation playback step is invalid")
        if not self.timeline.events:
            self.cursor = 0
            return None
        self.cursor = min(max(0, self.cursor + amount), len(self.timeline.events) - 1)
        return self.timeline.events[self.cursor]

    def previous_event(self) -> PlaybackEvent | None:
        return self.step(-1)

    def next_event(self) -> PlaybackEvent | None:
        return self.step(1)
