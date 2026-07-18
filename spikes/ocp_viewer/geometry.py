"""Headless OCCT geometry used by the OCP technical spike."""

from __future__ import annotations

from OCP.BRepBndLib import BRepBndLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS_Shape

from model import SelectionMetadata


def create_demo_box() -> TopoDS_Shape:
    """Create the box displayed by the viewer spike."""
    return BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()


def shape_bounds(shape: TopoDS_Shape) -> tuple[float, float, float, float, float, float]:
    """Return the axis-aligned bounds of an OCCT shape."""
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    return bounds.Get()


def topology_counts(shape: TopoDS_Shape) -> dict[str, int]:
    """Count solids, faces and edges in an OCCT shape."""
    result: dict[str, int] = {}
    for name, topology in (
        ("solid", TopAbs_ShapeEnum.TopAbs_SOLID),
        ("face", TopAbs_ShapeEnum.TopAbs_FACE),
        ("edge", TopAbs_ShapeEnum.TopAbs_EDGE),
    ):
        shapes = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, topology, shapes)
        result[name] = shapes.Extent()
    return result


def selection_metadata(shape: TopoDS_Shape) -> SelectionMetadata:
    """Convert an internal OCCT shape to immutable, OCP-free metadata."""
    topology = shape.ShapeType().name.removeprefix("TopAbs_").lower()
    return SelectionMetadata(
        shape_id=f"{topology}:{abs(hash(shape))}",
        topology=topology,
        bounds=shape_bounds(shape),
    )
