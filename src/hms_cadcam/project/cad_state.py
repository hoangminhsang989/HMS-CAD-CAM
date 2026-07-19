"""Pure persistent models for CAD view state stored in an HMS project."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from hms_cadcam.cad.persistent_keys import PersistentCadObjectKey
from hms_cadcam.viewer.models import (
    DisplayMode,
    ObjectAppearance,
    ViewDirection,
)

CAD_VIEW_STATE_VERSION = 1
DEFAULT_DISPLAY_MODE = DisplayMode.SHADED_WITH_EDGES
DEFAULT_VIEW_DIRECTION = ViewDirection.ISOMETRIC


@dataclass(frozen=True, slots=True)
class PersistentObjectAppearance:
    """One non-default appearance addressed only by a persistent key."""

    key: PersistentCadObjectKey
    appearance: ObjectAppearance

    def __post_init__(self) -> None:
        if not isinstance(self.key, PersistentCadObjectKey):
            raise TypeError("Persistent appearance key is invalid")
        if not isinstance(self.appearance, ObjectAppearance):
            raise TypeError("Persistent object appearance is invalid")


@dataclass(frozen=True, slots=True)
class CadViewState:
    """Versioned display state for one immutable logical CAD source."""

    source_id: UUID
    state_version: int = CAD_VIEW_STATE_VERSION
    display_mode: DisplayMode = DEFAULT_DISPLAY_MODE
    view_direction: ViewDirection = DEFAULT_VIEW_DIRECTION
    object_appearances: tuple[PersistentObjectAppearance, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise TypeError("CAD view-state source_id must be UUID")
        if type(self.state_version) is not int or self.state_version != CAD_VIEW_STATE_VERSION:
            raise ValueError("Unsupported CAD view-state version")
        if not isinstance(self.display_mode, DisplayMode):
            raise TypeError("CAD display mode is invalid")
        if not isinstance(self.view_direction, ViewDirection):
            raise TypeError("CAD view direction is invalid")
        keys = tuple(item.key for item in self.object_appearances)
        if any(item.key.source_id != self.source_id for item in self.object_appearances):
            raise ValueError("Persistent appearance belongs to another source")
        if len(keys) != len(set(keys)):
            raise ValueError("Persistent CAD appearance keys must be unique")

    @property
    def is_default(self) -> bool:
        """Return whether this state requires no SQLite rows."""
        return (
            self.display_mode is DEFAULT_DISPLAY_MODE
            and self.view_direction is DEFAULT_VIEW_DIRECTION
            and not self.object_appearances
        )

    def normalized(self) -> "CadViewState":
        """Drop default appearances and sort rows into deterministic order."""
        items = tuple(
            sorted(
                (
                    item
                    for item in self.object_appearances
                    if item.appearance != ObjectAppearance()
                ),
                key=lambda item: (
                    item.key.topology_path.value,
                    item.key.geometry_kind.value,
                ),
            )
        )
        return CadViewState(
            source_id=self.source_id,
            state_version=self.state_version,
            display_mode=self.display_mode,
            view_direction=self.view_direction,
            object_appearances=items,
        )


def default_cad_view_state(source_id: UUID) -> CadViewState:
    """Return the implicit state used when SQLite contains no row."""
    return CadViewState(source_id=source_id)
