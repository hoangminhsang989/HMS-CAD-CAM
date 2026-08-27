"""Focused behavioral contracts for the R275 Rest production UI slice."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QSplitter

from hms_cadcam.cam.domain.tool_profiles import DEFAULT_TOOL_PROFILE_REGISTRY
from hms_cadcam.cam.domain.contour import ContourSide
from hms_cadcam.cam.domain.rest_contour import RestContourParameters
from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    Stage16AStrategyRegistry,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor.state import FunctionEditorDraftState
from hms_cadcam.ui.function_editor.model import FunctionEditorAction
from hms_cadcam.ui.function_editor.host import FunctionEditorHost
from hms_cadcam.ui.function_editor.strategies.rest_machining import (
    RestMachiningDependencyPresentation,
    RestMachiningEditorContext,
    build_rest_machining_schema,
    prepare_rest_machining_update,
    rest_machining_applied_values,
    rest_machining_validation_diagnostics,
    rest_result_presentation,
    rest_creation_candidate_presentation,
)
from hms_cadcam.ui.function_editor.widgets import FunctionEditorPage
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.operation_creation_adapter import (
    Stage16AOperationCreationAdapter,
)
from hms_cadcam.ui.i18n import (
    TranslationService,
    UiLanguage,
    build_default_catalogs,
    set_translation_service,
    translation_service,
)
from test_rest_contour_core_r271 import _positive_inputs
from test_rest_finishing_core_r273 import _inputs as _finishing_inputs

_INTEGRATION_FIXTURES = Path(__file__).parents[1] / "integration"
if str(_INTEGRATION_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_INTEGRATION_FIXTURES))
from test_rest_contour_project_lifecycle_r272 import (  # noqa: E402
    _profile_resolver,
    _project_with_published_upstream,
)


def _contour_context() -> RestMachiningEditorContext:
    inputs = _positive_inputs()
    candidate = inputs.foundation.material.candidate
    assert candidate is not None
    operation = inputs.setup.operation_tree.get_operation(
        candidate.dependency.consumer_operation_id
    )
    return RestMachiningEditorContext(
        "Rest Contour",
        operation,
        (inputs.assembly,),
        (inputs.tool,),
        RestMachiningDependencyPresentation(
            candidate.producer_operation_id,
            "Upstream",
            "CURRENT",
            "Provenance current",
        ),
    )


def _finishing_context() -> RestMachiningEditorContext:
    inputs = _finishing_inputs()
    operation = inputs.setup.operation_tree.get_operation(inputs.consumer_operation_id)
    return RestMachiningEditorContext(
        "Rest Finishing",
        operation,
        (inputs.assembly,),
        (inputs.tool,),
        RestMachiningDependencyPresentation(
            inputs.producer_dependency.producer_operation_id,
            "Rest Contour upstream",
            "CURRENT",
            "R272 completion current",
        ),
    )


def _persisted_rest_workspace(tmp_path: Path) -> tuple[
    ProjectService, CamWorkspace, object, object
]:
    """Reopen one genuine R272 project through the production workspace."""
    service = ProjectService.create_default(tmp_path / "config")
    project = service.new_project(tmp_path, "R275 persisted Rest editor")
    inputs, _job, operation_id = _project_with_published_upstream(
        service, tmp_path
    )
    service.save()
    service.close_project()
    service.open_project(project.root_path)
    workspace = CamWorkspace(
        service,
        lambda: None,
        profile_resolver=_profile_resolver(inputs),
    )
    reopened_setup = service.cam_snapshot.jobs[0].setups[0]
    node = next(
        value
        for value in reopened_setup.operation_tree.nodes
        if value.operation_id == operation_id
    )
    workspace.refresh(("operation", str(node.node_id)))
    return service, workspace, inputs, operation_id


def test_persisted_rest_operation_opens_real_production_host_with_preview(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    service, workspace, _inputs, operation_id = _persisted_rest_workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    assert session.operation_key == str(operation_id)
    assert callable(session.preview_callback)

    host = FunctionEditorHost(
        workspace.editor,
        workspace.tree,
        workspace.editor.apply_draft,
        production_provider=workspace.production_function_editor_session,
        selection_restore=workspace.select_identity,
        selection_exists=workspace.selection_exists,
        follow_selection=False,
    )
    assert host.open_current_selection()
    assert host.current_mode == "framework"
    assert host.active_session is not None
    assert host.active_session.operation_key == str(operation_id)

    state = FunctionEditorDraftState(
        session.schema,
        session.applied_mapping(),
        project_key=session.project_key,
        operation_key=session.operation_key,
        generation=session.generation,
        validation_callback=session.validation_callback,
    )
    preview = session.preview_callback(state.preview_request())
    assert "chưa khả dụng" in str(preview)
    assert workspace._displayed_operation_id is None

    host.close()
    workspace.close()
    service.close_project(discard_changes=True)
    app.processEvents()


def test_ui_created_rest_contour_is_inside_and_backend_accepted(
    tmp_path: Path,
) -> None:
    service, workspace, inputs, _operation_id = _persisted_rest_workspace(tmp_path)
    snapshot = service.cam_snapshot
    job = snapshot.jobs[0]
    setup = job.setups[0]
    adapter = Stage16AOperationCreationAdapter(
        service,
        profile_pick_provider=lambda: inputs.profile_descriptor.reference,
        profile_resolver=_profile_resolver(inputs),
    )
    session = OperationCreationSession.start(
        project_id=service.current_project.manifest.project_id,
        project_generation=service.cam_generation,
        job_id=job.job_id,
        setup_id=setup.setup_id,
        parent_node_id=setup.operation_tree.root_id,
    ).select_strategy("rest_contour_3axis")
    choice = next(
        value
        for value in adapter.tool_choices(session)
        if value.compatible and value.tool_id == inputs.tool.tool_id
    )
    session = session.select_tool(choice)
    completed: list[bool] = []
    binding = adapter.build_editor(
        session,
        claim_finish=lambda: session,
        complete_finish=completed.append,
    )
    authoritative = inputs.parameters
    configured_values = {
        **binding.applied_values,
        "top_height": authoritative.top_height.value,
        "final_depth": authoritative.final_depth.value,
        "stepdown": authoritative.stepdown.value,
        "radial_stock_allowance": authoritative.radial_stock_allowance.value,
        "axial_stock_allowance": authoritative.axial_stock_allowance.value,
        "clearance_height": authoritative.clearance_height.value,
        "retract_height": authoritative.retract_height.value,
        "cutting_feed_rate": authoritative.cutting_feed_rate.value,
        "plunge_feed_rate": authoritative.plunge_feed_rate.value,
        "spindle_speed": authoritative.spindle_speed.value,
        "tolerance": authoritative.tolerance.value,
        "lead_in_length": authoritative.lead_in_length.value,
        "lead_out_length": authoritative.lead_out_length.value,
    }
    assert binding.validation_callback(configured_values) == ()
    created_projection = binding.finish_callback(configured_values)
    assert completed == [True]

    created = next(
        operation
        for current_job in service.cam_snapshot.jobs
        for current_setup in current_job.setups
        for operation in current_setup.operation_tree.operations
        if operation.operation_id == created_projection.operation_id
    )
    parameters = RestContourParameters.from_operation_parameters(created.parameters)
    assert parameters.side is ContourSide.INSIDE
    result = service.generate_rest_contour(
        created.operation_id,
        profile_resolver=_profile_resolver(inputs),
    )
    assert result.status.value == "SUCCESS", (
        result.diagnostic_code,
        result.message,
    )

    workspace.close()
    service.close_project(discard_changes=True)


def test_creation_registry_exposes_both_rest_strategies_and_real_tool_law() -> None:
    schemas = {
        schema.strategy_id: schema for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
    }
    choices = {choice.strategy_id: choice for choice in Stage16AStrategyRegistry().choices()}
    assert set(schemas) >= {"rest_contour_3axis", "rest_finishing_3axis"}
    assert set(choices) >= {"rest_contour_3axis", "rest_finishing_3axis"}
    assert schemas["rest_contour_3axis"].supported_tool_families == (
        "end_mill",
        "ball_end_mill",
        "bull_nose_end_mill",
    )
    assert schemas["rest_finishing_3axis"].supported_tool_families == ("end_mill",)


def test_rest_finishing_schema_is_manual_flat_constant_z_x_raster() -> None:
    context = _finishing_context()
    schema = build_rest_machining_schema(context)
    values = rest_machining_applied_values(context)
    assert "MANUAL ONLY" in str(values["parameter_mode"])
    assert "Flat End Mill" in str(values["strategy_law"])
    assert "Constant-Z planar" in str(values["strategy_law"])
    assert values["raster_direction"] == "X axis"
    assert not any("auto" in field.field_id.casefold() for field in schema.fields)


def test_rest_schema_has_basic_advanced_dependency_and_no_critical_elision() -> None:
    for context in (_contour_context(), _finishing_context()):
        schema = build_rest_machining_schema(context)
        sections = {section.section_id: section for section in schema.sections}
        assert set(sections) >= {
            "operation",
            "dependency",
            "tool",
            "basic_parameters",
            "advanced_parameters",
        }
        text = " ".join(
            [schema.summary.strategy]
            + [section.title for section in schema.sections]
            + [field.label + " " + str(field.value) for field in schema.fields]
        )
        assert "Material State" in text
        assert "…" not in text


def test_numeric_validation_rejects_nan_inf_and_finishing_diameter_overrun() -> None:
    context = _finishing_context()
    schema = build_rest_machining_schema(context)
    values = rest_machining_applied_values(context)
    for bad in (float("nan"), float("inf"), float("-inf")):
        candidate = {**values, "stepover": bad}
        assert rest_machining_validation_diagnostics(schema, context, candidate)
    diameter = context.tool_definitions[0].cutting_geometry.diameter.value
    assert rest_machining_validation_diagnostics(
        schema, context, {**values, "stepover": diameter + 0.01}
    )


def test_valid_edit_builds_persistable_operation_projection() -> None:
    context = _contour_context()
    values = rest_machining_applied_values(context)
    update = prepare_rest_machining_update(
        context, {**values, "operation_name": "Rest Contour edited", "stepdown": 0.25}
    )
    assert update.operation_name == "Rest Contour edited"
    assert update.operation.strategy_key == "rest_contour_3axis"
    assert dict(update.operation.parameters.values)["stepdown"] == 0.25
    assert update.operation.revision > context.operation.revision


def test_no_work_and_cancel_are_neutral_typed_states() -> None:
    assert rest_result_presentation("NO_REST_MATERIAL") == (
        "NO_WORK",
        "Không còn vật liệu Rest cần gia công.",
        False,
    )
    status, message, error = rest_result_presentation(
        "FAILURE", "rest_contour.cancelled", "cancelled"
    )
    assert status == "CANCELLED"
    assert "output một phần" in message
    assert error is False


def test_creation_dependency_is_candidate_not_fabricated_current() -> None:
    presentation = rest_creation_candidate_presentation("producer-id", "Upstream")
    assert presentation.status == "CANDIDATE"
    assert "ProjectService" in presentation.detail
    assert "xác minh hiện hành" in presentation.detail
    assert "CURRENT" not in presentation.status


def test_stale_cam_task_callbacks_cannot_clear_or_publish_newer_task() -> None:
    old_task = object()
    new_task = object()
    displayed: list[object] = []
    errors: list[str] = []
    harness = SimpleNamespace(
        _cam_calculation_task=new_task,
        _cam_calculation_generation=7,
        _cam_calculation_operation_id=object(),
        _cam_calculation_strategy="rest_finishing",
        _generation=7,
        _toolpath_display=lambda artifact: displayed.append(artifact),
        _error=errors.append,
    )
    CamWorkspace._cam_calculation_completed(
        harness,
        old_task,
        SimpleNamespace(status="SUCCESS", publication=SimpleNamespace(artifact=object())),
    )
    CamWorkspace._cam_calculation_finished(harness, old_task)
    assert harness._cam_calculation_task is new_task
    assert displayed == []
    assert errors == []


def test_rest_success_rejects_mismatched_publication_operation() -> None:
    task = object()
    expected = object()
    foreign = object()
    displayed: list[object] = []
    errors: list[str] = []
    harness = SimpleNamespace(
        _cam_calculation_task=task,
        _cam_calculation_generation=9,
        _cam_calculation_operation_id=expected,
        _cam_calculation_strategy="rest_contour",
        _generation=9,
        _toolpath_display=lambda artifact: displayed.append(artifact),
        _error=errors.append,
    )
    result = SimpleNamespace(
        status="SUCCESS",
        diagnostic_code=None,
        message="",
        publication=SimpleNamespace(
            operation=SimpleNamespace(operation_id=foreign), artifact=object()
        ),
    )
    CamWorkspace._cam_calculation_completed(harness, task, result)
    assert displayed == []
    assert errors == ["Kết quả Rest không khớp nguyên công đang tính."]


def test_r275_locale_contract_english_and_explicit_korean_fallback() -> None:
    previous = translation_service()
    service = TranslationService(build_default_catalogs())
    set_translation_service(service)
    try:
        with service.using(UiLanguage.EN_US):
            assert (
                service.translate_key("r275.rest.material_state_source")
                == "Material State source"
            )
            assert (
                service.translate_key("r275.rest.manual_max_stepdown")
                == "Manual maximum stepdown"
            )
            schema = build_rest_machining_schema(_finishing_context())
            labels = {field.field_id: field.label for field in schema.fields}
            assert labels["nominal_target_z"] == "Nominal target Z"
            assert labels["max_stepdown"] == "Manual maximum stepdown"
        service.clear_diagnostics()
        with service.using(UiLanguage.KO_KR):
            assert (
                service.translate_key("r275.rest.material_state_source")
                == "Nguồn Material State"
            )
            assert (
                service.translate_key("r275.rest.manual_max_stepdown")
                == "Bước xuống tối đa thủ công"
            )
            schema = build_rest_machining_schema(_finishing_context())
            assert all("r275." not in field.label for field in schema.fields)
            assert all("…" not in field.label for field in schema.fields)
        fallback_keys = {
            item.key
            for item in service.diagnostics
            if item.resolution == "VI_VN_FALLBACK"
        }
        assert {
            "r275.rest.material_state_source",
            "r275.rest.manual_max_stepdown",
        } <= fallback_keys
    finally:
        set_translation_service(previous)


def test_production_widget_uses_non_collapsible_resizable_split_and_indeterminate_progress() -> None:
    app = QApplication.instance() or QApplication([])
    context = _finishing_context()
    schema = build_rest_machining_schema(context)
    page = FunctionEditorPage(
        FunctionEditorDraftState(schema, rest_machining_applied_values(context))
    )
    page.resize(1000, 700)
    page.show()
    app.processEvents()
    splitters = page.findChildren(QSplitter, "RestMachiningFunctionEditorSplitter")
    assert len(splitters) == 1
    splitter = splitters[0]
    assert splitter.count() == 2
    assert not splitter.childrenCollapsible()
    assert all(size > 0 for size in splitter.sizes())
    assert all(splitter.widget(index).minimumWidth() >= 260 for index in range(2))
    page.set_calculation_active(True)
    assert page.calculation_progress.progress.minimum() == 0
    assert page.calculation_progress.progress.maximum() == 0
    assert "không có phần trăm backend" in page.calculation_progress.percentage.text()
    assert page.footer.buttons[FunctionEditorAction.CALCULATE].isEnabled() is False
    page.set_rest_result(
        "NO_WORK",
        "Không còn vật liệu Rest cần gia công; không tạo toolpath giả.",
    )
    assert page.findChild(QLabel, "RestMachiningStatusTitle").text() == "NO_WORK"
    assert "không tạo toolpath giả" in page.findChild(
        QLabel, "RestMachiningStatusDetail"
    ).text()
    legend = {
        label.text()
        for label in page.findChild(
            QLabel, "RestMachiningToolpathLegendTitle"
        ).parent().findChildren(QLabel)
    }
    assert {"CUTTING", "RAPID", "LEAD-IN", "LEAD-OUT"} <= legend
    lead_in = page.findChild(QLabel, "ToolpathLeadInSwatch")
    assert "#ffffff" in lead_in.styleSheet()
    assert "border: 1px solid" in lead_in.styleSheet()
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(
        None, QEvent.Type.DeferredDelete
    )
