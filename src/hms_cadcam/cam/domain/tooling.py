"""Immutable CAM tool, holder and assembly domain definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import (
    HolderDefinitionId,
    ToolAssemblyId,
    ToolDefinitionId,
)
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    Revision,
)
from hms_cadcam.cam.domain.spatial import _strict_payload
from hms_cadcam.cam.domain.tool_profiles import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    ToolCommonDefaults,
    ToolProgramProfile,
)
from hms_cadcam.cam.domain.units import Angle, AngleUnit, Length, LengthUnit

_TOOL_FORMAT = "HMS_CAM_TOOL_DEFINITION"
_HOLDER_FORMAT = "HMS_CAM_HOLDER_DEFINITION"
_ASSEMBLY_FORMAT = "HMS_CAM_TOOL_ASSEMBLY"
_VERSION = 1
_TOOL_VERSION = 2
_BORING_BAR_GEOMETRY_VERSION = 1


def _name(value: str, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{subject} name must not be empty")
    normalized = value.strip()
    if len(normalized) > 255:
        raise CamValidationError(f"{subject} name is too long")
    return normalized


def _optional_text(value: str | None, subject: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{subject} must be non-empty when provided")
    return value.strip()


def _length_dict(value: Length) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _length_from_dict(data: dict[str, Any]) -> Length:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Length payload is malformed")
    try:
        unit = LengthUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Length unit payload is invalid") from error
    return Length(data["value"], unit)


def _angle_dict(value: Angle) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _angle_from_dict(data: dict[str, Any]) -> Angle:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Angle payload is malformed")
    try:
        unit = AngleUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Angle unit payload is invalid") from error
    return Angle(data["value"], unit)


def _positive_length(value: Length, subject: str) -> None:
    if not isinstance(value, Length):
        raise CamValidationError(f"{subject} must be Length")
    if value.unit is LengthUnit.UNKNOWN:
        raise CamUnitError(f"{subject} requires a known length unit")
    if value.value <= 0.0:
        raise CamValidationError(f"{subject} must be greater than zero")


def _non_negative_length(value: Length, subject: str) -> None:
    if not isinstance(value, Length):
        raise CamValidationError(f"{subject} must be Length")
    if value.unit is LengthUnit.UNKNOWN:
        raise CamUnitError(f"{subject} requires a known length unit")
    if value.value < 0.0:
        raise CamValidationError(f"{subject} must not be negative")


def _one_unit(values: tuple[Length, ...], unit: LengthUnit, subject: str) -> None:
    if unit is LengthUnit.UNKNOWN:
        raise CamUnitError(f"{subject} requires a known unit")
    if any(value.unit is not unit for value in values):
        raise CamUnitError(f"{subject} dimensions must use definition unit")


class ToolFamily(StrEnum):
    """Tool families supported by the 7A.3 geometry contract."""

    END_MILL = "end_mill"
    BALL_END_MILL = "ball_end_mill"
    BULL_NOSE_END_MILL = "bull_nose_end_mill"
    DRILL = "drill"
    CENTER_DRILL = "center_drill"
    CHAMFER_MILL = "chamfer_mill"
    FACE_MILL = "face_mill"
    REAMER = "reamer"
    TAP = "tap"
    BORING_BAR = "boring_bar"
    TURNING_INSERT = "turning_insert"
    CUSTOM = "custom"


class ToolCoolantCapability(StrEnum):
    """Controller-neutral coolant delivery supported by a tool."""

    FLOOD = "flood"
    MIST = "mist"
    AIR = "air"
    THROUGH_TOOL = "through_tool"


class ToolHand(StrEnum):
    """Cutting or thread hand."""

    RIGHT = "right"
    LEFT = "left"


class CuttingGeometryKind(StrEnum):
    """Closed discriminator for concrete cutting geometry payloads."""

    CYLINDRICAL = "cylindrical"
    BALL_END = "ball_end"
    BULL_NOSE = "bull_nose"
    DRILL = "drill"
    CHAMFER = "chamfer"
    TAP = "tap"
    BORING_BAR = "boring_bar"
    TURNING_INSERT = "turning_insert"
    CUSTOM = "custom"


class CuttingGeometry:
    """Closed cutting-geometry base class."""

    kind: ClassVar[CuttingGeometryKind]

    @property
    def dimensions(self) -> tuple[Length, ...]:
        """Return all physical length values for unit validation."""
        raise NotImplementedError

    @property
    def axial_cutting_length(self) -> Length:
        """Return the axial cutting extent."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """Serialize the concrete geometry payload."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CuttingGeometry":
        """Deserialize exactly one concrete cutting geometry variant."""
        if not isinstance(data, dict) or "kind" not in data:
            raise CamValidationError("Cutting geometry payload is malformed")
        try:
            kind = CuttingGeometryKind(data["kind"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Cutting geometry kind is invalid") from error
        variant: type[CuttingGeometry] = {
            CuttingGeometryKind.CYLINDRICAL: CylindricalGeometry,
            CuttingGeometryKind.BALL_END: BallEndGeometry,
            CuttingGeometryKind.BULL_NOSE: BullNoseGeometry,
            CuttingGeometryKind.DRILL: DrillGeometry,
            CuttingGeometryKind.CHAMFER: ChamferGeometry,
            CuttingGeometryKind.TAP: TapGeometry,
            CuttingGeometryKind.BORING_BAR: BoringBarGeometry,
            CuttingGeometryKind.TURNING_INSERT: TurningInsertGeometry,
            CuttingGeometryKind.CUSTOM: CustomCuttingGeometry,
        }[kind]
        return variant.from_dict(data)


@dataclass(frozen=True, slots=True)
class CylindricalGeometry(CuttingGeometry):
    """Diameter and flute length for cylindrical cutters."""

    diameter: Length
    flute_length: Length
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.CYLINDRICAL

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Cutter diameter")
        _positive_length(self.flute_length, "Flute length")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.diameter, self.flute_length)

    @property
    def axial_cutting_length(self) -> Length:
        return self.flute_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "diameter": _length_dict(self.diameter),
            "flute_length": _length_dict(self.flute_length),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CylindricalGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "diameter",
            "flute_length",
        }:
            raise CamValidationError("Cylindrical geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Cylindrical geometry kind mismatch")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["flute_length"]),
        )


@dataclass(frozen=True, slots=True)
class BallEndGeometry(CuttingGeometry):
    """Ball-end geometry using diameter consistently."""

    diameter: Length
    flute_length: Length
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.BALL_END

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Ball diameter")
        _positive_length(self.flute_length, "Ball flute length")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.diameter, self.flute_length)

    @property
    def axial_cutting_length(self) -> Length:
        return self.flute_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "diameter": _length_dict(self.diameter),
            "flute_length": _length_dict(self.flute_length),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BallEndGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "diameter",
            "flute_length",
        }:
            raise CamValidationError("Ball-end geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Ball-end geometry kind mismatch")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["flute_length"]),
        )


@dataclass(frozen=True, slots=True)
class BullNoseGeometry(CuttingGeometry):
    """Bull-nose geometry with bounded corner radius."""

    diameter: Length
    flute_length: Length
    corner_radius: Length
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.BULL_NOSE

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Bull-nose diameter")
        _positive_length(self.flute_length, "Bull-nose flute length")
        _positive_length(self.corner_radius, "Bull-nose corner radius")
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Bull-nose dimensions must use one unit")
        if self.corner_radius.value > self.diameter.value / 2.0:
            raise CamInvariantError("Corner radius cannot exceed cutter radius")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.diameter, self.flute_length, self.corner_radius)

    @property
    def axial_cutting_length(self) -> Length:
        return self.flute_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "diameter": _length_dict(self.diameter),
            "flute_length": _length_dict(self.flute_length),
            "corner_radius": _length_dict(self.corner_radius),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BullNoseGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "diameter",
            "flute_length",
            "corner_radius",
        }:
            raise CamValidationError("Bull-nose geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Bull-nose geometry kind mismatch")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["flute_length"]),
            _length_from_dict(data["corner_radius"]),
        )


@dataclass(frozen=True, slots=True)
class DrillGeometry(CuttingGeometry):
    """Drill geometry with explicit point angle."""

    diameter: Length
    flute_length: Length
    point_angle: Angle
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.DRILL

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Drill diameter")
        _positive_length(self.flute_length, "Drill flute length")
        if not isinstance(self.point_angle, Angle):
            raise CamValidationError("Drill point angle is invalid")
        degrees = self.point_angle.to(AngleUnit.DEGREE).value
        if not 0.0 < degrees < 180.0:
            raise CamValidationError("Drill point angle must be between 0 and 180 degrees")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.diameter, self.flute_length)

    @property
    def axial_cutting_length(self) -> Length:
        return self.flute_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "diameter": _length_dict(self.diameter),
            "flute_length": _length_dict(self.flute_length),
            "point_angle": _angle_dict(self.point_angle),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrillGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "diameter",
            "flute_length",
            "point_angle",
        }:
            raise CamValidationError("Drill geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Drill geometry kind mismatch")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["flute_length"]),
            _angle_from_dict(data["point_angle"]),
        )


@dataclass(frozen=True, slots=True)
class ChamferGeometry(CuttingGeometry):
    """Chamfer cutter with included angle and explicit tip diameter."""

    maximum_diameter: Length
    cutting_length: Length
    included_angle: Angle
    tip_diameter: Length
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.CHAMFER

    def __post_init__(self) -> None:
        for value, subject in (
            (self.maximum_diameter, "Chamfer maximum diameter"),
            (self.cutting_length, "Chamfer cutting length"),
        ):
            _positive_length(value, subject)
        _non_negative_length(self.tip_diameter, "Chamfer tip diameter")
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Chamfer dimensions must use one unit")
        if self.tip_diameter.value >= self.maximum_diameter.value:
            raise CamInvariantError("Chamfer tip must be smaller than maximum diameter")
        if not isinstance(self.included_angle, Angle):
            raise CamValidationError("Chamfer included angle is invalid")
        degrees = self.included_angle.to(AngleUnit.DEGREE).value
        if not 0.0 < degrees < 180.0:
            raise CamValidationError("Chamfer angle must be between 0 and 180 degrees")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.maximum_diameter, self.cutting_length, self.tip_diameter)

    @property
    def axial_cutting_length(self) -> Length:
        return self.cutting_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "maximum_diameter": _length_dict(self.maximum_diameter),
            "cutting_length": _length_dict(self.cutting_length),
            "included_angle": _angle_dict(self.included_angle),
            "tip_diameter": _length_dict(self.tip_diameter),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChamferGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "maximum_diameter",
            "cutting_length",
            "included_angle",
            "tip_diameter",
        }:
            raise CamValidationError("Chamfer geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Chamfer geometry kind mismatch")
        return cls(
            _length_from_dict(data["maximum_diameter"]),
            _length_from_dict(data["cutting_length"]),
            _angle_from_dict(data["included_angle"]),
            _length_from_dict(data["tip_diameter"]),
        )


@dataclass(frozen=True, slots=True)
class TapGeometry(CuttingGeometry):
    """Minimal tap geometry without encoding a controller cycle."""

    nominal_diameter: Length
    threaded_length: Length
    pitch: Length
    hand: ToolHand
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.TAP

    def __post_init__(self) -> None:
        for value, subject in (
            (self.nominal_diameter, "Tap nominal diameter"),
            (self.threaded_length, "Tap threaded length"),
            (self.pitch, "Tap pitch"),
        ):
            _positive_length(value, subject)
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Tap dimensions must use one unit")
        if not isinstance(self.hand, ToolHand):
            raise CamValidationError("Tap hand is invalid")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.nominal_diameter, self.threaded_length, self.pitch)

    @property
    def axial_cutting_length(self) -> Length:
        return self.threaded_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "nominal_diameter": _length_dict(self.nominal_diameter),
            "threaded_length": _length_dict(self.threaded_length),
            "pitch": _length_dict(self.pitch),
            "hand": self.hand.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TapGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "nominal_diameter",
            "threaded_length",
            "pitch",
            "hand",
        }:
            raise CamValidationError("Tap geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Tap geometry kind mismatch")
        try:
            hand = ToolHand(data["hand"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Tap hand payload is invalid") from error
        return cls(
            _length_from_dict(data["nominal_diameter"]),
            _length_from_dict(data["threaded_length"]),
            _length_from_dict(data["pitch"]),
            hand,
        )


@dataclass(frozen=True, slots=True)
class BoringBarGeometry(CuttingGeometry):
    """Controller-neutral access envelope for one axial boring bar/head."""

    minimum_bore_diameter: Length
    maximum_bore_diameter: Length
    cutting_length: Length
    hand: ToolHand
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.BORING_BAR
    SERIALIZATION_VERSION: ClassVar[int] = _BORING_BAR_GEOMETRY_VERSION

    def __post_init__(self) -> None:
        for value, subject in (
            (self.minimum_bore_diameter, "Boring minimum bore diameter"),
            (self.maximum_bore_diameter, "Boring maximum bore diameter"),
            (self.cutting_length, "Boring cutting length"),
        ):
            _positive_length(value, subject)
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Boring bar dimensions must use one unit")
        if self.maximum_bore_diameter.value < self.minimum_bore_diameter.value:
            raise CamInvariantError(
                "Boring maximum bore diameter cannot be below the minimum"
            )
        if not isinstance(self.hand, ToolHand):
            raise CamValidationError("Boring bar hand is invalid")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (
            self.minimum_bore_diameter,
            self.maximum_bore_diameter,
            self.cutting_length,
        )

    @property
    def axial_cutting_length(self) -> Length:
        return self.cutting_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "geometry_version": _BORING_BAR_GEOMETRY_VERSION,
            "minimum_bore_diameter": _length_dict(self.minimum_bore_diameter),
            "maximum_bore_diameter": _length_dict(self.maximum_bore_diameter),
            "cutting_length": _length_dict(self.cutting_length),
            "hand": self.hand.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BoringBarGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "geometry_version",
            "minimum_bore_diameter",
            "maximum_bore_diameter",
            "cutting_length",
            "hand",
        }:
            raise CamValidationError("Boring bar geometry payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Boring bar geometry kind mismatch")
        if (
            type(data["geometry_version"]) is not int
            or data["geometry_version"] != _BORING_BAR_GEOMETRY_VERSION
        ):
            raise UnsupportedCamSchemaError(
                "Unsupported boring bar geometry version"
            )
        try:
            hand = ToolHand(data["hand"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Boring bar hand payload is invalid") from error
        return cls(
            _length_from_dict(data["minimum_bore_diameter"]),
            _length_from_dict(data["maximum_bore_diameter"]),
            _length_from_dict(data["cutting_length"]),
            hand,
        )


@dataclass(frozen=True, slots=True)
class TurningInsertGeometry(CuttingGeometry):
    """Minimal turning-insert envelope, not a full ISO insert model."""

    inscribed_circle: Length
    thickness: Length
    nose_radius: Length
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.TURNING_INSERT

    def __post_init__(self) -> None:
        for value, subject in (
            (self.inscribed_circle, "Insert inscribed circle"),
            (self.thickness, "Insert thickness"),
            (self.nose_radius, "Insert nose radius"),
        ):
            _positive_length(value, subject)
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Turning insert dimensions must use one unit")
        if self.nose_radius.value > self.inscribed_circle.value / 2.0:
            raise CamInvariantError("Insert nose radius exceeds insert envelope")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.inscribed_circle, self.thickness, self.nose_radius)

    @property
    def axial_cutting_length(self) -> Length:
        return self.thickness

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "inscribed_circle": _length_dict(self.inscribed_circle),
            "thickness": _length_dict(self.thickness),
            "nose_radius": _length_dict(self.nose_radius),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurningInsertGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "inscribed_circle",
            "thickness",
            "nose_radius",
        }:
            raise CamValidationError("Turning insert payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Turning insert geometry kind mismatch")
        return cls(
            _length_from_dict(data["inscribed_circle"]),
            _length_from_dict(data["thickness"]),
            _length_from_dict(data["nose_radius"]),
        )


@dataclass(frozen=True, slots=True)
class CustomCuttingGeometry(CuttingGeometry):
    """Conservative custom cutter envelope with a required description."""

    diameter: Length
    cutting_length: Length
    description: str
    kind: ClassVar[CuttingGeometryKind] = CuttingGeometryKind.CUSTOM

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Custom cutter diameter")
        _positive_length(self.cutting_length, "Custom cutter length")
        object.__setattr__(self, "description", _name(self.description, "Custom geometry"))

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (self.diameter, self.cutting_length)

    @property
    def axial_cutting_length(self) -> Length:
        return self.cutting_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "diameter": _length_dict(self.diameter),
            "cutting_length": _length_dict(self.cutting_length),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CustomCuttingGeometry":
        if not isinstance(data, dict) or set(data) != {
            "kind",
            "diameter",
            "cutting_length",
            "description",
        }:
            raise CamValidationError("Custom cutting payload is malformed")
        if data["kind"] != cls.kind.value:
            raise CamValidationError("Custom cutting geometry kind mismatch")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["cutting_length"]),
            data["description"],
        )


@dataclass(frozen=True, slots=True)
class ShankGeometry:
    """Simple cylindrical shank envelope."""

    diameter: Length
    length: Length

    def __post_init__(self) -> None:
        _positive_length(self.diameter, "Shank diameter")
        _positive_length(self.length, "Shank length")
        if self.diameter.unit is not self.length.unit:
            raise CamUnitError("Shank dimensions must use one unit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "diameter": _length_dict(self.diameter),
            "length": _length_dict(self.length),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShankGeometry":
        if not isinstance(data, dict) or set(data) != {"diameter", "length"}:
            raise CamValidationError("Shank geometry payload is malformed")
        return cls(
            _length_from_dict(data["diameter"]),
            _length_from_dict(data["length"]),
        )


_FAMILY_GEOMETRY: dict[ToolFamily, tuple[type[CuttingGeometry], ...]] = {
    ToolFamily.END_MILL: (CylindricalGeometry,),
    ToolFamily.BALL_END_MILL: (BallEndGeometry,),
    ToolFamily.BULL_NOSE_END_MILL: (BullNoseGeometry,),
    ToolFamily.DRILL: (DrillGeometry,),
    ToolFamily.CENTER_DRILL: (DrillGeometry,),
    ToolFamily.CHAMFER_MILL: (ChamferGeometry,),
    ToolFamily.FACE_MILL: (CylindricalGeometry,),
    ToolFamily.REAMER: (CylindricalGeometry,),
    ToolFamily.TAP: (TapGeometry,),
    ToolFamily.BORING_BAR: (BoringBarGeometry,),
    ToolFamily.TURNING_INSERT: (TurningInsertGeometry,),
    ToolFamily.CUSTOM: (CustomCuttingGeometry,),
}


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Versioned immutable cutting-tool definition."""

    tool_id: ToolDefinitionId
    name: str
    family: ToolFamily
    unit: LengthUnit
    cutting_geometry: CuttingGeometry
    overall_length: Length
    usable_length: Length
    shank: ShankGeometry
    revision: Revision = Revision(0)
    coolant_capabilities: tuple[ToolCoolantCapability, ...] = ()
    manufacturer: str | None = None
    model: str | None = None
    common_defaults: ToolCommonDefaults = ToolCommonDefaults()
    program_profiles: tuple[ToolProgramProfile, ...] = ()
    configuration_revision: Revision = Revision(0)
    SERIALIZATION_VERSION: ClassVar[int] = _TOOL_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, ToolDefinitionId):
            raise CamValidationError("Tool definition ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Tool"))
        if not isinstance(self.family, ToolFamily):
            raise CamValidationError("Tool family is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Tool definition requires a known unit")
        if not isinstance(self.cutting_geometry, CuttingGeometry):
            raise CamValidationError("Cutting geometry is invalid")
        if not isinstance(self.cutting_geometry, _FAMILY_GEOMETRY[self.family]):
            raise CamInvariantError("Tool family does not match cutting geometry")
        _positive_length(self.overall_length, "Tool overall length")
        _positive_length(self.usable_length, "Tool usable length")
        if not isinstance(self.shank, ShankGeometry):
            raise CamValidationError("Tool shank is invalid")
        lengths = (
            *self.cutting_geometry.dimensions,
            self.overall_length,
            self.usable_length,
            self.shank.diameter,
            self.shank.length,
        )
        _one_unit(lengths, self.unit, "Tool")
        if self.usable_length.value > self.overall_length.value:
            raise CamInvariantError("Usable length cannot exceed overall length")
        if self.cutting_geometry.axial_cutting_length.value > self.usable_length.value:
            raise CamInvariantError("Cutting length cannot exceed usable length")
        if self.shank.length.value > self.overall_length.value:
            raise CamInvariantError("Shank length cannot exceed overall length")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Tool revision is invalid")
        if not isinstance(self.coolant_capabilities, tuple) or any(
            not isinstance(item, ToolCoolantCapability)
            for item in self.coolant_capabilities
        ):
            raise CamValidationError("Tool coolant capabilities are invalid")
        if len(set(self.coolant_capabilities)) != len(self.coolant_capabilities):
            raise CamInvariantError("Tool coolant capabilities must be unique")
        object.__setattr__(
            self,
            "coolant_capabilities",
            tuple(sorted(self.coolant_capabilities, key=lambda item: item.value)),
        )
        object.__setattr__(
            self, "manufacturer", _optional_text(self.manufacturer, "Manufacturer")
        )
        object.__setattr__(self, "model", _optional_text(self.model, "Tool model"))
        if not isinstance(self.common_defaults, ToolCommonDefaults):
            raise CamValidationError("Tool common defaults are invalid")
        if not isinstance(self.program_profiles, tuple) or any(
            not isinstance(item, ToolProgramProfile)
            for item in self.program_profiles
        ):
            raise CamValidationError("Tool program profiles must be a typed tuple")
        ordered_profiles = tuple(
            sorted(self.program_profiles, key=lambda item: str(item.profile_id))
        )
        if len({item.profile_id for item in ordered_profiles}) != len(
            ordered_profiles
        ):
            raise CamInvariantError("Tool program profile IDs must be unique")
        for profile in ordered_profiles:
            if profile.tool_id != self.tool_id:
                raise CamInvariantError("Tool program profile belongs to another Tool")
            schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(profile.strategy_id)
            if profile.profile_schema_version != schema.profile_schema_version:
                raise CamValidationError("Tool profile schema version is unsupported")
            schema.normalize_values(profile.sparse_mapping)
        object.__setattr__(self, "program_profiles", ordered_profiles)
        if not isinstance(self.configuration_revision, Revision):
            raise CamValidationError("Tool configuration revision is invalid")

    @property
    def content_fingerprint(self) -> ContentFingerprint:
        """Fingerprint physical Tool content without optional profile metadata."""
        return ContentFingerprint.from_payload(self._physical_payload())

    @property
    def configuration_fingerprint(self) -> ContentFingerprint:
        """Hash only calculation-relevant common/profile configuration."""
        return ContentFingerprint.from_payload(
            {
                "common_defaults": self.common_defaults.to_dict(),
                "profiles": [
                    {
                        "profile_id": str(item.profile_id),
                        "fingerprint": item.fingerprint.to_dict(),
                    }
                    for item in self.program_profiles
                ],
            }
        )

    def profiles_for_strategy(
        self, strategy_id: str
    ) -> tuple[ToolProgramProfile, ...]:
        """Return deterministic optional profiles for one strategy."""
        return tuple(
            item
            for item in self.program_profiles
            if item.strategy_id == strategy_id
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this tool definition."""
        if (
            self.common_defaults.is_empty
            and not self.program_profiles
            and self.configuration_revision == Revision(0)
        ):
            return self._physical_payload()
        return {
            "format": _TOOL_FORMAT,
            "format_version": _TOOL_VERSION,
            "tool_id": str(self.tool_id),
            "name": self.name,
            "family": self.family.value,
            "unit": self.unit.value,
            "cutting_geometry": self.cutting_geometry.to_dict(),
            "overall_length": _length_dict(self.overall_length),
            "usable_length": _length_dict(self.usable_length),
            "shank": self.shank.to_dict(),
            "revision": self.revision.to_dict(),
            "coolant_capabilities": [item.value for item in self.coolant_capabilities],
            "manufacturer": self.manufacturer,
            "model": self.model,
            "common_defaults": self.common_defaults.to_dict(),
            "program_profiles": [item.to_dict() for item in self.program_profiles],
            "configuration_revision": self.configuration_revision.to_dict(),
        }

    def _physical_payload(self) -> dict[str, Any]:
        return {
            "format": _TOOL_FORMAT,
            "format_version": _VERSION,
            "tool_id": str(self.tool_id),
            "name": self.name,
            "family": self.family.value,
            "unit": self.unit.value,
            "cutting_geometry": self.cutting_geometry.to_dict(),
            "overall_length": _length_dict(self.overall_length),
            "usable_length": _length_dict(self.usable_length),
            "shank": self.shank.to_dict(),
            "revision": self.revision.to_dict(),
            "coolant_capabilities": [item.value for item in self.coolant_capabilities],
            "manufacturer": self.manufacturer,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolDefinition":
        """Deserialize atomically into one tool definition."""
        if not isinstance(data, dict):
            raise CamValidationError("Tool definition payload is malformed")
        version = data.get("format_version")
        base_fields = {
            "tool_id",
            "name",
            "family",
            "unit",
            "cutting_geometry",
            "overall_length",
            "usable_length",
            "shank",
            "revision",
            "coolant_capabilities",
            "manufacturer",
            "model",
        }
        if version == _VERSION:
            _strict_payload(
                data,
                format_name=_TOOL_FORMAT,
                version=_VERSION,
                fields=base_fields,
            )
            common_defaults = ToolCommonDefaults()
            program_profiles: tuple[ToolProgramProfile, ...] = ()
            configuration_revision = Revision(0)
        elif version == _TOOL_VERSION:
            _strict_payload(
                data,
                format_name=_TOOL_FORMAT,
                version=_TOOL_VERSION,
                fields={
                    *base_fields,
                    "common_defaults",
                    "program_profiles",
                    "configuration_revision",
                },
            )
            raw_profiles = data["program_profiles"]
            if not isinstance(raw_profiles, list):
                raise CamValidationError("Tool program profiles must be a list")
            common_defaults = ToolCommonDefaults.from_dict(data["common_defaults"])
            program_profiles = tuple(
                ToolProgramProfile.from_dict(item) for item in raw_profiles
            )
            configuration_revision = Revision.from_dict(
                data["configuration_revision"]
            )
        else:
            raise UnsupportedCamSchemaError("Unsupported Tool definition version")
        coolant = data["coolant_capabilities"]
        if not isinstance(coolant, list):
            raise CamValidationError("Tool coolant payload must be a list")
        try:
            family = ToolFamily(data["family"])
            unit = LengthUnit(data["unit"])
            capabilities = tuple(ToolCoolantCapability(item) for item in coolant)
        except (TypeError, ValueError) as error:
            raise CamValidationError("Tool enum payload is invalid") from error
        return cls(
            ToolDefinitionId.parse(data["tool_id"]),
            data["name"],
            family,
            unit,
            CuttingGeometry.from_dict(data["cutting_geometry"]),
            _length_from_dict(data["overall_length"]),
            _length_from_dict(data["usable_length"]),
            ShankGeometry.from_dict(data["shank"]),
            Revision.from_dict(data["revision"]),
            capabilities,
            data["manufacturer"],
            data["model"],
            common_defaults,
            program_profiles,
            configuration_revision,
        )


@dataclass(frozen=True, slots=True)
class HolderSection:
    """One continuous cylindrical or conical holder-profile section."""

    axial_start: Length
    axial_end: Length
    lower_diameter: Length
    upper_diameter: Length

    def __post_init__(self) -> None:
        _non_negative_length(self.axial_start, "Holder section start")
        _positive_length(self.axial_end, "Holder section end")
        _positive_length(self.lower_diameter, "Holder lower diameter")
        _positive_length(self.upper_diameter, "Holder upper diameter")
        if len({item.unit for item in self.dimensions}) != 1:
            raise CamUnitError("Holder section dimensions must use one unit")
        if self.axial_end.value <= self.axial_start.value:
            raise CamInvariantError("Holder section end must follow start")

    @property
    def dimensions(self) -> tuple[Length, ...]:
        return (
            self.axial_start,
            self.axial_end,
            self.lower_diameter,
            self.upper_diameter,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "axial_start": _length_dict(self.axial_start),
            "axial_end": _length_dict(self.axial_end),
            "lower_diameter": _length_dict(self.lower_diameter),
            "upper_diameter": _length_dict(self.upper_diameter),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HolderSection":
        if not isinstance(data, dict) or set(data) != {
            "axial_start",
            "axial_end",
            "lower_diameter",
            "upper_diameter",
        }:
            raise CamValidationError("Holder section payload is malformed")
        return cls(
            _length_from_dict(data["axial_start"]),
            _length_from_dict(data["axial_end"]),
            _length_from_dict(data["lower_diameter"]),
            _length_from_dict(data["upper_diameter"]),
        )


@dataclass(frozen=True, slots=True)
class HolderDefinition:
    """Immutable continuous holder collision profile."""

    holder_id: HolderDefinitionId
    name: str
    unit: LengthUnit
    sections: tuple[HolderSection, ...]
    gauge_line: Length
    revision: Revision = Revision(0)
    interface: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.holder_id, HolderDefinitionId):
            raise CamValidationError("Holder definition ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Holder"))
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Holder definition requires a known unit")
        if not isinstance(self.sections, tuple) or not self.sections:
            raise CamValidationError("Holder requires an immutable section tuple")
        if any(not isinstance(section, HolderSection) for section in self.sections):
            raise CamValidationError("Holder section is invalid")
        _one_unit(
            tuple(value for section in self.sections for value in section.dimensions),
            self.unit,
            "Holder",
        )
        if self.sections[0].axial_start.value != 0.0:
            raise CamInvariantError("Holder profile must start at the gauge origin")
        for previous, current in zip(self.sections, self.sections[1:]):
            if current.axial_start.value != previous.axial_end.value:
                raise CamInvariantError("Holder sections must be ordered without gaps")
        _non_negative_length(self.gauge_line, "Holder gauge line")
        if self.gauge_line.unit is not self.unit:
            raise CamUnitError("Holder gauge line must use holder unit")
        if self.gauge_line.value > self.sections[-1].axial_end.value:
            raise CamInvariantError("Holder gauge line must lie within the profile")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Holder revision is invalid")
        object.__setattr__(self, "interface", _optional_text(self.interface, "Interface"))
        object.__setattr__(
            self, "manufacturer", _optional_text(self.manufacturer, "Manufacturer")
        )
        object.__setattr__(self, "model", _optional_text(self.model, "Holder model"))

    @property
    def content_fingerprint(self) -> ContentFingerprint:
        """Return a deterministic fingerprint of this holder snapshot."""
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize this holder definition in axial order."""
        return {
            "format": _HOLDER_FORMAT,
            "format_version": _VERSION,
            "holder_id": str(self.holder_id),
            "name": self.name,
            "unit": self.unit.value,
            "sections": [section.to_dict() for section in self.sections],
            "gauge_line": _length_dict(self.gauge_line),
            "revision": self.revision.to_dict(),
            "interface": self.interface,
            "manufacturer": self.manufacturer,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HolderDefinition":
        """Deserialize atomically into one continuous holder profile."""
        _strict_payload(
            data,
            format_name=_HOLDER_FORMAT,
            version=_VERSION,
            fields={
                "holder_id",
                "name",
                "unit",
                "sections",
                "gauge_line",
                "revision",
                "interface",
                "manufacturer",
                "model",
            },
        )
        sections = data["sections"]
        if not isinstance(sections, list):
            raise CamValidationError("Holder sections payload must be a list")
        try:
            unit = LengthUnit(data["unit"])
        except (TypeError, ValueError) as error:
            raise CamUnitError("Holder unit payload is invalid") from error
        return cls(
            HolderDefinitionId.parse(data["holder_id"]),
            data["name"],
            unit,
            tuple(HolderSection.from_dict(item) for item in sections),
            _length_from_dict(data["gauge_line"]),
            Revision.from_dict(data["revision"]),
            data["interface"],
            data["manufacturer"],
            data["model"],
        )


class ToolAssemblyStatus(StrEnum):
    """Result of validating assembly references against library state."""

    VALID = "valid"
    MISSING_TOOL = "missing_tool"
    TOOL_REVISION_MISMATCH = "tool_revision_mismatch"
    MISSING_HOLDER = "missing_holder"
    HOLDER_REVISION_MISMATCH = "holder_revision_mismatch"
    INCOMPATIBLE_UNIT = "incompatible_unit"


@dataclass(frozen=True, slots=True)
class ToolAssembly:
    """Independent assembly referencing expected tool and holder snapshots."""

    assembly_id: ToolAssemblyId
    name: str
    unit: LengthUnit
    tool_id: ToolDefinitionId
    expected_tool_revision: Revision
    expected_tool_fingerprint: ContentFingerprint
    expected_tool_unit: LengthUnit
    stickout: Length
    gauge_length: Length
    holder_id: HolderDefinitionId | None = None
    expected_holder_revision: Revision | None = None
    expected_holder_fingerprint: ContentFingerprint | None = None
    expected_holder_unit: LengthUnit | None = None
    revision: Revision = Revision(0)
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.assembly_id, ToolAssemblyId):
            raise CamValidationError("Tool assembly ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Tool assembly"))
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Tool assembly requires a known unit")
        if not isinstance(self.tool_id, ToolDefinitionId):
            raise CamValidationError("Assembly tool ID is invalid")
        if not isinstance(self.expected_tool_revision, Revision):
            raise CamValidationError("Expected tool revision is invalid")
        if not isinstance(self.expected_tool_fingerprint, ContentFingerprint):
            raise CamValidationError("Expected tool fingerprint is invalid")
        if self.expected_tool_unit is not self.unit:
            raise CamUnitError("Tool unit must match assembly unit explicitly")
        _positive_length(self.stickout, "Tool stickout")
        _positive_length(self.gauge_length, "Tool gauge length")
        _one_unit((self.stickout, self.gauge_length), self.unit, "Tool assembly")
        if self.gauge_length.value < self.stickout.value:
            raise CamInvariantError("Gauge length cannot be shorter than tool stickout")
        holder_values = (
            self.holder_id,
            self.expected_holder_revision,
            self.expected_holder_fingerprint,
            self.expected_holder_unit,
        )
        if any(value is None for value in holder_values) and any(
            value is not None for value in holder_values
        ):
            raise CamInvariantError("Holder reference fields must be all present or absent")
        if self.holder_id is not None:
            if not isinstance(self.holder_id, HolderDefinitionId):
                raise CamValidationError("Assembly holder ID is invalid")
            if not isinstance(self.expected_holder_revision, Revision):
                raise CamValidationError("Expected holder revision is invalid")
            if not isinstance(self.expected_holder_fingerprint, ContentFingerprint):
                raise CamValidationError("Expected holder fingerprint is invalid")
            if self.expected_holder_unit is not self.unit:
                raise CamUnitError("Holder unit must match assembly unit explicitly")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Tool assembly revision is invalid")

    @classmethod
    def create(
        cls,
        assembly_id: ToolAssemblyId,
        name: str,
        tool: ToolDefinition,
        stickout: Length,
        gauge_length: Length,
        holder: HolderDefinition | None = None,
    ) -> "ToolAssembly":
        """Create an assembly by snapshotting expected library state."""
        if not isinstance(tool, ToolDefinition):
            raise CamValidationError("Assembly tool definition is invalid")
        if holder is not None and not isinstance(holder, HolderDefinition):
            raise CamValidationError("Assembly holder definition is invalid")
        if holder is not None and holder.unit is not tool.unit:
            raise CamUnitError("Tool and holder units require explicit conversion")
        return cls(
            assembly_id=assembly_id,
            name=name,
            unit=tool.unit,
            tool_id=tool.tool_id,
            expected_tool_revision=tool.revision,
            expected_tool_fingerprint=tool.content_fingerprint,
            expected_tool_unit=tool.unit,
            stickout=stickout,
            gauge_length=gauge_length,
            holder_id=holder.holder_id if holder is not None else None,
            expected_holder_revision=holder.revision if holder is not None else None,
            expected_holder_fingerprint=(
                holder.content_fingerprint if holder is not None else None
            ),
            expected_holder_unit=holder.unit if holder is not None else None,
        )

    @property
    def content_fingerprint(self) -> DependencyFingerprint:
        """Fingerprint assembly geometry inputs without Python object identity."""
        return DependencyFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize this assembly reference snapshot."""
        return {
            "format": _ASSEMBLY_FORMAT,
            "format_version": _VERSION,
            "assembly_id": str(self.assembly_id),
            "name": self.name,
            "unit": self.unit.value,
            "tool_id": str(self.tool_id),
            "expected_tool_revision": self.expected_tool_revision.to_dict(),
            "expected_tool_fingerprint": self.expected_tool_fingerprint.to_dict(),
            "expected_tool_unit": self.expected_tool_unit.value,
            "stickout": _length_dict(self.stickout),
            "gauge_length": _length_dict(self.gauge_length),
            "holder_id": str(self.holder_id) if self.holder_id is not None else None,
            "expected_holder_revision": (
                self.expected_holder_revision.to_dict()
                if self.expected_holder_revision is not None
                else None
            ),
            "expected_holder_fingerprint": (
                self.expected_holder_fingerprint.to_dict()
                if self.expected_holder_fingerprint is not None
                else None
            ),
            "expected_holder_unit": (
                self.expected_holder_unit.value
                if self.expected_holder_unit is not None
                else None
            ),
            "revision": self.revision.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolAssembly":
        """Deserialize atomically into one assembly snapshot reference."""
        _strict_payload(
            data,
            format_name=_ASSEMBLY_FORMAT,
            version=_VERSION,
            fields={
                "assembly_id",
                "name",
                "unit",
                "tool_id",
                "expected_tool_revision",
                "expected_tool_fingerprint",
                "expected_tool_unit",
                "stickout",
                "gauge_length",
                "holder_id",
                "expected_holder_revision",
                "expected_holder_fingerprint",
                "expected_holder_unit",
                "revision",
            },
        )
        try:
            unit = LengthUnit(data["unit"])
            tool_unit = LengthUnit(data["expected_tool_unit"])
            holder_unit = (
                LengthUnit(data["expected_holder_unit"])
                if data["expected_holder_unit"] is not None
                else None
            )
        except (TypeError, ValueError) as error:
            raise CamUnitError("Tool assembly unit payload is invalid") from error
        return cls(
            ToolAssemblyId.parse(data["assembly_id"]),
            data["name"],
            unit,
            ToolDefinitionId.parse(data["tool_id"]),
            Revision.from_dict(data["expected_tool_revision"]),
            ContentFingerprint.from_dict(data["expected_tool_fingerprint"]),
            tool_unit,
            _length_from_dict(data["stickout"]),
            _length_from_dict(data["gauge_length"]),
            (
                HolderDefinitionId.parse(data["holder_id"])
                if data["holder_id"] is not None
                else None
            ),
            (
                Revision.from_dict(data["expected_holder_revision"])
                if data["expected_holder_revision"] is not None
                else None
            ),
            (
                ContentFingerprint.from_dict(data["expected_holder_fingerprint"])
                if data["expected_holder_fingerprint"] is not None
                else None
            ),
            holder_unit,
            Revision.from_dict(data["revision"]),
        )


@dataclass(frozen=True, slots=True)
class ToolAssemblyEvidence:
    """Current library state used for native-free stale assessment."""

    tool_exists: bool
    tool_revision: Revision | None = None
    tool_fingerprint: ContentFingerprint | None = None
    tool_unit: LengthUnit | None = None
    holder_exists: bool = False
    holder_revision: Revision | None = None
    holder_fingerprint: ContentFingerprint | None = None
    holder_unit: LengthUnit | None = None

    def __post_init__(self) -> None:
        if type(self.tool_exists) is not bool or type(self.holder_exists) is not bool:
            raise CamValidationError("Assembly evidence existence flags must be boolean")
        tool_values = (self.tool_revision, self.tool_fingerprint, self.tool_unit)
        if self.tool_exists and any(value is None for value in tool_values):
            raise CamValidationError("Existing tool evidence must be complete")
        holder_values = (
            self.holder_revision,
            self.holder_fingerprint,
            self.holder_unit,
        )
        if self.holder_exists and any(value is None for value in holder_values):
            raise CamValidationError("Existing holder evidence must be complete")


def assess_tool_assembly(
    assembly: ToolAssembly,
    evidence: ToolAssemblyEvidence,
) -> ToolAssemblyStatus:
    """Detect missing, stale or unit-incompatible library references."""
    if not evidence.tool_exists:
        return ToolAssemblyStatus.MISSING_TOOL
    if evidence.tool_unit is not assembly.unit:
        return ToolAssemblyStatus.INCOMPATIBLE_UNIT
    if (
        evidence.tool_revision != assembly.expected_tool_revision
        or evidence.tool_fingerprint != assembly.expected_tool_fingerprint
    ):
        return ToolAssemblyStatus.TOOL_REVISION_MISMATCH
    if assembly.holder_id is None:
        return ToolAssemblyStatus.VALID
    if not evidence.holder_exists:
        return ToolAssemblyStatus.MISSING_HOLDER
    if evidence.holder_unit is not assembly.unit:
        return ToolAssemblyStatus.INCOMPATIBLE_UNIT
    if (
        evidence.holder_revision != assembly.expected_holder_revision
        or evidence.holder_fingerprint != assembly.expected_holder_fingerprint
    ):
        return ToolAssemblyStatus.HOLDER_REVISION_MISMATCH
    return ToolAssemblyStatus.VALID


class ToolLibraryPort(Protocol):
    """Persistence-neutral tooling library contract."""

    def get_tool(self, tool_id: ToolDefinitionId) -> ToolDefinition | None:
        """Return one tool snapshot."""
        ...

    def get_holder(self, holder_id: HolderDefinitionId) -> HolderDefinition | None:
        """Return one holder snapshot."""
        ...

    def get_assembly(self, assembly_id: ToolAssemblyId) -> ToolAssembly | None:
        """Return one assembly snapshot."""
        ...

    def list_tools(self, query: str | None = None) -> tuple[ToolDefinition, ...]:
        """List immutable tool metadata snapshots."""
        ...

    def list_holders(self, query: str | None = None) -> tuple[HolderDefinition, ...]:
        """List immutable holder metadata snapshots."""
        ...

    def list_assemblies(self, query: str | None = None) -> tuple[ToolAssembly, ...]:
        """List immutable assembly metadata snapshots."""
        ...

    def add_tool(self, tool: ToolDefinition) -> None:
        """Add a tool or report a persistence conflict."""
        ...

    def update_tool(self, tool: ToolDefinition, expected_revision: Revision) -> None:
        """Update a tool only at the expected current revision."""
        ...

    def remove_tool(
        self,
        tool_id: ToolDefinitionId,
        expected_revision: Revision,
    ) -> None:
        """Remove a tool only at the expected current revision."""
        ...

    def add_holder(self, holder: HolderDefinition) -> None:
        """Add a holder or report a persistence conflict."""
        ...

    def update_holder(
        self,
        holder: HolderDefinition,
        expected_revision: Revision,
    ) -> None:
        """Update a holder only at the expected revision."""
        ...

    def remove_holder(
        self,
        holder_id: HolderDefinitionId,
        expected_revision: Revision,
    ) -> None:
        """Remove a holder only at the expected revision."""
        ...

    def add_assembly(self, assembly: ToolAssembly) -> None:
        """Add an assembly or report a persistence conflict."""
        ...

    def update_assembly(
        self,
        assembly: ToolAssembly,
        expected_revision: Revision,
    ) -> None:
        """Update an assembly only at the expected revision."""
        ...

    def remove_assembly(
        self,
        assembly_id: ToolAssemblyId,
        expected_revision: Revision,
    ) -> None:
        """Remove an assembly only at the expected revision."""
        ...
