"""Stage 7B.3 persistent planar FACE resolver and clipping tests."""

from dataclasses import replace
from uuid import uuid4

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

from hms_cadcam.cad.models import CadFormat, CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpPlanarFaceResolver
from hms_cadcam.cam.adapters.ocp_planar_face import (
    PlanarFaceResolutionError,
    _validate_simple_polygon,
)
from hms_cadcam.cam.application.facing import _raster_lanes
from hms_cadcam.cam.domain import (
    DiagnosticCode,
    FacingRegion,
    GeometryResolutionStatus,
    LengthUnit,
    PersistentFaceSelectorV1,
    Point3,
    Revision,
    Vector3,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from spikes.xcaf_step.fixture import write_xcaf_step_fixture


def _box_context():
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10.0, 8.0, 5.0)
    source_id = uuid4()
    mapping = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, kernel.get_document_tree(document_id)
    )
    selection = SelectionMetadata(
        document_id,
        f"{document_id}:face:6",
        SelectionMode.FACE,
        kernel.get_bounding_box(document_id),
        next(iter(mapping.by_runtime)),
    )
    resolver = OcpPlanarFaceResolver(
        kernel, document_id, source_id, mapping, LengthUnit.MM
    )
    return resolver, selection


def test_box_face_binds_to_persistent_selector_and_resolves_without_ocp() -> None:
    resolver, selection = _box_context()
    reference = resolver.bind_selection(selection)
    selector = PersistentFaceSelectorV1.parse(reference.subshape_selector or "")
    assert str(selection.document_id) not in str(selector)
    assert selection.selection_id not in reference.to_dict().values()

    result = resolver.resolve(reference)
    assert result.status is GeometryResolutionStatus.RESOLVED
    descriptor = result.planar_face
    assert descriptor is not None
    assert descriptor.__class__.__module__.startswith("hms_cadcam.cam.domain")
    assert descriptor.unit is LengthUnit.MM
    assert descriptor.inner_boundaries == ()
    assert descriptor.outer_boundary.points[0] == descriptor.outer_boundary.points[-1]
    assert descriptor.bounds.maximum.z == pytest.approx(5.0)


def test_face_resolution_fails_closed_for_source_and_revision_changes() -> None:
    resolver, selection = _box_context()
    reference = resolver.bind_selection(selection)

    mismatch = resolver.resolve(replace(reference, source_id=uuid4()))
    assert mismatch.status is GeometryResolutionStatus.SOURCE_MISMATCH
    assert mismatch.diagnostic_code is DiagnosticCode.FACING_FACE_SOURCE_MISMATCH

    stale = resolver.resolve(replace(reference, expected_source_revision=Revision(1)))
    assert stale.status is GeometryResolutionStatus.STALE
    assert stale.diagnostic_code is DiagnosticCode.FACING_FACE_REFERENCE_STALE


def test_resolver_rejects_unknown_unit_instead_of_assuming_mm() -> None:
    resolver, selection = _box_context()
    with pytest.raises(ValueError, match="explicitly declared"):
        OcpPlanarFaceResolver(
            resolver._kernel,
            selection.document_id,
            resolver._source_id,
            resolver._persistent_map,
            LengthUnit.UNKNOWN,
        )


def test_self_intersecting_boundary_and_malformed_selector_are_rejected() -> None:
    unit = LengthUnit.MM
    bow_tie = tuple(Point3(x, y, 0.0, unit) for x, y in (
        (0, 0), (4, 4), (0, 4), (4, 0), (0, 0),
    ))
    with pytest.raises(PlanarFaceResolutionError) as captured:
        _validate_simple_polygon(
            bow_tie, Point3(0, 0, 0, unit), Vector3(1, 0, 0), Vector3(0, 1, 0)
        )
    assert captured.value.code is DiagnosticCode.FACING_INVALID_FACE_BOUNDARY
    with pytest.raises(ValueError):
        PersistentFaceSelectorV1.parse("ocp:runtime:face:12")


def test_concave_scanline_produces_independent_clipped_segments() -> None:
    unit = LengthUnit.MM
    boundary = tuple(Point3(x, y, 0.0, unit) for x, y in (
        (0, 0), (10, 0), (10, 10), (8, 10),
        (8, 2), (2, 2), (2, 10), (0, 10),
    ))
    from hms_cadcam.cam.domain import GeometryFingerprint

    region = FacingRegion(
        boundary, Vector3(0, 0, 1), GeometryFingerprint.from_payload({"shape": "u"})
    )
    lanes = _raster_lanes(region, 0.0, 1.0, 0.0)
    middle = [lane for lane in lanes if lane[0][1] == pytest.approx(5.0)]
    assert middle == [((0.0, 5.0), (2.0, 5.0)), ((8.0, 5.0), (10.0, 5.0))]
    assert all(not (start[0] < 5.0 < end[0]) for start, end in middle)


def test_planar_raster_keeps_nonzero_horizontal_boundary_strip() -> None:
    unit = LengthUnit.MM
    boundary = tuple(Point3(x, y, 0.0, unit) for x, y in (
        (0, 0), (10, 0), (10, 10), (0, 10),
    ))
    from hms_cadcam.cam.domain import GeometryFingerprint

    region = FacingRegion(
        boundary, Vector3(0, 0, 1),
        GeometryFingerprint.from_payload({"shape": "rectangle"}),
    )
    lanes = _raster_lanes(region, 0.0, 5.0, 0.0)

    assert lanes == (
        ((0.0, 0.0), (10.0, 0.0)),
        ((0.0, 5.0), (10.0, 5.0)),
        ((0.0, 10.0), (10.0, 10.0)),
    )
    assert all(start != end for start, end in lanes)


def test_xcaf_repeated_occurrences_resolve_once_in_world_coordinates(tmp_path) -> None:
    source = tmp_path / "repeated.step"
    write_xcaf_step_fixture(source)
    kernel = OcpCadKernel()
    imported = kernel.import_step(source)
    assert imported.document_id is not None
    document_id = imported.document_id
    tree = kernel.get_document_tree(document_id)
    source_id = uuid4()
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    resolver = OcpPlanarFaceResolver(
        kernel, document_id, source_id, mapping, LengthUnit.MM
    )
    global_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id),
                       TopAbs_ShapeEnum.TopAbs_FACE, global_faces)
    presentations = kernel._resolve_presentation_shapes(document_id)
    repeated = [node for node in tree.presentation_nodes
                if node.product_name == "Repeated Product"]
    assert len(repeated) == 2
    references = []
    descriptors = []
    for node in repeated:
        container = presentations[node.object_id]
        local_faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(container, TopAbs_ShapeEnum.TopAbs_FACE, local_faces)
        candidates = []
        for index in range(1, global_faces.Extent() + 1):
            face = TopoDS.Face_s(global_faces.FindKey(index))
            if local_faces.FindIndex(face) == 0:
                continue
            surface = BRepAdaptor_Surface(face, True)
            if (surface.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane and
                    abs(surface.Plane().Axis().Direction().Z()) > 0.99):
                candidates.append((surface.Plane().Location().Z(), index))
        _z, face_index = max(candidates)
        selection = SelectionMetadata(
            document_id, f"{document_id}:face:{face_index}", SelectionMode.FACE,
            node.bounding_box, node.object_id,
        )
        reference = resolver.bind_selection(selection)
        result = resolver.resolve(reference)
        assert result.status is GeometryResolutionStatus.RESOLVED
        references.append(reference)
        descriptors.append(result.planar_face)
    assert references[0].occurrence_path != references[1].occurrence_path
    assert references[0].subshape_selector != references[1].subshape_selector
    for node, descriptor in zip(repeated, descriptors, strict=True):
        assert descriptor is not None
        assert descriptor.bounds.minimum.x >= node.bounding_box.x_min - 1.0e-6
        assert descriptor.bounds.maximum.x <= node.bounding_box.x_max + 1.0e-6


def test_arc_boundary_is_supported_and_non_planar_face_is_rejected() -> None:
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(
        BRepPrimAPI_MakeCylinder(5.0, 3.0).Shape(), CadFormat.GENERATED
    )
    document_id = metadata.document_id
    source_id = uuid4()
    mapping = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, kernel.get_document_tree(document_id)
    )
    resolver = OcpPlanarFaceResolver(kernel, document_id, source_id, mapping, LengthUnit.MM)
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id), TopAbs_ShapeEnum.TopAbs_FACE, faces)
    planar_index = non_planar_index = None
    for index in range(1, faces.Extent() + 1):
        surface = BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(index)), True)
        if surface.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
            planar_index = index
        else:
            non_planar_index = index
    object_id = next(iter(mapping.by_runtime))
    planar = SelectionMetadata(document_id, f"{document_id}:face:{planar_index}",
                               SelectionMode.FACE, metadata.bounding_box, object_id)
    result = resolver.resolve(resolver.bind_selection(planar))
    assert result.planar_face is not None
    assert {curve.value for curve in result.planar_face.outer_boundary.source_curves} == {"arc"}

    curved = replace(planar, selection_id=f"{document_id}:face:{non_planar_index}")
    with pytest.raises(PlanarFaceResolutionError) as captured:
        resolver.bind_selection(curved)
    assert captured.value.code is DiagnosticCode.FACING_NON_PLANAR_FACE


def test_ocp_face_with_inner_wire_is_rejected_with_explicit_diagnostic() -> None:
    def wire(points):
        builder = BRepBuilderAPI_MakePolygon()
        for x, y in points:
            builder.Add(gp_Pnt(x, y, 0.0))
        builder.Close()
        return builder.Wire()

    face_builder = BRepBuilderAPI_MakeFace(wire(((0, 0), (10, 0), (10, 10), (0, 10))))
    face_builder.Add(wire(((2, 2), (2, 4), (4, 4), (4, 2))))
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(face_builder.Face(), CadFormat.GENERATED)
    document_id = metadata.document_id
    source_id = uuid4()
    mapping = build_persistent_object_map(
        source_id, CadGeometryKind.BREP, kernel.get_document_tree(document_id)
    )
    resolver = OcpPlanarFaceResolver(kernel, document_id, source_id, mapping, LengthUnit.MM)
    selection = SelectionMetadata(
        document_id, f"{document_id}:face:1", SelectionMode.FACE,
        metadata.bounding_box, next(iter(mapping.by_runtime)),
    )
    with pytest.raises(PlanarFaceResolutionError) as captured:
        resolver.bind_selection(selection)
    assert captured.value.code is DiagnosticCode.FACING_UNSUPPORTED_INNER_LOOPS
