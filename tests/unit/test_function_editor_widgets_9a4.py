"""Stage 9A.4 widget, host, responsive and performance tests."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from hms_cadcam.ui.function_editor import (  # noqa: E402
    ApplicabilityOperator,
    FunctionEditorApplicability,
    FunctionEditorDraftState,
    FunctionEditorDraftStatus,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorHost,
    FunctionEditorPage,
    FunctionEditorSchema,
    FunctionEditorSection,
    FunctionEditorStateStore,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    ParameterDisclosureLevel,
    build_contour_reference_schema,
)
from hms_cadcam.ui.function_editor.model import FunctionEditorAction  # noqa: E402


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dispose(widget: QWidget, application: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _page(
    *,
    settings: QSettings | None = None,
    apply_callback=None,
    preview_callback=None,
    calculate_callback=None,
    schema: FunctionEditorSchema | None = None,
) -> FunctionEditorPage:
    selected = schema or build_contour_reference_schema()
    store = FunctionEditorStateStore(settings) if settings is not None else None
    return FunctionEditorPage(
        FunctionEditorDraftState(selected),
        state_store=store,
        apply_callback=apply_callback,
        preview_callback=preview_callback,
        calculate_callback=calculate_callback,
        close_confirmation=lambda _state: True,
    )


def test_basic_sections_visible_and_advanced_expert_are_not_built() -> None:
    application = _application()
    page = _page()
    page.resize(420, 760)
    page.show()
    application.processEvents()

    assert set(page._section_widgets) == {
        "basic",
        "geometry",
        "tool",
        "cutting",
        "levels",
        "linking",
    }
    assert "advanced" not in page._section_widgets
    assert "expert" not in page._section_widgets
    assert "tolerance" not in page._field_widgets
    assert page.scroll_area.widgetResizable()
    assert page.footer.isVisible()
    _dispose(page, application)


def test_advanced_and_expert_are_collapsed_by_default() -> None:
    application = _application()
    page = _page()
    page.show()

    page.disclosure_selector.setCurrentIndex(
        page.disclosure_selector.findData(ParameterDisclosureLevel.ADVANCED)
    )
    application.processEvents()
    assert not page._section_widgets["advanced"].is_expanded
    assert "radial_allowance" in page._field_widgets

    page.disclosure_selector.setCurrentIndex(
        page.disclosure_selector.findData(ParameterDisclosureLevel.EXPERT)
    )
    application.processEvents()
    assert not page._section_widgets["expert"].is_expanded
    assert "tolerance" in page._field_widgets
    _dispose(page, application)


def test_applicability_removes_irrelevant_field_instead_of_disabling_it() -> None:
    application = _application()
    page = _page()
    page.show()
    application.processEvents()
    assert page._field_widgets["lead_length"].isVisible()

    page._field_changed("use_lead", False)
    application.processEvents()

    assert not page._field_widgets["lead_length"].isVisible()
    assert page._field_widgets["lead_length"].editor.isEnabled()
    assert "lead_length" not in page.state.applicable_snapshot()
    _dispose(page, application)


def test_field_renders_unit_source_default_help_and_accessibility() -> None:
    application = _application()
    page = _page()
    page.show()
    application.processEvents()
    safe = page._field_widgets["safe_z"]
    stepdown = page._field_widgets["stepdown"]

    assert safe.unit_label.text() == "mm"
    assert safe.source_label.text() == "Nguồn: Thiết lập"
    assert stepdown.default_label.isVisible()
    assert "mm" in stepdown.accessibleName()
    assert stepdown.editor.accessibleName()
    stepdown.help_button.click()
    assert page.help_panel.isVisible()
    assert "Đơn vị: mm" in page.help_text.text()
    _dispose(page, application)


def test_validate_shows_inline_error_section_badge_and_focuses_first_field() -> None:
    application = _application()
    page = _page()
    page.resize(420, 760)
    page.show()
    application.processEvents()
    page._field_changed("stepdown", "0")

    diagnostics = page.validate_draft()
    application.processEvents()

    assert diagnostics[0].field_id == "stepdown"
    field = page._field_widgets["stepdown"]
    assert field.diagnostic_label.isVisible()
    assert "Lỗi" in field.diagnostic_label.text()
    assert page._section_widgets["cutting"].badge.isVisible()
    assert page.diagnostic_view.list.count() >= 1
    assert QApplication.focusWidget() is field.editor
    assert page.state.status is FunctionEditorDraftStatus.INVALID
    _dispose(page, application)


def test_collapse_all_expand_relevant_and_user_only_persistence(
    tmp_path: Path,
) -> None:
    application = _application()
    settings = QSettings(str(tmp_path / "sections.ini"), QSettings.Format.IniFormat)
    page = _page(settings=settings)
    page.show()
    page.collapse_all()
    assert all(not item.is_expanded for item in page._section_widgets.values())
    page._field_changed("stepdown", "0")
    page.validate_draft()
    page.expand_relevant()
    assert page._section_widgets["cutting"].is_expanded
    settings.sync()
    assert "stepdown" not in (tmp_path / "sections.ini").read_text(encoding="utf-8")
    _dispose(page, application)

    reopened = _page(settings=settings)
    reopened.show()
    application.processEvents()
    assert reopened._section_widgets["cutting"].is_expanded
    assert not reopened._section_widgets["geometry"].is_expanded
    _dispose(reopened, application)


def test_footer_apply_is_atomic_and_does_not_auto_calculate() -> None:
    application = _application()
    applied: list[dict[str, object]] = []
    calculated: list[dict[str, object]] = []
    page = _page(
        apply_callback=lambda values: applied.append(dict(values)) or True,
        calculate_callback=lambda values: calculated.append(dict(values)),
    )
    page.show()
    page._field_changed("feed_rate", "640")
    page.footer.buttons[FunctionEditorAction.APPLY].click()
    application.processEvents()

    assert len(applied) == 1
    assert applied[0]["feed_rate"] == "640"
    assert calculated == []
    assert page.state.status is FunctionEditorDraftStatus.APPLIED
    assert not page.state.is_dirty
    assert FunctionEditorAction.CALCULATE not in page.footer.buttons
    _dispose(page, application)


def test_preview_is_transient_and_does_not_apply_or_calculate() -> None:
    application = _application()
    previews = []
    applies = 0

    def apply(_values) -> bool:
        nonlocal applies
        applies += 1
        return True

    page = _page(apply_callback=apply, preview_callback=previews.append)
    page.show()
    page._field_changed("feed_rate", "641")
    page.footer.buttons[FunctionEditorAction.PREVIEW].click()

    assert len(previews) == 1
    assert applies == 0
    assert page.state.applied_values["feed_rate"] == "500"
    assert page.state.is_dirty
    _dispose(page, application)


def test_close_dirty_uses_confirmation_and_never_auto_applies() -> None:
    application = _application()
    confirmations = 0
    applies = 0
    schema = build_contour_reference_schema()
    state = FunctionEditorDraftState(schema)

    def confirm(_state) -> bool:
        nonlocal confirmations
        confirmations += 1
        return False

    def apply(_values) -> bool:
        nonlocal applies
        applies += 1
        return True

    page = FunctionEditorPage(
        state,
        apply_callback=apply,
        close_confirmation=confirm,
    )
    page._field_changed("feed_rate", "700")

    assert not page.request_close()
    assert confirmations == 1
    assert applies == 0
    assert state.is_dirty
    _dispose(page, application)


def test_responsive_widths_have_internal_scroll_and_compact_footer() -> None:
    application = _application()
    page = _page()
    page.show()
    for width in (300, 360, 420, 520):
        page.resize(width, 680)
        application.processEvents()
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        assert page.footer.isVisible()
        assert page._field_widgets["stepdown"].label.text()
        assert page._field_widgets["stepdown"].unit_label.text() == "mm"
        assert page._compact is (width < page._density_metrics.field_reflow_width)
        assert page.footer._compact is (
            width < page._density_metrics.field_reflow_width
        )
    _dispose(page, application)


class _LegacyEditor(QWidget):
    draft_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        form = QFormLayout(self)
        self.value = QLineEdit("legacy")
        form.addRow("Legacy value", self.value)
        self.apply_button = QPushButton("Áp dụng cũ")
        form.addRow(self.apply_button)


def test_host_uses_legacy_adapter_by_default_without_duplicate_apply() -> None:
    application = _application()
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Status"])
    item = QTreeWidgetItem(["Contour production", "DIRTY"])
    tree.addTopLevelItem(item)
    tree.setCurrentItem(item)
    editor = _LegacyEditor()
    calls = 0

    def apply() -> None:
        nonlocal calls
        calls += 1

    host = FunctionEditorHost(editor, tree, apply)
    host.resize(420, 680)
    host.show()
    application.processEvents()

    assert host.current_mode == "legacy"
    assert host.scroll_area.widget() is editor
    assert host.legacy_adapter.selection_summary.text() == "Contour production"
    assert not editor.apply_button.isVisible()
    assert len(host.findChildren(QPushButton, "PrimaryPanelAction")) == 1
    host.apply_button.click()
    assert calls == 1
    _dispose(host, application)


def test_operation_selection_replaces_reference_and_marks_callbacks_stale() -> None:
    application = _application()
    tree = QTreeWidget()
    tree.setHeaderLabels(["Name", "Status"])
    first = QTreeWidgetItem(["First", "READY"])
    second = QTreeWidgetItem(["Second", "READY"])
    tree.addTopLevelItems([first, second])
    tree.setCurrentItem(first)
    host = FunctionEditorHost(_LegacyEditor(), tree, lambda: None)
    host.show()
    page = host.show_reference_editor(close_confirmation=lambda _state: True)
    request = page.state.preview_request()
    assert host.current_mode == "framework"

    tree.setCurrentItem(second)
    application.processEvents()

    assert host.current_mode == "legacy"
    assert page.state.status is FunctionEditorDraftStatus.STALE
    assert not page.state.accepts_preview(request)
    assert host.legacy_adapter.selection_summary.text() == "Second"
    _dispose(host, application)


def _large_schema(count: int) -> FunctionEditorSchema:
    controller = FunctionEditorField(
        "show_optional", "Show optional", FunctionEditorFieldKind.CHECKBOX, False
    )
    fields = [controller]
    fields.extend(
        FunctionEditorField(
            f"field_{index}",
            f"Field {index}",
            FunctionEditorFieldKind.NUMBER,
            str(index + 1),
            applicable_when=FunctionEditorApplicability(
                "show_optional", ApplicabilityOperator.TRUTHY
            ),
            order=index + 1,
        )
        for index in range(count - 1)
    )
    return FunctionEditorSchema(
        f"performance_{count}",
        FunctionEditorStrategyKey(f"performance_{count}"),
        FunctionEditorSummary(f"Performance {count}", "test"),
        (FunctionEditorSection("basic", "Basic", tuple(fields)),),
        FunctionEditorFooter(actions=(FunctionEditorAction.CLOSE,)),
    )


def test_20_50_100_field_schemas_are_lazy_and_do_not_calculate() -> None:
    application = _application()
    calculations = 0
    for count in (20, 50, 100):
        def calculated(_values) -> None:
            nonlocal calculations
            calculations += 1

        page = _page(schema=_large_schema(count), calculate_callback=calculated)
        page.show()
        application.processEvents()
        assert len(page.schema.fields) == count
        assert tuple(page._field_widgets) == ("show_optional",)
        assert calculations == 0
        _dispose(page, application)


def test_repeated_host_switching_keeps_one_active_editor() -> None:
    application = _application()
    tree = QTreeWidget()
    apply_calls = 0

    def apply() -> None:
        nonlocal apply_calls
        apply_calls += 1

    host = FunctionEditorHost(_LegacyEditor(), tree, apply)
    host.show()
    for _index in range(25):
        host.show_reference_editor(close_confirmation=lambda _state: True)
        assert host.stack.count() == 2
        host.show_legacy_editor()
        application.processEvents()
        assert host.stack.count() == 1
    assert host.current_mode == "legacy"
    host.apply_button.click()
    assert apply_calls == 1
    _dispose(host, application)
