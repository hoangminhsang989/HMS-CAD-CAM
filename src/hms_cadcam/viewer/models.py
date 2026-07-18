"""Pure Python models exchanged across the CAD viewport boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntFlag

from hms_cadcam.cad.models import BoundingBox, CadDocumentId


class ViewDirection(str, Enum):
    """Standard Z-up camera directions."""

    TOP = "top"
    BOTTOM = "bottom"
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    ISOMETRIC = "isometric"


class DisplayMode(str, Enum):
    """Supported CAD presentation styles."""

    SHADED = "shaded"
    WIREFRAME = "wireframe"
    SHADED_WITH_EDGES = "shaded_with_edges"


class SelectionMode(str, Enum):
    """Topology levels exposed for interactive selection."""

    SOLID = "solid"
    FACE = "face"
    EDGE = "edge"


class MouseButton(str, Enum):
    """Toolkit-neutral mouse buttons passed to viewport backends."""

    NONE = "none"
    LEFT = "left"
    MIDDLE = "middle"
    RIGHT = "right"


class KeyboardModifier(IntFlag):
    """Toolkit-neutral keyboard modifiers used during mouse input."""

    NONE = 0
    SHIFT = 1
    CONTROL = 2
    ALT = 4


@dataclass(frozen=True, slots=True)
class ViewportStatus:
    """Backend state suitable for diagnostics and an unavailable overlay."""

    available: bool
    initialized: bool
    backend: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionMetadata:
    """Stable OCP-free metadata for one selected topology item."""

    document_id: CadDocumentId
    selection_id: str
    topology: SelectionMode
    bounding_box: BoundingBox
