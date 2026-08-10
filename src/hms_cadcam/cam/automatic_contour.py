"""Evidence-backed automatic setup policy for closed 2D Contour operations."""

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
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourSegment,
    ContourSide,
    DependencyFingerprint,
    LengthUnit,
    Point3,
    ToolFamily,
)


CONTOUR_AUTOMATIC_POLICY_KEY = "contour.operation_intelligence"
CONTOUR_AUTOMATIC_POLICY_VERSION = 1
CONTOUR_AUTOMATIC_USER_KEYS = (
    "stepdown",
    "lead_in_length",
    "lead_out_length",
)
CONTOUR_AUTOMATIC_KEYS = (
    "entry_segment_index",
    "lead_form",
    *CONTOUR_AUTOMATIC_USER_KEYS,
)
_EPSILON = 1.0e-9
_GEOMETRY_TOLERANCE = 1.0e-8
_MAX_CLEARANCE_SEARCH_STEPS = 40


class ContourAutomaticLeadForm(StrEnum):
    """Linear lead forms already representable by the Contour toolpath IR."""

    TANGENT_LINEAR = "tangent_linear"
    NORMAL_LINEAR = "normal_linear"


@dataclass(frozen=True, slots=True)
class ContourAutomaticContext:
    """Validated, kernel-free evidence consumed by the Contour AUTO policy."""

    unit: LengthUnit
    tool_family: ToolFamily | None
    diameter: float | None
    corner_radius: float | None
    axial_cutting_length: float | None
    assembly_stickout: float | None
    depth_span: float | None
    tolerance: float | None
    side: ContourSide
    multiple_depth_passes: bool
    machining_loop: ContourLoop | None
    source_loop: ContourLoop | None
    profile_fingerprint: str | None
    tool_fingerprint: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise ValueError("Contour automatic policy requires a known unit")
        if not isinstance(self.side, ContourSide):
            raise TypeError("Contour automatic machining side is invalid")
        if type(self.multiple_depth_passes) is not bool:
            raise TypeError("Contour automatic depth-pass intent is invalid")
        for name in (
            "diameter",
            "corner_radius",
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
                raise ValueError(f"Contour automatic {name} is invalid")
        if (self.machining_loop is None) != (self.source_loop is None):
            raise ValueError("Contour automatic loops must be present together")
        for loop in (self.machining_loop, self.source_loop):
            if loop is not None and any(
                segment.unit is not self.unit for segment in loop.segments
            ):
                raise ValueError("Contour automatic loop unit is inconsistent")
        for name in ("profile_fingerprint", "tool_fingerprint"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Contour automatic {name} is invalid")


@dataclass(frozen=True, slots=True)
class ContourAutomaticLeadPlacement:
    """One validated entry/exit placement selected by the policy."""

    entry_segment_index: int
    form: ContourAutomaticLeadForm
    lead_in_length: float
    lead_out_length: float
    lead_in_upper_bound: float
    lead_out_upper_bound: float
    lead_in_clamped: bool
    lead_out_clamped: bool


def _dependency(
    context: ContourAutomaticContext,
    quality_profile: CamQualityProfile,
) -> DependencyFingerprint:
    return DependencyFingerprint.from_payload(
        {
            "unit": context.unit.value,
            "tool_family": (
                context.tool_family.value if context.tool_family is not None else None
            ),
            "diameter": context.diameter,
            "corner_radius": context.corner_radius,
            "axial_cutting_length": context.axial_cutting_length,
            "assembly_stickout": context.assembly_stickout,
            "depth_span": context.depth_span,
            "tolerance": context.tolerance,
            "side": context.side.value,
            "multiple_depth_passes": context.multiple_depth_passes,
            "profile": context.profile_fingerprint,
            "tool": context.tool_fingerprint,
            "machining_loop": (
                None
                if context.machining_loop is None
                else context.machining_loop.to_dict()
            ),
            "quality_profile": quality_profile.value,
        }
    )


def _inputs(
    context: ContourAutomaticContext,
    quality_profile: CamQualityProfile,
) -> tuple[tuple[str, object], ...]:
    return (
        (
            "tool_family",
            context.tool_family.value if context.tool_family is not None else None,
        ),
        ("diameter", context.diameter),
        ("corner_radius", context.corner_radius),
        ("axial_cutting_length", context.axial_cutting_length),
        ("assembly_stickout", context.assembly_stickout),
        ("depth_span", context.depth_span),
        ("tolerance", context.tolerance),
        ("quality_profile", quality_profile.value),
        ("unit", context.unit.value),
        ("side", context.side.value),
        ("profile_fingerprint", context.profile_fingerprint),
        ("tool_fingerprint", context.tool_fingerprint),
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
        CONTOUR_AUTOMATIC_POLICY_KEY,
        CONTOUR_AUTOMATIC_POLICY_VERSION,
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
        CONTOUR_AUTOMATIC_POLICY_KEY,
        CONTOUR_AUTOMATIC_POLICY_VERSION,
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
            (segment.start.z + segment.end.z) / 2.0,
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


def _tangent_at_midpoint(segment: ContourSegment) -> tuple[float, float]:
    if segment.kind is ContourCurveKind.LINE:
        dx = segment.end.x - segment.start.x
        dy = segment.end.y - segment.start.y
    else:
        assert segment.center is not None and segment.sweep_radians is not None
        midpoint = _segment_midpoint(segment)
        radial_x = midpoint.x - segment.center.x
        radial_y = midpoint.y - segment.center.y
        if segment.sweep_radians > 0.0:
            dx, dy = -radial_y, radial_x
        else:
            dx, dy = radial_y, -radial_x
    length = math.hypot(dx, dy)
    if length <= _GEOMETRY_TOLERANCE:
        raise ValueError("Contour entry tangent is degenerate")
    return dx / length, dy / length


def reorder_contour_entry(loop: ContourLoop, segment_index: int) -> ContourLoop:
    """Split one stable segment midpoint and rotate it to the Contour start."""
    if not isinstance(loop, ContourLoop):
        raise TypeError("Contour entry loop is invalid")
    if type(segment_index) is not int or not 0 <= segment_index < len(loop.segments):
        raise ValueError("Contour entry segment index is invalid")
    segment = loop.segments[segment_index]
    midpoint = _segment_midpoint(segment)
    if segment.kind is ContourCurveKind.LINE:
        first = ContourSegment(segment.kind, segment.start, midpoint)
        second = ContourSegment(segment.kind, midpoint, segment.end)
    else:
        assert segment.center is not None and segment.sweep_radians is not None
        half = segment.sweep_radians / 2.0
        first = ContourSegment(
            segment.kind, segment.start, midpoint, segment.center, half
        )
        second = ContourSegment(
            segment.kind, midpoint, segment.end, segment.center, half
        )
    ordered = (
        second,
        *loop.segments[segment_index + 1 :],
        *loop.segments[:segment_index],
        first,
    )
    return ContourLoop(tuple(ordered), loop.orientation)


def _sample_loop(loop: ContourLoop) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for segment in loop.segments:
        if not points:
            points.append((segment.start.x, segment.start.y))
        if segment.kind is ContourCurveKind.LINE:
            points.append((segment.end.x, segment.end.y))
            continue
        assert segment.center is not None
        assert segment.radius is not None
        assert segment.sweep_radians is not None
        count = max(2, math.ceil(abs(segment.sweep_radians) / math.radians(5.0)))
        start_angle = math.atan2(
            segment.start.y - segment.center.y,
            segment.start.x - segment.center.x,
        )
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
    point: tuple[float, float], polygon: tuple[tuple[float, float], ...]
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


def _crosses_boundary(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    direction = (end[0] - start[0], end[1] - start[1])
    direction_squared = direction[0] ** 2 + direction[1] ** 2
    if direction_squared <= _EPSILON**2:
        return True
    for first, second in zip(polygon, polygon[1:]):
        edge = (second[0] - first[0], second[1] - first[1])
        offset = (first[0] - start[0], first[1] - start[1])
        denominator = direction[0] * edge[1] - direction[1] * edge[0]
        if abs(denominator) > _GEOMETRY_TOLERANCE:
            lead_parameter = (
                offset[0] * edge[1] - offset[1] * edge[0]
            ) / denominator
            edge_parameter = (
                offset[0] * direction[1] - offset[1] * direction[0]
            ) / denominator
            if (
                _GEOMETRY_TOLERANCE < lead_parameter <= 1.0 + _GEOMETRY_TOLERANCE
                and -_GEOMETRY_TOLERANCE
                <= edge_parameter
                <= 1.0 + _GEOMETRY_TOLERANCE
            ):
                return True
            continue
        collinear = abs(
            offset[0] * direction[1] - offset[1] * direction[0]
        ) <= _GEOMETRY_TOLERANCE
        if not collinear:
            continue
        first_parameter = (
            offset[0] * direction[0] + offset[1] * direction[1]
        ) / direction_squared
        second_offset = (second[0] - start[0], second[1] - start[1])
        second_parameter = (
            second_offset[0] * direction[0]
            + second_offset[1] * direction[1]
        ) / direction_squared
        overlap_start = max(
            _GEOMETRY_TOLERANCE, min(first_parameter, second_parameter)
        )
        overlap_end = min(1.0, max(first_parameter, second_parameter))
        if overlap_end > overlap_start:
            return True
    return False


def _path_feasible(
    start: tuple[float, float],
    direction: tuple[float, float],
    length: float,
    *,
    want_inside: bool,
    source_polygon: tuple[tuple[float, float], ...],
    machining_polygon: tuple[tuple[float, float], ...],
) -> bool:
    end = (
        start[0] + direction[0] * length,
        start[1] + direction[1] * length,
    )
    samples = tuple(
        (
            start[0] + direction[0] * length * ratio,
            start[1] + direction[1] * length * ratio,
        )
        for ratio in (0.2, 0.4, 0.6, 0.8, 1.0)
    )
    return (
        all(_point_in_polygon(point, source_polygon) is want_inside for point in samples)
        and not _crosses_boundary(start, end, source_polygon)
        and not _crosses_boundary(start, end, machining_polygon)
    )


def _validated_length(
    desired: float,
    lower: float,
    feasible,
) -> tuple[float, bool] | None:
    if feasible(desired):
        return desired, False
    if not feasible(lower):
        return None
    low, high = lower, desired
    for _ in range(_MAX_CLEARANCE_SEARCH_STEPS):
        midpoint = (low + high) / 2.0
        if feasible(midpoint):
            low = midpoint
        else:
            high = midpoint
    return low, True


def _lead_placement(
    context: ContourAutomaticContext,
    quality_profile: CamQualityProfile,
) -> ContourAutomaticLeadPlacement | None:
    loop = context.machining_loop
    source_loop = context.source_loop
    diameter = context.diameter
    if loop is None or source_loop is None or diameter is None or diameter <= 0.0:
        return None
    source_polygon = _sample_loop(source_loop)
    machining_polygon = _sample_loop(loop)
    want_inside = context.side is ContourSide.INSIDE
    lower = max(context.tolerance or _EPSILON, _EPSILON)
    desired_scale = diameter * cam_quality_factor(quality_profile)
    ranked = sorted(
        enumerate(loop.segments),
        key=lambda item: (
            0 if item[1].kind is ContourCurveKind.ARC else 1,
            -_segment_length(item[1]),
            _segment_midpoint(item[1]).x,
            _segment_midpoint(item[1]).y,
            item[0],
        ),
    )
    for segment_index, segment in ranked:
        segment_length = _segment_length(segment)
        upper = min(diameter, segment_length / 2.0)
        if segment.kind is ContourCurveKind.ARC:
            assert segment.radius is not None
            upper = min(upper, segment.radius)
        if not math.isfinite(upper) or upper <= lower:
            continue
        desired = min(desired_scale, upper)
        midpoint = _segment_midpoint(segment)
        start = (midpoint.x, midpoint.y)
        tangent = _tangent_at_midpoint(segment)
        forms: list[tuple[ContourAutomaticLeadForm, tuple[float, float], tuple[float, float]]] = []
        if segment.kind is ContourCurveKind.ARC:
            forms.append(
                (
                    ContourAutomaticLeadForm.TANGENT_LINEAR,
                    (-tangent[0], -tangent[1]),
                    tangent,
                )
            )
        normals = ((-tangent[1], tangent[0]), (tangent[1], -tangent[0]))
        forms.extend(
            (
                ContourAutomaticLeadForm.NORMAL_LINEAR,
                normal,
                normal,
            )
            for normal in normals
        )
        for form, lead_in_direction, lead_out_direction in forms:
            lead_in = _validated_length(
                desired,
                lower,
                lambda length, direction=lead_in_direction: _path_feasible(
                    start,
                    direction,
                    length,
                    want_inside=want_inside,
                    source_polygon=source_polygon,
                    machining_polygon=machining_polygon,
                ),
            )
            lead_out = _validated_length(
                desired,
                lower,
                lambda length, direction=lead_out_direction: _path_feasible(
                    start,
                    direction,
                    length,
                    want_inside=want_inside,
                    source_polygon=source_polygon,
                    machining_polygon=machining_polygon,
                ),
            )
            if lead_in is None or lead_out is None:
                continue
            return ContourAutomaticLeadPlacement(
                segment_index,
                form,
                lead_in[0],
                lead_out[0],
                upper,
                upper,
                lead_in[1] or desired_scale > upper + _EPSILON,
                lead_out[1] or desired_scale > upper + _EPSILON,
            )
    return None


def contour_automatic_lead_points(
    loop: ContourLoop,
    source_loop: ContourLoop,
    side: ContourSide,
    placement: ContourAutomaticLeadPlacement,
) -> tuple[ContourLoop, tuple[float, float], tuple[float, float]]:
    """Revalidate a persisted AUTO placement and return exact lead endpoints."""
    reordered = reorder_contour_entry(loop, placement.entry_segment_index)
    start_point = reordered.segments[0].start
    start = (start_point.x, start_point.y)
    tangent = _tangent_at_midpoint(loop.segments[placement.entry_segment_index])
    if placement.form is ContourAutomaticLeadForm.TANGENT_LINEAR:
        directions = (-tangent[0], -tangent[1]), tangent
    else:
        candidate_normals = ((-tangent[1], tangent[0]), (tangent[1], -tangent[0]))
        source_polygon = _sample_loop(source_loop)
        machining_polygon = _sample_loop(loop)
        want_inside = side is ContourSide.INSIDE
        directions = next(
            (
                (normal, normal)
                for normal in candidate_normals
                if _path_feasible(
                    start,
                    normal,
                    placement.lead_in_length,
                    want_inside=want_inside,
                    source_polygon=source_polygon,
                    machining_polygon=machining_polygon,
                )
                and _path_feasible(
                    start,
                    normal,
                    placement.lead_out_length,
                    want_inside=want_inside,
                    source_polygon=source_polygon,
                    machining_polygon=machining_polygon,
                )
            ),
            None,
        )
        if directions is None:
            raise ValueError("Persisted Contour normal lead is no longer feasible")
    source_polygon = _sample_loop(source_loop)
    machining_polygon = _sample_loop(loop)
    want_inside = side is ContourSide.INSIDE
    for direction, length in zip(
        directions,
        (placement.lead_in_length, placement.lead_out_length),
        strict=True,
    ):
        if not _path_feasible(
            start,
            direction,
            length,
            want_inside=want_inside,
            source_polygon=source_polygon,
            machining_polygon=machining_polygon,
        ):
            raise ValueError("Persisted Contour tangent lead is no longer feasible")
    lead_in = (
        start[0] + directions[0][0] * placement.lead_in_length,
        start[1] + directions[0][1] * placement.lead_in_length,
    )
    lead_out = (
        start[0] + directions[1][0] * placement.lead_out_length,
        start[1] + directions[1][1] * placement.lead_out_length,
    )
    return reordered, lead_in, lead_out


def resolve_contour_automatic_contract(
    context: ContourAutomaticContext,
    *,
    quality_profile: CamQualityProfile = CamQualityProfile.BALANCED,
) -> AutomaticParameterContract:
    """Resolve only evidence-backed Contour values; never invent process data."""
    if not isinstance(context, ContourAutomaticContext):
        raise TypeError("Contour automatic context is invalid")
    if not isinstance(quality_profile, CamQualityProfile):
        raise TypeError("Contour automatic quality profile is invalid")
    dependency = _dependency(context, quality_profile)
    inputs = _inputs(context, quality_profile)
    supported = {ToolFamily.END_MILL, ToolFamily.BULL_NOSE_END_MILL}
    tool_valid = (
        context.tool_family in supported
        and context.diameter is not None
        and context.diameter > 0.0
        and context.axial_cutting_length is not None
        and context.axial_cutting_length > 0.0
        and context.assembly_stickout is not None
        and context.assembly_stickout > 0.0
    )
    if (
        tool_valid
        and context.tool_family is ToolFamily.BULL_NOSE_END_MILL
        and (
            context.corner_radius is None
            or context.corner_radius <= 0.0
            or context.corner_radius > context.diameter / 2.0  # type: ignore[operator]
        )
    ):
        tool_valid = False
    profile_valid = (
        context.machining_loop is not None
        and context.source_loop is not None
        and context.profile_fingerprint is not None
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
    if not context.multiple_depth_passes:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Single-depth Contour does not use an automatic stepdown.",
                inputs,
            )
        )
    elif not profile_valid:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "A validated closed Contour profile is required before AUTO setup.",
                inputs,
            )
        )
    elif not tool_valid:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "A supported cutter with explicit axial geometry and stickout is required.",
                inputs,
            )
        )
    elif depth is None or depth <= 0.0 or capacity is None or capacity <= 0.0:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Positive depth-span and usable axial capacity evidence is required.",
                inputs,
            )
        )
    elif depth > capacity + _GEOMETRY_TOLERANCE:
        values.append(
            _unsupported(
                "stepdown",
                dependency,
                "Requested depth exceeds validated cutter or assembly axial capacity.",
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
                    "Validated positive stepdown bounds are unavailable.",
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
                    "Derived from depth span, explicit axial cutting length, assembly stickout and quality profile.",
                    inputs,
                    lower=lower,
                    upper=upper,
                    clamped=abs(resolved - desired) > _EPSILON,
                )
            )

    placement = (
        _lead_placement(context, quality_profile)
        if tool_valid and profile_valid
        else None
    )
    if placement is None:
        reason = (
            "A supported cutter and validated closed-profile lead clearance are required."
            if tool_valid
            else "A supported cutter with explicit diameter geometry is required."
        )
        values.extend(
            _unsupported(key, dependency, reason, inputs)
            for key in (
                "entry_segment_index",
                "lead_form",
                "lead_in_length",
                "lead_out_length",
            )
        )
    else:
        placement_reason = (
            "Ranked deterministic non-corner entry with validated tangent continuity."
            if placement.form is ContourAutomaticLeadForm.TANGENT_LINEAR
            else "Ranked deterministic non-corner entry with validated normal linear fallback."
        )
        values.extend(
            (
                _derived(
                    "entry_segment_index",
                    placement.entry_segment_index,
                    dependency,
                    placement_reason,
                    inputs,
                    lower=0,
                    upper=len(context.machining_loop.segments) - 1,  # type: ignore[union-attr]
                ),
                _derived(
                    "lead_form",
                    placement.form.value,
                    dependency,
                    placement_reason,
                    inputs,
                ),
                _derived(
                    "lead_in_length",
                    placement.lead_in_length,
                    dependency,
                    "Cutter-scaled lead-in bounded by local segment, curvature and profile clearance.",
                    inputs,
                    lower=max(context.tolerance or _EPSILON, _EPSILON),
                    upper=placement.lead_in_upper_bound,
                    clamped=placement.lead_in_clamped,
                ),
                _derived(
                    "lead_out_length",
                    placement.lead_out_length,
                    dependency,
                    "Cutter-scaled lead-out independently bounded by local exit geometry and profile clearance.",
                    inputs,
                    lower=max(context.tolerance or _EPSILON, _EPSILON),
                    upper=placement.lead_out_upper_bound,
                    clamped=placement.lead_out_clamped,
                ),
            )
        )
    return AutomaticParameterContract(
        CONTOUR_AUTOMATIC_POLICY_KEY,
        CONTOUR_AUTOMATIC_POLICY_VERSION,
        quality_profile,
        tuple(values),
    )


__all__ = [
    "CONTOUR_AUTOMATIC_KEYS",
    "CONTOUR_AUTOMATIC_POLICY_KEY",
    "CONTOUR_AUTOMATIC_POLICY_VERSION",
    "CONTOUR_AUTOMATIC_USER_KEYS",
    "ContourAutomaticContext",
    "ContourAutomaticLeadForm",
    "ContourAutomaticLeadPlacement",
    "contour_automatic_lead_points",
    "reorder_contour_entry",
    "resolve_contour_automatic_contract",
]
