"""Convert native OCP topology into public immutable metadata."""

from __future__ import annotations

from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.Poly import Poly_Triangulation
from OCP.TopoDS import TopoDS_Shape

from hms_cadcam.cad.models import BoundingBox, TopologyCounts


def get_bounding_box(shape: TopoDS_Shape) -> BoundingBox:
    """Return public axis-aligned bounds for one non-null native shape."""
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    return BoundingBox(*bounds.Get())


def get_topology_counts(shape: TopoDS_Shape) -> TopologyCounts:
    """Count solids, faces and edges without exposing native maps."""
    return TopologyCounts(
        solids=_count_shapes(shape, TopAbs_ShapeEnum.TopAbs_SOLID),
        faces=_count_shapes(shape, TopAbs_ShapeEnum.TopAbs_FACE),
        edges=_count_shapes(shape, TopAbs_ShapeEnum.TopAbs_EDGE),
    )


def get_mesh_bounding_box(triangulation: Poly_Triangulation) -> BoundingBox:
    """Return bounds computed directly from triangle-mesh vertices."""
    if triangulation.NbNodes() <= 0:
        raise ValueError("Cannot bound an empty triangle mesh")
    first = triangulation.Node(1)
    x_min = x_max = first.X()
    y_min = y_max = first.Y()
    z_min = z_max = first.Z()
    for index in range(2, triangulation.NbNodes() + 1):
        point = triangulation.Node(index)
        x_min = min(x_min, point.X())
        y_min = min(y_min, point.Y())
        z_min = min(z_min, point.Z())
        x_max = max(x_max, point.X())
        y_max = max(y_max, point.Y())
        z_max = max(z_max, point.Z())
    return BoundingBox(x_min, y_min, z_min, x_max, y_max, z_max)


def _count_shapes(shape: TopoDS_Shape, shape_type: TopAbs_ShapeEnum) -> int:
    shapes = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, shape_type, shapes)
    return shapes.Extent()
