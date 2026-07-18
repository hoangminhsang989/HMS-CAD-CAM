"""OCP-independent state and metadata crossing the spike UI boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InteractionMode(str, Enum):
    """Mouse-drag behavior selected by the spike toolbar."""

    SELECT = "select"
    ROTATE = "rotate"
    PAN = "pan"


class SelectionKind(str, Enum):
    """Selectable OCCT topology level."""

    SOLID = "solid"
    FACE = "face"
    EDGE = "edge"


class DisplayMode(str, Enum):
    """Presentation mode for the demo shape."""

    SHADED = "shaded"
    WIREFRAME = "wireframe"
    SHADED_WITH_EDGES = "shaded_with_edges"


class ViewOrientation(str, Enum):
    """Named Z-up camera orientations exposed by the spike."""

    TOP = "top"
    BOTTOM = "bottom"
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    ISOMETRIC = "isometric"


@dataclass(frozen=True, slots=True)
class SelectionMetadata:
    """Serializable information derived from one selected OCCT sub-shape."""

    shape_id: str
    topology: str
    bounds: tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    """Selection result safe to deliver to non-OCCT UI code."""

    count: int
    items: tuple[SelectionMetadata, ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """OCP-free outcome delivered from the importer worker to the UI."""

    success: bool
    source_path: str
    detected_format: str
    shape_id: str | None
    topology_counts: dict[str, int]
    bounding_box: tuple[float, float, float, float, float, float] | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    elapsed_seconds: float


class SelectionModeState:
    """Small testable state holder shared by toolbar and viewer behavior."""

    def __init__(self) -> None:
        self._kind = SelectionKind.SOLID

    @property
    def kind(self) -> SelectionKind:
        """Return the active topology selection level."""
        return self._kind

    def set_kind(self, kind: SelectionKind) -> None:
        """Switch to a supported topology selection level."""
        if not isinstance(kind, SelectionKind):
            raise TypeError("kind must be a SelectionKind")
        self._kind = kind
