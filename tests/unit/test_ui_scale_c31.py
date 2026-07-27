from __future__ import annotations

import ast
from collections import Counter
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRect, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QFont, QFontMetrics, QIcon, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLabel, QPushButton, QStyle, QTableView, QToolButton, QWidget

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.feature_flags import UiFeatureFlags
from hms_cadcam.ui.i18n import (
    CORE_TRANSLATIONS,
    TranslationCatalog,
    TranslationService,
    UiLanguage,
    apply_application_font,
    build_default_catalogs,
    translation_service,
)
from hms_cadcam.ui.main_window import MainWindow, responsive_minimum_size
from hms_cadcam.ui.post_assembly_panel import (
    PostAssemblyOperationRow,
    PostAssemblyOperationTableModel,
    PostAssemblyScrollSnapshot,
    UnifiedPostAssemblyPanel,
)
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.ui.settings import (
    DEFAULT_PERCENT,
    UI_SCALE_PRESETS,
    UI_SCALE_SETTINGS_KEY,
    GeneralSettingsDialog,
    UiScaleManager,
    validate_percent,
)
from hms_cadcam.ui.settings.general_settings import settings_dialog_geometry
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(
        str(tmp_path / "ui_scale.ini"),
        QSettings.Format.IniFormat,
    )


def _rows() -> tuple[PostAssemblyOperationRow, ...]:
    return (
        PostAssemblyOperationRow(
            operation_id="op-1",
            execution_order=0,
            operation_name="Pocket",
            operation_type="pocket",
            strategy="contour_2d",
            tool="T1 Ø10",
            setup="Setup A",
            status="CURRENT",
        ),
    )


def _scroll_rows(
    count: int = 30, *, prefix: str = "op"
) -> tuple[PostAssemblyOperationRow, ...]:
    return tuple(
        PostAssemblyOperationRow(
            operation_id=f"{prefix}-{index}",
            execution_order=index,
            operation_name=f"Operation {index}",
            operation_type="operation",
            strategy="contour_2d",
            tool="T1",
            setup="Setup 1",
            status="CURRENT",
        )
        for index in range(count)
    )


def _scroll_panel(
    tmp_path: Path, *, row_count: int = 30
) -> tuple[UiScaleManager, UnifiedPostAssemblyPanel]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manager = UiScaleManager(_settings(tmp_path))
    panel = UnifiedPostAssemblyPanel()
    panel.resize(900, 520)
    panel.set_operation_rows(_scroll_rows(row_count))
    panel.show()
    panel.apply_ui_scale(manager)
    QApplication.processEvents()
    if row_count:
        current_row = min(22, row_count - 1)
        panel.select_operation(f"op-{current_row}")
        panel.operation_table.horizontalHeader().setStretchLastSection(False)
        for column in range(panel.model.columnCount()):
            panel.operation_table.setColumnWidth(column, 180)
        panel.operation_table.scrollTo(
            panel.model.index(current_row, 0),
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        QApplication.processEvents()
        horizontal = panel.operation_table.horizontalScrollBar()
        horizontal.setValue(horizontal.maximum())
        QApplication.processEvents()
    return manager, panel


def _assert_same_scroll_anchor(
    before: PostAssemblyScrollSnapshot,
    after: PostAssemblyScrollSnapshot,
) -> None:
    assert after.current_operation_id == before.current_operation_id
    assert after.selected_operation_ids == before.selected_operation_ids
    assert after.top_visible_operation_id == before.top_visible_operation_id
    assert after.top_visible_row == before.top_visible_row
    assert abs(after.top_visible_offset_px - before.top_visible_offset_px) <= 1
    assert abs(after.current_row_offset_px - before.current_row_offset_px) <= 1
    if after.viewport_size == before.viewport_size:
        assert after.horizontal_value == before.horizontal_value
    else:
        assert after.horizontal_normalized_position == pytest.approx(
            before.horizontal_normalized_position, abs=0.01
        )


def test_scale_validation_and_corrupt_persistence(tmp_path: Path) -> None:
    assert validate_percent(49) == 50
    assert validate_percent(50) == 50
    assert validate_percent(55) == 55
    assert validate_percent(100) == 100
    assert validate_percent(195) == 195
    assert validate_percent(200) == 200
    assert validate_percent(201) == 200
    assert validate_percent("corrupt") == DEFAULT_PERCENT
    settings = _settings(tmp_path)
    settings.setValue(UI_SCALE_SETTINGS_KEY, "corrupt")
    settings.sync()
    manager = UiScaleManager(settings)
    assert manager.current_percent == DEFAULT_PERCENT


def test_preview_apply_cancel_and_single_apply_signal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    emissions: list[int] = []
    manager.scale_changed.connect(emissions.append)
    manager.set_preview_percent(125)
    assert settings.value(UI_SCALE_SETTINGS_KEY, None) is None
    assert manager.apply_percent()
    assert emissions == [125]
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 125
    manager.set_preview_percent(150)
    assert manager.cancel_preview() == 125
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 125
    assert manager.reset_default() == 100
    assert manager.current_percent == 100
    assert manager.persisted_percent == 125
    assert manager.cancel_preview() == 125
    assert manager.reset_default() == 100
    assert manager.apply_percent()
    assert emissions == [125, 100]


def test_scale_metrics_are_baseline_derived_without_cumulative_drift(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    button = QPushButton("baseline")
    baseline_point = button.font().pointSizeF()
    manager.apply_widget_tree(button)
    baseline_height = button.minimumHeight()
    manager.set_preview_percent(150)
    manager.apply_widget_tree(button)
    assert button.font().pointSizeF() == pytest.approx(baseline_point * 1.5)
    assert button.minimumHeight() >= baseline_height
    manager.set_preview_percent(75)
    manager.apply_widget_tree(button)
    assert button.font().pointSizeF() == pytest.approx(baseline_point * 0.75)
    manager.set_preview_percent(100)
    manager.apply_widget_tree(button)
    assert button.font().pointSizeF() == pytest.approx(baseline_point)
    assert button.minimumHeight() == baseline_height
    manager.set_preview_percent(150)
    assert "6px" in manager.scale_stylesheet("QPushButton { padding: 4px; }")
    manager.set_preview_percent(75)
    assert "3px" in manager.scale_stylesheet("QPushButton { padding: 4px; }")


def test_table_model_and_real_view_receive_translated_text() -> None:
    model = PostAssemblyOperationTableModel(_rows())
    view = QTableView()
    view.setModel(model)
    view.show()
    QApplication.processEvents()
    service = translation_service()
    original = service.language
    try:
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            service.set_language(language)
            model.retranslate_ui(language)
            headers = tuple(
                str(
                    model.headerData(
                        column,
                        Qt.Orientation.Horizontal,
                        int(Qt.ItemDataRole.DisplayRole),
                    )
                )
                for column in range(model.columnCount())
            )
            assert all(
                header and header not in {"1", "2", "3", "4", "5", "6"}
                for header in headers
            )
            assert all(
                model.data(model.index(0, column), int(Qt.ItemDataRole.DisplayRole))
                for column in range(6)
            )
            assert all(
                view.model().data(
                    view.model().index(0, column),
                    int(Qt.ItemDataRole.DisplayRole),
                )
                for column in range(6)
            )
    finally:
        service.set_language(original)
        view.close()


def test_empty_model_retranslate_has_no_invalid_data_emission() -> None:
    model = PostAssemblyOperationTableModel(())
    data_emissions: list[tuple[object, ...]] = []
    header_emissions: list[tuple[object, ...]] = []
    model.dataChanged.connect(lambda *args: data_emissions.append(args))
    model.headerDataChanged.connect(lambda *args: header_emissions.append(args))
    model.retranslate_ui(UiLanguage.EN_US)
    assert data_emissions == []
    assert len(header_emissions) == 1


def test_dialog_shell_scale_controls_preview_and_i18n(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    assert dialog.category_list.count() == 8
    assert tuple(
        dialog.category_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(8)
    ) == (
        "Interface",
        "Keyboard shortcuts",
        "Language",
        "Storage & projects",
        "CAD/Viewer",
        "CAM",
        "Performance",
        "Advanced",
    )
    for row in range(8):
        dialog.category_list.setCurrentRow(row)
        assert dialog.page_stack.currentIndex() == row
    dialog.category_list.setCurrentRow(0)
    assert dialog.scale_slider.singleStep() == 5
    assert dialog.scale_spin.singleStep() == 1
    assert not dialog.sample_button.icon().isNull()
    assert tuple(manager_value for manager_value in UI_SCALE_PRESETS) == tuple(
        sorted(dialog.preset_buttons)
    )
    dialog.scale_spin.setValue(53)
    assert manager.current_percent == 53
    assert dialog.apply_button.isEnabled()
    assert settings.value(UI_SCALE_SETTINGS_KEY, None) is None
    dialog.apply_button.click()
    assert manager.persisted_percent == 53
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 53
    service = translation_service()
    original = service.language
    try:
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            service.set_language(language)
            QApplication.processEvents()
            assert manager.current_percent == 53
            assert dialog.category_list.count() == 8
            assert all(dialog.category_list.item(i).text() for i in range(8))
            assert dialog.preview_status.text()
    finally:
        service.set_language(original)
        dialog.close()


def test_main_window_general_settings_entry_is_idempotent(tmp_path: Path) -> None:
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("C3.1 test"),
        UnavailableCadViewportBackend("C3.1 test"),
        ui_feature_flags=UiFeatureFlags.for_review_harness(),
    )
    try:
        action = window.findChild(type(window.post_assembly_action), "GeneralSettingsAction")
        assert action is not None
        assert (
            action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
            == "Ctrl+,"
        )
        action.trigger()
        first = window._general_settings_dialog
        action.trigger()
        assert window._general_settings_dialog is first
        assert first is not None and first.isVisible()
        ribbon_buttons = window._ribbon.findChildren(QToolButton)
        ribbon_entry = next(
            button for button in ribbon_buttons if button.defaultAction() is action
        )
        ribbon_entry.click()
        assert window._general_settings_dialog is first
        first.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        assert window._general_settings_dialog is None
    finally:
        window.close()
        QApplication.processEvents()


def test_main_window_general_settings_retranslate_has_single_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = translation_service()
    original_language = service.language
    retranslate_calls: list[UiLanguage | None] = []
    preview_calls: list[int] = []
    count_runtime_calls = False
    original_retranslate = GeneralSettingsDialog.retranslate_ui
    original_preview = GeneralSettingsDialog._preview_scale_changed

    def counted_retranslate(dialog: GeneralSettingsDialog, language: object = None) -> None:
        if count_runtime_calls:
            retranslate_calls.append(UiLanguage.coerce(language) if language is not None else None)
        original_retranslate(dialog, language)

    def counted_preview(dialog: GeneralSettingsDialog, value: int) -> None:
        if count_runtime_calls:
            preview_calls.append(value)
        original_preview(dialog, value)

    monkeypatch.setattr(GeneralSettingsDialog, "retranslate_ui", counted_retranslate)
    monkeypatch.setattr(GeneralSettingsDialog, "_preview_scale_changed", counted_preview)
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("C3.1 F1"),
        UnavailableCadViewportBackend("C3.1 F1"),
        ui_feature_flags=UiFeatureFlags.for_review_harness(),
    )
    emissions: list[UiLanguage] = []
    try:
        service.set_language(UiLanguage.VI_VN)
        window.show()
        window._show_general_settings()
        QApplication.processEvents()
        dialog = window._general_settings_dialog
        assert dialog is not None
        assert dialog.scale_manager.apply_percent(100)
        dialog.scale_manager.set_preview_percent(125)
        dialog.category_list.setCurrentRow(2)
        QApplication.processEvents()
        page_ids = tuple(id(dialog.page_stack.widget(i)) for i in range(dialog.page_stack.count()))
        selected_category = dialog.selected_category
        settings_before = {
            key: dialog.scale_manager.settings.value(key)
            for key in dialog.scale_manager.settings.allKeys()
        }
        service.language_changed.connect(emissions.append)
        count_runtime_calls = True
        for language in (UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN):
            assert service.set_language(language)
            QApplication.processEvents()
            assert window._general_settings_dialog is dialog
            assert dialog.selected_category == selected_category
            assert tuple(id(dialog.page_stack.widget(i)) for i in range(dialog.page_stack.count())) == page_ids
            assert dialog.scale_manager.current_percent == 125
            assert dialog.scale_manager.persisted_percent == 100

        assert emissions == [UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN]
        assert retranslate_calls == [UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN]
        assert preview_calls == [125, 125, 125]
        assert {
            key: dialog.scale_manager.settings.value(key)
            for key in dialog.scale_manager.settings.allKeys()
        } == settings_before

        for index in range(10):
            language = (UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN)[index % 3]
            current = window._general_settings_dialog
            assert current is not None
            current.close()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
            assert window._general_settings_dialog is None
            window._show_general_settings()
            QApplication.processEvents()
            current = window._general_settings_dialog
            assert current is not None and current.isVisible()
            assert len(window.findChildren(GeneralSettingsDialog)) == 1
            assert service.set_language(language)
            QApplication.processEvents()
            assert window._general_settings_dialog is current
    finally:
        try:
            service.language_changed.disconnect(emissions.append)
        except (RuntimeError, TypeError):
            pass
        service.set_language(original_language)
        if window._general_settings_dialog is not None:
            window._general_settings_dialog.close()
        window.close()
        QApplication.processEvents()

def test_post_assembly_scale_hook_updates_table_metrics(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    panel = UnifiedPostAssemblyPanel()
    panel.set_operation_rows(_rows())
    manager.set_preview_percent(150)
    panel.apply_ui_scale(manager)
    metrics = manager.metrics()
    assert panel.operation_table.horizontalHeader().defaultSectionSize() == metrics.header_height
    assert panel.operation_table.verticalHeader().defaultSectionSize() == metrics.row_height
    assert panel.model.data(panel.model.index(0, 0), int(Qt.ItemDataRole.DisplayRole))
    assert panel.selected_operation_id is None
    panel.resize(900, 596)
    panel.apply_ui_scale(manager)
    assert all(
        group.isHidden()
        for group in (
            panel.artifact_summary,
            panel.preview_placeholder,
            panel.diagnostics_placeholder,
        )
    )
    manager.set_preview_percent(100)
    panel.apply_ui_scale(manager)
    assert all(
        not group.isHidden()
        for group in (
            panel.artifact_summary,
            panel.preview_placeholder,
            panel.diagnostics_placeholder,
        )
    )
    panel.close()


def test_ribbon_and_metrics_cover_required_scale_values(tmp_path: Path) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    parent = QWidget()
    ribbon = RibbonWidget({}, {}, parent, ui_scale_manager=manager)
    for percent in (50, 75, 100, 150, 200):
        manager.set_preview_percent(percent)
        QApplication.processEvents()
        assert manager.current_percent == percent
        assert ribbon.height() == manager.scaled_int(112, minimum=80)
        assert ribbon.iconSize() == manager.scaled_icon_size(
            QSize(24, 24), minimum=16
        )
        metrics = manager.metrics()
        assert metrics.percent == percent
        assert metrics.row_height >= 18
        assert metrics.header_height >= 22
    ribbon.close()


def test_ribbon_metrics_are_baseline_derived_and_round_trip(tmp_path: Path) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    parent = QWidget()
    action = QAction("Save", parent)
    action.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
    action.setCheckable(True)
    action.setChecked(True)
    ribbon = RibbonWidget({"save": action}, {}, parent, ui_scale_manager=manager)
    ribbon.show()
    QApplication.processEvents()
    button = next(
        item for item in ribbon.findChildren(QToolButton)
        if item.defaultAction() is action
    )
    assert ribbon.metrics is not None
    baseline = ribbon.metrics
    baseline_style = ribbon.styleSheet()
    icon_key = action.icon().cacheKey()
    styles: dict[int, str] = {}
    for percent in (100, 150, 75, 200, 50, 100, 125, 175, 100):
        manager.set_preview_percent(percent)
        QApplication.processEvents()
        metrics = ribbon.metrics
        assert metrics is not None and metrics.percent == percent
        assert metrics.icon_size.width() > 0 and metrics.icon_size.height() > 0
        assert metrics.ribbon_height >= 80
        assert metrics.group_spacing >= 1
        assert metrics.action_button_minimum_width >= 24
        assert metrics.action_button_padding_horizontal >= 4
        assert metrics.action_button_padding_vertical >= 1
        assert metrics.separator_width >= 1
        assert metrics.menu_padding_left >= 1
        assert metrics.tab_spacing >= 1
        assert ribbon.height() == metrics.ribbon_height
        assert ribbon.iconSize() == metrics.icon_size
        assert ribbon._page_layouts[0].contentsMargins() == metrics.page_margins
        assert ribbon._group_layouts[0].contentsMargins() == metrics.group_margins
        assert ribbon._group_layouts[0].spacing() == metrics.group_spacing
        assert button.minimumWidth() >= metrics.action_button_minimum_width
        assert action.isChecked() and button.isChecked()
        assert action.icon().cacheKey() == icon_key
        styles[percent] = ribbon.styleSheet()
    manager.set_preview_percent(150)
    QApplication.processEvents()
    assert ribbon.styleSheet() == styles[150]
    manager.set_preview_percent(100)
    QApplication.processEvents()
    assert ribbon.metrics == baseline
    assert ribbon.styleSheet() == baseline_style
    ribbon.close()
    parent.close()


def test_general_settings_shortcut_portable_across_locales_and_key_event(
    tmp_path: Path,
) -> None:
    service = translation_service()
    original_language = service.language
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("C3.1 D1"),
        UnavailableCadViewportBackend("C3.1 D1"),
        ui_feature_flags=UiFeatureFlags.for_production(),
    )
    try:
        action = window.findChild(type(window.post_assembly_action), "GeneralSettingsAction")
        assert action is not None
        window.show()
        window.activateWindow()
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            service.set_language(language)
            QApplication.processEvents()
            assert action.shortcut().toString(
                QKeySequence.SequenceFormat.PortableText
            ) == "Ctrl+,"
        QTest.keyClick(
            window,
            Qt.Key.Key_Comma,
            Qt.KeyboardModifier.ControlModifier,
        )
        QApplication.processEvents()
        dialog = window._general_settings_dialog
        assert dialog is not None and dialog.isVisible()
    finally:
        service.set_language(original_language)
        if window._general_settings_dialog is not None:
            window._general_settings_dialog.close()
        window.close()
        QApplication.processEvents()


def test_settings_action_and_post_action_respect_feature_flag(tmp_path: Path) -> None:
    production = MainWindow(
        ProjectService.create_default(tmp_path / "production"),
        UnavailableCadKernel("C3.1 D1"),
        UnavailableCadViewportBackend("C3.1 D1"),
        ui_feature_flags=UiFeatureFlags.for_production(),
    )
    review = MainWindow(
        ProjectService.create_default(tmp_path / "review"),
        UnavailableCadKernel("C3.1 D1"),
        UnavailableCadViewportBackend("C3.1 D1"),
        ui_feature_flags=UiFeatureFlags.for_review_harness(),
    )
    try:
        assert not production._post_assembly_review_host
        assert production._general_settings_action is not None
        assert review._post_assembly_review_host
        assert review._general_settings_action is not None
        assert review.post_assembly_action is not production._general_settings_action
        review_action_buttons = review._ribbon.findChildren(QToolButton)
        assert sum(
            button.defaultAction() is review.post_assembly_action
            for button in review_action_buttons
        ) == 1
        assert sum(
            button.defaultAction() is review._general_settings_action
            for button in review_action_buttons
        ) == 1
    finally:
        production.close()
        review.close()
        QApplication.processEvents()


def test_post_assembly_scroll_round_trip_preserves_anchor_and_signals(
    tmp_path: Path,
) -> None:
    manager, panel = _scroll_panel(tmp_path)
    table = panel.operation_table
    before = panel.capture_scroll_snapshot()
    assert before is not None
    assert before.current_operation_id == "op-22"
    assert before.selected_operation_ids == ("op-22",)
    assert before.top_visible_operation_id is not None
    assert before.horizontal_value > 0
    assert 0.0 <= before.vertical_normalized_position <= 1.0
    assert before.viewport_size.width() > 0 and before.viewport_size.height() > 0
    order = tuple(row.operation_id for row in panel.model.rows)
    data_emissions: list[tuple[object, ...]] = []
    selection_emissions: list[tuple[object, ...]] = []
    current_emissions: list[tuple[object, ...]] = []
    panel.model.dataChanged.connect(lambda *args: data_emissions.append(args))
    table.selectionModel().selectionChanged.connect(
        lambda *args: selection_emissions.append(args)
    )
    table.selectionModel().currentChanged.connect(
        lambda *args: current_emissions.append(args)
    )
    try:
        for percent in (150, 100):
            manager.set_preview_percent(percent)
            panel.apply_ui_scale(manager)
            QApplication.processEvents()
        after = panel.capture_scroll_snapshot()
        assert after is not None
        _assert_same_scroll_anchor(before, after)
        assert tuple(row.operation_id for row in panel.model.rows) == order
        assert panel.selected_operation_id == "op-22"
        assert table.currentIndex().row() == panel.model.row_for_operation_id("op-22")
        assert data_emissions == []
        assert selection_emissions == []
        assert current_emissions == []
    finally:
        panel.close()
        QApplication.processEvents()


def test_post_assembly_scroll_extreme_cycle_and_rapid_preview_coalesce(
    tmp_path: Path,
) -> None:
    manager, panel = _scroll_panel(tmp_path)
    before = panel.capture_scroll_snapshot()
    assert before is not None
    try:
        for percent in (200, 50, 100):
            manager.set_preview_percent(percent)
            panel.apply_ui_scale(manager)
            QApplication.processEvents()
        after_cycle = panel.capture_scroll_snapshot()
        assert after_cycle is not None
        _assert_same_scroll_anchor(before, after_cycle)
        generation = panel._scroll_restore_generation
        rapid_sequence = (105, 110, 125, 150, 175, 200, 150, 100)
        for percent in rapid_sequence:
            manager.set_preview_percent(percent)
            panel.apply_ui_scale(manager)
        assert panel._scroll_restore_scheduled
        assert panel._scroll_restore_generation == generation + len(rapid_sequence)
        QApplication.processEvents()
        after_rapid = panel.capture_scroll_snapshot()
        assert after_rapid is not None
        _assert_same_scroll_anchor(before, after_rapid)
        assert not panel._scroll_restore_scheduled
        assert panel._pending_scroll_snapshot is None
        assert panel._scale_scroll_anchor is None
    finally:
        panel.close()
        QApplication.processEvents()


def test_post_assembly_scroll_empty_one_row_and_model_fallbacks(
    tmp_path: Path,
) -> None:
    empty_manager, empty = _scroll_panel(tmp_path / "empty", row_count=0)
    try:
        assert empty.capture_scroll_snapshot() is None
        for percent in (150, 100):
            empty_manager.set_preview_percent(percent)
            empty.apply_ui_scale(empty_manager)
            QApplication.processEvents()
        assert empty.model.rowCount() == 0
    finally:
        empty.close()
    one_manager, one = _scroll_panel(tmp_path / "one", row_count=1)
    try:
        before_one = one.capture_scroll_snapshot()
        assert before_one is not None
        for percent in (200, 50, 100):
            one_manager.set_preview_percent(percent)
            one.apply_ui_scale(one_manager)
            QApplication.processEvents()
        after_one = one.capture_scroll_snapshot()
        assert after_one is not None
        assert after_one.current_operation_id == "op-0"
        assert after_one.selected_operation_ids == ("op-0",)
        one.set_operation_rows(_scroll_rows(30, prefix="new"))
        one.restore_scroll_snapshot(before_one)
        assert one.operation_table.currentIndex().isValid()
        assert one.selected_operation_id is not None
        assert one.selected_operation_id.startswith("new-")
    finally:
        one.close()
        QApplication.processEvents()


def test_post_assembly_scroll_reorder_delete_locale_and_cancel(
    tmp_path: Path,
) -> None:
    service = translation_service()
    original_language = service.language
    manager, panel = _scroll_panel(tmp_path)
    before = panel.capture_scroll_snapshot()
    assert before is not None and before.top_visible_operation_id is not None
    try:
        original_order = panel.operation_ids
        assert panel.move_selected_operation(-1)
        panel.restore_scroll_snapshot(before)
        assert panel.selected_operation_id == "op-22"
        assert panel.operation_ids != original_order
        assert panel.model.row_for_operation_id("op-22") == 21
        current_rect = panel.operation_table.visualRect(
            panel.operation_table.currentIndex()
        )
        assert current_rect.bottom() >= 0
        assert current_rect.top() < panel.operation_table.viewport().height()
        anchor_id = before.top_visible_operation_id
        remaining = tuple(
            row for row in _scroll_rows(30) if row.operation_id != anchor_id
        )
        panel.set_operation_rows(remaining)
        panel.restore_scroll_snapshot(before)
        assert panel.operation_table.currentIndex().isValid()
        assert panel.selected_operation_id == "op-22"
        panel.set_operation_rows(_scroll_rows(30))
        panel.select_operation("op-22")
        panel.operation_table.scrollTo(
            panel.model.index(22, 0), QAbstractItemView.ScrollHint.PositionAtCenter
        )
        QApplication.processEvents()
        locale_anchor = panel.capture_scroll_snapshot()
        assert locale_anchor is not None
        manager.set_preview_percent(150)
        panel.apply_ui_scale(manager)
        QApplication.processEvents()
        service.set_language(UiLanguage.KO_KR)
        QApplication.processEvents()
        assert manager.cancel_preview() == 100
        panel.apply_ui_scale(manager)
        QApplication.processEvents()
        after_locale_cancel = panel.capture_scroll_snapshot()
        assert after_locale_cancel is not None
        _assert_same_scroll_anchor(locale_anchor, after_locale_cancel)
    finally:
        service.set_language(original_language)
        panel.close()
        QApplication.processEvents()


def test_post_assembly_panel_delete_before_deferred_restore_is_safe(
    tmp_path: Path,
) -> None:
    manager, panel = _scroll_panel(tmp_path)
    manager.set_preview_percent(150)
    panel.apply_ui_scale(manager)
    assert panel._scroll_restore_scheduled
    panel.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()

def test_scale_layer_does_not_change_qt_dpi_environment(tmp_path: Path) -> None:
    before = os.environ.get("QT_SCALE_FACTOR")
    manager = UiScaleManager(_settings(tmp_path))
    manager.set_preview_percent(200)
    assert os.environ.get("QT_SCALE_FACTOR") == before
    effective = responsive_minimum_size(
        QRect(0, 0, 1000, 640),
        QSize(1024, 680),
        QSize(16, 39),
    )
    assert effective.width() <= 984
    assert effective.height() <= 601


@pytest.mark.parametrize("percent", (75, 100, 150, 200))
@pytest.mark.parametrize("profile", ((1920, 1080), (1600, 900), (1366, 768), (1280, 720)))
def test_responsive_profiles_bound_minimum_and_keep_positive_metrics(
    tmp_path: Path,
    percent: int,
    profile: tuple[int, int],
) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    manager.set_preview_percent(percent)
    available = QRect(0, 0, profile[0], profile[1])
    effective = responsive_minimum_size(
        available,
        QSize(1024, 680),
        QSize(16, 39),
    )
    assert effective.width() <= available.width() - 16
    assert effective.height() <= available.height() - 39
    metrics = manager.metrics()
    assert metrics.row_height > 0
    assert metrics.header_height > 0
    assert metrics.icon_size.width() > 0
    assert metrics.icon_size.height() > 0


def test_escape_and_window_close_restore_persisted_preview(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    assert manager.apply_percent(100)
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    manager.set_preview_percent(150)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert manager.current_percent == 100
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 100

    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    manager.set_preview_percent(75)
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert manager.current_percent == 100
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 100


def _c31_production_keys() -> frozenset[str]:
    root = Path(__file__).resolve().parents[2]
    source_path = root / "src/hms_cadcam/ui/settings/general_settings.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ui_text"
            and node.args
        ):
            keys.update(
                child.value
                for child in ast.walk(node.args[0])
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_SETTINGS_CATEGORIES":
                keys.update(
                    ast.literal_eval(call.args[0]) for call in node.value.elts
                )
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_SHELL_EMPTY_MESSAGE"
            for target in node.targets
        ):
            keys.add(ast.literal_eval(node.value))
    main_source = (root / "src/hms_cadcam/ui/main_window.py").read_text(
        encoding="utf-8"
    )
    for action_key in ("General settings", "Open general settings..."):
        assert f'ui_text("{action_key}")' in main_source
        keys.add(action_key)
    keys.add("Features")
    return frozenset(keys)


def _catalog_pairs(locale: UiLanguage) -> tuple[tuple[str, str], ...]:
    root = Path(__file__).resolve().parents[2]
    path = root / f"src/hms_cadcam/ui/catalogs/{locale.value}.json"
    payload = path.read_bytes()
    assert not payload.startswith(b"\xef\xbb\xbf")
    document = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=lambda pairs: tuple(pairs),
    )
    assert isinstance(document, tuple)
    return tuple((str(key), str(value)) for key, value in document)


def test_c31_core_and_catalog_key_coverage_is_source_derived() -> None:
    required = _c31_production_keys()
    assert len(required) == 36
    core_counts = Counter(row[0] for row in CORE_TRANSLATIONS)
    assert not {key: count for key, count in core_counts.items() if count > 1}
    assert required <= core_counts.keys()

    catalogs = {locale: dict(_catalog_pairs(locale)) for locale in UiLanguage}
    core = {row[0]: row[1:] for row in CORE_TRANSLATIONS}
    for key in required:
        assert all(key in catalogs[locale] for locale in UiLanguage)
        assert core[key] == (
            catalogs[UiLanguage.VI_VN][key],
            catalogs[UiLanguage.KO_KR][key],
        )


@pytest.mark.parametrize("locale", tuple(UiLanguage))
def test_c31_catalog_json_unicode_and_duplicate_integrity(locale: UiLanguage) -> None:
    required = _c31_production_keys()
    pairs = _catalog_pairs(locale)
    counts = Counter(key for key, _value in pairs)
    assert not {key: count for key, count in counts.items() if count > 1}
    catalog = dict(pairs)
    assert required <= catalog.keys()
    values = {key: catalog[key] for key in required}
    assert all(value.strip() for value in values.values())
    assert all("\ufffd" not in value for value in values.values())
    assert all("?" not in value for value in values.values())
    if locale is UiLanguage.EN_US:
        assert all(values[key] == key for key in required)
    else:
        technical = {"CAD/Viewer", "CAM"}
        assert all(values[key] != key for key in required - technical)


def test_features_translation_exact_unicode_values() -> None:
    expected = {
        UiLanguage.VI_VN: "T\u00ednh n\u0103ng",
        UiLanguage.EN_US: "Features",
        UiLanguage.KO_KR: "\uae30\ub2a5",
    }
    for locale, value in expected.items():
        catalog = dict(_catalog_pairs(locale))
        assert catalog["Features"] == value
        assert "?" not in value
        assert "\ufffd" not in value


def test_c31_missing_locale_key_falls_back_to_vietnamese_fixture() -> None:
    key = "Features"
    vietnamese = "T\u00ednh n\u0103ng"
    service = TranslationService(
        {
            UiLanguage.VI_VN: TranslationCatalog.from_pairs(
                UiLanguage.VI_VN, ((key, vietnamese),)
            ),
            UiLanguage.EN_US: TranslationCatalog.from_pairs(UiLanguage.EN_US, ()),
            UiLanguage.KO_KR: TranslationCatalog.from_pairs(UiLanguage.KO_KR, ()),
        },
        language=UiLanguage.EN_US,
    )
    assert service.translate_key(key) == vietnamese
    assert service.diagnostics[-1].resolution == "VI_VN_FALLBACK"
    service.set_language("unsupported-locale")
    assert service.language is UiLanguage.VI_VN
    assert service.translate_key(key) == vietnamese


def test_general_settings_source_has_no_obsolete_or_raw_sample_phrases() -> None:
    root = Path(__file__).resolve().parents[2]
    settings_source = (
        root / "src/hms_cadcam/ui/settings/general_settings.py"
    ).read_text(encoding="utf-8")
    catalogs = "\n".join(
        (root / f"src/hms_cadcam/ui/catalogs/{locale.value}.json").read_text(
            encoding="utf-8"
        )
        for locale in UiLanguage
    )
    forbidden_roadmap = "reserved for a later " + "C3.x step"
    damaged_vietnamese = "T" + "?nh n?ng"
    assert forbidden_roadmap not in settings_source
    assert forbidden_roadmap not in catalogs
    assert damaged_vietnamese not in catalogs
    assert 'setHeaderLabels(["Sample tree"])' not in settings_source
    assert 'QTreeWidgetItem(["Root"])' not in settings_source
    assert 'QTreeWidgetItem(["Child"])' not in settings_source


def test_general_settings_locale_cycle_preserves_runtime_state(tmp_path: Path) -> None:
    service = translation_service()
    original = service.language
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    assert manager.apply_percent(100)
    service.set_language(UiLanguage.VI_VN)
    dialog = GeneralSettingsDialog(manager, service=service)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(125)
    dialog.category_list.setCurrentRow(2)
    page_ids = tuple(id(dialog.page_stack.widget(i)) for i in range(8))
    emissions: list[UiLanguage] = []
    slot = emissions.append
    service.language_changed.connect(slot)
    catalogs = build_default_catalogs()

    def assert_locale(locale: UiLanguage) -> None:
        expected = catalogs[locale].entries
        assert dialog.windowTitle() == expected["General settings"]
        assert tuple(dialog.category_list.item(i).text() for i in range(8)) == tuple(
            expected[key]
            for key in (
                "Interface",
                "Keyboard shortcuts",
                "Language",
                "Storage & projects",
                "CAD/Viewer",
                "CAM",
                "Performance",
                "Advanced",
            )
        )
        assert dialog.interface_heading.text() == expected["Scale and density"]
        assert dialog.scale_label.text() == expected["UI scale"]
        assert dialog.apply_button.text() == expected["Apply"]
        assert dialog.cancel_button.text() == expected["Cancel"]
        assert dialog.reset_button.text() == expected["Reset"]
        assert expected["Preview: {percent}%"].format(percent=125) in dialog.preview_status.text()
        assert expected["Applied: {percent}%"].format(percent=100) in dialog.preview_status.text()
        placeholder = dialog._category_pages[1].findChildren(QLabel)[1]
        assert placeholder.text() == expected[
            "This settings category has no available options in the current version."
        ]
        resolved = (
            dialog.windowTitle(),
            *(dialog.category_list.item(i).text() for i in range(8)),
            dialog.interface_heading.text(),
            dialog.scale_label.text(),
            dialog.apply_button.text(),
            dialog.cancel_button.text(),
            dialog.reset_button.text(),
            dialog.preview_status.text(),
            placeholder.text(),
            expected["Features"],
        )
        assert all(value and "\ufffd" not in value and "?" not in value for value in resolved)
        assert dialog.selected_category == "Language"
        assert manager.current_percent == 125
        assert manager.persisted_percent == 100
        assert settings.value(UI_SCALE_SETTINGS_KEY) == 100
        assert tuple(id(dialog.page_stack.widget(i)) for i in range(8)) == page_ids

    try:
        assert_locale(UiLanguage.VI_VN)
        for locale in (UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN):
            before = len(emissions)
            assert service.set_language(locale)
            QApplication.processEvents()
            assert len(emissions) == before + 1
            assert_locale(locale)
        assert emissions == [UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN]
    finally:
        service.language_changed.disconnect(slot)
        service.set_language(original)
        dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


@pytest.mark.parametrize("profile", ((1280, 720), (1366, 768), (1600, 900), (1920, 1080)))
def test_settings_geometry_helper_keeps_200_percent_inside_profiles(
    profile: tuple[int, int],
) -> None:
    desired = QSize(1640, 1200)
    requested_minimum = QSize(1200, 840)
    evidence = settings_dialog_geometry(
        QRect(0, 0, profile[0], profile[1]),
        QSize(16, 39),
        desired,
        requested_minimum,
        content_minimum_size=desired,
    )
    assert evidence.maximum_dialog_size.width() <= profile[0] - 24 - 16
    assert evidence.maximum_dialog_size.height() <= profile[1] - 24 - 39
    assert evidence.contained_in_available_geometry
    assert evidence.target_dialog_size.width() <= evidence.maximum_dialog_size.width()
    assert evidence.target_dialog_size.height() <= evidence.maximum_dialog_size.height()
    assert evidence.content_scroll_required


def test_settings_dialog_uses_local_scroll_and_fixed_footer_at_200(tmp_path: Path) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(200)
    dialog._fit_to_screen()
    QApplication.processEvents()
    evidence = dialog.geometry_evidence()
    assert evidence.contained_in_available_geometry
    assert evidence.footer_accessible
    assert dialog.page_scroll.widget() is dialog.page_stack
    assert dialog.page_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dialog.page_scroll.verticalScrollBar().maximum() >= 0
    assert dialog.category_list.isVisible()
    assert all(button.isVisible() and button.width() > 0 and button.height() > 0 for button in dialog._footer_buttons)
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_settings_dialog_50_percent_keeps_positive_hit_targets(tmp_path: Path) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(50)
    dialog._fit_to_screen()
    QApplication.processEvents()
    assert dialog.scale_slider.height() > 0
    assert dialog.scale_spin.height() > 0
    assert all(button.width() > 0 and button.height() > 0 for button in dialog._footer_buttons)
    assert dialog.apply_button.text()
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_reset_is_scoped_to_interface_page_and_cancel_restores_applied(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = UiScaleManager(settings)
    assert manager.apply_percent(125)
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(150)
    dialog.category_list.setCurrentRow(0)
    dialog.reset_button.click()
    assert manager.current_percent == 100
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 125
    dialog._cancel()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert manager.current_percent == 125

    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(150)
    dialog.category_list.setCurrentRow(1)
    assert not dialog.reset_button.isEnabled()
    dialog._reset_default()
    assert manager.current_percent == 150
    assert settings.value(UI_SCALE_SETTINGS_KEY) == 125
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_settings_page_switches_do_not_emit_preview_or_rebuild_pages(tmp_path: Path) -> None:
    manager = UiScaleManager(_settings(tmp_path))
    dialog = GeneralSettingsDialog(manager)
    dialog.show()
    QApplication.processEvents()
    manager.set_preview_percent(150)
    emissions: list[int] = []
    manager.preview_changed.connect(emissions.append)
    page_ids = tuple(id(dialog.page_stack.widget(row)) for row in range(dialog.page_stack.count()))
    for row in (1, 4, 7, 0, 6, 0):
        dialog.category_list.setCurrentRow(row)
    assert manager.current_percent == 150
    assert emissions == []
    assert tuple(id(dialog.page_stack.widget(row)) for row in range(dialog.page_stack.count())) == page_ids
    dialog.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()



def _point_font(size: float, family: str | None = None) -> QFont:
    font = QFont(family or QApplication.instance().font())
    font.setPointSizeF(size)
    return font


def _pixel_font(size: int, family: str | None = None) -> QFont:
    font = QFont(family or QApplication.instance().font())
    font.setPointSize(-1)
    font.setPixelSize(size)
    return font


def test_application_point_font_baseline_scales_without_mode_drift(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_point_font(9.0))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        assert manager.application_font_mode == "point"
        for percent, expected in ((100, 9.0), (150, 13.5), (75, 6.75), (200, 18.0), (50, 4.5), (100, 9.0)):
            manager.set_preview_percent(percent)
            manager.apply_runtime()
            actual = app.font()
            assert actual.pointSizeF() == pytest.approx(expected)
            assert actual.pixelSize() <= 0
    finally:
        app.setFont(original)


def test_application_pixel_font_baseline_scales_without_point_conversion(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_pixel_font(16))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        assert manager.application_font_mode == "pixel"
        for percent, expected in ((100, 16), (150, 24), (75, 12), (200, 32), (50, 8), (100, 16)):
            manager.set_preview_percent(percent)
            manager.apply_runtime()
            actual = app.font()
            assert actual.pixelSize() == expected
            assert actual.pointSizeF() <= 0
            assert not (actual.pointSizeF() > 0 and actual.pixelSize() > 0)
    finally:
        app.setFont(original)


def test_external_point_font_rebase_preserves_current_scale(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_point_font(9.0))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        external = _point_font(10.0)
        app.setFont(external)
        assert manager.notify_external_application_font_changed()
        assert app.font().pointSizeF() == pytest.approx(15.0)
        assert manager.application_font_baseline().pointSizeF() == pytest.approx(10.0)
        manager.set_preview_percent(100)
        manager.apply_runtime()
        assert app.font().pointSizeF() == pytest.approx(10.0)
    finally:
        app.setFont(original)


def test_external_pixel_font_rebase_preserves_current_scale(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_pixel_font(16))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        app.setFont(_pixel_font(18))
        assert manager.rebase_application_font()
        assert app.font().pixelSize() == 27
        manager.set_preview_percent(100)
        manager.apply_runtime()
        assert app.font().pixelSize() == 18
        assert app.font().pointSizeF() <= 0
    finally:
        app.setFont(original)


def test_manager_applied_font_is_not_rebased_or_signal_recursive(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_point_font(9.0))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        preview_emissions: list[int] = []
        scale_emissions: list[int] = []
        manager.preview_changed.connect(preview_emissions.append)
        manager.scale_changed.connect(scale_emissions.append)
        assert not manager.notify_external_application_font_changed()
        assert manager.application_font_baseline().pointSizeF() == pytest.approx(9.0)
        assert preview_emissions == []
        assert scale_emissions == []
        manager.set_preview_percent(100)
        manager.apply_runtime()
        assert app.font().pointSizeF() == pytest.approx(9.0)
    finally:
        app.setFont(original)


def test_preview_cancel_after_external_rebase_keeps_theme_baseline(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_point_font(9.0))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        assert manager.apply_percent(100)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        app.setFont(_point_font(10.0))
        manager.notify_external_application_font_changed()
        manager.set_preview_percent(200)
        manager.apply_runtime()
        assert app.font().pointSizeF() == pytest.approx(20.0)
        assert manager.cancel_preview() == 100
        manager.apply_runtime()
        assert app.font().pointSizeF() == pytest.approx(10.0)
        assert manager.settings.value(UI_SCALE_SETTINGS_KEY) == 100
    finally:
        app.setFont(original)


def test_pixel_widget_baseline_remains_pixel_after_application_scale(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_pixel_font(16))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        widget = QPushButton("pixel")
        manager.apply_widget_tree(widget)
        assert widget.font().pixelSize() == 24
        assert widget.font().pointSizeF() <= 0
        manager.set_preview_percent(100)
        manager.apply_widget_tree(widget)
        assert widget.font().pixelSize() == 16
    finally:
        widget.close() if "widget" in locals() else None
        app.setFont(original)


@pytest.mark.parametrize("language", (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR))
@pytest.mark.parametrize("mode", ("point", "pixel"))
def test_locale_font_helper_preserves_glyphs_and_scale_mode(
    language: UiLanguage, mode: str, tmp_path: Path
) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        app.setFont(_point_font(9.0) if mode == "point" else _pixel_font(16))
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        before_family = app.font().family()
        apply_application_font(language, application=app)
        manager.notify_external_application_font_changed(already_scaled=True)
        assert manager.current_percent == 150
        if mode == "point":
            assert app.font().pointSizeF() == pytest.approx(13.5)
            assert app.font().pixelSize() <= 0
        else:
            assert app.font().pixelSize() == 24
            assert app.font().pointSizeF() <= 0
        sample = "Ti\u1ebfng Vi\u1ec7t \ud55c\uae00"
        metrics = QFontMetrics(app.font())
        assert metrics.horizontalAdvance(sample) > 0
        assert "\ufffd" not in sample
        assert "?" not in sample
        assert app.font().family() or before_family
    finally:
        app.setFont(original)



@pytest.mark.parametrize("mode", ("point", "pixel"))
def test_application_font_scaling_preserves_non_size_attributes(mode: str, tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    try:
        source = _point_font(9.0) if mode == "point" else _pixel_font(16)
        source.setWeight(QFont.Weight.DemiBold)
        source.setItalic(True)
        source.setStretch(110)
        source.setCapitalization(QFont.Capitalization.SmallCaps)
        source.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.4)
        app.setFont(source)
        expected = QFont(app.font())
        manager = UiScaleManager(_settings(tmp_path), application=app)
        manager.set_preview_percent(150)
        manager.apply_runtime()
        actual = app.font()
        assert actual.family() == expected.family()
        assert actual.styleName() == expected.styleName()
        assert actual.weight() == expected.weight()
        assert actual.italic() == expected.italic()
        assert actual.stretch() == expected.stretch()
        assert actual.capitalization() == expected.capitalization()
        assert actual.letterSpacingType() == expected.letterSpacingType()
        assert actual.letterSpacing() == pytest.approx(expected.letterSpacing())
    finally:
        app.setFont(original)


def test_persisted_125_new_main_windows_and_dialog_do_not_double_scale(tmp_path: Path) -> None:
    app = QApplication.instance()
    original = QFont(app.font())
    first: MainWindow | None = None
    second: MainWindow | None = None
    try:
        app.setFont(_point_font(9.0))
        settings_path = tmp_path / "window_scale.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setValue(UI_SCALE_SETTINGS_KEY, 125)
        settings.sync()
        first = MainWindow(
            ProjectService.create_default(tmp_path / "first_config"),
            UnavailableCadKernel("C3.1 Step B"),
            UnavailableCadViewportBackend("C3.1 Step B"),
            ui_feature_flags=UiFeatureFlags.for_review_harness(),
            layout_store=WorkspaceLayoutStore(
                QSettings(str(settings_path), QSettings.Format.IniFormat)
            ),
        )
        assert first._ui_scale_manager.current_percent == 125
        assert app.font().pointSizeF() == pytest.approx(11.25)
        first._show_general_settings()
        dialog = first._general_settings_dialog
        assert dialog is not None
        assert dialog.scale_manager.current_percent == 125
        second = MainWindow(
            ProjectService.create_default(tmp_path / "second_config"),
            UnavailableCadKernel("C3.1 Step B"),
            UnavailableCadViewportBackend("C3.1 Step B"),
            ui_feature_flags=UiFeatureFlags.for_review_harness(),
            layout_store=WorkspaceLayoutStore(
                QSettings(str(settings_path), QSettings.Format.IniFormat)
            ),
        )
        assert second._ui_scale_manager.current_percent == 125
        assert second._ui_scale_manager.application_font_baseline().pointSizeF() == pytest.approx(9.0)
        assert app.font().pointSizeF() == pytest.approx(11.25)
    finally:
        if second is not None:
            second.close()
        if first is not None:
            if first._general_settings_dialog is not None:
                first._general_settings_dialog.close()
            first.close()
        QApplication.processEvents()
        app.setFont(original)
