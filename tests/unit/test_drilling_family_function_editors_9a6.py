"""Stage 9A.6 production Function Editor contracts for drilling operations."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoringStrategy,
    DrillingCycle,
    DrillingStrategy,
    ReamingStrategy,
    TappingStrategy,
)
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorFieldKind,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.strategies import (
    DrillingFamilyEditorKind,
    validate_drilling_family_schema_contract,
)
from hms_cadcam.ui.operation_manager_projection import (
    OperationManagerProjectionBuilder,
)
from hms_cadcam.ui.operation_manager_types import (
    OperationManagerCapability,
    OperationManagerNodeKind,
)
from tests.unit import test_boring_ui as boring_ui
from tests.unit import test_drilling_ui as drilling_ui
from tests.unit import test_reaming_ui as reaming_ui
from tests.unit import test_tapping_ui as tapping_ui


_WorkspaceFactory = Callable[[object], tuple]
_CASES = (
    (drilling_ui._workspace, DrillingFamilyEditorKind.DRILLING, DrillingStrategy),
    (tapping_ui._workspace, DrillingFamilyEditorKind.TAPPING, TappingStrategy),
    (reaming_ui._workspace, DrillingFamilyEditorKind.REAMING, ReamingStrategy),
    (boring_ui._workspace, DrillingFamilyEditorKind.BORING, BoringStrategy),
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize(("factory", "kind", "strategy_type"), _CASES)
def test_four_production_sessions_construct_with_shared_sections(
    tmp_path,
    factory: _WorkspaceFactory,
    kind: DrillingFamilyEditorKind,
    strategy_type: type,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    assert production.schema.editor_id == f"{kind.value}_production_9a6"
    assert str(production.schema.strategy) == f"{kind.strategy_key}_9a6"
    assert tuple(section.section_id for section in production.schema.ordered_sections) == (
        "basic",
        "geometry",
        "tool",
        "process",
        "levels",
        "cutting",
        "linking",
        "advanced",
        "capability",
        "expert",
    )
    values = production.applied_mapping()
    assert set(values) == {field.field_id for field in production.schema.fields}
    assert values["operation_type"] == kind.title
    assert int(values["hole_count"]) >= 1
    assert "Post cycle" in str(values["capability_summary"])
    validate_drilling_family_schema_contract(production.schema, kind)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert isinstance(strategy_type.from_operation_parameters(operation.parameters), strategy_type)
    assert DATABASE_SCHEMA_VERSION == 4
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "kind", "_strategy_type"), _CASES)
def test_basic_advanced_expert_and_widget_construction(
    tmp_path,
    factory: _WorkspaceFactory,
    kind: DrillingFamilyEditorKind,
    _strategy_type: type,
) -> None:
    application = _application()
    _service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    schema = production.schema
    values = production.applied_mapping()
    basic = schema.visible_sections(values, ParameterDisclosureLevel.BASIC)
    assert {section.section_id for section in basic} == {
        "basic", "geometry", "tool", "process", "levels", "cutting", "linking"
    }
    advanced = schema.visible_sections(values, ParameterDisclosureLevel.ADVANCED)
    assert {"advanced", "capability"} <= {
        section.section_id for section in advanced
    }
    assert "expert" not in {section.section_id for section in advanced}
    assert schema.field("tolerance").disclosure_level is ParameterDisclosureLevel.EXPERT
    assert schema.field("operation_type").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert schema.field("enabled").disclosure_level is ParameterDisclosureLevel.ADVANCED
    for field_id in (
        "selection_mode",
        "machining_direction",
        "coordinate_system",
        "holder_summary",
    ):
        assert schema.field(field_id).disclosure_level is ParameterDisclosureLevel.ADVANCED
    if kind in {DrillingFamilyEditorKind.DRILLING, DrillingFamilyEditorKind.TAPPING}:
        assert schema.field("coolant_summary").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert schema.footer.actions.index(FunctionEditorAction.APPLY) < schema.footer.actions.index(
        FunctionEditorAction.CALCULATE
    )
    assert not schema.section("advanced").default_expanded
    assert not schema.section("expert").default_expanded
    state = FunctionEditorDraftState(schema, values)
    page = FunctionEditorPage(state)
    page.resize(460, 760)
    page.show()
    application.processEvents()
    assert page.schema is schema
    assert page.maximum_disclosure is ParameterDisclosureLevel.BASIC
    assert not page.footer._compact
    operation_name = page._field_widgets["operation_name"]
    assert operation_name.label.text() == "Tên nguyên công *"
    assert operation_name.label.width() >= 80
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
    if kind is DrillingFamilyEditorKind.TAPPING:
        assert schema.field("synchronized_feed").kind is FunctionEditorFieldKind.READ_ONLY
    if kind in {DrillingFamilyEditorKind.REAMING, DrillingFamilyEditorKind.BORING}:
        assert schema.field("feed_per_minute").kind is FunctionEditorFieldKind.READ_ONLY
    _dispose(page, application)
    workspace.deleteLater()


def test_drilling_peck_progressive_validation_and_domain_binding(tmp_path) -> None:
    _application()
    service, _session, workspace, *_rest = drilling_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    state = FunctionEditorDraftState(
        production.schema,
        production.applied_mapping(),
        validation_callback=production.validation_callback,
    )
    assert "peck_depth" not in state.applicable_field_ids()
    state.edit("cycle", DrillingCycle.PECK_DRILL.value)
    assert "peck_depth" in state.applicable_field_ids()
    state.edit("peck_depth", "0")
    assert any(item.code == "drill.peck_positive" for item in state.validate())
    state.edit("peck_depth", "1")
    assert not state.validate()
    assert state.apply(production.apply_callback)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    strategy = DrillingStrategy.from_operation_parameters(operation.parameters)
    assert strategy.cycle is DrillingCycle.PECK_DRILL
    assert strategy.peck_depth is not None and strategy.peck_depth.value == 1.0
    workspace.deleteLater()


def test_tapping_valid_draft_reports_unbound_post_as_warning_only(tmp_path) -> None:
    _application()
    _service, _session, workspace, *_rest = tapping_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    diagnostics = production.validation_callback(production.applied_mapping())
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "tap.post_capability_unbound"
    assert diagnostics[0].severity.name == "WARNING"
    state = FunctionEditorDraftState(
        production.schema,
        production.applied_mapping(),
        validation_callback=production.validation_callback,
    )
    assert not any(item.severity.name == "ERROR" for item in state.validate())
    assert state.can_calculate
    workspace.deleteLater()


@pytest.mark.parametrize(
    ("factory", "field_id", "invalid_value", "expected_code"),
    (
        (drilling_ui._workspace, "feed_rate", "0", "drill.feed_positive"),
        (tapping_ui._workspace, "pitch", "0", "tapping.pitch_positive"),
        (reaming_ui._workspace, "pre_hole_diameter", "9", "ream.prehole_invalid"),
        (boring_ui._workspace, "pre_bore_diameter", "21", "bore.prebore_invalid"),
    ),
)
def test_operation_specific_invalid_drafts_never_mutate_domain(
    tmp_path,
    factory: _WorkspaceFactory,
    field_id: str,
    invalid_value: str,
    expected_code: str,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    state = FunctionEditorDraftState(
        production.schema,
        production.applied_mapping(),
        validation_callback=production.validation_callback,
    )
    before = service.cam_snapshot
    state.edit(field_id, invalid_value)
    assert any(item.code == expected_code for item in state.validate())
    assert not state.apply(production.apply_callback)
    assert service.cam_snapshot == before
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "kind", "strategy_type"), _CASES)
def test_apply_reopen_and_save_open_round_trip(
    tmp_path,
    factory: _WorkspaceFactory,
    kind: DrillingFamilyEditorKind,
    strategy_type: type,
) -> None:
    _application()
    service, session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    values = production.applied_mapping()
    values["dwell_seconds"] = "0.25"
    assert production.apply_callback(values)
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    strategy = strategy_type.from_operation_parameters(operation.parameters)
    assert strategy.dwell_seconds == 0.25
    service.save()
    root = session.root_path
    node_id = operation.node_id
    service.close_project()
    reopened = service.open_project(root)
    workspace.bind_project(reopened)
    assert workspace.select_identity("operation", str(node_id))
    restored = workspace.production_function_editor_session()
    assert restored is not None
    assert restored.schema.editor_id == f"{kind.value}_production_9a6"
    assert restored.applied_mapping()["dwell_seconds"] == "0.25"
    restored_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert strategy_type.from_operation_parameters(restored_operation.parameters) == strategy
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "kind", "_strategy_type"), _CASES)
def test_operation_manager_duplicate_uses_fresh_ids_and_opens_correct_editor(
    tmp_path,
    factory: _WorkspaceFactory,
    kind: DrillingFamilyEditorKind,
    _strategy_type: type,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    tree_before = service.cam_snapshot.jobs[0].setups[0].operation_tree
    original = tree_before.operations[0]
    workspace.duplicate_selected_operation()
    tree_after = service.cam_snapshot.jobs[0].setups[0].operation_tree
    assert len(tree_after.operations) == 2
    duplicate = next(
        operation
        for operation in tree_after.operations
        if operation.operation_id != original.operation_id
    )
    assert duplicate.node_id != original.node_id
    assert duplicate.parameters == original.parameters
    assert duplicate.tool_assembly == original.tool_assembly
    assert duplicate.machine_requirement == original.machine_requirement
    assert duplicate.artifact_state.status is ArtifactStatus.MISSING
    assert tuple(item.reference for item in duplicate.geometry_inputs) == tuple(
        item.reference for item in original.geometry_inputs
    )
    assert {item.input_id for item in duplicate.geometry_inputs}.isdisjoint(
        {item.input_id for item in original.geometry_inputs}
    )
    production = workspace.production_function_editor_session()
    assert production is not None
    assert production.schema.editor_id == f"{kind.value}_production_9a6"
    projection = OperationManagerProjectionBuilder().build(
        service, service.current_project
    )
    operation_nodes = tuple(
        node for node in projection.nodes
        if node.kind is OperationManagerNodeKind.OPERATION
    )
    assert len(operation_nodes) == 2
    assert all(
        OperationManagerCapability.DUPLICATE in node.capabilities
        for node in operation_nodes
    )
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "_kind", "_strategy_type"), _CASES)
def test_unchanged_apply_preserves_operation_and_ui_contains_no_gcode_cycle(
    tmp_path,
    factory: _WorkspaceFactory,
    _kind: DrillingFamilyEditorKind,
    _strategy_type: type,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    before = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert production.apply_callback(production.applied_mapping())
    after = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert after == before
    presentation_text = " ".join(
        (
            production.schema.summary.title,
            production.schema.summary.strategy,
            *(section.title for section in production.schema.sections),
            *(field.label for field in production.schema.fields),
            *(str(choice) for field in production.schema.fields for choice in field.choices),
        )
    ).upper()
    assert not {"G74", "G81", "G83", "G84", "G85", "G86", "G88", "G89"} & set(
        presentation_text.split()
    )
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "kind", "_strategy_type"), _CASES)
def test_preview_is_transient_and_apply_updates_name_enabled_status(
    tmp_path,
    factory: _WorkspaceFactory,
    kind: DrillingFamilyEditorKind,
    _strategy_type: type,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    state = FunctionEditorDraftState(
        production.schema,
        production.applied_mapping(),
        project_key=production.project_key,
        operation_key=production.operation_key,
        generation=production.generation,
        validation_callback=production.validation_callback,
    )
    before = service.cam_snapshot
    preview = production.preview_callback(state.preview_request())
    assert "Preview approximate" in str(preview)
    assert service.cam_snapshot == before
    values = production.applied_mapping()
    values["operation_name"] = f"{kind.title} Production"
    values["enabled"] = False
    assert production.apply_callback(values)
    tree = service.cam_snapshot.jobs[0].setups[0].operation_tree
    operation = tree.operations[0]
    node = tree.get_node(operation.node_id)
    assert node.name == f"{kind.title} Production"
    assert not operation.enabled
    reopened = workspace.production_function_editor_session()
    assert reopened is not None
    reopened_state = FunctionEditorDraftState(
        reopened.schema,
        reopened.applied_mapping(),
        validation_callback=reopened.validation_callback,
    )
    assert not reopened_state.can_calculate
    projection = OperationManagerProjectionBuilder().build(
        service, service.current_project
    )
    projected = next(
        item
        for item in projection.nodes
        if item.kind is OperationManagerNodeKind.OPERATION
    )
    assert projected.label == f"{kind.title} Production"
    assert not projected.enabled
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "_kind", "_strategy_type"), _CASES)
def test_dirty_operation_switch_discard_keeps_domain_unchanged(
    tmp_path,
    factory: _WorkspaceFactory,
    _kind: DrillingFamilyEditorKind,
    _strategy_type: type,
) -> None:
    application = _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    original = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    workspace.duplicate_selected_operation()
    duplicate = next(
        item
        for item in service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if item.operation_id != original.operation_id
    )
    host = FunctionEditorHost(
        workspace.editor,
        workspace.tree,
        workspace.editor.apply_draft,
        production_provider=workspace.production_function_editor_session,
        selection_restore=workspace.select_identity,
        selection_exists=workspace.selection_exists,
        switch_confirmation=lambda _state: "discard",
    )
    assert host.active_page is not None
    assert host.active_page.state.operation_key == str(duplicate.operation_id)
    host.active_page.state.edit("dwell_seconds", "0.9")
    assert host.active_page.state.is_dirty
    assert workspace.select_identity("operation", str(original.node_id))
    application.processEvents()
    assert host.active_page is not None
    assert host.active_page.state.operation_key == str(original.operation_id)
    current_duplicate = next(
        item
        for item in service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if item.operation_id == duplicate.operation_id
    )
    assert current_duplicate.parameters == duplicate.parameters
    _dispose(host, application)
    workspace.deleteLater()
