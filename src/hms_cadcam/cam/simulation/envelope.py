"""Tool, shank and holder collision envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamUnitError, CamValidationError
from hms_cadcam.cam.domain.tooling import (
    BallEndGeometry, BoringBarGeometry, BullNoseGeometry, CylindricalGeometry,
    DrillGeometry, HolderDefinition, ShankGeometry, TapGeometry, ToolAssembly, ToolDefinition,
)
from hms_cadcam.cam.domain.units import LengthUnit
from .model import SimulationIssueCode


class EnvelopePrimitiveKind(StrEnum):
    CYLINDER = "cylinder"
    BALL = "ball"
    FRUSTUM = "frustum"


class EnvelopeSupport(StrEnum):
    EXACT = "exact"
    GEOMETRY_FAITHFUL = "geometry_faithful"
    CONSERVATIVE = "conservative"


@dataclass(frozen=True, slots=True)
class EnvelopePrimitive:
    kind: EnvelopePrimitiveKind
    axial_start: float
    axial_end: float
    lower_radius: float
    upper_radius: float
    unit: LengthUnit
    label: str
    support: EnvelopeSupport

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EnvelopePrimitiveKind) or not isinstance(self.support, EnvelopeSupport):
            raise CamValidationError("Envelope primitive kind/support is invalid")
        if self.unit is LengthUnit.UNKNOWN or not isinstance(self.unit, LengthUnit):
            raise CamUnitError("Envelope primitive unit is invalid")
        values = (self.axial_start, self.axial_end, self.lower_radius, self.upper_radius)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values) or self.axial_end <= self.axial_start or self.lower_radius < 0.0 or self.upper_radius < 0.0 or max(self.lower_radius, self.upper_radius) <= 0.0:
            raise CamValidationError("Envelope primitive dimensions are invalid")
        if self.kind in {EnvelopePrimitiveKind.CYLINDER, EnvelopePrimitiveKind.BALL} and min(self.lower_radius, self.upper_radius) <= 0.0:
            raise CamValidationError("Cylinder/ball radius must be positive")
        if not isinstance(self.label, str) or not self.label:
            raise CamValidationError("Envelope primitive label is invalid")

    @property
    def radius(self) -> float:
        return max(self.lower_radius, self.upper_radius)


@dataclass(frozen=True, slots=True)
class ToolEnvelope:
    cutter: tuple[EnvelopePrimitive, ...]
    shank: tuple[EnvelopePrimitive, ...]
    holder: tuple[EnvelopePrimitive, ...]
    unit: LengthUnit
    support: EnvelopeSupport

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Tool envelope unit is invalid")
        if not all(isinstance(item, EnvelopePrimitive) and item.unit is self.unit for group in (self.cutter, self.shank, self.holder) for item in group):
            raise CamValidationError("Tool envelope primitive unit mismatch")
        if not isinstance(self.support, EnvelopeSupport):
            raise CamValidationError("Tool envelope support is invalid")


class UnsupportedToolGeometryError(RuntimeError):
    code = SimulationIssueCode.UNSUPPORTED_GEOMETRY


def _primitive(kind: EnvelopePrimitiveKind, start: float, end: float, radius: float, unit: LengthUnit, label: str, support: EnvelopeSupport, upper: float | None = None) -> EnvelopePrimitive:
    return EnvelopePrimitive(kind, start, end, radius, radius if upper is None else upper, unit, label, support)


def build_tool_envelope(*, tool: ToolDefinition, assembly: ToolAssembly, holder: HolderDefinition | None) -> ToolEnvelope:
    """Build a fixed-axis rotating envelope; malformed/missing profiles fail closed."""
    if not isinstance(tool, ToolDefinition) or not isinstance(assembly.unit, LengthUnit):
        raise CamValidationError("Tool envelope inputs are invalid")
    unit = tool.unit
    if assembly.unit is not unit:
        raise CamUnitError("Tool and assembly units differ")
    geometry = tool.cutting_geometry
    support = EnvelopeSupport.EXACT
    cutter: list[EnvelopePrimitive] = []
    if isinstance(geometry, CylindricalGeometry):
        cutter.append(_primitive(EnvelopePrimitiveKind.CYLINDER, 0.0, geometry.flute_length.value, geometry.diameter.value / 2.0, unit, "cutter", support))
    elif isinstance(geometry, BallEndGeometry):
        radius = geometry.diameter.value / 2.0
        cutter.extend((_primitive(EnvelopePrimitiveKind.BALL, 0.0, radius, radius, unit, "ball_tip", support), _primitive(EnvelopePrimitiveKind.CYLINDER, radius, geometry.flute_length.value, radius, unit, "ball_shaft", support)))
    elif isinstance(geometry, BullNoseGeometry):
        support = EnvelopeSupport.GEOMETRY_FAITHFUL
        cutter.append(_primitive(EnvelopePrimitiveKind.FRUSTUM, 0.0, geometry.flute_length.value, geometry.diameter.value / 2.0, unit, "bull_nose", support, geometry.diameter.value / 2.0))
    elif isinstance(geometry, DrillGeometry):
        import math
        radius = geometry.diameter.value / 2.0
        tip_length = radius / math.tan(math.radians(geometry.point_angle.to(type(geometry.point_angle.unit).DEGREE).value / 2.0))
        if tip_length >= geometry.flute_length.value:
            raise CamValidationError("Drill point exceeds the flute length")
        cutter.extend((_primitive(EnvelopePrimitiveKind.FRUSTUM, 0.0, tip_length, 0.0, unit, "drill_tip", EnvelopeSupport.GEOMETRY_FAITHFUL, radius), _primitive(EnvelopePrimitiveKind.CYLINDER, tip_length, geometry.flute_length.value, radius, unit, "drill_flute", EnvelopeSupport.GEOMETRY_FAITHFUL)))
    elif isinstance(geometry, TapGeometry):
        support = EnvelopeSupport.CONSERVATIVE
        cutter.append(_primitive(EnvelopePrimitiveKind.CYLINDER, 0.0, geometry.threaded_length.value, geometry.nominal_diameter.value / 2.0, unit, "tap_nominal", support))
    elif isinstance(geometry, BoringBarGeometry):
        support = EnvelopeSupport.CONSERVATIVE
        cutter.append(_primitive(EnvelopePrimitiveKind.CYLINDER, 0.0, geometry.cutting_length.value, geometry.maximum_bore_diameter.value / 2.0, unit, "boring_max_envelope", support))
    else:
        raise UnsupportedToolGeometryError("Tool cutting geometry is unsupported")

    shank = tool.shank
    if not isinstance(shank, ShankGeometry) or shank.diameter.unit is not unit or shank.length.unit is not unit:
        raise CamValidationError("Tool shank geometry is invalid")
    cutting_end = max(item.axial_end for item in cutter)
    if assembly.stickout.value < cutting_end or assembly.stickout.value > cutting_end + shank.length.value + 1.0e-9:
        raise CamValidationError("Tool stickout is inconsistent with shank geometry")
    shank_profile = (_primitive(EnvelopePrimitiveKind.CYLINDER, cutting_end, assembly.stickout.value, shank.diameter.value / 2.0, unit, "shank", EnvelopeSupport.GEOMETRY_FAITHFUL),)
    holder_profile: tuple[EnvelopePrimitive, ...] = ()
    if assembly.holder_id is None:
        raise CamValidationError("Simulation requires an explicit holder definition")
    if holder is None or holder.holder_id != assembly.holder_id:
        raise CamValidationError("Holder definition is missing or mismatched")
    if holder.unit is not unit:
        raise CamUnitError("Holder and tool units differ")
    offset = assembly.gauge_length.value - holder.gauge_line.value
    holder_profile = tuple(_primitive(EnvelopePrimitiveKind.FRUSTUM, offset + section.axial_start.value, offset + section.axial_end.value, section.lower_diameter.value / 2.0, unit, f"holder_{index}", EnvelopeSupport.GEOMETRY_FAITHFUL, section.upper_diameter.value / 2.0) for index, section in enumerate(holder.sections))
    return ToolEnvelope(tuple(cutter), shank_profile, holder_profile, unit, support)
