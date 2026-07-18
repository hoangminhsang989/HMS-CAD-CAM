"""Headless checks for the OCP technical spike."""

from __future__ import annotations

import math

import OCP

from geometry import create_demo_box, shape_bounds


def test_ocp_import_and_box_geometry() -> None:
    """OCP must import and create a bounded, non-null OCCT shape."""
    assert OCP.__file__
    shape = create_demo_box()
    assert not shape.IsNull()

    bounds = shape_bounds(shape)
    assert all(math.isfinite(value) for value in bounds)
    assert bounds[0] < bounds[3]
    assert bounds[1] < bounds[4]
    assert bounds[2] < bounds[5]
