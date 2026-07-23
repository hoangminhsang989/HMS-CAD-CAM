"""Focused singleton, dirty-switch and child-popup tests for Stage 8A.2.3."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QRect,
    QSettings,
    Qt,
    Signal,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hms_cadcam.ui.cam_function_popup import (  # noqa: E402
    CAMFunctionPopupHost,
    CAMToolSelectorDialog,
    clamp_popup_geometry,
)
from hms_cadcam.ui.cam_illustrations import CAMIllustrationDialog  # noqa: E402
from hms_cadcam.ui.function_editor.host import FunctionEditorHost  # noqa: E402
from hms_cadcam.ui.function_editor.model import (  # noqa: E402
    FunctionEditorAction,
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
)
from hms_cadcam.ui.function_editor.production import (  # noqa: E402
    FunctionEditorProductionSession,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema  # noqa: E402
from hms_cadcam.ui.localization import operation_display_name  # noqa: E402
from hms_cadcam.ui.ui_tokens import (  # noqa: E402
    CAM_POPUP_DENSITY,
    CAM_RESPONSIVE_GRID,
)


EDITOR_IDS = (
    "facing_production_9a5_1",
    "planar_face_facing_production_9a5_1",
    "contour_production_9a5_2",
    "pocket_production_9a5_3",
    "drilling_production_9a6",
    "tapping_production_9a6",
    "reaming_production_9a6",
    "boring_production_9a6",
    "parallel_finishing_production_8a2_3",
)

EDITOR_SOURCE_TITLES = dict(
    zip(
        EDITOR_IDS,
        (
            "Facing 2.5D",
            "Planar Face Facing",
            "2D Contour",
            "Pocket 2.5D",
            "Drilling",
            "Tapping",
            "Reaming",
            "Boring",
            "Parallel Finishing",
        ),
        strict=True,
    )
)


class _Legacy(QWidget):
    draft_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.value = QLineEdit("cũ")
        layout.addWidget(self.value)
        self.apply_button = QPushButton("Áp dụng cũ")
        layout.addWidget(self.apply_button)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _schema(editor_id: str) -> FunctionEditorSchema:
    return FunctionEditorSchema(
        editor_id,
        FunctionEditorStrategyKey(f"strategy_{editor_id}"),
        FunctionEditorSummary(EDITOR_SOURCE_TITLES[editor_id], "Kiểm thử popup"),
        (
            FunctionEditorSection(
                "basic",
                "CƠ BẢN",
                (
                    FunctionEditorField(
                        "name", "Tên nguyên công", FunctionEditorFieldKind.TEXT, "A"
                    ),
                    FunctionEditorField(
                        "tool_assembly_id",
                        "Tool Assembly",
                        FunctionEditorFieldKind.CHOICE,
                        "tool-a",
                        choices=("tool-a", "tool-b"),
                        choice_labels=(
                            ("tool-a", "Tool cầu Ø10"),
                            ("tool-b", "Tool cầu Ø6"),
                        ),
                    ),
                ),
            ),
        ),
        FunctionEditorFooter(
            actions=(FunctionEditorAction.APPLY, FunctionEditorAction.CLOSE)
        ),
    )


def _session(
    key: str,
    editor_id: str,
    applied: list[tuple[str, dict[str, object]]],
) -> FunctionEditorProductionSession:
    schema = _schema(editor_id)

    def validate(values):
        if str(values["name"]).strip():
            return ()
        return (
            FunctionEditorDiagnostic(
                "name.required",
                "Tên nguyên công là bắt buộc.",
                FunctionEditorDiagnosticSeverity.ERROR,
                "name",
                "basic",
            ),
        )

    def apply(values):
        applied.append((key, dict(values)))
        return True

    return FunctionEditorProductionSession(
        selection_key=("operation", key),
        schema=schema,
        applied_values=(("name", key), ("tool_assembly_id", "tool-a")),
        project_key="project-one",
        operation_key=key,
        generation=1,
        apply_callback=apply,
        validation_callback=validate,
        preview_callback=lambda _request: None,
        calculate_callback=lambda _values: None,
    )


def _environment(tmp_path: Path):
    application = _application()
    tree = QTreeWidget()
    items = {}
    for key in ("A", "B"):
        item = QTreeWidgetItem([key, "CẦN TÍNH"])
        tree.addTopLevelItem(item)
        items[key] = item
    tree.setCurrentItem(items["A"])
    applied: list[tuple[str, dict[str, object]]] = []
    editor_id = {"A": EDITOR_IDS[0], "B": EDITOR_IDS[-1]}

    def provider():
        current = tree.currentItem()
        return (
            _session(current.text(0), editor_id[current.text(0)], applied)
            if current is not None
            else None
        )

    host = FunctionEditorHost(
        _Legacy(),
        tree,
        lambda: None,
        production_provider=provider,
        selection_restore=lambda _kind, key: bool(tree.setCurrentItem(items[key]) is None),
        selection_exists=lambda selection: selection[1] in items,
        follow_selection=False,
    )
    parent = QWidget()
    parent.resize(1280, 760)
    settings = QSettings(str(tmp_path / "popup.ini"), QSettings.Format.IniFormat)
    popup = CAMFunctionPopupHost(host, settings, parent)
    return application, tree, items, applied, host, parent, popup


def _dispose(parent: QWidget, popup: CAMFunctionPopupHost, application: QApplication) -> None:
    popup.invalidate_project()
    parent.close()
    popup.deleteLater()
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_single_selection_does_not_open_and_repeated_open_reuses_single_popup(tmp_path) -> None:
    application, tree, items, _applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        tree.setCurrentItem(items["B"])
        application.processEvents()
        assert host.active_session is None
        assert not popup.isVisible()

        assert popup.open_current_operation()
        first_page = host.active_page
        assert popup.isVisible()
        assert popup.active_operation_key == "B"
        assert popup.open_current_operation()
        assert host.active_page is first_page
        assert host.stack.count() == 2
    finally:
        _dispose(parent, popup, application)


def test_dirty_switch_apply_discard_continue_and_validation_failure(tmp_path) -> None:
    application, tree, items, applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        assert popup.open_current_operation()
        page_a = host.active_page
        assert page_a is not None
        page_a._field_changed("name", "A đã sửa")
        tree.setCurrentItem(items["B"])

        host._switch_confirmation = lambda _state: "continue"
        assert not popup.open_current_operation()
        application.processEvents()
        assert popup.active_operation_key == "A"
        assert tree.currentItem() is items["A"]

        tree.setCurrentItem(items["B"])
        host._switch_confirmation = lambda _state: "apply"
        assert popup.open_current_operation()
        assert popup.active_operation_key == "B"
        assert applied[-1][0] == "A"

        host.active_page._field_changed("name", "B đã sửa")
        tree.setCurrentItem(items["A"])
        host._switch_confirmation = lambda _state: "discard"
        assert popup.open_current_operation()
        assert popup.active_operation_key == "A"
        assert [item[0] for item in applied] == ["A"]

        host.active_page._field_changed("name", "")
        tree.setCurrentItem(items["B"])
        host._switch_confirmation = lambda _state: "apply"
        assert not popup.open_current_operation()
        assert popup.active_operation_key == "A"
        assert host.active_page._field_widgets["name"].editor.hasFocus()
    finally:
        _dispose(parent, popup, application)


def test_one_child_popup_replaces_previous_and_restores_focus(tmp_path) -> None:
    application, _tree, _items, _applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        assert popup.open_current_operation()
        page = host.active_page
        tool_field = page._ensure_field("tool_assembly_id")
        tool_field.action_button.click()
        application.processEvents()
        first = popup.child_dialog
        assert isinstance(first, CAMToolSelectorDialog)
        assert first.parentWidget() is popup
        assert first.windowModality() is Qt.WindowModality.WindowModal
        assert popup.frameGeometry().intersects(first.frameGeometry())

        replacement = QDialog()
        popup.adopt_child_dialog("diagnostics", replacement, tool_field.editor)
        application.processEvents()
        assert popup.child_dialog is replacement
        assert replacement.parentWidget() is popup
        replacement.reject()
        application.processEvents()
        application.processEvents()
        assert popup.child_dialog is None
        assert tool_field.editor.hasFocus()
    finally:
        _dispose(parent, popup, application)


def test_illustration_child_wording_escape_singleton_focus_and_draft(tmp_path) -> None:
    application, _tree, _items, _applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        assert popup.open_current_operation()
        page = host.active_page
        assert page is not None and page.illustration_panel is not None
        page._field_changed("name", "Bản nháp còn nguyên")
        panel = page.illustration_panel
        panel.enlarge_button.setFocus()
        panel.enlarge_button.click()
        application.processEvents()

        child = popup.child_dialog
        assert isinstance(child, CAMIllustrationDialog)
        assert child.windowTitle() == "Minh họa · Phay mặt 2.5D"
        assert child.close_button.text() == "Đóng minh họa"
        editor_close = page.footer.buttons[FunctionEditorAction.CLOSE]
        assert editor_close.text() == "Đóng"
        assert editor_close.text() != child.close_button.text()
        assert child.windowModality() is Qt.WindowModality.WindowModal

        popup._open_child_request(
            "illustration", {"state": panel.state, "focus": panel.enlarge_button}
        )
        application.processEvents()
        assert popup.child_dialog is child

        QTest.keyClick(child, Qt.Key.Key_Escape)
        application.processEvents()
        application.processEvents()
        assert popup.child_dialog is None
        assert panel.enlarge_button.hasFocus()
        assert page.state.values["name"] == "Bản nháp còn nguyên"
        assert page.state.is_dirty
    finally:
        _dispose(parent, popup, application)


def test_all_nine_production_editor_ids_open_through_same_content_host(tmp_path) -> None:
    application = _application()
    tree = QTreeWidget()
    applied = []
    current = {"id": EDITOR_IDS[0]}

    def provider():
        return _session(current["id"], current["id"], applied)

    host = FunctionEditorHost(
        _Legacy(), tree, lambda: None, production_provider=provider, follow_selection=False
    )
    parent = QWidget()
    popup = CAMFunctionPopupHost(
        host,
        QSettings(str(tmp_path / "all.ini"), QSettings.Format.IniFormat),
        parent,
    )
    try:
        for editor_id in EDITOR_IDS:
            current["id"] = editor_id
            assert popup.open_current_operation()
            assert host.active_page.schema.editor_id == editor_id
            assert host.active_page.illustration_panel is not None
            assert host.active_page._density_metrics == popup.density_metrics
            assert not host.active_page.illustration_panel.is_expanded
            assert host.stack.count() == 2
            expected_name = operation_display_name(EDITOR_SOURCE_TITLES[editor_id])
            assert popup.windowTitle() == f"Chỉnh sửa CAM · {expected_name}"
            assert host.active_page.summary.title.toolTip() == expected_name
            assert expected_name in host.active_page.accessibleName()
    finally:
        _dispose(parent, popup, application)


def test_project_invalidation_marks_old_page_stale_and_closes_popup(tmp_path) -> None:
    application, _tree, _items, _applied, host, parent, popup = _environment(tmp_path)
    parent.show()
    assert popup.open_current_operation()
    old_page = host.active_page
    old_request = old_page.state.preview_request()

    popup.invalidate_project()
    application.processEvents()

    assert host.active_session is None
    assert host.active_page is None
    assert not popup.isVisible()
    assert not old_page.state.accepts_preview(old_request)
    _dispose(parent, popup, application)


def test_popup_geometry_is_clamped_to_available_work_area() -> None:
    result = clamp_popup_geometry(
        QRect(5000, 5000, 1100, 900),
        (QRect(0, 0, 1366, 728),),
    )
    assert result.left() >= 0 and result.top() >= 0
    assert result.right() <= 1365 and result.bottom() <= 727
    assert result.width() <= 1366 and result.height() <= 728


def test_compact_policy_hits_three_responsive_work_area_targets() -> None:
    cases = (
        (QRect(0, 0, 1366, 768), (540, 600), (560, 650), 0.45, 0.84),
        (QRect(0, 0, 1600, 900), (580, 660), (620, 720), 0.43, 0.82),
        (QRect(0, 0, 1920, 1080), (620, 700), (680, 800), 0.42, 0.80),
    )
    for area, width_range, height_range, width_ratio, height_ratio in cases:
        metrics = CAM_POPUP_DENSITY.metrics_for(area)
        assert width_range[0] <= metrics.popup_width <= width_range[1]
        assert height_range[0] <= metrics.popup_height <= height_range[1]
        assert metrics.maximum_width <= round(area.width() * width_ratio)
        assert metrics.maximum_height <= round(area.height() * height_ratio)
        assert metrics.minimum_width < metrics.popup_width <= metrics.maximum_width
        assert metrics.minimum_height < metrics.popup_height <= metrics.maximum_height


def test_high_dpi_uses_logical_pixels_without_double_scaling() -> None:
    area = QRect(0, 0, 1600, 900)
    base = CAM_POPUP_DENSITY.metrics_for(area, display_scale_factor=1.0)
    scale_125 = CAM_POPUP_DENSITY.metrics_for(
        area, display_scale_factor=1.25
    )
    scale_150 = CAM_POPUP_DENSITY.metrics_for(
        area, display_scale_factor=1.5
    )

    assert scale_125.display_scale_factor == 1.25
    assert scale_150.display_scale_factor == 1.5
    assert scale_125.popup_width == scale_150.popup_width == base.popup_width
    assert scale_125.control_height == scale_150.control_height == 27
    assert scale_125.regular_font_point_size == scale_150.regular_font_point_size == 9.0


def test_responsive_grid_uses_two_readable_columns_and_narrow_fallback() -> None:
    metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1366, 768))
    assert CAM_RESPONSIVE_GRID.columns_for(
        metrics.popup_width - 2, metrics, minimum_size_hint=230
    ) == 2
    assert CAM_RESPONSIVE_GRID.columns_for(
        480, metrics, minimum_size_hint=230
    ) == 1
    assert CAM_RESPONSIVE_GRID.columns_for(
        metrics.popup_width - 2, metrics, minimum_size_hint=900
    ) == 2


def test_compact_tokens_stay_readable_and_professional() -> None:
    metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
    assert 8 <= metrics.content_margin <= 10
    assert 6 <= metrics.section_spacing <= 8
    assert 3 <= metrics.row_spacing <= 5
    assert 6 <= metrics.label_spacing <= 8
    assert 26 <= metrics.control_height <= 30
    assert 28 <= metrics.button_height <= 32
    assert 26 <= metrics.compact_button_height <= 30
    assert 24 <= metrics.table_row_height <= 28
    assert 24 <= metrics.tree_row_height <= 28
    assert 9.0 <= metrics.regular_font_point_size <= 10.0
    assert 10.0 <= metrics.heading_font_point_size <= 11.0
    assert 11.0 <= metrics.operation_title_font_point_size <= 12.0


def test_saved_popup_preference_is_restored_then_clamped(tmp_path) -> None:
    application, _tree, _items, _applied, _host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        settings = popup._settings
        settings.beginGroup("cam_function_popup_v2")
        settings.setValue("rect", QRect(50_000, 50_000, 1400, 1000))
        settings.endGroup()
        settings.sync()

        assert popup.open_current_operation()
        application.processEvents()
        available = popup.screen().availableGeometry()
        geometry = popup.frameGeometry()
        assert available.contains(geometry.center())
        assert geometry.width() <= popup.density_metrics.maximum_width
        assert geometry.height() <= popup.density_metrics.maximum_height
        assert not popup.isMaximized()
        assert not popup.isFullScreen()
    finally:
        _dispose(parent, popup, application)


def test_footer_scroll_and_collapsed_illustration_remain_visible(tmp_path) -> None:
    application, _tree, _items, _applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        assert popup.open_current_operation()
        application.processEvents()
        page = host.active_page
        assert page is not None and page.illustration_panel is not None
        assert page.footer.isVisible()
        assert not page.illustration_panel.is_expanded
        assert (
            page.illustration_panel.maximumHeight()
            == popup.density_metrics.illustration_collapsed_height
        )
        assert (
            page.scroll_area.horizontalScrollBarPolicy()
            is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.maximum_disclosure.name == "BASIC"

        page.illustration_panel.set_expanded(True)
        application.processEvents()
        if page.height() < popup.density_metrics.illustration_auto_collapse_height:
            assert not page.illustration_panel.is_expanded
        else:
            assert page.illustration_panel.is_expanded
        assert popup.height() <= popup.density_metrics.maximum_height
        assert page.footer.isVisible()
    finally:
        _dispose(parent, popup, application)


def test_sparse_editor_prefers_shorter_height_than_dense_target(tmp_path) -> None:
    application, _tree, _items, _applied, host, parent, popup = _environment(tmp_path)
    try:
        parent.show()
        assert popup.open_current_operation()
        page = host.active_page
        metrics = CAM_POPUP_DENSITY.metrics_for(QRect(0, 0, 1600, 900))
        assert page.preferred_popup_height(metrics) == 540
        assert page.preferred_popup_height(metrics) < metrics.popup_height
    finally:
        _dispose(parent, popup, application)
