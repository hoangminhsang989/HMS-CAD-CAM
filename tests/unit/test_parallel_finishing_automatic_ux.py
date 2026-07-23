"""Automatic-first Parallel Finishing UX and lifecycle contracts."""

from __future__ import annotations

from dataclasses import replace

from hms_cadcam.cam.application import AUTOMATIC_PARAMETER_CONTRACT_KEY
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    CamQualityProfile,
)
from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_AUTOMATIC_POLICY_VERSION,
    ParallelGeometryEvidence,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.application import basic_parallel_resources
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    LengthUnit,
    OperationParameterSet,
    Point3,
    ToolAssemblyReference,
)
from hms_cadcam.ui.function_editor import (
    FunctionEditorDraftState,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    ParallelEditorDraftContext,
    build_parallel_schema,
    parallel_applied_values,
    parallel_draft_derived_values,
    parallel_validation_diagnostics,
    prepare_parallel_update,
)
from tests.unit.test_parallel_finishing_function_editor_8a23 import (
    _context,
    _valid_values,
)
from tests.unit._parallel_finishing_fixtures import planar_fixture


def _draft(context, evidence: ParallelGeometryEvidence | None = None):
    return ParallelEditorDraftContext(
        context.zone.part_surfaces.selection.surfaces,
        geometry_evidence=evidence or context.geometry_evidence,
    )


def _with_contract(operation, contract: AutomaticParameterContract):
    values = tuple(
        (name, contract.to_json() if name == AUTOMATIC_PARAMETER_CONTRACT_KEY else value)
        for name, value in operation.parameters.values
    )
    return replace(
        operation,
        parameters=OperationParameterSet(
            operation.parameters.strategy_key,
            operation.parameters.strategy_version,
            values,
            operation.parameters.schema_version,
        ),
    )


def test_basic_mode_contains_only_geometry_tool_quality_and_auto_summary() -> None:
    context, machine = _context()
    schema = build_parallel_schema(context)
    applied = parallel_applied_values(context)
    visible = schema.visible_sections(applied, ParameterDisclosureLevel.BASIC)
    assert [item.section_id for item in visible] == [
        "geometry",
        "tool",
        "quality",
        "automatic_summary",
    ]
    assert schema.field("linking_mode").choices == ()
    assert schema.field("direction_angle_degrees").applicable_when is not None
    assert schema.field("stepover_mm").applicable_when is not None
    assert "tham số tự động" in str(
        applied["automatic_mode_counts"]
    )
    applied["machine_id"] = str(machine.machine_id)
    automatic = prepare_parallel_update(context, _draft(context), applied)
    assert float(applied["stepover_mm"]) == automatic.parameters.stepover_mm


def test_principal_extent_direction_and_quality_profiles_are_deterministic() -> None:
    context, machine = _context()
    values = _valid_values(context, machine)
    long_u = ParallelGeometryEvidence(0.0, 40.0, 0.0, 10.0, "Vùng U dài")
    long_v = ParallelGeometryEvidence(0.0, 10.0, 0.0, 40.0, "Vùng V dài")
    first = prepare_parallel_update(context, _draft(context, long_u), values)
    repeated = prepare_parallel_update(context, _draft(context, long_u), values)
    rotated = prepare_parallel_update(context, _draft(context, long_v), values)
    assert first.parameters.direction_angle_degrees == 0.0
    assert repeated.automatic_contract == first.automatic_contract
    assert rotated.parameters.direction_angle_degrees == 90.0
    assert (
        first.automatic_contract.value("cut_direction").resolved_value
        == "zigzag"
    )
    single_pass = prepare_parallel_update(
        context,
        _draft(
            context,
            ParallelGeometryEvidence(0.0, 40.0, 0.0, 0.0, "Một lượt cắt"),
        ),
        values,
    )
    assert (
        single_pass.automatic_contract.value("cut_direction").resolved_value
        == "one_way"
    )

    results = {}
    for profile in CamQualityProfile:
        candidate = prepare_parallel_update(
            context,
            _draft(context, long_u),
            {**values, "quality_profile": profile.value},
        )
        results[profile] = candidate
    assert (
        results[CamQualityProfile.FAST].parameters.stepover_mm
        > results[CamQualityProfile.BALANCED].parameters.stepover_mm
        > results[CamQualityProfile.HIGH].parameters.stepover_mm
    )
    assert (
        results[CamQualityProfile.FAST].zone.tolerance.chordal_tolerance
        >= results[CamQualityProfile.BALANCED].zone.tolerance.chordal_tolerance
        > results[CamQualityProfile.HIGH].zone.tolerance.chordal_tolerance
    )


def test_manual_override_persists_and_switching_to_auto_ignores_stale_invalid() -> None:
    context, machine = _context()
    schema = build_parallel_schema(context)
    draft = _draft(context)
    state = FunctionEditorDraftState(
        schema,
        _valid_values(context, machine),
        validation_callback=lambda values: parallel_validation_diagnostics(
            schema, context, draft, values
        ),
        draft_transform_callback=lambda values: parallel_draft_derived_values(
            context, draft, values
        ),
    )
    state.edit("stepover_override_enabled", True)
    state.edit("stepover_mm", "không-phải-số")
    assert any(item.field_id == "stepover_mm" for item in state.validate())
    assert state.values["stepover_mm"] == "không-phải-số"

    state.edit("stepover_override_enabled", False)
    assert not any(item.severity.name == "ERROR" for item in state.validate())
    assert state.values["stepover_mm"] == "không-phải-số"
    update = prepare_parallel_update(context, draft, state.values)
    assert update.parameters.stepover_mm > 0.0
    assert update.automatic_contract.value("stepover_mm").mode is AutomaticParameterMode.AUTO
    assert update.automatic_contract.value("stepover_mm").override_value == "không-phải-số"


def test_direction_named_manual_modes_use_supported_x_y_axes() -> None:
    context, machine = _context()
    values = _valid_values(context, machine)
    for mode, expected in (("axis_x", 0.0), ("axis_y", 90.0)):
        update = prepare_parallel_update(
            context,
            _draft(context),
            {
                **values,
                "direction_override_enabled": True,
                "direction_override_mode": mode,
                "direction_angle_degrees": "giá-trị-ẩn-không-hợp-lệ",
            },
        )
        assert update.parameters.direction_angle_degrees == expected


def test_apply_reopen_reconstructs_manual_intent_without_schema_bump() -> None:
    context, machine = _context()
    values = _valid_values(context, machine)
    values.update(
        stepover_override_enabled=True,
        stepover_mm="1.25",
        tolerance_override_enabled=True,
        tolerance_mm="0.008",
    )
    applied = prepare_parallel_update(context, _draft(context), values)
    assert applied.operation.parameters.schema_version == 1
    assert applied.operation.parameters.strategy_version == 1
    assert AUTOMATIC_PARAMETER_CONTRACT_KEY in dict(applied.operation.parameters.values)
    reopened = replace(context, operation=applied.operation, zone=applied.zone)
    reconstructed = parallel_applied_values(reopened)
    assert reconstructed["stepover_override_enabled"] is True
    assert reconstructed["stepover_mm"] == "1.25"
    assert reconstructed["tolerance_override_enabled"] is True
    assert reconstructed["tolerance_mm"] == "0.008"
    reconstructed["machine_id"] = str(machine.machine_id)
    unchanged = prepare_parallel_update(reopened, _draft(reopened), reconstructed)
    assert unchanged.operation == applied.operation
    assert unchanged.zone == applied.zone


def test_quality_geometry_mode_and_policy_changes_invalidate_effective_hash() -> None:
    context, machine = _context()
    values = _valid_values(context, machine)
    baseline = prepare_parallel_update(context, _draft(context), values)
    high = prepare_parallel_update(
        context,
        _draft(context),
        {**values, "quality_profile": CamQualityProfile.HIGH.value},
    )
    manual_same = prepare_parallel_update(
        context,
        _draft(context),
        {
            **values,
            "stepover_override_enabled": True,
            "stepover_mm": str(baseline.parameters.stepover_mm),
        },
    )
    changed_surface = replace(
        context.zone.part_surfaces.selection.surfaces[0],
        face_identity="face-auto-dependency-changed",
    )
    geometry = prepare_parallel_update(
        context,
        ParallelEditorDraftContext(
            (changed_surface,),
            geometry_evidence=ParallelGeometryEvidence(
                0.0, 8.0, 0.0, 30.0, "Hình học đã đổi"
            ),
        ),
        values,
    )
    hashes = {
        item.automatic_contract.effective_fingerprint.digest
        for item in (baseline, high, manual_same, geometry)
    }
    assert len(hashes) == 4

    current = replace(context, operation=baseline.operation, zone=baseline.zone)
    current_values = parallel_applied_values(current)
    current_values["machine_id"] = str(machine.machine_id)
    changed = prepare_parallel_update(
        current,
        _draft(current),
        {**current_values, "quality_profile": CamQualityProfile.HIGH.value},
    )
    assert changed.operation.revision == current.operation.revision.next()
    assert changed.operation.artifact_state.status is ArtifactStatus.DIRTY
    assert changed.operation.parameters.fingerprint != current.operation.parameters.fingerprint

    stale_policy = replace(
        baseline.automatic_contract,
        policy_version=PARALLEL_AUTOMATIC_POLICY_VERSION + 1,
    )
    policy_operation = _with_contract(baseline.operation, stale_policy)
    policy_context = replace(context, operation=policy_operation, zone=baseline.zone)
    policy_values = parallel_applied_values(policy_context)
    policy_values["machine_id"] = str(machine.machine_id)
    refreshed = prepare_parallel_update(
        policy_context, _draft(policy_context), policy_values
    )
    assert refreshed.automatic_contract.policy_version == PARALLEL_AUTOMATIC_POLICY_VERSION
    assert refreshed.operation.parameters != policy_operation.parameters


def test_holder_and_setup_dependencies_change_contract_hash() -> None:
    context, machine = _context()
    baseline = prepare_parallel_update(
        context, _draft(context), _valid_values(context, machine)
    )
    _tool, holder, _assembly, _machine = basic_parallel_resources(LengthUnit.MM)
    assembly = replace(
        context.tool_assemblies[0],
        holder_id=holder.holder_id,
        expected_holder_revision=holder.revision,
        expected_holder_fingerprint=holder.content_fingerprint,
        expected_holder_unit=holder.unit,
    )
    holder_context = replace(
        context,
        operation=replace(
            context.operation,
            tool_assembly=ToolAssemblyReference.from_assembly(assembly),
        ),
        tool_assemblies=(assembly,),
        holder_definitions=(holder,),
    )
    holder_values = _valid_values(holder_context, machine)
    holder_changed = prepare_parallel_update(
        holder_context, _draft(holder_context), holder_values
    )

    frame = context.setup.wcs
    moved_setup = replace(
        context.setup,
        wcs=replace(
            frame,
            origin=Point3(5.0, 0.0, 0.0, LengthUnit.MM),
        ),
    )
    setup_context = replace(context, setup=moved_setup)
    setup_changed = prepare_parallel_update(
        setup_context,
        _draft(setup_context),
        _valid_values(setup_context, machine),
    )
    hashes = {
        baseline.automatic_contract.effective_fingerprint.digest,
        holder_changed.automatic_contract.effective_fingerprint.digest,
        setup_changed.automatic_contract.effective_fingerprint.digest,
    }
    assert len(hashes) == 3


def test_auto_policy_affects_artifact_and_safety_report_hash(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    context, machine = _context(fixture)
    values = _valid_values(context, machine)
    balanced = prepare_parallel_update(context, _draft(context), values)
    high = prepare_parallel_update(
        context,
        _draft(context),
        {**values, "quality_profile": CamQualityProfile.HIGH.value},
    )
    first = calculate_and_publish_parallel_finishing(
        tmp_path,
        balanced.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    second = calculate_and_publish_parallel_finishing(
        tmp_path,
        high.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert first.accepted and second.accepted
    assert first.artifact is not None and second.artifact is not None
    assert first.safety_report is not None and second.safety_report is not None
    assert first.artifact.input_fingerprint != second.artifact.input_fingerprint
    assert first.safety_report.fingerprint != second.safety_report.fingerprint
