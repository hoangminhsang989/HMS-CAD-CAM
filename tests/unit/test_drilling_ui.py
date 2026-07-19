"""Stage 7B.6.4 Drilling UI and project lifecycle integration tests."""

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillGeometryInput,
    DrillingCycle,
    DrillingRegion,
    DrillingStrategy,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionStatus,
    HoleLocation,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    LengthUnit,
    Point3,
    ResolvedDrillingGeometry,
    Revision,
    ToolFamily,
    ValidationDiagnostic,
    Vector3,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace


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
    selector = f"vertex:{GeometryFingerprint.from_payload({'hint': hint}).digest}"
    return GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id,
        GeometryReferenceKind.VERTEX,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}),
        Revision(0),
        hint=hint,
        subshape_selector=selector,
    )


def _hole(source_id, *, hint: str, x: float = 4.0) -> HoleReference:
    return HoleReference(
        _reference(source_id, hint=hint),
        Vector3(0, 0, 1),
        Point3(x, 7, 0, LengthUnit.MM),
        LengthUnit.MM,
    )


def _resolved(geometry, depth) -> ResolvedDrillingGeometry:
    source = geometry.source
    if not isinstance(source, HoleReference):
        raise TypeError("test resolver requires a HoleReference")
    location = HoleLocation(
        source.plane_origin,
        source.axis,
        source.plane_origin,
        None,
        source.unit,
        HoleSourceKind.BREP_VERTEX,
        source,
    )
    pattern = HolePattern((location,), source.unit)
    region = DrillingRegion(
        DrillGeometryInput(source, source.unit),
        pattern,
        depth,
        source.unit,
        GeometryFingerprint.from_payload({"hole": source.to_dict()}),
    )
    return ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)


def _find_item(item: QTreeWidgetItem, text: str) -> QTreeWidgetItem | None:
    if item.text(0) == text:
        return item
    for index in range(item.childCount()):
        found = _find_item(item.child(index), text)
        if found is not None:
            return found
    return None


def _select_tool_family(workspace: CamWorkspace, family: ToolFamily) -> None:
    snapshot = workspace._service.cam_snapshot
    tool_ids = {
        tool.tool_id for tool in snapshot.tool_definitions if tool.family is family
    }
    assembly = next(
        value for value in snapshot.tool_assemblies if value.tool_id in tool_ids
    )
    index = workspace.editor.tool.findData(str(assembly.assembly_id))
    assert index >= 0
    workspace.editor.tool.setCurrentIndex(index)


def _workspace(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Drilling UI", source)
    source_id = session.manifest.source_files[0].source_id
    selected = {"hole": _hole(source_id, hint="hole-a")}
    viewer = _Viewer()
    workspace = CamWorkspace(
        service,
        lambda: source_id,
        toolpath_display=viewer.display_toolpath,
        toolpath_clear=viewer.clear_toolpaths,
        toolpath_remove=viewer.remove_toolpath,
        drilling_pick_provider=lambda _axis: selected["hole"],
        drilling_resolver=_resolved,
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_drilling_operation()
    return service, session, workspace, viewer, selected


def test_drilling_ui_create_edit_bind_cycles_generate_and_diagnostics(
    tmp_path, monkeypatch,
) -> None:
    service, _session, workspace, viewer, selected = _workspace(tmp_path)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.strategy_key == "drilling_v1"
    assert operation.geometry_inputs[0].reference == selected["hole"].reference
    assert workspace.actions["generate"].isEnabled()
    assert "RESOLVED" in workspace.editor.status.text()

    before_invalid = service.cam_snapshot
    workspace.editor._drilling_fields["depth"].setText("0")
    workspace.editor._submit()
    assert service.cam_snapshot == before_invalid
    assert workspace.editor.error.text()
    assert not workspace.actions["generate"].isEnabled()

    workspace.editor._drilling_fields["depth"].setText("4")
    workspace.editor._drilling_fields["dwell"].setText("0.25")
    workspace.editor._submit()
    committed = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    strategy = DrillingStrategy.from_operation_parameters(committed.parameters)
    assert strategy.depth.depth.value == 4
    assert strategy.dwell_seconds == 0.25
    assert DirtyReason.PARAMETERS_CHANGED in committed.artifact_state.dirty_reasons

    workspace.editor.drilling_cycle.setCurrentText(DrillingCycle.SPOT_DRILL.value)
    _select_tool_family(workspace, ToolFamily.CENTER_DRILL)
    workspace.editor._submit()
    spot = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert DrillingStrategy.from_operation_parameters(spot.parameters).cycle is DrillingCycle.SPOT_DRILL
    workspace.generate_selected()
    assert spot.operation_id == service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].operation_id
    assert viewer.displayed

    workspace.editor.drilling_cycle.setCurrentText(DrillingCycle.PECK_DRILL.value)
    workspace.editor._drilling_fields["peck"].setText("1")
    _select_tool_family(workspace, ToolFamily.DRILL)
    workspace.editor._submit()
    workspace.generate_selected()
    peck = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert peck.artifact_state.status is ArtifactStatus.VALID
    assert DrillingStrategy.from_operation_parameters(peck.parameters).cycle is DrillingCycle.PECK_DRILL
    assert "drilling_v1" in workspace.editor.toolpath_metadata.text()
    first_display_count = len(viewer.displayed)
    workspace.generate_selected()
    assert len(viewer.displayed) == first_display_count + 1

    workspace.toggle_toolpath_visibility()
    workspace.toggle_toolpath_visibility()
    assert viewer.visibility[-2:] == [
        (peck.operation_id, False),
        (peck.operation_id, True),
    ]

    replacement = _hole(selected["hole"].reference.source_id, hint="hole-b", x=12)
    selected["hole"] = replacement
    workspace.pick_geometry()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].geometry_inputs[0].reference != replacement.reference
    workspace.editor._submit()
    rebound = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert rebound.geometry_inputs[0].reference == replacement.reference
    assert DirtyReason.GEOMETRY_CHANGED in rebound.artifact_state.dirty_reasons

    before_clear = service.cam_snapshot
    original_execute = service.execute_cam_command
    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("clear rollback")),
    )
    workspace.clear_geometry_pick()
    assert service.cam_snapshot == before_clear
    assert workspace._picked_hole_reference == replacement
    assert "clear rollback" in workspace.editor.error.text()
    monkeypatch.setattr(service, "execute_cam_command", original_execute)

    workspace.clear_geometry_pick()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].geometry_inputs == ()
    assert "HOLE MISSING" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    selected["hole"] = replacement
    workspace.pick_geometry()
    workspace.editor._submit()
    workspace.generate_selected()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].artifact_state.status is ArtifactStatus.VALID

    stale = ResolvedDrillingGeometry(
        GeometryResolutionStatus.STALE,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR,
            DiagnosticCode.DRILL_GEOMETRY_STALE,
            "hole reference stale",
        ),),
    )
    workspace._drilling_resolver = lambda _geometry, _depth: stale
    workspace.cad_context_changed()
    assert "STALE/INVALID" in workspace.editor.status.text()
    assert service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].artifact_state.status is ArtifactStatus.DIRTY
    workspace.deleteLater()


def test_drilling_persistence_save_as_autosave_recovery_and_stale_callback(
    tmp_path, monkeypatch,
) -> None:
    service, session, workspace, viewer, _selected = _workspace(tmp_path)
    workspace.generate_selected()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    parameters = operation.parameters
    geometry_input = operation.geometry_inputs[0]
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None

    service.save()
    root = session.root_path
    service.close_project()
    reopened = service.open_project(root)
    assert not reopened.is_dirty
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    restored_strategy = DrillingStrategy.from_operation_parameters(restored.parameters)
    assert restored.parameters == parameters
    assert restored.geometry_inputs == (geometry_input,)
    assert isinstance(restored_strategy.geometry.source, HoleReference)
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert restored.artifact_state.token is None
    assert service.load_toolpath_artifact(restored.operation_id) == artifact

    copied = service.save_as(tmp_path, "Drilling UI Copy")
    copied_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert copied_operation.parameters == parameters
    assert copied_operation.geometry_inputs == (geometry_input,)
    assert service.load_toolpath_artifact(copied_operation.operation_id) == artifact
    workspace.bind_project(copied)
    drilling_item = _find_item(workspace.tree.topLevelItem(0), "Drilling")
    assert drilling_item is not None
    workspace.tree.setCurrentItem(drilling_item)

    workspace.editor._drilling_fields["depth"].setText("3")
    workspace.editor._submit()
    autosaved = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert DrillingStrategy.from_operation_parameters(autosaved.parameters).depth.depth.value == 3
    assert service.is_dirty and service.autosave() is not None

    opener = ProjectService.create_default(tmp_path / "recovery-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(copied.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    recovered_operation = opener.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert DrillingStrategy.from_operation_parameters(recovered_operation.parameters).depth.depth.value == 3
    assert recovered_operation.geometry_inputs == (geometry_input,)
    assert recovered_operation.artifact_state.token is None
    assert not recovered.is_dirty

    workspace.bind_project(service.current_project)
    drilling_item = _find_item(workspace.tree.topLevelItem(0), "Drilling")
    assert drilling_item is not None
    workspace.tree.setCurrentItem(drilling_item)
    original_compute = service.compute_drilling

    def stale_compute(*args, **kwargs):
        result = original_compute(*args, **kwargs)
        workspace._generation += 1
        return result

    monkeypatch.setattr(service, "compute_drilling", stale_compute)
    displayed_before = len(viewer.displayed)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    assert "stale" in workspace.editor.error.text().lower()

    cad_only = ProjectService.create_default(tmp_path / "cad-only-config")
    cad_session = cad_only.new_project(tmp_path, "CAD Only")
    workspace._service = cad_only
    workspace.bind_project(cad_session)
    assert workspace.tree.topLevelItem(0).data(0, 0x0100) is None
    assert viewer.cleared > 0
    cad_only.close_project()
    workspace.bind_project(None)
    assert workspace.tree.topLevelItemCount() == 1
    workspace.deleteLater()
