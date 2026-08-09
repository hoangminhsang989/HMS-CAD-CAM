"""Real project and three-step workflow probes for Stage16A Mega-WP2."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from hms_cadcam.cam.application import AUTOMATIC_PARAMETER_CONTRACT_KEY
from hms_cadcam.cam.automatic_parameters import AutomaticParameterContract
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    LengthUnit,
    Revision,
    ToolCommonDefaults,
    ToolFamily,
    ToolProfileSaveMode,
    ToolProfileValueSource,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.tool_library import ToolDefinitionDraft
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.ui.tool_library import ToolLibraryDialog
from tests.integration.test_operation_creation_product_stage16a import (
    _open_parallel_wizard,
    _prepare_parallel_binding,
    _workspace,
)


def _draft(
    name: str,
    family: ToolFamily = ToolFamily.BALL_END_MILL,
    *,
    create_assembly: bool = True,
) -> ToolDefinitionDraft:
    return ToolDefinitionDraft(
        name,
        family,
        LengthUnit.MM,
        8.0,
        20.0,
        70.0,
        35.0,
        8.0,
        45.0,
        detail_angle_degrees=(
            118.0
            if family in {ToolFamily.DRILL, ToolFamily.CENTER_DRILL}
            else None
        ),
        create_assembly=create_assembly,
        assembly_name=f"{name} Assembly",
        stickout=32.0,
        gauge_length=65.0,
    )


def _select_tool_row(wizard, assembly_id) -> None:
    for row in range(wizard.tool_list.topLevelItemCount()):
        item = wizard.tool_list.topLevelItem(row)
        if item.data(0, Qt.ItemDataRole.UserRole) == str(assembly_id):
            wizard.tool_list.setCurrentItem(item)
            return
    raise AssertionError(f"Managed Tool Assembly missing from Step2: {assembly_id}")


def _operation_count(snapshot) -> int:
    return sum(
        len(setup.operation_tree.operations)
        for job in snapshot.jobs
        for setup in job.setups
    )


def test_r178_real_tool_library_safe_reuse_and_persistence_round_trip(
    tmp_path, qapp: QApplication, monkeypatch
) -> None:
    application = qapp
    service, project, workspace = _workspace(tmp_path, application)
    try:
        before_ids = {item.tool_id for item in service.cam_snapshot.tool_definitions}
        service.execute_cam_command(
            lambda app: app.create_managed_tool(_draft("Managed Tool A"))
        )
        created = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.tool_id not in before_ids
        )
        assembly_a = next(
            item
            for item in service.cam_snapshot.tool_assemblies
            if item.tool_id == created.tool_id
        )
        assert created.revision == Revision(0)
        assert created.configuration_revision == Revision(0)

        duplicate_box = []

        def duplicate_command(app):
            duplicate_box.append(app.duplicate_tool_definition(created.tool_id))
            return app.snapshot

        service.execute_cam_command(duplicate_command)
        duplicate = duplicate_box[0]
        assembly_b = next(
            item
            for item in service.cam_snapshot.tool_assemblies
            if item.tool_id == duplicate.tool_id
        )
        assert duplicate.tool_id != created.tool_id
        assert assembly_b.assembly_id != assembly_a.assembly_id

        preview = preview_tool_profile_capture(
            duplicate,
            "parallel_finishing_3d",
            "Managed parallel profile",
            {"stepover_mm": 0.8},
            overridden_field_ids=frozenset({"stepover_mm"}),
            mode=ToolProfileSaveMode.OVERRIDES_ONLY,
            registry=DEFAULT_TOOL_PROFILE_REGISTRY,
        )
        service.execute_cam_command(
            lambda app: app.save_tool_program_profile(
                preview,
                expected_configuration_revision=duplicate.configuration_revision,
            )
        )
        profiled = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.tool_id == duplicate.tool_id
        )
        assert profiled.configuration_revision == Revision(1)

        wizard = _open_parallel_wizard(workspace, application)
        _select_tool_row(wizard, assembly_b.assembly_id)
        binding_a, values_a = _prepare_parallel_binding(wizard, application)
        assert wizard.session.tool_id == duplicate.tool_id
        assert wizard.session.tool_configuration_revision == Revision(1)
        assert float(binding_a.applied_values["stepover_mm"]) == pytest.approx(0.8)
        assert ToolProfileValueSource.TOOL_PROGRAM_PROFILE in (
            wizard.session.resolved_provenance
        )

        operations_before = _operation_count(service.cam_snapshot)
        zones_before = service.cam3d_config.zones
        database_before = (project.root_path / "project.db").read_bytes()
        simulation_before = service.simulation_runs.results()
        post_before = service.post_service.results()
        service.execute_cam_command(
            lambda app: app.update_tool_common_defaults(
                duplicate.tool_id,
                ToolCommonDefaults(quality_profile="high"),
                expected_configuration_revision=Revision(1),
            )
        )

        with pytest.raises(RuntimeError, match="profile"):
            binding_a.finish_callback(values_a)
        assert _operation_count(service.cam_snapshot) == operations_before == 0
        assert service.cam3d_config.zones == zones_before == ()
        assert (project.root_path / "project.db").read_bytes() == database_before
        assert service.simulation_runs.results() == simulation_before == ()
        assert service.post_service.results() == post_before == ()

        wizard.back_button.click()
        wizard.refresh_live_state()
        application.processEvents()
        assert wizard.session.tool_id == duplicate.tool_id
        live_choice = wizard._selected_tool_choice()
        assert live_choice is not None
        assert live_choice.configuration_revision == Revision(2)
        _select_tool_row(wizard, assembly_b.assembly_id)
        binding_b, values_b = _prepare_parallel_binding(wizard, application)
        assert wizard.session.tool_configuration_revision == Revision(2)
        assert binding_b.applied_values["quality_profile"] == "high"
        assert float(binding_b.applied_values["stepover_mm"]) == pytest.approx(0.8)
        assert values_b["quality_profile"] == "high"
        assert ToolProfileValueSource.TOOL_COMMON_DEFAULT in (
            wizard.session.resolved_provenance
        )
        assert ToolProfileValueSource.TOOL_PROGRAM_PROFILE in (
            wizard.session.resolved_provenance
        )

        wizard.finish_button.click()
        application.processEvents()
        assert _operation_count(service.cam_snapshot) == 1
        operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
        assert operation.tool_assembly.assembly_id == assembly_b.assembly_id
        automatic = AutomaticParameterContract.from_json(
            dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
        )
        assert automatic.quality_profile.value == "high"
        assert automatic.value("stepover_mm").effective_value == pytest.approx(0.8)
        assert service.cam_snapshot.artifacts == ()
        assert service.simulation_runs.results() == ()
        assert service.post_service.results() == ()

        live_duplicate = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.tool_id == duplicate.tool_id
        )
        snapshot_before_delete = service.cam_snapshot
        config_before_delete = service.cam3d_config
        with pytest.raises(ValueError, match="referenced"):
            service.execute_cam_command(
                lambda app: app.remove_managed_tool(
                    live_duplicate.tool_id,
                    expected_revision=live_duplicate.revision,
                    expected_configuration_revision=(
                        live_duplicate.configuration_revision
                    ),
                )
            )
        assert service.cam_snapshot == snapshot_before_delete
        assert service.cam3d_config == config_before_delete

        service.execute_cam_command(
            lambda app: app.create_managed_tool(
                _draft("Managed Bare Tool", create_assembly=False)
            )
        )
        bare = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.name == "Managed Bare Tool"
        )
        service.execute_cam_command(
            lambda app: app.remove_managed_tool(
                bare.tool_id,
                expected_revision=bare.revision,
                expected_configuration_revision=bare.configuration_revision,
            )
        )
        assert all(
            item.tool_id != bare.tool_id
            for item in service.cam_snapshot.tool_definitions
        )

        service.execute_cam_command(
            lambda app: app.create_managed_tool(_draft("Compatibility Probe"))
        )
        probe = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.name == "Compatibility Probe"
        )
        probe_assembly = next(
            item
            for item in service.cam_snapshot.tool_assemblies
            if item.tool_id == probe.tool_id
        )
        wizard_probe = _open_parallel_wizard(workspace, application)
        _select_tool_row(wizard_probe, probe_assembly.assembly_id)
        wizard_probe.next_button.click()
        application.processEvents()
        wizard_probe.back_button.click()
        application.processEvents()
        assert wizard_probe.session.tool_id == probe.tool_id
        service.execute_cam_command(
            lambda app: app.update_managed_tool(
                probe.tool_id,
                _draft("Compatibility Probe", ToolFamily.DRILL),
                expected_revision=probe.revision,
                expected_configuration_revision=probe.configuration_revision,
            )
        )
        wizard_probe.refresh_live_state()
        application.processEvents()
        assert wizard_probe.session.tool_id is None
        assert wizard_probe._selected_tool_choice() is None
        assert not wizard_probe.next_button.isEnabled()
        row = next(
            wizard_probe.tool_list.topLevelItem(index)
            for index in range(wizard_probe.tool_list.topLevelItemCount())
            if wizard_probe.tool_list.topLevelItem(index).data(
                0, Qt.ItemDataRole.UserRole
            )
            == str(probe_assembly.assembly_id)
        )
        assert row.isDisabled()
        wizard_probe.reject()

        wizard_live = _open_parallel_wizard(workspace, application)
        before_rows = wizard_live.tool_list.topLevelItemCount()

        def managed_exec(_dialog) -> QDialog.DialogCode:
            service.execute_cam_command(
                lambda app: app.create_managed_tool(_draft("Live Refresh Tool"))
            )
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(ToolLibraryDialog, "exec", managed_exec)
        wizard_live.manage_tools.click()
        application.processEvents()
        live_tool = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.name == "Live Refresh Tool"
        )
        live_assembly = next(
            item
            for item in service.cam_snapshot.tool_assemblies
            if item.tool_id == live_tool.tool_id
        )
        row_ids = tuple(
            wizard_live.tool_list.topLevelItem(index).data(
                0, Qt.ItemDataRole.UserRole
            )
            for index in range(wizard_live.tool_list.topLevelItemCount())
        )
        assert str(live_assembly.assembly_id) in row_ids
        assert len(row_ids) == len(set(row_ids))
        assert wizard_live.tool_list.topLevelItemCount() == before_rows + 1
        wizard_live.reject()

        operation_parameters = operation.parameters
        current_b = next(
            item
            for item in service.cam_snapshot.tool_definitions
            if item.tool_id == duplicate.tool_id
        )
        service.execute_cam_command(
            lambda app: app.update_tool_common_defaults(
                current_b.tool_id,
                ToolCommonDefaults(quality_profile="balanced"),
                expected_configuration_revision=current_b.configuration_revision,
            )
        )
        preserved = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
        assert preserved.parameters == operation_parameters
        assert preserved.tool_assembly == operation.tool_assembly

        root = project.root_path
        stable_ids = tuple(
            item.tool_id for item in service.cam_snapshot.tool_definitions
        )
        assembly_ids = tuple(
            item.assembly_id for item in service.cam_snapshot.tool_assemblies
        )
        operation_id = preserved.operation_id
        service.save()
        assert ProjectDatabase().current_schema_version(root / "project.db") == 5
        service.close_project(discard_changes=True)
        reopened = service.open_project(root)
        assert tuple(item.tool_id for item in reopened.cam_snapshot.tool_definitions) == stable_ids
        assert tuple(item.assembly_id for item in reopened.cam_snapshot.tool_assemblies) == assembly_ids
        restored = reopened.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
        assert restored.operation_id == operation_id
        assert restored.parameters == operation_parameters
        assert restored.tool_assembly == operation.tool_assembly
        assert ProjectDatabase().current_schema_version(root / "project.db") == 5
    finally:
        if service.current_project is not None:
            service.close_project(discard_changes=True)
        workspace.close()
