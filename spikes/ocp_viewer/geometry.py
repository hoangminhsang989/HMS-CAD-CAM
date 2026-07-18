"""Headless OCCT geometry used by the OCP technical spike."""

from __future__ import annotations

from OCP.BRepBndLib import BRepBndLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.Bnd import Bnd_Box
from OCP.TopoDS import TopoDS_Shape


def create_demo_box() -> TopoDS_Shape:
    """Create the box displayed by the viewer spike."""
    return BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()


def shape_bounds(shape: TopoDS_Shape) -> tuple[float, float, float, float, float, float]:
    """Return the axis-aligned bounds of an OCCT shape."""
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    return bounds.Get()
