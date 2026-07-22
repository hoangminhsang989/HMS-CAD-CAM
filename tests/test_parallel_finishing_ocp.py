"""Small deterministic OCP geometry fixtures for Parallel Finishing 8A.2.1."""

from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Copy,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
)
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt
import pytest

from hms_cadcam.cam.cam3d.parallel import ParallelFinishingGenerator
from tests.unit._parallel_finishing_fixtures import parallel_fixture


def _face(points):
    polygon = BRepBuilderAPI_MakePolygon()
    for x, y, z in points:
        polygon.Add(gp_Pnt(x, y, z))
    polygon.Close()
    return BRepBuilderAPI_MakeFace(polygon.Wire()).Face()


def _faces(shape):
    values = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_FACE, values)
    return tuple(
        TopoDS.Face_s(values.FindKey(index))
        for index in range(1, values.Extent() + 1)
    )


def _definition(name, face, tolerance=0.05):
    copied = TopoDS.Face_s(BRepBuilderAPI_Copy(face).Shape())
    mesher = BRepMesh_IncrementalMesh(copied, tolerance, False, 0.2, True)
    assert mesher.IsDone()
    location = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(copied, location)
    assert triangulation is not None
    transform = location.Transformation()
    vertices = tuple(
        triangulation.Node(index).Transformed(transform).Coord()
        for index in range(1, triangulation.NbNodes() + 1)
    )
    reverse = copied.Orientation() is TopAbs_Orientation.TopAbs_REVERSED
    triangles = []
    for index in range(1, triangulation.NbTriangles() + 1):
        first, second, third = triangulation.Triangle(index).Get()
        triangle = (first - 1, second - 1, third - 1)
        if reverse:
            triangle = (triangle[0], triangle[2], triangle[1])
        triangles.append(triangle)
    return name, vertices, tuple(triangles)


def _candidate(definitions, *, stepover=5.0):
    fixture = parallel_fixture(
        tuple(definitions),
        stepover=stepover,
        maximum_segment_length=2.0,
    )
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return generator.generate(computing)


@pytest.mark.ocp
def test_ocp_planar_and_inclined_faces_generate_parallel_paths() -> None:
    planar = _definition(
        "ocp-planar",
        _face(((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0))),
    )
    inclined = _definition(
        "ocp-inclined",
        _face(((0, 0, 0), (10, 0, 5), (10, 10, 5), (0, 10, 0))),
    )
    planar_result = _candidate((planar,))
    inclined_result = _candidate((inclined,))
    assert planar_result.preview.statistics.non_empty_pass_count == 3
    heights = [
        point.contact_point.z
        for item in inclined_result.preview.passes
        for segment in item.segments
        for point in segment.points
    ]
    assert max(heights) > min(heights)


@pytest.mark.ocp
def test_ocp_curved_cylinder_face_intersects_and_discretizes() -> None:
    shape = BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape()
    side = next(
        face
        for face in _faces(shape)
        if (lambda values: max(values) - min(values) > 9.0)(
            [point[2] for point in _definition("probe", face)[1]]
        )
    )
    result = _candidate((_definition("ocp-cylinder", side),), stepover=2.5)
    normals = {
        (round(point.surface_normal.x, 2), round(point.surface_normal.y, 2))
        for item in result.preview.passes
        for segment in item.segments
        for point in segment.points
    }
    assert len(normals) > 2
    assert result.preview.raw_intersection_segment_count > 0


@pytest.mark.ocp
def test_ocp_contiguous_and_disconnected_regions_keep_expected_segmentation() -> None:
    left = _definition(
        "ocp-left",
        _face(((0, 0, 0), (5, 0, 0), (5, 10, 0), (0, 10, 0))),
    )
    right = _definition(
        "ocp-right",
        _face(((5, 0, 0), (10, 0, 0), (10, 10, 0), (5, 10, 0))),
    )
    contiguous = _candidate((left, right))
    assert len(contiguous.preview.passes[1].segments) == 1

    separated_left = _definition(
        "ocp-separated-left",
        _face(((0, 0, 0), (4, 0, 0), (4, 10, 0), (0, 10, 0))),
    )
    separated_right = _definition(
        "ocp-separated-right",
        _face(((6, 0, 0), (10, 0, 0), (10, 10, 0), (6, 10, 0))),
    )
    disconnected = _candidate((separated_left, separated_right))
    assert len(disconnected.preview.passes[1].segments) == 2
