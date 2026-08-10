"""Evidence-backed automatic setup for one closed Pocket 2D region."""

from __future__ import annotations

from dataclasses import dataclass
import math

from hms_cadcam.cam.automatic_contour import reorder_contour_entry
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
    ContourCurveKind,
    ContourLoop,
    ContourSegment,
    DependencyFingerprint,
    LengthUnit,
    Point3,
    ToolFamily,
)


POCKET_AUTOMATIC_POLICY_KEY = "pocket.operation_intelligence"
POCKET_AUTOMATIC_POLICY_VERSION = 1
POCKET_AUTOMATIC_USER_KEYS = ("stepdown", "stepover")
POCKET_AUTOMATIC_KEYS = (
    *POCKET_AUTOMATIC_USER_KEYS,
    "entry_loop_index",
    "entry_segment_index",
    "entry_point_x",
    "entry_point_y",
    "entry_clearance",
    "entry_form",
    "linking_mode",
)
POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES = frozenset({ToolFamily.END_MILL})

_EPSILON = 1.0e-9
_GEOMETRY_TOLERANCE = 1.0e-8


@dataclass(frozen=True, slots=True)
class PocketAutomaticContext:
    """Validated native-free evidence consumed by the Pocket AUTO policy."""

    unit: LengthUnit
    tool_family: ToolFamily | None
    diameter: float | None
    axial_cutting_length: float | None
    assembly_stickout: float | None
    depth_span: float | None
    tolerance: float | None
    source_loop: ContourLoop | None
    offset_loops: tuple[ContourLoop, ...]
    pocket_fingerprint: str | None
    outer_loop_fingerprint: str | None
    island_fingerprint: str | None
    tool_fingerprint: str | None
    accessibility_result: str

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise ValueError("Pocket automatic policy requires a known unit")
        for name in (
            "diameter",
            "axial_cutting_length",
            "assembly_stickout",
            "depth_span",
            "tolerance",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise ValueError(f"Pocket automatic {name} is invalid")
        if self.source_loop is not None and any(
            segment.unit is not self.unit for segment in self.source_loop.segments
        ):
            raise ValueError("Pocket automatic source-loop unit is inconsistent")
        if not isinstance(self.offset_loops, tuple) or any(
            not isinstance(loop, ContourLoop)
            or any(segment.unit is not self.unit for segment in loop.segments)
            for loop in self.offset_loops
        ):
            raise TypeError("Pocket automatic offset loops are invalid")
        for name in (
            "pocket_fingerprint",
            "outer_loop_fingerprint",
            "island_fingerprint",
            "tool_fingerprint",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Pocket automatic {name} is invalid")
        if not isinstance(self.accessibility_result, str) or not self.accessibility_result:
            raise ValueError("Pocket automatic accessibility result is invalid")


@dataclass(frozen=True, slots=True)
class PocketAutomaticEntryPlacement:
    """One deterministic cutter-centre entry point on a reachable offset loop."""

    loop_index: int
    segment_index: int
    point_x: float
    point_y: float
    local_clearance: float

    def __post_init__(self) -> None:
        if type(self.loop_index) is not int or self.loop_index < 0:
            raise ValueError("Pocket automatic entry loop index is invalid")
        if type(self.segment_index) is not int or self.segment_index < 0:
            raise ValueError("Pocket automatic entry segment index is invalid")
        for value in (self.point_x, self.point_y, self.local_clearance):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("Pocket automatic entry geometry is invalid")
        if self.local_clearance <= 0.0:
            raise ValueError("Pocket automatic entry clearance must be positive")


def pocket_geometric_stepover_target(
    diameter: float,
    tolerance: float,
    quality_profile: CamQualityProfile,
) -> tuple[float, float, float, bool]:
    """Return a geometric coverage step strictly below the cutter diameter."""
    if (
        isinstance(diameter, bool)
        or not isinstance(diameter, (int, float))
        or not math.isfinite(diameter)
        or diameter <= 0.0
        or isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0.0
        or not isinstance(quality_profile, CamQualityProfile)
    ):
        raise ValueError("Pocket geometric stepover evidence is invalid")
    lower = max(float(tolerance), _EPSILON)
    upper = float(diameter) - lower
    if upper <= lower:
        raise ValueError("Pocket cutter diameter has no positive stepover interval")
    desired = float(diameter) * cam_quality_factor(quality_profile)
    resolved = min(max(desired, lower), upper)
    if not math.isfinite(resolved) or not lower <= resolved <= upper or resolved >= diameter:
        raise ValueError("Pocket geometric stepover could not be bounded safely")
    return resolved, lower, upper, abs(resolved - desired) > _EPSILON


def _dependency(
    context: PocketAutomaticContext,
    quality_profile: CamQualityProfile,
) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload(
        {
            "unit": context.unit.value,
            "tool_family": (
                context.tool_family.value if context.tool_family is not None else None
            ),
            "diameter": context.diameter,
            "axial_cutting_length": context.axial_cutting_length,
            "assembly_stickout": context.assembly_stickout,
            "depth_span": context.depth_span,
            "tolerance": context.tolerance,
            "pocket_fingerprint": context.pocket_fingerprint,
            "outer_loop_fingerprint": context.outer_loop_fingerprint,
            "island_fingerprint": context.island_fingerprint,
            "tool_fingerprint": context.tool_fingerprint,
            "source_loop": (
                None if context.source_loop is None else context.source_loop.to_dict()
            ),
            "offset_loops": [loop.to_dict() for loop in context.offset_loops],
            "accessibility_result": context.accessibility_result,
            "quality_profile": quality_profile.value,
        }
    )


def _inputs(
    context: PocketAutomaticContext,
    quality_profile: CamQualityProfile,
) -> tuple[tuple[str, object], ...]:
    return (
        (
            "tool_family",
            context.tool_family.value if context.tool_family is not None else None,
        ),
        ("diameter", context.diameter),
        ("axial_cutting_length", context.axial_cutting_length),
        ("assembly_stickout", context.assembly_stickout),
        ("depth_span", context.depth_span),
        ("tolerance", context.tolerance),
        ("quality_profile", quality_profile.value),
        ("unit", context.unit.value),
        ("pocket_fingerprint", context.pocket_fingerprint),
        ("outer_loop_fingerprint", context.outer_loop_fingerprint),
        ("island_fingerprint", context.island_fingerprint),
        ("tool_fingerprint", context.tool_fingerprint),
        ("accessibility_result", context.accessibility_result),
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
        POCKET_AUTOMATIC_POLICY_KEY,
        POCKET_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.UNSUPPORTED,
        reason,
        None,
        AutomaticValidationResult(True),
        inputs,
    )


def _derived(
    key: str,
    value: str | int | float,
    dependency: DependencyFingerprint,
    reason: str,
    inputs: tuple[tuple[str, object], ...],
    *,
    lower: int | float | None = None,
    upper: int | float | None = None,
    clamped: bool = False,
) -> AutomaticParameterValue:
    return AutomaticParameterValue(
        key,
        AutomaticParameterMode.AUTO,
        value,
        POCKET_AUTOMATIC_POLICY_KEY,
        POCKET_AUTOMATIC_POLICY_VERSION,
        dependency,
        AutomaticParameterStatus.RESOLVED,
        reason,
        None,
        AutomaticValidationResult(True),
        inputs,
        lower,
        upper,
        clamped,
    )


def _segment_length(segment: ContourSegment) -> float:
    if segment.kind is ContourCurveKind.LINE:
        return math.hypot(
            segment.end.x - segment.start.x,
            segment.end.y - segment.start.y,
        )
    assert segment.radius is not None and segment.sweep_radians is not None
    return segment.radius * abs(segment.sweep_radians)


def _segment_midpoint(segment: ContourSegment) -> Point3:
    if segment.kind is ContourCurveKind.LINE:
        return Point3(
            (segment.start.x + segment.end.x) / 2.0,
            (segment.start.y + segment.end.y) / 2.0,
            segment.start.z,
            segment.unit,
        )
    assert segment.center is not None
    assert segment.radius is not None
    assert segment.sweep_radians is not None
    start_angle = math.atan2(
        segment.start.y - segment.center.y,
        segment.start.x - segment.center.x,
    )
    angle = start_angle + segment.sweep_radians / 2.0
    return Point3(
        segment.center.x + segment.radius * math.cos(angle),
        segment.center.y + segment.radius * math.sin(angle),
        segment.start.z,
        segment.unit,
    )


def _sample_loop(loop: ContourLoop) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for segment in loop.segments:
        if not points:
            points.append((segment.start.x, segment.start.y))
        if segment.kind is ContourCurveKind.LINE:
            points.append((segment.end.x, segment.end.y))
            continue
        assert segment.center is not None
        assert segment.sweep_radians is not None
        count = max(2, math.ceil(abs(segment.sweep_radians) / math.radians(5.0)))
        start_angle = math.atan2(
            segment.start.y - segment.center.y,
            segment.start.x - segment.center.x,
        )
        assert segment.radius is not None
        points.extend(
            (
                segment.center.x
                + segment.radius
                * math.cos(start_angle + segment.sweep_radians * index / count),
                segment.center.y
                + segment.radius
                * math.sin(start_angle + segment.sweep_radians * index / count),
            )
            for index in range(1, count + 1)
        )
    if math.dist(points[0], points[-1]) > _GEOMETRY_TOLERANCE:
        points.append(points[0])
    else:
        points[-1] = points[0]
    return tuple(points)


def _point_in_polygon(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    inside = False
    x, y = point
    for first, second in zip(polygon, polygon[1:]):
        if (first[1] > y) != (second[1] > y):
            crossing = (
                (second[0] - first[0])
                * (y - first[1])
                / (second[1] - first[1])
                + first[0]
            )
            if x < crossing:
                inside = not inside
    return inside


def _point_segment_distance(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator <= _EPSILON**2:
        return math.dist(point, first)
    ratio = min(
        1.0,
        max(
            0.0,
            ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy)
            / denominator,
        ),
    )
    closest = first[0] + ratio * dx, first[1] + ratio * dy
    return math.dist(point, closest)


def _boundary_clearance(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> float:
    return min(
        _point_segment_distance(point, first, second)
        for first, second in zip(polygon, polygon[1:])
    )


def _entry_placement(
    context: PocketAutomaticContext,
) -> PocketAutomaticEntryPlacement | None:
    if (
        context.source_loop is None
        or not context.offset_loops
        or context.diameter is None
        or context.diameter <= 0.0
    ):
        return None
    source_polygon = _sample_loop(context.source_loop)
    loop = context.offset_loops[0]
    tolerance = max(context.tolerance or _EPSILON, _EPSILON)
    cutter_radius = context.diameter / 2.0
    ranked: list[tuple[tuple[float, float, float, float, int], PocketAutomaticEntryPlacement]] = []
    for segment_index, segment in enumerate(loop.segments):
        length = _segment_length(segment)
        if not math.isfinite(length) or length <= tolerance:
            continue
        midpoint = _segment_midpoint(segment)
        point = midpoint.x, midpoint.y
        if not _point_in_polygon(point, source_polygon):
            continue
        clearance = _boundary_clearance(point, source_polygon)
        if not math.isfinite(clearance) or clearance + tolerance < cutter_radius:
            continue
        placement = PocketAutomaticEntryPlacement(
            0,
            segment_index,
            midpoint.x,
            midpoint.y,
            clearance,
        )
        ranked.append(
            (
                (-clearance, -length, midpoint.x, midpoint.y, segment_index),
                placement,
            )
        )
    return None if not ranked else min(ranked, key=lambda item: item[0])[1]


def pocket_automatic_entry_loops(
    source_loop: ContourLoop,
    offset_loops: tuple[ContourLoop, ...],
    placement: PocketAutomaticEntryPlacement,
    *,
    cutter_radius: float,
    tolerance: float,
) -> tuple[ContourLoop, ...]:
    """Revalidate and apply one persisted cutter-centre entry placement."""
    if (
        not isinstance(source_loop, ContourLoop)
        or not isinstance(offset_loops, tuple)
        or not offset_loops
        or not isinstance(placement, PocketAutomaticEntryPlacement)
        or placement.loop_index >= len(offset_loops)
        or isinstance(cutter_radius, bool)
        or not isinstance(cutter_radius, (int, float))
        or not math.isfinite(cutter_radius)
        or cutter_radius <= 0.0
        or isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or tolerance <= 0.0
    ):
        raise ValueError("Persisted Pocket entry placement evidence is invalid")
    loop = offset_loops[placement.loop_index]
    if placement.segment_index >= len(loop.segments):
        raise ValueError("Persisted Pocket entry segment is no longer available")
    reordered = reorder_contour_entry(loop, placement.segment_index)
    start = reordered.segments[0].start
    if (
        not math.isclose(start.x, placement.point_x, rel_tol=0.0, abs_tol=tolerance)
        or not math.isclose(start.y, placement.point_y, rel_tol=0.0, abs_tol=tolerance)
    ):
        raise ValueError("Persisted Pocket entry point no longer matches geometry")
    polygon = _sample_loop(source_loop)
    point = start.x, start.y
    clearance = _boundary_clearance(point, polygon)
    if (
        not _point_in_polygon(point, polygon)
        or clearance + tolerance < cutter_radius
        or clearance + tolerance < placement.local_clearance
    ):
        raise ValueError("Persisted Pocket entry point is no longer cutter-accessible")
    updated = list(offset_loops)
    updated[placement.loop_index] = reordered
    return tuple(updated)


def resolve_pocket_automatic_contract(
    context: PocketAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve geometry-only Pocket setup without inventing process data."""
    if not isinstance(context, PocketAutomaticContext):
        raise TypeError("Pocket automatic context is invalid")
    if not isinstance(quality_profile, CamQualityProfile):
        raise TypeError("Pocket automatic quality profile is invalid")
    dependency = _dependency(context, quality_profile)
    inputs = _inputs(context, quality_profile)
    tool_valid = (
        context.tool_family in POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES
        and context.diameter is not None
        and context.diameter > 0.0
        and context.axial_cutting_length is not None
        and context.axial_cutting_length > 0.0
        and context.assembly_stickout is not None
        and context.assembly_stickout > 0.0
    )
    geometry_valid = (
        context.source_loop is not None
        and bool(context.offset_loops)
        and context.pocket_fingerprint is not None
        and context.outer_loop_fingerprint is not None
        and context.island_fingerprint is None
        and context.accessibility_result == "reachable"
    )
    values: list[AutomaticParameterValue] = []

    capacity = (
        min(context.axial_cutting_length, context.assembly_stickout)
        if tool_valid
        and context.axial_cutting_length is not None
        and context.assembly_stickout is not None
        else None
    )
    depth = context.depth_span
    if not geometry_valid:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "A validated cutter-accessible closed Pocket region is required.",
                inputs,
            )
        )
    elif not tool_valid:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Pocket AUTO requires a generator-supported End Mill with axial geometry and stickout.",
                inputs,
            )
        )
    elif depth is None or depth <= 0.0 or capacity is None or capacity <= 0.0:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Positive Pocket depth span and usable axial capacity are required.",
                inputs,
            )
        )
    elif depth > capacity + _GEOMETRY_TOLERANCE:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Pocket depth exceeds validated axial cutting length or stickout.",
                inputs,
            )
        )
    else:
        lower = max(context.tolerance or _EPSILON, _EPSILON)
        upper = min(depth, capacity)
        desired = min(depth, capacity * cam_quality_factor(quality_profile))
        if upper <= lower or desired <= 0.0:
            values.append(
                _unsupported(
                    "stepdown",
                    dependency,
                    "Validated positive Pocket stepdown bounds are unavailable.",
                    inputs,
                )
            )
        else:
            resolved = min(max(desired, lower), upper)
            values.append(
                _derived(
                    "stepdown",
                    resolved,
                    dependency,
                    "Derived from Pocket depth span, axial cutting length, stickout and quality profile.",
                    inputs,
                    lower=lower,
                    upper=upper,
                    clamped=abs(resolved - desired) > _EPSILON,
                )
            )

    if not geometry_valid:
        values.append(
            _unsupported(
                "stepover",
                dependency,
                "Production Pocket offset geometry did not prove a reachable cutter-centre region.",
                inputs,
            )
        )
    elif not tool_valid or context.diameter is None:
        values.append(
            _unsupported(
                "stepover",
                dependency,
                "Pocket AUTO stepover requires a generator-supported End Mill diameter.",
                inputs,
            )
        )
    else:
        try:
            stepover, lower, upper, clamped = pocket_geometric_stepover_target(
                context.diameter,
                max(context.tolerance or _EPSILON, _EPSILON),
                quality_profile,
            )
        except ValueError:
            values.append(
                _unsupported(
                    "stepover",
                    dependency,
                    "Validated positive Pocket stepover bounds are unavailable.",
                    inputs,
                )
            )
        else:
            values.append(
                _derived(
                    "stepover",
                    stepover,
                    dependency,
                    "Geometric Pocket coverage derived from cutter diameter and quality profile; no material-load claim.",
                    inputs,
                    lower=lower,
                    upper=upper,
                    clamped=clamped,
                )
            )

    placement = _entry_placement(context) if tool_valid and geometry_valid else None
    if placement is None:
        reason = "No deterministic cutter-centre-accessible Pocket entry location was proven."
        values.extend(
            _unsupported(key, dependency, reason, inputs)
            for key in (
                "entry_loop_index",
                "entry_segment_index",
                "entry_point_x",
                "entry_point_y",
                "entry_clearance",
            )
        )
    else:
        reason = "Ranked deterministic Pocket entry by local boundary clearance and stable geometry tie-break."
        values.extend(
            (
                _derived(
                    "entry_loop_index",
                    placement.loop_index,
                    dependency,
                    reason,
                    inputs,
                    lower=0,
                    upper=len(context.offset_loops) - 1,
                ),
                _derived(
                    "entry_segment_index",
                    placement.segment_index,
                    dependency,
                    reason,
                    inputs,
                    lower=0,
                    upper=len(context.offset_loops[placement.loop_index].segments) - 1,
                ),
                _derived("entry_point_x", placement.point_x, dependency, reason, inputs),
                _derived("entry_point_y", placement.point_y, dependency, reason, inputs),
                _derived(
                    "entry_clearance",
                    placement.local_clearance,
                    dependency,
                    "Validated cutter-centre clearance to the closed Pocket outer boundary.",
                    inputs,
                    lower=context.diameter / 2.0 if context.diameter is not None else None,
                ),
            )
        )

    values.append(
        _unsupported(
            "entry_form",
            dependency,
            "Vertical plunge is the only generator form and Tool metadata does not prove center-cutting capability.",
            inputs,
        )
    )
    values.append(
        _unsupported(
            "linking_mode",
            dependency,
            "Existing retract linking is preserved because no complete stay-down path validator exists.",
            inputs,
        )
    )
    return AutomaticParameterContract(
        POCKET_AUTOMATIC_POLICY_KEY,
        POCKET_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


__all__ = [
    "POCKET_AUTOMATIC_KEYS",
    "POCKET_AUTOMATIC_POLICY_KEY",
    "POCKET_AUTOMATIC_POLICY_VERSION",
    "POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES",
    "POCKET_AUTOMATIC_USER_KEYS",
    "PocketAutomaticContext",
    "PocketAutomaticEntryPlacement",
    "pocket_automatic_entry_loops",
    "pocket_geometric_stepover_target",
    "resolve_pocket_automatic_contract",
]
