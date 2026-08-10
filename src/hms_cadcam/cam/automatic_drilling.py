"""Evidence-backed automatic geometry setup for Standard, Spot and Peck drilling."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    DependencyFingerprint,
    DrillingCycle,
    HoleLocation,
    LengthUnit,
    ToolFamily,
)


DRILLING_AUTOMATIC_POLICY_KEY = "drilling.geometry_setup"
DRILLING_AUTOMATIC_POLICY_VERSION = 1
DRILLING_AUTOMATIC_USER_KEYS = (
    "final_depth",
    "top_z",
    "clearance_height",
    "retract_height",
)
DRILLING_AUTOMATIC_KEYS = (
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
    "spot_depth",
    "peck_depth",
    *DRILLING_AUTOMATIC_USER_KEYS,
)
_EPSILON = 1.0e-9
_AXIS_TOLERANCE = 1.0e-8


@dataclass(frozen=True, slots=True)
class DrillingPatternAnalysis:
    """Normalized hole-pattern evidence without CAD-kernel objects."""

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
class DrillingAutomaticContext:
    """Validated production evidence consumed by the pure Drilling AUTO policy."""

    unit: LengthUnit
    cycle: DrillingCycle
    hole_locations: tuple[HoleLocation, ...]
    geometry_fingerprint: str | None
    geometry_resolved: bool
    tool_family: ToolFamily | None
    tool_fingerprint: str | None
    tool_diameter: float | None
    axial_cutting_length: float | None
    assembly_stickout: float | None
    tool_point_angle_degrees: float | None
    manual_top_z: float
    manual_final_depth: float
    manual_clearance_height: float
    manual_retract_height: float
    manual_peck_depth: float | None
    tolerance: float
    authoritative_depth_ranges: tuple[tuple[float, float], ...] = ()
    safe_retract_height: float | None = None
    safe_clearance_height: float | None = None
    safe_plane_source: str | None = None
    spot_target_diameter: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise ValueError("Drilling automatic policy requires a known unit")
        if not isinstance(self.cycle, DrillingCycle):
            raise TypeError("Drilling automatic cycle is invalid")
        if not isinstance(self.hole_locations, tuple) or any(
            not isinstance(item, HoleLocation) for item in self.hole_locations
        ):
            raise TypeError("Drilling automatic hole locations are invalid")
        if type(self.geometry_resolved) is not bool:
            raise TypeError("Drilling geometry resolution state is invalid")
        for name in ("geometry_fingerprint", "tool_fingerprint", "safe_plane_source"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Drilling automatic {name} is invalid")
        for name in (
            "tool_diameter",
            "axial_cutting_length",
            "assembly_stickout",
            "tool_point_angle_degrees",
            "manual_top_z",
            "manual_final_depth",
            "manual_clearance_height",
            "manual_retract_height",
            "tolerance",
            "safe_retract_height",
            "safe_clearance_height",
            "spot_target_diameter",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"Drilling automatic {name} must be finite")
        if self.tolerance <= 0.0:
            raise ValueError("Drilling automatic tolerance must be positive")
        if not isinstance(self.authoritative_depth_ranges, tuple):
            raise TypeError("Drilling authoritative depth evidence must be a tuple")
        for item in self.authoritative_depth_ranges:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in item
                )
            ):
                raise ValueError("Drilling authoritative depth range is invalid")


def analyze_drilling_pattern(
    context: DrillingAutomaticContext,
) -> DrillingPatternAnalysis:
    """Validate and normalize the current resolved 2D +Z hole pattern."""
    if not context.geometry_resolved:
        return _ineligible("Hole geometry is missing, stale or unresolved.")
    if not context.hole_locations:
        return _ineligible("At least one resolved hole location is required.")
    if context.geometry_fingerprint is None:
        return _ineligible("A stable hole-geometry fingerprint is required.")
    ordered: list[tuple[tuple[float, float, float], str, HoleLocation]] = []
    first = context.hole_locations[0]
    if first.unit is not context.unit:
        return _ineligible("Hole-pattern unit does not match the operation.")
    axis = (first.axis.x, first.axis.y, first.axis.z)
    if (
        abs(axis[0]) > _AXIS_TOLERANCE
        or abs(axis[1]) > _AXIS_TOLERANCE
        or axis[2] < 1.0 - _AXIS_TOLERANCE
    ):
        return _ineligible("The current drilling generator supports only the setup +Z axis.")
    plane_z = first.plane_origin.z
    for location in context.hole_locations:
        centre = (location.position.x, location.position.y, location.position.z)
        numeric = (*centre, location.axis.x, location.axis.y, location.axis.z)
        if location.unit is not context.unit or any(not math.isfinite(v) for v in numeric):
            return _ineligible("Hole centres, axes and units must be finite and consistent.")
        if (
            abs(location.axis.x - axis[0]) > _AXIS_TOLERANCE
            or abs(location.axis.y - axis[1]) > _AXIS_TOLERANCE
            or abs(location.axis.z - axis[2]) > _AXIS_TOLERANCE
        ):
            return _ineligible("Selected holes have mixed incompatible drilling axes.")
        if abs(location.plane_origin.z - plane_z) > context.tolerance:
            return _ineligible("Selected holes do not share one machining plane.")
        ordered.append((centre, location.fingerprint.digest, location))
    ordered.sort(key=lambda item: (*item[0], item[1]))
    centres = tuple(item[0] for item in ordered)
    identities = tuple(item[1] for item in ordered)
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
    return DrillingPatternAnalysis(
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


def _ineligible(reason: str) -> DrillingPatternAnalysis:
    return DrillingPatternAnalysis(False, reason, (), (), 0, None, None, None, None, None)


def _tool_valid(context: DrillingAutomaticContext) -> tuple[bool, str]:
    expected = (
        ToolFamily.CENTER_DRILL
        if context.cycle is DrillingCycle.SPOT_DRILL
        else ToolFamily.DRILL
    )
    if context.tool_family is not expected:
        return False, "Selected Tool family is not supported for this drilling cycle."
    if context.tool_fingerprint is None:
        return False, "A current Tool fingerprint is required."
    if any(
        value is None or value <= 0.0
        for value in (
            context.tool_diameter,
            context.axial_cutting_length,
            context.assembly_stickout,
        )
    ):
        return False, "Explicit positive Tool diameter, axial cutting length and stickout are required."
    return True, "Current Tool family and axial geometry match the selected drilling cycle."


def _dependency(
    context: DrillingAutomaticContext,
    pattern: DrillingPatternAnalysis,
) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload(
        {
            "unit": context.unit.value,
            "cycle": context.cycle.value,
            "geometry_fingerprint": context.geometry_fingerprint,
            "pattern_fingerprint": pattern.fingerprint,
            "hole_count": pattern.count,
            "axis": pattern.axis,
            "plane_z": pattern.plane_z,
            "tool_family": None if context.tool_family is None else context.tool_family.value,
            "tool_fingerprint": context.tool_fingerprint,
            "tool_diameter": context.tool_diameter,
            "axial_cutting_length": context.axial_cutting_length,
            "assembly_stickout": context.assembly_stickout,
            "point_angle": context.tool_point_angle_degrees,
            "authoritative_depth_ranges": context.authoritative_depth_ranges,
            "safe_retract_height": context.safe_retract_height,
            "safe_clearance_height": context.safe_clearance_height,
            "safe_plane_source": context.safe_plane_source,
            "spot_target_diameter": context.spot_target_diameter,
            "tolerance": context.tolerance,
        }
    )


def _inputs(
    context: DrillingAutomaticContext,
    pattern: DrillingPatternAnalysis,
) -> tuple[tuple[str, object], ...]:
    return (
        ("operation_type", context.cycle.value),
        ("unit", context.unit.value),
        ("geometry_fingerprint", context.geometry_fingerprint),
        ("pattern_fingerprint", pattern.fingerprint),
        ("hole_count", pattern.count),
        ("axis_x", None if pattern.axis is None else pattern.axis[0]),
        ("axis_y", None if pattern.axis is None else pattern.axis[1]),
        ("axis_z", None if pattern.axis is None else pattern.axis[2]),
        ("plane_z", pattern.plane_z),
        ("tool_family", None if context.tool_family is None else context.tool_family.value),
        ("tool_fingerprint", context.tool_fingerprint),
        ("tool_diameter", context.tool_diameter),
        ("axial_cutting_length", context.axial_cutting_length),
        ("assembly_stickout", context.assembly_stickout),
        ("tool_point_angle_degrees", context.tool_point_angle_degrees),
        ("safe_plane_source", context.safe_plane_source),
        ("spot_target_diameter", context.spot_target_diameter),
    )


def _automatic(
    key: str,
    value: str | int | float,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
    *,
    lower: float | int | None = None,
    upper: float | int | None = None,
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.AUTO,
        value,
        DRILLING_AUTOMATIC_POLICY_KEY,
        DRILLING_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.RESOLVED,
        reason,
        inputs=inputs,
        lower_bound=lower,
        upper_bound=upper,
    )


def _unavailable(
    key: str,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.NOT_APPLICABLE,
        None,
        DRILLING_AUTOMATIC_POLICY_KEY,
        DRILLING_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.UNSUPPORTED,
        reason,
        inputs=inputs,
    )


def _manual(
    key: str,
    value: float | None,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
    *,
    valid: bool = True,
    message: str = "",
) -> AutomaticParameterValue:
    override = (
        float(value)
        if value is not None
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        else None
    )
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.MANUAL_OVERRIDE,
        None,
        DRILLING_AUTOMATIC_POLICY_KEY,
        DRILLING_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.RESOLVED if valid else AutomaticParameterStatus.NEEDS_CONFIRMATION,
        reason,
        override,
        AutomaticValidationResult(valid, message),
        inputs,
    )


def _feature_depth(
    context: DrillingAutomaticContext,
    pattern: DrillingPatternAnalysis,
    capacity: float,
) -> tuple[float, float] | None:
    ranges = context.authoritative_depth_ranges
    if not ranges or len(ranges) != pattern.count:
        return None
    starts = tuple(float(item[0]) for item in ranges)
    ends = tuple(float(item[1]) for item in ranges)
    if any(start - end <= context.tolerance for start, end in zip(starts, ends, strict=True)):
        return None
    if max(starts) - min(starts) > context.tolerance or max(ends) - min(ends) > context.tolerance:
        return None
    if pattern.plane_z is None or abs(starts[0] - pattern.plane_z) > context.tolerance:
        return None
    span = starts[0] - ends[0]
    if span > capacity + context.tolerance:
        return None
    return ends[0], span


def _spot_depth(
    context: DrillingAutomaticContext,
    pattern: DrillingPatternAnalysis,
    capacity: float,
) -> tuple[float, float] | None:
    if (
        context.cycle is not DrillingCycle.SPOT_DRILL
        or pattern.plane_z is None
        or context.spot_target_diameter is None
        or context.tool_diameter is None
        or context.tool_point_angle_degrees is None
    ):
        return None
    target = context.spot_target_diameter
    angle = context.tool_point_angle_degrees
    if target <= 0.0 or target > context.tool_diameter + context.tolerance:
        return None
    if not 0.0 < angle < 180.0:
        return None
    depth = (target / 2.0) / math.tan(math.radians(angle / 2.0))
    if not math.isfinite(depth) or depth <= context.tolerance or depth > capacity + context.tolerance:
        return None
    return pattern.plane_z - depth, depth


def resolve_drilling_automatic_contract(
    context: DrillingAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve geometry authority and preserve every process-owned value as manual."""
    if not isinstance(context, DrillingAutomaticContext):
        raise TypeError("Drilling automatic context is invalid")
    if not isinstance(quality_profile, CamQualityProfile):
        raise TypeError("Drilling automatic quality profile is invalid")
    pattern = analyze_drilling_pattern(context)
    dependency = _dependency(context, pattern)
    inputs = _inputs(context, pattern)
    tool_valid, tool_reason = _tool_valid(context)
    eligible = pattern.eligible and tool_valid
    values: list[AutomaticParameterValue] = []
    metadata = {
        "pattern_count": pattern.count,
        "pattern_fingerprint": pattern.fingerprint,
        "axis_x": None if pattern.axis is None else pattern.axis[0],
        "axis_y": None if pattern.axis is None else pattern.axis[1],
        "axis_z": None if pattern.axis is None else pattern.axis[2],
        "plane_z": pattern.plane_z,
        "bbox_min_x": None if pattern.bounding_box is None else pattern.bounding_box[0],
        "bbox_min_y": None if pattern.bounding_box is None else pattern.bounding_box[1],
        "bbox_max_x": None if pattern.bounding_box is None else pattern.bounding_box[2],
        "bbox_max_y": None if pattern.bounding_box is None else pattern.bounding_box[3],
        "minimum_spacing": pattern.minimum_spacing,
    }
    for key, value in metadata.items():
        if eligible and value is not None:
            values.append(_automatic(key, value, dependency, pattern.reason, inputs))
        else:
            reason = pattern.reason if not pattern.eligible else tool_reason
            values.append(_unavailable(key, dependency, reason, inputs))

    capacity = (
        min(float(context.axial_cutting_length), float(context.assembly_stickout))
        if eligible
        and context.axial_cutting_length is not None
        and context.assembly_stickout is not None
        else 0.0
    )
    spot = _spot_depth(context, pattern, capacity) if eligible else None
    feature = _feature_depth(context, pattern, capacity) if eligible else None
    resolved_depth = spot or feature
    depth_source = "spot_geometry" if spot is not None else "feature_geometry" if feature is not None else "manual"
    values.append(
        _automatic("depth_source", depth_source, dependency, "Depth authority classification is explicit.", inputs)
        if eligible
        else _unavailable("depth_source", dependency, pattern.reason if not pattern.eligible else tool_reason, inputs)
    )
    if resolved_depth is not None:
        final_depth, depth_span = resolved_depth
        values.append(
            _automatic(
                "final_depth",
                final_depth,
                dependency,
                "Derived only from explicit feature depth evidence." if spot is None else "Derived from explicit Spot target diameter and current Center Drill point angle.",
                inputs,
                upper=pattern.plane_z,
            )
        )
    else:
        values.append(
            _manual(
                "final_depth",
                context.manual_final_depth,
                dependency,
                "No authoritative common feature depth is present; target depth remains user intent.",
                inputs,
            )
        )
        depth_span = context.manual_top_z - context.manual_final_depth

    if eligible and pattern.plane_z is not None:
        values.append(
            _automatic(
                "top_z",
                pattern.plane_z,
                dependency,
                "Derived from the resolved common hole machining plane.",
                inputs,
            )
        )
    else:
        values.append(
            _manual("top_z", context.manual_top_z, dependency, pattern.reason if not pattern.eligible else tool_reason, inputs)
        )

    safe = (
        eligible
        and context.safe_plane_source is not None
        and context.safe_retract_height is not None
        and context.safe_clearance_height is not None
        and pattern.plane_z is not None
        and context.safe_retract_height > pattern.plane_z
        and context.safe_clearance_height > context.safe_retract_height
    )
    if safe:
        values.extend(
            (
                _automatic("retract_height", float(context.safe_retract_height), dependency, "Derived from the explicit safe-plane authority.", inputs),
                _automatic("clearance_height", float(context.safe_clearance_height), dependency, "Derived from the explicit safe-plane authority.", inputs),
            )
        )
    else:
        reason = "No authoritative stock/fixture safe-plane source exists; clearance and retract remain manual."
        values.extend(
            (
                _manual("retract_height", context.manual_retract_height, dependency, reason, inputs),
                _manual("clearance_height", context.manual_clearance_height, dependency, reason, inputs),
            )
        )

    if spot is not None:
        values.append(
            _automatic("spot_depth", spot[1], dependency, "Geometric Spot depth from explicit target diameter and Tool point angle.", inputs, lower=context.tolerance, upper=capacity)
        )
    else:
        values.append(
            _unavailable(
                "spot_depth",
                dependency,
                "Spot depth requires an explicit target spot diameter and current Center Drill point angle; hole diameter is not substituted.",
                inputs,
            )
        )

    if context.cycle is DrillingCycle.PECK_DRILL:
        peck = context.manual_peck_depth
        peck_override = (
            float(peck)
            if peck is not None
            and not isinstance(peck, bool)
            and isinstance(peck, (int, float))
            and math.isfinite(float(peck))
            else None
        )
        valid = (
            peck_override is not None
            and peck_override > 0.0
            and depth_span > 0.0
            and peck_override < depth_span
        )
        values.append(
            _manual(
                "peck_depth",
                peck_override,
                dependency,
                "Peck amount is explicit machining-process intent; no material-less AUTO rule is permitted.",
                inputs,
                valid=valid,
                message="Peck depth must be finite, positive and smaller than the drilling depth." if not valid else "",
            )
        )
    else:
        values.append(_unavailable("peck_depth", dependency, "The selected cycle does not use a peck amount.", inputs))

    return AutomaticParameterContract(
        DRILLING_AUTOMATIC_POLICY_KEY,
        DRILLING_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


def merge_drilling_automatic_intent(
    current: AutomaticParameterContract,
    stored: AutomaticParameterContract | None,
    manual_values: Mapping[str, float],
    *,
    requested_modes: Mapping[str, AutomaticParameterMode] | None = None,
) -> AutomaticParameterContract:
    """Preserve legacy/manual intent while allowing explicit reset to current AUTO."""
    if current.policy_key != DRILLING_AUTOMATIC_POLICY_KEY:
        raise ValueError("Current Drilling automatic policy identity is invalid")
    if stored is not None and stored.policy_key != DRILLING_AUTOMATIC_POLICY_KEY:
        raise ValueError("Stored Drilling automatic policy identity is invalid")
    requested_modes = requested_modes or {}
    merged: list[AutomaticParameterValue] = []
    for item in current.values:
        if item.key not in DRILLING_AUTOMATIC_USER_KEYS:
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
                    reason="Legacy explicit Drilling numeric value preserved as manual intent.",
                )
            )
    return replace(current, values=tuple(merged))


def validate_drilling_automatic_contract(
    stored: AutomaticParameterContract,
    current: AutomaticParameterContract,
) -> None:
    """Reject stale persisted AUTO values before generator emission."""
    if (
        stored.policy_key != DRILLING_AUTOMATIC_POLICY_KEY
        or current.policy_key != DRILLING_AUTOMATIC_POLICY_KEY
        or stored.policy_version != DRILLING_AUTOMATIC_POLICY_VERSION
        or current.policy_version != DRILLING_AUTOMATIC_POLICY_VERSION
    ):
        raise ValueError("Drilling automatic policy identity is invalid")
    if {item.key for item in stored.values} != set(DRILLING_AUTOMATIC_KEYS):
        raise ValueError("Drilling automatic contract keys are malformed")
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
            raise ValueError(f"Drilling automatic value is stale: {previous.key}")


__all__ = [
    "DRILLING_AUTOMATIC_KEYS",
    "DRILLING_AUTOMATIC_POLICY_KEY",
    "DRILLING_AUTOMATIC_POLICY_VERSION",
    "DRILLING_AUTOMATIC_USER_KEYS",
    "DrillingAutomaticContext",
    "DrillingPatternAnalysis",
    "analyze_drilling_pattern",
    "merge_drilling_automatic_intent",
    "resolve_drilling_automatic_contract",
    "validate_drilling_automatic_contract",
]
