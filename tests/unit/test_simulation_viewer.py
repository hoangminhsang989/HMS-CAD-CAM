"""Phase 7C.2 native presentation, OCP transaction and stale-guard tests."""

from __future__ import annotations

import dataclasses
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactState,
    BoxStock,
    CamNodeId,
    ContentFingerprint,
    CylindricalGeometry,
    DependencyFingerprint,
    DiagnosticSeverity,
    FeedRate,
    FeedUnit,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    HolderDefinition,
    HolderDefinitionId,
    HolderSection,
    Length,
    LengthUnit,
    Operation,
    OperationFamily,
    OperationId,
    OperationParameterSet,
    Point3,
    Revision,
    SimulationResultId,
    Setup,
    SetupId,
    SetupKind,
    ShankGeometry,
    SourceScope,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolpathArtifactId,
    Vector3,
    WcsFrame,
    WorkOffset,
)
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.simulation import (
    SimulationIssue,
    SimulationIssueCategory,
    SimulationIssueCode,
    SimulationResult,
    SimulationSamplingPolicy,
    SimulationStatistics,
    SimulationStatus,
    build_simulation_request,
)
from hms_cadcam.cam.simulation.sampling import sample_toolpath
from hms_cadcam.cam.toolpath import MotionClass, Pose, ToolpathBuilder
from hms_cadcam.cam.toolpath.geometry import Bounds3
from hms_cadcam.viewer.ocp import backend as ocp_backend_module
from hms_cadcam.viewer.simulation import (
    SimulationDisplayContext,
    SimulationDisplayPolicy,
    SimulationMarkerKind,
    SimulationPathSemantic,
    SimulationPresentation,
    SimulationPresentationRegistry,
)


def _mm(value: float) -> Length:
    return Length(value, LengthUnit.MM)


def _pose(x: float, y: float, z: float) -> Pose:
    return Pose(Point3(x, y, z, LengthUnit.MM), Vector3(0, 0, 1))


def _fixture(
    codes: tuple[SimulationIssueCode, ...] = (),
    *,
    operation_id: OperationId | None = None,
    project_id=None,
    result_id: SimulationResultId | None = None,
):
    operation_id = operation_id or OperationId.new()
    project_id = project_id or uuid4()
    source_id = uuid4()
    reference = GeometryReference(
        GeometryReferenceId.new(),
        "hms_persistent_geometry",
        1,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"model": 1}),
        Revision(1),
        subshape_selector="face:1",
    )
    frame = WcsFrame.identity(LengthUnit.MM)
    setup_id = SetupId.new()
    setup = Setup(
        setup_id,
        "Setup",
        SetupKind.MILL,
        frame,
        WorkOffset("PRIMARY", 1),
        BoxStock(_mm(30), _mm(30), _mm(10), frame),
        reference,
        SourceScope(source_id),
        revision=Revision(0),
    )
    tool = ToolDefinition(
        ToolDefinitionId.new(),
        "End mill",
        ToolFamily.END_MILL,
        LengthUnit.MM,
        CylindricalGeometry(_mm(4), _mm(10)),
        _mm(80),
        _mm(20),
        ShankGeometry(_mm(4), _mm(60)),
        Revision(2),
    )
    holder = HolderDefinition(
        HolderDefinitionId.new(),
        "Holder",
        LengthUnit.MM,
        (HolderSection(_mm(0), _mm(30), _mm(20), _mm(30)),),
        _mm(0),
        Revision(3),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Assembly", tool, _mm(20), _mm(60), holder
    )
    artifact_input = DependencyFingerprint.from_payload({"operation": 1})
    computing, token = ArtifactState().begin(artifact_input)
    operation = Operation(
        operation_id,
        CamNodeId.new(),
        OperationFamily.MILLING,
        setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (),
        OperationParameterSet("mill.simulation", 1),
        revision=Revision(0),
        artifact_state=computing,
    )
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation_id,
        operation_revision=operation.revision,
        computation_token=token,
        input_fingerprint=artifact_input,
        unit=LengthUnit.MM,
        setup_id=setup_id,
        setup_revision=setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(frame.to_dict()),
        tool_assembly_id=assembly.assembly_id,
        tool_assembly_fingerprint=assembly.content_fingerprint,
    )
    rapid = FeedRate(1000, FeedUnit.MM_PER_MINUTE)
    feed = FeedRate(100, FeedUnit.MM_PER_MINUTE)
    builder.set_initial_pose(_pose(0, 0, 6))
    builder.rapid_to(_pose(0, 0, 4), rapid_rate=rapid, provenance="cycle.approach")
    builder.linear_to(_pose(5, 0, 4), feed, provenance="cycle.cutting")
    builder.arc_to(
        _pose(6, 1, 4),
        center=Point3(5, 1, 4, LengthUnit.MM),
        plane_normal=Vector3(0, 0, 1),
        sweep_radians=math.pi / 2,
        feed_rate=feed,
        motion_class=MotionClass.LINK,
        provenance="cycle.link",
    )
    builder.linear_to(
        _pose(6, 1, 6),
        feed,
        motion_class=MotionClass.RETRACT,
        provenance="cycle.retract",
    )
    builder.rapid_to(_pose(0, 0, 6), rapid_rate=rapid, provenance="cycle.rapid")
    artifact = builder.finalize()
    valid_state, accepted = operation.artifact_state.publish(
        token,
        artifact_input,
        artifact.artifact_fingerprint,
    )
    assert accepted
    operation = dataclasses.replace(operation, artifact_state=valid_state)
    request = build_simulation_request(
        operation=operation,
        artifact=artifact,
        setup=setup,
        tool=tool,
        assembly=assembly,
        holder=holder,
        machine=None,
        sampling_policy=SimulationSamplingPolicy(
            max_linear_step=0.5,
            chord_tolerance=0.01,
            max_arc_angle=math.pi / 8,
        ),
        safe_height=5,
    )
    sampling = sample_toolpath(
        artifact=artifact,
        wcs=frame,
        policy=request.sampling_policy,
    )
    issues = tuple(
        _issue(code, request, sampling, index)
        for index, code in enumerate(codes)
    )
    errors = sum(issue.severity is DiagnosticSeverity.ERROR for issue in issues)
    warnings = sum(issue.severity is DiagnosticSeverity.WARNING for issue in issues)
    collisions = sum(
        issue.category in {
            SimulationIssueCategory.COLLISION,
            SimulationIssueCategory.GOUGE,
        }
        for issue in issues
    )
    status = (
        SimulationStatus.FAIL
        if errors or collisions
        else SimulationStatus.WARN if issues else SimulationStatus.PASS
    )
    statistics = SimulationStatistics(
        len(sampling.samples),
        len(sampling.segments),
        collisions,
        warnings,
        errors,
        Bounds3.from_points(
            tuple(sample.world_pose.position for sample in sampling.samples)
        ),
    )
    result = SimulationResult.create(
        result_id=result_id or SimulationResultId.new(),
        request=request,
        status=status,
        issues=issues,
        statistics=statistics,
    )
    context = SimulationDisplayContext(
        project_id,
        7,
        operation.operation_id,
        operation.revision,
        True,
        True,
        artifact.artifact_id,
        artifact.artifact_fingerprint,
        request.input_fingerprint,
        result.result_id,
        result.result_fingerprint,
    )
    return operation, artifact, frame, request, sampling, result, context


def _issue(code, request, sampling, index: int) -> SimulationIssue:
    collisions = {
        SimulationIssueCode.TOOL_FIXTURE_COLLISION,
        SimulationIssueCode.SHANK_STOCK_COLLISION,
        SimulationIssueCode.SHANK_FIXTURE_COLLISION,
        SimulationIssueCode.HOLDER_STOCK_COLLISION,
        SimulationIssueCode.HOLDER_FIXTURE_COLLISION,
    }
    if code in collisions:
        category, severity = SimulationIssueCategory.COLLISION, DiagnosticSeverity.ERROR
    elif code is SimulationIssueCode.GOUGE_DETECTED:
        category, severity = SimulationIssueCategory.GOUGE, DiagnosticSeverity.ERROR
    elif code in {SimulationIssueCode.RAPID_BELOW_SAFE, SimulationIssueCode.FAILED}:
        category, severity = (
            SimulationIssueCategory.CLEARANCE_WARNING,
            DiagnosticSeverity.WARNING,
        )
    elif code is SimulationIssueCode.UNSUPPORTED_GEOMETRY:
        category, severity = (
            SimulationIssueCategory.UNSUPPORTED_GEOMETRY,
            DiagnosticSeverity.WARNING,
        )
    else:
        category, severity = (
            SimulationIssueCategory.INVALID_ARTIFACT,
            DiagnosticSeverity.ERROR,
        )
    segment_index = index % len(sampling.segments)
    segment = sampling.segments[segment_index]
    sample_index = segment.sample_indices[min(1, len(segment.sample_indices) - 1)]
    point = sampling.samples[sample_index].world_pose.position
    bounds = Bounds3(
        Point3(point.x - 0.1, point.y - 0.1, point.z - 0.1, point.unit),
        Point3(point.x + 0.1, point.y + 0.1, point.z + 0.1, point.unit),
    )
    return SimulationIssue(
        severity=severity,
        category=category,
        code=code,
        message_key="simulation.issue",
        operation_id=request.operation_id,
        artifact_id=request.artifact_id,
        segment_index=segment.segment_index,
        event_index=segment.event_index,
        sample_index=sample_index,
        world_point=point,
        bounds=bounds,
        involved_entities=(f"fixture:{index % 2}", "occurrence:shared"),
        evidence=(("proof", "exact"), ("slot", str(index))),
    )


@pytest.mark.parametrize(
    ("codes", "status"),
    [
        ((), SimulationStatus.PASS),
        ((SimulationIssueCode.RAPID_BELOW_SAFE,), SimulationStatus.WARN),
        ((SimulationIssueCode.TOOL_FIXTURE_COLLISION,), SimulationStatus.FAIL),
    ],
)
def test_pass_warn_fail_presentation_is_deterministic_and_native_free(
    codes,
    status,
) -> None:
    _, artifact, wcs, _, _, result, context = _fixture(codes)
    first = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context
    )
    second = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context
    )
    assert first == second
    assert first.status is status
    assert first.statistics == result.statistics
    assert "OCP" not in repr(first) and "QObject" not in repr(first)
    assert "G-code" not in repr(first) and "callback" not in repr(first)


def test_line_arc_path_semantics_provenance_and_junctions_are_preserved() -> None:
    _, artifact, wcs, _, _, result, context = _fixture()
    presentation = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context
    )
    assert tuple(item.semantic for item in presentation.path_segments) == (
        SimulationPathSemantic.APPROACH,
        SimulationPathSemantic.CUTTING,
        SimulationPathSemantic.LINK,
        SimulationPathSemantic.RETRACT,
        SimulationPathSemantic.RAPID,
    )
    assert presentation.path_segments[2].event_kind == "arc"
    for previous, current in zip(
        presentation.path_segments,
        presentation.path_segments[1:],
    ):
        assert previous.points[-1] == current.points[0]
        assert previous.sample_indices[-1] == current.sample_indices[0]


def test_deterministic_decimation_keeps_endpoints_and_issue_adjacent_samples() -> None:
    _, artifact, wcs, _, sampling, result, context = _fixture(
        (SimulationIssueCode.RAPID_BELOW_SAFE,)
    )
    policy = SimulationDisplayPolicy(maximum_path_points=6, maximum_markers=2)
    first = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context, policy=policy
    )
    second = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context, policy=policy
    )
    assert first.path_segments == second.path_segments
    assert first.total_path_point_count == len(sampling.samples)
    assert first.displayed_path_point_count < first.total_path_point_count
    for source, displayed in zip(sampling.segments, first.path_segments, strict=True):
        assert displayed.sample_indices[0] == source.sample_indices[0]
        assert displayed.sample_indices[-1] == source.sample_indices[-1]
    issue_sample = result.issues[0].sample_index
    displayed_indices = {
        item for segment in first.path_segments for item in segment.sample_indices
    }
    assert issue_sample in displayed_indices


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (SimulationIssueCode.TOOL_FIXTURE_COLLISION, SimulationMarkerKind.TOOL_FIXTURE_COLLISION),
        (SimulationIssueCode.SHANK_STOCK_COLLISION, SimulationMarkerKind.SHANK_STOCK_COLLISION),
        (SimulationIssueCode.SHANK_FIXTURE_COLLISION, SimulationMarkerKind.SHANK_FIXTURE_COLLISION),
        (SimulationIssueCode.HOLDER_STOCK_COLLISION, SimulationMarkerKind.HOLDER_STOCK_COLLISION),
        (SimulationIssueCode.HOLDER_FIXTURE_COLLISION, SimulationMarkerKind.HOLDER_FIXTURE_COLLISION),
        (SimulationIssueCode.GOUGE_DETECTED, SimulationMarkerKind.GOUGE),
        (SimulationIssueCode.RAPID_BELOW_SAFE, SimulationMarkerKind.RAPID_BELOW_SAFE),
        (SimulationIssueCode.FAILED, SimulationMarkerKind.CLEARANCE_WARNING),
        (SimulationIssueCode.UNSUPPORTED_GEOMETRY, SimulationMarkerKind.UNSUPPORTED),
        (SimulationIssueCode.INVALID_REQUEST, SimulationMarkerKind.INVALID),
    ],
)
def test_issue_categories_build_deterministic_world_and_bounds_markers(code, kind) -> None:
    _, artifact, wcs, _, _, result, context = _fixture((code,))
    presentation = SimulationPresentation.from_result(
        result=result, artifact=artifact, wcs=wcs, context=context
    )
    marker = presentation.markers[0]
    assert marker.kind is kind
    assert marker.world_point is not None and marker.bounds is not None
    assert marker.marker_id == marker.evidence_fingerprint.digest
    assert marker.entity_ids == ("fixture:0", "occurrence:shared")
    assert presentation.issue_evidence[0].count == 1


def test_bounds_only_issue_uses_bounds_center_without_changing_identity() -> None:
    _, artifact, wcs, request, sampling, result, context = _fixture(
        (SimulationIssueCode.FAILED,)
    )
    issue = dataclasses.replace(result.issues[0], world_point=None, sample_index=None)
    statistics = dataclasses.replace(result.statistics)
    bounds_result = SimulationResult.create(
        result_id=SimulationResultId.new(),
        request=request,
        status=SimulationStatus.WARN,
        issues=(issue,),
        statistics=statistics,
    )
    bounds_context = dataclasses.replace(
        context,
        current_result_id=bounds_result.result_id,
        current_result_fingerprint=bounds_result.result_fingerprint,
    )
    marker = SimulationPresentation.from_result(
        result=bounds_result,
        artifact=artifact,
        wcs=wcs,
        context=bounds_context,
    ).markers[0]
    assert marker.world_point is None and marker.anchor_point is not None
    assert marker.bounds == issue.bounds
    assert len(sampling.samples) == bounds_result.statistics.sampled_point_count


def test_marker_cap_never_drops_errors_before_warnings() -> None:
    codes = (
        SimulationIssueCode.TOOL_FIXTURE_COLLISION,
        SimulationIssueCode.RAPID_BELOW_SAFE,
        SimulationIssueCode.SHANK_STOCK_COLLISION,
        SimulationIssueCode.FAILED,
    )
    _, artifact, wcs, _, _, result, context = _fixture(codes)
    presentation = SimulationPresentation.from_result(
        result=result,
        artifact=artifact,
        wcs=wcs,
        context=context,
        policy=SimulationDisplayPolicy(maximum_path_points=100, maximum_markers=1),
    )
    assert presentation.total_marker_count == 4
    assert presentation.displayed_marker_count == 2
    assert presentation.marker_cap_overflow
    assert all(marker.severity is DiagnosticSeverity.ERROR for marker in presentation.markers)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_operation",
        "source_fingerprint",
        "input_fingerprint",
        "result_not_current",
        "operation_deleted",
        "operation_disabled",
        "operation_revision",
    ],
)
def test_result_source_and_runtime_stale_guards_reject(mutation: str) -> None:
    _, artifact, wcs, _, _, result, context = _fixture()
    updates = {
        "wrong_operation": {"operation_id": OperationId.new()},
        "source_fingerprint": {"artifact_fingerprint": ContentFingerprint.from_payload({"stale": 1})},
        "input_fingerprint": {"simulation_input_fingerprint": DependencyFingerprint.from_payload({"stale": 1})},
        "result_not_current": {"current_result_id": SimulationResultId.new()},
        "operation_deleted": {"operation_exists": False},
        "operation_disabled": {"operation_enabled": False},
        "operation_revision": {"operation_revision": Revision(99)},
    }[mutation]
    with pytest.raises(CamValidationError, match="stale|mismatched"):
        SimulationPresentation.from_result(
            result=result,
            artifact=artifact,
            wcs=wcs,
            context=dataclasses.replace(context, **updates),
        )


@pytest.mark.parametrize(
    "mutation",
    ["future", "fingerprint", "issue_order", "non_finite", "malformed_issue"],
)
def test_future_fingerprint_nonfinite_and_malformed_results_fail_closed(mutation) -> None:
    _, artifact, wcs, _, _, result, context = _fixture(
        (
            SimulationIssueCode.TOOL_FIXTURE_COLLISION,
            SimulationIssueCode.RAPID_BELOW_SAFE,
        )
    )
    if mutation == "future":
        object.__setattr__(result, "algorithm_version", 99)
    elif mutation == "fingerprint":
        object.__setattr__(result, "result_fingerprint", ContentFingerprint.from_payload({"wrong": 1}))
    elif mutation == "issue_order":
        object.__setattr__(result, "issues", tuple(reversed(result.issues)))
    elif mutation == "non_finite":
        object.__setattr__(result.issues[0].world_point, "x", float("nan"))
    else:
        object.__setattr__(result.issues[0], "sample_index", 999_999)
    with pytest.raises(Exception):
        SimulationPresentation.from_result(
            result=result, artifact=artifact, wcs=wcs, context=context
        )


def test_registry_current_key_visibility_callback_and_project_isolation() -> None:
    operation, artifact, wcs, _, _, result, context = _fixture()
    registry = SimulationPresentationRegistry()
    registry.bind_project(context.project_id, context.project_generation)
    stale_request = registry.request_display(operation.operation_id, generation=7)
    current_request = registry.request_display(operation.operation_id, generation=7)
    assert stale_request is not None and current_request is not None
    assert not registry.display(
        result=result,
        artifact=artifact,
        wcs=wcs,
        context=context,
        request=stale_request,
    )
    assert registry.display(
        result=result,
        artifact=artifact,
        wcs=wcs,
        context=context,
        request=current_request,
    )
    registry.set_visible(operation.operation_id, False)
    assert not registry.presentations[0].visible
    marker_fixture = _fixture(
        (SimulationIssueCode.FAILED,),
        operation_id=operation.operation_id,
        project_id=context.project_id,
    )
    _, replacement_artifact, replacement_wcs, _, _, replacement, replacement_context = marker_fixture
    assert registry.display(
        result=replacement,
        artifact=replacement_artifact,
        wcs=replacement_wcs,
        context=replacement_context,
    )
    assert not registry.presentations[0].visible
    marker = registry.presentations[0].markers[0]
    assert registry.lookup_issue(
        project_id=context.project_id,
        operation_id=operation.operation_id,
        result_id=replacement.result_id,
        marker_id=marker.marker_id,
    ) == marker
    assert registry.lookup_issue(
        project_id=uuid4(),
        operation_id=operation.operation_id,
        result_id=replacement.result_id,
        marker_id=marker.marker_id,
    ) is None
    registry.bind_project(uuid4(), 8)
    assert registry.presentations == ()


class _Context:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
        self.removed: list[object] = []
        self.deactivated: set[object] = set()
        self.fail_display = False
        self.fail_remove: set[object] = set()

    def SetColor(self, *_args) -> None:
        return None

    def Display(self, presentation, *_args) -> None:
        if self.fail_display:
            raise RuntimeError("injected display failure")
        self.displayed.add(presentation)

    def Erase(self, presentation, *_args) -> None:
        self.displayed.discard(presentation)

    def Remove(self, presentation, *_args) -> None:
        if presentation in self.fail_remove:
            self.fail_remove.remove(presentation)
            raise RuntimeError("injected remove failure")
        self.displayed.discard(presentation)
        self.removed.append(presentation)

    def Deactivate(self, presentation) -> None:
        self.deactivated.add(presentation)

    def UpdateCurrentViewer(self) -> None:
        return None


class _Builder:
    def MakeCompound(self, _compound) -> None:
        return None

    def Add(self, _compound, _shape) -> None:
        return None


def _ocp_backend(monkeypatch: pytest.MonkeyPatch):
    context = _Context()
    backend = object.__new__(ocp_backend_module.OcpCadViewportBackend)
    backend._lifecycle = SimpleNamespace(initialized=True, context=context)
    backend._toolpaths = {}
    backend._toolpath_metadata = {}
    backend._simulations = {}
    backend._simulation_marker_objects = {}
    backend._simulation_registry = SimulationPresentationRegistry()
    monkeypatch.setattr(ocp_backend_module, "TopoDS_Compound", object)
    monkeypatch.setattr(ocp_backend_module, "BRep_Builder", _Builder)
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeEdge",
        lambda *_args: SimpleNamespace(Edge=lambda: object()),
    )
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeVertex",
        lambda *_args: SimpleNamespace(Vertex=lambda: object()),
    )
    monkeypatch.setattr(ocp_backend_module, "AIS_Shape", lambda _shape: object())
    monkeypatch.setattr(ocp_backend_module, "Quantity_Color", lambda *_args: object())
    return backend, context


def test_ocp_show_hide_source_independence_selection_lookup_remove_and_clear(monkeypatch) -> None:
    backend, native_context = _ocp_backend(monkeypatch)
    operation, artifact, wcs, _, _, result, context = _fixture(
        (SimulationIssueCode.FAILED,)
    )
    source_native = object()
    backend._toolpaths[operation.operation_id] = (source_native,)
    backend.bind_simulation_project(context.project_id, 7)
    assert backend.display_simulation(result, artifact, wcs, context)
    metadata = backend.get_simulation_presentations()[0]
    simulation_objects = backend._simulations[operation.operation_id]
    assert source_native not in simulation_objects
    assert all(item in native_context.deactivated for item in simulation_objects)
    marker = metadata.markers[0]
    assert backend.lookup_simulation_issue(
        project_id=context.project_id,
        operation_id=operation.operation_id,
        result_id=result.result_id,
        marker_id=marker.marker_id,
    ) == marker
    marker_native_id = next(iter(backend._simulation_marker_objects[operation.operation_id]))
    marker_native = next(item for item in simulation_objects if id(item) == marker_native_id)
    assert backend.lookup_native_simulation_marker(marker_native) == marker
    backend.set_simulation_visibility(operation.operation_id, False)
    assert source_native in backend._toolpaths[operation.operation_id]
    assert not backend.get_simulation_presentations()[0].visible
    backend.set_simulation_visibility(operation.operation_id, True)
    backend.remove_simulation(operation.operation_id)
    assert backend.get_simulation_presentations() == ()
    assert source_native in backend._toolpaths[operation.operation_id]
    backend.clear_simulations()


def test_ocp_conversion_and_display_failure_keep_previous_overlay(monkeypatch) -> None:
    backend, native_context = _ocp_backend(monkeypatch)
    operation, artifact, wcs, _, _, first, first_context = _fixture(
        (), operation_id=OperationId.new()
    )
    backend.bind_simulation_project(first_context.project_id, 7)
    assert backend.display_simulation(first, artifact, wcs, first_context)
    old_objects = backend._simulations[operation.operation_id]
    old_metadata = backend.get_simulation_presentations()[0]
    replacement_fixture = _fixture(
        (SimulationIssueCode.FAILED,),
        operation_id=operation.operation_id,
        project_id=first_context.project_id,
    )
    _, next_artifact, next_wcs, _, _, replacement, next_context = replacement_fixture
    native_context.fail_display = True
    with pytest.raises(RuntimeError, match="injected display failure"):
        backend.display_simulation(replacement, next_artifact, next_wcs, next_context)
    native_context.fail_display = False
    assert backend._simulations[operation.operation_id] == old_objects
    assert backend.get_simulation_presentations() == (old_metadata,)
    malformed = dataclasses.replace(next_context, operation_enabled=False)
    assert not backend.display_simulation(replacement, next_artifact, next_wcs, malformed)
    assert backend._simulations[operation.operation_id] == old_objects


def test_ocp_conversion_failure_keeps_previous_overlay(monkeypatch) -> None:
    backend, _ = _ocp_backend(monkeypatch)
    operation, artifact, wcs, _, _, first, first_context = _fixture(
        (), operation_id=OperationId.new()
    )
    backend.bind_simulation_project(first_context.project_id, 7)
    assert backend.display_simulation(first, artifact, wcs, first_context)
    old_objects = backend._simulations[operation.operation_id]
    old_metadata = backend.get_simulation_presentations()[0]
    replacement_fixture = _fixture(
        (SimulationIssueCode.FAILED,),
        operation_id=operation.operation_id,
        project_id=first_context.project_id,
    )
    _, next_artifact, next_wcs, _, _, replacement, next_context = replacement_fixture
    object.__setattr__(replacement, "result_fingerprint", ContentFingerprint.from_payload({"wrong": 1}))
    with pytest.raises(CamValidationError):
        backend.display_simulation(replacement, next_artifact, next_wcs, next_context)
    assert backend._simulations[operation.operation_id] == old_objects
    assert backend.get_simulation_presentations() == (old_metadata,)


def test_source_artifact_recompute_invalidates_old_simulation_overlay(monkeypatch) -> None:
    backend, _ = _ocp_backend(monkeypatch)
    operation, artifact, wcs, _, _, result, context = _fixture(
        (), operation_id=OperationId.new()
    )
    backend.bind_simulation_project(context.project_id, 7)
    assert backend.display_simulation(result, artifact, wcs, context)
    replacement_fixture = _fixture(
        (), operation_id=operation.operation_id, project_id=context.project_id
    )
    _, replacement_artifact, _, _, _, _, _ = replacement_fixture
    backend.display_toolpath(replacement_artifact)
    assert backend.get_simulation_presentations() == ()
    assert operation.operation_id in backend._toolpaths


def test_ocp_old_remove_failure_rolls_back_registry_visibility_and_no_orphans(monkeypatch) -> None:
    backend, native_context = _ocp_backend(monkeypatch)
    operation_id = OperationId.new()
    first_fixture = _fixture((), operation_id=operation_id)
    _, first_artifact, first_wcs, _, _, first, first_context = first_fixture
    backend.bind_simulation_project(first_context.project_id, 7)
    assert backend.display_simulation(first, first_artifact, first_wcs, first_context)
    backend.set_simulation_visibility(operation_id, False)
    old_objects = backend._simulations[operation_id]
    old_metadata = backend.get_simulation_presentations()[0]
    replacement_fixture = _fixture(
        (SimulationIssueCode.TOOL_FIXTURE_COLLISION,),
        operation_id=operation_id,
        project_id=first_context.project_id,
    )
    _, artifact, wcs, _, _, result, context = replacement_fixture
    native_context.fail_remove.add(old_objects[0])
    with pytest.raises(RuntimeError, match="injected remove failure"):
        backend.display_simulation(result, artifact, wcs, context)
    assert backend._simulations[operation_id] == old_objects
    assert backend.get_simulation_presentations() == (old_metadata,)
    assert not backend.get_simulation_presentations()[0].visible
    assert all(item not in native_context.displayed for item in old_objects)


def test_ocp_operation_and_project_isolation_and_lifecycle_cleanup(monkeypatch) -> None:
    backend, _ = _ocp_backend(monkeypatch)
    project_id = uuid4()
    first = _fixture((), project_id=project_id)
    second = _fixture((SimulationIssueCode.FAILED,), project_id=project_id)
    backend.bind_simulation_project(project_id, 7)
    for fixture in (first, second):
        _, artifact, wcs, _, _, result, context = fixture
        assert backend.display_simulation(result, artifact, wcs, context)
    first_operation = first[0].operation_id
    second_operation = second[0].operation_id
    backend.remove_simulation(first_operation)
    assert tuple(
        item.key.operation_id for item in backend.get_simulation_presentations()
    ) == (second_operation,)
    backend.bind_simulation_project(uuid4(), 8)
    assert backend.get_simulation_presentations() == ()
    assert backend._simulations == {}
