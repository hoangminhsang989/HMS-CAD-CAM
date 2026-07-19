"""Small explicit quantity model for CAM inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamUnitError

_MM_PER_INCH = 25.4


class LengthUnit(StrEnum):
    """Length units known by the CAM domain."""

    UNKNOWN = "unknown"
    MM = "mm"
    INCH = "inch"


class AngleUnit(StrEnum):
    """Supported angular units."""

    DEGREE = "degree"
    RADIAN = "radian"


class FeedUnit(StrEnum):
    """Linear feed units with explicit time semantics."""

    MM_PER_MINUTE = "mm_per_minute"
    INCH_PER_MINUTE = "inch_per_minute"


class SpindleSpeedUnit(StrEnum):
    """Supported rotational-speed units."""

    RPM = "rpm"


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamUnitError(f"{name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CamUnitError(f"{name} must be finite")
    return normalized


@dataclass(frozen=True, slots=True)
class Length:
    """A signed length whose unit is never inferred."""

    value: float
    unit: LengthUnit

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _finite(self.value, "Length"))
        if not isinstance(self.unit, LengthUnit):
            raise CamUnitError("Length unit is invalid")

    def to(self, unit: LengthUnit) -> "Length":
        """Convert to a known target unit."""
        if not isinstance(unit, LengthUnit) or LengthUnit.UNKNOWN in (self.unit, unit):
            raise CamUnitError("Unknown length units cannot be converted")
        if unit is self.unit:
            return self
        factor = _MM_PER_INCH if self.unit is LengthUnit.INCH else 1.0 / _MM_PER_INCH
        return Length(self.value * factor, unit)


@dataclass(frozen=True, slots=True)
class Angle:
    """A finite angle with explicit conversion."""

    value: float
    unit: AngleUnit

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _finite(self.value, "Angle"))
        if not isinstance(self.unit, AngleUnit):
            raise CamUnitError("Angle unit is invalid")

    def to(self, unit: AngleUnit) -> "Angle":
        """Convert between degrees and radians."""
        if not isinstance(unit, AngleUnit):
            raise CamUnitError("Angle unit is invalid")
        if unit is self.unit:
            return self
        value = math.degrees(self.value) if unit is AngleUnit.DEGREE else math.radians(self.value)
        return Angle(value, unit)


@dataclass(frozen=True, slots=True)
class FeedRate:
    """A positive linear distance per minute."""

    value: float
    unit: FeedUnit

    def __post_init__(self) -> None:
        value = _finite(self.value, "Feed rate")
        if value <= 0.0:
            raise CamUnitError("Feed rate must be greater than zero")
        object.__setattr__(self, "value", value)
        if not isinstance(self.unit, FeedUnit):
            raise CamUnitError("Feed unit is invalid")

    def to(self, unit: FeedUnit) -> "FeedRate":
        """Convert to another distance-per-minute unit."""
        if not isinstance(unit, FeedUnit):
            raise CamUnitError("Feed unit is invalid")
        if unit is self.unit:
            return self
        factor = (
            _MM_PER_INCH
            if self.unit is FeedUnit.INCH_PER_MINUTE
            else 1.0 / _MM_PER_INCH
        )
        return FeedRate(self.value * factor, unit)


@dataclass(frozen=True, slots=True)
class SpindleSpeed:
    """A strictly positive spindle speed."""

    value: float
    unit: SpindleSpeedUnit = SpindleSpeedUnit.RPM

    def __post_init__(self) -> None:
        value = _finite(self.value, "Spindle speed")
        if value <= 0.0:
            raise CamUnitError("Spindle speed must be greater than zero")
        object.__setattr__(self, "value", value)
        if self.unit is not SpindleSpeedUnit.RPM:
            raise CamUnitError("Spindle speed unit is invalid")
