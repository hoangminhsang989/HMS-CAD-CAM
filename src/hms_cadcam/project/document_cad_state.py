"""JSON codec for standalone-document CAD view state."""

from __future__ import annotations

from uuid import UUID

from hms_cadcam.cad.models import CadGeometryKind, XcafNodeRole
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    PersistentKeyScheme,
    PersistentXcafOccurrenceKey,
    TopologyPath,
    TopologyPathVersion,
    XcafOccurrenceKeyVersion,
    XcafOccurrencePath,
    XcafProductIdentity,
)
from hms_cadcam.project.cad_state import (
    CadViewState,
    ObjectAppearanceOverride,
    PersistentObjectAppearance,
)
from hms_cadcam.viewer.models import (
    DisplayMode,
    ObjectAppearance,
    ObjectColor,
    ViewDirection,
)


def cad_view_state_to_dict(state: CadViewState) -> dict[str, object]:
    """Encode a normalized state without SQLite or runtime object IDs."""
    normalized = state.normalized()
    appearances: list[dict[str, object]] = []
    for item in normalized.object_appearances:
        key = item.key
        appearance = item.appearance
        if isinstance(key, PersistentCadObjectKey):
            if not isinstance(appearance, ObjectAppearance):
                raise TypeError("Topology appearance payload is invalid")
            appearances.append(
                {
                    "kind": "topology",
                    "geometry_kind": key.geometry_kind.value,
                    "topology_path_version": int(key.topology_path_version),
                    "topology_path": key.topology_path.value,
                    "appearance": {
                        "visible": appearance.visible,
                        "color": [
                            appearance.color.red,
                            appearance.color.green,
                            appearance.color.blue,
                        ],
                        "transparency": appearance.transparency,
                    },
                }
            )
            continue
        if not isinstance(key, PersistentXcafOccurrenceKey) or not isinstance(
            appearance, ObjectAppearanceOverride
        ):
            raise TypeError("XCAF appearance payload is invalid")
        appearances.append(
            {
                "kind": "xcaf_occurrence",
                "geometry_kind": key.geometry_kind.value,
                "key_scheme": key.key_scheme.value,
                "key_version": int(key.key_version),
                "occurrence_path": key.occurrence_path.value,
                "product_identity": key.product_identity.value,
                "occurrence_role": key.occurrence_role.value,
                "appearance": {
                    "visible": appearance.visible,
                    "color": (
                        None
                        if appearance.color is None
                        else [
                            appearance.color.red,
                            appearance.color.green,
                            appearance.color.blue,
                        ]
                    ),
                    "transparency": appearance.transparency,
                },
            }
        )
    return {
        "state_version": normalized.state_version,
        "source_id": str(normalized.source_id),
        "display_mode": normalized.display_mode.value,
        "view_direction": normalized.view_direction.value,
        "object_appearances": appearances,
    }


def cad_view_state_from_dict(
    data: object,
    *,
    expected_source_id: UUID,
) -> CadViewState:
    """Strictly decode state and bind it to the current document identity."""
    if not isinstance(data, dict):
        raise TypeError("CAD display state must be an object")
    source_id = UUID(str(data["source_id"]))
    if source_id != expected_source_id:
        raise ValueError("CAD display state belongs to another document")
    raw_items = data.get("object_appearances")
    if not isinstance(raw_items, list):
        raise TypeError("object_appearances must be a list")
    items: list[PersistentObjectAppearance] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("appearance"), dict):
            raise TypeError("Appearance item must be an object")
        raw_appearance = raw["appearance"]
        color_values = raw_appearance.get("color")
        color = (
            None
            if color_values is None
            else ObjectColor(*_color_values(color_values))
        )
        if raw.get("kind") == "topology":
            if color is None or not isinstance(raw_appearance.get("visible"), bool):
                raise TypeError("Topology appearance requires color and visibility")
            key = PersistentCadObjectKey(
                source_id=source_id,
                geometry_kind=CadGeometryKind(str(raw["geometry_kind"])),
                topology_path_version=TopologyPathVersion(
                    int(raw["topology_path_version"])
                ),
                topology_path=TopologyPath(str(raw["topology_path"])),
            )
            appearance: ObjectAppearance | ObjectAppearanceOverride = ObjectAppearance(
                visible=raw_appearance["visible"],
                color=color,
                transparency=float(raw_appearance["transparency"]),
            )
        elif raw.get("kind") == "xcaf_occurrence":
            visible = raw_appearance.get("visible")
            transparency = raw_appearance.get("transparency")
            if visible is not None and not isinstance(visible, bool):
                raise TypeError("XCAF visibility must be bool or null")
            key = PersistentXcafOccurrenceKey(
                source_id=source_id,
                geometry_kind=CadGeometryKind(str(raw["geometry_kind"])),
                key_scheme=PersistentKeyScheme(str(raw["key_scheme"])),
                key_version=XcafOccurrenceKeyVersion(int(raw["key_version"])),
                occurrence_path=XcafOccurrencePath(str(raw["occurrence_path"])),
                product_identity=XcafProductIdentity(str(raw["product_identity"])),
                occurrence_role=XcafNodeRole(str(raw["occurrence_role"])),
            )
            appearance = ObjectAppearanceOverride(
                visible=visible,
                color=color,
                transparency=(
                    None if transparency is None else float(transparency)
                ),
            )
        else:
            raise ValueError("Unsupported persistent appearance kind")
        items.append(PersistentObjectAppearance(key, appearance))
    return CadViewState(
        source_id=source_id,
        state_version=int(data["state_version"]),
        display_mode=DisplayMode(str(data["display_mode"])),
        view_direction=ViewDirection(str(data["view_direction"])),
        object_appearances=tuple(items),
    ).normalized()


def _color_values(value: object) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(component) not in {int, float} for component in value)
    ):
        raise TypeError("CAD color must contain three numbers")
    return float(value[0]), float(value[1]), float(value[2])
