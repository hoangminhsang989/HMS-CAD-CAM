"""Production Function Editor contracts for Parallel Finishing Stage 8A.2.3."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QHeaderView, QLabel, QScrollArea, QWidget

from hms_cadcam.cam.application import basic_mill_resources, basic_parallel_resources
from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    PARALLEL_FINISHING_STRATEGY_VERSION,
    ParallelCutDirection,
    ParallelFinishingParameters,
    ParallelGeometryEvidence,
    ParallelProgress,
    ParallelProgressPhase,
    ParallelSafetyStatus,
    calculate_and_publish_parallel_finishing,
    parallel_artifact_has_safe_contract,
)
from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DiagnosticCode,
    DiagnosticSeverity,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    ValidationDiagnostic,
)
from hms_cadcam.cam.toolpath import MarkerEvent
from hms_cadcam.ui.function_editor import (
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.parallel_widgets import (
    ParallelSafetyDiagnosticsDialog,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    PARALLEL_EDITOR_ID,
    ParallelEditorContext,
    ParallelEditorDraftContext,
    build_parallel_schema,
    parallel_applied_values,
    parallel_draft_derived_values,
    parallel_safety_presentation,
    parallel_validation_diagnostics,
    prepare_parallel_update,
)
from hms_cadcam.ui.operation_manager_status import parallel_safety_status
from hms_cadcam.ui.ui_tokens import CAM_POPUP_DENSITY
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from tests.unit._parallel_finishing_fixtures import planar_fixture
from tests.unit._parallel_finishing_safety_fixtures import (
    adjacent_wall_fixture,
    holder_collision_fixture,
    safe_holder_fixture,
)
from tests.unit.test_parallel_finishing_persistence import _snapshot


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def assert_widget_fully_visible(widget: QWidget, ancestor: QWidget) -> None:
    """Assert the complete widget rectangle lies inside an owning content rect."""
    assert widget.isVisible()
    mapped = QRect(widget.mapTo(ancestor, QPoint()), widget.size())
    assert ancestor.contentsRect().contains(mapped), (
        f"{widget.objectName() or type(widget).__name__} {mapped.getRect()} "
        f"vượt {ancestor.objectName() or type(ancestor).__name__} "
        f"{ancestor.contentsRect().getRect()}"
    )
    mask = widget.mask()
    assert mask.isEmpty() or mask.boundingRect().contains(widget.rect())


def assert_no_sibling_overlap(
    widgets: tuple[QWidget, ...],
    ancestor: QWidget,
) -> None:
    """Assert visible grid siblings have disjoint rectangles in one coordinate space."""
    visible = tuple(widget for widget in widgets if widget.isVisible())
    rectangles = {
        widget: QRect(widget.mapTo(ancestor, QPoint()), widget.size())
        for widget in visible
    }
    for index, first in enumerate(visible):
        for second in visible[index + 1 :]:
            assert not rectangles[first].intersects(rectangles[second]), (
                f"{first.objectName()} overlap {second.objectName()}"
            )


def assert_text_baseline_visible(label: QLabel) -> None:
    """Assert a visible label owns enough vertical space for Vietnamese glyphs."""
    assert label.isVisible()
    metrics = label.fontMetrics()
    assert label.contentsRect().height() >= metrics.height()
    assert label.contentsRect().height() >= metrics.ascent() + metrics.descent()


def assert_content_fits_without_scroll(scroll_area: QScrollArea) -> None:
    """Assert a zero scroll range is backed by real content geometry."""
    assert scroll_area.horizontalScrollBar().maximum() == 0
    assert scroll_area.verticalScrollBar().maximum() == 0
    content = scroll_area.widget()
    assert content is not None
    assert content.sizeHint().height() <= scroll_area.viewport().height()
    assert content.minimumSizeHint().width() <= scroll_area.viewport().width()


def _context(
    fixture=None,
    *,
    operation: Operation | None = None,
    artifact=None,
    report=None,
    holders=(),
) -> tuple[ParallelEditorContext, object]:
    fixture = fixture or planar_fixture(stepover=5.0)
    operation = operation or fixture.operation
    setup = _snapshot(fixture, operation).jobs[0].setups[0]
    machine = basic_mill_resources(LengthUnit.MM)[3]
    return (
        ParallelEditorContext(
            "Parallel Finishing",
            operation,
            setup,
            fixture.zone.job_id,
            fixture.zone.project_id,
            fixture.zone,
            (fixture.assembly,),
            (fixture.tool,),
            tuple(holders),
            (machine,),
            artifact,
            report,
            geometry_evidence=ParallelGeometryEvidence(
                0.0, 10.0, 0.0, 10.0, "Hộp bao kiểm thử"
            ),
        ),
        machine,
    )


def _valid_values(context: ParallelEditorContext, machine) -> dict[str, object]:
    values = parallel_applied_values(context)
    values["machine_id"] = str(machine.machine_id)
    return values


def _parallel_page() -> FunctionEditorPage:
    context, machine = _context()
    return FunctionEditorPage(
        FunctionEditorDraftState(
            build_parallel_schema(context),
            _valid_values(context, machine),
            draft_transform_callback=lambda values: parallel_draft_derived_values(
                context,
                ParallelEditorDraftContext(
                    context.zone.part_surfaces.selection.surfaces,
                    geometry_evidence=context.geometry_evidence,
                ),
                values,
            ),
        )
    )


def test_parallel_editor_construction_has_compact_sections_and_no_expert() -> None:
    context, _machine = _context()
    schema = build_parallel_schema(context)
    assert schema.editor_id == PARALLEL_EDITOR_ID
    assert [section.title for section in schema.sections] == [
        "OPERATION",
        "GEOMETRY",
        "TOOL",
        "CHẤT LƯỢNG",
        "TÓM TẮT TÍNH TOÁN TỰ ĐỘNG",
        "DIRECTION",
        "CUT PARAMETERS",
        "LEVELS / LINKING",
        "ADVANCED",
        "CAPABILITY AND SAFETY",
        "SUMMARY",
    ]
    advanced = next(item for item in schema.sections if item.title == "ADVANCED")
    assert not advanced.default_expanded
    assert all(item.title != "EXPERT" for item in schema.sections)
    basic = schema.visible_sections(
        parallel_applied_values(context), ParameterDisclosureLevel.BASIC
    )
    assert [item.section_id for item in basic] == [
        "geometry",
        "tool",
        "quality",
        "automatic_summary",
    ]
    assert schema.footer.actions.index(FunctionEditorAction.APPLY) < schema.footer.actions.index(
        FunctionEditorAction.CALCULATE
    )


def test_parallel_default_resource_is_supported_and_holder_verified() -> None:
    tool, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM)
    assert tool.family.value == "ball_end_mill"
    assert assembly.holder_id == holder.holder_id
    assert assembly.expected_holder_fingerprint == holder.content_fingerprint
    assert OperationCapability.MILLING in machine.capabilities.operations


def test_operation_to_ui_exposes_foundation_values_and_capability_limits() -> None:
    context, _machine = _context()
    values = parallel_applied_values(context)
    assert values["operation_type"] == "Parallel Finishing"
    assert 0.0 < float(values["stepover_mm"]) < 5.0
    assert values["cut_direction"] == ParallelCutDirection.ZIGZAG.value
    assert "fixed 3-axis" in str(values["capability_summary"])
    assert "production Post" in str(values["unsupported_summary"])
    assert values["machine_ready_clearance"] == "Chưa xác minh"


def test_apply_candidate_updates_operation_and_zone_atomically_in_memory() -> None:
    context, machine = _context()
    values = _valid_values(context, machine)
    values.update(
        {
            "stepover_mm": "1.25",
            "direction_angle_degrees": "37.5",
            "cut_direction": ParallelCutDirection.ZIGZAG.value,
            "tolerance_mm": "0.02",
            "surface_allowance_mm": "0.1",
            "stepover_override_enabled": True,
            "direction_override_enabled": True,
            "ordering_override_enabled": True,
            "tolerance_override_enabled": True,
            "allowance_override_enabled": True,
            "clearance_z_mm": "60",
            "retract_z_mm": "45",
            "link_clearance_mm": "1.5",
        }
    )
    update = prepare_parallel_update(
        context,
        ParallelEditorDraftContext(context.zone.part_surfaces.selection.surfaces),
        values,
    )
    restored = ParallelFinishingParameters.from_operation_parameters(
        update.operation.parameters
    )
    assert update.operation.revision == context.operation.revision.next()
    assert update.operation.artifact_state.status is ArtifactStatus.DIRTY
    assert restored.stepover_mm == 1.25
    assert restored.direction_angle_degrees == 37.5
    assert restored.clearance_z_mm == 60.0
    assert update.zone.tolerance.chordal_tolerance == 0.02
    assert update.zone.allowance.part_normal == 0.1
    assert update.safe_motion_policy.retract_z == 45.0


def test_draft_cancel_does_not_mutate_operation_or_artifact() -> None:
    context, machine = _context()
    original = context.operation.to_dict()
    schema = build_parallel_schema(context)
    state = FunctionEditorDraftState(schema, _valid_values(context, machine))
    state.edit("stepover_mm", "0.75")
    state.reset_draft()
    assert context.operation.to_dict() == original
    assert state.values["stepover_mm"] == _valid_values(context, machine)["stepover_mm"]


@pytest.mark.parametrize(
    ("field_id", "value", "needle"),
    (
        ("stepover_mm", "0", "bước ngang"),
        ("stepover_mm", "nan", "số hữu hạn"),
        ("tolerance_mm", "0", "dung sai"),
        ("surface_allowance_mm", "-1", "lượng dư"),
        ("clearance_z_mm", "20", "clearance"),
        ("link_clearance_mm", "-1", "link clearance"),
    ),
)
def test_structured_validation_preserves_invalid_draft(
    field_id: str, value: str, needle: str
) -> None:
    context, machine = _context()
    schema = build_parallel_schema(context)
    values = _valid_values(context, machine)
    values[field_id] = value
    toggle = {
        "stepover_mm": "stepover_override_enabled",
        "tolerance_mm": "tolerance_override_enabled",
        "surface_allowance_mm": "allowance_override_enabled",
    }.get(field_id)
    if toggle is not None:
        values[toggle] = True
    diagnostics = parallel_validation_diagnostics(
        schema,
        context,
        ParallelEditorDraftContext(context.zone.part_surfaces.selection.surfaces),
        values,
    )
    assert diagnostics and diagnostics[0].severity.name == "ERROR"
    assert needle in diagnostics[0].message.casefold()
    assert values[field_id] == value


def test_missing_geometry_and_unsupported_tool_fail_closed() -> None:
    context, machine = _context()
    schema = build_parallel_schema(context)
    missing = parallel_validation_diagnostics(
        schema,
        context,
        ParallelEditorDraftContext(()),
        {**_valid_values(context, machine), "selected_face_count": "0"},
    )
    assert missing[0].code.startswith("parallel.")
    unsupported_tool, _holder, unsupported, _other_machine = basic_mill_resources(
        LengthUnit.MM
    )
    bad = replace(
        context,
        tool_assemblies=(unsupported,),
        tool_definitions=(unsupported_tool,),
    )
    values = parallel_applied_values(bad)
    values["tool_assembly_id"] = str(unsupported.assembly_id)
    values["machine_id"] = str(machine.machine_id)
    diagnostics = parallel_validation_diagnostics(
        build_parallel_schema(bad),
        bad,
        ParallelEditorDraftContext(bad.zone.part_surfaces.selection.surfaces),
        values,
    )
    assert "UNSUPPORTED_TOOL_GEOMETRY" in diagnostics[0].message


def test_holder_absent_wording_never_claims_holder_safe() -> None:
    context, _machine = _context()
    safety = parallel_safety_presentation(context)
    assert "Chưa khai báo Holder" in safety.holder_state
    assert safety.unverified_components == "Holder"
    assert "holder safe" not in safety.holder_state.casefold()


def test_safe_ready_ui_requires_v3_contract_and_keeps_machine_ready_false(
    tmp_path,
) -> None:
    fixture = planar_fixture(stepover=5.0)
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.artifact is not None
    context, _machine = _context(
        fixture,
        operation=result.operation,
        artifact=result.artifact,
        report=result.safety_report,
    )
    safety = parallel_safety_presentation(context)
    assert safety.status.startswith("An toàn")
    assert safety.machine_ready_clearance == "Chưa xác minh"
    assert safety.simulation_gate.startswith("Available")
    assert safety.post_gate.startswith("Blocked")
    assert parallel_artifact_has_safe_contract(result.artifact)
    assert build_parallel_schema(context).field("simulation_gate").action_id == (
        "open_parallel_simulation"
    )
    assert PARALLEL_FINISHING_ALGORITHM_VERSION == 3
    assert PARALLEL_FINISHING_STRATEGY_VERSION == 1


def test_unsafe_and_unknown_reports_never_become_ready(tmp_path) -> None:
    unsafe_fixture, _ = adjacent_wall_fixture()
    unsafe = calculate_and_publish_parallel_finishing(
        tmp_path / "unsafe",
        unsafe_fixture.operation,
        unsafe_fixture.context,
        assembly=unsafe_fixture.assembly,
        tool=unsafe_fixture.tool,
    )
    assert unsafe.safety_report is not None
    assert unsafe.safety_report.status is ParallelSafetyStatus.UNSAFE
    unsafe_context, _machine = _context(
        unsafe_fixture,
        operation=unsafe.operation,
        report=unsafe.safety_report,
    )
    assert parallel_safety_presentation(unsafe_context).status == "Không an toàn"

    unknown_fixture, _holder = holder_collision_fixture()
    unknown = calculate_and_publish_parallel_finishing(
        tmp_path / "unknown",
        unknown_fixture.operation,
        unknown_fixture.context,
        assembly=unknown_fixture.assembly,
        tool=unknown_fixture.tool,
    )
    assert unknown.safety_report is not None
    assert unknown.safety_report.status is ParallelSafetyStatus.UNKNOWN
    unknown_context, _machine = _context(
        unknown_fixture,
        operation=unknown.operation,
        report=unknown.safety_report,
    )
    assert parallel_safety_presentation(unknown_context).status == "Chưa xác định"


def test_verified_holder_scope_lists_all_tool_components(tmp_path) -> None:
    fixture, holder = safe_holder_fixture()
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder,
    )
    assert result.accepted and result.safety_report is not None
    context, _machine = _context(
        fixture,
        operation=result.operation,
        artifact=result.artifact,
        report=result.safety_report,
        holders=(holder,),
    )
    safety = parallel_safety_presentation(context)
    assert safety.holder_state == (
        "Holder đã được xác minh · Đã kiểm tra Dao cắt, Cán dao và Holder"
    )
    assert safety.checked_components == "Dao cắt, Cán dao và Holder"
    assert safety.unverified_components == "Không có"


def test_stale_holder_reference_is_unavailable_and_blocks_calculate() -> None:
    fixture, holder = safe_holder_fixture()
    stale_holder = replace(holder, revision=holder.revision.next())
    context, machine = _context(fixture, holders=(stale_holder,))
    values = _valid_values(context, machine)
    diagnostics = parallel_validation_diagnostics(
        build_parallel_schema(context),
        context,
        ParallelEditorDraftContext(context.zone.part_surfaces.selection.surfaces),
        values,
    )
    assert any(
        item.severity.name == "ERROR" and "Holder verification unavailable" in item.message
        for item in diagnostics
    )
    assert parallel_safety_presentation(context).holder_state == "Holder không hợp lệ"


def test_operation_manager_parallel_status_is_textual_and_scoped(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    before = parallel_safety_status(fixture.operation, None)
    assert before is not None and before.text == "CHƯA KIỂM TRA"
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    ready = parallel_safety_status(result.operation, result.artifact)
    assert ready is not None and ready.text == "ĐÃ KIỂM TRA PHẠM VI"
    assert "chưa được xác minh" in ready.tooltip
    assert len(ready.tooltip) > len(ready.text)


def test_safety_diagnostics_table_keeps_headers_rows_and_full_cell_access(
    tmp_path,
) -> None:
    application = _application()
    fixture, _wall = adjacent_wall_fixture()
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.safety_report is not None
    dialog = ParallelSafetyDiagnosticsDialog(
        result.safety_report,
        result.operation.diagnostics,
    )
    dialog.show()
    application.processEvents()

    expected_headers = (
        "Mã",
        "Mức độ",
        "Lượt cắt",
        "Đoạn",
        "Chuyển động",
        "Thành phần",
        "Hình học",
        "Khoảng cách gần nhất",
        "Độ xuyên",
        "Số lần xuất hiện",
        "Thông báo",
    )
    assert tuple(
        dialog.table.horizontalHeaderItem(column).text()
        for column in range(dialog.table.columnCount())
    ) == expected_headers
    header = dialog.table.horizontalHeader()
    assert all(
        header.sectionResizeMode(column) is QHeaderView.ResizeMode.Fixed
        for column in (1, 2, 3, 4, 5, 7, 8, 9)
    )
    assert all(
        header.sectionResizeMode(column) is QHeaderView.ResizeMode.Stretch
        for column in (0, 6, 10)
    )
    assert dialog.table.horizontalScrollBarPolicy() is (
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert dialog.table.isSortingEnabled()
    assert dialog.table.wordWrap()
    assert dialog.table.rowCount() >= 1
    font_metrics = header.fontMetrics()
    for column in (1, 2, 3, 4, 5, 7, 8, 9):
        text = expected_headers[column]
        assert header.sectionSize(column) >= font_metrics.horizontalAdvance(text) + 12
    for column, text in enumerate(expected_headers):
        assert dialog.table.horizontalHeaderItem(column).toolTip() == text
    for column in range(dialog.table.columnCount()):
        item = dialog.table.item(0, column)
        assert item is not None and item.toolTip() == item.text()
    geometry = dialog.table.item(0, 6)
    assert geometry is not None
    component = dialog.table.item(0, 5)
    code = dialog.table.item(0, 0)
    assert component is not None and component.text() == "Dao cắt"
    assert code is not None and code.text().startswith("parallel.safety.")
    assert "Tham chiếu hình học:" in geometry.text()
    assert "geometry_reference" not in geometry.text()
    assert dialog.summary.toolTip().startswith("Mã phạm vi nội bộ:")
    dialog.table.setCurrentCell(0, 6)
    assert dialog.copy_selected_value()
    assert QApplication.clipboard().text() == geometry.text()
    assert dialog.close_button.isVisible()
    dialog.close()
    dialog.deleteLater()
    application.processEvents()


def test_v2_or_invalid_safety_evidence_blocks_simulation_and_post(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    result = calculate_and_publish_parallel_finishing(
        tmp_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.artifact is not None
    events = tuple(
        replace(
            event,
            metadata=tuple(
                (key, "2" if key == "algorithm_version" else value)
                for key, value in event.metadata
            ),
        )
        if isinstance(event, MarkerEvent)
        and event.semantic_key == "parallel.safety.contract"
        else event
        for event in result.artifact.events
    )
    v2 = replace(result.artifact, events=events, artifact_fingerprint=None)
    context, _machine = _context(
        fixture,
        operation=result.operation,
        artifact=v2,
    )
    safety = parallel_safety_presentation(context)
    assert safety.status == "Chưa tính toán"
    assert safety.simulation_gate.startswith("Blocked")
    assert safety.post_gate.startswith("Blocked")
    assert build_parallel_schema(context).field("simulation_gate").action_id == ""
    manager = parallel_safety_status(result.operation, v2)
    assert manager is not None and manager.text == "AN TOÀN KHÔNG HỢP LỆ"


def test_cancelled_state_uses_persisted_text_evidence_without_exception() -> None:
    fixture = planar_fixture()
    diagnostic = ValidationDiagnostic(
        DiagnosticSeverity.ERROR,
        DiagnosticCode.PARALLEL_CANCELLED,
        "Parallel calculation cancelled.",
        (("safety_status", "cancelled"),),
    )
    operation = replace(
        fixture.operation,
        artifact_state=replace(
            fixture.operation.artifact_state,
            status=ArtifactStatus.FAILED,
            dirty_reasons=(),
            diagnostics=(diagnostic,),
        ),
    )
    context, _machine = _context(fixture, operation=operation)
    assert parallel_safety_presentation(context).status == "Đã hủy"
    manager = parallel_safety_status(operation, None)
    assert manager is not None and manager.text == "ĐÃ HỦY"


def test_extended_v1_payload_round_trip_preserves_levels_without_schema_bump() -> None:
    fixture = planar_fixture()
    value = ParallelFinishingParameters(
        ParallelFinishingParameters.from_operation_parameters(
            fixture.operation.parameters
        ).zone_id,
        0.8,
        clearance_z_mm=72.0,
        retract_z_mm=61.0,
        link_clearance_mm=2.5,
    )
    restored = ParallelFinishingParameters.from_operation_parameters(
        value.to_operation_parameters()
    )
    assert restored == value
    assert restored.to_operation_parameters().strategy_version == 1


def test_parallel_page_is_responsive_and_progress_is_non_modal() -> None:
    application = _application()
    context, machine = _context()
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            build_parallel_schema(context),
            _valid_values(context, machine),
            draft_transform_callback=lambda values: parallel_draft_derived_values(
                context,
                ParallelEditorDraftContext(
                    context.zone.part_surfaces.selection.surfaces,
                    geometry_evidence=context.geometry_evidence,
                ),
                values,
            ),
        )
    )
    page.show()
    assert page.parallel_direction_preview is not None
    page._field_changed("direction_override_enabled", True)
    page._field_changed("direction_angle_degrees", "42.5")
    assert page.parallel_direction_preview._angle_degrees == 42.5
    page.reset_draft()
    for width in (360, 460, 520):
        page.resize(width, 700)
        application.processEvents()
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        assert page.footer.isVisible()
    page.set_calculation_active(True)
    page.update_calculation_progress(
        ParallelProgress(
            context.operation.operation_id,
            ParallelProgressPhase.SAFETY_VALIDATION,
            7,
            10,
        )
    )
    application.processEvents()
    assert page.calculation_progress.isVisible()
    assert page.calculation_progress.testAttribute(
        Qt.WidgetAttribute.WA_StyledBackground
    )
    assert 84 <= page.calculation_progress.height() <= 90
    assert page.calculation_progress.phase.text() == "Giai đoạn: Kiểm tra an toàn"
    assert page.calculation_progress.percentage.text() == "Tổng thể: 70%"
    assert page.calculation_progress.detail.text() == "Hạng mục: 7 / 10"
    assert page.calculation_progress.cancel_button.isVisible()
    assert not page.calculation_progress.geometry().intersects(page.footer.geometry())
    cancelled: list[bool] = []
    page.calculation_cancel_requested.connect(lambda: cancelled.append(True))
    page.calculation_progress.cancel_button.click()
    assert cancelled == [True]
    for action, button in page.footer.buttons.items():
        assert button.isEnabled() is (action is FunctionEditorAction.CLOSE)
    page.set_calculation_active(False)
    assert page.footer.buttons[FunctionEditorAction.VALIDATE].isEnabled()
    page.close()
    page.deleteLater()
    application.processEvents()


@pytest.mark.parametrize(
    "work_area",
    (
        QRect(0, 0, 1366, 768),
        QRect(0, 0, 1600, 900),
        QRect(0, 0, 1920, 1080),
    ),
)
def test_parallel_basic_is_two_column_one_screen_with_sticky_footer(
    work_area: QRect,
) -> None:
    application = _application()
    page = _parallel_page()
    metrics = CAM_POPUP_DENSITY.metrics_for(work_area)
    try:
        page.apply_compact_density(metrics)
        page.resize(metrics.popup_width, metrics.popup_height)
        page.show()
        application.processEvents()

        assert page.maximum_disclosure is ParameterDisclosureLevel.BASIC
        assert page.responsive_grid_columns == 2
        assert not page.basic_uses_vertical_scroll
        assert page.scroll_area.verticalScrollBar().maximum() == 0
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        assert page.content.sizeHint().height() <= page.scroll_area.viewport().height()
        assert page.footer.isVisible()
        assert page.footer.geometry().bottom() <= page.rect().bottom()
        assert page._section_widgets["automatic_summary"].field_columns == 2
        assert not page._field_widgets["automatic_direction_summary"].source_label.isVisible()
        assert "Tự động" in page._field_widgets[
            "automatic_direction_summary"
        ].editor.text()
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


@pytest.mark.parametrize(
    ("physical_size", "display_scale", "expected_columns", "expects_scroll"),
    (
        ((1366, 768), 1.0, 2, False),
        ((1600, 900), 1.0, 2, False),
        ((1920, 1080), 1.0, 2, False),
        ((1600, 900), 1.25, 2, False),
        ((1920, 1080), 1.25, 2, False),
        ((1600, 900), 1.5, 1, True),
        ((1920, 1080), 1.5, 2, None),
        ((1920, 1080), 2.0, 1, True),
    ),
)
def test_parallel_basic_real_bounds_follow_logical_dpi_matrix(
    physical_size: tuple[int, int],
    display_scale: float,
    expected_columns: int,
    expects_scroll: bool | None,
) -> None:
    application = _application()
    logical_size = (
        round(physical_size[0] / display_scale),
        round(physical_size[1] / display_scale),
    )
    page = _parallel_page()
    metrics = CAM_POPUP_DENSITY.metrics_for(
        QRect(0, 0, *logical_size),
        display_scale_factor=display_scale,
    )
    try:
        page.apply_compact_density(metrics)
        page.resize(metrics.popup_width, metrics.popup_height)
        page.show()
        for _ in range(6):
            application.processEvents()

        assert metrics.display_scale_factor == display_scale
        assert page.responsive_grid_columns == expected_columns
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        if expects_scroll is not None:
            assert (
                page.scroll_area.verticalScrollBar().maximum() > 0
            ) is expects_scroll
        assert page.footer.isVisible()
        assert_widget_fully_visible(page.footer, page)
        assert not page.scroll_area.geometry().intersects(page.footer.geometry())

        section_ids = ("geometry", "tool", "quality", "automatic_summary")
        sections = tuple(page._section_widgets[item] for item in section_ids)
        siblings = (*sections, page.illustration_panel)
        assert page.illustration_panel is not None
        assert_no_sibling_overlap(siblings, page.content)
        for section in sections:
            assert section.height() >= section.minimumSizeHint().height()
            assert_text_baseline_visible(section.title_label)
            assert_widget_fully_visible(section.body, section)
            fields = tuple(
                field for field in section._fields if not field.isHidden()
            )
            assert_no_sibling_overlap(fields, section.body)
            for field in fields:
                assert_widget_fully_visible(field, section.body)
                assert_text_baseline_visible(field.label)
                assert field.editor.height() >= field.editor.minimumSizeHint().height()
                assert field.editor.width() >= field.editor.minimumWidth()

        summary = page._section_widgets["automatic_summary"]
        for field in summary._fields:
            if field.isHidden():
                continue
            assert isinstance(field.editor, QLabel)
            assert_text_baseline_visible(field.editor)
            assert (
                field.editor.fontMetrics().horizontalAdvance(field.editor.text())
                <= field.editor.contentsRect().width()
            )

        if expects_scroll is True:
            for section in sections:
                page.scroll_area.ensureWidgetVisible(section, 0, 0)
                application.processEvents()
                assert_widget_fully_visible(
                    section, page.scroll_area.viewport()
                )
        elif expects_scroll is False:
            assert_content_fits_without_scroll(page.scroll_area)
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_1366_summary_keeps_priority_values_and_full_tooltip_on_two_lines() -> None:
    application = _application()
    context, machine = _context()
    schema = build_parallel_schema(context)
    summary = replace(
        schema.summary,
        strategy=(
            "Gia công tinh song song · Bước ngang 0.8 mm · "
            "Dung sai 0.02 mm · Tự động"
        ),
        tool=(
            "Tool cầu chuyên dụng có tên rất dài cho khuôn chính xác cao "
            "Ø10 mm"
        ),
        geometry="18 bề mặt gia công đã xác định",
    )
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            replace(schema, summary=summary),
            _valid_values(context, machine),
        )
    )
    metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1366, 768))
    try:
        page.apply_compact_density(metrics)
        page.resize(metrics.popup_width, metrics.popup_height)
        page.show()
        application.processEvents()
        lines = page.summary.context.text().splitlines()
        assert len(lines) == 2
        assert "0.8 mm" in lines[0]
        assert "0.02 mm" in lines[0]
        assert "Tự động" in lines[0]
        tooltip = page.summary.context.toolTip()
        assert "Tool cầu chuyên dụng có tên rất dài" in tooltip
        assert "Ø10 mm" in tooltip
        assert "18 bề mặt gia công" in tooltip
        assert page.footer.isVisible()
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_parallel_grid_falls_back_to_one_column_only_when_too_narrow() -> None:
    application = _application()
    page = _parallel_page()
    try:
        page.resize(480, 630)
        page.show()
        application.processEvents()
        assert page.responsive_grid_columns == 1
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        assert page.scroll_area.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded

        advanced_index = page.disclosure_selector.findData(
            ParameterDisclosureLevel.ADVANCED
        )
        page.disclosure_selector.setCurrentIndex(advanced_index)
        application.processEvents()
        assert page.scroll_area.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert not page._section_widgets["advanced"].is_expanded
        assert page._section_widgets["advanced"].body.isHidden()
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_parallel_validation_reveals_first_error_and_preserves_draft() -> None:
    application = _application()
    context, machine = _context()
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            build_parallel_schema(context),
            _valid_values(context, machine),
            validation_callback=lambda values: parallel_validation_diagnostics(
                build_parallel_schema(context),
                context,
                ParallelEditorDraftContext(
                    context.zone.part_surfaces.selection.surfaces
                ),
                values,
            ),
            draft_transform_callback=lambda values: parallel_draft_derived_values(
                context,
                ParallelEditorDraftContext(
                    context.zone.part_surfaces.selection.surfaces,
                    geometry_evidence=context.geometry_evidence,
                ),
                values,
            ),
        )
    )
    page.resize(460, 700)
    page.show()
    page.activateWindow()
    application.processEvents()
    page.collapse_all()
    page._field_changed("stepover_override_enabled", True)
    page._field_changed("stepover_mm", "0")

    diagnostics = page.validate_draft()
    application.processEvents()

    assert diagnostics[0].field_id == "stepover_mm"
    field = page._field_widgets["stepover_mm"]
    assert page._section_widgets["cut_parameters"].is_expanded
    assert field.isVisible() and field.diagnostic_label.isVisible()
    assert "Bước ngang phải lớn hơn 0." in field.diagnostic_label.text()
    assert field.editor.property("validationState") == "error"
    assert field.editor.focusPolicy() is not Qt.FocusPolicy.NoFocus
    top = field.mapTo(page.scroll_area.viewport(), QPoint()).y()
    assert top < page.scroll_area.viewport().height()
    assert top + field.height() > 0
    assert page.state.values["stepover_mm"] == "0"
    assert "1" in page.summary.validation.text()
    assert not page.footer.buttons[FunctionEditorAction.APPLY].isEnabled()
    assert not page.footer.buttons[FunctionEditorAction.CALCULATE].isEnabled()

    page._field_changed("stepover_mm", "1.25")
    application.processEvents()
    assert page.state.values["stepover_mm"] == "1.25"
    assert not field.diagnostic_label.isVisible()
    assert field.editor.property("validationState") == ""
    page.close()
    page.deleteLater()
    application.processEvents()


def test_real_workspace_apply_save_open_and_duplicate_contract(tmp_path) -> None:
    application = _application()
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Parallel Editor")
    fixture = planar_fixture(project_id=session.manifest.project_id, stepover=5.0)
    machine = basic_mill_resources(LengthUnit.MM)[3]
    snapshot = replace(
        _snapshot(fixture, fixture.operation),
        machine_definitions=(machine,),
    )
    service.stage_cam_snapshot(snapshot)
    service.stage_cam3d_config(
        Cam3DProjectConfig(session.manifest.project_id, (fixture.zone,))
    )
    workspace = CamWorkspace(service, lambda: None)
    workspace.bind_project(session)
    assert workspace.select_identity("operation", str(fixture.operation.node_id))
    production = workspace.production_function_editor_session()
    assert production is not None and production.schema.editor_id == PARALLEL_EDITOR_ID
    values = production.applied_mapping()
    values["machine_id"] = str(machine.machine_id)
    values["stepover_mm"] = "1.75"
    values["stepover_override_enabled"] = True
    assert not any(
        item.severity.name == "ERROR"
        for item in production.validation_callback(values)
    )
    production.apply_callback(values)
    application.processEvents()
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert ParallelFinishingParameters.from_operation_parameters(
        applied.parameters
    ).stepover_mm == 1.75
    assert applied.artifact_state.status is ArtifactStatus.DIRTY

    workspace.duplicate_selected_operation()
    application.processEvents()
    operations = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(operations) == 2
    duplicate = next(item for item in operations if item.operation_id != applied.operation_id)
    duplicate_parameters = ParallelFinishingParameters.from_operation_parameters(
        duplicate.parameters
    )
    assert duplicate.revision.value == 0
    assert duplicate.artifact_state.status is ArtifactStatus.MISSING
    assert duplicate.node_id != applied.node_id
    assert duplicate_parameters.zone_id != ParallelFinishingParameters.from_operation_parameters(
        applied.parameters
    ).zone_id
    assert {item.input_id for item in duplicate.geometry_inputs}.isdisjoint(
        {item.input_id for item in applied.geometry_inputs}
    )

    root = session.root_path
    service.save()
    service.close_project(discard_changes=True)
    reopened = service.open_project(root)
    restored = reopened.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(restored) == 2
    assert any(
        ParallelFinishingParameters.from_operation_parameters(item.parameters).stepover_mm
        == 1.75
        for item in restored
    )
    assert len(service.cam3d_config.zones) == 2
    service.close_project()
    workspace.close()
    workspace.deleteLater()
    application.processEvents()


def test_operation_registration_creates_supported_parallel_draft(tmp_path) -> None:
    application = _application()
    source = tmp_path / "parallel-editor.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    project = service.create_project_from_source(
        tmp_path, "Parallel Registration", source
    )
    source_id = project.manifest.source_files[0].source_id
    workspace = CamWorkspace(service, lambda: source_id)
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_parallel_resources()
    workspace.add_parallel_operation()
    application.processEvents()
    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assembly = service.cam_snapshot.tool_assemblies[0]
    tool = service.cam_snapshot.tool_definitions[0]
    assert operation.strategy_key == "parallel_finishing_3d"
    assert operation.tool_assembly.assembly_id == assembly.assembly_id
    assert tool.family.value == "ball_end_mill"
    assert operation.artifact_state.status is ArtifactStatus.MISSING
    production = workspace.production_function_editor_session()
    assert production is not None and production.schema.editor_id == PARALLEL_EDITOR_ID
    errors = production.validation_callback(production.applied_mapping())
    assert any(
        "geometry" in item.message.casefold() or "face" in item.message.casefold()
        for item in errors
    )
    service.close_project(discard_changes=True)
    workspace.close()
    workspace.deleteLater()
    application.processEvents()


def test_project_gateway_stages_computing_then_commits_safe_result(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Parallel Async Gateway")
    fixture = planar_fixture(project_id=session.manifest.project_id, stepover=5.0)
    service.stage_cam_snapshot(_snapshot(fixture, fixture.operation))
    generation = service.cam_generation

    def current_operation() -> Operation:
        return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]

    observed: list[ArtifactStatus] = []

    def begin(computing: Operation) -> bool:
        accepted = service.begin_parallel_calculation(
            computing, expected_generation=generation
        )
        observed.append(current_operation().artifact_state.status)
        return accepted

    result = calculate_and_publish_parallel_finishing(
        session.root_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        computing_callback=begin,
        current_operation=current_operation,
    )
    assert observed == [ArtifactStatus.COMPUTING]
    assert result.accepted
    assert service.commit_parallel_calculation(
        result, expected_generation=generation
    )
    assert current_operation().artifact_state.status is ArtifactStatus.VALID
    assert len(service.cam_snapshot.artifacts) == 1
    service.close_project(discard_changes=True)
