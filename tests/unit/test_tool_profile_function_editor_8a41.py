"""Stage 8A.4.1 Function Editor resolution and dependency integration."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hms_cadcam.cam.application import (
    basic_parallel_resources,
)
from hms_cadcam.cam.automatic_parameters import AutomaticParameterMode
from hms_cadcam.cam.cam3d.parallel import (
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    ArtifactStatus,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolProgramProfile,
    ToolProgramProfileId,
    preview_tool_profile_capture,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    ParallelEditorContext,
    _resolve_automatic_contract,
    parallel_applied_values,
)
from hms_cadcam.project.service import ProjectService
from tests.unit._parallel_finishing_fixtures import planar_fixture
from tests.unit.test_parallel_finishing_persistence import _snapshot


_NOW = datetime(2026, 7, 24, 11, 30, tzinfo=UTC)


def _profile(tool, stepover: float, *, holder_fingerprint=None):
    schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(
        "parallel_finishing_3d"
    )
    return ToolProgramProfile(
        ToolProgramProfileId.new(),
        tool.tool_id,
        schema.strategy_id,
        schema.display_name_vi,
        True,
        schema.profile_schema_version,
        schema.normalize_values({"stepover_mm": stepover}),
        _NOW,
        _NOW,
        tool.revision,
        tool.content_fingerprint,
        source_holder_fingerprint=holder_fingerprint,
    )


def _context(
    fixture,
    *,
    tools,
    assemblies,
    holders=(),
) -> ParallelEditorContext:
    setup = _snapshot(fixture, fixture.operation).jobs[0].setups[0]
    return ParallelEditorContext(
        "Gia công tinh song song",
        fixture.operation,
        setup,
        fixture.zone.job_id,
        fixture.zone.project_id,
        fixture.zone,
        tuple(assemblies),
        tuple(tools),
        tuple(holders),
        (),
    )


def test_parallel_editor_profile_manual_override_and_tool_change_recompute() -> None:
    fixture = planar_fixture(stepover=5.0)
    first_profile = _profile(fixture.tool, 0.55)
    first_tool = replace(
        fixture.tool,
        program_profiles=(first_profile,),
        configuration_revision=Revision(1),
    )
    second_tool, second_holder, second_assembly, _machine = (
        basic_parallel_resources(LengthUnit.MM)
    )
    second_profile = _profile(
        second_tool,
        0.75,
        holder_fingerprint=second_holder.content_fingerprint,
    )
    second_tool = replace(
        second_tool,
        program_profiles=(second_profile,),
        configuration_revision=Revision(1),
    )
    context = _context(
        fixture,
        tools=(first_tool, second_tool),
        assemblies=(fixture.assembly, second_assembly),
        holders=(second_holder,),
    )
    values = parallel_applied_values(context)

    first = _resolve_automatic_contract(context, None, values)
    assert first.value("stepover_mm").effective_value == 0.55
    assert first.value("stepover_mm").source == (
        "Cấu hình Tool theo chương trình"
    )

    manual_values = {
        **values,
        "stepover_override_enabled": True,
        "stepover_mm": "0.25",
    }
    manual = _resolve_automatic_contract(context, None, manual_values)
    assert manual.value("stepover_mm").mode is AutomaticParameterMode.MANUAL
    assert manual.value("stepover_mm").effective_value == 0.25
    assert first_tool.program_profiles[0].sparse_mapping == {
        "stepover_mm": 0.55
    }

    changed_tool_values = {
        **values,
        "tool_assembly_id": str(second_assembly.assembly_id),
    }
    changed_tool = _resolve_automatic_contract(
        context,
        None,
        changed_tool_values,
    )
    assert changed_tool.value("stepover_mm").effective_value == 0.75
    assert (
        changed_tool.value("stepover_mm").dependency_fingerprint
        != first.value("stepover_mm").dependency_fingerprint
    )


def test_profile_value_change_stales_matching_artifact_but_rename_does_not(
    tmp_path,
) -> None:
    fixture = planar_fixture(stepover=5.0)
    project_root = tmp_path / "Profile Stale.HMS"
    project_root.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project_root,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.metadata is not None
    assert result.operation.artifact_state.status is ArtifactStatus.VALID
    profile = _profile(fixture.tool, 0.7)
    configured = replace(
        fixture.tool,
        program_profiles=(profile,),
        configuration_revision=Revision(1),
    )
    snapshot = replace(
        _snapshot(
            fixture,
            result.operation,
            (result.metadata,),
        ),
        tool_definitions=(configured,),
    )

    from hms_cadcam.cam.application import CamApplicationService

    application = CamApplicationService()
    application.load(snapshot)
    renamed = application.rename_tool_program_profile(
        configured.tool_id,
        profile.profile_id,
        "Tên trình bày mới",
        expected_configuration_revision=Revision(1),
    )
    renamed_operation = (
        renamed.jobs[0].setups[0].operation_tree.operations[0]
    )
    assert renamed_operation.artifact_state.status is ArtifactStatus.VALID

    preview = preview_tool_profile_capture(
        renamed.tool_definitions[0],
        "parallel_finishing_3d",
        "Tên trình bày mới",
        {"stepover_mm": 0.45},
        overridden_field_ids=frozenset({"stepover_mm"}),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    changed = application.save_tool_program_profile(
        preview,
        expected_configuration_revision=Revision(2),
    )
    changed_operation = (
        changed.jobs[0].setups[0].operation_tree.operations[0]
    )
    assert changed_operation.artifact_state.status is ArtifactStatus.DIRTY
    assert result.metadata in changed.artifacts


def test_project_lifecycle_stales_only_the_changed_profile_operation(
    tmp_path,
    monkeypatch,
) -> None:
    fixture = planar_fixture(stepover=5.0)
    profile = _profile(fixture.tool, 0.7)
    configured = replace(
        fixture.tool,
        program_profiles=(profile,),
        configuration_revision=Revision(1),
    )
    snapshot = replace(
        _snapshot(fixture, fixture.operation),
        tool_definitions=(configured,),
    )
    service = ProjectService.create_default(tmp_path / "config")
    service.new_project(tmp_path, "Profile Lifecycle")
    service.stage_cam_snapshot(snapshot)
    marked: list[object] = []
    cancel_all_calls: list[bool] = []
    monkeypatch.setattr(
        service.simulation_runs,
        "mark_stale",
        lambda operation_id, _message="": marked.append(operation_id),
    )
    monkeypatch.setattr(
        service.simulation_runs,
        "cancel_all",
        lambda *, stale=False: cancel_all_calls.append(stale),
    )

    service.execute_cam_command(
        lambda application: application.rename_tool_program_profile(
            configured.tool_id,
            profile.profile_id,
            "Tên trình bày mới",
            expected_configuration_revision=Revision(1),
        )
    )
    assert marked == []
    assert cancel_all_calls == []

    renamed = service.cam_snapshot.tool_definitions[0]
    preview = preview_tool_profile_capture(
        renamed,
        "parallel_finishing_3d",
        "Tên trình bày mới",
        {"stepover_mm": 0.45},
        overridden_field_ids=frozenset({"stepover_mm"}),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    service.execute_cam_command(
        lambda application: application.save_tool_program_profile(
            preview,
            expected_configuration_revision=Revision(2),
        )
    )

    assert marked == [fixture.operation.operation_id]
    assert cancel_all_calls == []
    service.close_project(discard_changes=True)


def test_common_defaults_change_stales_registered_strategy_without_profile() -> None:
    fixture = planar_fixture(stepover=5.0)
    application_snapshot = _snapshot(fixture, fixture.operation)

    from hms_cadcam.cam.application import CamApplicationService

    application = CamApplicationService()
    application.load(application_snapshot)
    changed = application.update_tool_common_defaults(
        fixture.tool.tool_id,
        ToolCommonDefaults(quality_profile="high"),
        expected_configuration_revision=Revision(0),
    )

    changed_tool = changed.tool_definitions[0]
    changed_operation = (
        changed.jobs[0].setups[0].operation_tree.operations[0]
    )
    assert changed_tool.content_fingerprint == fixture.tool.content_fingerprint
    assert (
        changed_tool.configuration_fingerprint
        != fixture.tool.configuration_fingerprint
    )
    assert changed_operation.artifact_state.status is ArtifactStatus.DIRTY
