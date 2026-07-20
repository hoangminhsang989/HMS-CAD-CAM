"""Stage 7B.6.1 drilling geometry/model/resolver foundation tests."""

from dataclasses import fields, is_dataclass, replace
import json
from uuid import uuid4

import pytest
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Compound

from hms_cadcam.cad.models import CadFormat, CadGeometryKind
from hms_cadcam.cad.ocp.kernel import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpDrillingGeometryResolver
from hms_cadcam.cam.adapters.ocp_drilling import DrillingGeometryResolutionError
from hms_cadcam.cam.application import DrillingGeometryResolver
from hms_cadcam.cam.domain import (
    DiagnosticCode,
    DiagnosticSeverity,
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillingRegion,
    DrillValidationError,
    GeometryReferenceId,
    GeometryReference,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryFingerprint,
    GeometryResolutionStatus,
    HoleLocation,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    Length,
    LengthUnit,
    Point3,
    Revision,
    ResolvedHoleLocation,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from spikes.xcaf_step.fixture import write_xcaf_step_fixture


def _depth(unit: LengthUnit = LengthUnit.MM) -> DrillDepthDefinition:
    return DrillDepthDefinition(unit, Length(5, unit), Length(0, unit))


def _point(
    x: float,
    y: float,
    z: float = 5.0,
    unit: LengthUnit = LengthUnit.MM,
) -> HoleLocation:
    position = Point3(x, y, z, unit)
    return HoleLocation(
        position,
        Vector3(0, 0, 1),
        position,
        None,
        unit,
    )


def _persistent_vertex(
    x: float,
    *,
    source_id=None,
    occurrence_path: str = "root/part:1",
) -> HoleLocation:
    source_id = source_id or uuid4()
    position = Point3(x, 0, 5, LengthUnit.MM)
    reference = GeometryReference(
        GeometryReferenceId.new(),
        "hms_persistent_geometry",
        1,
        source_id,
        GeometryReferenceKind.VERTEX,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({
            "x": x,
            "occurrence_path": occurrence_path,
        }),
        Revision(0),
        occurrence_path=occurrence_path,
        subshape_selector=f"vertex:{x}:{occurrence_path}",
    )
    hole_reference = HoleReference(
        reference,
        Vector3(0, 0, 1),
        Point3(0, 0, 5, LengthUnit.MM),
        LengthUnit.MM,
    )
    return HoleLocation(
        position,
        Vector3(0, 0, 1),
        Point3(0, 0, 5, LengthUnit.MM),
        None,
        LengthUnit.MM,
        HoleSourceKind.BREP_VERTEX,
        hole_reference,
    )


class _PatternReferenceResolver:
    def __init__(self, responses) -> None:
        self.responses = responses
        self.calls = []

    def resolve(self, reference: HoleReference) -> ResolvedHoleLocation:
        self.calls.append(reference)
        return self.responses[reference.reference.reference_id]


def _context(
    kernel: OcpCadKernel,
    document_id,
    mode: SelectionMode,
    index: int = 1,
    *,
    source_id=None,
    object_id=None,
):
    source_id = source_id or uuid4()
    mapping = build_persistent_object_map(
        source_id,
        CadGeometryKind.BREP,
        kernel.get_document_tree(document_id),
    )
    object_id = object_id or next(iter(mapping.by_runtime))
    selection = SelectionMetadata(
        document_id,
        f"{document_id}:{mode.value}:{index}",
        mode,
        kernel.get_bounding_box(document_id),
        object_id,
    )
    resolver = OcpDrillingGeometryResolver(
        kernel, document_id, source_id, mapping, LengthUnit.MM
    )
    return resolver, selection


def test_single_explicit_hole_point_and_region_codec_round_trip() -> None:
    pattern = HolePattern((_point(4, 7),), LengthUnit.MM)
    geometry_input = DrillGeometryInput(pattern, LengthUnit.MM)
    result = DrillingGeometryResolver().resolve(geometry_input, _depth())

    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.region is not None
    assert result.region.pattern.locations[0].position == Point3(
        4, 7, 5, LengthUnit.MM
    )
    assert result.region.depth.depth == Length(5, LengthUnit.MM)
    assert DrillingRegion.from_dict(result.region.to_dict()) == result.region
    assert DrillGeometryInput.from_dict(geometry_input.to_dict()) == geometry_input
    with pytest.raises(DrillValidationError) as mismatch:
        replace(
            result.region,
            pattern=HolePattern((_point(8, 9),), LengthUnit.MM),
        )
    assert mismatch.value.code is DiagnosticCode.DRILL_SOURCE_MISMATCH


@pytest.mark.parametrize("consumer", ("drilling", "tapping", "reaming"))
def test_multi_brep_pattern_is_fully_reresolved_for_hole_strategies(consumer) -> None:
    del consumer
    source_id = uuid4()
    locations = (
        _persistent_vertex(4, source_id=source_id, occurrence_path="root/part:2"),
        _persistent_vertex(1, source_id=source_id, occurrence_path="root/part:1"),
    )
    pattern = HolePattern(locations, LengthUnit.MM)
    responses = {
        location.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, location
        )
        for location in pattern.locations
    }
    port = _PatternReferenceResolver(responses)

    result = DrillingGeometryResolver(port).resolve(
        DrillGeometryInput(pattern, LengthUnit.MM), _depth()
    )

    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.region is not None and result.region.pattern == pattern
    assert tuple(port.calls) == tuple(
        location.reference for location in pattern.locations
    )
    assert tuple(
        item.reference.reference.occurrence_path
        for item in result.region.pattern.locations
    ) == ("root/part:1", "root/part:2")


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (GeometryResolutionStatus.STALE, DiagnosticCode.DRILL_GEOMETRY_STALE),
        (
            GeometryResolutionStatus.SOURCE_MISMATCH,
            DiagnosticCode.DRILL_SOURCE_MISMATCH,
        ),
    ),
)
def test_one_failed_pattern_reference_rejects_the_whole_pattern(status, code) -> None:
    first = _persistent_vertex(1)
    second = _persistent_vertex(4, source_id=first.reference.reference.source_id)
    pattern = HolePattern((first, second), LengthUnit.MM)
    failed_id = pattern.locations[0].reference.reference.reference_id
    responses = {
        location.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, location
        )
        for location in pattern.locations
    }
    responses[failed_id] = ResolvedHoleLocation(
        status,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR, code, "failed reference"
        ),),
    )
    port = _PatternReferenceResolver(responses)

    result = DrillingGeometryResolver(port).resolve(
        DrillGeometryInput(pattern, LengthUnit.MM), _depth()
    )

    assert result.status is status
    assert result.region is None
    assert result.diagnostics[0].code is code
    assert len(port.calls) == 1


def test_pattern_reresolution_rejects_cross_occurrence_and_new_duplicate() -> None:
    source_id = uuid4()
    first = _persistent_vertex(
        1, source_id=source_id, occurrence_path="root/part:1"
    )
    second = _persistent_vertex(
        4, source_id=source_id, occurrence_path="root/part:2"
    )
    pattern = HolePattern((first, second), LengthUnit.MM)
    crossed = replace(
        first,
        reference=second.reference,
        position=second.position,
    )
    cross_port = _PatternReferenceResolver({
        first.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, crossed
        ),
        second.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, second
        ),
    })
    mismatch = DrillingGeometryResolver(cross_port).resolve(
        DrillGeometryInput(pattern, LengthUnit.MM), _depth()
    )
    assert mismatch.status is GeometryResolutionStatus.SOURCE_MISMATCH
    assert mismatch.region is None

    duplicate_second = replace(second, position=first.position)
    duplicate_port = _PatternReferenceResolver({
        first.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, first
        ),
        second.reference.reference.reference_id: ResolvedHoleLocation(
            GeometryResolutionStatus.RESOLVED, duplicate_second
        ),
    })
    duplicate = DrillingGeometryResolver(duplicate_port).resolve(
        DrillGeometryInput(pattern, LengthUnit.MM), _depth()
    )
    assert duplicate.status is GeometryResolutionStatus.INVALID
    assert duplicate.region is None
    assert duplicate.diagnostics[0].code is DiagnosticCode.DRILL_DUPLICATE_LOCATION


def test_explicit_pattern_is_canonical_unique_and_fingerprint_stable() -> None:
    first, second = _point(10, 2), _point(2, 10)
    forward = HolePattern((first, second), LengthUnit.MM)
    reverse = HolePattern((second, first), LengthUnit.MM)

    assert forward == reverse
    assert forward.fingerprint == reverse.fingerprint
    assert tuple(value.position.x for value in forward.locations) == (2.0, 10.0)
    with pytest.raises(DrillValidationError) as duplicate:
        HolePattern((first, first), LengthUnit.MM)
    assert duplicate.value.code is DiagnosticCode.DRILL_DUPLICATE_LOCATION

    near_boundary = (_point(0.49e-8, 0), _point(0.51e-8, 0))
    with pytest.raises(DrillValidationError) as tolerance_duplicate:
        HolePattern(near_boundary, LengthUnit.MM)
    assert tolerance_duplicate.value.code is DiagnosticCode.DRILL_DUPLICATE_LOCATION


def test_pattern_rejects_non_planar_locations_and_unit_mismatch() -> None:
    with pytest.raises(DrillValidationError) as non_planar:
        HolePattern((_point(0, 0, 5), _point(2, 0, 6)), LengthUnit.MM)
    assert non_planar.value.code is DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY

    geometry_input = DrillGeometryInput(
        HolePattern((_point(0, 0),), LengthUnit.MM),
        LengthUnit.MM,
    )
    result = DrillingGeometryResolver().resolve(
        geometry_input,
        _depth(LengthUnit.INCH),
    )
    assert result.status is GeometryResolutionStatus.INVALID
    assert result.diagnostics[0].code is DiagnosticCode.DRILL_UNIT_MISSING


def test_depth_validation_is_finite_versioned_and_uses_facing_z_convention() -> None:
    depth = _depth()
    assert depth.depth.value == 5
    assert DrillDepthDefinition.from_dict(depth.to_dict()) == depth
    with pytest.raises(DrillValidationError) as inverted:
        DrillDepthDefinition(
            LengthUnit.MM,
            Length(0, LengthUnit.MM),
            Length(1, LengthUnit.MM),
        )
    assert inverted.value.code is DiagnosticCode.DRILL_INVALID_DEPTH
    payload = depth.to_dict()
    payload["top_z"] = float("nan")
    with pytest.raises(DrillValidationError) as non_finite:
        DrillDepthDefinition.from_dict(payload)
    assert non_finite.value.code is DiagnosticCode.DRILL_INVALID_DEPTH
    with pytest.raises(DrillValidationError) as unknown:
        DrillDepthDefinition(
            LengthUnit.UNKNOWN,
            Length(5, LengthUnit.UNKNOWN),
            Length(0, LengthUnit.UNKNOWN),
        )
    assert unknown.value.code is DiagnosticCode.DRILL_UNIT_MISSING


def test_vertex_reference_resolves_persistently_without_runtime_id() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 8, 5)
    resolver, selection = _context(kernel, document_id, SelectionMode.VERTEX)

    reference = resolver.bind_selection(selection, axis=Vector3(0, 0, 1))
    resolved = resolver.resolve(reference)
    geometry = DrillingGeometryResolver(resolver).resolve(
        DrillGeometryInput(reference, LengthUnit.MM), _depth()
    )

    assert reference.reference.kind.value == "vertex"
    assert selection.selection_id not in json.dumps(reference.to_dict(), sort_keys=True)
    assert resolved.status is GeometryResolutionStatus.RESOLVED
    assert resolved.location is not None and resolved.location.diameter is None
    assert geometry.status is GeometryResolutionStatus.RESOLVED
    assert HoleReference.from_dict(reference.to_dict()) == reference


def test_complete_circular_edge_resolves_center_axis_and_diameter() -> None:
    edge = BRepBuilderAPI_MakeEdge(
        gp_Circ(gp_Ax2(gp_Pnt(2, 3, 4), gp_Dir(0, 0, 1)), 2.5)
    ).Edge()
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(edge, CadFormat.GENERATED)
    resolver, selection = _context(
        kernel, metadata.document_id, SelectionMode.EDGE
    )

    reference = resolver.bind_selection(selection)
    result = resolver.resolve(reference)

    assert result.status is GeometryResolutionStatus.RESOLVED
    assert result.location is not None
    assert result.location.position == Point3(2, 3, 4, LengthUnit.MM)
    assert result.location.axis == Vector3(0, 0, 1)
    assert result.location.diameter == Length(5, LengthUnit.MM)
    assert result.location.source_kind is HoleSourceKind.CIRCULAR_EDGE


def test_open_or_non_circular_edge_is_rejected() -> None:
    edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(5, 0, 0)).Edge()
    kernel = OcpCadKernel()
    metadata = kernel._documents.add_brep(edge, CadFormat.GENERATED)
    resolver, selection = _context(
        kernel, metadata.document_id, SelectionMode.EDGE
    )

    with pytest.raises(DrillingGeometryResolutionError) as unsupported:
        resolver.bind_selection(selection)
    assert unsupported.value.code is DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY


def test_stale_source_mismatch_ambiguous_and_missing_fail_closed() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 8, 5)
    resolver, selection = _context(kernel, document_id, SelectionMode.VERTEX)
    reference = resolver.bind_selection(selection, axis=Vector3(0, 0, 1))

    stale = OcpDrillingGeometryResolver(
        kernel,
        document_id,
        reference.reference.source_id,
        resolver._persistent_map,
        LengthUnit.MM,
        Revision(1),
    ).resolve(reference)
    foreign = replace(
        reference,
        reference=replace(reference.reference, source_id=uuid4()),
    )
    mismatch = resolver.resolve(foreign)
    missing = DrillingGeometryResolver().resolve(None, _depth())

    assert stale.status is GeometryResolutionStatus.STALE
    assert stale.diagnostics[0].code is DiagnosticCode.DRILL_GEOMETRY_STALE
    assert mismatch.status is GeometryResolutionStatus.SOURCE_MISMATCH
    assert mismatch.diagnostics[0].code is DiagnosticCode.DRILL_SOURCE_MISMATCH
    assert missing.status is GeometryResolutionStatus.MISSING
    assert missing.diagnostics[0].code is DiagnosticCode.DRILL_GEOMETRY_MISSING

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Vertex())
    builder.Add(compound, BRepBuilderAPI_MakeVertex(gp_Pnt(0, 0, 0)).Vertex())
    builder.Add(
        compound,
        BRepBuilderAPI_MakeEdge(gp_Pnt(10, 0, 0), gp_Pnt(20, 0, 0)).Edge(),
    )
    ambiguous_kernel = OcpCadKernel()
    metadata = ambiguous_kernel._documents.add_brep(compound, CadFormat.GENERATED)
    ambiguous_resolver, ambiguous_selection = _context(
        ambiguous_kernel, metadata.document_id, SelectionMode.VERTEX
    )
    ambiguous_reference = ambiguous_resolver.bind_selection(
        ambiguous_selection, axis=Vector3(0, 0, 1)
    )
    ambiguous = ambiguous_resolver.resolve(ambiguous_reference)
    assert ambiguous.status is GeometryResolutionStatus.AMBIGUOUS
    assert ambiguous.diagnostics[0].code is DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS


def test_repeated_xcaf_occurrences_cannot_cross_resolve(tmp_path) -> None:
    source = tmp_path / "repeated.step"
    write_xcaf_step_fixture(source)
    kernel = OcpCadKernel()
    imported = kernel.import_step(source)
    document_id = imported.document_id
    assert document_id is not None
    tree = kernel.get_document_tree(document_id)
    source_id = uuid4()
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    resolver = OcpDrillingGeometryResolver(
        kernel, document_id, source_id, mapping, LengthUnit.MM
    )
    global_vertices = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(
        kernel._resolve_shape(document_id),
        TopAbs_ShapeEnum.TopAbs_VERTEX,
        global_vertices,
    )
    presentations = kernel._resolve_presentation_shapes(document_id)
    repeated = [
        node for node in tree.presentation_nodes
        if node.product_name == "Repeated Product"
    ]
    assert len(repeated) == 2
    references = []
    positions = []
    locations = []
    for node in repeated:
        local_vertices = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(
            presentations[node.object_id],
            TopAbs_ShapeEnum.TopAbs_VERTEX,
            local_vertices,
        )
        candidates = []
        for index in range(1, global_vertices.Extent() + 1):
            vertex = global_vertices.FindKey(index)
            if local_vertices.FindIndex(vertex) == 0:
                continue
            point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex))
            candidates.append(((point.X(), point.Y(), point.Z()), index))
        _coordinates, vertex_index = max(candidates)
        selection = SelectionMetadata(
            document_id,
            f"{document_id}:vertex:{vertex_index}",
            SelectionMode.VERTEX,
            node.bounding_box,
            node.object_id,
        )
        reference = resolver.bind_selection(selection, axis=Vector3(0, 0, 1))
        result = resolver.resolve(reference)
        assert result.status is GeometryResolutionStatus.RESOLVED
        assert result.location is not None
        references.append(reference)
        positions.append(result.location.position)
        locations.append(result.location)
    assert references[0].reference.occurrence_path != references[1].reference.occurrence_path
    assert references[0].fingerprint != references[1].fingerprint
    assert positions[0] != positions[1]
    pattern = HolePattern(tuple(locations), LengthUnit.MM)
    resolved_pattern = DrillingGeometryResolver(resolver).resolve(
        DrillGeometryInput(pattern, LengthUnit.MM), _depth()
    )
    assert resolved_pattern.status is GeometryResolutionStatus.RESOLVED
    assert resolved_pattern.region is not None
    assert resolved_pattern.region.pattern == pattern


def test_reference_fingerprint_ignores_editable_id_and_public_model_is_ocp_free() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(10, 8, 5)
    resolver, selection = _context(kernel, document_id, SelectionMode.VERTEX)
    first = resolver.bind_selection(selection, axis=Vector3(0, 0, 1))
    second = replace(
        first,
        reference=replace(
            first.reference,
            reference_id=GeometryReferenceId.new(),
        ),
    )
    result = DrillingGeometryResolver(resolver).resolve(
        DrillGeometryInput(first, LengthUnit.MM), _depth()
    )
    assert result.region is not None
    assert first.fingerprint == second.fingerprint

    def walk(value):
        yield value
        if is_dataclass(value):
            for field in fields(value):
                yield from walk(getattr(value, field.name))
        elif isinstance(value, (tuple, list, dict)):
            items = value.values() if isinstance(value, dict) else value
            for item in items:
                yield from walk(item)

    values = tuple(walk(result))
    assert all(
        not type(value).__module__.startswith(("OCP", "PySide6"))
        for value in values
    )
    payload = json.dumps(result.region.to_dict(), sort_keys=True)
    assert all(marker not in payload for marker in (
        str(document_id), selection.selection_id, "CadObjectId", "TopoDS", "OCP", "AIS",
    ))
