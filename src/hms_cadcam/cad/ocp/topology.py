"""Convert native OCP topology into public immutable metadata."""

from __future__ import annotations

from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
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


def _count_shapes(shape: TopoDS_Shape, shape_type: TopAbs_ShapeEnum) -> int:
    shapes = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, shape_type, shapes)
    return shapes.Extent()
