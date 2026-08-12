"""Typed codecs for reusable intermediate CAM calculation phases."""

from __future__ import annotations

from typing import Any

from hms_cadcam.cam.domain import FacingRegion, GeometryFingerprint, Point3, Vector3
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
