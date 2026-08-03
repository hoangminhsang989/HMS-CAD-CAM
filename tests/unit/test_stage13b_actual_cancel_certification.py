"""Stage 13B close-without-apply reconstruction tests for production sessions."""
from __future__ import annotations

from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from test_stage13b_camworkspace_dispatch_certification import _facing_workspace
from test_drilling_ui import _workspace as drilling_workspace
from hms_cadcam.ui.cam_ui import CamWorkspace
from PySide6.QtCore import QCoreApplication, QEvent


def _state(session):
    return FunctionEditorDraftState(session.schema, session.applied_mapping(), project_key=session.project_key, operation_key=session.operation_key, generation=session.generation, validation_callback=session.validation_callback)


def _dispose_host(host) -> None:
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_stage13b_facing_actual_editor_cancel_reconstructs_persisted_values(tmp_path, monkeypatch):
    service, _project, workspace = _facing_workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    before = session.applied_mapping()
    commands = []
    monkeypatch.setattr(service, "execute_cam_command", lambda *args, **kwargs: commands.append((args, kwargs)))
    host = FunctionEditorHost(
        workspace.editor, workspace.tree, lambda: None,
        production_provider=workspace.production_function_editor_session,
        selection_exists=workspace.selection_exists,
        switch_confirmation=lambda _state: "discard",
    )
    assert host.active_page is not None
    monkeypatch.setattr(host.active_page, "_close_confirmation", lambda _state: True)
    host.active_page._field_changed("stepover", "3.5")
    # The shared Function Editor's real close route discards its draft page.
    assert host.request_close()
    assert commands == []
    # The source project snapshot has never crossed the command boundary.
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].operation_id == service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].operation_id
    operation_id = session.selection_key[1]
    recreated = CamWorkspace(service, lambda: _project.manifest.source_files[0].source_id)
    recreated.refresh(("operation", operation_id))
    reconstructed = recreated.production_function_editor_session()
    assert reconstructed is not None
    assert reconstructed.applied_mapping() == before
    _dispose_host(host)
    recreated.close()
    workspace.close()


def test_stage13b_drilling_actual_editor_cancel_keeps_project_operation_and_reopens_session(tmp_path, monkeypatch):
    service, _project, workspace, _viewer, _selected = drilling_workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    original = session.applied_mapping()
    calls = []
    monkeypatch.setattr(service, "execute_cam_command", lambda *args, **kwargs: calls.append(args))
    host = FunctionEditorHost(
        workspace.editor, workspace.tree, lambda: None,
        production_provider=workspace.production_function_editor_session,
        selection_exists=workspace.selection_exists,
        switch_confirmation=lambda _state: "discard",
    )
    assert host.active_page is not None
    monkeypatch.setattr(host.active_page, "_close_confirmation", lambda _state: True)
    host.active_page._field_changed("feed_rate", "0.1")
    assert host.request_close()
    assert calls == []
    # Rebuild uses the persisted snapshot; a new workspace session still has the
    # original applied mapping because draft state never calls apply_callback.
    reopened = workspace.production_function_editor_session()
    assert reopened is not None
    assert reopened.applied_mapping() == original
    _dispose_host(host)
    workspace.close()
