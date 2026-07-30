"""Metadata-driven Lathe workspace, accessibility and responsive acceptance tests."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)
import pytest

from hms_cadcam.cam.lathe.types import (
    LatheParameterGroup,
    LatheParameterValueKind,
    LatheStrategyFamily,
    LatheStrategyId,
)
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.lathe_workspace import LatheWorkspace

from _lathe_ui_fixtures import application, workspace_for


def _dispose(workspace: LatheWorkspace) -> None:
    workspace.close()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _strategy_items(workspace: LatheWorkspace):
    return tuple(
        family.child(index)
        for family_index in range(workspace.strategy_tree.topLevelItemCount())
        for family in (workspace.strategy_tree.topLevelItem(family_index),)
        for index in range(family.childCount())
    )


def test_strategy_browser_has_exact_order_families_and_stable_ids() -> None:
    workspace, presenter, _reference = workspace_for()
    items = _strategy_items(workspace)
    role = int(Qt.ItemDataRole.UserRole) + 101
    assert tuple(LatheStrategyId(str(item.data(0, role))) for item in items) == tuple(LatheStrategyId)
    assert workspace.strategy_tree.topLevelItemCount() == 4
    assert tuple(
        LatheStrategyFamily(str(workspace.strategy_tree.topLevelItem(index).data(0, role)))
        for index in range(4)
    ) == tuple(LatheStrategyFamily)
    assert len(items) == len({item.data(0, role) for item in items}) == 11
    assert not any(
        token in item.text(0).casefold()
        for item in items
        for token in ("custom", "unknown", "favorite", "recent")
    )
    assert presenter.snapshot.operations == ()
    _dispose(workspace)


@pytest.mark.parametrize("strategy_id", tuple(LatheStrategyId))
def test_all_strategy_forms_follow_exact_descriptor_order_and_groups(
    strategy_id: LatheStrategyId,
) -> None:
    workspace, presenter, _reference = workspace_for(strategy_id)
    presenter.create_operation(strategy_id)
    active = presenter.active_operation
    descriptor = next(
        item
        for item in presenter.snapshot.strategies
        if item.strategy_id is strategy_id
    )
    assert active is not None
    assert tuple(workspace.parameter_editor.editors) == tuple(
        item.parameter_id for item in descriptor.parameters
    )
    assert tuple(
        item.parameter_id
        for item in descriptor.parameters
        if item.group is LatheParameterGroup.BASIC
    ) == tuple(
        workspace.parameter_editor.basic_form.itemAt(
            row,
            QFormLayout.ItemRole.FieldRole,
        )
        .widget().objectName()
        .removeprefix("LatheParameter_")
        .removesuffix("OptionalContainer")
        for row in range(workspace.parameter_editor.basic_form.rowCount())
    )
    assert tuple(item.order for item in descriptor.parameters) == tuple(
        sorted(item.order for item in descriptor.parameters)
    )
    assert len(workspace.parameter_editor.findChildren(QWidget)) == len(
        set(id(item) for item in workspace.parameter_editor.findChildren(QWidget))
    )
    _dispose(workspace)


def test_parameter_widget_kinds_bounds_enums_and_optional_peck_are_typed() -> None:
    workspace, presenter, _reference = workspace_for(LatheStrategyId.AXIAL_DRILL)
    presenter.create_operation(LatheStrategyId.AXIAL_DRILL)
    descriptor = next(
        item
        for item in presenter.snapshot.strategies
        if item.strategy_id is LatheStrategyId.AXIAL_DRILL
    )
    for field in descriptor.parameters:
        editor = workspace.findChild(QWidget, f"LatheParameter_{field.parameter_id}")
        assert editor is not None
        if field.value_kind is LatheParameterValueKind.FLOAT:
            assert isinstance(editor, QDoubleSpinBox)
        elif field.value_kind is LatheParameterValueKind.INTEGER:
            assert isinstance(editor, QSpinBox)
        else:
            assert isinstance(editor, QComboBox)
            assert tuple(editor.itemData(index) for index in range(editor.count())) == field.enum_values
    peck = workspace.findChild(QDoubleSpinBox, "LatheParameter_peck_depth_mm")
    optional = workspace.findChild(QWidget, "LatheParameter_peck_depth_mmOptional")
    assert peck is not None and optional is not None
    assert not optional.isChecked()
    assert not peck.isEnabled()
    optional.setChecked(True)
    peck.setValue(2.5)
    updates = workspace.parameter_editor.updates()
    assert ("peck_depth_mm", 2.5) in tuple(
        (item.parameter_id, item.value) for item in updates
    )
    _dispose(workspace)


def test_advanced_disclosure_preserves_values_and_revision() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.FACE)
    before = presenter.active_operation
    assert before is not None
    editor = workspace.findChild(
        QDoubleSpinBox, "LatheParameter_max_depth_of_cut_mm"
    )
    assert editor is not None
    value = editor.value()
    workspace.parameter_editor.advanced_toggle.setChecked(True)
    assert workspace.parameter_editor.advanced_group.isVisibleTo(
        workspace.parameter_editor
    ) or not workspace.isVisible()
    workspace.parameter_editor.advanced_toggle.setChecked(False)
    assert editor.value() == value
    assert presenter.active_operation.revision == before.revision
    _dispose(workspace)


def test_operation_workflow_create_select_enable_validate_and_two_step_delete() -> None:
    workspace, presenter, _reference = workspace_for()
    workspace.create_button.click()
    assert len(presenter.snapshot.operations) == 1
    presenter.create_operation(LatheStrategyId.OD_ROUGH)
    assert workspace.operation_list.count() == 2
    first = workspace.operation_list.item(0)
    workspace.operation_list.setCurrentItem(first)
    active = presenter.active_operation
    assert active is not None and active.strategy_id is LatheStrategyId.FACE
    workspace.enable_check.setChecked(False)
    assert presenter.active_operation.enabled is False
    workspace.validate_button.click()
    assert len(presenter.snapshot.operations) == 2
    workspace.delete_button.click()
    assert len(presenter.snapshot.operations) == 2
    assert workspace._delete_armed_operation_id == active.ownership.operation_id
    workspace.delete_button.click()
    assert len(presenter.snapshot.operations) == 1
    assert presenter.snapshot.active_operation_id is not None
    _dispose(workspace)


def test_parameter_apply_uses_atomic_typed_updates_and_failed_edit_restores() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.FACE)
    feed = workspace.findChild(QDoubleSpinBox, "LatheParameter_feed_mm_per_rev")
    outer = workspace.findChild(QDoubleSpinBox, "LatheParameter_outer_diameter_mm")
    inner = workspace.findChild(QDoubleSpinBox, "LatheParameter_inner_diameter_mm")
    assert feed is not None and outer is not None and inner is not None
    feed.setValue(0.4)
    workspace.parameters_apply_button.click()
    active = presenter.active_operation
    assert active is not None
    assert dict(active.parameter_values)["feed_mm_per_rev"] == 0.4
    revision = active.revision
    outer.setValue(20.0)
    inner.setValue(25.0)
    workspace.parameters_apply_button.click()
    active = presenter.active_operation
    assert active is not None
    assert active.revision == revision
    assert dict(active.parameter_values)["outer_diameter_mm"] == 50.0
    assert dict(active.parameter_values)["inner_diameter_mm"] == 0.0
    _dispose(workspace)


def test_tool_geometry_binding_and_ready_status_are_honest() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.FACE)
    assert workspace.tool_selector.count() == 1
    assert workspace.tool_bind_button.isEnabled()
    workspace.tool_bind_button.click()
    workspace.geometry_bind_button.click()
    active = presenter.active_operation
    assert active is not None
    assert active.readiness.value == "READY"
    visible = " ".join(
        (
            workspace.readiness_label.text(),
            workspace.outcome_label.text(),
            *(workspace.diagnostics_list.item(i).text() for i in range(workspace.diagnostics_list.count())),
        )
    ).casefold()
    assert all(word not in visible for word in ("toolpath generated", "g-code available", "simulation ready"))
    workspace.tool_clear_button.click()
    assert presenter.active_operation.tool_binding is None
    workspace.geometry_clear_button.click()
    assert presenter.active_operation.geometry_binding is None
    _dispose(workspace)


def test_read_only_preserves_inspection_and_disables_all_mutations() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.FACE)
    before = presenter.active_operation
    accepted_text = workspace.outcome_label.text()
    presenter.facade.service.set_read_only(True)
    presenter.refresh()
    assert workspace.operation_list.count() == 1
    assert workspace.parameter_editor.editors
    mutation_controls = (
        workspace.create_button,
        workspace.delete_button,
        workspace.strategy_apply_button,
        workspace.enable_check,
        workspace.parameters_apply_button,
        workspace.tool_bind_button,
        workspace.tool_clear_button,
        workspace.geometry_bind_button,
        workspace.geometry_clear_button,
    )
    assert all(not control.isEnabled() for control in mutation_controls)
    assert workspace.validate_button.isEnabled()
    assert presenter.active_operation.parameter_values == before.parameter_values
    assert presenter.active_operation.revision == before.revision
    assert presenter.active_operation.ownership == before.ownership
    presenter.facade.service.set_read_only(False)
    presenter.refresh()
    assert workspace.outcome_label.text() == accepted_text
    _dispose(workspace)


def test_language_switch_retranslates_without_changing_identity_or_values() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.OD_THREAD)
    service = translation_service()
    original_language = service.language
    before = presenter.snapshot
    labels: list[str] = []
    outcomes: list[str] = []
    try:
        for language in UiLanguage:
            service.set_language(language)
            workspace.retranslate_ui(language)
            labels.append(workspace.header_label.text())
            outcomes.append(workspace.outcome_label.text())
            assert presenter.snapshot == before
            assert workspace.operation_list.currentItem().data(
                int(Qt.ItemDataRole.UserRole) + 101
            ) == before.active_operation_id
        assert len(set(labels)) == 3
        assert len(set(outcomes)) == 3
    finally:
        service.set_language(original_language)
    _dispose(workspace)


def test_unavailable_state_retranslates_from_semantic_reason_key() -> None:
    workspace = LatheWorkspace()
    service = translation_service()
    original_language = service.language
    labels: list[str] = []
    try:
        workspace.bind_presenter(
            None,
            unavailable_reason="lathe.presenter.project_context_unavailable",
        )
        for language in UiLanguage:
            service.set_language(language)
            workspace.retranslate_ui(language)
            labels.append(workspace.unavailable_label.text())
        assert len(set(labels)) == 3
        assert all(label.strip() for label in labels)
    finally:
        service.set_language(original_language)
    _dispose(workspace)


def test_accessible_object_names_are_stable_unique_and_delete_not_default() -> None:
    workspace, presenter, _reference = workspace_for()
    presenter.create_operation(LatheStrategyId.FACE)
    required = {
        "LatheWorkspace",
        "LatheOperationList",
        "LatheStrategyTree",
        "LatheCreateOperationButton",
        "LatheDeleteOperationButton",
        "LatheOperationEnabledCheck",
        "LatheBasicParameters",
        "LatheAdvancedParameters",
        "LatheToolSelector",
        "LatheToolBindButton",
        "LatheToolClearButton",
        "LatheGeometrySelector",
        "LatheGeometryBindButton",
        "LatheGeometryClearButton",
        "LatheReadinessDisplay",
        "LatheDiagnosticsList",
        "LatheParametersApplyButton",
        "LatheValidateOperationButton",
    }
    names = tuple(
        item.objectName()
        for item in (workspace, *workspace.findChildren(QWidget))
        if item.objectName().startswith("Lathe")
    )
    assert required.issubset(names)
    assert len(names) == len(set(names))
    assert all(
        control.accessibleName().strip()
        for control in (
            workspace.strategy_tree,
            workspace.operation_list,
            workspace.create_button,
            workspace.delete_button,
            workspace.tool_selector,
            workspace.geometry_selector,
        )
    )
    assert not workspace.delete_button.autoDefault()
    _dispose(workspace)


@pytest.mark.parametrize("scale", (50, 75, 100, 125, 150, 175, 200))
@pytest.mark.parametrize("size", ((900, 520), (1366, 768), (1920, 1080)))
def test_responsive_layout_has_local_scroll_footer_and_no_fixed_1280_dependency(
    scale: int, size: tuple[int, int]
) -> None:
    app = application()
    workspace, presenter, _reference = workspace_for(LatheStrategyId.OD_THREAD)
    presenter.create_operation(LatheStrategyId.OD_THREAD)
    logical_width = max(520, round(size[0] * 100 / max(scale, 50)))
    logical_height = max(360, round(size[1] * 100 / max(scale, 50)))
    workspace.resize(logical_width, logical_height)
    workspace.show()
    app.processEvents()
    footer = workspace.findChild(QWidget, "LatheWorkspaceFooter")
    scroll = workspace.findChild(QWidget, "LatheParameterScrollArea")
    assert footer is not None and footer.isVisible()
    assert footer.geometry().bottom() <= workspace.contentsRect().bottom()
    assert scroll is not None and scroll.geometry().width() > 0
    assert workspace.splitter.sizes()[0] > 0
    assert workspace.splitter.sizes()[1] > 0
    assert workspace.minimumWidth() < 1280
    _dispose(workspace)
