"""Stage 7B.8.3 Reaming UI, persistence and lifecycle integration tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CylindricalGeometry,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillGeometryInput,
    DrillingRegion,
    FeedRate,
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
    ReamingCoolantMode,
    ReamingRetractPolicy,
    ReamingStrategy,
    ResolvedDrillingGeometry,
    Revision,
    SpindleDirection,
    ToolAssembly,
    ToolAssemblyId,
    ToolDefinitionId,
    ToolFamily,
    ValidationDiagnostic,
    Vector3,
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.models import UnitSystem
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
    unit: LengthUnit,
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
        Point3(x, 7.0 / (25.4 if unit is LengthUnit.INCH else 1.0), 0, unit),
        unit,
    )


def _location(hole: HoleReference) -> HoleLocation:
    is_edge = hole.reference.kind is GeometryReferenceKind.EDGE
    scale = 1.0 if hole.unit is LengthUnit.MM else 1.0 / 25.4
    diameter = 7.0 if hole.reference.hint == "diameter-mismatch" else 8.0
    return HoleLocation(
        hole.plane_origin,
        hole.axis,
        hole.plane_origin,
        Length(diameter * scale, hole.unit) if is_edge else None,
        hole.unit,
        HoleSourceKind.CIRCULAR_EDGE if is_edge else HoleSourceKind.BREP_VERTEX,
        hole,
    )


def _pattern(*holes: HoleReference) -> HolePattern:
    assert holes
    return HolePattern(tuple(_location(hole) for hole in holes), holes[0].unit)


def _explicit_pattern(unit: LengthUnit) -> HolePattern:
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    axis = Vector3(0, 0, 1)
    return HolePattern(tuple(
        HoleLocation(
            Point3(x * scale, 5 * scale, 0, unit),
            axis,
            Point3(x * scale, 5 * scale, 0, unit),
            None,
            unit,
            HoleSourceKind.EXPLICIT_POINT,
        )
        for x in (0.0, 12.0)
    ), unit)


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
        GeometryFingerprint.from_payload({"holes": pattern.to_dict()}),
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


def _select_family(workspace: CamWorkspace, family: ToolFamily) -> None:
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


def _select_reaming_machine(workspace: CamWorkspace) -> None:
    machine = next(
        value for value in workspace._service.cam_snapshot.machine_definitions
        if "doa" in value.name.lower()
    )
    index = workspace.editor.machine.findData(str(machine.machine_id))
    assert index >= 0
    workspace.editor.machine.setCurrentIndex(index)


def _workspace(
    tmp_path,
    *,
    hole_source=None,
    units: UnitSystem = UnitSystem.MILLIMETER,
):
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(
        tmp_path,
        "Reaming UI",
        source,
        units=units,
    )
    source_id = session.manifest.source_files[0].source_id
    unit = LengthUnit.INCH if units is UnitSystem.INCH else LengthUnit.MM
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    selected = {
        "source": hole_source or _pattern(
            _hole(
                source_id,
                hint="vertex-left",
                x=4 * scale,
                unit=unit,
                occurrence_path="root/left",
            ),
            _hole(
                source_id,
                hint="circle-right",
                x=14 * scale,
                unit=unit,
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
    workspace.create_basic_reaming_resources()
    workspace.add_reaming_operation()
    return service, session, workspace, viewer, selected, source_id


def test_reaming_ui_atomic_draft_validation_picking_generate_and_viewer(
    tmp_path,
    monkeypatch,
) -> None:
    service, _session, workspace, viewer, selected, source_id = _workspace(tmp_path)
    operation = _operation(service)
    strategy = ReamingStrategy.from_operation_parameters(operation.parameters)
    assert operation.strategy_key == "reaming_v1"
    assert strategy.pre_hole_diameter.value == pytest.approx(7.8)
    assert strategy.retract_policy is ReamingRetractPolicy.CONTROLLED_FEED
    assert len(operation.geometry_inputs) == 2
    assert workspace.actions["generate"].isEnabled()
    assert "MẪU LỖ 2" in workspace.editor.status.text()
    assert "Phôi/mặt bên: 0.1" in workspace.editor.reaming_derived.text()
    assert "Lượng chạy dao/phút: 50" in workspace.editor.reaming_derived.text()
    service.save()
    assert not service.is_dirty

    for field, value, diagnostic in (
        ("pre_hole", "", "ream.prehole_missing"),
        ("pre_hole", "0", "ream.prehole_invalid"),
        ("pre_hole", "8", "ream.prehole_invalid"),
        ("pre_hole", "7.9999999", "ream.stock_invalid"),
        ("diameter", "0", "ream.invalid_parameters"),
        ("spindle", "0", "ream.invalid_parameters"),
        ("feed_per_revolution", "0", "ream.invalid_parameters"),
        ("final", "0", "ream.depth_invalid"),
        ("clearance", "2", "ream.unsafe_clearance"),
    ):
        committed = _operation(service)
        original = workspace.editor._reaming_fields[field].text()
        workspace.editor._reaming_fields[field].setText(value)
        assert not workspace.actions["generate"].isEnabled()
        workspace.editor._submit()
        assert _operation(service) == committed
        assert not service.is_dirty
        assert diagnostic in workspace.editor.error.text()
        assert workspace.editor._reaming_fields[field].text() == original

    _select_family(workspace, ToolFamily.DRILL)
    before_tool = _operation(service)
    workspace.editor._submit()
    assert _operation(service) == before_tool
    assert "ream.unsupported_tool" in workspace.editor.error.text()

    basic_machine = next(
        value for value in service.cam_snapshot.machine_definitions
        if value.name == "Máy phay cơ bản"
    )
    machine_index = workspace.editor.machine.findData(str(basic_machine.machine_id))
    assert machine_index >= 0
    workspace.editor.machine.setCurrentIndex(machine_index)
    workspace.editor.reaming_spindle_direction.setCurrentText(
        SpindleDirection.COUNTERCLOCKWISE.value
    )
    before_machine = _operation(service)
    workspace.editor._submit()
    assert _operation(service) == before_machine
    assert "ream.machine_incompatible" in workspace.editor.error.text()

    old_source = workspace._picked_hole_source
    workspace._drilling_pick_provider = lambda _axis: (_ for _ in ()).throw(
        RuntimeError("pick cancelled")
    )
    workspace.pick_geometry()
    assert workspace._picked_hole_source == old_source
    assert "pick cancelled" in workspace.editor.error.text()

    mismatch = _pattern(_hole(
        source_id,
        hint="diameter-mismatch",
        x=22,
        unit=LengthUnit.MM,
        kind=GeometryReferenceKind.EDGE,
        occurrence_path="root/mismatch",
    ))
    workspace._drilling_pick_provider = lambda _axis: mismatch
    workspace.pick_geometry()
    before_mismatch = _operation(service)
    workspace.editor._submit()
    assert _operation(service) == before_mismatch
    assert "ream.diameter_mismatch" in workspace.editor.error.text()
    assert workspace._picked_hole_source == old_source

    replacement = _pattern(_hole(
        source_id,
        hint="replacement",
        x=22,
        unit=LengthUnit.MM,
        occurrence_path="root/replacement",
    ))
    selected["source"] = replacement
    workspace._drilling_pick_provider = lambda _axis: selected["source"]
    workspace.pick_geometry()
    before_rebind = _operation(service)
    assert before_rebind.geometry_inputs[0].reference != (
        replacement.locations[0].reference.reference
    )
    workspace.editor._submit()
    rebound = _operation(service)
    assert rebound.geometry_inputs[0].reference == (
        replacement.locations[0].reference.reference
    )
    assert DirtyReason.GEOMETRY_CHANGED in rebound.artifact_state.dirty_reasons

    original_execute = service.execute_cam_command
    committed_dwell = workspace.editor._reaming_fields["dwell"].text()
    before_rollback = _operation(service)
    workspace.editor._reaming_fields["dwell"].setText("0.3")
    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("atomic apply rollback")
        ),
    )
    workspace.editor._submit()
    assert _operation(service) == before_rollback
    assert workspace.editor._reaming_fields["dwell"].text() == committed_dwell
    assert "atomic apply rollback" in workspace.editor.error.text()
    monkeypatch.setattr(service, "execute_cam_command", original_execute)

    _select_family(workspace, ToolFamily.REAMER)
    _select_reaming_machine(workspace)
    workspace.editor.reaming_spindle_direction.setCurrentText(
        SpindleDirection.COUNTERCLOCKWISE.value
    )
    workspace.editor.reaming_coolant.setCurrentText(
        ReamingCoolantMode.FLOOD.value
    )
    workspace.editor._reaming_fields["spindle"].setText("600")
    workspace.editor._reaming_fields["feed_per_revolution"].setText("0.12")
    workspace.editor._reaming_fields["dwell"].setText("0.2")
    assert "Lượng chạy dao/phút: 72" in workspace.editor.reaming_derived.text()
    workspace.editor._submit()
    applied = ReamingStrategy.from_operation_parameters(_operation(service).parameters)
    assert applied.feed_per_minute.value == pytest.approx(72.0)
    assert applied.stock_per_side.value == pytest.approx(0.1)
    assert applied.coolant is ReamingCoolantMode.FLOOD

    workspace.generate_selected()
    generated = _operation(service)
    artifact = service.load_toolpath_artifact(generated.operation_id)
    assert generated.artifact_state.status is ArtifactStatus.VALID
    assert artifact is not None and viewer.displayed
    assert "controlled_feed" in workspace.editor.toolpath_metadata.text()
    assert "stock/side 0.1" in workspace.editor.toolpath_metadata.text()
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
            "reaming reference stale",
        ),),
    )
    workspace._drilling_resolver = lambda _geometry, _depth: stale_geometry
    workspace.generate_selected()
    assert service.load_toolpath_artifact(generated.operation_id) == previous_artifact
    assert viewer.displayed[-1] == previous_display
    assert "ream.geometry_stale" in workspace.editor.error.text()

    workspace._drilling_resolver = _resolved
    workspace.clear_geometry_pick()
    assert _operation(service).geometry_inputs == ()
    assert "THIẾU LỖ" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    workspace.deleteLater()


def test_reaming_explicit_pattern_stale_callback_and_project_switch(
    tmp_path,
    monkeypatch,
) -> None:
    service, _session, workspace, viewer, _selected, _source_id = _workspace(
        tmp_path,
        hole_source=_explicit_pattern(LengthUnit.MM),
    )
    operation = _operation(service)
    assert operation.geometry_inputs == ()
    assert workspace.actions["generate"].isEnabled()
    workspace.generate_selected()
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None
    assert sum(
        event.semantic_key == "ream.hole_complete"
        for event in artifact.events
        if hasattr(event, "semantic_key")
    ) == 2
    displayed_before = len(viewer.displayed)
    original_compute = service.compute_reaming

    def stale_compute(*args, **kwargs):
        result = original_compute(*args, **kwargs)
        workspace._generation += 1
        return result

    monkeypatch.setattr(service, "compute_reaming", stale_compute)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    assert "ream.stale_result" in workspace.editor.error.text()

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


def test_reaming_ui_tool_diameter_length_stickout_and_machine_limits(
    tmp_path,
) -> None:
    service, _session, workspace, _viewer, _selected, _source_id = _workspace(
        tmp_path
    )
    snapshot = service.cam_snapshot
    reamer = next(
        tool for tool in snapshot.tool_definitions
        if tool.family is ToolFamily.REAMER
    )
    reaming_assembly = next(
        assembly for assembly in snapshot.tool_assemblies
        if assembly.tool_id == reamer.tool_id
    )
    holder = next(
        value for value in snapshot.holder_definitions
        if value.holder_id == reaming_assembly.holder_id
    )
    machine = next(
        value for value in snapshot.machine_definitions
        if "doa" in value.name.lower()
    )
    unit = LengthUnit.MM

    diameter_tool = replace(
        reamer,
        tool_id=ToolDefinitionId.new(),
        name="Reamer wrong diameter",
        cutting_geometry=CylindricalGeometry(
            Length(7.0, unit), Length(25.0, unit)
        ),
    )
    diameter_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Wrong diameter assembly",
        diameter_tool,
        Length(35, unit),
        Length(75, unit),
        holder,
    )
    short_tool = replace(
        reamer,
        tool_id=ToolDefinitionId.new(),
        name="Short reamer",
        cutting_geometry=CylindricalGeometry(
            Length(8.0, unit), Length(5.0, unit)
        ),
        usable_length=Length(5.0, unit),
    )
    short_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Short stickout assembly",
        short_tool,
        Length(5, unit),
        Length(50, unit),
        holder,
    )
    slow_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="Slow spindle machine",
        spindles=tuple(
            replace(spindle, maximum_speed=replace(
                spindle.maximum_speed, value=400.0,
            ))
            for spindle in machine.spindles
        ),
    )
    low_feed_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="Low feed machine",
        capabilities=replace(
            machine.capabilities,
            maximum_feed=FeedRate(
                10.0, machine.capabilities.maximum_feed.unit,
            ),
        ),
    )
    dry_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="No coolant machine",
        capabilities=replace(machine.capabilities, coolant=()),
    )

    def add_invalid_resources(app):
        app.add_tool_definition(diameter_tool)
        app.add_tool_assembly(diameter_assembly)
        app.add_tool_definition(short_tool)
        app.add_tool_assembly(short_assembly)
        app.add_machine_definition(slow_machine)
        app.add_machine_definition(low_feed_machine)
        return app.add_machine_definition(dry_machine)

    service.execute_cam_command(add_invalid_resources)
    workspace.refresh(workspace._selected_key)
    service.save()

    for assembly, diagnostic in (
        (diameter_assembly, "ream.diameter_mismatch"),
        (short_assembly, "ream.unsupported_tool"),
    ):
        index = workspace.editor.tool.findData(str(assembly.assembly_id))
        assert index >= 0
        workspace.editor.tool.setCurrentIndex(index)
        before = _operation(service)
        workspace.editor._submit()
        assert _operation(service) == before
        assert not service.is_dirty
        assert diagnostic in workspace.editor.error.text()

    for invalid_machine, mutate, diagnostic in (
        (slow_machine, lambda: None, "ream.machine_incompatible"),
        (
            low_feed_machine,
            lambda: workspace.editor._reaming_fields[
                "feed_per_revolution"
            ].setText("0.1"),
            "ream.machine_incompatible",
        ),
        (
            dry_machine,
            lambda: workspace.editor.reaming_coolant.setCurrentText(
                ReamingCoolantMode.FLOOD.value
            ),
            "ream.machine_incompatible",
        ),
    ):
        mutate()
        index = workspace.editor.machine.findData(str(invalid_machine.machine_id))
        assert index >= 0
        workspace.editor.machine.setCurrentIndex(index)
        before = _operation(service)
        workspace.editor._submit()
        assert _operation(service) == before
        assert not service.is_dirty
        assert diagnostic in workspace.editor.error.text()
    workspace.deleteLater()


def test_reaming_inch_ui_and_persistence_lifecycle(tmp_path) -> None:
    service, session, workspace, _viewer, _selected, _source_id = _workspace(
        tmp_path,
        units=UnitSystem.INCH,
    )
    workspace.generate_selected()
    operation = _operation(service)
    strategy = ReamingStrategy.from_operation_parameters(operation.parameters)
    assert strategy.unit is LengthUnit.INCH
    assert strategy.stock_per_side.value == pytest.approx(0.1 / 25.4)
    assert strategy.feed_per_minute.value == pytest.approx(50.0 / 25.4)
    assert "stock_per_side" not in dict(operation.parameters.values)
    assert "feed_per_minute" not in dict(operation.parameters.values)
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
    restored_strategy = ReamingStrategy.from_operation_parameters(
        restored.parameters
    )
    assert restored.operation_id == operation.operation_id
    assert restored.geometry_inputs == geometry_inputs
    assert restored.tool_assembly == tool_reference
    assert restored.machine_requirement == machine_requirement
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert restored.artifact_state.token is None
    assert restored_strategy.stock_per_side == strategy.stock_per_side
    assert restored_strategy.feed_per_minute == strategy.feed_per_minute
    assert service.load_toolpath_artifact(restored.operation_id) == artifact

    copied = service.save_as(tmp_path, "Reaming UI Copy")
    copied_operation = _operation(service)
    assert copied_operation.operation_id == operation.operation_id
    assert ReamingStrategy.from_operation_parameters(
        copied_operation.parameters
    ) == strategy
    assert service.load_toolpath_artifact(copied_operation.operation_id) == artifact

    workspace.bind_project(copied)
    reaming_item = _find_item(workspace.tree.topLevelItem(0), "Doa lỗ")
    assert reaming_item is not None
    workspace.tree.setCurrentItem(reaming_item)
    workspace.editor._reaming_fields["dwell"].setText("0.4")
    workspace.editor._submit()
    autosaved_operation = _operation(service)
    assert ReamingStrategy.from_operation_parameters(
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
    assert ReamingStrategy.from_operation_parameters(
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

    recomputed = opener.compute_reaming(
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
    assert ReamingStrategy.from_operation_parameters(
        reconciled.parameters
    ).geometry.source == strategy.geometry.source
    assert opener.load_toolpath_artifact(reconciled.operation_id) is None
    workspace.deleteLater()
