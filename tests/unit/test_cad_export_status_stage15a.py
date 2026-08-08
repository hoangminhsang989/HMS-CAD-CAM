"""Stage15A WP3 export status ownership, i18n, and scale tests."""

from __future__ import annotations

import inspect

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from hms_cadcam.cad.export_models import ExportFormatId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cad_export_status import (
    CadExportStatusSurface,
    ExportOperationEvent,
    ExportOperationState,
)
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


def _event(
    request_id: int,
    state: ExportOperationState,
    format_id: ExportFormatId = ExportFormatId.STEP,
) -> ExportOperationEvent:
    return ExportOperationEvent(request_id, state, format_id)


def _show_active(qtbot, cancel=lambda: True) -> CadExportStatusSurface:
    surface = CadExportStatusSurface(cancel)
    qtbot.addWidget(surface)
    surface.handle_export_event(_event(1, ExportOperationState.ACTIVE))
    surface.adjustSize()
    surface.show()
    qtbot.waitUntil(surface.isVisible)
    return surface


def _assert_geometry(surface: CadExportStatusSurface) -> None:
    surface.adjustSize()
    contents = surface.contentsRect()
    for widget in (
        surface.icon_label,
        surface.status_label,
        surface.progress_bar,
        surface.cancel_button,
    ):
        if widget.isVisible():
            assert contents.contains(widget.geometry())
    assert surface.cancel_button.width() >= surface.cancel_button.sizeHint().width()


def _production_window(qtbot, tmp_path) -> MainWindow:
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("WP3 status test"),
        UnavailableCadViewportBackend("WP3 status test"),
        layout_store=WorkspaceLayoutStore(
            QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
        ),
    )
    qtbot.addWidget(window)
    window.resize(1500, 900)
    window.show()
    qtbot.waitUntil(window.isVisible)
    return window


def _process_geometry_events() -> None:
    for _index in range(3):
        QApplication.sendPostedEvents()
        QApplication.processEvents()


def _visible_status_neighbors(window: MainWindow) -> tuple[QWidget, ...]:
    status = window.statusBar()
    surface = window._cad_export_status
    return tuple(
        widget
        for widget in status.findChildren(
            QWidget,
            options=Qt.FindChildOption.FindDirectChildrenOnly,
        )
        if widget is not surface and widget.isVisible()
    )


def test_active_surface_is_immediate_indeterminate_and_format_specific(qtbot) -> None:
    surface = _show_active(qtbot)
    assert surface.active_request_id == 1
    assert surface.last_event == _event(1, ExportOperationState.ACTIVE)
    assert surface.progress_bar.minimum() == 0
    assert surface.progress_bar.maximum() == 0
    assert surface.progress_bar.isVisible()
    assert surface.cancel_button.isVisible()
    assert surface.cancel_button.isEnabled()
    assert surface.status_label.text().startswith("STEP — ")


def test_cancel_click_is_non_modal_and_idempotently_disabled(qtbot) -> None:
    calls: list[bool] = []
    surface = _show_active(qtbot, lambda: calls.append(True) or True)
    QTest.mouseClick(surface.cancel_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(surface.cancel_button, Qt.MouseButton.LeftButton)
    assert calls == [True]
    assert not surface.cancel_button.isEnabled()
    source = inspect.getsource(CadExportStatusSurface)
    assert "QMessageBox" not in source
    assert ".wait(" not in source
    assert "sleep(" not in source


def test_cancelling_keeps_activity_and_disables_repeated_cancel(qtbot) -> None:
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, ExportOperationState.CANCELLING))
    assert surface.active_request_id == 1
    assert surface.progress_bar.isVisible()
    assert surface.cancel_button.isVisible()
    assert not surface.cancel_button.isEnabled()
    assert (
        translation_service().translate("Cancelling 3D export…")
        in surface.status_label.text()
    )


def test_commit_started_hides_cancel_without_claiming_cancellation(qtbot) -> None:
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, ExportOperationState.COMMITTING))
    assert surface.active_request_id == 1
    assert surface.progress_bar.isVisible()
    assert surface.cancel_button.isHidden()
    assert translation_service().translate(
        "Cannot cancel because the file is being finalized"
    ) in surface.status_label.text()


@pytest.mark.parametrize(
    "state",
    (
        ExportOperationState.SUCCEEDED,
        ExportOperationState.CANCELLED,
        ExportOperationState.FAILED,
    ),
)
def test_terminal_surface_is_deterministic_and_releases_request_owner(
    qtbot, state: ExportOperationState
) -> None:
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, state))
    assert surface.active_request_id is None
    assert surface.last_event == _event(1, state)
    assert surface.progress_bar.isHidden()
    assert surface.cancel_button.isHidden()
    assert surface.isVisible()


def test_stale_terminal_and_old_cancel_cannot_overwrite_newer_request(qtbot) -> None:
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, ExportOperationState.SUCCEEDED))
    surface.handle_export_event(
        _event(2, ExportOperationState.ACTIVE, ExportFormatId.IGES)
    )
    expected = surface.status_label.text()
    surface.handle_export_event(_event(1, ExportOperationState.CANCELLED))
    surface.handle_export_event(_event(1, ExportOperationState.FAILED))
    assert surface.active_request_id == 2
    assert surface.last_event == _event(
        2, ExportOperationState.ACTIVE, ExportFormatId.IGES
    )
    assert surface.status_label.text() == expected
    assert surface.status_label.text().startswith("IGES — ")


def test_abandoned_owner_clears_surface_without_stale_terminal_text(qtbot) -> None:
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, ExportOperationState.ABANDONED))
    assert surface.active_request_id is None
    assert surface.last_event is None
    assert surface.isHidden()


@pytest.mark.parametrize("language", tuple(UiLanguage))
@pytest.mark.parametrize("scale", (1.0, 1.25, 1.5, 2.0))
def test_active_locale_scale_matrix_preserves_owner_without_clipping(
    qtbot, language: UiLanguage, scale: float
) -> None:
    translator = translation_service()
    previous = translator.language
    surface = _show_active(qtbot)
    try:
        translator.set_language(language)
        font = QFont(surface.font())
        point_size = font.pointSizeF() if font.pointSizeF() > 0 else 9.0
        font.setPointSizeF(point_size * scale)
        surface.setFont(font)
        surface.retranslate_ui(language)
        surface.adjustSize()
        QApplication.processEvents()
        assert surface.active_request_id == 1
        assert surface.progress_bar.isVisible()
        assert surface.cancel_button.isVisible()
        assert surface.cancel_button.isEnabled()
        _assert_geometry(surface)
    finally:
        translator.set_language(previous)


def test_runtime_locale_scale_change_preserves_cancelling_state(qtbot) -> None:
    translator = translation_service()
    previous = translator.language
    surface = _show_active(qtbot)
    surface.handle_export_event(_event(1, ExportOperationState.CANCELLING))
    try:
        for language, scale in (
            (UiLanguage.VI_VN, 1.0),
            (UiLanguage.EN_US, 1.25),
            (UiLanguage.KO_KR, 1.5),
            (UiLanguage.VI_VN, 2.0),
        ):
            translator.set_language(language)
            font = QFont(surface.font())
            font.setPointSizeF(9.0 * scale)
            surface.setFont(font)
            surface.retranslate_ui(language)
            assert surface.active_request_id == 1
            assert surface.last_event == _event(1, ExportOperationState.CANCELLING)
            assert surface.progress_bar.isVisible()
            assert not surface.cancel_button.isEnabled()
            _assert_geometry(surface)
    finally:
        translator.set_language(previous)


def test_main_window_binds_export_surface_without_status_bar_collision(
    qtbot, tmp_path
) -> None:
    window = _production_window(qtbot, tmp_path)
    event = _event(1, ExportOperationState.ACTIVE, ExportFormatId.BREP)
    window.export_controller.operation_state_changed.emit(event)
    font = QFont(window.font())
    point_size = font.pointSizeF() if font.pointSizeF() > 0 else 9.0
    font.setPointSizeF(point_size * 2.0)
    window.setFont(font)
    window.resize(1500, 900)
    window._cad_export_status.retranslate_ui()
    assert window._cad_export_status.active_request_id == 1
    assert window._cad_export_status.progress_bar.isVisible()
    assert (
        window._cad_loading_status.isHidden()
        or not window._cad_export_status.geometry().intersects(
            window._cad_loading_status.geometry()
        )
    )
    assert window._cad_export_status.cancel_button.width() >= (
        window._cad_export_status.cancel_button.sizeHint().width()
    )


def test_production_main_window_export_status_geometry_matrix(
    qtbot, tmp_path
) -> None:
    """Cover 3 locales x 4 scales x 6 states in the real status bar."""

    window = _production_window(qtbot, tmp_path)
    translator = translation_service()
    previous_language = translator.language
    previous_scale = window._ui_scale_manager.current_percent
    states = (
        ExportOperationState.ACTIVE,
        ExportOperationState.CANCELLING,
        ExportOperationState.COMMITTING,
        ExportOperationState.SUCCEEDED,
        ExportOperationState.CANCELLED,
        ExportOperationState.FAILED,
    )
    active_states = {
        ExportOperationState.ACTIVE,
        ExportOperationState.CANCELLING,
        ExportOperationState.COMMITTING,
    }
    matrix_cases = 0
    request_id = 100
    mandatory_committing_checked = False
    try:
        for language in (
            UiLanguage.VI_VN,
            UiLanguage.EN_US,
            UiLanguage.KO_KR,
        ):
            translator.set_language(language)
            for scale_percent in (100, 125, 150, 200):
                window._ui_scale_manager.set_preview_percent(scale_percent)
                window.resize(1500, 900)
                for state in states:
                    request_id += 1
                    active = _event(
                        request_id,
                        ExportOperationState.ACTIVE,
                    )
                    window.export_controller.operation_state_changed.emit(active)
                    if state is not ExportOperationState.ACTIVE:
                        window.export_controller.operation_state_changed.emit(
                            _event(request_id, state)
                        )
                    _process_geometry_events()

                    surface = window._cad_export_status
                    status_bar = window.statusBar()
                    assert window.size().width() == 1500
                    assert window.size().height() == 900
                    assert surface.isVisible()
                    assert surface.status_label.width() >= (
                        surface.status_label.sizeHint().width()
                    )
                    assert surface.contentsRect().contains(
                        surface.status_label.geometry()
                    )
                    assert status_bar.contentsRect().contains(surface.geometry()), (
                        language,
                        scale_percent,
                        state,
                        status_bar.contentsRect(),
                        surface.geometry(),
                    )
                    for neighbor in _visible_status_neighbors(window):
                        assert not surface.geometry().intersects(
                            neighbor.geometry()
                        )

                    progress_expected = state in active_states
                    cancel_visible = state in {
                        ExportOperationState.ACTIVE,
                        ExportOperationState.CANCELLING,
                    }
                    assert surface.progress_bar.isVisible() is progress_expected
                    assert surface.cancel_button.isVisible() is cancel_visible
                    assert surface.cancel_button.isEnabled() is (
                        state is ExportOperationState.ACTIVE
                    )
                    if state in active_states:
                        assert surface.active_request_id == request_id
                    else:
                        assert surface.active_request_id is None
                    assert surface.last_event == _event(request_id, state)

                    if (
                        language is UiLanguage.EN_US
                        and scale_percent == 200
                        and state is ExportOperationState.COMMITTING
                    ):
                        assert surface.status_label.text().endswith(
                            "Cannot cancel because the file is being finalized"
                        )
                        mandatory_committing_checked = True
                    matrix_cases += 1
        assert matrix_cases == 72
        assert mandatory_committing_checked
    finally:
        window._ui_scale_manager.set_preview_percent(previous_scale)
        translator.set_language(previous_language)


def test_production_status_preserves_state_through_locale_and_scale(
    qtbot, tmp_path
) -> None:
    window = _production_window(qtbot, tmp_path)
    translator = translation_service()
    previous_language = translator.language
    previous_scale = window._ui_scale_manager.current_percent
    transitions = (
        (201, ExportOperationState.ACTIVE),
        (202, ExportOperationState.CANCELLING),
        (203, ExportOperationState.COMMITTING),
    )
    try:
        for request_id, state in transitions:
            window.export_controller.operation_state_changed.emit(
                _event(request_id, ExportOperationState.ACTIVE)
            )
            if state is not ExportOperationState.ACTIVE:
                window.export_controller.operation_state_changed.emit(
                    _event(request_id, state)
                )
            for language, scale_percent in (
                (UiLanguage.VI_VN, 100),
                (UiLanguage.EN_US, 125),
                (UiLanguage.KO_KR, 150),
                (UiLanguage.EN_US, 200),
            ):
                translator.set_language(language)
                window._ui_scale_manager.set_preview_percent(scale_percent)
                window.resize(1500, 900)
                _process_geometry_events()
                surface = window._cad_export_status
                assert surface.active_request_id == request_id
                assert surface.last_event == _event(request_id, state)
                assert surface.status_label.width() >= (
                    surface.status_label.sizeHint().width()
                )
                if state is ExportOperationState.ACTIVE:
                    assert surface.cancel_button.isEnabled()
                elif state is ExportOperationState.CANCELLING:
                    assert surface.cancel_button.isVisible()
                    assert not surface.cancel_button.isEnabled()
                else:
                    assert surface.cancel_button.isHidden()
                    assert not surface.cancel_button.isEnabled()
    finally:
        window._ui_scale_manager.set_preview_percent(previous_scale)
        translator.set_language(previous_language)
