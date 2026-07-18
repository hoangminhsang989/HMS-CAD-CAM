"""Convert native OCP topology into public immutable metadata."""

from __future__ import annotations

from OCP.BRepBndLib import BRepBndLib
from OCP.BRep import BRep_Builder
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.Poly import Poly_Triangulation
from OCP.TopoDS import TopoDS_Compound, TopoDS_Iterator, TopoDS_Shape

from hms_cadcam.cad.models import (
    BoundingBox,
    CadDocumentId,
    CadDocumentTree,
    CadObjectId,
    CadObjectKind,
    CadObjectNode,
    TopologyCounts,
)

_MANAGED_KINDS = {
    TopAbs_ShapeEnum.TopAbs_COMPOUND: CadObjectKind.COMPOUND,
    TopAbs_ShapeEnum.TopAbs_COMPSOLID: CadObjectKind.COMPSOLID,
    TopAbs_ShapeEnum.TopAbs_SOLID: CadObjectKind.SOLID,
    TopAbs_ShapeEnum.TopAbs_SHELL: CadObjectKind.SHELL,
}


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


def build_brep_document_tree(
    document_id: CadDocumentId,
    shape: TopoDS_Shape,
) -> tuple[CadDocumentTree, dict[CadObjectId, TopoDS_Shape]]:
    """Build a shallow management tree without materializing face/edge nodes."""
    counter = 0
    presentation_shapes: dict[CadObjectId, TopoDS_Shape] = {}

    def next_id() -> CadObjectId:
        nonlocal counter
        counter += 1
        return CadObjectId(f"{document_id}:object:{counter}")

    def make_node(current: TopoDS_Shape) -> CadObjectNode:
        shape_type = current.ShapeType()
        kind = _MANAGED_KINDS.get(shape_type, CadObjectKind.SHAPE)
        object_id = next_id()
        direct_children = _direct_children(current)
        managed_children = [
            child for child in direct_children if child.ShapeType() in _MANAGED_KINDS
        ]
        residual_children = [
            child for child in direct_children if child.ShapeType() not in _MANAGED_KINDS
        ]
        if kind in {CadObjectKind.SOLID, CadObjectKind.SHELL, CadObjectKind.SHAPE}:
            managed_children = []
        if managed_children:
            children_list = [make_node(child) for child in managed_children]
            if residual_children:
                residual_shape = (
                    residual_children[0]
                    if len(residual_children) == 1
                    else _compound_of(residual_children)
                )
                residual_id = next_id()
                presentation_shapes[residual_id] = residual_shape
                children_list.append(
                    CadObjectNode(
                        document_id,
                        residual_id,
                        CadObjectKind.SHAPE,
                        _object_label(CadObjectKind.SHAPE, residual_id),
                        get_bounding_box(residual_shape),
                        (),
                        True,
                    )
                )
            children = tuple(children_list)
            return CadObjectNode(
                document_id,
                object_id,
                kind,
                _object_label(kind, object_id),
                get_bounding_box(current),
                children,
                False,
            )
        presentation_shapes[object_id] = current
        return CadObjectNode(
            document_id,
            object_id,
            kind,
            _object_label(kind, object_id),
            get_bounding_box(current),
            (),
            True,
        )

    shape_node = make_node(shape)
    root = CadObjectNode(
        document_id=document_id,
        object_id=CadObjectId(f"{document_id}:document"),
        kind=CadObjectKind.DOCUMENT,
        label="CAD document",
        bounding_box=get_bounding_box(shape),
        children=(shape_node,),
    )
    return CadDocumentTree(document_id, root), presentation_shapes


def build_mesh_document_tree(
    document_id: CadDocumentId,
    bounds: BoundingBox,
) -> CadDocumentTree:
    """Return exactly one mesh object below the document node."""
    mesh = CadObjectNode(
        document_id=document_id,
        object_id=CadObjectId(f"{document_id}:object:1"),
        kind=CadObjectKind.MESH,
        label="Triangle mesh",
        bounding_box=bounds,
        has_presentation=True,
    )
    root = CadObjectNode(
        document_id=document_id,
        object_id=CadObjectId(f"{document_id}:document"),
        kind=CadObjectKind.DOCUMENT,
        label="CAD document",
        bounding_box=bounds,
        children=(mesh,),
    )
    return CadDocumentTree(document_id, root)


def _direct_children(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    iterator = TopoDS_Iterator(shape)
    children: list[TopoDS_Shape] = []
    while iterator.More():
        children.append(iterator.Value())
        iterator.Next()
    return children


def _compound_of(shapes: list[TopoDS_Shape]) -> TopoDS_Compound:
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _object_label(kind: CadObjectKind, object_id: CadObjectId) -> str:
    index = object_id.value.rsplit(":", 1)[-1]
    labels = {
        CadObjectKind.COMPOUND: "Compound",
        CadObjectKind.COMPSOLID: "CompSolid",
        CadObjectKind.SOLID: "Solid",
        CadObjectKind.SHELL: "Shell",
        CadObjectKind.SHAPE: "Shape",
    }
    return f"{labels[kind]} {index}"


def _count_shapes(shape: TopoDS_Shape, shape_type: TopAbs_ShapeEnum) -> int:
    shapes = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, shape_type, shapes)
    return shapes.Extent()
