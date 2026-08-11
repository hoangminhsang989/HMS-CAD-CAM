"""Finished-feature-authority-safe automatic geometry setup for Boring."""

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


BORING_AUTOMATIC_POLICY_KEY = "boring.geometry_setup"
BORING_AUTOMATIC_POLICY_VERSION = 1
BORING_AUTOMATIC_USER_KEYS = (
    "top_z",
    "final_depth",
    "clearance_height",
    "retract_height",
    "finished_bore_diameter",
)
BORING_AUTOMATIC_KEYS = (
    *HOLE_GEOMETRY_METADATA_KEYS,
    *BORING_AUTOMATIC_USER_KEYS,
)


@dataclass(frozen=True, slots=True)
class BoringAutomaticContext:
    """All validated evidence consumed by the pure Boring policy."""

    geometry: HoleGeometryContext
    tool_family: ToolFamily | None
    tool_fingerprint: str | None
    holder_fingerprint: str | None
    tool_minimum_bore_diameter: float | None
    tool_maximum_bore_diameter: float | None
    tool_axial_cutting_length: float | None
    assembly_stickout: float | None
    manual_top_z: float
    manual_final_depth: float
    manual_clearance_height: float
    manual_retract_height: float
    manual_finished_bore_diameter: float


def _positive(value: float | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0.0
    )


def resolve_boring_automatic_contract(
    context: BoringAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve geometry-owned values without inventing radial/head settings."""
    if not isinstance(context, BoringAutomaticContext):
        raise TypeError("Boring automatic context is invalid")
    analysis = analyze_hole_geometry(context.geometry)
    diameter = finished_diameter_evidence(context.geometry, analysis)
    tool_inputs = (
        ("tool_family", None if context.tool_family is None else context.tool_family.value),
        ("tool_fingerprint", context.tool_fingerprint),
        ("holder_fingerprint", context.holder_fingerprint),
        ("tool_minimum_bore_diameter", context.tool_minimum_bore_diameter),
        ("tool_maximum_bore_diameter", context.tool_maximum_bore_diameter),
        ("tool_axial_cutting_length", context.tool_axial_cutting_length),
        ("assembly_stickout", context.assembly_stickout),
    )
    dependency = geometry_dependency(
        context.geometry,
        analysis,
        operation_family="boring",
        tool_inputs=tool_inputs,
    )
    inputs = provenance_inputs(
        context.geometry,
        analysis,
        operation_family="boring",
        tool_inputs=tool_inputs,
    )
    if context.tool_family is not ToolFamily.BORING_BAR:
        tool_valid, tool_reason = False, "Boring AUTO requires the production BORING_BAR Tool family."
    elif (
        context.tool_fingerprint is None
        or not context.tool_fingerprint.strip()
        or context.holder_fingerprint is None
        or not context.holder_fingerprint.strip()
    ):
        tool_valid, tool_reason = False, "Current Boring Tool and holder fingerprints are required."
    elif not all(
        _positive(value)
        for value in (
            context.tool_minimum_bore_diameter,
            context.tool_maximum_bore_diameter,
            context.tool_axial_cutting_length,
            context.assembly_stickout,
        )
    ) or float(context.tool_minimum_bore_diameter) > float(context.tool_maximum_bore_diameter):
        tool_valid, tool_reason = False, "Explicit valid Boring bar range, cutting length and stickout are required."
    elif diameter.resolved and not (
        float(context.tool_minimum_bore_diameter) - context.geometry.tolerance
        <= float(diameter.value)
        <= float(context.tool_maximum_bore_diameter) + context.geometry.tolerance
    ):
        tool_valid, tool_reason = False, "Authoritative finished bore is outside the selected Boring Tool capability range."
    else:
        tool_valid, tool_reason = True, "Current Boring Tool range and holder evidence are explicit."
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
        policy_key=BORING_AUTOMATIC_POLICY_KEY,
        policy_version=BORING_AUTOMATIC_POLICY_VERSION,
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
            policy_key=BORING_AUTOMATIC_POLICY_KEY,
            policy_version=BORING_AUTOMATIC_POLICY_VERSION,
            dependency=dependency,
            inputs=inputs,
        )
    )
    if eligible and diameter.resolved:
        values.append(
            automatic_value(
                "finished_bore_diameter",
                diameter.value,
                policy_key=BORING_AUTOMATIC_POLICY_KEY,
                policy_version=BORING_AUTOMATIC_POLICY_VERSION,
                dependency=dependency,
                reason=diameter.reason,
                inputs=inputs,
                lower=context.tool_minimum_bore_diameter,
                upper=context.tool_maximum_bore_diameter,
            )
        )
    else:
        values.append(
            manual_value(
                "finished_bore_diameter",
                context.manual_finished_bore_diameter,
                policy_key=BORING_AUTOMATIC_POLICY_KEY,
                policy_version=BORING_AUTOMATIC_POLICY_VERSION,
                dependency=dependency,
                reason=diameter.reason if eligible else unavailable_reason,
                inputs=inputs,
            )
        )
    return AutomaticParameterContract(
        BORING_AUTOMATIC_POLICY_KEY,
        BORING_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


def merge_boring_automatic_intent(
    current: AutomaticParameterContract,
    stored: AutomaticParameterContract | None,
    manual_values: Mapping[str, str | int | float | bool | None],
    *,
    requested_modes: Mapping[str, AutomaticParameterMode] | None = None,
) -> AutomaticParameterContract:
    return merge_hole_automatic_intent(
        current,
        stored,
        BORING_AUTOMATIC_USER_KEYS,
        manual_values,
        requested_modes=requested_modes,
        legacy_reason="Legacy explicit Boring value preserved as manual intent.",
    )


def validate_boring_automatic_contract(
    stored: AutomaticParameterContract,
    current: AutomaticParameterContract,
) -> None:
    validate_hole_automatic_contract(
        stored,
        current,
        expected_policy_key=BORING_AUTOMATIC_POLICY_KEY,
        expected_policy_version=BORING_AUTOMATIC_POLICY_VERSION,
        expected_keys=BORING_AUTOMATIC_KEYS,
    )


__all__ = [
    "BORING_AUTOMATIC_KEYS",
    "BORING_AUTOMATIC_POLICY_KEY",
    "BORING_AUTOMATIC_POLICY_VERSION",
    "BORING_AUTOMATIC_USER_KEYS",
    "BoringAutomaticContext",
    "merge_boring_automatic_intent",
    "resolve_boring_automatic_contract",
    "validate_boring_automatic_contract",
]
