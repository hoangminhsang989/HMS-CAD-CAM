"""Pure persistent models for CAD view state stored in an HMS project."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentObjectKey,
    PersistentXcafOccurrenceKey,
)
from hms_cadcam.viewer.models import (
    DisplayMode,
    ObjectAppearance,
    ObjectColor,
    ViewDirection,
)

CAD_VIEW_STATE_VERSION = 1
DEFAULT_DISPLAY_MODE = DisplayMode.SHADED_WITH_EDGES
DEFAULT_VIEW_DIRECTION = ViewDirection.ISOMETRIC


@dataclass(frozen=True, slots=True)
class ObjectAppearanceOverride:
    """Only user-authored XCAF values; ``None`` preserves source appearance."""

    visible: bool | None = None
    color: ObjectColor | None = None
    transparency: float | None = None

    def __post_init__(self) -> None:
        if self.visible is not None and not isinstance(self.visible, bool):
            raise TypeError("CAD visibility override must be bool or None")
        if self.color is not None and not isinstance(self.color, ObjectColor):
            raise TypeError("CAD color override must be ObjectColor or None")
        if self.transparency is not None:
            ObjectAppearance(transparency=self.transparency)

    @property
    def is_empty(self) -> bool:
        return (
            self.visible is None
            and self.color is None
            and self.transparency is None
        )

    def apply(self, source: ObjectAppearance) -> ObjectAppearance:
        """Layer this user override over source/default appearance."""
        if not isinstance(source, ObjectAppearance):
            raise TypeError("Source appearance must be ObjectAppearance")
        return ObjectAppearance(
            visible=source.visible if self.visible is None else self.visible,
            color=source.color if self.color is None else self.color,
            transparency=(
                source.transparency
                if self.transparency is None
                else self.transparency
            ),
        )


@dataclass(frozen=True, slots=True)
class PersistentObjectAppearance:
    """One non-default appearance addressed only by a persistent key."""

    key: PersistentObjectKey
    appearance: ObjectAppearance | ObjectAppearanceOverride

    def __post_init__(self) -> None:
        if not isinstance(
            self.key, (PersistentCadObjectKey, PersistentXcafOccurrenceKey)
        ):
            raise TypeError("Persistent appearance key is invalid")
        if isinstance(self.key, PersistentCadObjectKey):
            if not isinstance(self.appearance, ObjectAppearance):
                raise TypeError("Topology appearance must be ObjectAppearance")
        elif not isinstance(self.appearance, ObjectAppearanceOverride):
            raise TypeError("XCAF appearance must be a user override")


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
                    if _has_persisted_value(item)
                ),
                key=_appearance_sort_key,
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


def _has_persisted_value(item: PersistentObjectAppearance) -> bool:
    if isinstance(item.appearance, ObjectAppearanceOverride):
        return not item.appearance.is_empty
    return item.appearance != ObjectAppearance()


def _appearance_sort_key(item: PersistentObjectAppearance) -> tuple[str, ...]:
    key = item.key
    if isinstance(key, PersistentCadObjectKey):
        return (
            "topology",
            key.topology_path.value,
            key.geometry_kind.value,
        )
    return (
        key.key_scheme.value,
        key.occurrence_path.value,
        key.product_identity.value,
        key.occurrence_role.value,
    )
