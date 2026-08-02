"""Coordinate boundary for the Lathe 2D XZ simulation domain.

Canonical HMS Lathe toolpaths expose X as a diameter in millimetres.  The
simulation domain stores the non-negative radial distance from the spindle
centreline and converts exactly once at this boundary.
"""

from __future__ import annotations

import math

SIMULATION_TOLERANCE_MM = 1.0e-9


def finite_mm(value: object, subject: str) -> float:
    """Return a finite millimetre value while rejecting bool and signed zero."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{subject} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{subject} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def diameter_x_to_radius_mm(x_diameter_mm: object) -> float:
    """Convert canonical non-negative diameter-X millimetres to radius."""

    diameter = finite_mm(x_diameter_mm, "Lathe X diameter")
    if diameter < 0.0:
        raise ValueError("Lathe X diameter must be non-negative")
    return diameter * 0.5


def radius_to_diameter_x_mm(radius_mm: object) -> float:
    """Convert a non-negative simulation radius to canonical diameter-X."""

    radius = finite_mm(radius_mm, "Lathe simulation radius")
    if radius < 0.0:
        raise ValueError("Lathe simulation radius must be non-negative")
    return radius * 2.0


__all__ = [
    "SIMULATION_TOLERANCE_MM",
    "diameter_x_to_radius_mm",
    "finite_mm",
    "radius_to_diameter_x_mm",
]
