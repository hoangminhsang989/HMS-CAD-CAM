"""Shared analytic material-engagement proofs for top-down Rest machining.

The functions in this module operate only on a verified ``MaterialState`` and
its exact ``CutterEnvelope`` contact law.  They do not mint machining
authority, choose regions, emit motion, or apply a geometric tolerance.
"""

from __future__ import annotations

import math

from hms_cadcam.cam.domain import Point3
from hms_cadcam.cam.material_state import CutterEnvelope, MaterialState


def cutter_engages_material_at(
    state: MaterialState,
    envelope: CutterEnvelope,
    x: float,
    y: float,
    tip_z: float,
) -> bool:
    """Return whether the cutter's strict open footprint engages material."""
    for row in range(state.height):
        center_y = (row + 0.5) * state.cell_size_y
        for column in range(state.width):
            radius = math.hypot(
                (column + 0.5) * state.cell_size_x - x,
                center_y - y,
            )
            # The forbidden cutter footprint is an open disk. Contact at its
            # boundary is not an intersection, but even a 1e-10 penetration
            # must remain forbidden; never shrink this disk by a tolerance.
            if radius >= envelope.radius:
                continue
            if (
                state.top_heights[row * state.width + column]
                > tip_z
                + envelope.surface_offset(radius)
                + state.precision.residual_threshold
            ):
                return True
    return False


def horizontal_segment_is_clear(
    state: MaterialState,
    envelope: CutterEnvelope,
    start: Point3,
    end: Point3,
    tip_z: float,
) -> bool:
    """Prove a horizontal segment clear by exact open-disk cell intervals."""
    # The endpoint predicate has a stricter authority than the later
    # representation-only interval normalization. A line whose exact endpoint
    # is inside a meaningful open cutter footprint is material-engaging even if
    # the independently rounded quadratic interval has collapsed at t=0 or
    # t=1. Check it before any ULP contraction; exact tangency remains legal.
    if (
        cutter_engages_material_at(state, envelope, start.x, start.y, tip_z)
        or cutter_engages_material_at(state, envelope, end.x, end.y, tip_z)
    ):
        return False
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    # A point proof is valid only for an exactly coincident move. Positive
    # sub-micrometre links must retain their analytic segment authority: their
    # squared length can underflow a tolerance even while crossing material.
    if length == 0.0:
        return not cutter_engages_material_at(
            state,
            envelope,
            start.x,
            start.y,
            tip_z,
        )
    direction_x, direction_y = dx / length, dy / length
    for row in range(state.height):
        center_y = (row + 0.5) * state.cell_size_y
        for column in range(state.width):
            maximum = envelope.maximum_removable_radius(
                target_tip_z=tip_z,
                current_height=state.top_heights[row * state.width + column],
                threshold=state.precision.residual_threshold,
            )
            if maximum is None:
                continue
            center_x = (column + 0.5) * state.cell_size_x
            offset_x, offset_y = center_x - start.x, center_y - start.y
            projection = offset_x * direction_x + offset_y * direction_y
            perpendicular = offset_x * direction_y - offset_y * direction_x
            # Open-disk interval proof. Do not subtract a tolerance from the
            # radius or interval: a positive near-tangent penetration is still
            # a material-engaging link and must fail closed.
            # Keep this ratio-safe: squaring a legal subnormal cutter radius
            # turns both terms into zero and incorrectly proves a positive
            # segment clear. ``hypot`` above retained the chord scale.
            if abs(perpendicular) >= maximum:
                continue
            ratio = perpendicular / maximum
            half_distance = maximum * math.sqrt(
                (1.0 - ratio) * (1.0 + ratio)
            )
            lower = max(0.0, (projection - half_distance) / length)
            upper = min(1.0, (projection + half_distance) / length)
            # ``lower``/``upper`` arise from separate floating-point paths.
            # Normalize at a bounded 8-ULP representation envelope so an exact
            # planner tangency cannot become a fake ~1e-15 interval. This is
            # intentionally not a geometric tolerance and never subtracts from
            # the cutter radius: a positive 1e-10 penetration remains forbidden
            # by the same open-disk predicate.
            for _ in range(8):
                lower = math.nextafter(lower, math.inf)
                upper = math.nextafter(upper, -math.inf)
            if lower < upper:
                return False
    return True


__all__ = [
    "cutter_engages_material_at",
    "horizontal_segment_is_clear",
]
