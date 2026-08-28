"""R277 fail-closed Rest Post/export compatibility over delivered artifacts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import pytest

import test_rest_finishing_core_r273 as finishing_core
from test_rest_contour_core_r271 import _positive_inputs
from test_rest_contour_foundation_r270 import _inputs as _r270_inputs
from test_rest_finishing_toolpath_r273 import _context as _finishing_context
from test_program_assembly import _source_variant

from hms_cadcam.cam.application.rest_contour_geometry import plan_rest_contour_residual
from hms_cadcam.cam.application.rest_contour_toolpath import (
    RestContourPhaseBExecutionContext,
    generate_rest_contour_phase_b,
    prepare_rest_contour_phase_b,
)
from hms_cadcam.cam.application.rest_finishing_lifecycle import (
    RestFinishingLifecycleStatus,
    generate_rest_finishing_3axis,
    prepare_rest_finishing_3axis,
)
from hms_cadcam.cam.domain import (
    CamInvariantError,
    CamJobId,
    ContentFingerprint,
    HolderDefinition,
    HolderSection,
    Length,
    LengthUnit,
    OperationParameterSet,
    SpindleDirection,
    WorkOffset,
)
from hms_cadcam.cam.post import (
    ControllerToolBinding,
    CutterCompensationPolicy,
    FanucRobodrill21iAdapter,
    NCAssemblyExportRequest,
    NCAssemblyExportSourceSnapshot,
    NCExportRequest,
    NCExportService,
    NCExportSourceSnapshot,
    PostProcessorCapabilities,
    PostRequest,
    PostRuntimeService,
    ProgramAssemblyContext,
    ProgramAssemblyOperationInput,
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyService,
    SimulationGateMode,
    SimulationGatePolicy,
    build_assembly_input_fingerprint,
    robodrill_21i_definition,
    robodrill_21i_definition_v2,
    robodrill_21i_profile,
    robodrill_21i_profile_v2,
    validate_assembly_plan,
    validate_assembly_request,
)
from hms_cadcam.cam.post.fanuc_validation import validate_fanuc_program
from hms_cadcam.cam.post.lowering import PostSourceSnapshot, lower_toolpath
from hms_cadcam.cam.post.model import (
    LinearMotionRecord,
    RapidMotionRecord,
    ToolActivationRecord,
)
from hms_cadcam.cam.post.profile import ProductionProgramContext
from hms_cadcam.cam.toolpath import (
    LinearMove,
    RapidMove,
    ToolContextEvent,
    publish_toolpath,
)


GOLDEN_DIR = (
    Path(__file__).parents[1]
    / "golden"
    / "post"
    / "robodrill_fanuc_21i_rest_v2"
)


def _fanuc_machine(machine):
    mapping = {"axis_x": "X", "axis_y": "Y", "axis_z": "Z"}
    axes = tuple(replace(axis, name=mapping.get(axis.name, axis.name)) for axis in machine.axes)
    chain = replace(
        machine.kinematic_chain,
        nodes=tuple(
            replace(node, axis_name=mapping.get(node.axis_name, node.axis_name))
            for node in machine.kinematic_chain.nodes
        ),
    )
    return replace(
        machine,
        axes=axes,
        kinematic_chain=chain,
        spindles=tuple(
            replace(spindle, directions=(SpindleDirection.CLOCKWISE,))
            for spindle in machine.spindles
        ),
        manufacturer="FANUC",
        model="ROBODRILL",
    )


def _holder(assembly) -> HolderDefinition:
    unit = assembly.unit
    holder = HolderDefinition(
        assembly.holder_id,
        "Holder cơ bản",
        unit,
        (
            HolderSection(
                Length(0, unit),
                Length(40, unit),
                Length(30, unit),
                Length(40, unit),
            ),
        ),
        Length(0, unit),
        interface="generic_taper",
    )
    assert holder.content_fingerprint == assembly.expected_holder_fingerprint
    return holder


@lru_cache(maxsize=1)
def _shared_rest_sources() -> tuple[PostSourceSnapshot, PostSourceSnapshot]:
    base = _r270_inputs()
    machine = _fanuc_machine(base.machine)
    requirement = replace(
        base.machine_requirement,
        expected_revision=machine.revision,
        expected_fingerprint=machine.content_fingerprint,
    )
    base = replace(
        base,
        setup=replace(base.setup, work_offset=WorkOffset("PRIMARY", 1)),
        machine=machine,
        machine_requirement=requirement,
    )
    contour_inputs = _positive_inputs(base_inputs=base)

    original = finishing_core._r271_positive_inputs
    finishing_core._r271_positive_inputs = lambda: contour_inputs
    try:
        finishing_inputs = finishing_core._inputs(
            consumer_machine_mutator=_fanuc_machine,
        )
    finally:
        finishing_core._r271_positive_inputs = original

    contour_candidate = finishing_inputs.material_candidates[0]
    contour_operation = finishing_inputs.setup.operation_tree.get_operation(
        contour_candidate.producer_operation_id
    )
    project_id = UUID("27700000-0000-4000-8000-000000000001")
    contour_source = PostSourceSnapshot(
        project_id,
        contour_operation,
        contour_candidate.producer_artifact,
        replace(finishing_inputs.setup, work_offset=WorkOffset("PRIMARY", 1)),
        contour_inputs.assembly,
        contour_inputs.tool,
        _holder(contour_inputs.assembly),
        contour_inputs.machine,
    )

    preparation = prepare_rest_finishing_3axis(_finishing_context(finishing_inputs))
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.SUCCESS
    assert result.candidate is not None
    candidate = result.candidate
    publication = publish_toolpath(
        candidate.prepared.computing_operation,
        candidate.artifact,
        candidate.prepared.computation_token,
        candidate.prepared.input_fingerprint,
    )
    assert publication.accepted and publication.operation is not None
    finishing_source = PostSourceSnapshot(
        project_id,
        publication.operation,
        candidate.artifact,
        replace(finishing_inputs.setup, work_offset=WorkOffset("PRIMARY", 1)),
        finishing_inputs.assembly,
        finishing_inputs.tool,
        _holder(finishing_inputs.assembly),
        finishing_inputs.machine,
    )
    assert contour_source.setup.setup_id == finishing_source.setup.setup_id
    assert contour_source.machine == finishing_source.machine
    return contour_source, finishing_source


def _context(source: PostSourceSnapshot, file_name: str, station: int) -> ProductionProgramContext:
    geometry = source.tool.cutting_geometry
    return ProductionProgramContext(
        file_name,
        Length(100, LengthUnit.MM),
        ControllerToolBinding(
            source.assembly.content_fingerprint,
            station,
            station,
            None,
            source.operation.strategy_key.replace("_", " "),
        ),
        Length(geometry.diameter.value / 2.0, LengthUnit.MM),
        Length(0, LengthUnit.MM),
        Length(-18, LengthUnit.MM),
        False,
    )


def _post_request(source: PostSourceSnapshot, file_name: str, station: int) -> PostRequest:
    return PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        robodrill_21i_definition_v2(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
        program_context=_context(source, file_name, station),
    )


def test_v1_identity_is_frozen_and_v2_is_explicitly_distinct() -> None:
    v1_profile = robodrill_21i_profile()
    v1_definition = robodrill_21i_definition()
    v2_profile = robodrill_21i_profile_v2()
    v2_definition = robodrill_21i_definition_v2()

    assert v1_profile.fingerprint.digest == "d116afec9e86fef5d156358f7e6580a35b51ad18e1d9a4dac308f283cadcdc2a"
    assert v1_definition.fingerprint.digest == "e341e777c2693567a3cfd024fb04c071b4dfa7ebcaeda2a0b5a3d9908b9d17a4"
    assert v2_profile.fingerprint != v1_profile.fingerprint
    assert v2_definition.fingerprint != v1_definition.fingerprint
    assert not {"rest_contour_3axis", "rest_finishing_3axis"}.intersection(
        v1_definition.capabilities.supported_operation_strategies
    )
    assert {"rest_contour_3axis", "rest_finishing_3axis"}.issubset(
        v2_definition.capabilities.supported_operation_strategies
    )
    assert PostProcessorCapabilities().supported_operation_strategies == (
        "boring_v1",
        "contour_2d",
        "drilling_v1",
        "facing_2_5d",
        "pocket_2_5d",
        "reaming_v1",
        "tapping_v1",
    )
    assert v2_profile.cutter_compensation_policy is CutterCompensationPolicy.DISABLED


@pytest.mark.parametrize(
    ("source_index", "file_name", "station"),
    ((0, "rest_contour_3axis.fn", 1), (1, "rest_finishing_3axis.fn", 2)),
)
def test_delivered_rest_artifacts_lower_one_for_one_and_match_v2_golden(
    source_index: int,
    file_name: str,
    station: int,
) -> None:
    source = _shared_rest_sources()[source_index]
    request = _post_request(source, file_name, station)
    first = lower_toolpath(request, source)
    second = lower_toolpath(request, source)
    adapter = FanucRobodrill21iAdapter(request.post_definition)

    assert first.program_fingerprint == second.program_fingerprint
    assert first.artifact_fingerprint == source.artifact.artifact_fingerprint
    assert adapter.validate_program_ir(first) == ()
    source_motions = tuple(
        event for event in source.artifact.events if isinstance(event, (RapidMove, LinearMove))
    )
    ir_motions = tuple(
        record for record in first.records if isinstance(record, (RapidMotionRecord, LinearMotionRecord))
    )
    assert len(source_motions) == len(ir_motions)
    assert tuple(type(event).__name__ for event in source_motions) == tuple(
        "RapidMove" if isinstance(record, RapidMotionRecord) else "LinearMove"
        for record in ir_motions
    )
    assert len(tuple(event for event in source.artifact.events if isinstance(event, ToolContextEvent))) == 1
    assert len(tuple(record for record in first.records if isinstance(record, ToolActivationRecord))) == 1

    text = adapter.format_program(first, request.post_definition)
    assert adapter.validate_output(text, first, request.post_definition) == ()
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert text.encode("utf-8") == (GOLDEN_DIR / file_name).read_bytes()
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == manifest[file_name]


@pytest.mark.parametrize("source_index", (0, 1))
def test_v1_rejects_rest_while_exact_v2_runtime_is_deterministic(source_index: int) -> None:
    source = _shared_rest_sources()[source_index]
    file_name = f"{source.operation.strategy_key}.fn"
    v2_request = _post_request(source, file_name, source_index + 1)
    first = PostRuntimeService().post(v2_request, source)
    second = PostRuntimeService().post(v2_request, source)
    assert first.accepted and second.accepted
    assert first.result is not None and second.result is not None
    assert first.result.program_ir_fingerprint == second.result.program_ir_fingerprint
    assert first.result.result_fingerprint == second.result.result_fingerprint
    assert first.result.output_checksum == second.result.output_checksum
    assert first.result.canonical_text == second.result.canonical_text
    assert first.result.artifact_fingerprint == source.artifact.artifact_fingerprint

    v1_request = replace(v2_request, post_definition=robodrill_21i_definition())
    rejected = PostRuntimeService().post(v1_request, source)
    assert not rejected.accepted
    assert rejected.result is None


def test_v2_contract_and_rest_compensation_fail_closed() -> None:
    source = _shared_rest_sources()[0]
    request = _post_request(source, "rest_contour_3axis.fn", 1)
    adapter = FanucRobodrill21iAdapter(request.post_definition)
    altered_version = replace(request.post_definition, definition_version=1)
    assert any(
        item.message_key == "post.fanuc.definition_mismatch"
        for item in FanucRobodrill21iAdapter(altered_version).validate_request(
            replace(request, post_definition=altered_version)
        )
    )
    altered_profile = replace(
        request.post_definition.production_profile,
        controller_family="FOREIGN",
    )
    altered_definition = replace(request.post_definition, production_profile=altered_profile)
    assert any(
        item.message_key == "post.fanuc.definition_mismatch"
        for item in FanucRobodrill21iAdapter(altered_definition).validate_request(
            replace(request, post_definition=altered_definition)
        )
    )

    compensated_context = replace(request.program_context, use_legacy_cutter_compensation=True)
    compensated = replace(request, program_context=compensated_context)
    execution = PostRuntimeService().post(compensated, source)
    assert not execution.accepted
    assert any("compensation" in item.message_key for item in execution.diagnostics)
    assert adapter.capabilities() == request.post_definition.capabilities


def test_unknown_strategy_foreign_controller_and_mixed_contracts_fail_closed() -> None:
    source = _shared_rest_sources()[0]
    request = _post_request(source, "rest_contour_3axis.fn", 1)

    unknown = replace(
        source,
        operation=replace(
            source.operation,
            parameters=OperationParameterSet("unknown_rest_strategy", 1),
        ),
    )
    unknown_execution = PostRuntimeService().post(
        replace(request, operation_id=unknown.operation.operation_id),
        unknown,
    )
    assert not unknown_execution.accepted

    foreign_machine = replace(source.machine, manufacturer="FOREIGN")
    foreign = replace(source, machine=foreign_machine)
    foreign_execution = PostRuntimeService().post(request, foreign)
    assert not foreign_execution.accepted

    wrong_definition = replace(request.post_definition, numeric_precision=14)
    wrong_execution = PostRuntimeService().post(
        replace(request, post_definition=wrong_definition),
        source,
    )
    assert not wrong_execution.accepted
    assert any(
        item.message_key == "post.fanuc.definition_mismatch"
        for item in wrong_execution.diagnostics
    )

    with pytest.raises(CamInvariantError):
        replace(
            request.post_definition,
            production_profile=robodrill_21i_profile(),
        )


def _assembly_request(sources: tuple[PostSourceSnapshot, ...]) -> ProgramAssemblyRequest:
    definition = robodrill_21i_definition_v2()
    items = []
    for index, source in enumerate(sources):
        context = _context(source, "REST_ASSEMBLY.fn", index + 1)
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
        shared_context=ProgramAssemblyContext("REST_ASSEMBLY.fn"),
        operations=tuple(items),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )


def _published_mixed_assembly() -> tuple[
    ProgramAssemblyRequest, ProgramAssemblyResult
]:
    contour, finishing = _shared_rest_sources()
    facing = _source_variant(contour, "facing_2_5d")
    request = _assembly_request((contour, finishing, facing))
    execution = ProgramAssemblyService().assemble(request)
    assert execution.accepted and execution.result is not None
    return request, execution.result


def _reseal_assembly_result(
    result: ProgramAssemblyResult, *, plan
) -> ProgramAssemblyResult:
    return replace(result, plan=plan, result_fingerprint=None)


def _assert_assembly_export_rejected(
    root: Path,
    request: ProgramAssemblyRequest,
    result: ProgramAssemblyResult,
) -> None:
    root.mkdir()
    export = NCExportService().export_assembly(
        root,
        NCAssemblyExportRequest(
            result.project_id, result.result_id, "REST_ASSEMBLY.fn"
        ),
        NCAssemblyExportSourceSnapshot(1, request, result),
    )
    assert not export.accepted
    assert export.artifact is None
    assert not (root / "nc").exists()


def test_v2_program_assembly_supports_rest_and_existing_operations_in_order() -> None:
    contour, finishing = _shared_rest_sources()
    facing = _source_variant(contour, "facing_2_5d")
    request = _assembly_request((contour, finishing, facing))
    execution = ProgramAssemblyService().assemble(request)
    assert execution.accepted and execution.result is not None
    assert tuple(section.program_ir.strategy_key for section in execution.result.plan.sections) == (
        "rest_contour_3axis",
        "rest_finishing_3axis",
        "facing_2_5d",
    )
    assert tuple(section.order_index for section in execution.result.plan.sections) == (0, 1, 2)

    compensated_item = replace(
        request.operations[0],
        cutter_compensation_policy=CutterCompensationPolicy.LEGACY_WORKNC_LEFT,
        program_context=replace(
            request.operations[0].program_context,
            use_legacy_cutter_compensation=True,
        ),
    )
    rejected = ProgramAssemblyService().assemble(
        replace(request, request_id=None, operations=(compensated_item, *request.operations[1:]))
    )
    assert not rejected.accepted
    assert any("compensation" in item.message_key for item in rejected.diagnostics)


def test_resealed_assembly_contract_forgery_fails_every_gate(tmp_path: Path) -> None:
    request, result = _published_mixed_assembly()
    definition = request.post_definition
    profile = definition.production_profile
    assert profile is not None
    plan = result.plan
    bad_fingerprint = ContentFingerprint("sha256", 1, "0" * 64)
    forged_plans = (
        replace(plan, production_profile_version=1, plan_fingerprint=None),
        replace(
            plan,
            production_profile_id=type(plan.production_profile_id)(
                UUID("27700000-0000-4000-8000-000000000101")
            ),
            plan_fingerprint=None,
        ),
        replace(
            plan,
            production_profile_fingerprint=bad_fingerprint,
            plan_fingerprint=None,
        ),
        replace(
            plan,
            post_definition_id=type(plan.post_definition_id)(
                UUID("27700000-0000-4000-8000-000000000102")
            ),
            plan_fingerprint=None,
        ),
        replace(
            plan,
            post_definition_fingerprint=bad_fingerprint,
            plan_fingerprint=None,
        ),
    )
    adapter = FanucRobodrill21iAdapter(definition)
    for index, forged_plan in enumerate(forged_plans):
        forged_result = _reseal_assembly_result(result, plan=forged_plan)
        assert validate_assembly_plan(forged_plan, definition)
        with pytest.raises(ValueError, match="assembly.profile_mismatch"):
            adapter.format_assembly(forged_plan, definition)
        _assert_assembly_export_rejected(
            tmp_path / f"forged-plan-{index}.HMS", request, forged_result
        )

    wrong_definition_version = replace(definition, definition_version=1)
    assert validate_assembly_plan(plan, wrong_definition_version)
    with pytest.raises(ValueError, match="post.fanuc.definition_mismatch"):
        FanucRobodrill21iAdapter(wrong_definition_version).format_assembly(
            plan, wrong_definition_version
        )
    _assert_assembly_export_rejected(
        tmp_path / "forged-definition-version.HMS",
        replace(request, post_definition=wrong_definition_version, request_id=None),
        result,
    )


def test_assembly_export_rejects_changed_request_and_source_artifact(
    tmp_path: Path,
) -> None:
    request, result = _published_mixed_assembly()
    changed_metadata = replace(
        request,
        request_id=None,
        shared_context=replace(
            request.shared_context,
            global_metadata=(("revision", "changed"),),
        ),
    )
    assert result.input_fingerprint == build_assembly_input_fingerprint(request)
    assert result.input_fingerprint != build_assembly_input_fingerprint(
        changed_metadata
    )
    _assert_assembly_export_rejected(
        tmp_path / "changed-metadata.HMS", changed_metadata, result
    )

    first = request.operations[0]
    changed_artifact_id = type(first.artifact_id)(
        UUID("27700000-0000-4000-8000-000000000103")
    )
    changed_source = replace(
        first.source_snapshot,
        artifact=replace(
            first.source_snapshot.artifact,
            artifact_id=changed_artifact_id,
        ),
    )
    changed_first = replace(
        first,
        artifact_id=changed_artifact_id,
        source_snapshot=changed_source,
    )
    changed_artifact_request = replace(
        request,
        request_id=None,
        operations=(changed_first, *request.operations[1:]),
    )
    _assert_assembly_export_rejected(
        tmp_path / "changed-artifact.HMS", changed_artifact_request, result
    )


def test_direct_fanuc_and_assembly_request_require_exact_canonical_contract() -> None:
    request, result = _published_mixed_assembly()
    definition = request.post_definition
    profile = definition.production_profile
    assert profile is not None
    program = result.plan.sections[0].program_ir

    foreign_profile = replace(profile, controller_family="FOREIGN")
    wrong_profile_id = replace(
        profile,
        profile_id=type(profile.profile_id)(
            UUID("27700000-0000-4000-8000-000000000104")
        ),
    )
    wrong_profile_version = replace(profile, profile_version=1)
    variants = (
        replace(definition, production_profile=foreign_profile),
        replace(definition, production_profile=wrong_profile_id),
        replace(definition, production_profile=wrong_profile_version),
        replace(
            definition,
            definition_id=type(definition.definition_id)(
                UUID("27700000-0000-4000-8000-000000000105")
            ),
        ),
        replace(definition, definition_version=1),
        replace(definition, numeric_precision=14),
        replace(
            definition,
            definition_version=1,
            production_profile=wrong_profile_version,
        ),
    )
    for forged_definition in variants:
        assert validate_fanuc_program(program, forged_definition)
        assert validate_assembly_request(
            replace(request, post_definition=forged_definition, request_id=None)
        )


@pytest.mark.parametrize("source_index", (0, 1))
def test_v2_export_identity_and_currentness_are_reused(
    tmp_path: Path,
    source_index: int,
) -> None:
    source = _shared_rest_sources()[source_index]
    request = _post_request(
        source,
        f"{source.operation.strategy_key}.fn",
        source_index + 1,
    )
    first_post = PostRuntimeService().post(request, source)
    second_post = PostRuntimeService().post(request, source)
    assert first_post.result is not None and second_post.result is not None
    first_snapshot = NCExportSourceSnapshot(1, request, first_post.result, source)
    second_snapshot = NCExportSourceSnapshot(1, request, second_post.result, source)
    first_request = NCExportRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        first_post.result.result_id,
        request.program_context.file_name,
    )
    second_request = replace(first_request, request_id=None, post_result_id=second_post.result.result_id)

    first_root = tmp_path / "first.HMS"
    second_root = tmp_path / "second.HMS"
    first_root.mkdir()
    second_root.mkdir()
    first_export = NCExportService().export(first_root, first_request, first_snapshot)
    second_export = NCExportService().export(second_root, second_request, second_snapshot)
    assert first_export.accepted and second_export.accepted
    assert first_export.artifact is not None and second_export.artifact is not None
    assert first_export.artifact.artifact_id == second_export.artifact.artifact_id
    assert first_export.artifact.source_artifact_fingerprint == source.artifact.artifact_fingerprint
    assert first_export.artifact.sha256 == first_post.result.output_checksum

    other_source = _shared_rest_sources()[1 - source_index]
    stale_root = tmp_path / "stale.HMS"
    stale_root.mkdir()
    stale = NCExportService().export(
        stale_root,
        replace(first_request, request_id=None),
        first_snapshot,
        current_source=lambda: replace(first_snapshot, source=other_source),
    )
    assert not stale.accepted
    assert not (tmp_path / "stale.HMS" / "nc").exists()

    wrong_definition = replace(request, post_definition=robodrill_21i_definition())
    profile_stale_root = tmp_path / "profile-stale.HMS"
    profile_stale_root.mkdir()
    profile_stale = NCExportService().export(
        profile_stale_root,
        replace(first_request, request_id=None),
        replace(first_snapshot, post_request=wrong_definition),
    )
    assert not profile_stale.accepted
    assert not (tmp_path / "profile-stale.HMS" / "nc").exists()
