"""Minimal native-free spatial value objects used by CAM setups."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import CamUnitError, CamValidationError
from hms_cadcam.cam.domain.units import LengthUnit

WCS_ORTHONORMAL_TOLERANCE = 1.0e-9
_WCS_FORMAT = "HMS_CAM_WCS_FRAME"
_WCS_VERSION = 1


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CamValidationError(f"{name} must be finite")
    return normalized


def _strict_payload(
    data: dict[str, Any],
    *,
    format_name: str,
    version: int,
    fields: set[str],
) -> None:
    from hms_cadcam.cam.domain.errors import UnsupportedCamSchemaError

    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != version:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


@dataclass(frozen=True, slots=True)
class Vector3:
    """Finite dimensionless 3D vector."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite(self.x, "Vector x"))
        object.__setattr__(self, "y", _finite(self.y, "Vector y"))
        object.__setattr__(self, "z", _finite(self.z, "Vector z"))

    @property
    def magnitude(self) -> float:
        """Return Euclidean magnitude."""
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def dot(self, other: "Vector3") -> float:
        """Return the scalar product."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vector3") -> "Vector3":
        """Return the right-handed cross product."""
        return Vector3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def to_dict(self) -> dict[str, float]:
        """Serialize this vector."""
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Vector3":
        """Deserialize one exact vector payload."""
        if not isinstance(data, dict) or set(data) != {"x", "y", "z"}:
            raise CamValidationError("Vector payload is malformed")
        return cls(data["x"], data["y"], data["z"])


@dataclass(frozen=True, slots=True)
class Point3:
    """Finite 3D point carrying an explicit length unit."""

    x: float
    y: float
    z: float
    unit: LengthUnit

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite(self.x, "Point x"))
        object.__setattr__(self, "y", _finite(self.y, "Point y"))
        object.__setattr__(self, "z", _finite(self.z, "Point z"))
        if not isinstance(self.unit, LengthUnit):
            raise CamUnitError("Point unit is invalid")

    def to_dict(self) -> dict[str, float | str]:
        """Serialize this point."""
        return {"x": self.x, "y": self.y, "z": self.z, "unit": self.unit.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Point3":
        """Deserialize one exact point payload."""
        if not isinstance(data, dict) or set(data) != {"x", "y", "z", "unit"}:
            raise CamValidationError("Point payload is malformed")
        try:
            unit = LengthUnit(data["unit"])
        except (TypeError, ValueError) as error:
            raise CamUnitError("Point unit payload is invalid") from error
        return cls(data["x"], data["y"], data["z"], unit)


@dataclass(frozen=True, slots=True)
class WcsFrame:
    """Right-handed orthonormal work coordinate frame."""

    origin: Point3
    x_axis: Vector3
    y_axis: Vector3
    z_axis: Vector3
    SERIALIZATION_VERSION: ClassVar[int] = _WCS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Point3):
            raise CamValidationError("WCS origin is invalid")
        if self.origin.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("WCS origin requires a known length unit")
        axes = (self.x_axis, self.y_axis, self.z_axis)
        if not all(isinstance(axis, Vector3) for axis in axes):
            raise CamValidationError("WCS axes are invalid")
        if any(
            not math.isclose(
                axis.magnitude,
                1.0,
                rel_tol=0.0,
                abs_tol=WCS_ORTHONORMAL_TOLERANCE,
            )
            for axis in axes
        ):
            raise CamValidationError("WCS axes must be unit vectors")
        if any(
            abs(first.dot(second)) > WCS_ORTHONORMAL_TOLERANCE
            for first, second in (
                (self.x_axis, self.y_axis),
                (self.x_axis, self.z_axis),
                (self.y_axis, self.z_axis),
            )
        ):
            raise CamValidationError("WCS axes must be orthogonal")
        expected_z = self.x_axis.cross(self.y_axis)
        if any(
            abs(actual - expected) > WCS_ORTHONORMAL_TOLERANCE
            for actual, expected in zip(
                (self.z_axis.x, self.z_axis.y, self.z_axis.z),
                (expected_z.x, expected_z.y, expected_z.z),
                strict=True,
            )
        ):
            raise CamValidationError("WCS must be right-handed with X cross Y equal to Z")

    @classmethod
    def identity(cls, unit: LengthUnit) -> "WcsFrame":
        """Create a right-handed frame at the origin."""
        return cls(
            Point3(0.0, 0.0, 0.0, unit),
            Vector3(1.0, 0.0, 0.0),
            Vector3(0.0, 1.0, 0.0),
            Vector3(0.0, 0.0, 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this frame deterministically."""
        return {
            "format": _WCS_FORMAT,
            "format_version": _WCS_VERSION,
            "origin": self.origin.to_dict(),
            "x_axis": self.x_axis.to_dict(),
            "y_axis": self.y_axis.to_dict(),
            "z_axis": self.z_axis.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WcsFrame":
        """Deserialize one complete work coordinate frame."""
        _strict_payload(
            data,
            format_name=_WCS_FORMAT,
            version=_WCS_VERSION,
            fields={"origin", "x_axis", "y_axis", "z_axis"},
        )
        return cls(
            Point3.from_dict(data["origin"]),
            Vector3.from_dict(data["x_axis"]),
            Vector3.from_dict(data["y_axis"]),
            Vector3.from_dict(data["z_axis"]),
        )


@dataclass(frozen=True, slots=True)
class AffineTransform:
    """Immutable row-major affine 4x4 transform for fixture placement."""

    values: tuple[float, ...]
    translation_unit: LengthUnit

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple) or len(self.values) != 16:
            raise CamValidationError("Affine transform requires 16 values")
        normalized = tuple(_finite(value, "Transform value") for value in self.values)
        if normalized[12:] != (0.0, 0.0, 0.0, 1.0):
            raise CamValidationError("Transform must be affine, not perspective")
        if not isinstance(self.translation_unit, LengthUnit):
            raise CamUnitError("Transform translation unit is invalid")
        if self.translation_unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Transform translation requires a known unit")
        object.__setattr__(self, "values", normalized)

    @classmethod
    def identity(cls, unit: LengthUnit) -> "AffineTransform":
        """Return the identity placement."""
        return cls(
            (
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ),
            unit,
        )

    def to_dict(self) -> dict[str, list[float] | str]:
        """Serialize this affine matrix."""
        return {
            "values": list(self.values),
            "translation_unit": self.translation_unit.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AffineTransform":
        """Deserialize one exact affine matrix payload."""
        if not isinstance(data, dict) or set(data) != {
            "values",
            "translation_unit",
        }:
            raise CamValidationError("Affine transform payload is malformed")
        values = data["values"]
        if not isinstance(values, list):
            raise CamValidationError("Affine transform values must be a list")
        try:
            unit = LengthUnit(data["translation_unit"])
        except (TypeError, ValueError) as error:
            raise CamUnitError("Transform translation unit payload is invalid") from error
        return cls(tuple(values), unit)
