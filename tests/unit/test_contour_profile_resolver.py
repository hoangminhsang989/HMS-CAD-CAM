"""Persistent FACE/WIRE profile resolver tests for 2D Contour 7B.4."""

from dataclasses import replace
from uuid import uuid4

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.Geom import Geom_BezierCurve
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.TColgp import TColgp_Array1OfPnt

from hms_cadcam.cad.models import CadFormat, CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpContourProfileResolver
from hms_cadcam.cam.adapters.ocp_contour import ContourProfileResolutionError
from hms_cadcam.cam.domain import (
    ContourCurveKind, DiagnosticCode, GeometryResolutionStatus, LengthUnit,
    PersistentProfileSelectorV1, Revision,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from spikes.xcaf_step.fixture import write_xcaf_step_fixture


def _context(kernel: OcpCadKernel, document_id, mode: SelectionMode, index: int = 1):
    source_id = uuid4()
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP,
                                          kernel.get_document_tree(document_id))
    selection = SelectionMetadata(document_id, f"{document_id}:{mode.value}:{index}", mode,
                                  kernel.get_bounding_box(document_id), next(iter(mapping.by_runtime)))
    return OcpContourProfileResolver(kernel, document_id, source_id, mapping, LengthUnit.MM), selection


def test_planar_face_outer_profile_is_persistent_and_ocp_free() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(20, 10, 5)
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id), TopAbs_ShapeEnum.TopAbs_FACE, faces)
    planar_index = 6
    resolver, selection = _context(kernel, document_id, SelectionMode.FACE, planar_index)
    reference = resolver.bind_selection(selection)
    selector = PersistentProfileSelectorV1.parse(reference.subshape_selector or "")
    assert selector.source_kind == "face"
    assert selection.selection_id not in reference.to_dict().values()
    result = resolver.resolve(reference)
    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.profile is not None
    assert result.profile.__class__.__module__.startswith("hms_cadcam.cam.domain")
    assert {segment.kind for segment in result.profile.outer_loop.segments} == {ContourCurveKind.LINE}
    assert result.profile.inner_loops == ()


def test_explicit_closed_wire_resolves_and_open_wire_is_rejected() -> None:
    closed = BRepBuilderAPI_MakePolygon()
    for point in ((0, 0, 0), (12, 0, 0), (12, 8, 0), (0, 8, 0)):
        closed.Add(gp_Pnt(*point))
    closed.Close()
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(closed.Wire(), CadFormat.GENERATED)
    resolver, selection = _context(kernel, metadata.document_id, SelectionMode.WIRE)
    reference = resolver.bind_selection(selection)
    result = resolver.resolve(reference)
    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.profile is not None and result.profile.reference == reference

    opened = BRepBuilderAPI_MakePolygon()
    for point in ((0, 0, 0), (10, 0, 0), (10, 5, 0)):
        opened.Add(gp_Pnt(*point))
    open_kernel = OcpCadKernel()
    open_metadata = open_kernel._documents.add_brep(opened.Wire(), CadFormat.GENERATED)
    open_resolver, open_selection = _context(open_kernel, open_metadata.document_id, SelectionMode.WIRE)
    with pytest.raises(ContourProfileResolutionError) as captured:
        open_resolver.bind_selection(open_selection)
    assert captured.value.code is DiagnosticCode.CONTOUR_OPEN_PROFILE


def test_profile_resolution_fails_closed_for_source_revision_and_transform_fingerprint() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 8, 5)
    resolver, selection = _context(kernel, document_id, SelectionMode.FACE, 6)
    reference = resolver.bind_selection(selection)
    foreign = replace(reference, source_id=uuid4())
    assert resolver.resolve(foreign).diagnostic_code is DiagnosticCode.CONTOUR_SOURCE_MISMATCH
    stale_resolver = OcpContourProfileResolver(kernel, document_id, reference.source_id,
        resolver._persistent_map, LengthUnit.MM, Revision(1))
    assert stale_resolver.resolve(reference).diagnostic_code is DiagnosticCode.CONTOUR_PROFILE_STALE


def test_circular_arc_profile_and_non_planar_face_policy() -> None:
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(BRepPrimAPI_MakeCylinder(5, 3).Shape(), CadFormat.GENERATED)
    document_id = metadata.document_id
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id), TopAbs_ShapeEnum.TopAbs_FACE, faces)
    planar = curved = None
    for index in range(1, faces.Extent() + 1):
        surface = BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(index)), True)
        if surface.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
            planar = index
        else:
            curved = index
    resolver, selection = _context(kernel, document_id, SelectionMode.FACE, planar)
    result = resolver.resolve(resolver.bind_selection(selection))
    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.profile is not None
    assert {segment.kind for segment in result.profile.outer_loop.segments} == {ContourCurveKind.ARC}
    with pytest.raises(ContourProfileResolutionError) as captured:
        resolver.bind_selection(replace(selection, selection_id=f"{document_id}:face:{curved}"))
    assert captured.value.code is DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE


def test_repeated_xcaf_occurrences_keep_distinct_profile_transform_provenance(tmp_path) -> None:
    source = tmp_path / "repeated.step"
    write_xcaf_step_fixture(source)
    kernel = OcpCadKernel(); imported = kernel.import_step(source)
    document_id = imported.document_id
    assert document_id is not None
    tree = kernel.get_document_tree(document_id)
    source_id = uuid4()
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    resolver = OcpContourProfileResolver(kernel, document_id, source_id, mapping, LengthUnit.MM)
    global_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id), TopAbs_ShapeEnum.TopAbs_FACE, global_faces)
    presentations = kernel._resolve_presentation_shapes(document_id)
    repeated = [node for node in tree.presentation_nodes if node.product_name == "Repeated Product"]
    assert len(repeated) == 2
    references = []
    for node in repeated:
        local_faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(presentations[node.object_id], TopAbs_ShapeEnum.TopAbs_FACE, local_faces)
        candidates = []
        for index in range(1, global_faces.Extent() + 1):
            face = TopoDS.Face_s(global_faces.FindKey(index))
            if local_faces.FindIndex(face) == 0:
                continue
            surface = BRepAdaptor_Surface(face, True)
            if surface.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
                candidates.append((surface.Plane().Location().Z(), index))
        _z, face_index = max(candidates)
        selection = SelectionMetadata(document_id, f"{document_id}:face:{face_index}",
                                      SelectionMode.FACE, node.bounding_box, node.object_id)
        reference = resolver.bind_selection(selection)
        result = resolver.resolve(reference)
        assert result.status is GeometryResolutionStatus.RESOLVED and result.profile is not None
        assert result.profile.provenance.occurrence_transform.occurrence_path == reference.occurrence_path
        references.append(reference)
    assert references[0].occurrence_path != references[1].occurrence_path
    assert references[0].expected_geometry_fingerprint != references[1].expected_geometry_fingerprint


def test_inner_loop_and_spline_profiles_are_rejected_explicitly() -> None:
    def polygon(points):
        builder = BRepBuilderAPI_MakePolygon()
        for point in points:
            builder.Add(gp_Pnt(*point))
        builder.Close()
        return builder.Wire()

    face_builder = BRepBuilderAPI_MakeFace(polygon(((0, 0, 0), (10, 0, 0),
                                                     (10, 10, 0), (0, 10, 0))))
    face_builder.Add(polygon(((2, 2, 0), (2, 4, 0), (4, 4, 0), (4, 2, 0))))
    kernel = OcpCadKernel(); metadata = kernel._documents.add_brep(face_builder.Face(), CadFormat.GENERATED)
    resolver, selection = _context(kernel, metadata.document_id, SelectionMode.FACE)
    with pytest.raises(ContourProfileResolutionError) as captured:
        resolver.bind_selection(selection)
    assert captured.value.code is DiagnosticCode.CONTOUR_UNSUPPORTED_INNER_LOOPS

    poles = TColgp_Array1OfPnt(1, 3)
    poles.SetValue(1, gp_Pnt(0, 0, 0)); poles.SetValue(2, gp_Pnt(5, 3, 0)); poles.SetValue(3, gp_Pnt(10, 0, 0))
    spline_edge = BRepBuilderAPI_MakeEdge(Geom_BezierCurve(poles)).Edge()
    wire_builder = BRepBuilderAPI_MakeWire()
    wire_builder.Add(spline_edge)
    wire_builder.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(10, 0, 0), gp_Pnt(0, -5, 0)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(0, -5, 0), gp_Pnt(0, 0, 0)).Edge())
    spline_kernel = OcpCadKernel()
    spline_metadata = spline_kernel._documents.add_brep(wire_builder.Wire(), CadFormat.GENERATED)
    spline_resolver, spline_selection = _context(spline_kernel, spline_metadata.document_id, SelectionMode.WIRE)
    with pytest.raises(ContourProfileResolutionError) as spline_error:
        spline_resolver.bind_selection(spline_selection)
    assert spline_error.value.code is DiagnosticCode.CONTOUR_UNSUPPORTED_CURVE
