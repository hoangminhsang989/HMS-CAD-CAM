"""Phase 7C.1 source validation and atomic publication tests."""

import dataclasses
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactState, BoxStock, CamNodeId, ContentFingerprint, CylindricalGeometry,
    DependencyFingerprint, DirtyReason, FeedRate, FeedUnit, GeometryFingerprint, GeometryReference,
    GeometryReferenceId, GeometryReferenceKind, GeometryRepresentationKind, HolderDefinition,
    HolderDefinitionId, HolderSection, Length, LengthUnit, Operation, OperationFamily,
    OperationId, OperationParameterSet, Point3, Revision, Setup, SetupId, SetupKind,
    ShankGeometry, SourceScope, ToolAssembly, ToolAssemblyId, ToolAssemblyReference,
    ToolDefinition, ToolDefinitionId, ToolFamily, ToolpathArtifactId, Vector3, WcsFrame,
    WorkOffset,
)
from hms_cadcam.cam.simulation import (
    CollisionScene, CollisionTarget, CollisionTargetKind, InMemoryAabbBackend,
    SimulationIssueCode, SimulationRuntimeService, build_simulation_request,
)
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder
from hms_cadcam.cam.toolpath.geometry import Bounds3


def _mm(value): return Length(value, LengthUnit.MM)


def _tooling():
    tool = ToolDefinition(ToolDefinitionId.new(), "End mill", ToolFamily.END_MILL, LengthUnit.MM,
        CylindricalGeometry(_mm(4), _mm(10)), _mm(80), _mm(20), ShankGeometry(_mm(4), _mm(60)), Revision(2))
    holder = HolderDefinition(HolderDefinitionId.new(), "Holder", LengthUnit.MM,
        (HolderSection(_mm(0), _mm(30), _mm(20), _mm(30)),), _mm(0), Revision(3))
    assembly = ToolAssembly.create(ToolAssemblyId.new(), "Assembly", tool, _mm(20), _mm(60), holder)
    return tool, holder, assembly


def _source():
    source_id = uuid4()
    reference = GeometryReference(GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
        GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"model": 1}), Revision(1), subshape_selector="face:1")
    frame = WcsFrame.identity(LengthUnit.MM)
    setup_id = SetupId.new()
    setup = Setup(setup_id, "Setup", SetupKind.MILL, frame, WorkOffset("PRIMARY", 1),
        BoxStock(_mm(20), _mm(20), _mm(10), frame), reference, SourceScope(source_id), revision=Revision(0))
    tool, holder, assembly = _tooling()
    fingerprint = DependencyFingerprint.from_payload({"operation": 1})
    computing, token = ArtifactState().begin(fingerprint)
    operation = Operation(OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup_id,
        ToolAssemblyReference.from_assembly(assembly), (), OperationParameterSet("mill.simulation", 1),
        revision=Revision(0), artifact_state=computing)
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=operation.operation_id,
        operation_revision=operation.revision, computation_token=token, input_fingerprint=fingerprint,
        unit=LengthUnit.MM, setup_id=setup_id, setup_revision=setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(frame.to_dict()), tool_assembly_id=assembly.assembly_id,
        tool_assembly_fingerprint=assembly.content_fingerprint)
    builder.set_initial_pose(Pose(Point3(0, 0, 20, LengthUnit.MM), Vector3(0, 0, 1)))
    builder.rapid_to(Pose(Point3(5, 0, 20, LengthUnit.MM), Vector3(0, 0, 1)), rapid_rate=FeedRate(1000, FeedUnit.MM_PER_MINUTE))
    artifact = builder.finalize()
    valid_state, accepted = operation.artifact_state.publish(token, fingerprint, artifact.artifact_fingerprint)
    assert accepted
    operation = dataclasses.replace(operation, artifact_state=valid_state)
    request = build_simulation_request(operation=operation, artifact=artifact, setup=setup,
        tool=tool, assembly=assembly, holder=holder, machine=None, safe_height=15)
    far = Bounds3(Point3(100, 100, 100, LengthUnit.MM), Point3(120, 120, 120, LengthUnit.MM))
    scene = CollisionScene(CollisionTarget("stock", CollisionTargetKind.STOCK, far))
    return operation, artifact, setup, tool, holder, assembly, request, scene


def test_valid_current_complete_artifact_runs_and_publishes_atomically():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    service = SimulationRuntimeService()
    result = service.run(request=request, artifact=artifact, setup=setup, tool=tool,
        assembly=assembly, holder=holder, scene=scene, backend=InMemoryAabbBackend())
    assert result.accepted and result.result is service.get(operation.operation_id)


def test_disabled_dirty_partial_or_stale_source_is_rejected_before_run():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    with pytest.raises(Exception) as disabled:
        build_simulation_request(operation=dataclasses.replace(operation, enabled=False), artifact=artifact,
            setup=setup, tool=tool, assembly=assembly, holder=holder, machine=None)
    assert disabled.value.code is SimulationIssueCode.INVALID_REQUEST
    dirty = dataclasses.replace(operation, artifact_state=operation.artifact_state.mark_dirty(DirtyReason.UPSTREAM_CHANGED))
    with pytest.raises(Exception) as stale:
        build_simulation_request(operation=dirty, artifact=artifact, setup=setup, tool=tool,
            assembly=assembly, holder=holder, machine=None)
    assert stale.value.code is SimulationIssueCode.SOURCE_STALE


def test_latest_token_wins_and_stale_candidate_does_not_replace_result():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    service = SimulationRuntimeService()
    first = service.begin(request)
    second = service.begin(request)
    sampling_result = service.run(request=request, artifact=artifact, setup=setup, tool=tool,
        assembly=assembly, holder=holder, scene=scene, backend=InMemoryAabbBackend())
    assert sampling_result.accepted
    stale = service.publish(request=request, token=first, candidate=sampling_result.result)
    assert not stale.accepted and stale.code is SimulationIssueCode.STALE_RESULT
    assert service.get(operation.operation_id) is sampling_result.result


def test_cancelled_recompute_preserves_previous_published_result():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    service = SimulationRuntimeService()
    first = service.run(request=request, artifact=artifact, setup=setup, tool=tool,
        assembly=assembly, holder=holder, scene=scene, backend=InMemoryAabbBackend())
    previous = first.result
    cancelled = service.run(request=dataclasses.replace(request, request_id=type(request.request_id).new()),
        artifact=artifact, setup=setup, tool=tool, assembly=assembly, holder=holder, scene=scene,
        backend=InMemoryAabbBackend(), cancellation=lambda: True)
    assert not cancelled.accepted and cancelled.code is SimulationIssueCode.CANCELLED
    assert service.get(operation.operation_id) is previous


def test_project_switch_clears_runtime_registry_without_persistence():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    service = SimulationRuntimeService()
    service.run(request=request, artifact=artifact, setup=setup, tool=tool, assembly=assembly,
        holder=holder, scene=scene, backend=InMemoryAabbBackend())
    service.bind_project("next-project", service.generation + 1)
    assert service.get(operation.operation_id) is None
