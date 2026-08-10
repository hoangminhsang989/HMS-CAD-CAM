"""Deterministic automatic setup policies for Facing and Planar Facing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
    cam_quality_factor,
)
from hms_cadcam.cam.domain import (
    DependencyFingerprint,
    LengthUnit,
    ToolFamily,
)


FACING_AUTOMATIC_POLICY_KEY = "facing.operation_intelligence"
FACING_AUTOMATIC_POLICY_VERSION = 1
FACING_AUTOMATIC_KEYS = ("stepover", "stepdown", "overtravel")
_EPSILON = 1.0e-9


class FacingAutomaticVariant(StrEnum):
    """Operation boundary evidence used by the policy."""

    STOCK_BOX = "stock_box"
    PLANAR_FACE = "planar_face"


@dataclass(frozen=True, slots=True)
class FacingAutomaticContext:
    """Validated, unit-normalized evidence accepted by the policy."""

    variant: FacingAutomaticVariant
    unit: LengthUnit
    tool_family: ToolFamily | None
    diameter: float | None
    corner_radius: float | None
    axial_cutting_length: float | None
    depth_span: float | None
    tolerance: float | None
    boundary_fingerprint: str | None
    tool_fingerprint: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.variant, FacingAutomaticVariant):
            raise TypeError("Facing automatic variant is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise ValueError("Facing automatic policy requires a known unit")
        for name in (
            "diameter",
            "corner_radius",
            "axial_cutting_length",
            "depth_span",
            "tolerance",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)):
                raise ValueError(f"Facing automatic {name} is invalid")
            if value is not None and value < 0.0:
                raise ValueError(f"Facing automatic {name} cannot be negative")


def _dependency(context: FacingAutomaticContext, profile: CamQualityProfile) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload(
        {
            "variant": context.variant.value,
            "unit": context.unit.value,
            "tool_family": context.tool_family.value if context.tool_family else None,
            "diameter": context.diameter,
            "corner_radius": context.corner_radius,
            "axial_cutting_length": context.axial_cutting_length,
            "depth_span": context.depth_span,
            "tolerance": context.tolerance,
            "boundary": context.boundary_fingerprint,
            "tool": context.tool_fingerprint,
            "quality_profile": profile.value,
        }
    )


def _unsupported(
    key: str,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.NOT_APPLICABLE,
        None,
        "facing.operation_intelligence",
        FACING_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.UNSUPPORTED,
        reason,
        None,
        AutomaticValidationResult(True),
        inputs,
    )


def _derived(
    key: str,
    value: float,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
    lower: float,
    upper: float,
) -> AutomaticParameterValue:
    if not all(math.isfinite(item) for item in (value, lower, upper)) or lower <= 0.0 or upper <= 0.0:
        return _unsupported(key, dependency, "Validated positive safety bounds are unavailable.", inputs)
    if lower > upper:
        return _unsupported(key, dependency, "Automatic safety bounds are contradictory.", inputs)
    clamped_value = min(max(value, lower), upper)
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.AUTO,
        clamped_value,
        "facing.operation_intelligence",
        FACING_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.RESOLVED,
        reason,
        None,
        AutomaticValidationResult(True),
        inputs,
        lower,
        upper,
        abs(clamped_value - value) > _EPSILON,
    )


def resolve_facing_automatic_contract(
    context: FacingAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve only evidence-backed Facing values; never invent process data."""
    dependency = _dependency(context, quality_profile)
    factor = cam_quality_factor(quality_profile)
    diameter = context.diameter
    supported = {
        ToolFamily.END_MILL,
        ToolFamily.BALL_END_MILL,
        ToolFamily.BULL_NOSE_END_MILL,
        ToolFamily.FACE_MILL,
        ToolFamily.CUSTOM,
    }
    inputs = (
        ("tool_family", context.tool_family.value if context.tool_family else None),
        ("diameter", diameter),
        ("corner_radius", context.corner_radius),
        ("axial_cutting_length", context.axial_cutting_length),
        ("depth_span", context.depth_span),
        ("tolerance", context.tolerance),
        ("quality_profile", quality_profile.value),
    )
    values: list[AutomaticParameterValue] = []
    if context.tool_family not in supported or diameter is None or diameter <= 0.0:
        values.append(_unsupported("stepover", dependency, "A supported cutter diameter is required.", inputs))
    else:
        upper = diameter
        if context.tool_family is ToolFamily.BULL_NOSE_END_MILL:
            radius = context.corner_radius
            if radius is None or radius <= 0.0 or radius > diameter / 2.0:
                values.append(_unsupported("stepover", dependency, "Bull Nose corner radius evidence is missing or invalid.", inputs))
            else:
                upper = max(_EPSILON, diameter - radius)
                values.append(_derived("stepover", diameter * factor, dependency, "Derived from cutter diameter, Bull Nose corner radius and quality profile.", inputs, max(context.tolerance or _EPSILON, _EPSILON), upper))
        else:
            values.append(_derived("stepover", diameter * factor, dependency, "Derived from cutter diameter and quality profile.", inputs, max(context.tolerance or _EPSILON, _EPSILON), upper))
    depth = context.depth_span
    axial = context.axial_cutting_length
    if depth is None or axial is None or depth <= 0.0 or axial <= 0.0:
        values.append(_unsupported("stepdown", dependency, "Positive depth-span and axial cutting-length evidence is required.", inputs))
    else:
        values.append(_derived("stepdown", min(depth, axial * factor), dependency, "Derived from operation depth span, cutter axial cutting length and quality profile.", inputs, max(context.tolerance or _EPSILON, _EPSILON), min(depth, axial)))
    if context.variant is FacingAutomaticVariant.STOCK_BOX and diameter is not None and diameter > 0.0 and context.tolerance is not None and context.tolerance > 0.0:
        values.append(_derived("overtravel", max(context.tolerance * 2.0, diameter * 0.25), dependency, "Derived from Stock BOX boundary tolerance and cutter diameter.", inputs, context.tolerance, max(diameter, context.tolerance)))
    else:
        values.append(_unsupported("overtravel", dependency, "Stock BOX boundary and tolerance evidence are required.", inputs))
    return AutomaticParameterContract(
        FACING_AUTOMATIC_POLICY_KEY,
        FACING_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


__all__ = [
    "FACING_AUTOMATIC_KEYS",
    "FACING_AUTOMATIC_POLICY_KEY",
    "FACING_AUTOMATIC_POLICY_VERSION",
    "FacingAutomaticContext",
    "FacingAutomaticVariant",
    "resolve_facing_automatic_contract",
]
