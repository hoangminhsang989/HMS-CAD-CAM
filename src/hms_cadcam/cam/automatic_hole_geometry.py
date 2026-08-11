"""Shared native-free hole geometry intelligence for Stage17A operations.

This module owns only evidence normalization and the reusable AUTO-state
mechanics.  Operation-specific thread, reaming and boring semantics remain in
their dedicated policy modules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
)
from hms_cadcam.cam.domain import (
    DependencyFingerprint,
    HoleLocation,
    LengthUnit,
)


HOLE_GEOMETRY_METADATA_KEYS = (
    "pattern_count",
    "pattern_fingerprint",
    "axis_x",
    "axis_y",
    "axis_z",
    "plane_z",
    "bbox_min_x",
    "bbox_min_y",
    "bbox_max_x",
    "bbox_max_y",
    "minimum_spacing",
    "depth_source",
    "diameter_source",
)
_AXIS_TOLERANCE = 1.0e-8

AutomaticPrimitive = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class HoleGeometryContext:
    """Validated production evidence shared by all hole-family policies."""

    unit: LengthUnit
    hole_locations: tuple[HoleLocation, ...]
    geometry_fingerprint: str | None
    geometry_resolved: bool
    tolerance: float
    authoritative_depth_ranges: tuple[tuple[float, float], ...] = ()
    depth_source: str | None = None
    authoritative_finished_diameters: tuple[float, ...] = ()
    diameter_source: str | None = None
    safe_retract_height: float | None = None
    safe_clearance_height: float | None = None
    safe_plane_source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise ValueError("Hole geometry intelligence requires a known unit")
        if not isinstance(self.hole_locations, tuple) or any(
            not isinstance(item, HoleLocation) for item in self.hole_locations
        ):
            raise TypeError("Hole geometry locations are invalid")
        if type(self.geometry_resolved) is not bool:
            raise TypeError("Hole geometry resolution state is invalid")
        for name in (
            "geometry_fingerprint",
            "depth_source",
            "diameter_source",
            "safe_plane_source",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Hole geometry {name} is invalid")
        if (
            isinstance(self.tolerance, bool)
            or not isinstance(self.tolerance, (int, float))
            or not math.isfinite(float(self.tolerance))
            or self.tolerance <= 0.0
        ):
            raise ValueError("Hole geometry tolerance must be finite and positive")
        for name in ("safe_retract_height", "safe_clearance_height"):
            value = getattr(self, name)
            if value is not None and not _finite(value):
                raise ValueError(f"Hole geometry {name} must be finite")
        if not isinstance(self.authoritative_depth_ranges, tuple):
            raise TypeError("Authoritative hole depths must be a tuple")
        for item in self.authoritative_depth_ranges:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or any(not _finite(value) for value in item)
            ):
                raise ValueError("Authoritative hole depth evidence is invalid")
        if not isinstance(self.authoritative_finished_diameters, tuple) or any(
            not _finite(value) for value in self.authoritative_finished_diameters
        ):
            raise ValueError("Authoritative finished-diameter evidence is invalid")
        if bool(self.authoritative_depth_ranges) != (self.depth_source is not None):
            raise ValueError("Depth evidence requires one explicit authoritative source")
        if bool(self.authoritative_finished_diameters) != (
            self.diameter_source is not None
        ):
            raise ValueError("Diameter evidence requires one explicit authoritative source")


@dataclass(frozen=True, slots=True)
class HoleGeometryAnalysis:
    """Normalized deterministic 2D +Z hole-pattern evidence."""

    eligible: bool
    reason: str
    normalized_centres: tuple[tuple[float, float, float], ...]
    ordered_hole_identity: tuple[str, ...]
    count: int
    axis: tuple[float, float, float] | None
    plane_z: float | None
    bounding_box: tuple[float, float, float, float] | None
    minimum_spacing: float | None
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class NumericEvidence:
    """One consensus numeric result or an evidence-backed unavailable state."""

    value: float | None
    span: float | None
    source: str
    reason: str

    @property
    def resolved(self) -> bool:
        return self.value is not None


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def analyze_hole_geometry(context: HoleGeometryContext) -> HoleGeometryAnalysis:
    """Validate and normalize a resolved 2D +Z hole selection."""
    if not context.geometry_resolved:
        return _ineligible("Hole geometry is missing, stale or unresolved.")
    if not context.hole_locations:
        return _ineligible("At least one resolved hole location is required.")
    if context.geometry_fingerprint is None:
        return _ineligible("A stable hole-geometry fingerprint is required.")
    first = context.hole_locations[0]
    if first.unit is not context.unit:
        return _ineligible("Hole-pattern unit does not match the operation.")
    axis = (first.axis.x, first.axis.y, first.axis.z)
    if (
        abs(axis[0]) > _AXIS_TOLERANCE
        or abs(axis[1]) > _AXIS_TOLERANCE
        or axis[2] < 1.0 - _AXIS_TOLERANCE
    ):
        return _ineligible("The current hole-operation generators support only Setup +Z.")
    plane_z = first.plane_origin.z
    ordered: list[tuple[tuple[float, float, float], str]] = []
    for location in context.hole_locations:
        centre = (location.position.x, location.position.y, location.position.z)
        numeric = (
            *centre,
            location.axis.x,
            location.axis.y,
            location.axis.z,
            location.plane_origin.z,
        )
        if location.unit is not context.unit or any(not math.isfinite(v) for v in numeric):
            return _ineligible("Hole centres, axes, planes and units must be finite and consistent.")
        if (
            abs(location.axis.x - axis[0]) > _AXIS_TOLERANCE
            or abs(location.axis.y - axis[1]) > _AXIS_TOLERANCE
            or abs(location.axis.z - axis[2]) > _AXIS_TOLERANCE
        ):
            return _ineligible("Selected holes have mixed incompatible drilling axes.")
        if abs(location.plane_origin.z - plane_z) > context.tolerance:
            return _ineligible("Selected holes do not share one machining plane.")
        ordered.append((centre, location.fingerprint.digest))
    ordered.sort(key=lambda item: (*item[0], item[1]))
    centres = tuple(item[0] for item in ordered)
    identities = tuple(item[1] for item in ordered)
    if len(set(identities)) != len(identities):
        return _ineligible("Duplicate or ambiguous hole identity is not eligible.")
    minimum_spacing: float | None = None
    for index, centre in enumerate(centres):
        for other in centres[index + 1 :]:
            distance = math.dist(centre, other)
            if distance <= context.tolerance:
                return _ineligible("Duplicate or ambiguous hole centres are not eligible.")
            minimum_spacing = (
                distance if minimum_spacing is None else min(minimum_spacing, distance)
            )
    xs = tuple(item[0] for item in centres)
    ys = tuple(item[1] for item in centres)
    bounding = (min(xs), min(ys), max(xs), max(ys))
    dependency = DependencyFingerprint.from_payload(
        {
            "unit": context.unit.value,
            "geometry_fingerprint": context.geometry_fingerprint,
            "centres": centres,
            "identities": identities,
            "axis": axis,
            "plane_z": plane_z,
        }
    )
    return HoleGeometryAnalysis(
        True,
        "Resolved finite hole pattern with one setup-compatible axis and plane.",
        centres,
        identities,
        len(centres),
        axis,
        plane_z,
        bounding,
        minimum_spacing,
        dependency.digest,
    )


def _ineligible(reason: str) -> HoleGeometryAnalysis:
    return HoleGeometryAnalysis(False, reason, (), (), 0, None, None, None, None, None)


def depth_evidence(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
    *,
    usable_axial_capacity: float | None,
) -> NumericEvidence:
    """Resolve one common final Z only from explicitly typed feature depths."""
    if not analysis.eligible:
        return NumericEvidence(None, None, "unavailable", analysis.reason)
    if not context.authoritative_depth_ranges or context.depth_source is None:
        return NumericEvidence(
            None,
            None,
            "absent",
            "No authoritative feature-depth evidence is present; depth remains user intent.",
        )
    if len(context.authoritative_depth_ranges) != analysis.count:
        return NumericEvidence(
            None,
            None,
            "incompatible_group",
            "Grouped holes do not provide one authoritative depth per selected hole.",
        )
    starts = tuple(float(item[0]) for item in context.authoritative_depth_ranges)
    ends = tuple(float(item[1]) for item in context.authoritative_depth_ranges)
    if any(start - end <= context.tolerance for start, end in zip(starts, ends, strict=True)):
        return NumericEvidence(None, None, "invalid", "Authoritative hole depth must be positive along Setup -Z.")
    if max(starts) - min(starts) > context.tolerance or max(ends) - min(ends) > context.tolerance:
        return NumericEvidence(None, None, "incompatible_group", "Grouped holes have incompatible authoritative depths.")
    if analysis.plane_z is None or abs(starts[0] - analysis.plane_z) > context.tolerance:
        return NumericEvidence(None, None, "plane_mismatch", "Authoritative feature depth does not start at the machining plane.")
    span = starts[0] - ends[0]
    if (
        usable_axial_capacity is None
        or not _finite(usable_axial_capacity)
        or usable_axial_capacity <= 0.0
    ):
        return NumericEvidence(None, None, "tool_capacity_missing", "Validated usable axial Tool capacity is required for AUTO depth.")
    if span > usable_axial_capacity + context.tolerance:
        return NumericEvidence(None, None, "tool_capacity_exceeded", "Authoritative feature depth exceeds validated usable axial Tool capacity.")
    return NumericEvidence(
        ends[0],
        span,
        context.depth_source,
        "Derived only from explicit authoritative feature-depth evidence.",
    )


def finished_diameter_evidence(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
) -> NumericEvidence:
    """Resolve one target diameter without treating source-hole diameter as design intent."""
    if not analysis.eligible:
        return NumericEvidence(None, None, "unavailable", analysis.reason)
    if not context.authoritative_finished_diameters or context.diameter_source is None:
        classification = (
            "unqualified_source_hole_diameter"
            if any(item.diameter is not None for item in context.hole_locations)
            else "absent"
        )
        return NumericEvidence(
            None,
            None,
            classification,
            "No authoritative finished-feature diameter is present; source-hole and Tool diameters are not substituted.",
        )
    values = tuple(float(value) for value in context.authoritative_finished_diameters)
    if len(values) != analysis.count:
        return NumericEvidence(None, None, "incompatible_group", "Grouped holes do not provide one finished diameter per selected hole.")
    if any(value <= context.tolerance for value in values):
        return NumericEvidence(None, None, "invalid", "Authoritative finished-feature diameter must be positive.")
    if max(values) - min(values) > context.tolerance:
        return NumericEvidence(None, None, "incompatible_group", "Grouped holes have incompatible authoritative finished diameters.")
    return NumericEvidence(values[0], None, context.diameter_source, "Derived only from explicit authoritative finished-feature geometry.")


def geometry_dependency(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
    *,
    operation_family: str,
    tool_inputs: Sequence[tuple[str, AutomaticPrimitive]],
    policy_inputs: Sequence[tuple[str, AutomaticPrimitive]] = (),
) -> DependencyFingerprint:
    """Fingerprint every dependency that can change one hole-completion result."""
    return DependencyFingerprint.from_payload(
        {
            "operation_family": operation_family,
            "unit": context.unit.value,
            "geometry_fingerprint": context.geometry_fingerprint,
            "pattern_fingerprint": analysis.fingerprint,
            "hole_count": analysis.count,
            "axis": analysis.axis,
            "plane_z": analysis.plane_z,
            "depth_ranges": context.authoritative_depth_ranges,
            "depth_source": context.depth_source,
            "finished_diameters": context.authoritative_finished_diameters,
            "diameter_source": context.diameter_source,
            "safe_retract_height": context.safe_retract_height,
            "safe_clearance_height": context.safe_clearance_height,
            "safe_plane_source": context.safe_plane_source,
            "tolerance": context.tolerance,
            "tool": tuple(tool_inputs),
            "policy": tuple(policy_inputs),
        }
    )


def provenance_inputs(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
    *,
    operation_family: str,
    tool_inputs: Sequence[tuple[str, AutomaticPrimitive]],
    policy_inputs: Sequence[tuple[str, AutomaticPrimitive]] = (),
) -> tuple[tuple[str, AutomaticPrimitive], ...]:
    """Return compact scalar provenance accepted by AutomaticParameterValue."""
    result: list[tuple[str, AutomaticPrimitive]] = [
        ("operation_family", operation_family),
        ("unit", context.unit.value),
        ("geometry_fingerprint", context.geometry_fingerprint),
        ("pattern_fingerprint", analysis.fingerprint),
        ("hole_count", analysis.count),
        ("axis_x", None if analysis.axis is None else analysis.axis[0]),
        ("axis_y", None if analysis.axis is None else analysis.axis[1]),
        ("axis_z", None if analysis.axis is None else analysis.axis[2]),
        ("plane_z", analysis.plane_z),
        ("depth_source", context.depth_source),
        ("diameter_source", context.diameter_source),
        ("safe_plane_source", context.safe_plane_source),
    ]
    result.extend(tool_inputs)
    result.extend(policy_inputs)
    if len({key for key, _value in result}) != len(result):
        raise ValueError("Hole geometry provenance keys must be unique")
    return tuple(result)


def automatic_value(
    key: str,
    value: AutomaticPrimitive,
    *,
    policy_key: str,
    policy_version: int,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, AutomaticPrimitive], ...],
    lower: AutomaticPrimitive = None,
    upper: AutomaticPrimitive = None,
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.AUTO,
        value,
        policy_key,
        policy_version,
        dependency,
        AutomaticParameterStatus.RESOLVED,
        reason,
        inputs=inputs,
        lower_bound=lower,
        upper_bound=upper,
    )


def unavailable_value(
    key: str,
    *,
    policy_key: str,
    policy_version: int,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, AutomaticPrimitive], ...],
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.NOT_APPLICABLE,
        None,
        policy_key,
        policy_version,
        dependency,
        AutomaticParameterStatus.UNSUPPORTED,
        reason,
        inputs=inputs,
    )


def manual_value(
    key: str,
    value: AutomaticPrimitive,
    *,
    policy_key: str,
    policy_version: int,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, AutomaticPrimitive], ...],
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.MANUAL_OVERRIDE,
        None,
        policy_key,
        policy_version,
        dependency,
        AutomaticParameterStatus.RESOLVED,
        reason,
        value,
        AutomaticValidationResult(True),
        inputs,
    )


def metadata_values(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
    *,
    eligible: bool,
    unavailable_reason: str,
    depth: NumericEvidence,
    diameter: NumericEvidence,
    policy_key: str,
    policy_version: int,
    dependency: DependencyFingerprint,
    inputs: tuple[tuple[str, AutomaticPrimitive], ...],
) -> list[AutomaticParameterValue]:
    """Build the shared pattern and evidence-classification AUTO state."""
    metadata: dict[str, AutomaticPrimitive] = {
        "pattern_count": analysis.count,
        "pattern_fingerprint": analysis.fingerprint,
        "axis_x": None if analysis.axis is None else analysis.axis[0],
        "axis_y": None if analysis.axis is None else analysis.axis[1],
        "axis_z": None if analysis.axis is None else analysis.axis[2],
        "plane_z": analysis.plane_z,
        "bbox_min_x": None if analysis.bounding_box is None else analysis.bounding_box[0],
        "bbox_min_y": None if analysis.bounding_box is None else analysis.bounding_box[1],
        "bbox_max_x": None if analysis.bounding_box is None else analysis.bounding_box[2],
        "bbox_max_y": None if analysis.bounding_box is None else analysis.bounding_box[3],
        "minimum_spacing": analysis.minimum_spacing,
        "depth_source": depth.source,
        "diameter_source": diameter.source,
    }
    values: list[AutomaticParameterValue] = []
    for key, value in metadata.items():
        if eligible and value is not None:
            values.append(
                automatic_value(
                    key,
                    value,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason=(
                        "Authoritative depth evidence classification."
                        if key == "depth_source"
                        else "Authoritative diameter evidence classification."
                        if key == "diameter_source"
                        else analysis.reason
                    ),
                    inputs=inputs,
                )
            )
        else:
            values.append(
                unavailable_value(
                    key,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason=unavailable_reason,
                    inputs=inputs,
                )
            )
    return values


def common_user_values(
    context: HoleGeometryContext,
    analysis: HoleGeometryAnalysis,
    *,
    eligible: bool,
    unavailable_reason: str,
    depth: NumericEvidence,
    manual_top_z: float,
    manual_final_depth: float,
    manual_clearance_height: float,
    manual_retract_height: float,
    policy_key: str,
    policy_version: int,
    dependency: DependencyFingerprint,
    inputs: tuple[tuple[str, AutomaticPrimitive], ...],
) -> list[AutomaticParameterValue]:
    """Build shared reference/depth/safe-plane values for one operation policy."""
    values: list[AutomaticParameterValue] = []
    if eligible and analysis.plane_z is not None:
        values.append(
            automatic_value(
                "top_z",
                analysis.plane_z,
                policy_key=policy_key,
                policy_version=policy_version,
                dependency=dependency,
                reason="Derived from the resolved common hole machining plane.",
                inputs=inputs,
            )
        )
    else:
        values.append(
            manual_value(
                "top_z",
                manual_top_z,
                policy_key=policy_key,
                policy_version=policy_version,
                dependency=dependency,
                reason=unavailable_reason,
                inputs=inputs,
            )
        )
    if eligible and depth.resolved:
        values.append(
            automatic_value(
                "final_depth",
                depth.value,
                policy_key=policy_key,
                policy_version=policy_version,
                dependency=dependency,
                reason=depth.reason,
                inputs=inputs,
                upper=analysis.plane_z,
            )
        )
    else:
        values.append(
            manual_value(
                "final_depth",
                manual_final_depth,
                policy_key=policy_key,
                policy_version=policy_version,
                dependency=dependency,
                reason=depth.reason if eligible else unavailable_reason,
                inputs=inputs,
            )
        )
    safe = (
        eligible
        and context.safe_plane_source is not None
        and context.safe_retract_height is not None
        and context.safe_clearance_height is not None
        and analysis.plane_z is not None
        and context.safe_retract_height > analysis.plane_z
        and context.safe_clearance_height > context.safe_retract_height
    )
    if safe:
        values.extend(
            (
                automatic_value(
                    "retract_height",
                    context.safe_retract_height,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason="Derived from explicit stock/fixture safe-plane authority.",
                    inputs=inputs,
                ),
                automatic_value(
                    "clearance_height",
                    context.safe_clearance_height,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason="Derived from explicit stock/fixture safe-plane authority.",
                    inputs=inputs,
                ),
            )
        )
    else:
        reason = "No authoritative stock/fixture safe-plane source exists; clearance and retract remain manual."
        values.extend(
            (
                manual_value(
                    "retract_height",
                    manual_retract_height,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason=reason,
                    inputs=inputs,
                ),
                manual_value(
                    "clearance_height",
                    manual_clearance_height,
                    policy_key=policy_key,
                    policy_version=policy_version,
                    dependency=dependency,
                    reason=reason,
                    inputs=inputs,
                ),
            )
        )
    return values


def merge_hole_automatic_intent(
    current: AutomaticParameterContract,
    stored: AutomaticParameterContract | None,
    user_keys: Sequence[str],
    manual_values: Mapping[str, AutomaticPrimitive],
    *,
    requested_modes: Mapping[str, AutomaticParameterMode] | None = None,
    legacy_reason: str,
) -> AutomaticParameterContract:
    """Preserve legacy/manual intent and explicit reset-to-AUTO semantics."""
    if stored is not None and stored.policy_key != current.policy_key:
        raise ValueError("Stored hole automatic policy identity is invalid")
    requested_modes = requested_modes or {}
    user_key_set = set(user_keys)
    if set(manual_values) != user_key_set:
        raise ValueError("Hole automatic manual-value mapping is incomplete")
    merged: list[AutomaticParameterValue] = []
    for item in current.values:
        if item.key not in user_key_set:
            merged.append(item)
            continue
        requested = requested_modes.get(item.key)
        previous: AutomaticParameterValue | None = None
        if stored is not None:
            try:
                previous = stored.value(item.key)
            except KeyError:
                previous = None
        if requested is AutomaticParameterMode.AUTO:
            if item.mode is AutomaticParameterMode.AUTO and item.status is AutomaticParameterStatus.RESOLVED:
                merged.append(item)
            elif previous is not None and previous.mode is AutomaticParameterMode.AUTO:
                merged.append(
                    replace(
                        item,
                        mode=AutomaticParameterMode.AUTO,
                        resolved_value=previous.resolved_value,
                        status=AutomaticParameterStatus.UNRESOLVED,
                        reason=f"AUTO intent preserved while current evidence is unavailable: {item.reason}",
                    )
                )
            else:
                merged.append(item)
            continue
        if requested is AutomaticParameterMode.MANUAL_OVERRIDE:
            merged.append(
                replace(
                    item,
                    mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                    override_value=manual_values[item.key],
                    validation=AutomaticValidationResult(True),
                    reason="Explicit Advanced manual override.",
                )
            )
            continue
        if previous is not None and previous.mode is AutomaticParameterMode.AUTO:
            if item.mode is AutomaticParameterMode.AUTO:
                merged.append(item)
            else:
                merged.append(
                    replace(
                        item,
                        mode=AutomaticParameterMode.AUTO,
                        resolved_value=previous.resolved_value,
                        status=AutomaticParameterStatus.UNRESOLVED,
                        reason=f"AUTO intent preserved while current evidence is unavailable: {item.reason}",
                    )
                )
        elif previous is not None and previous.has_manual_override:
            merged.append(
                replace(
                    item,
                    mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                    override_value=previous.override_value,
                    validation=previous.validation,
                    reason=previous.reason,
                )
            )
        else:
            merged.append(
                replace(
                    item,
                    mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                    override_value=manual_values[item.key],
                    validation=AutomaticValidationResult(True),
                    reason=legacy_reason,
                )
            )
    return replace(current, values=tuple(merged))


def validate_hole_automatic_contract(
    stored: AutomaticParameterContract,
    current: AutomaticParameterContract,
    *,
    expected_policy_key: str,
    expected_policy_version: int,
    expected_keys: Sequence[str],
) -> None:
    """Reject malformed or stale persisted AUTO state before toolpath emission."""
    if (
        stored.policy_key != expected_policy_key
        or current.policy_key != expected_policy_key
        or stored.policy_version != expected_policy_version
        or current.policy_version != expected_policy_version
    ):
        raise ValueError("Hole automatic policy identity is invalid")
    if {item.key for item in stored.values} != set(expected_keys):
        raise ValueError("Hole automatic contract keys are malformed")
    for previous in stored.values:
        if previous.mode is not AutomaticParameterMode.AUTO:
            continue
        now = current.value(previous.key)
        if (
            now.mode is not AutomaticParameterMode.AUTO
            or now.status is not AutomaticParameterStatus.RESOLVED
            or now.dependency_fingerprint != previous.dependency_fingerprint
            or now.effective_value != previous.effective_value
        ):
            raise ValueError(f"Hole automatic value is stale: {previous.key}")


__all__ = [
    "HOLE_GEOMETRY_METADATA_KEYS",
    "HoleGeometryAnalysis",
    "HoleGeometryContext",
    "NumericEvidence",
    "analyze_hole_geometry",
    "automatic_value",
    "common_user_values",
    "depth_evidence",
    "finished_diameter_evidence",
    "geometry_dependency",
    "manual_value",
    "merge_hole_automatic_intent",
    "metadata_values",
    "provenance_inputs",
    "unavailable_value",
    "validate_hole_automatic_contract",
]
