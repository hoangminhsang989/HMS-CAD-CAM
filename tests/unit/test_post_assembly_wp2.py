from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTableView, QWidget

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.cam.domain import ArtifactStatus
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.feature_flags import UiFeatureFlags
from hms_cadcam.ui.main_window import MainWindow, responsive_minimum_size
from hms_cadcam.ui.post_assembly_panel import (
    PostAssemblyOperationRow,
    PostAssemblyOperationTableModel,
    PostAssemblyProjectionAdapter,
    UnifiedPostAssemblyPanel,
)
from hms_cadcam.ui.ribbon import RibbonWidget
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tools.post_assembly_geometry_evidence import (
    capture_post_assembly_geometry,
    mapped_rect,
    minimum_exceeds_available,
    rect_inside,
    visible_sibling_overlaps,
)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _rows(count: int = 3):
    return tuple(
        PostAssemblyOperationRow(
            operation_id=f"op-{index}",
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


def test_operation_table_preserves_explicit_order_and_identity():
    model = PostAssemblyOperationTableModel(_rows())
    assert model.rowCount() == 3
    assert [model.operation_id_at(index) for index in range(3)] == [
        "op-0",
        "op-1",
        "op-2",
    ]
    assert model.data(model.index(1, 0)) == "2"
    assert model.data(model.index(1, 0), model.OPERATION_ID if hasattr(model, "OPERATION_ID") else 257) == "op-1"


def test_operation_table_rejects_duplicate_ids():
    rows = _rows(2)
    with pytest.raises(ValueError, match="duplicate"):
        PostAssemblyOperationTableModel((rows[0], rows[0]))


def test_panel_actions_change_assembly_only_and_preserve_selection():
    panel = UnifiedPostAssemblyPanel()
    panel.set_available_operations(_rows())
    panel.set_selected_available_operation("op-0")
    assert panel.add_selected_operation()
    panel.set_selected_available_operation("op-1")
    assert panel.add_selected_operation()
    panel.select_operation("op-1")
    assert panel.move_selected_operation(-1)
    assert panel.operation_ids == ("op-1", "op-0")
    assert panel.selected_operation_id == "op-1"
    assert panel.remove_selected_operation()
    assert panel.operation_ids == ("op-0",)
    assert panel.selected_operation_id == "op-0"
    panel.deleteLater()


def test_panel_wp3_wp4_actions_are_disabled():
    panel = UnifiedPostAssemblyPanel()
    assert not panel.generate_button.isEnabled()
    assert not panel.save_managed_button.isEnabled()
    assert not panel.export_external_button.isEnabled()
    assert not panel.preview_placeholder.isEnabled()
    assert not panel.diagnostics_placeholder.isEnabled()
    panel.deleteLater()


def test_adapter_without_project_fails_closed_without_domain_side_effects(tmp_path: Path):
    service = ProjectService.create_default(tmp_path / "config")
    adapter = PostAssemblyProjectionAdapter(service, None)
    evidence = adapter.capture()
    assert evidence.operation_rows == ()
    assert evidence.projection_input.operation_ids == ()
    assert evidence.projection_input.project_generation is None
    assert adapter.project(evidence.projection_input).readiness_state.value == "MISSING_INPUT"


def _window(tmp_path: Path, flags: UiFeatureFlags) -> MainWindow:
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("wp2 test"),
        UnavailableCadViewportBackend("wp2 test"),
        ui_feature_flags=flags,
    )


def test_main_window_uses_one_entry_action_and_legacy_fallback(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_development_and_tests())
    assert window.post_assembly_action.property("commandId") == "cam.post_assembly.open"
    window.post_assembly_action.trigger()
    app.processEvents()
    assert window.workspace_bar.active_workspace.value == "post"
    assert window.post_assembly_dock.objectName() == "SecondaryWorkflowDock"
    window.close()


def test_main_window_review_flag_opens_idempotent_unified_host(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    window.post_assembly_action.trigger()
    window.post_assembly_action.trigger()
    app.processEvents()
    assert not window.post_assembly_dock.isHidden()
    assert window.unified_post_assembly_panel.objectName() == "UnifiedPostAssemblyPanel"
    assert len(window.findChildren(type(window.post_assembly_dock))) >= 1
    window.close()


def test_panel_add_flow_uses_public_picker_and_qt_click() -> None:
    panel = UnifiedPostAssemblyPanel()
    panel.set_available_operations(_rows())
    assert panel.source_operation_picker.count() == 3
    assert not panel.add_button.isEnabled()
    panel.source_operation_picker.setCurrentIndex(0)
    assert panel.add_button.isEnabled()
    QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
    assert panel.operation_ids == ("op-0",)
    assert panel.selected_operation_id == "op-0"
    panel.source_operation_picker.setCurrentIndex(0)
    assert not panel.add_button.isEnabled()
    missing = replace(_rows()[2], enabled=False, missing=True)
    panel.set_available_operations((*_rows()[:2], missing))
    panel.source_operation_picker.setCurrentIndex(2)
    assert not panel.add_button.isEnabled()
    panel.deleteLater()


def test_main_window_production_operation_picker_add_flow(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    project = ProjectService.create_default(tmp_path / "config")
    session = project.create_project_from_source(tmp_path, "Production Add Flow", source)
    source_id = session.manifest.source_files[0].source_id
    workspace = CamWorkspace(project, lambda: source_id)
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_group()
    workspace.add_operation()
    snapshot = project.cam_snapshot
    job = snapshot.jobs[0]
    setup = job.setups[0]
    operation = setup.operation_tree.operations[0]
    valid_state = replace(
        operation.artifact_state, status=ArtifactStatus.VALID, dirty_reasons=()
    )
    valid_operation = replace(operation, artifact_state=valid_state)
    valid_tree = replace(setup.operation_tree, operations=(valid_operation,))
    job.update_operation_tree(setup.setup_id, valid_tree)
    project.stage_cam_snapshot(snapshot)
    project.save()
    window = MainWindow(
        project,
        UnavailableCadKernel("wp2 production add test"),
        UnavailableCadViewportBackend("wp2 production add test"),
        ui_feature_flags=UiFeatureFlags.for_review_harness(),
    )
    try:
        window.show()
        app.processEvents()
        window.post_assembly_action.trigger()
        app.processEvents()
        panel = window.unified_post_assembly_panel
        assert panel.source_operation_picker.count() == 1
        operation_id = panel.source_operation_picker.itemData(0)
        panel.source_operation_picker.setCurrentIndex(0)
        assert panel.add_button.isEnabled()
        QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
        assert panel.operation_ids == (operation_id,)
        assert panel.selected_operation_id == operation_id
        panel.source_operation_picker.setCurrentIndex(0)
        assert not panel.add_button.isEnabled()
    finally:
        window.close()
        workspace.deleteLater()
        app.processEvents()


def test_retranslate_has_one_model_emission_and_preserves_state() -> None:
    from hms_cadcam.ui.i18n import UiLanguage, translation_service

    service = translation_service()
    original = service.language
    service.set_language(UiLanguage.VI_VN)
    panel = UnifiedPostAssemblyPanel()
    panel.set_operation_rows(_rows(2))
    panel.select_operation("op-1")
    before = panel.snapshot_state()
    stable_widgets = (
        panel.title_label,
        panel.readiness_label,
        panel.summary_label,
        panel.operation_table_group,
        panel.operation_table,
        panel.source_operation_label,
        panel.source_operation_picker,
        panel.add_button,
        panel.remove_button,
        panel.move_up_button,
        panel.move_down_button,
        panel.clear_button,
        panel.generate_button,
        panel.save_managed_button,
        panel.export_external_button,
    )
    stable_names = tuple(widget.objectName() for widget in stable_widgets)
    emissions: list[object] = []
    logic_calls: list[str] = []
    original_model_retranslate = panel.model.retranslate_ui

    def counted_model_retranslate(language: UiLanguage | None = None) -> None:
        resolved = language or service.language
        logic_calls.append(resolved.value)
        original_model_retranslate(language)

    panel.model.retranslate_ui = counted_model_retranslate  # type: ignore[method-assign]
    panel.model.dataChanged.connect(lambda *args: emissions.append(args))
    try:
        for language in (
            UiLanguage.EN_US,
            UiLanguage.KO_KR,
            UiLanguage.VI_VN,
            UiLanguage.EN_US,
            UiLanguage.KO_KR,
            UiLanguage.VI_VN,
        ):
            emissions.clear()
            before_logic_calls = len(logic_calls)
            service.set_language(language)
            QApplication.processEvents()
            assert len(emissions) == 1
            assert len(logic_calls) - before_logic_calls == 1
            assert panel.operation_ids == before.operation_ids
            assert panel.selected_operation_id == before.selected_operation_id
            assert tuple(widget.objectName() for widget in stable_widgets) == stable_names
    finally:
        service.set_language(original)
        panel.deleteLater()


def test_ribbon_parent_remains_third_positional_argument() -> None:
    parent = QWidget()
    ribbon = RibbonWidget({}, {}, parent)
    keyword_ribbon = RibbonWidget({}, {}, parent, workspace_actions={})
    assert ribbon.parentWidget() is parent
    assert keyword_ribbon.parentWidget() is parent
    ribbon.deleteLater()
    keyword_ribbon.deleteLater()
    parent.deleteLater()


def test_layout_audit_measures_production_widget_geometry(tmp_path: Path) -> None:
    _require_native_geometry_qpa()
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    try:
        window.show()
        window.post_assembly_action.trigger()
        app.processEvents()
        panel = window.unified_post_assembly_panel
        panel.set_operation_rows(_rows(2))
        app.processEvents()
        audit = _layout_audit(window, panel)
        assert audit["clipped_widget_count"] == 0
        assert audit["overlap_count"] == 0
        assert audit["minimum_readable_dimensions"]["pass"] is True
        assert audit["table_viewport_bounds"][2] > 0
        assert audit["dock_content_bounds"][2] > 0
    finally:
        window.close()


def test_main_window_retranslate_owns_no_duplicate_panel_call(tmp_path: Path) -> None:
    from hms_cadcam.ui.i18n import UiLanguage, translation_service

    app = QApplication.instance() or QApplication([])
    service = translation_service()
    original = service.language
    service.set_language(UiLanguage.VI_VN)
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    panel = window.unified_post_assembly_panel
    panel.set_operation_rows(_rows(2))
    panel.select_operation("op-1")
    before = panel.snapshot_state()
    child_count = len(panel.findChildren(QWidget))
    emissions: list[object] = []
    panel.model.dataChanged.connect(lambda *args: emissions.append(args))
    try:
        for language in (UiLanguage.EN_US, UiLanguage.KO_KR, UiLanguage.VI_VN):
            emissions.clear()
            service.set_language(language)
            app.processEvents()
            assert len(emissions) == 1
            assert panel.operation_ids == before.operation_ids
            assert panel.selected_operation_id == before.selected_operation_id
            assert len(panel.findChildren(QWidget)) == child_count
    finally:
        service.set_language(original)
        window.close()

def test_qtableview_runtime_roles_render_translated_headers_and_all_cells() -> None:
    from hms_cadcam.ui.i18n import UiLanguage, translation_service

    class RuntimeProbeModel(PostAssemblyOperationTableModel):
        def __init__(self) -> None:
            self.data_calls: list[tuple[type[object], object]] = []
            self.header_calls: list[tuple[type[object], object, object]] = []
            super().__init__(_rows(1))

        def data(self, index, role=Qt.ItemDataRole.DisplayRole):
            self.data_calls.append((type(role), role))
            return super().data(index, role)

        def headerData(
            self, section, orientation, role=Qt.ItemDataRole.DisplayRole
        ):
            self.header_calls.append((type(role), role, orientation))
            return super().headerData(section, orientation, role)

    app = QApplication.instance() or QApplication([])
    service = translation_service()
    original = service.language
    model = RuntimeProbeModel()
    view = QTableView()
    view.setModel(model)
    view.resize(900, 260)
    view.show()
    try:
        app.processEvents()
        assert any(role_type is int and role == 0 for role_type, role in model.data_calls)
        assert any(
            role_type is int
            and role == 0
            and orientation == Qt.Orientation.Horizontal
            for role_type, role, orientation in model.header_calls
        )
        header_sources = ("Order", "Operation", "Strategy", "Tool", "Setup", "Status")
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            service.set_language(language)
            model.retranslate_ui(language)
            app.processEvents()
            headers = tuple(
                view.model().headerData(
                    section, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)
                )
                for section in range(model.columnCount())
            )
            assert headers == tuple(service.translate_key(key) for key in header_sources)
            assert all(
                isinstance(value, str) and value.strip()
                for value in headers
            )
            assert headers != tuple(range(1, 7))
            cells = tuple(
                view.model().data(
                    model.index(0, column), int(Qt.ItemDataRole.DisplayRole)
                )
                for column in range(model.columnCount())
            )
            assert len(cells) == 6
            assert all(isinstance(value, str) and value.strip() for value in cells)
    finally:
        service.set_language(original)
        view.close()
        model.deleteLater()


def test_empty_and_populated_model_retranslate_emit_valid_ranges_only() -> None:
    from hms_cadcam.ui.i18n import UiLanguage

    empty_model = PostAssemblyOperationTableModel()
    empty_data_emissions: list[tuple[object, ...]] = []
    empty_header_emissions: list[tuple[object, ...]] = []
    empty_model.dataChanged.connect(
        lambda *args: empty_data_emissions.append(args)
    )
    empty_model.headerDataChanged.connect(
        lambda *args: empty_header_emissions.append(args)
    )
    empty_model.retranslate_ui(UiLanguage.EN_US)
    assert empty_data_emissions == []
    assert len(empty_header_emissions) == 1
    assert empty_header_emissions[0][1:] == (0, 5)

    populated_model = PostAssemblyOperationTableModel(_rows(1))
    populated_emissions: list[tuple[object, ...]] = []
    populated_model.dataChanged.connect(
        lambda *args: populated_emissions.append(args)
    )
    populated_model.retranslate_ui(UiLanguage.KO_KR)
    assert len(populated_emissions) == 1
    top_left, bottom_right = populated_emissions[0][:2]
    assert top_left.isValid()
    assert bottom_right.isValid()
    assert (top_left.row(), top_left.column()) == (0, 0)
    assert (bottom_right.row(), bottom_right.column()) == (0, 5)



def _layout_audit(
    window: MainWindow,
    panel: UnifiedPostAssemblyPanel,
    *,
    capture_id: str = "stage9a7_wp2_layout",
    requested_window_size: tuple[int, int] | None = None,
) -> dict[str, object]:
    return capture_post_assembly_geometry(
        window,
        panel,
        capture_id=capture_id,
        requested_window_size=requested_window_size,
    )


def _require_native_geometry_qpa() -> None:
    if QApplication.platformName().lower() != "windows":
        pytest.skip("Runtime geometry evidence requires the native Windows QPA.")


def _assert_geometry_evidence(evidence: dict[str, object], row_count: int) -> None:
    assert evidence["schema_version"] == "stage9a7.wp2.geometry.v2"
    assert evidence["result"] == "PASS"
    assert evidence["exceeds_available_width"] is False
    assert evidence["exceeds_available_height"] is False
    assert evidence["footer_accessible"] is True
    assert isinstance(evidence["scroll_area_present"], bool)
    assert len(evidence["available_screen_geometry"]) == 4
    assert len(evidence["requested_window_size"]) == 2
    assert len(evidence["actual_window_size"]) == 2
    assert len(evidence["effective_minimum_size"]) == 2
    assert evidence["violations"] == []
    window = evidence["window"]
    dock = evidence["dock"]
    panel = evidence["panel"]
    table = evidence["table"]
    assert window["size"][0] > 0 and window["size"][1] > 0
    assert window["client_rect"][2] > 0 and window["client_rect"][3] > 0
    assert dock["object_name"] == "PostAssemblyDock"
    assert dock["visible"] is True
    assert panel["object_name"] == "UnifiedPostAssemblyPanel"
    assert panel["visible"] is True
    assert panel["minimum_size"] == [0, 0]
    assert panel["minimum_size_hint"][0] > 0
    assert panel["minimum_size_hint"][1] > 0
    assert table["column_count"] == 6
    assert table["row_count"] == row_count
    assert table["viewport_geometry"][2] > 0
    assert table["viewport_geometry"][3] > 0
    assert table["header_geometry"][3] > 0
    assert table["header_visible"] is True
    if row_count:
        assert table["visible_row_range"] is not None
        assert table["fully_visible_row_range"] is not None
    assert evidence["footer_actions"]
    assert all(
        action["visible"]
        and action["geometry_in_panel"][2] > 0
        and action["geometry_in_panel"][3] > 0
        and not action["clipped"]
        for action in evidence["footer_actions"]
    )


def test_step_e_native_containment_matrix_and_resizable(tmp_path: Path) -> None:
    _require_native_geometry_qpa()
    from hms_cadcam.ui.i18n import UiLanguage, translation_service

    app = QApplication.instance() or QApplication([])
    service = translation_service()
    original = service.language
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    window.resize(1600, 900)
    try:
        window.show()
        window.post_assembly_action.trigger()
        app.processEvents()
        panel = window.unified_post_assembly_panel
        manager = window._ui_scale_manager
        screen = window.screen() or app.primaryScreen()
        assert screen is not None
        available = screen.availableGeometry()
        frame_delta = window.frameGeometry().size() - window.geometry().size()
        native_available_size = (
            max(1024, available.width() - frame_delta.width() - 8),
            max(680, available.height() - frame_delta.height() - 8),
        )
        cases = (
            ((1600, 900), UiLanguage.EN_US, (), 100),
            ((1366, 768), UiLanguage.EN_US, (), 100),
            ((1280, 720), UiLanguage.EN_US, (), 100),
            (native_available_size, UiLanguage.EN_US, (), 100),
            ((1280, 720), UiLanguage.EN_US, (), 200),
            ((1280, 720), UiLanguage.VI_VN, (), 100),
            ((1280, 720), UiLanguage.KO_KR, (), 100),
            ((1280, 720), UiLanguage.EN_US, _rows(1), 100),
            ((1280, 720), UiLanguage.EN_US, _rows(12), 100),
            ((1280, 720), UiLanguage.EN_US, _rows(30), 50),
        )
        for requested_size, language, rows, scale in cases:
            window.move(available.topLeft())
            window.resize(*requested_size)
            service.set_language(language)
            manager.set_preview_percent(scale)
            panel.set_operation_rows(rows)
            panel.select_operation(rows[0].operation_id if rows else None)
            app.processEvents()
            evidence = _layout_audit(
                window,
                panel,
                capture_id=(
                    f"{requested_size[0]}x{requested_size[1]}_"
                    f"{language.value}_{len(rows)}_{scale}"
                ),
                requested_window_size=requested_size,
            )
            assert evidence["result"] == "PASS"
            assert evidence["layout_stable"] is True
            assert evidence["violations"] == []
            assert rect_inside(
                window.rect(), mapped_rect(window.post_assembly_dock, window)
            )
            assert rect_inside(
                window.rect(), mapped_rect(panel, window)
            )
            assert rect_inside(
                window.rect(), mapped_rect(panel.operation_table, window)
            )
            assert rect_inside(
                window.rect(), mapped_rect(panel.operation_table.viewport(), window)
            )
            assert all(
                rect_inside(window.rect(), mapped_rect(button, window))
                for button in (
                    panel.generate_button,
                    panel.save_managed_button,
                    panel.export_external_button,
                )
            )
            assert rect_inside(window.rect(), mapped_rect(window._ribbon, window))
            assert panel.operation_table.viewport().width() > 0
            assert panel.operation_table.viewport().height() > 0
            assert evidence["footer_accessible"] is True
            assert window.post_assembly_dock.isFloating() is False
            if scale == 200:
                assert not panel.source_operation_picker.isVisible()
                assert all(
                    button.isVisible()
                    for button in (
                        panel.add_button,
                        panel.remove_button,
                        panel.move_up_button,
                        panel.move_down_button,
                        panel.clear_button,
                    )
                )
            else:
                assert panel.source_operation_picker.isVisible()

        window.resize(1366, 768)
        service.set_language(UiLanguage.EN_US)
        manager.set_preview_percent(100)
        panel.set_operation_rows(_rows(12))
        panel.select_operation("op-5")
        app.processEvents()
        _layout_audit(window, panel, capture_id="resizable_before")
        dock = window.post_assembly_dock
        before_width = dock.width()
        window.resizeDocks(
            [dock],
            [before_width + 40],
            Qt.Orientation.Horizontal,
        )
        app.processEvents()
        _layout_audit(window, panel, capture_id="resizable_after")
        assert dock.width() != before_width
        assert rect_inside(window.rect(), mapped_rect(dock, window))
        stable_width = dock.width()
        window._ensure_post_assembly_dock_contained()
        app.processEvents()
        assert dock.width() == stable_width

        manager.set_preview_percent(200)
        window._show_general_settings()
        dialog = window._general_settings_dialog
        assert dialog is not None
        dialog._fit_to_screen()
        app.processEvents()
        settings_evidence = dialog.geometry_evidence()
        assert settings_evidence.contained_in_available_geometry
        assert settings_evidence.footer_accessible
        dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    finally:
        service.set_language(original)
        if window._general_settings_dialog is not None:
            window._general_settings_dialog.close()
        window.close()
        QApplication.processEvents()


def test_geometry_evidence_production_states_and_locales(tmp_path: Path) -> None:
    _require_native_geometry_qpa()
    from hms_cadcam.ui.i18n import UiLanguage, translation_service

    app = QApplication.instance() or QApplication([])
    service = translation_service()
    original = service.language
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    window.resize(1600, 900)
    try:
        window.show()
        window.post_assembly_action.trigger()
        app.processEvents()
        panel = window.unified_post_assembly_panel
        for requested_size in ((1600, 900), (1366, 768), (1280, 720)):
            window.resize(*requested_size)
            app.processEvents()
            for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
                service.set_language(language)
                app.processEvents()
                states = (
                    ("empty", (), None),
                    ("one", _rows(1), None),
                    ("many", _rows(12), None),
                    ("selected", _rows(12), "op-5"),
                )
                for state_name, rows, selected in states:
                    panel.set_operation_rows(rows)
                    panel.select_operation(selected)
                    app.processEvents()
                    capture_id = (
                        f"{requested_size[0]}x{requested_size[1]}_"
                        f"{language.value}_{state_name}"
                    )
                    evidence = _layout_audit(
                        window,
                        panel,
                        capture_id=capture_id,
                        requested_window_size=requested_size,
                    )
                    assert evidence["capture_id"] == capture_id
                    _assert_geometry_evidence(evidence, len(rows))
                    if selected is not None:
                        assert panel.selected_operation_id == selected
        many = _layout_audit(window, panel, capture_id="scrollbar_behavior")
        assert any(many["table"]["scrollbar_state"].values())
        assert not any(
            "SCROLLBAR" in violation["rule_id"]
            or "CONTENT_EXCEEDS" in violation["rule_id"]
            for violation in many["violations"]
        )
    finally:
        service.set_language(original)
        window.close()


def test_geometry_evidence_smaller_valid_layout_is_not_clipped(
    tmp_path: Path,
) -> None:
    _require_native_geometry_qpa()
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    try:
        window.resize(1280, 720)
        window.show()
        window.post_assembly_action.trigger()
        panel = window.unified_post_assembly_panel
        panel.set_operation_rows(_rows(8))
        evidence = _layout_audit(
            window,
            panel,
            capture_id="smaller_valid",
            requested_window_size=(1280, 720),
        )
        assert evidence["window"]["minimum_size"] == [1024, 680]
        assert evidence["window"]["size"] == [1280, 720]
        assert evidence["footer_accessible"] is True
        assert evidence["effective_minimum_size"] != [1280, 900]
        assert evidence["table"]["fully_visible_row_range"] is not None
        assert evidence["clipped_widget_count"] == 0
        assert evidence["overlap_count"] == 0
        assert evidence["result"] == "PASS"
    finally:
        window.close()
        app.processEvents()


def test_geometry_helper_detects_child_outside_parent_bounds() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.setObjectName("BoundsParent")
    parent.resize(100, 80)
    child = QPushButton("Outside", parent)
    child.setObjectName("OutsideChild")
    child.setGeometry(82, 10, 40, 24)
    parent.show()
    app.processEvents()
    try:
        child_bounds = mapped_rect(child, parent)
        assert child_bounds.width() > 0 and child_bounds.height() > 0
        assert not rect_inside(parent.rect(), child_bounds)
    finally:
        parent.close()


def test_geometry_helper_detects_visible_sibling_button_overlap() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.setObjectName("OverlapParent")
    parent.resize(160, 80)
    first = QPushButton("First", parent)
    first.setObjectName("FirstButton")
    first.setGeometry(10, 10, 80, 28)
    second = QPushButton("Second", parent)
    second.setObjectName("SecondButton")
    second.setGeometry(60, 10, 80, 28)
    parent.show()
    app.processEvents()
    try:
        overlaps = visible_sibling_overlaps((first, second), parent)
        assert len(overlaps) == 1
        assert overlaps[0]["first"] == "FirstButton"
        assert overlaps[0]["second"] == "SecondButton"
        assert overlaps[0]["intersection"][2:] == [30, 28]
    finally:
        parent.close()


def test_geometry_evidence_is_runtime_json_without_machine_paths(
    tmp_path: Path,
) -> None:
    _require_native_geometry_qpa()
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    try:
        window.resize(1600, 900)
        window.show()
        window.post_assembly_action.trigger()
        panel = window.unified_post_assembly_panel
        panel.set_operation_rows(_rows(3))
        large = _layout_audit(window, panel, capture_id="runtime_large")
        window.resize(1280, 720)
        app.processEvents()
        small = _layout_audit(
            window,
            panel,
            capture_id="runtime_small",
            requested_window_size=(1280, 720),
        )
        assert large["window"]["size"] == [1600, 900]
        assert small["window"]["size"] == [1280, 720]
        assert large["panel"]["geometry_in_window"] != small["panel"]["geometry_in_window"]
        rendered = json.dumps(small, ensure_ascii=False)
        assert str(tmp_path) not in rendered
        assert "E:\\\\" not in rendered
        assert "C:\\\\" not in rendered
    finally:
        window.close()



@pytest.mark.parametrize(
    "profile",
    ((1920, 1080), (1600, 900), (1366, 768), (1280, 720)),
)
def test_responsive_minimum_profile_logic_never_exceeds_available(
    profile: tuple[int, int],
) -> None:
    available = QRect(0, 0, *profile)
    frame_delta = QSize(8, 31)
    effective = responsive_minimum_size(
        available, QSize(1024, 680), frame_delta
    )
    assert effective.width() <= available.width() - frame_delta.width()
    assert effective.height() <= available.height() - frame_delta.height()
    assert effective != QSize(1280, 900)


@pytest.mark.parametrize("scale", (1.25, 1.50, 1.75))
def test_responsive_minimum_scaled_logical_screen_budget(scale: float) -> None:
    available = QRect(0, 0, int(1920 / scale), int(1032 / scale))
    frame_delta = QSize(8, 31)
    effective = responsive_minimum_size(
        available, QSize(1024, 680), frame_delta
    )
    assert effective.width() + frame_delta.width() <= available.width()
    assert effective.height() + frame_delta.height() <= available.height()


def test_minimum_exceeds_available_negative_fixture_catches_height_blocker() -> None:
    exceeds_width, exceeds_height = minimum_exceeds_available(
        (1280, 900), QRect(0, 0, 1280, 720)
    )
    assert exceeds_width is False
    assert exceeds_height is True


def test_feature_flag_on_off_share_screen_aware_minimum_without_stale_state(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    legacy = _window(tmp_path / "legacy", UiFeatureFlags.for_production())
    unified = _window(tmp_path / "unified", UiFeatureFlags.for_review_harness())
    try:
        legacy.show()
        unified.show()
        app.processEvents()
        screen = unified.screen() or app.primaryScreen()
        assert screen is not None
        available = screen.availableGeometry()
        for window in (legacy, unified):
            minimum = window.minimumSize()
            assert minimum.width() <= available.width()
            assert minimum.height() <= available.height()
        assert legacy.minimumSize() == unified.minimumSize()
        assert unified.minimumSize() != QSize(1280, 900)
        unified.post_assembly_action.trigger()
        app.processEvents()
        assert unified.minimumSize() == legacy.minimumSize()
        assert unified.post_assembly_dock.isVisible()
        assert legacy.post_assembly_dock is legacy.secondary_dock
    finally:
        legacy.close()
        unified.close()
        app.processEvents()


def test_native_available_geometry_minimum_rule_and_footer_access(
    tmp_path: Path,
) -> None:
    _require_native_geometry_qpa()
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, UiFeatureFlags.for_review_harness())
    try:
        window.resize(1366, 768)
        window.show()
        window.post_assembly_action.trigger()
        panel = window.unified_post_assembly_panel
        panel.set_operation_rows(_rows(8))
        panel.select_operation("op-6")
        evidence = _layout_audit(
            window,
            panel,
            capture_id="native_available_geometry",
            requested_window_size=(1366, 768),
        )
        blocker = next(
            check
            for check in evidence["bounds_checks"]
            if check["rule_id"] == "WINDOW_MINIMUM_EXCEEDS_AVAILABLE_SCREEN"
        )
        assert blocker["pass"] is True
        assert evidence["window"]["device_pixel_ratio"] > 0
        assert evidence["footer_accessible"] is True
        assert evidence["table"]["fully_visible_row_range"] is not None
        assert not evidence["overlap_checks"]["table_footer_intersections"]
        assert evidence["result"] == "PASS"
    finally:
        window.close()
        app.processEvents()
