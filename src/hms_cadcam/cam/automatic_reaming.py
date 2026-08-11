"""Finished-feature-authority-safe automatic geometry setup for Reaming."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from hms_cadcam.cam.automatic_hole_geometry import (
    HOLE_GEOMETRY_METADATA_KEYS,
    HoleGeometryContext,
    analyze_hole_geometry,
    automatic_value,
    common_user_values,
    depth_evidence,
    finished_diameter_evidence,
    geometry_dependency,
    manual_value,
    merge_hole_automatic_intent,
    metadata_values,
    provenance_inputs,
    validate_hole_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import ToolFamily


REAMING_AUTOMATIC_POLICY_KEY = "reaming.geometry_setup"
REAMING_AUTOMATIC_POLICY_VERSION = 1
REAMING_AUTOMATIC_USER_KEYS = (
    "top_z",
    "final_depth",
    "clearance_height",
    "retract_height",
    "nominal_diameter",
)
REAMING_AUTOMATIC_KEYS = (
    *HOLE_GEOMETRY_METADATA_KEYS,
    *REAMING_AUTOMATIC_USER_KEYS,
)


@dataclass(frozen=True, slots=True)
class ReamingAutomaticContext:
    """All validated evidence consumed by the pure Reaming policy."""

    geometry: HoleGeometryContext
    tool_family: ToolFamily | None
    tool_fingerprint: str | None
    tool_diameter: float | None
    tool_axial_cutting_length: float | None
    assembly_stickout: float | None
    manual_top_z: float
    manual_final_depth: float
    manual_clearance_height: float
    manual_retract_height: float
    manual_nominal_diameter: float


def _positive(value: float | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0.0
    )


def resolve_reaming_automatic_contract(
    context: ReamingAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve geometry-owned values without deriving a target from the Reamer."""
    if not isinstance(context, ReamingAutomaticContext):
        raise TypeError("Reaming automatic context is invalid")
    analysis = analyze_hole_geometry(context.geometry)
    diameter = finished_diameter_evidence(context.geometry, analysis)
    tool_inputs = (
        ("tool_family", None if context.tool_family is None else context.tool_family.value),
        ("tool_fingerprint", context.tool_fingerprint),
        ("tool_diameter", context.tool_diameter),
        ("tool_axial_cutting_length", context.tool_axial_cutting_length),
        ("assembly_stickout", context.assembly_stickout),
    )
    dependency = geometry_dependency(
        context.geometry,
        analysis,
        operation_family="reaming",
        tool_inputs=tool_inputs,
    )
    inputs = provenance_inputs(
        context.geometry,
        analysis,
        operation_family="reaming",
        tool_inputs=tool_inputs,
    )
    if context.tool_family is not ToolFamily.REAMER:
        tool_valid, tool_reason = False, "Reaming AUTO requires the production REAMER Tool family."
    elif context.tool_fingerprint is None or not context.tool_fingerprint.strip():
        tool_valid, tool_reason = False, "A current Reamer Tool fingerprint is required."
    elif not all(
        _positive(value)
        for value in (
            context.tool_diameter,
            context.tool_axial_cutting_length,
            context.assembly_stickout,
        )
    ):
        tool_valid, tool_reason = False, "Explicit positive Reamer diameter, axial cutting length and stickout are required."
    elif diameter.resolved and abs(float(context.tool_diameter) - float(diameter.value)) > context.geometry.tolerance:
        tool_valid, tool_reason = False, "Authoritative finished diameter is incompatible with the selected Reamer."
    else:
        tool_valid, tool_reason = True, "Current Reamer geometry is explicit and compatible."
    eligible = analysis.eligible and tool_valid
    unavailable_reason = analysis.reason if not analysis.eligible else tool_reason
    capacity = (
        min(float(context.tool_axial_cutting_length), float(context.assembly_stickout))
        if tool_valid
        and context.tool_axial_cutting_length is not None
        and context.assembly_stickout is not None
        else None
    )
    depth = depth_evidence(
        context.geometry,
        analysis,
        usable_axial_capacity=capacity,
    )
    values = metadata_values(
        context.geometry,
        analysis,
        eligible=eligible,
        unavailable_reason=unavailable_reason,
        depth=depth,
        diameter=diameter,
        policy_key=REAMING_AUTOMATIC_POLICY_KEY,
        policy_version=REAMING_AUTOMATIC_POLICY_VERSION,
        dependency=dependency,
        inputs=inputs,
    )
    values.extend(
        common_user_values(
            context.geometry,
            analysis,
            eligible=eligible,
            unavailable_reason=unavailable_reason,
            depth=depth,
            manual_top_z=context.manual_top_z,
            manual_final_depth=context.manual_final_depth,
            manual_clearance_height=context.manual_clearance_height,
            manual_retract_height=context.manual_retract_height,
            policy_key=REAMING_AUTOMATIC_POLICY_KEY,
            policy_version=REAMING_AUTOMATIC_POLICY_VERSION,
            dependency=dependency,
            inputs=inputs,
        )
    )
    if eligible and diameter.resolved:
        values.append(
            automatic_value(
                "nominal_diameter",
                diameter.value,
                policy_key=REAMING_AUTOMATIC_POLICY_KEY,
                policy_version=REAMING_AUTOMATIC_POLICY_VERSION,
                dependency=dependency,
                reason=diameter.reason,
                inputs=inputs,
                lower=context.geometry.tolerance,
            )
        )
    else:
        values.append(
            manual_value(
                "nominal_diameter",
                context.manual_nominal_diameter,
                policy_key=REAMING_AUTOMATIC_POLICY_KEY,
                policy_version=REAMING_AUTOMATIC_POLICY_VERSION,
                dependency=dependency,
                reason=diameter.reason if eligible else unavailable_reason,
                inputs=inputs,
            )
        )
    return AutomaticParameterContract(
        REAMING_AUTOMATIC_POLICY_KEY,
        REAMING_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


def merge_reaming_automatic_intent(
    current: AutomaticParameterContract,
    stored: AutomaticParameterContract | None,
    manual_values: Mapping[str, str | int | float | bool | None],
    *,
    requested_modes: Mapping[str, AutomaticParameterMode] | None = None,
) -> AutomaticParameterContract:
    return merge_hole_automatic_intent(
        current,
        stored,
        REAMING_AUTOMATIC_USER_KEYS,
        manual_values,
        requested_modes=requested_modes,
        legacy_reason="Legacy explicit Reaming value preserved as manual intent.",
    )


def validate_reaming_automatic_contract(
    stored: AutomaticParameterContract,
    current: AutomaticParameterContract,
) -> None:
    validate_hole_automatic_contract(
        stored,
        current,
        expected_policy_key=REAMING_AUTOMATIC_POLICY_KEY,
        expected_policy_version=REAMING_AUTOMATIC_POLICY_VERSION,
        expected_keys=REAMING_AUTOMATIC_KEYS,
    )


__all__ = [
    "REAMING_AUTOMATIC_KEYS",
    "REAMING_AUTOMATIC_POLICY_KEY",
    "REAMING_AUTOMATIC_POLICY_VERSION",
    "REAMING_AUTOMATIC_USER_KEYS",
    "ReamingAutomaticContext",
    "merge_reaming_automatic_intent",
    "resolve_reaming_automatic_contract",
    "validate_reaming_automatic_contract",
]
