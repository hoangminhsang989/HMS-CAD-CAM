"""Stage 7B.5.1 Pocket geometry/model/validation foundation tests."""

from dataclasses import replace
import json
import math
from uuid import uuid4

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpContourProfileResolver
from hms_cadcam.cam.application import PocketGeometryResolver
from hms_cadcam.cam.domain import (
    ContourBounds,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileDescriptor,
    ContourProfileSource,
    ContourSegment,
    DiagnosticCode,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    Length,
    LengthUnit,
    OccurrenceTransformProvenance,
    PocketBoundary,
    PocketDepthDefinition,
    PocketGeometryInput,
    PocketStrategy,
    PocketValidationError,
    Point3,
    ProfileProvenance,
    ResolvedContourProfile,
    Revision,
    Vector3,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from spikes.xcaf_step.fixture import write_xcaf_step_fixture

IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


class _StaticContourResolver:
    def __init__(self, result: ResolvedContourProfile) -> None:
        self.result = result

    def resolve(self, _reference: GeometryReference) -> ResolvedContourProfile:
        return self.result


def _rectangle(unit: LengthUnit = LengthUnit.MM) -> ContourLoop:
    points = tuple(Point3(x, y, 0, unit) for x, y in ((0, 0), (20, 0), (20, 10), (0, 10)))
    return ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)


def _reference(*, occurrence: str = "assembly:1/part:1") -> GeometryReference:
    selector = f"hms_profile_v1:{'a' * 64}:face:{'b' * 64}"
    fingerprint = GeometryFingerprint.from_payload({"selector": selector, "occurrence": occurrence})
    return GeometryReference(
        GeometryReferenceId.new(), HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION, uuid4(), GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP, fingerprint, Revision(0),
        occurrence_path=occurrence, subshape_selector=selector,
    )


def _descriptor(
    loop: ContourLoop,
    reference: GeometryReference,
    *,
    x_axis: Vector3 = Vector3(1, 0, 0),
    y_axis: Vector3 = Vector3(0, 1, 0),
    normal: Vector3 = Vector3(0, 0, 1),
) -> ContourProfileDescriptor:
    points = tuple(point for segment in loop.segments for point in (segment.start, segment.end))
    unit = loop.segments[0].unit
    bounds = ContourBounds(
        Point3(min(point.x for point in points), min(point.y for point in points),
               min(point.z for point in points), unit),
        Point3(max(point.x for point in points), max(point.y for point in points),
               max(point.z for point in points), unit),
    )
    geometry = GeometryFingerprint.from_payload({"loop": loop.to_dict()})
    provenance = ProfileProvenance(
        ContourProfileSource.PLANAR_FACE_OUTER,
        OccurrenceTransformProvenance(reference.occurrence_path, IDENTITY),
    )
    return ContourProfileDescriptor(reference, loop.segments[0].start, x_axis, y_axis, normal,
                                    loop, (), bounds, unit, geometry, provenance)


def _resolve(descriptor: ContourProfileDescriptor, unit: LengthUnit | None = None):
    resolver = PocketGeometryResolver(_StaticContourResolver(
        ResolvedContourProfile(GeometryResolutionStatus.RESOLVED, descriptor)))
    return resolver.resolve(PocketGeometryInput(descriptor.reference, unit or descriptor.unit))


def test_rectangle_boundary_and_strategy_codec_round_trip() -> None:
    reference = _reference()
    descriptor = _descriptor(_rectangle(), reference)
    result = _resolve(descriptor)
    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.region is not None
    assert isinstance(result.region.boundary, PocketBoundary)
    assert len(result.region.boundary.outer_loop.segments) == 4

    depth = PocketDepthDefinition(LengthUnit.MM, Length(5, LengthUnit.MM),
                                  Length(-2, LengthUnit.MM), Length(0.25, LengthUnit.MM))
    strategy = PocketStrategy(PocketGeometryInput(reference, LengthUnit.MM), depth)
    assert PocketStrategy.from_dict(strategy.to_dict()) == strategy
    assert depth.depth == Length(7, LengthUnit.MM)
    assert depth.final_bottom_z == Length(-1.75, LengthUnit.MM)


def test_rotated_profile_basis_is_preserved() -> None:
    unit = LengthUnit.MM
    cosine = math.sqrt(0.5)
    points = tuple(Point3(x, y * cosine, y * cosine, unit)
                   for x, y in ((0, 0), (20, 0), (20, 10), (0, 10)))
    loop = ContourLoop(tuple(ContourSegment(ContourCurveKind.LINE, points[index],
        points[(index + 1) % len(points)]) for index in range(len(points))),
        ContourOrientation.COUNTERCLOCKWISE)
    reference = _reference()
    descriptor = _descriptor(loop, reference, x_axis=Vector3(1, 0, 0),
                             y_axis=Vector3(0, cosine, cosine),
                             normal=Vector3(0, -cosine, cosine))
    result = _resolve(descriptor)
    assert result.region is not None
    assert result.region.normal == descriptor.normal
    assert result.region.boundary.outer_loop == loop


def test_line_arc_profile_is_retained_exactly() -> None:
    unit = LengthUnit.MM
    loop = ContourLoop((
        ContourSegment(ContourCurveKind.LINE, Point3(-5, 0, 0, unit), Point3(5, 0, 0, unit)),
        ContourSegment(ContourCurveKind.ARC, Point3(5, 0, 0, unit), Point3(-5, 0, 0, unit),
                       Point3(0, 0, 0, unit), math.pi),
    ), ContourOrientation.COUNTERCLOCKWISE)
    result = _resolve(_descriptor(loop, _reference()))
    assert result.region is not None
    assert tuple(segment.kind for segment in result.region.boundary.outer_loop.segments) == (
        ContourCurveKind.LINE, ContourCurveKind.ARC,
    )


@pytest.mark.parametrize(("status", "source_code", "pocket_code"), (
    (GeometryResolutionStatus.MISSING, DiagnosticCode.CONTOUR_PROFILE_MISSING,
     DiagnosticCode.POCKET_PROFILE_MISSING),
    (GeometryResolutionStatus.STALE, DiagnosticCode.CONTOUR_PROFILE_STALE,
     DiagnosticCode.POCKET_PROFILE_STALE),
    (GeometryResolutionStatus.AMBIGUOUS, DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS,
     DiagnosticCode.POCKET_PROFILE_INVALID),
    (GeometryResolutionStatus.SOURCE_MISMATCH, DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
     DiagnosticCode.POCKET_PROFILE_INVALID),
    (GeometryResolutionStatus.INVALID, DiagnosticCode.CONTOUR_OPEN_PROFILE,
     DiagnosticCode.POCKET_PROFILE_INVALID),
    (GeometryResolutionStatus.INVALID, DiagnosticCode.CONTOUR_UNSUPPORTED_CURVE,
     DiagnosticCode.POCKET_UNSUPPORTED_CURVE),
    (GeometryResolutionStatus.INVALID, DiagnosticCode.CONTOUR_SELF_INTERSECTION,
     DiagnosticCode.POCKET_SELF_INTERSECTION),
    (GeometryResolutionStatus.INVALID, DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
     DiagnosticCode.POCKET_PROFILE_INVALID),
))
def test_contour_resolution_failures_map_to_pocket_diagnostics(
    status: GeometryResolutionStatus,
    source_code: DiagnosticCode,
    pocket_code: DiagnosticCode,
) -> None:
    resolver = PocketGeometryResolver(_StaticContourResolver(
        ResolvedContourProfile(status, message="invalid profile", diagnostic_code=source_code)))
    result = resolver.resolve(PocketGeometryInput(_reference(), LengthUnit.MM))
    assert result.status is status
    assert result.region is None
    assert result.diagnostics[0].code is pocket_code


def test_inner_loop_is_rejected_without_island_fallback() -> None:
    reference = _reference()
    descriptor = replace(_descriptor(_rectangle(), reference), inner_loops=(_rectangle(),))
    result = _resolve(descriptor)
    assert result.status is GeometryResolutionStatus.INVALID
    assert result.region is None
    assert result.diagnostics[0].code is DiagnosticCode.POCKET_PROFILE_INVALID


def test_invalid_depth_and_unknown_unit_fail_with_stable_diagnostics() -> None:
    unit = LengthUnit.MM
    with pytest.raises(PocketValidationError) as inverted:
        PocketDepthDefinition(unit, Length(0, unit), Length(1, unit), Length(0, unit))
    assert inverted.value.code is DiagnosticCode.POCKET_INVALID_DEPTH
    with pytest.raises(PocketValidationError) as allowance:
        PocketDepthDefinition(unit, Length(1, unit), Length(0, unit), Length(1, unit))
    assert allowance.value.code is DiagnosticCode.POCKET_INVALID_DEPTH
    payload = PocketDepthDefinition(unit, Length(1, unit), Length(0, unit), Length(0, unit)).to_dict()
    payload["top_z"] = float("nan")
    with pytest.raises(PocketValidationError) as non_finite:
        PocketDepthDefinition.from_dict(payload)
    assert non_finite.value.code is DiagnosticCode.POCKET_INVALID_DEPTH
    with pytest.raises(PocketValidationError) as unknown:
        PocketDepthDefinition(LengthUnit.UNKNOWN, Length(1, LengthUnit.UNKNOWN),
                              Length(0, LengthUnit.UNKNOWN), Length(0, LengthUnit.UNKNOWN))
    assert unknown.value.code is DiagnosticCode.POCKET_UNIT_MISSING


def test_unit_mismatch_fails_closed() -> None:
    descriptor = _descriptor(_rectangle(), _reference())
    result = _resolve(descriptor, LengthUnit.INCH)
    assert result.status is GeometryResolutionStatus.INVALID
    assert result.diagnostics[0].code is DiagnosticCode.POCKET_UNIT_MISSING
    depth = PocketDepthDefinition(LengthUnit.INCH, Length(1, LengthUnit.INCH),
                                  Length(0, LengthUnit.INCH), Length(0, LengthUnit.INCH))
    with pytest.raises(PocketValidationError) as mismatch:
        PocketStrategy(PocketGeometryInput(descriptor.reference, LengthUnit.MM), depth)
    assert mismatch.value.code is DiagnosticCode.POCKET_UNIT_MISSING


def test_region_fingerprint_is_deterministic_and_ignores_reference_runtime_identity() -> None:
    first_reference = _reference()
    second_reference = replace(first_reference, reference_id=GeometryReferenceId.new())
    first = _resolve(_descriptor(_rectangle(), first_reference))
    second = _resolve(_descriptor(_rectangle(), second_reference))
    assert first.region is not None and second.region is not None
    assert first.region.fingerprint == second.region.fingerprint
    assert first.region.boundary.fingerprint == second.region.boundary.fingerprint


def test_repeated_xcaf_occurrences_resolve_to_distinct_pocket_regions(tmp_path) -> None:
    source = tmp_path / "repeated.step"
    write_xcaf_step_fixture(source)
    kernel = OcpCadKernel()
    imported = kernel.import_step(source)
    assert imported.document_id is not None
    document_id = imported.document_id
    tree = kernel.get_document_tree(document_id)
    source_id = uuid4()
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    contour = OcpContourProfileResolver(kernel, document_id, source_id, mapping, LengthUnit.MM)
    pocket = PocketGeometryResolver(contour)
    global_faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(kernel._resolve_shape(document_id), TopAbs_ShapeEnum.TopAbs_FACE, global_faces)
    presentations = kernel._resolve_presentation_shapes(document_id)
    repeated = [node for node in tree.presentation_nodes if node.product_name == "Repeated Product"]
    assert len(repeated) == 2
    regions = []
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
        reference = contour.bind_selection(selection)
        result = pocket.resolve(PocketGeometryInput(reference, LengthUnit.MM))
        assert result.status is GeometryResolutionStatus.RESOLVED and result.region is not None
        assert result.region.reference.occurrence_path == reference.occurrence_path
        regions.append(result.region)
    assert regions[0].reference.occurrence_path != regions[1].reference.occurrence_path
    assert regions[0].fingerprint != regions[1].fingerprint


def test_public_pocket_model_is_ocp_free_and_serializes_no_runtime_state() -> None:
    reference = _reference()
    result = _resolve(_descriptor(_rectangle(), reference))
    assert result.region is not None
    values = (result, result.region, result.region.boundary,
              *result.region.boundary.outer_loop.segments)
    assert all(not type(value).__module__.startswith(("OCP", "PySide6")) for value in values)
    strategy = PocketStrategy(
        PocketGeometryInput(reference, LengthUnit.MM),
        PocketDepthDefinition(LengthUnit.MM, Length(2, LengthUnit.MM),
                              Length(0, LengthUnit.MM), Length(0, LengthUnit.MM)),
    )
    payload = json.dumps(strategy.to_dict(), sort_keys=True)
    assert all(marker not in payload for marker in ("CadDocumentId", "CadObjectId", "TopoDS", "OCP", "AIS"))
