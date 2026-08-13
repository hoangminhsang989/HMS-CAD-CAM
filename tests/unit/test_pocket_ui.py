"""Stage 7B.5.4 Pocket UI and project persistence integration tests."""

from types import SimpleNamespace
import gc

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContourBounds,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileSource,
    ContourSegment,
    DirtyReason,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    LengthUnit,
    OccurrenceTransformProvenance,
    PocketBoundary,
    PocketRegion,
    PocketStrategy,
    Point3,
    ProfileProvenance,
    ResolvedPocketGeometry,
    Revision,
    Vector3,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.localization import operation_display_name

IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class _Viewer:
    def __init__(self) -> None:
        self.displayed = []
        self.cleared = 0
        self.removed = []
        self.visibility = []

    def display_toolpath(self, artifact) -> bool:
        self.displayed.append(artifact)
        return True

    def clear_toolpaths(self) -> None:
        self.cleared += 1

    def remove_toolpath(self, operation_id) -> None:
        self.removed.append(operation_id)

    def set_toolpath_visibility(self, operation_id, visible: bool) -> None:
        self.visibility.append((operation_id, visible))


def _reference(source_id, *, hint: str) -> GeometryReference:
    selector = f"hms_profile_v1:{'a' * 64}:face:{GeometryFingerprint.from_payload({'hint': hint}).digest}"
    return GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}),
        Revision(0),
        hint=hint,
        subshape_selector=selector,
    )


def _region(reference: GeometryReference) -> PocketRegion:
    unit = LengthUnit.MM
    points = tuple(Point3(x, y, 50, unit) for x, y in (
        (0, 0), (40, 0), (40, 30), (0, 30),
    ))
    loop = ContourLoop(tuple(
        ContourSegment(ContourCurveKind.LINE, points[index], points[(index + 1) % 4])
        for index in range(4)
    ), ContourOrientation.COUNTERCLOCKWISE)
    bounds = ContourBounds(Point3(0, 0, 50, unit), Point3(40, 30, 50, unit))
    return PocketRegion(
        reference,
        PocketBoundary(loop, unit),
        points[0],
        Vector3(1, 0, 0),
        Vector3(0, 1, 0),
        Vector3(0, 0, 1),
        bounds,
        unit,
        GeometryFingerprint.from_payload({"loop": loop.to_dict(), "hint": reference.hint}),
        ProfileProvenance(
            ContourProfileSource.PLANAR_FACE_OUTER,
            OccurrenceTransformProvenance(reference.occurrence_path, IDENTITY),
        ),
    )


def _find_item(item: QTreeWidgetItem, text: str) -> QTreeWidgetItem | None:
    if item.text(0) == text:
        return item
    for index in range(item.childCount()):
        found = _find_item(item.child(index), text)
        if found is not None:
            return found
    return None


def _workspace(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Pocket UI", source)
    source_id = session.manifest.source_files[0].source_id
    selected = {"reference": _reference(source_id, hint="pocket-a")}
    viewer = _Viewer()
    workspace = CamWorkspace(
        service,
        lambda: source_id,
        toolpath_display=viewer.display_toolpath,
        toolpath_clear=viewer.clear_toolpaths,
        toolpath_remove=viewer.remove_toolpath,
        contour_pick_provider=lambda: selected["reference"],
        profile_resolver=lambda _reference: SimpleNamespace(
            status=GeometryResolutionStatus.RESOLVED,
        ),
        pocket_resolver=lambda reference: ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED, _region(reference),
        ),
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_pocket_operation()
    return service, session, workspace, viewer, selected


def test_pocket_ui_create_edit_bind_generate_status_and_draft_lifecycle(
    tmp_path, monkeypatch,
) -> None:
    service, _session, workspace, viewer, selected = _workspace(tmp_path)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.strategy_key == "pocket_2_5d"
    assert operation.geometry_inputs == ()
    assert not workspace.actions["generate"].isEnabled()
    assert "THIẾU BIÊN DẠNG" in workspace.editor.status.text()

    workspace.pick_geometry()
    assert "RESOLVED" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    workspace.editor._submit()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert len(operation.geometry_inputs) == 1
    assert operation.geometry_inputs[0].reference == selected["reference"]
    assert operation.artifact_state.status is ArtifactStatus.DIRTY
    assert workspace.actions["generate"].isEnabled()

    before_invalid = service.cam_snapshot
    workspace.editor._pocket_fields["stepdown"].setText("0")
    workspace.editor._submit()
    assert service.cam_snapshot == before_invalid
    assert "hợp lệ" in workspace.editor.error.text()
    assert not workspace.actions["generate"].isEnabled()

    workspace.editor._pocket_fields["stepdown"].setText("1")
    workspace.editor.tool.setCurrentIndex(-1)
    before_missing_tool = service.cam_snapshot
    workspace.editor._submit()
    assert service.cam_snapshot == before_missing_tool
    assert "cụm Tool" in workspace.editor.error.text()
    workspace.editor.tool.setCurrentIndex(0)
    original_execute = service.execute_cam_command
    workspace.editor._pocket_fields["bottom"].setText("48.25")
    before_command_error = service.cam_snapshot
    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected mutation error")),
    )
    workspace.editor._submit()
    assert service.cam_snapshot == before_command_error
    assert workspace.editor._pocket_fields["bottom"].text() == "48.25"
    assert "injected mutation error" in workspace.editor.error.text()
    monkeypatch.setattr(service, "execute_cam_command", original_execute)
    workspace.editor._pocket_fields["bottom"].setText("48.5")
    job_item = workspace.tree.topLevelItem(0)
    pocket_item = _find_item(job_item, "Phay hốc 2.5D")
    assert pocket_item is not None
    workspace.tree.setCurrentItem(job_item)
    workspace.tree.setCurrentItem(pocket_item)
    assert workspace.editor._pocket_fields["bottom"].text() == "48.5"
    assert not workspace.actions["generate"].isEnabled()
    workspace.editor._submit()
    committed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert dict(committed.parameters.values)["bottom_z"] == 48.5

    workspace.generate_selected()
    assert viewer.displayed
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].artifact_state.status is ArtifactStatus.VALID
    assert "pocket_2_5d" in workspace.editor.toolpath_metadata.text()
    first_display_count = len(viewer.displayed)
    workspace.generate_selected()
    assert len(viewer.displayed) == first_display_count + 1

    workspace.toggle_toolpath_visibility()
    workspace.toggle_toolpath_visibility()
    assert viewer.visibility[-2:] == [
        (operation.operation_id, False),
        (operation.operation_id, True),
    ]

    workspace._profile_resolver = lambda _reference: SimpleNamespace(
        status=GeometryResolutionStatus.STALE,
    )
    workspace.cad_context_changed()
    stale_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert stale_operation.artifact_state.status is ArtifactStatus.DIRTY
    assert "ĐÃ LỖI THỜI/KHÔNG HỢP LỆ" in workspace.editor.status.text()
    workspace._profile_resolver = lambda _reference: SimpleNamespace(
        status=GeometryResolutionStatus.RESOLVED,
    )

    replacement = _reference(selected["reference"].source_id, hint="pocket-b")
    selected["reference"] = replacement
    workspace.pick_geometry()
    workspace.editor._submit()
    rebound = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert rebound.geometry_inputs[0].reference == replacement
    assert DirtyReason.GEOMETRY_CHANGED in rebound.artifact_state.dirty_reasons

    workspace.clear_geometry_pick()
    cleared = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert cleared.geometry_inputs == ()
    assert "THIẾU BIÊN DẠNG" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    workspace.deleteLater()


def test_r266_native_rest_pocket_ui_creation_editor_and_fresh_reopen(
    tmp_path,
) -> None:
    service, session, workspace, _viewer, selected = _workspace(tmp_path)
    # Replace the normal Pocket created by the shared fixture with a fresh Setup
    # so this proof has exactly one Rest operation and no console-only insertion.
    first = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    service.execute_cam_command(lambda app: app.update_tree(
        service.cam_snapshot.active_job_id,
        first.setup_id,
        lambda tree: tree.remove_node(first.node_id),
    ))
    workspace.refresh()
    rest_action = workspace.actions["rest_pocket_operation"]
    assert rest_action.text() == "Thêm Phay hốc phần dư 3 trục"
    assert sum(action is rest_action for action in workspace.toolbar.actions()) == 1

    rest_action.trigger()
    operations = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(operations) == 1
    operation = operations[0]
    assert operation.strategy_key == "rest_pocket_3axis"
    assert operation.geometry_inputs == ()
    assert operation_display_name(
        "", strategy_key=operation.strategy_key,
    ) == "Phay hốc phần dư 3 trục"

    workspace.pick_geometry()
    workspace.editor._submit()
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert applied.strategy_key == "rest_pocket_3axis"
    assert applied.geometry_inputs[0].reference == selected["reference"]
    production = workspace.production_function_editor_session()
    assert production is not None
    assert production.schema.editor_id == "rest_pocket_production_r266"
    assert str(production.schema.strategy) == "rest_pocket_3axis_r266"
    assert production.schema.field("material_state_source").label == "Nguồn phần dư"
    assert production.schema.field("material_state_status").label == "Trạng thái phần dư"
    assert production.schema.field("lead_in_length").label == "Chiều dài Lead-In"
    assert production.applied_mapping()["material_state_source"] == "Tự động xác định"
    assert production.applied_mapping()["material_state_status"] == (
        "Không tìm thấy nguồn phần dư phù hợp"
    )
    edited = dict(production.applied_mapping())
    edited["lead_in_length"] = "1.25"
    production.apply_callback(edited)
    updated = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert updated.strategy_key == "rest_pocket_3axis"
    assert dict(updated.parameters.values)["lead_in_length"] == 1.25

    service.save()
    project_root = session.root_path
    service.close_project()
    workspace.deleteLater()
    del service, workspace
    gc.collect()

    reopened = ProjectService.create_default(tmp_path / "config-rest-reopen")
    reopened.open_project(project_root)
    restored_operations = (
        reopened.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    )
    assert len(restored_operations) == 1
    restored = restored_operations[0]
    assert restored.operation_id == operation.operation_id
    assert restored.strategy_key == "rest_pocket_3axis"
    assert restored.geometry_inputs == applied.geometry_inputs
    assert dict(restored.parameters.values)["lead_in_length"] == 1.25
    reopened.close_project()


def test_pocket_ui_persistence_save_as_autosave_recovery_and_stale_display(
    tmp_path, monkeypatch,
) -> None:
    service, session, workspace, viewer, selected = _workspace(tmp_path)
    workspace.pick_geometry()
    workspace.editor._submit()
    workspace.generate_selected()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    parameters = operation.parameters
    geometry_input = operation.geometry_inputs[0]
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None and service.is_dirty

    service.save()
    assert not service.is_dirty
    root = session.root_path
    service.close_project()
    assert not service.has_project
    reopened = service.open_project(root)
    assert not reopened.is_dirty
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert restored.parameters == parameters
    assert restored.geometry_inputs == (geometry_input,)
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert restored.artifact_state.token is None
    assert service.load_toolpath_artifact(restored.operation_id) == artifact

    copied = service.save_as(tmp_path, "Pocket UI Copy")
    copied_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert copied_operation.parameters == parameters
    assert copied_operation.geometry_inputs == (geometry_input,)
    assert service.load_toolpath_artifact(copied_operation.operation_id) == artifact
    workspace.bind_project(copied)
    pocket_item = _find_item(workspace.tree.topLevelItem(0), "Phay hốc 2.5D")
    assert pocket_item is not None
    workspace.tree.setCurrentItem(pocket_item)

    workspace.editor._pocket_fields["bottom"].setText("48")
    workspace.editor._submit()
    autosaved_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert dict(autosaved_operation.parameters.values)["bottom_z"] == 48.0
    assert service.is_dirty and service.autosave() is not None

    opener = ProjectService.create_default(tmp_path / "recovery-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(copied.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    recovered_operation = opener.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert dict(recovered_operation.parameters.values)["bottom_z"] == 48.0
    assert recovered_operation.geometry_inputs == (geometry_input,)
    assert recovered_operation.artifact_state.token is None
    assert not recovered.is_dirty

    workspace._generation = service.cam_generation
    workspace.bind_project(service.current_project)
    pocket_item = _find_item(workspace.tree.topLevelItem(0), "Phay hốc 2.5D")
    assert pocket_item is not None
    workspace.tree.setCurrentItem(pocket_item)
    selected["reference"] = geometry_input.reference
    workspace._picked_reference = geometry_input.reference
    workspace._picked_reference_resolved = True
    workspace.editor._pocket_fields["bottom"].setText("48")
    workspace.editor._submit()
    original_compute = service.compute_pocket

    def stale_compute(*args, **kwargs):
        result = original_compute(*args, **kwargs)
        workspace._generation += 1
        return result

    monkeypatch.setattr(service, "compute_pocket", stale_compute)
    displayed_before_stale = len(viewer.displayed)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before_stale
    assert "lỗi thời" in workspace.editor.error.text().lower()

    cad_only = ProjectService.create_default(tmp_path / "cad-only-config")
    cad_session = cad_only.new_project(tmp_path, "CAD Only")
    workspace._service = cad_only
    workspace.bind_project(cad_session)
    assert workspace.tree.topLevelItem(0).text(0) == "Chưa có công việc CAM"
    assert viewer.cleared > 0
    cad_only.close_project()
    workspace.bind_project(None)
    assert workspace.tree.topLevelItem(0).text(0) == "Chưa mở dự án CAM"
    workspace.deleteLater()


def test_pocket_generation_failure_keeps_diagnostic_and_failed_status(tmp_path) -> None:
    service, _session, workspace, _viewer, _selected = _workspace(tmp_path)
    workspace.pick_geometry()
    workspace.editor._pocket_fields["allowance"].setText("20")
    workspace.editor._submit()
    assert workspace.actions["generate"].isEnabled()

    workspace.generate_selected()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.artifact_state.status is ArtifactStatus.FAILED
    assert workspace.editor.error.text()
    pocket_item = _find_item(workspace.tree.topLevelItem(0), "Phay hốc 2.5D")
    assert pocket_item is not None and "Thất bại" in pocket_item.text(1)
    workspace.deleteLater()


def test_clear_profile_mutation_failure_restores_committed_ui(
    tmp_path, monkeypatch,
) -> None:
    service, _session, workspace, _viewer, selected = _workspace(tmp_path)
    workspace.pick_geometry()
    workspace.editor._submit()
    before = service.cam_snapshot

    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected clear failure")
        ),
    )
    workspace.clear_geometry_pick()

    assert service.cam_snapshot == before
    assert workspace._picked_reference == selected["reference"]
    assert workspace._picked_reference_resolved
    assert "RESOLVED" in workspace.editor.status.text()
    assert workspace.actions["generate"].isEnabled()
    assert "injected clear failure" in workspace.editor.error.text()
    workspace.deleteLater()
