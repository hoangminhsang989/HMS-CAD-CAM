"""Stage 9A.5.4 visual-consistency tests for production milling editors."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from hms_cadcam.ui.function_editor import (  # noqa: E402
    FunctionEditorAction,
    FunctionEditorDraftState,
    FunctionEditorFieldKind,
    FunctionEditorPage,
    FunctionEditorStateStore,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.strategies import (  # noqa: E402
    FacingEditorVariant,
    build_contour_schema,
    build_facing_schema,
    build_planar_face_facing_schema,
    build_pocket_schema,
)
from tests.unit.test_contour_function_editor_9a52 import _context as contour_context  # noqa: E402
from tests.unit.test_facing_function_editors_9a51 import _context as facing_context  # noqa: E402
from tests.unit.test_pocket_function_editor_9a53 import _context as pocket_context  # noqa: E402
from tests.unit.test_workspace_shell import (  # noqa: E402
    _dispose as dispose_shell,
    _window as build_shell,
)


_FOOTER_ORDER = (
    FunctionEditorAction.RESET_DRAFT,
    FunctionEditorAction.PREVIEW,
    FunctionEditorAction.VALIDATE,
    FunctionEditorAction.APPLY,
    FunctionEditorAction.CALCULATE,
    FunctionEditorAction.CLOSE,
)
_SECTION_ORDER = (
    "basic",
    "geometry",
    "tool",
    "cutting",
    "levels",
    "entry",
    "linking",
    "advanced",
    "expert",
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _schemas():
    return (
        build_facing_schema(facing_context()),
        build_planar_face_facing_schema(
            facing_context(FacingEditorVariant.PLANAR_FACE)
        ),
        build_contour_schema(contour_context()[0]),
        build_pocket_schema(pocket_context()[0]),
    )


def _page(schema, **kwargs) -> FunctionEditorPage:
    return FunctionEditorPage(
        FunctionEditorDraftState(schema),
        close_confirmation=lambda _state: True,
        **kwargs,
    )


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_production_schemas_share_order_footer_and_reasonable_basic() -> None:
    for schema in _schemas():
        actual_sections = tuple(item.section_id for item in schema.ordered_sections)
        assert actual_sections == tuple(
            section_id for section_id in _SECTION_ORDER if section_id in actual_sections
        )
        assert schema.footer.actions == _FOOTER_ORDER
        basic_sections = schema.visible_sections(
            dict(FunctionEditorDraftState(schema).values),
            ParameterDisclosureLevel.BASIC,
        )
        basic_editable = {
            field.field_id
            for section in basic_sections
            for field in section.fields
            if field.disclosure_level is ParameterDisclosureLevel.BASIC
            and field.kind is not FunctionEditorFieldKind.READ_ONLY
        }
        assert 5 <= len(basic_editable) <= 10
        for section in schema.sections:
            if section.disclosure_level > ParameterDisclosureLevel.BASIC:
                assert not section.default_expanded

    facing, planar, contour, pocket = _schemas()
    for schema in (facing, planar):
        assert all(
            field.disclosure_level is not ParameterDisclosureLevel.EXPERT
            for field in schema.fields
        )
    for schema in (facing, planar, contour, pocket):
        assert schema.field("stepdown").disclosure_level is ParameterDisclosureLevel.BASIC
    for schema in (facing, planar, contour, pocket):
        field_ids = {item.field_id for item in schema.fields}
        feed_id = "feed_rate" if "feed_rate" in field_ids else "cutting_feed_rate"
        assert schema.field(feed_id).disclosure_level is ParameterDisclosureLevel.BASIC


def test_disclosure_options_and_collapsed_defaults_match_real_fields() -> None:
    application = _application()
    facing, _planar, contour, pocket = _schemas()
    pages = [_page(schema) for schema in (facing, contour, pocket)]
    try:
        for page in pages:
            page.resize(420, 720)
            page.show()
            application.processEvents()
            assert page.maximum_disclosure is ParameterDisclosureLevel.BASIC
            assert "advanced" not in page._section_widgets
        assert pages[0].disclosure_selector.findData(ParameterDisclosureLevel.EXPERT) < 0
        for page in pages[1:]:
            expert_index = page.disclosure_selector.findData(
                ParameterDisclosureLevel.EXPERT
            )
            assert expert_index >= 0
            page.disclosure_selector.setCurrentIndex(expert_index)
            application.processEvents()
            assert not page._section_widgets["advanced"].is_expanded
            assert not page._section_widgets["expert"].is_expanded
    finally:
        for page in pages:
            _dispose(page, application)


def test_responsive_header_footer_and_internal_scroll_are_consistent() -> None:
    application = _application()
    pages = [_page(schema) for schema in _schemas()]
    try:
        for width in (300, 360, 420, 520):
            heights: list[int] = []
            for page in pages:
                page.resize(width, 700)
                page.show()
                application.processEvents()
                assert page.scroll_area.horizontalScrollBar().maximum() == 0
                assert page.footer.isVisible()
                assert page.footer._compact is (width < 400)
                heights.append(page.summary.height())
            assert max(heights) - min(heights) <= 2
    finally:
        for page in pages:
            _dispose(page, application)


def test_source_default_and_inline_error_have_text_and_focus() -> None:
    application = _application()
    page = _page(_schemas()[-1])
    try:
        page.resize(420, 720)
        page.show()
        application.processEvents()
        derived = page._field_widgets["final_depth_summary"]
        recommended = page._field_widgets["stepover"]
        assert derived.source_label.text() == "Nguồn: Derived"
        assert derived.source_label.isVisible()
        assert recommended.default_label.isVisible()
        before_height = page._field_widgets["stepdown"].height()

        page._field_changed("stepdown", "0")
        page.validate_draft()
        application.processEvents()

        invalid = page._field_widgets["stepdown"]
        assert invalid.diagnostic_label.isVisible()
        assert "Lỗi" in invalid.diagnostic_label.text()
        assert page._section_widgets["levels"].badge.isVisible()
        assert QApplication.focusWidget() is invalid.editor
        assert invalid.height() - before_height < 100
    finally:
        _dispose(page, application)


def test_edit_preserves_scroll_focus_field_order_and_single_signal() -> None:
    application = _application()
    calls: list[str] = []

    def field_action(action_id, _values):
        calls.append(action_id)
        return None

    page = _page(_schemas()[2], field_action_callback=field_action)
    try:
        page.resize(360, 420)
        page.show()
        application.processEvents()
        bar = page.scroll_area.verticalScrollBar()
        bar.setValue(max(1, bar.maximum() // 2))
        application.processEvents()
        before_scroll = bar.value()
        editor = page._field_widgets["cutting_feed_rate"].editor
        editor.setFocus()
        application.processEvents()

        page._field_changed("cutting_feed_rate", "510")
        application.processEvents()

        assert QApplication.focusWidget() is editor
        assert abs(bar.value() - before_scroll) <= 2
        cutting = page._section_widgets["cutting"].body_layout
        actual_ids = [
            cutting.itemAt(index).widget().definition.field_id
            for index in range(cutting.count())
        ]
        expected_ids = [
            field.field_id
            for field in page.schema.visible_fields(
                "cutting", page.state.values, page.maximum_disclosure
            )
        ]
        assert actual_ids == expected_ids

        for level in (
            ParameterDisclosureLevel.ADVANCED,
            ParameterDisclosureLevel.BASIC,
        ) * 3:
            page.disclosure_selector.setCurrentIndex(
                page.disclosure_selector.findData(level)
            )
            application.processEvents()
        page._field_widgets["geometry_summary"].action_button.click()
        application.processEvents()
        assert calls == ["select_geometry"]
    finally:
        _dispose(page, application)


def test_tab_order_layout_state_and_viewport_minimum_are_preserved(
    tmp_path: Path,
) -> None:
    application = _application()
    shell = build_shell(tmp_path)
    session = shell.project_controller.service.new_project(tmp_path, "Stage9A54 Layout")
    dirty_before = session.is_dirty
    settings = QSettings(str(tmp_path / "function_editor.ini"), QSettings.Format.IniFormat)
    page = FunctionEditorPage(
        FunctionEditorDraftState(_schemas()[0]),
        state_store=FunctionEditorStateStore(settings),
        close_confirmation=lambda _state: True,
    )
    try:
        page.resize(360, 640)
        page.show()
        application.processEvents()
        page.disclosure_selector.setCurrentIndex(
            page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
        )
        application.processEvents()
        page._section_widgets["advanced"].toggle.click()
        page.resize(520, 640)
        application.processEvents()
        assert not page.state.is_dirty
        assert shell.project_controller.service.current_project.is_dirty is dirty_before

        focus_chain: list[QWidget] = []
        current: QWidget = page.summary.help_button
        for _index in range(300):
            if current in focus_chain:
                break
            focus_chain.append(current)
            current = current.nextInFocusChain()
        assert focus_chain.index(page.disclosure_selector) < focus_chain.index(
            page._section_widgets["basic"].toggle
        )
        assert focus_chain.index(page._field_widgets["operation_name"].editor) < focus_chain.index(
            page.footer.buttons[FunctionEditorAction.RESET_DRAFT]
        )

        shell.show()
        for width, height in ((1366, 768), (1600, 900), (1920, 1080)):
            shell.resize(width, height)
            application.processEvents()
            assert shell.viewport.width() >= 520
            assert shell.viewport.height() >= 360
    finally:
        _dispose(page, application)
        dispose_shell(shell, application)
