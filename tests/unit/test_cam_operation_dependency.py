"""Stage 7A.4 operation tree, DAG and recompute domain tests."""

import dataclasses
import json
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactState,
    ArtifactStatus,
    BoxStock,
    CamInvariantError,
    CamJob,
    CamJobId,
    CamNode,
    CamNodeId,
    CamNodeKind,
    CamValidationError,
    ContentFingerprint,
    DependencyEdge,
    DependencyFingerprint,
    DependencyGraph,
    DependencyKind,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DuplicateCamIdError,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionResult,
    GeometryResolutionStatus,
    Length,
    LengthUnit,
    MachineDefinitionId,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    OperationInputSnapshot,
    OperationParameterSet,
    OperationTree,
    Revision,
    Setup,
    SetupId,
    SetupKind,
    SourceScope,
    ToolAssemblyId,
    ToolAssemblyReference,
    UnsupportedCamSchemaError,
    ValidationDiagnostic,
    WcsFrame,
    WorkOffset,
    validate_operation,
)


def _reference(source_id=None, *, occurrence="assembly:1/part:1", selector="face:1"):
    return GeometryReference(
        GeometryReferenceId.new(), "hms_persistent_geometry", 1,
        source_id or uuid4(), GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"occurrence": occurrence, "selector": selector}),
        Revision(2), occurrence_path=occurrence, subshape_selector=selector,
    )


def _tool_reference():
    return ToolAssemblyReference(
        ToolAssemblyId.new(), Revision(4),
        ContentFingerprint.from_payload({"assembly": "T1"}), LengthUnit.MM,
    )


def _operation(setup_id, *, node_id=None, operation_id=None, reference=None, enabled=True):
    geometry = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY,
        reference or _reference(), True, GeometryReferenceKind.FACE, 0,
    )
    return Operation(
        operation_id or OperationId.new(), node_id or CamNodeId.new(),
        OperationFamily.MILLING, setup_id, _tool_reference(), (geometry,),
        OperationParameterSet("mill.contour", 1, (("stepdown", 2.5), ("climb", True))),
        enabled=enabled,
    )


def _setup(setup_id=None):
    setup_id = setup_id or SetupId.new()
    source_id = uuid4()
    frame = WcsFrame.identity(LengthUnit.MM)
    return Setup(
        setup_id, "Setup", SetupKind.MILL, frame, WorkOffset("PRIMARY", 1),
        BoxStock(Length(10, LengthUnit.MM), Length(10, LengthUnit.MM), Length(5, LengthUnit.MM), frame),
        _reference(source_id), SourceScope(source_id),
    )


def _tree_with_three():
    setup_id = SetupId.new()
    tree = OperationTree.empty(setup_id)
    group = CamNodeId.new()
    tree = tree.add_group(tree.root_id, group, "Roughing")
    first = _operation(setup_id)
    second = _operation(setup_id)
    unrelated = _operation(setup_id)
    tree = tree.add_operation(group, "First", first)
    tree = tree.add_operation(group, "Second", second)
    tree = tree.add_operation(tree.root_id, "Unrelated", unrelated)
    return tree, group, first, second, unrelated


def test_valid_root_group_operation_and_immutable_collections():
    tree, group, first, _, _ = _tree_with_three()

    assert tree.root.kind is CamNodeKind.GROUP
    assert tree.get_node(group).child_ids[0] == first.node_id
    assert tree.get_node(first.node_id).child_ids == ()
    assert isinstance(tree.nodes, tuple) and isinstance(tree.operations, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tree.root.name = "Bypass"


def test_duplicate_id_and_operation_children_are_rejected_atomically():
    tree = OperationTree.empty(SetupId.new())
    before = tree.to_dict()
    with pytest.raises(DuplicateCamIdError):
        tree.add_group(tree.root_id, tree.root_id, "Duplicate")
    with pytest.raises(CamInvariantError):
        CamNode(CamNodeId.new(), CamNodeKind.OPERATION, "Bad", child_ids=(CamNodeId.new(),), operation_id=OperationId.new())
    assert tree.to_dict() == before


def test_move_cycle_rejected_and_reorder_is_deterministic():
    setup_id = SetupId.new()
    tree = OperationTree.empty(setup_id)
    first, child, second = CamNodeId.new(), CamNodeId.new(), CamNodeId.new()
    tree = tree.add_group(tree.root_id, first, "First")
    tree = tree.add_group(first, child, "Child")
    tree = tree.add_group(tree.root_id, second, "Second")
    before = tree.to_dict()
    with pytest.raises(CamInvariantError):
        tree.move_node(first, child)
    assert tree.to_dict() == before
    moved = tree.reorder_node(second, 0)
    assert moved.root.child_ids == (second, first)
    assert OperationTree.from_dict(moved.to_dict()).root.child_ids == (second, first)


def test_remove_group_is_recursive_and_cleans_dependency_graph():
    tree, group, first, second, unrelated = _tree_with_three()
    tree = tree.with_dependency_added(DependencyEdge.operation_output(first.operation_id, second.operation_id))

    removed = tree.remove_node(group)

    assert tuple(item.operation_id for item in removed.operations) == (unrelated.operation_id,)
    assert removed.dependency_graph.edges == ()
    assert removed.root.child_ids == (unrelated.node_id,)


def test_operation_strategy_inputs_and_round_trip_preserve_order_and_occurrences():
    setup_id = SetupId.new()
    reference = _reference()
    first = OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference)
    second = OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference)
    operation = dataclasses.replace(_operation(setup_id), geometry_inputs=(first, second))

    restored = Operation.from_dict(operation.to_dict())

    assert restored == operation
    assert restored.family is OperationFamily.MILLING
    assert tuple(item.input_id for item in restored.geometry_inputs) == (first.input_id, second.input_id)
    assert first.input_id != second.input_id and first.reference == second.reference
    assert restored.tool_assembly.expected_fingerprint == operation.tool_assembly.expected_fingerprint


def test_operation_machine_expected_snapshot_round_trip():
    requirement = MachineRequirement(
        MachineDefinitionId.new(), Revision(7),
        ContentFingerprint.from_payload({"machine": "VMC"}), LengthUnit.MM,
        (OperationCapability.MILLING,),
    )
    operation = dataclasses.replace(_operation(SetupId.new()), machine_requirement=requirement)

    restored = Operation.from_dict(operation.to_dict())

    assert restored.machine_requirement == requirement


@pytest.mark.parametrize("value", (float("nan"), float("inf"), object(), {"native": object()}))
def test_malformed_parameter_values_are_rejected(value):
    with pytest.raises(CamValidationError):
        OperationParameterSet("mill.contour", 1, (("depth", value),))


def test_parameter_normalization_fingerprint_and_future_versions():
    first = OperationParameterSet("mill.contour", 1, (("z", 1), ("a", True)))
    second = OperationParameterSet("mill.contour", 1, (("a", True), ("z", 1)))
    assert first.values == (("a", True), ("z", 1))
    assert first.fingerprint == second.fingerprint
    with pytest.raises(UnsupportedCamSchemaError):
        OperationParameterSet("mill.contour", 2)
    payload = first.to_dict()
    payload["schema_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        OperationParameterSet.from_dict(payload)


def test_geometry_resolution_validation_distinguishes_stale_and_missing():
    operation = _operation(SetupId.new())
    reference_id = operation.geometry_inputs[0].reference.reference_id
    stale = validate_operation(operation, geometry_results=(GeometryResolutionResult(reference_id, GeometryResolutionStatus.STALE),))
    missing = validate_operation(operation)
    assert stale[0].code is DiagnosticCode.GEOMETRY_STALE
    assert missing[0].code is DiagnosticCode.GEOMETRY_UNRESOLVED
    assert ValidationDiagnostic.from_dict(stale[0].to_dict()) == stale[0]


def test_valid_dag_cycle_rejection_and_deterministic_topology():
    a, b, c = OperationId.new(), OperationId.new(), OperationId.new()
    graph = DependencyGraph((c, a, b))
    graph = graph.with_edge_added(DependencyEdge.operation_output(a, b))
    graph = graph.with_edge_added(DependencyEdge.operation_output(b, c))
    assert graph.topological_order == (a, b, c)
    before = graph.to_dict()
    with pytest.raises(CamInvariantError):
        graph.with_edge_added(DependencyEdge.operation_output(c, a))
    assert graph.to_dict() == before
    assert DependencyGraph.from_dict(graph.to_dict()) == graph


def test_dag_rejects_duplicate_and_missing_operation_edges():
    a, b = OperationId.new(), OperationId.new()
    graph = DependencyGraph((a, b), (DependencyEdge.operation_output(a, b),))
    with pytest.raises(DuplicateCamIdError):
        graph.with_edge_added(DependencyEdge.operation_output(a, b))
    with pytest.raises(CamInvariantError):
        DependencyGraph((a,), (DependencyEdge.operation_output(a, b),))


def test_dirty_propagates_downstream_only_and_disabled_remains_dirty():
    tree, _, first, second, unrelated = _tree_with_three()
    source = "geometry:main"
    tree = tree.with_dependency_added(DependencyEdge(DependencyKind.GEOMETRY, source, first.operation_id))
    tree = tree.with_dependency_added(DependencyEdge.operation_output(first.operation_id, second.operation_id))
    tree = tree.set_enabled(second.node_id, False)

    changed = tree.mark_dependency_changed(DependencyKind.GEOMETRY, source)

    assert changed.get_operation(first.operation_id).artifact_state.status is ArtifactStatus.DIRTY
    downstream = changed.get_operation(second.operation_id)
    assert downstream.artifact_state.status is ArtifactStatus.DIRTY and not downstream.enabled
    assert DirtyReason.UPSTREAM_CHANGED in downstream.artifact_state.dirty_reasons
    assert changed.get_operation(unrelated.operation_id) == tree.get_operation(unrelated.operation_id)


def test_artifact_valid_transitions_failed_recovery_and_invalid_transition():
    fingerprint = DependencyFingerprint.from_payload({"input": 1})
    state, token = ArtifactState().begin(fingerprint)
    failed, accepted = state.fail(token)
    assert accepted and failed.status is ArtifactStatus.FAILED
    retry, retry_token = failed.begin(fingerprint)
    valid, accepted = retry.publish(retry_token, fingerprint, ContentFingerprint.from_payload({"artifact": 1}))
    assert accepted and valid.status is ArtifactStatus.VALID and valid.dirty_reasons == ()
    dirty = valid.transition(ArtifactStatus.DIRTY)
    assert dirty.status is ArtifactStatus.DIRTY
    with pytest.raises(CamInvariantError):
        ArtifactState().transition(ArtifactStatus.VALID)


def test_stale_token_and_changed_input_cannot_publish():
    first = DependencyFingerprint.from_payload({"input": 1})
    second = DependencyFingerprint.from_payload({"input": 2})
    state, token = ArtifactState().begin(first)
    stale_state, accepted = state.publish(dataclasses.replace(token, generation=token.generation + 1), first, ContentFingerprint.from_payload({"result": 1}))
    assert not accepted and stale_state == state
    dirty, accepted = state.publish(token, second, ContentFingerprint.from_payload({"result": 2}))
    assert not accepted and dirty.status is ArtifactStatus.DIRTY


def test_artifact_computing_state_codec_keeps_token_and_generation():
    fingerprint = DependencyFingerprint.from_payload({"input": "codec"})
    computing, token = ArtifactState().begin(fingerprint)
    restored = ArtifactState.from_dict(computing.to_dict())
    assert restored == computing
    assert restored.token == token and restored.generation == 1


def test_dirty_reasons_and_input_snapshot_fingerprint_are_canonical():
    state = ArtifactState().mark_dirty(DirtyReason.WCS_CHANGED).mark_dirty(DirtyReason.GEOMETRY_CHANGED)
    assert state.dirty_reasons == tuple(sorted(set(state.dirty_reasons), key=lambda item: item.value))
    parameter = ContentFingerprint.from_payload({"parameter": 1})
    a = ContentFingerprint.from_payload({"a": 1})
    b = ContentFingerprint.from_payload({"b": 1})
    first = OperationInputSnapshot("mill.contour", 1, parameter, (("b", b), ("a", a)))
    second = OperationInputSnapshot("mill.contour", 1, parameter, (("a", a), ("b", b)))
    assert first.fingerprint == second.fingerprint


def test_tree_codec_round_trip_and_future_nested_version_rejected():
    tree, *_ = _tree_with_three()
    payload = tree.to_dict()
    assert OperationTree.from_dict(payload) == tree
    assert json.dumps(payload, sort_keys=True, allow_nan=False)
    payload["operations"][0]["parameters"]["format_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        OperationTree.from_dict(payload)


def test_setup_v1_compatibility_empty_tree_and_revision_policy():
    setup = _setup()
    v1 = setup.to_dict()
    v1["format_version"] = 1
    v1.pop("operation_tree")
    v1.pop("revision")
    restored = Setup.from_dict(v1)
    assert restored.operation_tree.is_empty
    assert restored.revision == Revision(0)

    job = CamJob(CamJobId.new(), "Job", setups=(restored,), active_setup_id=restored.setup_id)
    changed_tree = restored.operation_tree.add_group(restored.operation_tree.root_id, CamNodeId.new(), "Group")
    job.update_operation_tree(restored.setup_id, changed_tree)
    assert job.revision == Revision(1)
    assert job.get_setup(restored.setup_id).revision == Revision(1)


def test_malformed_tree_does_not_partially_change_job_and_public_graph_is_native_free():
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    before = job.to_dict()
    payload = setup.operation_tree.to_dict()
    payload["root_id"] = str(CamNodeId.new())
    with pytest.raises(CamInvariantError):
        job.update_operation_tree(setup.setup_id, OperationTree.from_dict(payload))
    assert job.to_dict() == before
    assert all(not type(value).__module__.startswith(("OCP", "PySide6")) for value in (
        setup.operation_tree, setup.operation_tree.nodes, setup.operation_tree.operations,
        setup.operation_tree.dependency_graph,
    ))


def test_diagnostic_codec_future_version_is_rejected():
    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR, DiagnosticCode.UPSTREAM_INVALID, "Bad upstream")
    payload = diagnostic.to_dict()
    payload["format_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        ValidationDiagnostic.from_dict(payload)
