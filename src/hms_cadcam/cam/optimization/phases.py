"""Typed codecs for reusable intermediate CAM calculation phases."""

from __future__ import annotations

from typing import Any

from hms_cadcam.cam.domain import (
    ContentFingerprint,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourSegment,
    FacingRegion,
    GeometryFingerprint,
    Point3,
    Vector3,
)
from hms_cadcam.cam.domain.errors import CamValidationError


def facing_region_to_dict(value: FacingRegion) -> dict[str, Any]:
    """Serialize one resolved Facing region without native CAD objects."""
    if not isinstance(value, FacingRegion):
        raise TypeError("Facing region phase value is invalid")
    return {
        "format": "HMS_R249_FACING_REGION",
        "format_version": 1,
        "boundary": [point.to_dict() for point in value.boundary],
        "normal": value.normal.to_dict(),
        "geometry_fingerprint": value.fingerprint.to_dict(),
    }


def facing_region_from_dict(data: dict[str, Any]) -> FacingRegion:
    """Deserialize and validate one complete Facing region phase artifact."""
    fields = {"format", "format_version", "boundary", "normal", "geometry_fingerprint"}
    if (
        not isinstance(data, dict)
        or set(data) != fields
        or data["format"] != "HMS_R249_FACING_REGION"
        or data["format_version"] != 1
        or not isinstance(data["boundary"], list)
    ):
        raise CamValidationError("Facing region phase payload is malformed")
    return FacingRegion(
        tuple(Point3.from_dict(item) for item in data["boundary"]),
        Vector3.from_dict(data["normal"]),
        GeometryFingerprint.from_dict(data["geometry_fingerprint"]),
    )


def contour_loop_to_dict(value: ContourLoop) -> dict[str, Any]:
    if not isinstance(value, ContourLoop):
        raise TypeError("Contour loop phase value is invalid")
    return value.to_dict()


def contour_loop_from_dict(data: dict[str, Any]) -> ContourLoop:
    if not isinstance(data, dict) or set(data) != {"closed", "orientation", "segments"}:
        raise CamValidationError("Contour loop phase payload is malformed")
    if data["closed"] is not True or not isinstance(data["segments"], list):
        raise CamValidationError("Contour loop phase metadata is invalid")
    segments = []
    for raw in data["segments"]:
        if not isinstance(raw, dict) or set(raw) != {
            "kind", "start", "end", "center", "sweep_radians"
        }:
            raise CamValidationError("Contour phase segment is malformed")
        segments.append(ContourSegment(
            ContourCurveKind(raw["kind"]),
            Point3.from_dict(raw["start"]),
            Point3.from_dict(raw["end"]),
            None if raw["center"] is None else Point3.from_dict(raw["center"]),
            raw["sweep_radians"],
        ))
    return ContourLoop(tuple(segments), ContourOrientation(data["orientation"]))


def contour_geometry_to_dict(
    path_loop: ContourLoop,
    path_fingerprint: ContentFingerprint,
    offset: ContourLoop,
    source_polygon: tuple[tuple[float, float], ...],
) -> dict[str, Any]:
    """Serialize the expensive Contour WCS/offset phase, excluding leads/depths."""
    return {
        "format": "HMS_R250_CONTOUR_GEOMETRY",
        "format_version": 1,
        "path_loop": contour_loop_to_dict(path_loop),
        "path_fingerprint": path_fingerprint.to_dict(),
        "offset_loop": contour_loop_to_dict(offset),
        "source_polygon": [[x, y] for x, y in source_polygon],
    }


def contour_geometry_from_dict(
    data: dict[str, Any],
) -> tuple[ContourLoop, ContentFingerprint, ContourLoop, tuple[tuple[float, float], ...]]:
    fields = {"format", "format_version", "path_loop", "path_fingerprint",
              "offset_loop", "source_polygon"}
    if (not isinstance(data, dict) or set(data) != fields
            or data["format"] != "HMS_R250_CONTOUR_GEOMETRY"
            or data["format_version"] != 1 or not isinstance(data["source_polygon"], list)):
        raise CamValidationError("Contour geometry phase payload is malformed")
    polygon = tuple(
        (float(item[0]), float(item[1]))
        for item in data["source_polygon"]
        if isinstance(item, list) and len(item) == 2
    )
    if len(polygon) != len(data["source_polygon"]):
        raise CamValidationError("Contour geometry polygon is malformed")
    return (
        contour_loop_from_dict(data["path_loop"]),
        ContentFingerprint.from_dict(data["path_fingerprint"]),
        contour_loop_from_dict(data["offset_loop"]),
        polygon,
    )


def pocket_geometry_to_dict(
    path_loop: ContourLoop,
    path_fingerprint: ContentFingerprint,
    loops: tuple[ContourLoop, ...],
) -> dict[str, Any]:
    """Serialize Pocket WCS path and expensive inward-offset loops."""
    return {
        "format": "HMS_R250_POCKET_GEOMETRY",
        "format_version": 1,
        "path_loop": contour_loop_to_dict(path_loop),
        "path_fingerprint": path_fingerprint.to_dict(),
        "offset_loops": [contour_loop_to_dict(loop) for loop in loops],
    }


def pocket_geometry_from_dict(
    data: dict[str, Any],
) -> tuple[ContourLoop, ContentFingerprint, tuple[ContourLoop, ...]]:
    fields = {"format", "format_version", "path_loop", "path_fingerprint", "offset_loops"}
    if (not isinstance(data, dict) or set(data) != fields
            or data["format"] != "HMS_R250_POCKET_GEOMETRY"
            or data["format_version"] != 1 or not isinstance(data["offset_loops"], list)
            or not data["offset_loops"]):
        raise CamValidationError("Pocket geometry phase payload is malformed")
    return (
        contour_loop_from_dict(data["path_loop"]),
        ContentFingerprint.from_dict(data["path_fingerprint"]),
        tuple(contour_loop_from_dict(loop) for loop in data["offset_loops"]),
    )
