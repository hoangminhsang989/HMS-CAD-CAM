"""Stage 7B.9.3 Boring UI, persistence and lifecycle integration tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoringBarGeometry,
    BoringCoolantMode,
    BoringStrategy,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DirtyReason,
    DrillGeometryInput,
    DrillingRegion,
    FeedRate,
    GeometryFingerprint,
    GeometryReferenceKind,
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
    ShankGeometry,
    SpindleDirection,
    ToolAssembly,
    ToolAssemblyId,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.models import UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace

from tests.unit.test_reaming_ui import _hole


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


def _location(
    hole: HoleReference,
    *,
    diameter: float = 20.0,
) -> HoleLocation:
    is_edge = hole.reference.kind is GeometryReferenceKind.EDGE
    return HoleLocation(
        hole.plane_origin,
        hole.axis,
        hole.plane_origin,
        Length(diameter, hole.unit) if is_edge else None,
        hole.unit,
        HoleSourceKind.CIRCULAR_EDGE if is_edge else HoleSourceKind.BREP_VERTEX,
        hole,
    )


def _pattern(
    *holes: HoleReference,
    edge_diameter: float = 20.0,
) -> HolePattern:
    assert holes
    return HolePattern(
        tuple(_location(hole, diameter=edge_diameter) for hole in holes),
        holes[0].unit,
    )


def _explicit_pattern(unit: LengthUnit) -> HolePattern:
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    axis = Vector3(0, 0, 1)
    return HolePattern(tuple(
        HoleLocation(
            point,
            axis,
            point,
            None,
            unit,
            HoleSourceKind.EXPLICIT_POINT,
        )
        for point in (
            Point3(0, 5 * scale, 0, unit),
            Point3(12 * scale, 5 * scale, 0, unit),
        )
    ), unit)


def _resolved(geometry, depth) -> ResolvedDrillingGeometry:
    source = geometry.source
    pattern = (
        source
        if isinstance(source, HolePattern)
        else HolePattern((_location(source),), source.unit)
    )
    return ResolvedDrillingGeometry(
        GeometryResolutionStatus.RESOLVED,
        DrillingRegion(
            DrillGeometryInput(source, source.unit),
            pattern,
            depth,
            source.unit,
            GeometryFingerprint.from_payload({"holes": pattern.to_dict()}),
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


def _operation(service: ProjectService):
    return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]


def _select_boring_tool(workspace: CamWorkspace, assembly_id=None) -> None:
    snapshot = workspace._service.cam_snapshot
    boring_tool_ids = {
        tool.tool_id for tool in snapshot.tool_definitions
        if tool.family is ToolFamily.BORING_BAR
    }
    assembly = next(
        value for value in snapshot.tool_assemblies
        if value.tool_id in boring_tool_ids
        and (assembly_id is None or value.assembly_id == assembly_id)
    )
    index = workspace.editor.tool.findData(str(assembly.assembly_id))
    assert index >= 0
    workspace.editor.tool.setCurrentIndex(index)


def _select_boring_machine(workspace: CamWorkspace, machine_id=None) -> None:
    machine = next(
        value for value in workspace._service.cam_snapshot.machine_definitions
        if (machine_id is None and "tiện lỗ" in value.name.lower())
        or value.machine_id == machine_id
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
        "Boring UI",
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
            edge_diameter=20.0 * scale,
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
    workspace.create_basic_boring_resources()
    workspace.add_boring_operation()
    return service, session, workspace, viewer, selected, source_id


def test_boring_ui_atomic_draft_picking_generate_and_viewer(
    tmp_path,
    monkeypatch,
) -> None:
    service, _session, workspace, viewer, selected, source_id = _workspace(
        tmp_path
    )
    operation = _operation(service)
    strategy = BoringStrategy.from_operation_parameters(operation.parameters)
    assert operation.strategy_key == "boring_v1"
    assert strategy.pre_bore_diameter.value == pytest.approx(18.0)
    assert strategy.radial_stock.value == pytest.approx(1.0)
    assert strategy.feed_per_minute.value == pytest.approx(60.0)
    assert len(operation.geometry_inputs) == 2
    assert workspace.actions["generate"].isEnabled()
    assert "MẪU LỖ 2" in workspace.editor.status.text()
    assert "Lượng dư hướng kính: 1" in workspace.editor.boring_derived.text()
    assert "Lượng chạy dao/phút: 60" in workspace.editor.boring_derived.text()
    assert workspace.editor.tool.count() == 1
    assert "boring_bar" in workspace.editor.boring_tool_details.text()
    assert "ảnh chụp HIỆN HÀNH" in workspace.editor.boring_tool_details.text()
    assert "D cán 12" in workspace.editor.boring_tool_details.text()
    assert "cụm Tool bản sửa đổi 0/fp" in workspace.editor.boring_tool_details.text()
    service.save()
    assert not service.is_dirty

    workspace.editor._boring_fields["pre_bore"].setText("17.8")
    assert "Lượng dư hướng kính: 1.1" in workspace.editor.boring_derived.text()
    assert _operation(service) == operation
    assert not service.is_dirty
    workspace.editor._boring_fields["pre_bore"].setText("18.0")

    for field, value, diagnostic in (
        ("pre_bore", "", "bore.prebore_missing"),
        ("pre_bore", "0", "bore.prebore_invalid"),
        ("pre_bore", "20", "bore.prebore_invalid"),
        ("pre_bore", "19.9999999", "bore.stock_invalid"),
        ("finished_diameter", "0", "bore.invalid_parameters"),
        ("spindle", "0", "bore.invalid_parameters"),
        ("feed_per_revolution", "0", "bore.invalid_parameters"),
        ("final", "0", "bore.depth_invalid"),
        ("clearance", "2", "bore.unsafe_clearance"),
    ):
        committed = _operation(service)
        original = workspace.editor._boring_fields[field].text()
        workspace.editor._boring_fields[field].setText(value)
        assert not workspace.actions["generate"].isEnabled()
        workspace.editor._submit()
        assert _operation(service) == committed
        assert not service.is_dirty
        assert diagnostic in workspace.editor.error.text()
        assert workspace.editor._boring_fields[field].text() == original

    old_source = workspace._picked_hole_source
    workspace._drilling_pick_provider = lambda _axis: (_ for _ in ()).throw(
        RuntimeError("pick cancelled")
    )
    workspace.pick_geometry()
    assert workspace._picked_hole_source == old_source
    assert "pick cancelled" in workspace.editor.error.text()

    workspace._drilling_pick_provider = lambda _axis: selected["source"]
    for status, drilling_code, boring_code in (
        (
            GeometryResolutionStatus.AMBIGUOUS,
            DiagnosticCode.DRILL_GEOMETRY_AMBIGUOUS,
            "bore.geometry_ambiguous",
        ),
        (
            GeometryResolutionStatus.SOURCE_MISMATCH,
            DiagnosticCode.DRILL_SOURCE_MISMATCH,
            "bore.source_mismatch",
        ),
        (
            GeometryResolutionStatus.STALE,
            DiagnosticCode.DRILL_GEOMETRY_STALE,
            "bore.geometry_stale",
        ),
    ):
        failed = ResolvedDrillingGeometry(
            status,
            diagnostics=(ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                drilling_code,
                "multi-reference resolve failed",
            ),),
        )
        workspace._drilling_resolver = lambda _geometry, _depth, value=failed: value
        workspace.pick_geometry()
        assert workspace._picked_hole_source == old_source
        assert boring_code in workspace.editor.error.text()

    mismatch = _pattern(
        _hole(
            source_id,
            hint="diameter-mismatch",
            x=22,
            unit=LengthUnit.MM,
            kind=GeometryReferenceKind.EDGE,
            occurrence_path="root/mismatch",
        ),
        edge_diameter=19.8,
    )
    workspace._drilling_resolver = _resolved
    workspace._drilling_pick_provider = lambda _axis: mismatch
    workspace.pick_geometry()
    before_mismatch = _operation(service)
    workspace.editor._submit()
    assert _operation(service) == before_mismatch
    assert "bore.diameter_mismatch" in workspace.editor.error.text()
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
    workspace.editor._submit()
    rebound = _operation(service)
    assert rebound.geometry_inputs[0].reference == (
        replacement.locations[0].reference.reference
    )
    assert rebound.geometry_inputs != before_rebind.geometry_inputs
    assert DirtyReason.GEOMETRY_CHANGED in rebound.artifact_state.dirty_reasons

    original_execute = service.execute_cam_command
    committed_dwell = workspace.editor._boring_fields["dwell"].text()
    before_rollback = _operation(service)
    workspace.editor._boring_fields["dwell"].setText("0.3")
    monkeypatch.setattr(
        service,
        "execute_cam_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("atomic apply rollback")
        ),
    )
    workspace.editor._submit()
    assert _operation(service) == before_rollback
    assert workspace.editor._boring_fields["dwell"].text() == committed_dwell
    assert "atomic apply rollback" in workspace.editor.error.text()
    monkeypatch.setattr(service, "execute_cam_command", original_execute)

    _select_boring_tool(workspace)
    _select_boring_machine(workspace)
    workspace.editor.boring_coolant.setCurrentText(BoringCoolantMode.FLOOD.value)
    workspace.editor._boring_fields["spindle"].setText("700")
    workspace.editor._boring_fields["feed_per_revolution"].setText("0.12")
    workspace.editor._boring_fields["dwell"].setText("0.2")
    assert "Lượng chạy dao/phút: 84" in workspace.editor.boring_derived.text()
    workspace.editor._submit()
    applied = BoringStrategy.from_operation_parameters(_operation(service).parameters)
    assert applied.feed_per_minute.value == pytest.approx(84.0)
    assert applied.radial_stock.value == pytest.approx(1.0)
    assert applied.coolant is BoringCoolantMode.FLOOD

    workspace.generate_selected()
    generated = _operation(service)
    artifact = service.load_toolpath_artifact(generated.operation_id)
    assert generated.artifact_state.status is ArtifactStatus.VALID
    assert artifact is not None and viewer.displayed
    assert "controlled_feed" in workspace.editor.toolpath_metadata.text()
    assert "radial stock 1" in workspace.editor.toolpath_metadata.text()
    assert "tool boring_bar" in workspace.editor.toolpath_metadata.text()
    workspace.toggle_toolpath_visibility()
    workspace.toggle_toolpath_visibility()
    assert viewer.visibility[-2:] == [
        (generated.operation_id, False),
        (generated.operation_id, True),
    ]

    previous_artifact = artifact
    previous_display = viewer.displayed[-1]
    workspace._drilling_resolver = lambda _geometry, _depth: (
        ResolvedDrillingGeometry(
            GeometryResolutionStatus.STALE,
            diagnostics=(ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                DiagnosticCode.DRILL_GEOMETRY_STALE,
                "boring reference stale",
            ),),
        )
    )
    workspace.generate_selected()
    assert service.load_toolpath_artifact(generated.operation_id) == previous_artifact
    assert viewer.displayed[-1] == previous_display
    assert "bore.geometry_stale" in workspace.editor.error.text()

    workspace._drilling_resolver = _resolved
    workspace.clear_geometry_pick()
    assert _operation(service).geometry_inputs == ()
    assert "THIẾU LỖ" in workspace.editor.status.text()
    assert not workspace.actions["generate"].isEnabled()
    workspace.deleteLater()


def test_boring_explicit_pattern_stale_callback_and_project_switch(
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
        event.semantic_key == "bore.hole_complete"
        for event in artifact.events
        if hasattr(event, "semantic_key")
    ) == 2
    assert any(
        getattr(event, "provenance", "").endswith("controlled_retract")
        for event in artifact.events
    )
    displayed_before = len(viewer.displayed)
    original_compute = service.compute_boring

    def stale_compute(*args, **kwargs):
        result = original_compute(*args, **kwargs)
        workspace._generation += 1
        return result

    monkeypatch.setattr(service, "compute_boring", stale_compute)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    assert "bore.stale_result" in workspace.editor.error.text()

    other = ProjectService.create_default(tmp_path / "other-config")
    other_session = other.new_project(tmp_path, "Other")
    workspace._service = other
    workspace.bind_project(other_session)
    assert viewer.cleared > 0
    assert workspace.tree.topLevelItemCount() == 1
    assert workspace._boring_drafts == {}
    other.close_project()
    workspace.bind_project(None)
    workspace.generate_selected()
    assert len(viewer.displayed) == displayed_before
    workspace.deleteLater()


def test_boring_ui_filters_tools_and_validates_access_holder_and_machine(
    tmp_path,
) -> None:
    service, _session, workspace, _viewer, _selected, _source_id = _workspace(
        tmp_path
    )
    snapshot = service.cam_snapshot
    original_tool = next(
        tool for tool in snapshot.tool_definitions
        if tool.family is ToolFamily.BORING_BAR
    )
    original_assembly = next(
        assembly for assembly in snapshot.tool_assemblies
        if assembly.tool_id == original_tool.tool_id
    )
    holder = next(
        value for value in snapshot.holder_definitions
        if value.holder_id == original_assembly.holder_id
    )
    machine = next(
        value for value in snapshot.machine_definitions
        if "tiện lỗ" in value.name.lower()
    )
    unit = LengthUnit.MM

    def invalid_assembly(name: str, **changes):
        geometry = BoringBarGeometry(
            Length(changes.get("minimum", 15.0), unit),
            Length(changes.get("maximum", 25.0), unit),
            Length(changes.get("cutting", 25.0), unit),
            changes.get("hand", ToolHand.RIGHT),
        )
        tool = replace(
            original_tool,
            tool_id=ToolDefinitionId.new(),
            name=name,
            cutting_geometry=geometry,
            usable_length=Length(changes.get("usable", 35.0), unit),
            shank=ShankGeometry(
                Length(changes.get("shank", 12.0), unit),
                original_tool.shank.length,
            ),
        )
        assembly = ToolAssembly.create(
            ToolAssemblyId.new(),
            f"{name} assembly",
            tool,
            Length(changes.get("stickout", 25.0), unit),
            original_assembly.gauge_length,
            holder,
        )
        return tool, assembly

    invalid_tools = (
        (*invalid_assembly("Minimum access", minimum=19.0), "bore.tool_access_invalid"),
        (*invalid_assembly("Maximum reach", maximum=19.0), "bore.tool_access_invalid"),
        (*invalid_assembly("Short cutting", cutting=5.0), "bore.unsupported_tool"),
        (*invalid_assembly("Short usable", usable=5.0, cutting=5.0), "bore.unsupported_tool"),
        (*invalid_assembly("Shank collision", shank=18.0), "bore.clearance_invalid"),
        (*invalid_assembly("Holder margin", stickout=10.0), "bore.clearance_invalid"),
        (*invalid_assembly("Wrong hand", hand=ToolHand.LEFT), "bore.unsupported_tool"),
    )
    slow_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="Slow boring spindle",
        spindles=tuple(
            replace(spindle, maximum_speed=replace(
                spindle.maximum_speed, value=400.0,
            ))
            for spindle in machine.spindles
        ),
    )
    reverse_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="Reverse-only boring spindle",
        spindles=tuple(
            replace(
                spindle,
                directions=(SpindleDirection.COUNTERCLOCKWISE,),
            )
            for spindle in machine.spindles
        ),
    )
    low_feed_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="Low boring feed",
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
        name="Dry boring machine",
        capabilities=replace(machine.capabilities, coolant=()),
    )
    no_drilling_machine = replace(
        machine,
        machine_id=MachineDefinitionId.new(),
        name="No drilling capability",
        capabilities=replace(
            machine.capabilities,
            operations=(OperationCapability.MILLING,),
        ),
    )

    def add_invalid_resources(app):
        for tool, assembly, _diagnostic in invalid_tools:
            app.add_tool_definition(tool)
            app.add_tool_assembly(assembly)
        for value in (
            slow_machine,
            reverse_machine,
            low_feed_machine,
            dry_machine,
            no_drilling_machine,
        ):
            app.add_machine_definition(value)
        return app.snapshot

    service.execute_cam_command(add_invalid_resources)
    workspace.refresh(workspace._selected_key)
    visible_ids = {
        workspace.editor.tool.itemData(index)
        for index in range(workspace.editor.tool.count())
    }
    boring_ids = {
        str(assembly.assembly_id)
        for assembly in service.cam_snapshot.tool_assemblies
        if any(
            tool.tool_id == assembly.tool_id
            and tool.family is ToolFamily.BORING_BAR
            for tool in service.cam_snapshot.tool_definitions
        )
    }
    assert visible_ids == boring_ids
    assert all(
        workspace.editor.tool.itemText(index) != "Cụm dao cơ bản"
        for index in range(workspace.editor.tool.count())
    )
    service.save()

    for _tool, assembly, diagnostic in invalid_tools:
        _select_boring_tool(workspace, assembly.assembly_id)
        before = _operation(service)
        workspace.editor._submit()
        assert _operation(service) == before
        assert not service.is_dirty
        assert diagnostic in workspace.editor.error.text()

    for invalid_machine, mutate in (
        (slow_machine, lambda: None),
        (reverse_machine, lambda: None),
        (low_feed_machine, lambda: None),
        (
            dry_machine,
            lambda: workspace.editor.boring_coolant.setCurrentText(
                BoringCoolantMode.FLOOD.value
            ),
        ),
        (no_drilling_machine, lambda: None),
    ):
        _select_boring_tool(workspace, original_assembly.assembly_id)
        mutate()
        _select_boring_machine(workspace, invalid_machine.machine_id)
        before = _operation(service)
        workspace.editor._submit()
        assert _operation(service) == before
        assert not service.is_dirty
        assert "bore.machine_incompatible" in workspace.editor.error.text()
    workspace.deleteLater()


def test_boring_inch_ui_and_persistence_lifecycle(tmp_path) -> None:
    service, session, workspace, _viewer, _selected, _source_id = _workspace(
        tmp_path,
        units=UnitSystem.INCH,
    )
    workspace.generate_selected()
    operation = _operation(service)
    strategy = BoringStrategy.from_operation_parameters(operation.parameters)
    assert strategy.unit is LengthUnit.INCH
    assert strategy.radial_stock.value == pytest.approx(1.0 / 25.4)
    assert strategy.feed_per_minute.value == pytest.approx(60.0 / 25.4)
    parameter_values = dict(operation.parameters.values)
    assert "radial_stock" not in parameter_values
    assert "feed_per_minute" not in parameter_values
    geometry_inputs = operation.geometry_inputs
    tool_reference = operation.tool_assembly
    machine_requirement = operation.machine_requirement
    artifact = service.load_toolpath_artifact(operation.operation_id)
    assert artifact is not None
    boring_tool = next(
        tool for tool in service.cam_snapshot.tool_definitions
        if tool.family is ToolFamily.BORING_BAR
    )
    assert isinstance(boring_tool.cutting_geometry, BoringBarGeometry)

    service.save()
    root = session.root_path
    service.close_project()
    reopened = service.open_project(root)
    assert not reopened.is_dirty
    restored = _operation(service)
    restored_strategy = BoringStrategy.from_operation_parameters(restored.parameters)
    restored_tool = next(
        tool for tool in service.cam_snapshot.tool_definitions
        if tool.tool_id == boring_tool.tool_id
    )
    assert restored.operation_id == operation.operation_id
    assert restored.geometry_inputs == geometry_inputs
    assert restored.tool_assembly == tool_reference
    assert restored.machine_requirement == machine_requirement
    assert restored.artifact_state.status is ArtifactStatus.VALID
    assert restored.artifact_state.token is None
    assert restored_strategy.radial_stock == strategy.radial_stock
    assert restored_strategy.feed_per_minute == strategy.feed_per_minute
    assert restored_tool == boring_tool
    assert service.load_toolpath_artifact(restored.operation_id) == artifact

    copied = service.save_as(tmp_path, "Boring UI Copy")
    copied_operation = _operation(service)
    assert copied_operation.operation_id == operation.operation_id
    assert BoringStrategy.from_operation_parameters(
        copied_operation.parameters
    ) == strategy
    assert service.load_toolpath_artifact(copied_operation.operation_id) == artifact

    workspace.bind_project(copied)
    boring_item = _find_item(workspace.tree.topLevelItem(0), "Khoét lỗ")
    assert boring_item is not None
    workspace.tree.setCurrentItem(boring_item)
    workspace.editor._boring_fields["dwell"].setText("0.4")
    workspace.editor._submit()
    autosaved_operation = _operation(service)
    assert BoringStrategy.from_operation_parameters(
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
    assert BoringStrategy.from_operation_parameters(
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

    recomputed = opener.compute_boring(
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
    assert BoringStrategy.from_operation_parameters(
        reconciled.parameters
    ).geometry.source == strategy.geometry.source
    assert opener.load_toolpath_artifact(reconciled.operation_id) is None

    future_tool = boring_tool.to_dict()
    future_geometry = dict(future_tool["cutting_geometry"])
    future_geometry["geometry_version"] = 2
    future_tool["cutting_geometry"] = future_geometry
    with pytest.raises(ValueError):
        ToolDefinition.from_dict(future_tool)
    workspace.deleteLater()
