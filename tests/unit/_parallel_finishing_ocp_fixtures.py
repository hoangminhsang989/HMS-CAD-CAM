"""Deterministic original-BRep fixtures for Parallel contact-normal tests."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Copy,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
)
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IMeshTools import IMeshTools_Parameters
from OCP.TopAbs import TopAbs_Orientation
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Face
from OCP.gp import gp_Ax3, gp_Cylinder, gp_Dir, gp_Pnt

from hms_cadcam.cam.adapters import OcpParallelContactResolver
from hms_cadcam.cam.cam3d import Cam3DTolerancePolicy, CamSurfaceOrientation
from hms_cadcam.cam.cam3d.parallel import ParallelCutDirection
from tests.unit._cam3d_fixtures import tolerance
from tests.unit._parallel_finishing_fixtures import (
    ParallelFixture,
    parallel_fixture,
)


@dataclass(frozen=True, slots=True)
class ParallelOcpFixture:
    fixture: ParallelFixture
    face: TopoDS_Face
    resolver: OcpParallelContactResolver


def curved_brep_tolerance_fixture(
    *,
    stepover: float = 1.0,
    cut_direction: ParallelCutDirection = ParallelCutDirection.ONE_WAY,
) -> ParallelOcpFixture:
    """Create a trimmed upper half-cylinder meshed at CAM tolerance 0.01 mm."""
    policy = tolerance(0.01)
    axis = gp_Ax3(
        gp_Pnt(0.0, 0.0, 0.0),
        gp_Dir(1.0, 0.0, 0.0),
        gp_Dir(0.0, 1.0, 0.0),
    )
    cylinder = gp_Cylinder(axis, 5.0)
    u_min = math.acos(3.0 / 5.0)
    face = BRepBuilderAPI_MakeFace(
        cylinder,
        u_min,
        math.pi - u_min,
        0.0,
        10.0,
    ).Face()
    definition = _definition("curved-brep-tolerance", face, policy)
    fixture = parallel_fixture(
        (definition,),
        stepover=stepover,
        maximum_segment_length=1.0,
        cut_direction=cut_direction,
    )
    surface = fixture.zone.part_surfaces.selection.surfaces[0]
    resolver = OcpParallelContactResolver(((surface, face),))
    return ParallelOcpFixture(fixture, face, resolver)


def concave_brep_tolerance_fixture(
    *,
    stepover: float = 1.0,
) -> ParallelOcpFixture:
    """Create a reversed lower cylindrical channel smaller than the 5 mm ball."""
    policy = tolerance(0.01)
    axis = gp_Ax3(
        gp_Pnt(0.0, 0.0, 0.0),
        gp_Dir(1.0, 0.0, 0.0),
        gp_Dir(0.0, 1.0, 0.0),
    )
    cylinder = gp_Cylinder(axis, 4.0)
    u_min = math.acos(2.5 / 4.0)
    outward = BRepBuilderAPI_MakeFace(
        cylinder,
        math.pi + u_min,
        2.0 * math.pi - u_min,
        0.0,
        10.0,
    ).Face()
    face = TopoDS.Face_s(outward.Reversed())
    definition = _definition("concave-brep-too-small", face, policy)
    fixture = parallel_fixture(
        (definition,),
        stepover=stepover,
        maximum_segment_length=0.5,
    )
    surface = replace(
        fixture.zone.part_surfaces.selection.surfaces[0],
        orientation=CamSurfaceOrientation.REVERSED,
    )
    resolver = OcpParallelContactResolver(((surface, face),))
    return ParallelOcpFixture(fixture, face, resolver)


def inclined_brep_tolerance_fixture() -> ParallelOcpFixture:
    """Create one exact inclined planar face with a source-normal resolver."""
    points = (
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 5.0),
        (10.0, 10.0, 5.0),
        (0.0, 10.0, 0.0),
    )
    polygon = BRepBuilderAPI_MakePolygon()
    for point in points:
        polygon.Add(gp_Pnt(*point))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    definition = _definition("inclined-brep-tolerance", face, tolerance(0.01))
    fixture = parallel_fixture(
        (definition,),
        stepover=2.0,
        maximum_segment_length=1.0,
    )
    surface = fixture.zone.part_surfaces.selection.surfaces[0]
    resolver = OcpParallelContactResolver(((surface, face),))
    return ParallelOcpFixture(fixture, face, resolver)


def _definition(
    name: str,
    face: TopoDS_Face,
    policy: Cam3DTolerancePolicy,
) -> tuple[
    str,
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    copied = TopoDS.Face_s(BRepBuilderAPI_Copy(face).Shape())
    parameters = IMeshTools_Parameters()
    parameters.Deflection = policy.chordal_tolerance
    parameters.DeflectionInterior = policy.chordal_tolerance
    parameters.Angle = policy.angular_tolerance
    parameters.AngleInterior = policy.angular_tolerance
    parameters.Relative = False
    parameters.InParallel = False
    parameters.ControlSurfaceDeflection = True
    parameters.ForceFaceDeflection = True
    if policy.minimum_triangle_size is not None:
        parameters.MinSize = policy.minimum_triangle_size
        parameters.AdjustMinSize = False
    mesher = BRepMesh_IncrementalMesh(copied, parameters)
    if not mesher.IsDone():
        raise RuntimeError("OCP curved BRep fixture tessellation did not complete")
    location = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(copied, location)
    if triangulation is None:
        raise RuntimeError("OCP curved BRep fixture has no triangulation")
    transform = location.Transformation()
    vertices = tuple(
        triangulation.Node(index).Transformed(transform).Coord()
        for index in range(1, triangulation.NbNodes() + 1)
    )
    reverse = copied.Orientation() is TopAbs_Orientation.TopAbs_REVERSED
    triangles: list[tuple[int, int, int]] = []
    for index in range(1, triangulation.NbTriangles() + 1):
        first, second, third = triangulation.Triangle(index).Get()
        triangle = (first - 1, second - 1, third - 1)
        if reverse:
            triangle = (triangle[0], triangle[2], triangle[1])
        triangles.append(triangle)
    return name, vertices, tuple(triangles)
