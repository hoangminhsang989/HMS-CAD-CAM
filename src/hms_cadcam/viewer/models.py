"""Pure Python models exchanged across the CAD viewport boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, IntFlag

from hms_cadcam.cad.models import BoundingBox, CadDocumentId, CadObjectId


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
    VERTEX = "vertex"


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
    object_id: CadObjectId | None = None

    @property
    def topology_type(self) -> SelectionMode:
        """Return the selected topology type without native CAD data."""
        return self.topology


@dataclass(frozen=True, slots=True)
class ObjectColor:
    """Normalized RGB color exchanged without Quantity_Color."""

    red: float
    green: float
    blue: float

    def __post_init__(self) -> None:
        values = (self.red, self.green, self.blue)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            raise ValueError("Object color channels must be finite values from 0.0 to 1.0")

    def to_hex(self) -> str:
        """Return a UI-friendly sRGB hex string."""
        return "#{:02X}{:02X}{:02X}".format(
            round(self.red * 255),
            round(self.green * 255),
            round(self.blue * 255),
        )


DEFAULT_OBJECT_COLOR = ObjectColor(0.78, 0.80, 0.84)


@dataclass(frozen=True, slots=True)
class ObjectAppearance:
    """Immutable public display state for one topology-tree object."""

    visible: bool = True
    color: ObjectColor = DEFAULT_OBJECT_COLOR
    transparency: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.visible, bool):
            raise TypeError("Object visibility must be bool")
        if not isinstance(self.color, ObjectColor):
            raise TypeError("Object color must be ObjectColor")
        if not math.isfinite(self.transparency) or not 0.0 <= self.transparency <= 1.0:
            raise ValueError("Object transparency must be from 0.0 to 1.0")
