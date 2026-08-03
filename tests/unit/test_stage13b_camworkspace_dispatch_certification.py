"""Stage 13B certification against the real selected CAM operation tree."""
from __future__ import annotations

from hms_cadcam.ai_assist.production_draft_bridge import FacingEditorDraftBridge
from hms_cadcam.ai_assist.selective_apply import ApplyOwnership, SelectiveApplyService
from hms_cadcam.cam.domain import FacingParameters
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace, _ID_ROLE, _KIND_ROLE
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.model import FunctionEditorAction
from test_drilling_ui import _workspace as drilling_workspace
from PySide6.QtCore import QCoreApplication, QEvent


def _facing_workspace(tmp_path):
    """Use the normal production creation/rebuild route before measurement."""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "facing-existing.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    project = service.create_project_from_source(tmp_path, "Stage13B Facing", source)
    workspace = CamWorkspace(service, lambda: project.manifest.source_files[0].source_id)
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    # This command has completed before the certification spies are armed.
    workspace.add_operation()
    workspace.refresh()
    return service, project, workspace


def _state(session):
    return FunctionEditorDraftState(
        session.schema,
        session.applied_mapping(),
        project_key=session.project_key,
        operation_key=session.operation_key,
        generation=session.generation,
        validation_callback=session.validation_callback,
    )


def _dispose_host(host) -> None:
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_stage13b_facing_session_is_constructed_from_existing_selected_production_operation(tmp_path):
    service, project, workspace = _facing_workspace(tmp_path)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    item = workspace.tree.currentItem()
    assert item is not None
    assert item.data(0, _KIND_ROLE) == "operation"
    assert item.data(0, _ID_ROLE) == str(operation.node_id)

    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.project_key == str(project.manifest.project_id)
    assert session.operation_key == str(operation.operation_id)
    assert session.schema.editor_id == "facing_production_9a5_1"
    values = session.applied_mapping()
    context = next(
        cell.cell_contents for cell in session.apply_callback.__closure__
        if type(cell.cell_contents).__name__ == "FacingEditorContext"
    )
    bridge = FacingEditorDraftBridge.from_context(
        context, _state(session), project_id=str(project.manifest.project_id)
    )
    assert bridge.read_advisor_inputs()["diameter_mm"] > 0
    assert FacingParameters.from_operation_parameters(operation.parameters).unit.value == "mm"

    rebuilt = workspace.production_function_editor_session()
    assert rebuilt is not None
    assert rebuilt.applied_values == session.applied_values
    assert rebuilt.selection_key == session.selection_key
    workspace.close()


def test_stage13b_facing_validation_selective_apply_and_normal_apply_have_separate_boundaries(tmp_path, monkeypatch):
    service, project, workspace = _facing_workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    state = _state(session)
    operation_before = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    calls = {"validate": 0, "apply": 0, "command": 0}
    original_validate = workspace._validate_facing_production
    original_apply = workspace._apply_facing_production
    original_command = service.execute_cam_command

    def validate(*args, **kwargs):
        calls["validate"] += 1
        return original_validate(*args, **kwargs)

    def apply(*args, **kwargs):
        calls["apply"] += 1
        return original_apply(*args, **kwargs)

    def command(*args, **kwargs):
        calls["command"] += 1
        return original_command(*args, **kwargs)

    monkeypatch.setattr(workspace, "_validate_facing_production", validate)
    monkeypatch.setattr(workspace, "_apply_facing_production", apply)
    monkeypatch.setattr(service, "execute_cam_command", command)

    # Production validation uses the normal session callback and is non-persistent.
    assert session.validation_callback(session.applied_mapping()) == ()
    assert calls == {"validate": 1, "apply": 0, "command": 0}
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0] == operation_before

    bridge = FacingEditorDraftBridge.from_context(
        # The concrete context is held by the production callback closure; use
        # the same session state for the draft-only bridge contract.
        next(cell.cell_contents for cell in session.apply_callback.__closure__
             if type(cell.cell_contents).__name__ == "FacingEditorContext"),
        state,
        project_id=str(project.manifest.project_id),
    )
    owner = ApplyOwnership(str(project.manifest.project_id), bridge.editor_identity(), bridge.operation_identity(), type(bridge).__name__, session.generation, "cutting-parameters-v1", "input", bridge.current_revision_or_digest())
    result = SelectiveApplyService().apply(bridge, owner, {"spindle_speed": 1200.0, "feed_rate": 500.0}, frozenset({"spindle_speed"}))
    assert result.status == "APPLIED"
    assert state.values["feed_rate"] == session.applied_mapping()["feed_rate"]
    assert calls == {"validate": 1, "apply": 0, "command": 0}

    # The actual shared Function Editor Apply control is the sole persistence
    # boundary; it invokes FunctionEditorDraftState.apply internally.
    host = FunctionEditorHost(workspace.editor, workspace.tree, lambda: None,
                              production_provider=workspace.production_function_editor_session,
                              selection_exists=workspace.selection_exists)
    assert host.active_page is not None
    host.active_page._field_changed("stepover", "4.0")
    host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
    assert calls["apply"] == 1
    assert calls["command"] == 1
    updated = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert updated.operation_id == operation_before.operation_id
    assert FacingParameters.from_operation_parameters(updated.parameters).stepover.value == 4.0
    _dispose_host(host)
    workspace.close()


def test_stage13b_drilling_selected_tree_normal_editor_apply_is_one_atomic_command(tmp_path, monkeypatch):
    service, project, workspace, _viewer, _selected = drilling_workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.schema.editor_id.startswith("drilling")
    operation_before = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    calls = {"command": 0, "apply": 0}
    original_command = service.execute_cam_command
    original_apply = workspace._apply_drilling_family_production

    def command(*args, **kwargs):
        calls["command"] += 1
        return original_command(*args, **kwargs)

    def apply(*args, **kwargs):
        calls["apply"] += 1
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(service, "execute_cam_command", command)
    monkeypatch.setattr(workspace, "_apply_drilling_family_production", apply)
    host = FunctionEditorHost(workspace.editor, workspace.tree, lambda: None,
                              production_provider=workspace.production_function_editor_session,
                              selection_exists=workspace.selection_exists)
    assert host.active_page is not None
    host.active_page._field_changed("feed_rate", "0.08")
    host.active_page.footer.buttons[FunctionEditorAction.APPLY].click()
    assert calls == {"command": 1, "apply": 1}
    changed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert changed.operation_id == operation_before.operation_id
    assert changed.parameters != operation_before.parameters
    assert host.active_session is not None
    assert host.active_session.project_key == str(project.manifest.project_id)
    _dispose_host(host)
    workspace.close()
