"""Stage 7B.7.3 Tapping UI, persistence and lifecycle integration tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillGeometryInput,
    DrillingRegion,
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
    Length,
    LengthUnit,
    MachineDefinitionId,
    OperationCapability,
    Point3,
    ResolvedDrillingGeometry,
    Revision,
    TapGeometry,
    TappingHand,
    TappingStrategy,
    TappingSynchronizationPolicy,
    ToolFamily,
    ToolHand,
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


def _reference(
    source_id,
    *,
    hint: str,
    kind: GeometryReferenceKind = GeometryReferenceKind.VERTEX,
    occurrence_path: str | None = None,
) -> GeometryReference:
    selector_kind = "vertex" if kind is GeometryReferenceKind.VERTEX else "edge"
    selector = (
        f"{selector_kind}:"
        f"{GeometryFingerprint.from_payload({'hint': hint}).digest}"
    )
    return GeometryReference(
        GeometryReferenceId.new(),
        HMS_GEOMETRY_REFERENCE_SCHEME,
        HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id,
        kind,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({
            "selector": selector,
            "occurrence": occurrence_path,
        }),
        Revision(0),
        occurrence_path=occurrence_path,
        hint=hint,
        subshape_selector=selector,
    )


def _hole(
    source_id,
    *,
    hint: str,
    x: float,
    kind: GeometryReferenceKind = GeometryReferenceKind.VERTEX,
    occurrence_path: str | None = None,
) -> HoleReference:
    return HoleReference(
        _reference(
            source_id,
            hint=hint,
            kind=kind,
            occurrence_path=occurrence_path,
        ),
        Vector3(0, 0, 1),
        Point3(x, 7, 0, LengthUnit.MM),
        LengthUnit.MM,
    )


def _location(hole: HoleReference) -> HoleLocation:
    is_edge = hole.reference.kind is GeometryReferenceKind.EDGE
    return HoleLocation(
        hole.plane_origin,
        hole.axis,
        hole.plane_origin,
        Length(8, hole.unit) if is_edge else None,
        hole.unit,
        (
            HoleSourceKind.CIRCULAR_EDGE
            if is_edge else HoleSourceKind.BREP_VERTEX
        ),
        hole,
    )


def _pattern(*holes: HoleReference) -> HolePattern:
    return HolePattern(tuple(_location(hole) for hole in holes), LengthUnit.MM)


def _explicit_pattern() -> HolePattern:
    axis = Vector3(0, 0, 1)
    return HolePattern(tuple(
        HoleLocation(
            Point3(x, 5, 0, LengthUnit.MM),
            axis,
            Point3(x, 5, 0, LengthUnit.MM),
            None,
            LengthUnit.MM,
            HoleSourceKind.EXPLICIT_POINT,
        )
        for x in (0.0, 12.0)
    ), LengthUnit.MM)


def _resolved(geometry, depth) -> ResolvedDrillingGeometry:
    source = geometry.source
    pattern = (
        source
        if isinstance(source, HolePattern)
        else HolePattern((_location(source),), source.unit)
    )
    region = DrillingRegion(
        DrillGeometryInput(source, source.unit),
        pattern,
        depth,
        source.unit,
        GeometryFingerprint.from_payload({
            "holes": pattern.to_dict(),
        }),
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


def _operation(service: ProjectService):
    return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]


def _select_tap(workspace: CamWorkspace, hand: ToolHand) -> None:
    snapshot = workspace._service.cam_snapshot
    tool_ids = {
        tool.tool_id
        for tool in snapshot.tool_definitions
        if tool.family is ToolFamily.TAP
        and isinstance(tool.cutting_geometry, TapGeometry)
        and tool.cutting_geometry.hand is hand
    }
    assembly = next(
        value for value in snapshot.tool_assemblies if value.tool_id in tool_ids
    )
    index = workspace.editor.tool.findData(str(assembly.assembly_id))
    assert index >= 0
    workspace.editor.tool.setCurrentIndex(index)


def _select_machine(workspace: CamWorkspace, machine_id) -> None:
    index = workspace.editor.machine.findData(str(machine_id))
    assert index >= 0
    workspace.editor.machine.setCurrentIndex(index)


def _workspace(tmp_path, hole_source=None):
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Tapping UI", source)
    source_id = session.manifest.source_files[0].source_id
    selected = {
        "source": hole_source or _pattern(
            _hole(
                source_id,
                hint="vertex-left",
                x=4,
                occurrence_path="root/left",
            ),
            _hole(
                source_id,
                hint="circle-right",
                x=14,
                kind=GeometryReferenceKind.EDGE,
                occurrence_path="root/right",
            ),
        ),
    }
    viewer = _Viewer()
    workspace = CamWorkspace(
        service,
        lambda: source_id,
        toolpath_display=viewer.display_toolpath,
        toolpath_clear=viewer.clear_toolpaths,
        toolpath_remove=viewer.remove_toolpath,
        drilling_pick_provider=lambda _axis: selected["source"],
        drilling_resolver=_resolved,
    )
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.create_basic_tapping_resources()
    workspace.add_tapping_operation()
    return service, session, workspace, viewer, selected


def test_tapping_ui_create_edit_validate_bind_generate_and_rollback(
    tmp_path,
    monkeypatch,
) -> None:
    service, _session, workspace, viewer, selected = _workspace(tmp_path)
    operation = _operation(service)
    strategy = TappingStrategy.from_operation_parameters(operation.parameters)
    assert operation.strategy_key == "tapping_v1"
    assert strategy.hand is TappingHand.RIGHT_HAND_TAP
    assert strategy.synchronization_policy is TappingSynchronizationPolicy.RIGID
    assert len(operation.geometry_inputs) == 2
    assert workspace.actions["generate"].isEnabled()
    assert "HOLE PATTERN 2" in workspace.editor.status.text()

    committed = service.cam_snapshot
    for field, value, diagnostic in (
        ("pitch", "0", "tap.invalid_parameters"),
        ("spindle", "0", "tap.invalid_parameters"),
        ("final", "0", "tap.depth_invalid"),
    ):
        original = workspace.editor._tapping_fields[field].text()
        workspace.editor._tapping_fields[field].setText(value)
        workspace.editor._submit()
        assert service.cam_snapshot == committed
        assert diagnostic in workspace.editor.error.text()
        assert not workspace.actions["generate"].isEnabled()
        workspace.editor._tapping_fields[field].setText(original)

    workspace.editor._tapping_fields["pitch"].setText("1.1")
    workspace.tree.setCurrentItem(workspace.tree.topLevelItem(0))
    tapping_item = _find_item(workspace.tree.topLevelItem(0), "Tapping")
    assert tapping_item is not None
    workspace.tree.setCurrentItem(tapping_item)
    assert workspace.editor._tapping_fields["pitch"].text() == "1.1"
    assert not workspace.actions["generate"].isEnabled()
    workspace.editor._tapping_fields["pitch"].setText("1.25")

    for field, value, diagnostic in (
        ("diameter", "7", "tap.diameter_mismatch"),
        ("pitch", "1", "tap.pitch_mismatch"),
    ):
        original = workspace.editor._tapping_fields[field].text()
        before = _operation(service)
        workspace.editor._tapping_fields[field].setText(value)
        workspace.editor._submit()
        assert _operation(service) == before
        assert diagnostic in workspace.editor.error.text()
        workspace.editor._tapping_fields[field].setText(original)

    before_hand = _operation(service)
    workspace.editor.tapping_hand.setCurrentText(
        TappingHand.LEFT_HAND_TAP.value
    )
    workspace.editor._submit()
    assert _operation(service) == before_hand
    assert "tap.hand_mismatch" in workspace.editor.error.text()
    workspace.editor.tapping_hand.setCurrentText(
        TappingHand.RIGHT_HAND_TAP.value
    )

    tapping_machine = next(
        machine for machine in service.cam_snapshot.machine_definitions
        if OperationCapability.TAPPING in machine.capabilities.operations
    )
    bad_machine = replace(
        tapping_machine,
        machine_id=MachineDefinitionId.new(),
        name="Tapping without sync",
        spindles=tuple(
            replace(spindle, synchronized_feed=False)
            for spindle in tapping_machine.spindles
        ),
    )
    service.execute_cam_command(
        lambda app: app.add_machine_definition(bad_machine)
    )
    workspace.refresh(workspace._selected_key)
    _select_machine(workspace, bad_machine.machine_id)
    before_machine = _operation(service)
    workspace.editor._submit()
    assert _operation(service) == before_machine
    assert "tap.sync_unsupported" in workspace.editor.error.text()
    _select_machine(workspace, tapping_machine.machine_id)

    old_source = workspace._picked_hole_source
    workspace._drilling_pick_provider = lambda _axis: (_ for _ in ()).throw(
        RuntimeError("pick cancelled")
    )
    workspace.pick_geometry()
    assert workspace._picked_hole_source == old_source
    assert "pick cancelled" in workspace.editor.error.text()

    replacement_source = _pattern(
        _hole(
            selected["source"].locations[0].reference.reference.source_id,
            hint="replacement",
            x=22,
            occurrence_path="root/replacement",
        )
    )
    selected["source"] = replacement_source
    workspace._drilling_pick_provider = lambda _axis: selected["source"]
    before_rebind = _operation(service)
    workspace.pick_geometry()
    assert _operation(service) == before_rebind
    workspace.editor._submit()
    rebound = _operation(service)
    assert rebound.geometry_inputs[0].reference == (
        replacement_source.locations[0].reference.reference
    )
    assert DirtyReason.GEOMETRY_CHANGED in rebound.artifact_state.dirty_reasons

    original_execute = service.execute_cam_command
    before_rollback = _operation(service)
    committed_dwell = workspace.editor._tapping_fields["dwell"].text()
    workspace.editor._tapping_fields["dwell"].setText("0.3")
    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("atomic apply rollback")
        ),
    )
    workspace.editor._submit()
    assert _operation(service) == before_rollback
    assert workspace.editor._tapping_fields["dwell"].text() == committed_dwell
    assert "atomic apply rollback" in workspace.editor.error.text()
    monkeypatch.setattr(service, "execute_cam_command", original_execute)

    workspace.editor.tapping_hand.setCurrentText(
        TappingHand.LEFT_HAND_TAP.value
    )
    workspace.editor.tapping_mode.setCurrentText(
        TappingSynchronizationPolicy.FLOATING.value
    )
    _select_tap(workspace, ToolHand.LEFT)
    workspace.editor._tapping_fields["final"].setText("-8")
    workspace.editor._tapping_fields["dwell"].setText("")
    workspace.editor._submit()
    left = TappingStrategy.from_operation_parameters(_operation(service).parameters)
    assert left.hand is TappingHand.LEFT_HAND_TAP
    assert left.synchronization_policy is TappingSynchronizationPolicy.FLOATING
    assert left.final_depth.value == -8
    assert left.dwell_seconds == 0

    workspace.generate_selected()
    generated = _operation(service)
    artifact = service.load_toolpath_artifact(generated.operation_id)
    assert generated.artifact_state.status is ArtifactStatus.VALID
    assert artifact is not None and len(viewer.displayed) > 0
    assert "left_hand_tap" in workspace.editor.toolpath_metadata.text()
    assert "floating" in workspace.editor.toolpath_metadata.text()
    workspace.toggle_toolpath_visibility()
    workspace.toggle_toolpath_visibility()
    assert viewer.visibility[-2:] == [
        (generated.operation_id, False),
        (generated.operation_id, True),
    ]

    previous_artifact = artifact
    previous_display = viewer.displayed[-1]
    stale_geometry = ResolvedDrillingGeometry(
        GeometryResolutionStatus.STALE,
        diagnostics=(ValidationDiagnostic(
            DiagnosticSeverity.ERROR,
            DiagnosticCode.DRILL_GEOMETRY_STALE,
            "tapping reference stale",
        ),),
    )
    workspace._drilling_resolver = lambda _geometry, _depth: stale_geometry
    workspace.generate_selected()
    assert service.load_toolpath_artifact(generated.operation_id) == previous_artifact
    assert viewer.displayed[-1] == previous_display
    assert "tap.geometry_stale" in workspace.editor.error.text()

    workspace._drilling_resolver = _resolved
    workspace.clear_geometry_pick()
    assert _operation(service).geometry_inputs == ()
    assert "HOLE MISSING" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    workspace.deleteLater()


def test_tapping_explicit_pattern_and_stale_callback_are_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    service, _session, workspace, viewer, _selected = _workspace(
        tmp_path,
        _explicit_pattern(),
    )
    operation = _operation(service)
    assert operation.geometry_inputs == ()
    assert workspace.actions["generate"].isEnabled()
    workspace.generate_selected()
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None
    assert sum(
        event.semantic_key == "tap.hole_complete"
        for event in artifact.events
        if hasattr(event, "semantic_key")
    ) == 2
    displayed_before = len(viewer.displayed)
    original_compute = service.compute_tapping

    def stale_compute(*args, **kwargs):
        result = original_compute(*args, **kwargs)
        workspace._generation += 1
        return result

    monkeypatch.setattr(service, "compute_tapping", stale_compute)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    assert "tap.stale_result" in workspace.editor.error.text()

    other = ProjectService.create_default(tmp_path / "other-config")
    other_session = other.new_project(tmp_path, "Other")
    workspace._service = other
    workspace.bind_project(other_session)
    assert viewer.cleared > 0
    assert workspace.tree.topLevelItemCount() == 1
    other.close_project()
    workspace.bind_project(None)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    workspace.deleteLater()


def test_tapping_persistence_save_as_autosave_recovery_and_normalization(
    tmp_path,
) -> None:
    service, session, workspace, _viewer, _selected = _workspace(tmp_path)
    workspace.generate_selected()
    operation = _operation(service)
    strategy = TappingStrategy.from_operation_parameters(operation.parameters)
    geometry_inputs = operation.geometry_inputs
    tool_reference = operation.tool_assembly
    machine_requirement = operation.machine_requirement
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None

    service.save()
    root = session.root_path
    service.close_project()
    reopened = service.open_project(root)
    assert not reopened.is_dirty
    restored = _operation(service)
    assert restored.operation_id == operation.operation_id
    assert restored.parameters == operation.parameters
    assert restored.geometry_inputs == geometry_inputs
    assert restored.tool_assembly == tool_reference
    assert restored.machine_requirement == machine_requirement
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert restored.artifact_state.token is None
    assert service.load_toolpath_artifact(restored.operation_id) == artifact

    copied = service.save_as(tmp_path, "Tapping UI Copy")
    copied_operation = _operation(service)
    assert copied_operation.operation_id == operation.operation_id
    assert TappingStrategy.from_operation_parameters(
        copied_operation.parameters
    ) == strategy
    assert service.load_toolpath_artifact(copied_operation.operation_id) == artifact

    workspace.bind_project(copied)
    tapping_item = _find_item(workspace.tree.topLevelItem(0), "Tapping")
    assert tapping_item is not None
    workspace.tree.setCurrentItem(tapping_item)
    workspace.editor._tapping_fields["dwell"].setText("0.4")
    workspace.editor._submit()
    autosaved_operation = _operation(service)
    assert TappingStrategy.from_operation_parameters(
        autosaved_operation.parameters
    ).dwell_seconds == 0.4
    assert service.is_dirty and service.autosave() is not None

    opener = ProjectService.create_default(tmp_path / "recovery-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(copied.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    recovered_operation = _operation(opener)
    assert recovered_operation.operation_id == operation.operation_id
    assert TappingStrategy.from_operation_parameters(
        recovered_operation.parameters
    ).dwell_seconds == 0.4
    assert recovered_operation.geometry_inputs == geometry_inputs
    assert recovered_operation.artifact_state.token is None
    assert not recovered.is_dirty

    dirty_state = recovered_operation.artifact_state.mark_dirty(
        DirtyReason.PARAMETERS_CHANGED
    )
    computing_state, _token = dirty_state.begin(
        DependencyFingerprint.from_payload({"pending": True})
    )
    computing = replace(recovered_operation, artifact_state=computing_state)
    job = opener.cam_snapshot.jobs[0]
    setup = job.setups[0]
    opener.execute_cam_command(lambda app: app.update_tree(
        job.job_id,
        setup.setup_id,
        lambda tree: tree.replace_operation(computing),
    ))
    opener.save()
    recovered_root = opener.current_project.root_path
    opener.close_project()
    opener.open_project(recovered_root)
    normalized = _operation(opener)
    assert normalized.artifact_state.status is ArtifactStatus.DIRTY
    assert normalized.artifact_state.token is None
    assert normalized.operation_id == operation.operation_id
    assert TappingStrategy.from_operation_parameters(
        normalized.parameters
    ).dwell_seconds == 0.4

    recomputed = opener.compute_tapping(
        normalized.operation_id,
        geometry_resolver=_resolved,
    )
    assert recomputed.accepted
    opener.save()
    metadata = opener.cam_snapshot.artifacts[0]
    artifact_path = opener.current_project.root_path / metadata.relative_path
    opener.close_project()
    artifact_path.write_text("tampered", encoding="utf-8")
    opener.open_project(recovered_root)
    reconciled = _operation(opener)
    assert reconciled.artifact_state.status in {
        ArtifactStatus.DIRTY,
        ArtifactStatus.MISSING,
    }
    assert TappingStrategy.from_operation_parameters(
        reconciled.parameters
    ).geometry.source == strategy.geometry.source
    assert opener.load_toolpath_artifact(reconciled.operation_id) is None
    workspace.deleteLater()
