"""Stage 7D.3.1 explicit-order program-assembly regression tests."""

from dataclasses import replace
from pathlib import Path

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamJobId,
    CamNodeId,
    ContentFingerprint,
    DependencyFingerprint,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    Operation,
    OperationId,
    OperationParameterSet,
    Point3,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.post import (
    CutterCompensationPolicy,
    ProgramAssemblyContext,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyOperationInput,
    ProgramAssemblyRequest,
    ProgramAssemblyService,
    ProgramAssemblyStatus,
    SimulationGateMode,
    SimulationGatePolicy,
    robodrill_21i_definition,
    NCArtifactStore,
    NCAssemblyExportRequest,
    NCAssemblyExportSourceSnapshot,
    NCExportService,
)
from hms_cadcam.cam.post.assembly_codec import dumps, loads
from hms_cadcam.cam.post.profile import ControllerToolBinding
from hms_cadcam.cam.toolpath import FeedMode, Pose, SpindleState, ToolpathBuilder
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source


def _source_variant(source, strategy: str):
    operation_id = OperationId.new()
    operation = Operation(
        operation_id,
        CamNodeId.new(),
        source.operation.family,
        source.setup.setup_id,
        source.operation.tool_assembly,
        (),
        OperationParameterSet(strategy, 1),
    )
    input_fingerprint = DependencyFingerprint.from_payload(
        {"assembly-test": str(operation_id)}
    )
    computing, token = operation.artifact_state.begin(input_fingerprint)
    operation = replace(operation, artifact_state=computing)
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=operation_id,
        operation_revision=operation.revision,
        computation_token=token,
        input_fingerprint=input_fingerprint,
        unit=LengthUnit.MM,
        setup_id=source.setup.setup_id,
        setup_revision=source.setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(source.setup.wcs.to_dict()),
        tool_assembly_id=source.assembly.assembly_id,
        tool_assembly_fingerprint=source.assembly.content_fingerprint,
        machine_id=source.machine.machine_id,
        machine_fingerprint=source.machine.content_fingerprint,
    )
    pose = lambda x, y, z: Pose(
        Point3(x, y, z, LengthUnit.MM), Vector3(0.0, 0.0, 1.0)
    )
    builder.set_initial_pose(pose(0.0, 0.0, 10.0))
    builder.set_feed_mode(FeedMode.UNITS_PER_MINUTE)
    builder.set_spindle(SpindleState.CLOCKWISE, source.machine.spindles[0].maximum_speed)
    builder.rapid_to(pose(10.0, 0.0, 10.0), rapid_rate=FeedRate(1000.0, FeedUnit.MM_PER_MINUTE))
    builder.linear_to(pose(10.0, 0.0, 0.0), feed_rate=FeedRate(100.0, FeedUnit.MM_PER_MINUTE))
    builder.rapid_to(pose(10.0, 0.0, 10.0), rapid_rate=FeedRate(1000.0, FeedUnit.MM_PER_MINUTE))
    builder.set_spindle(SpindleState.OFF)
    artifact = builder.finalize()
    state, accepted = computing.publish(
        token, input_fingerprint, artifact.artifact_fingerprint, enabled=True
    )
    assert accepted
    return replace(
        source,
        operation=replace(operation, artifact_state=state),
        artifact=artifact,
    )


def _request(sources, *, order=None, strategy_context=None):
    definition = robodrill_21i_definition()
    order = order or list(range(len(sources)))
    items = []
    for index, source in zip(order, sources):
        context = fixture_context(
            source,
            file_name="ASSEMBLY.fn",
            cutter=False,
        )
        items.append(
            ProgramAssemblyOperationInput(
                operation_id=source.operation.operation_id,
                order_index=index,
                artifact_id=source.artifact.artifact_id,
                artifact_fingerprint=source.artifact.artifact_fingerprint,
                tool_assembly_fingerprint=source.assembly.content_fingerprint,
                tool_binding=context.tool_binding,
                source_snapshot=source,
                simulation_result=None,
                program_context=context,
                cutter_compensation_policy=CutterCompensationPolicy.DISABLED,
                display_metadata=(("name", source.operation.strategy_key),),
            )
        )
    first = sources[0]
    return ProgramAssemblyRequest(
        project_id=first.project_id,
        project_generation=1,
        job_id=CamJobId.new(),
        setup_id=first.setup.setup_id,
        machine_id=first.machine.machine_id,
        machine_fingerprint=first.machine.content_fingerprint,
        post_definition=definition,
        shared_context=ProgramAssemblyContext(
            "ASSEMBLY.fn", (("customer", "HMS"),)
        ),
        operations=tuple(items),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )


def test_two_operations_have_one_header_footer_and_explicit_boundaries():
    first = _runtime_source()
    second = _source_variant(first, "pocket_2_5d")
    request = _request([first, second])

    execution = ProgramAssemblyService().assemble(request)

    assert execution.accepted
    assert execution.result is not None
    text = execution.result.canonical_text
    assert text.splitlines().count("%") == 2
    assert text.count("\r\n%\r\n") == 1
    assert text.count("(SHL-TECH)") == 1
    assert text.count("M30") == 1
    assert text.count("M06T1") == 2
    assert text.index(f"(OPERATION={first.operation.operation_id},SECTION=0)") < text.index(
        f"(OPERATION={second.operation.operation_id},SECTION=1)"
    )
    assert text.splitlines()[-3:] == ["G28Y0.", "M30", "%"]


def test_reordering_changes_checksum_but_does_not_auto_group_tools():
    first = _runtime_source()
    second = _source_variant(first, "contour_2d")
    first_request = _request([first, second])
    reordered = _request([second, first])
    service = ProgramAssemblyService()

    first_result = service.assemble(first_request).result
    second_result = service.assemble(reordered).result

    assert first_result is not None and second_result is not None
    assert first_result.output_checksum != second_result.output_checksum
    assert first_result.canonical_text.count("M06T1") == 2
    assert tuple(
        section.order_index for section in first_result.plan.sections
    ) == (0, 1)


def test_empty_and_duplicate_order_fail_closed():
    source = _runtime_source()
    empty = _request([source])
    empty = replace(empty, operations=())
    duplicate = _request([source, _source_variant(source, "pocket_2_5d")])
    duplicate = replace(
        duplicate,
        operations=(duplicate.operations[0], replace(duplicate.operations[1], operation_id=duplicate.operations[0].operation_id)),
    )

    service = ProgramAssemblyService()
    empty_execution = service.assemble(empty)
    duplicate_execution = service.assemble(duplicate)

    assert not empty_execution.accepted
    assert any(item.code is ProgramAssemblyDiagnosticCode.EMPTY for item in empty_execution.diagnostics)
    assert not duplicate_execution.accepted
    assert any(item.code is ProgramAssemblyDiagnosticCode.DUPLICATE_OPERATION for item in duplicate_execution.diagnostics)


def test_tapping_rejects_whole_assembly():
    source = _runtime_source()
    tapping = replace(
        source,
        operation=replace(
            source.operation,
            parameters=OperationParameterSet("tapping_v1", 1),
        ),
    )
    request = _request([tapping])

    execution = ProgramAssemblyService().assemble(request)

    assert not execution.accepted
    assert execution.status is ProgramAssemblyStatus.BLOCKED
    assert any(
        item.code is ProgramAssemblyDiagnosticCode.UNSUPPORTED_TAPPING
        for item in execution.diagnostics
    )


def test_request_and_result_codec_round_trip_without_runtime_identity_changes():
    source = _runtime_source()
    request = _request([source])
    execution = ProgramAssemblyService().assemble(request)
    assert execution.result is not None

    restored_request = loads(dumps(request))
    restored_result = loads(dumps(execution.result))

    assert restored_request.input_fingerprint == request.input_fingerprint
    assert restored_result.result_fingerprint == execution.result.result_fingerprint
    assert restored_request.request_id == request.request_id


def test_stale_callback_cannot_publish_newer_request():
    source = _runtime_source()
    request = _request([source])
    service = ProgramAssemblyService()
    changed = replace(request, project_generation=2)

    execution = service.assemble(request, current_request=lambda: changed)

    assert not execution.accepted
    assert execution.status is ProgramAssemblyStatus.STALE


def test_managed_assembly_export_round_trips_ordered_provenance(tmp_path: Path):
    source = _runtime_source()
    request = _request([source])
    assembly = ProgramAssemblyService().assemble(request)
    assert assembly.result is not None
    export_request = NCAssemblyExportRequest(
        source.project_id, assembly.result.result_id, "ASSEMBLY.fn"
    )
    snapshot = NCAssemblyExportSourceSnapshot(1, request, assembly.result)

    execution = NCExportService().export_assembly(tmp_path, export_request, snapshot)

    assert execution.accepted
    assert execution.artifact is not None
    assert execution.artifact.assembly_result_id == assembly.result.result_id
    assert execution.artifact.assembly_operation_ids == (source.operation.operation_id,)
    assert (tmp_path / "nc" / "ASSEMBLY.fn").read_bytes() == assembly.result.canonical_text.encode()
    loaded = NCArtifactStore().load(tmp_path, source.project_id)
    assert loaded.entries[0].assembly_section_ids == (
        assembly.result.plan.sections[0].section_id,
    )
