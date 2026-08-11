"""Thread-authority-safe automatic geometry setup for Tapping."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from hms_cadcam.cam.automatic_hole_geometry import (
    HOLE_GEOMETRY_METADATA_KEYS,
    HoleGeometryContext,
    NumericEvidence,
    analyze_hole_geometry,
    automatic_value,
    common_user_values,
    depth_evidence,
    geometry_dependency,
    manual_value,
    merge_hole_automatic_intent,
    metadata_values,
    provenance_inputs,
    unavailable_value,
    validate_hole_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import ToolFamily


TAPPING_AUTOMATIC_POLICY_KEY = "tapping.geometry_setup"
TAPPING_AUTOMATIC_POLICY_VERSION = 1
TAPPING_AUTOMATIC_USER_KEYS = (
    "top_z",
    "final_depth",
    "clearance_height",
    "retract_height",
    "nominal_diameter",
    "pitch",
    "hand",
)
TAPPING_AUTOMATIC_KEYS = (
    *HOLE_GEOMETRY_METADATA_KEYS,
    "thread_source",
    *TAPPING_AUTOMATIC_USER_KEYS,
)


@dataclass(frozen=True, slots=True)
class TappingThreadEvidence:
    """Explicit CAD/feature thread definition; never inferred from a circle."""

    nominal_diameter: float
    pitch: float
    hand: str
    source: str
    thread_depth_source: str | None = None

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0.0
            for value in (self.nominal_diameter, self.pitch)
        ):
            raise ValueError("Authoritative thread dimensions must be finite and positive")
        if self.hand not in {"left", "right"}:
            raise ValueError("Authoritative thread hand is invalid")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Authoritative thread source is invalid")
        if self.thread_depth_source is not None and (
            not isinstance(self.thread_depth_source, str)
            or not self.thread_depth_source.strip()
        ):
            raise ValueError("Authoritative threaded-depth source is invalid")


@dataclass(frozen=True, slots=True)
class TappingAutomaticContext:
    """All validated evidence consumed by the pure Tapping policy."""

    geometry: HoleGeometryContext
    tool_family: ToolFamily | None
    tool_fingerprint: str | None
    tool_nominal_diameter: float | None
    tool_pitch: float | None
    tool_hand: str | None
    tool_threaded_length: float | None
    assembly_stickout: float | None
    manual_top_z: float
    manual_final_depth: float
    manual_clearance_height: float
    manual_retract_height: float
    manual_nominal_diameter: float
    manual_pitch: float
    manual_hand: str
    thread_evidence: TappingThreadEvidence | None = None


def _positive(value: float | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0.0
    )


def _tool_state(context: TappingAutomaticContext) -> tuple[bool, str]:
    if context.tool_family is not ToolFamily.TAP:
        return False, "Tapping AUTO requires the production TAP Tool family."
    if context.tool_fingerprint is None or not context.tool_fingerprint.strip():
        return False, "A current Tap Tool fingerprint is required."
    if not all(
        _positive(value)
        for value in (
            context.tool_nominal_diameter,
            context.tool_pitch,
            context.tool_threaded_length,
            context.assembly_stickout,
        )
    ) or context.tool_hand not in {"left", "right"}:
        return False, "Explicit Tap diameter, pitch, hand, threaded length and stickout are required."
    evidence = context.thread_evidence
    if evidence is not None and (
        abs(float(context.tool_nominal_diameter) - evidence.nominal_diameter)
        > context.geometry.tolerance
        or abs(float(context.tool_pitch) - evidence.pitch) > context.geometry.tolerance
        or context.tool_hand != evidence.hand
    ):
        return False, "Authoritative thread metadata is incompatible with the selected Tap Tool."
    return True, "Current Tap Tool geometry is explicit and compatible."


def resolve_tapping_automatic_contract(
    context: TappingAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve only pattern/plane and explicitly authoritative thread evidence."""
    if not isinstance(context, TappingAutomaticContext):
        raise TypeError("Tapping automatic context is invalid")
    analysis = analyze_hole_geometry(context.geometry)
    tool_inputs = (
        ("tool_family", None if context.tool_family is None else context.tool_family.value),
        ("tool_fingerprint", context.tool_fingerprint),
        ("tool_nominal_diameter", context.tool_nominal_diameter),
        ("tool_pitch", context.tool_pitch),
        ("tool_hand", context.tool_hand),
        ("tool_threaded_length", context.tool_threaded_length),
        ("assembly_stickout", context.assembly_stickout),
    )
    evidence = context.thread_evidence
    policy_inputs = (
        ("thread_source", None if evidence is None else evidence.source),
        ("thread_nominal_diameter", None if evidence is None else evidence.nominal_diameter),
        ("thread_pitch", None if evidence is None else evidence.pitch),
        ("thread_hand", None if evidence is None else evidence.hand),
        (
            "thread_depth_source",
            None if evidence is None else evidence.thread_depth_source,
        ),
    )
    dependency = geometry_dependency(
        context.geometry,
        analysis,
        operation_family="tapping",
        tool_inputs=tool_inputs,
        policy_inputs=policy_inputs,
    )
    inputs = provenance_inputs(
        context.geometry,
        analysis,
        operation_family="tapping",
        tool_inputs=tool_inputs,
        policy_inputs=policy_inputs,
    )
    tool_valid, tool_reason = _tool_state(context)
    eligible = analysis.eligible and tool_valid
    unavailable_reason = analysis.reason if not analysis.eligible else tool_reason
    capacity = (
        min(float(context.tool_threaded_length), float(context.assembly_stickout))
        if tool_valid
        and context.tool_threaded_length is not None
        and context.assembly_stickout is not None
        else None
    )
    depth = (
        depth_evidence(
            context.geometry,
            analysis,
            usable_axial_capacity=capacity,
        )
        if evidence is not None
        and evidence.thread_depth_source is not None
        and context.geometry.depth_source is not None
        and context.geometry.depth_source == evidence.thread_depth_source
        else NumericEvidence(
            None,
            None,
            "thread_depth_absent",
            "Plain-hole depth is not authoritative threaded-feature depth; tapping depth remains user intent.",
        )
    )
    diameter = NumericEvidence(
        None if evidence is None else evidence.nominal_diameter,
        None,
        "thread_metadata_absent" if evidence is None else evidence.source,
        (
            "Plain hole diameter does not define thread standard, pitch or nominal diameter."
            if evidence is None
            else "Explicit authoritative thread metadata is present."
        ),
    )
    values = metadata_values(
        context.geometry,
        analysis,
        eligible=eligible,
        unavailable_reason=unavailable_reason,
        depth=depth,
        diameter=diameter,
        policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
        policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
        dependency=dependency,
        inputs=inputs,
    )
    values.append(
        automatic_value(
            "thread_source",
            "absent" if evidence is None else evidence.source,
            policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
            policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
            dependency=dependency,
            reason=diameter.reason,
            inputs=inputs,
        )
        if eligible
        else unavailable_value(
            "thread_source",
            policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
            policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
            dependency=dependency,
            reason=unavailable_reason,
            inputs=inputs,
        )
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
            policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
            policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
            dependency=dependency,
            inputs=inputs,
        )
    )
    thread_manual = {
        "nominal_diameter": context.manual_nominal_diameter,
        "pitch": context.manual_pitch,
        "hand": context.manual_hand,
    }
    for key, manual in thread_manual.items():
        resolved = None if evidence is None else getattr(evidence, key)
        if eligible and resolved is not None:
            values.append(
                automatic_value(
                    key,
                    resolved,
                    policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
                    policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
                    dependency=dependency,
                    reason="Derived from explicit authoritative thread metadata; no Tool-name inference.",
                    inputs=inputs,
                )
            )
        else:
            values.append(
                manual_value(
                    key,
                    manual,
                    policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
                    policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
                    dependency=dependency,
                    reason=diameter.reason if eligible else unavailable_reason,
                    inputs=inputs,
                )
            )
    return AutomaticParameterContract(
        TAPPING_AUTOMATIC_POLICY_KEY,
        TAPPING_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


def merge_tapping_automatic_intent(
    current: AutomaticParameterContract,
    stored: AutomaticParameterContract | None,
    manual_values: Mapping[str, str | int | float | bool | None],
    *,
    requested_modes: Mapping[str, AutomaticParameterMode] | None = None,
) -> AutomaticParameterContract:
    return merge_hole_automatic_intent(
        current,
        stored,
        TAPPING_AUTOMATIC_USER_KEYS,
        manual_values,
        requested_modes=requested_modes,
        legacy_reason="Legacy explicit Tapping value preserved as manual intent.",
    )


def validate_tapping_automatic_contract(
    stored: AutomaticParameterContract,
    current: AutomaticParameterContract,
) -> None:
    validate_hole_automatic_contract(
        stored,
        current,
        expected_policy_key=TAPPING_AUTOMATIC_POLICY_KEY,
        expected_policy_version=TAPPING_AUTOMATIC_POLICY_VERSION,
        expected_keys=TAPPING_AUTOMATIC_KEYS,
    )


__all__ = [
    "TAPPING_AUTOMATIC_KEYS",
    "TAPPING_AUTOMATIC_POLICY_KEY",
    "TAPPING_AUTOMATIC_POLICY_VERSION",
    "TAPPING_AUTOMATIC_USER_KEYS",
    "TappingAutomaticContext",
    "TappingThreadEvidence",
    "merge_tapping_automatic_intent",
    "resolve_tapping_automatic_contract",
    "validate_tapping_automatic_contract",
]
