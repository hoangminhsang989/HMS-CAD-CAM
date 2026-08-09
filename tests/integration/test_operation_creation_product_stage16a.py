"""Real project/service/UI probes for the Stage16A operation workflow."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.application import AUTOMATIC_PARAMETER_CONTRACT_KEY
from hms_cadcam.cam.application import basic_parallel_resources
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
)
from hms_cadcam.cam.cam3d import (
    CamSurfaceOrientation,
    CamSurfaceReference,
    CamSurfaceRole,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    LengthUnit,
    Revision,
)
from hms_cadcam.cam.operation_creation import OperationCreationState
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from tests.unit.test_drilling_ui import _hole, _resolved


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _surface(project_id, source_id) -> CamSurfaceReference:
    reference = GeometryReference(
        GeometryReferenceId.new(),
        "hms_cam3d_surface",
        1,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": "stage16a-face"}),
        Revision(1),
        subshape_selector="stage16a-face",
    )
    return CamSurfaceReference(
        project_id,
        reference,
        CamSurfaceOrientation.FORWARD,
        CamSurfaceRole.PART,
        body_identity="stage16a-body",
        face_identity="stage16a-face",
    )


def _workspace(tmp_path, application: QApplication):
    source = tmp_path / "stage16a.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    project = service.create_project_from_source(tmp_path, "Stage16A Product", source)
    source_id = project.manifest.source_files[0].source_id
    surfaces = (_surface(project.manifest.project_id, source_id),)
    hole = _hole(source_id, hint="stage16a-hole")
    bounds = (
        SimpleNamespace(
            x_min=0.0,
            y_min=0.0,
            z_min=0.0,
            x_max=20.0,
            y_max=10.0,
            z_max=5.0,
        ),
    )
    workspace = CamWorkspace(
        service,
        lambda: source_id,
        parallel_surface_provider=lambda: surfaces,
        parallel_geometry_bounds_provider=lambda: bounds,
        drilling_pick_provider=lambda _axis: hole,
        drilling_resolver=_resolved,
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_parallel_resources()
    workspace.create_basic_resources()
    application.processEvents()
    return service, project, workspace


def _open_strategy_wizard(
    workspace: CamWorkspace, application: QApplication, strategy_id: str
):
    assert workspace.open_operation_creation()
    application.processEvents()
    wizard = workspace._operation_creation_wizard
    assert wizard is not None
    for row in range(wizard.strategy_list.count()):
        item = wizard.strategy_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == strategy_id:
            wizard.strategy_list.setCurrentItem(item)
            break
    else:
        raise AssertionError(f"Strategy missing: {strategy_id}")
    wizard.next_button.click()
    application.processEvents()
    return wizard


def _select_strategy(wizard, strategy_id: str) -> None:
    for row in range(wizard.strategy_list.count()):
        item = wizard.strategy_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == strategy_id:
            wizard.strategy_list.setCurrentItem(item)
            return
    raise AssertionError(strategy_id)


def _open_parallel_wizard(workspace: CamWorkspace, application: QApplication):
    return _open_strategy_wizard(workspace, application, "parallel_finishing_3d")


def _add_second_parallel_tool(service: ProjectService):
    tool, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: (
            app.add_tool_definition(tool),
            app.add_holder_definition(holder),
            app.add_tool_assembly(assembly),
            app.add_machine_definition(machine),
        )[-1]
    )
    return assembly


def _prepare_parallel_binding(wizard, application: QApplication):
    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    binding = wizard._binding
    assert page is not None and binding is not None
    page._field_widgets["geometry_summary"].action_button.click()
    application.processEvents()
    return binding, page.state.applicable_snapshot()


def test_real_parallel_workflow_creates_exactly_once_without_automatic_runtime(
    tmp_path, application: QApplication
) -> None:
    service, project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)

    incompatible = tuple(
        wizard.tool_list.topLevelItem(row)
        for row in range(wizard.tool_list.topLevelItemCount())
        if wizard.tool_list.topLevelItem(row).isDisabled()
    )
    assert incompatible
    assert any("end_mill" in item.text(1) for item in incompatible)
    assert all(item.toolTip(4) for item in incompatible)

    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    assert page is not None
    assert page.schema.editor_id == "parallel_finishing_production_8a2_3"
    assert "nguồn" in str(page.state.values["automatic_stepover_summary"]).casefold()

    page._field_widgets["geometry_summary"].action_button.click()
    page.state.edit_many(
        {"stepover_override_enabled": True, "stepover_mm": "1.25"}
    )
    application.processEvents()
    assert page.state.values["selected_face_count"] == "1"
    assert not any(item.severity.name == "ERROR" for item in page.state.validate())

    simulation_before = service.simulation_runs.results()
    post_before = service.post_service.results()
    wizard.finish_button.click()
    wizard._finish()
    application.processEvents()

    setup = service.cam_snapshot.jobs[0].setups[0]
    assert len(setup.operation_tree.operations) == 1
    operation = setup.operation_tree.operations[0]
    assert operation.strategy_key == "parallel_finishing_3d"
    assert operation.artifact_state.status is ArtifactStatus.DIRTY
    assert operation.artifact_state.status not in {
        ArtifactStatus.COMPUTING,
        ArtifactStatus.VALID,
    }
    assert len(service.cam3d_config.zones) == 1
    assert service.cam_snapshot.artifacts == ()
    assert service.simulation_runs.results() == simulation_before == ()
    assert service.post_service.results() == post_before == ()

    automatic = AutomaticParameterContract.from_json(
        dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    )
    stepover = automatic.value("stepover_mm")
    assert stepover.mode is AutomaticParameterMode.MANUAL
    assert stepover.effective_value == pytest.approx(1.25)

    root = project.root_path
    service.save()
    service.close_project(discard_changes=True)
    reopened = service.open_project(root)
    restored = reopened.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(restored) == 1
    assert restored[0].operation_id == operation.operation_id
    assert len(service.cam3d_config.zones) == 1
    service.close_project(discard_changes=True)
    workspace.close()


def test_cancel_discards_editor_working_copy_without_project_side_effect(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    before = service.cam_snapshot
    config_before = service.cam3d_config
    wizard = _open_parallel_wizard(workspace, application)
    wizard.next_button.click()
    application.processEvents()
    assert wizard.editor_page is not None
    wizard.editor_page.state.edit("operation_name", "Discard me")
    wizard.reject()
    application.processEvents()
    assert service.cam_snapshot == before
    assert service.cam3d_config == config_before
    assert all(
        not setup.operation_tree.operations
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    assert service.cam_snapshot.artifacts == ()
    assert service.simulation_runs.results() == ()
    assert service.post_service.results() == ()
    service.close_project(discard_changes=True)
    workspace.close()


def test_created_product_session_rejects_resurrection_and_second_transaction(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    choice = wizard._selected_tool_choice()
    assert choice is not None
    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    binding = wizard._binding
    assert page is not None
    assert binding is not None
    page._field_widgets["geometry_summary"].action_button.click()
    application.processEvents()
    values = page.state.applicable_snapshot()
    simulation_before = service.simulation_runs.results()
    post_before = service.post_service.results()

    wizard.finish_button.click()
    application.processEvents()
    created = wizard.session
    assert created.state is OperationCreationState.CREATED
    setup = service.cam_snapshot.jobs[0].setups[0]
    operation_ids = tuple(
        operation.operation_id for operation in setup.operation_tree.operations
    )
    zones = service.cam3d_config.zones
    tools = service.cam_snapshot.tool_definitions
    assert len(operation_ids) == 1
    assert len(zones) == 1

    for attempt in (
        lambda: created.configure({"operation_name": "Duplicate"}),
        lambda: created.select_tool(choice),
        lambda: created.select_strategy("z_level_finishing_3d"),
        created.back,
        created.mark_created,
        created.cancel,
    ):
        with pytest.raises(RuntimeError):
            attempt()

    with pytest.raises(RuntimeError, match="hiện hành"):
        binding.finish_callback(values)
    wizard._finish()
    application.processEvents()

    setup_after = service.cam_snapshot.jobs[0].setups[0]
    assert tuple(
        operation.operation_id
        for operation in setup_after.operation_tree.operations
    ) == operation_ids
    assert service.cam3d_config.zones == zones
    assert service.cam_snapshot.tool_definitions == tools
    assert service.simulation_runs.results() == simulation_before == ()
    assert service.post_service.results() == post_before == ()
    service.close_project(discard_changes=True)
    workspace.close()


def test_cancelled_product_session_cannot_reach_finish_or_persistence(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    choice = wizard._selected_tool_choice()
    assert choice is not None
    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    assert page is not None
    page._field_widgets["geometry_summary"].action_button.click()
    application.processEvents()
    snapshot_before = service.cam_snapshot
    config_before = service.cam3d_config
    simulation_before = service.simulation_runs.results()
    post_before = service.post_service.results()

    wizard.reject()
    application.processEvents()
    cancelled = wizard.session
    assert cancelled.state is OperationCreationState.CANCELLED

    for attempt in (
        lambda: cancelled.configure({"operation_name": "Forbidden"}),
        lambda: cancelled.select_tool(choice),
        lambda: cancelled.select_strategy("z_level_finishing_3d"),
        cancelled.back,
        cancelled.mark_created,
    ):
        with pytest.raises(RuntimeError, match="terminal"):
            attempt()
    assert cancelled.cancel() is cancelled
    with pytest.raises(RuntimeError, match="terminal"):
        wizard._finish()

    assert wizard.session.state is OperationCreationState.CANCELLED
    assert service.cam_snapshot == snapshot_before
    assert service.cam3d_config == config_before
    assert service.simulation_runs.results() == simulation_before == ()
    assert service.post_service.results() == post_before == ()
    assert all(
        not setup.operation_tree.operations
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    service.close_project(discard_changes=True)
    workspace.close()


def test_saved_finish_callback_after_cancel_is_never_authoritative(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    binding, values = _prepare_parallel_binding(wizard, application)
    operations_before = sum(
        len(setup.operation_tree.operations)
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    zones_before = service.cam3d_config.zones

    wizard.reject()
    application.processEvents()
    assert wizard.session.state is OperationCreationState.CANCELLED
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding.finish_callback(values)

    operations_after = sum(
        len(setup.operation_tree.operations)
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    assert operations_after == operations_before == 0
    assert service.cam3d_config.zones == zones_before == ()
    service.close_project(discard_changes=True)
    workspace.close()


def test_saved_finish_callback_after_close_is_never_authoritative(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    binding, values = _prepare_parallel_binding(wizard, application)
    wizard.close()
    application.processEvents()
    assert wizard.session.state is OperationCreationState.CANCELLED
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding.finish_callback(values)
    assert all(
        not setup.operation_tree.operations
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    assert service.cam3d_config.zones == ()
    service.close_project(discard_changes=True)
    workspace.close()


def test_old_callback_after_back_is_blocked_and_replacement_is_valid(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    binding_a, values_a = _prepare_parallel_binding(wizard, application)
    wizard.back_button.click()
    application.processEvents()
    assert wizard.session.current_step.value == "select_tool"
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_a.finish_callback(values_a)

    binding_b, values_b = _prepare_parallel_binding(wizard, application)
    assert binding_b is not binding_a
    wizard.finish_button.click()
    application.processEvents()
    assert wizard.session.state is OperationCreationState.CREATED
    assert len(service.cam_snapshot.jobs[0].setups[0].operation_tree.operations) == 1
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_a.finish_callback(values_a)
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_b.finish_callback(values_b)
    service.close_project(discard_changes=True)
    workspace.close()


def test_old_callback_after_tool_change_is_blocked_and_current_tool_creates_once(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    binding_a, values_a = _prepare_parallel_binding(wizard, application)
    wizard.back_button.click()
    application.processEvents()
    tool_b = _add_second_parallel_tool(service)
    wizard.refresh_live_state()
    application.processEvents()
    for row in range(wizard.tool_list.topLevelItemCount()):
        item = wizard.tool_list.topLevelItem(row)
        if item.data(0, Qt.ItemDataRole.UserRole) == str(tool_b.assembly_id):
            wizard.tool_list.setCurrentItem(item)
            break
    else:
        raise AssertionError("Second compatible Tool was not listed")
    binding_b, _values_b = _prepare_parallel_binding(wizard, application)
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_a.finish_callback(values_a)
    wizard.finish_button.click()
    application.processEvents()
    operations = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(operations) == 1
    assert operations[0].tool_assembly.assembly_id == tool_b.assembly_id
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_b.finish_callback(_values_b)
    service.close_project(discard_changes=True)
    workspace.close()


def test_old_callback_after_strategy_change_is_blocked(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    binding_a, values_a = _prepare_parallel_binding(wizard, application)
    wizard.back_button.click()
    wizard.back_button.click()
    application.processEvents()
    _select_strategy(wizard, "z_level_finishing_3d")
    wizard.next_button.click()
    application.processEvents()
    assert wizard.session.strategy_id == "z_level_finishing_3d"
    with pytest.raises(RuntimeError, match="hiện hành"):
        binding_a.finish_callback(values_a)
    assert all(
        not setup.operation_tree.operations
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    assert service.cam3d_config.zones == ()
    service.close_project(discard_changes=True)
    workspace.close()


def test_deleted_tool_while_open_is_invalidated_and_blocks_next(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    selected = wizard._selected_tool_choice()
    assert selected is not None
    snapshot = service.cam_snapshot
    service.stage_cam_snapshot(
        replace(
            snapshot,
            tool_definitions=tuple(
                item
                for item in snapshot.tool_definitions
                if item.tool_id != selected.tool_id
            ),
        )
    )
    wizard.refresh_live_state()
    application.processEvents()
    assert wizard._selected_tool_choice() is None
    assert not wizard.next_button.isEnabled()
    assert all(
        wizard.tool_list.topLevelItem(row).isDisabled()
        for row in range(wizard.tool_list.topLevelItemCount())
        if wizard.tool_list.topLevelItem(row).data(0, Qt.ItemDataRole.UserRole)
        == str(selected.assembly_id)
    )
    wizard.reject()
    service.close_project(discard_changes=True)
    workspace.close()


def test_stale_tool_configuration_at_finish_creates_no_partial_operation_or_zone(
    tmp_path, application: QApplication
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_parallel_wizard(workspace, application)
    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    assert page is not None
    page._field_widgets["geometry_summary"].action_button.click()
    selected_tool_id = wizard.session.tool_id
    snapshot = service.cam_snapshot
    service.stage_cam_snapshot(
        replace(
            snapshot,
            tool_definitions=tuple(
                replace(
                    item,
                    configuration_revision=item.configuration_revision.next(),
                )
                if item.tool_id == selected_tool_id
                else item
                for item in snapshot.tool_definitions
            ),
        )
    )
    wizard.finish_button.click()
    application.processEvents()
    assert all(
        not setup.operation_tree.operations
        for job in service.cam_snapshot.jobs
        for setup in job.setups
    )
    assert service.cam3d_config.zones == ()
    assert service.cam_snapshot.artifacts == ()
    assert wizard.feedback.isVisible()
    assert "profile" in wizard.feedback.text().casefold()
    wizard.reject()
    service.close_project(discard_changes=True)
    workspace.close()


@pytest.mark.parametrize(
    ("strategy_id", "editor_id", "geometry_field"),
    (
        ("drilling_v1", "drilling_production_9a6", None),
        (
            "z_level_finishing_3d",
            "z_level_finishing_production_8a3_3",
            "geometry_summary",
        ),
    ),
)
def test_drilling_and_zlevel_use_real_production_editor_and_transaction(
    tmp_path,
    application: QApplication,
    strategy_id: str,
    editor_id: str,
    geometry_field: str | None,
) -> None:
    service, _project, workspace = _workspace(tmp_path, application)
    wizard = _open_strategy_wizard(workspace, application, strategy_id)
    assert wizard._selected_tool_choice() is not None
    wizard.next_button.click()
    application.processEvents()
    page = wizard.editor_page
    assert page is not None
    assert page.schema.editor_id == editor_id
    if geometry_field is not None:
        page._field_widgets[geometry_field].action_button.click()
        application.processEvents()
    assert not any(item.severity.name == "ERROR" for item in page.state.validate())
    wizard.finish_button.click()
    application.processEvents()
    operations = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(operations) == 1
    assert operations[0].strategy_key == strategy_id
    assert operations[0].artifact_state.status is ArtifactStatus.DIRTY
    assert service.cam_snapshot.artifacts == ()
    assert service.simulation_runs.results() == ()
    assert service.post_service.results() == ()
    service.close_project(discard_changes=True)
    workspace.close()
