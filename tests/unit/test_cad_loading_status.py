"""Focused Qt-offscreen tests for the Stage 14A WP2 CAD loading surface."""

from __future__ import annotations

import inspect
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.models import CadFormat
from hms_cadcam.ui.cad_loading import (
    CadLoadError,
    CadLoadErrorCode,
    CadLoadEvent,
    CadLoadOrigin,
    CadLoadRequest,
    CadLoadState,
)
from hms_cadcam.ui.cad_loading_status import CadLoadingStatusSurface
from hms_cadcam.ui.i18n import (
    UiLanguage,
    apply_widget_font_tree,
    translation_service,
)
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.settings.ui_scale import UiScaleManager
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from PySide6.QtCore import QSettings


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _request(request_id: int, origin: CadLoadOrigin) -> CadLoadRequest:
    return CadLoadRequest(
        request_id,
        Path(f"part-{request_id}.step"),
        origin,
        CadFormat.STEP,
        f"owner-{request_id}",
    )


def _loading(request_id: int, origin: CadLoadOrigin) -> CadLoadEvent:
    return CadLoadEvent(
        _request(request_id, origin),
        CadLoadState.LOADING,
    )


def _terminal(
    request_id: int,
    state: CadLoadState,
    error: CadLoadError | None = None,
    *,
    origin: CadLoadOrigin = CadLoadOrigin.OPEN_DIALOG,
) -> CadLoadEvent:
    return CadLoadEvent(_request(request_id, origin), state, error)


def _dispose(widget: object, application: QApplication) -> None:
    if hasattr(widget, "close"):
        widget.close()
    application.processEvents()
    if hasattr(widget, "deleteLater"):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _apply_language_and_scale(
    surface: CadLoadingStatusSurface,
    manager: UiScaleManager,
    language: UiLanguage,
    percent: int,
    application: QApplication,
) -> None:
    translation_service().set_language(language)
    apply_widget_font_tree(surface, language, application)
    manager.notify_external_application_font_changed(already_scaled=True)
    manager.set_preview_percent(percent)
    manager.apply_widget_tree(surface)
    application.processEvents()
    application.processEvents()
    surface.adjustSize()
    application.processEvents()


def _assert_scale_safe_geometry(surface: CadLoadingStatusSurface) -> None:
    visible_children = tuple(
        child
        for child in (
            surface.icon_label,
            surface.status_label,
            surface.progress_bar,
            surface.cancel_button,
        )
        if child.isVisible()
    )
    assert visible_children
    assert surface.minimumHeight() <= surface.maximumHeight()
    assert surface.height() == surface.sizeHint().height()
    assert surface.height() >= max(child.height() for child in visible_children)
    assert surface.contentsRect().contains(
        surface.layout().contentsRect()
    )
    for child in visible_children:
        assert child.minimumWidth() <= child.maximumWidth()
        assert child.minimumHeight() <= child.maximumHeight()
        assert surface.contentsRect().contains(child.geometry())


def test_loading_is_immediate_indeterminate_and_has_compact_cancel_surface() -> None:
    application = _application()
    surface = CadLoadingStatusSurface(lambda: True)
    surface.show()
    surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
    application.processEvents()

    assert surface.isVisible()
    assert surface.status_label.text() == "Đang tải CAD…"
    assert surface.progress_bar.isVisible()
    assert surface.progress_bar.minimum() == 0
    assert surface.progress_bar.maximum() == 0
    assert not surface.progress_bar.isTextVisible()
    assert not surface.cancel_button.isHidden()
    assert surface.cancel_button.isEnabled()
    assert surface.maximumHeight() <= 24
    _dispose(surface, application)


def test_cancel_button_calls_controller_once_even_when_clicked_repeatedly() -> None:
    application = _application()
    calls: list[int] = []

    def cancel() -> bool:
        calls.append(1)
        return True

    surface = CadLoadingStatusSurface(cancel)
    surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
    surface.cancel_button.click()
    surface.cancel_button.click()

    assert calls == [1]
    assert not surface.cancel_button.isEnabled()
    _dispose(surface, application)


def test_terminal_states_are_deterministic_and_disable_cancel() -> None:
    application = _application()
    cases = (
        (CadLoadState.SUCCEEDED, None, "Đã tải CAD."),
        (
            CadLoadState.FAILED,
            CadLoadError(
                CadLoadErrorCode.BACKEND_UNAVAILABLE,
                "Backend CAD hiện không khả dụng.",
            ),
            "Backend CAD hiện không khả dụng.",
        ),
        (
            CadLoadState.CANCELLED,
            CadLoadError(CadLoadErrorCode.CANCELLED, "Đã hủy tải CAD."),
            "Đã hủy tải CAD.",
        ),
    )
    for state, error, expected in cases:
        surface = CadLoadingStatusSurface(lambda: True)
        surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
        surface.handle_loading_event(_terminal(1, state, error))

        assert surface.status_label.text() == expected
        assert not surface.progress_bar.isVisible()
        assert not surface.cancel_button.isVisible()
        assert not surface.cancel_button.isEnabled()
        _dispose(surface, application)


def test_stale_terminal_event_cannot_overwrite_newer_request() -> None:
    application = _application()
    surface = CadLoadingStatusSurface(lambda: True)
    surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
    surface.handle_loading_event(_loading(2, CadLoadOrigin.DRAG_DROP))
    surface.handle_loading_event(
        _terminal(
            1,
            CadLoadState.FAILED,
            CadLoadError(CadLoadErrorCode.IMPORTER_FAILURE, "old failure"),
        )
    )

    assert surface.active_request_id == 2
    assert surface.status_label.text() == "Đang tải CAD…"
    assert not surface.cancel_button.isHidden()
    _dispose(surface, application)


def test_dialog_and_drop_requests_share_one_bound_surface() -> None:
    application = _application()
    surface = CadLoadingStatusSurface(lambda: True)
    surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
    first_surface = surface
    surface.handle_loading_event(
        _terminal(1, CadLoadState.SUCCEEDED, origin=CadLoadOrigin.OPEN_DIALOG)
    )
    surface.handle_loading_event(_loading(2, CadLoadOrigin.DRAG_DROP))

    assert surface is first_surface
    assert surface.active_request_id == 2
    assert not surface.cancel_button.isHidden()
    _dispose(surface, application)


def test_loading_surface_retranslates_active_and_terminal_states_accessibly() -> None:
    application = _application()
    service = translation_service()
    with service.using(UiLanguage.VI_VN):
        surface = CadLoadingStatusSurface(lambda: True)
        surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
        with service.using(UiLanguage.EN_US):
            assert surface.status_label.text() == "Loading CAD…"
            assert surface.cancel_button.text() == "Cancel"
            assert surface.status_label.accessibleName() == "CAD loading status"
            assert surface.progress_bar.accessibleName() == "CAD loading progress"
            assert surface.cancel_button.accessibleName() == "Cancel CAD loading"
            assert surface.cancel_button.toolTip() == "Cancel CAD loading"

            surface.handle_loading_event(
                _terminal(1, CadLoadState.SUCCEEDED)
            )
            assert surface.status_label.text() == "CAD loaded."
            assert surface.cancel_button.isHidden()
            assert not surface.cancel_button.isEnabled()
        assert surface.status_label.text() == "Đã tải CAD."
        _dispose(surface, application)


@pytest.mark.parametrize("language", tuple(UiLanguage))
@pytest.mark.parametrize("percent", (100, 150, 200))
@pytest.mark.parametrize(
    "state",
    (
        CadLoadState.LOADING,
        CadLoadState.SUCCEEDED,
        CadLoadState.FAILED,
        CadLoadState.CANCELLED,
    ),
)
def test_loading_surface_locale_scale_geometry_matrix(
    language: UiLanguage,
    percent: int,
    state: CadLoadState,
    tmp_path: Path,
) -> None:
    application = _application()
    original_font = QFont(application.font())
    original_language = translation_service().language
    surface: CadLoadingStatusSurface | None = None
    try:
        baseline_font = QFont(original_font)
        baseline_font.setPointSizeF(9.0)
        application.setFont(baseline_font)
        translation_service().set_language(UiLanguage.VI_VN)
        manager = UiScaleManager(
            QSettings(
                str(tmp_path / "scale.ini"),
                QSettings.Format.IniFormat,
            ),
            application=application,
        )
        surface = CadLoadingStatusSurface(lambda: True)
        surface.show()
        surface.handle_loading_event(_loading(1, CadLoadOrigin.OPEN_DIALOG))
        manager.apply_widget_tree(surface)
        _apply_language_and_scale(
            surface,
            manager,
            language,
            percent,
            application,
        )
        assert surface.active_request_id == 1

        if state is not CadLoadState.LOADING:
            error = (
                CadLoadError(
                    CadLoadErrorCode.IMPORTER_FAILURE,
                    "typed importer failure",
                )
                if state is CadLoadState.FAILED
                else None
            )
            surface.handle_loading_event(_terminal(1, state, error))
            application.processEvents()
            surface.adjustSize()

        _assert_scale_safe_geometry(surface)
        assert surface.status_label.text()
        assert surface.status_label.height() >= QFontMetrics(
            surface.status_label.font()
        ).height()
        if state is CadLoadState.LOADING:
            assert surface.active_request_id == 1
            assert surface.progress_bar.isVisible()
            assert surface.progress_bar.minimum() == 0
            assert surface.progress_bar.maximum() == 0
            assert not surface.progress_bar.isTextVisible()
            assert surface.cancel_button.isVisible()
            assert surface.cancel_button.text()
            assert surface.cancel_button.width() >= surface.cancel_button.sizeHint().width()
            assert surface.cancel_button.height() >= surface.cancel_button.sizeHint().height()
        else:
            assert surface.active_request_id is None
            assert not surface.progress_bar.isVisible()
            assert not surface.cancel_button.isVisible()
            if state is CadLoadState.FAILED:
                assert surface.status_label.text() == "typed importer failure"
    finally:
        if surface is not None:
            _dispose(surface, application)
        translation_service().set_language(original_language)
        application.setFont(original_font)


def test_loading_surface_runtime_locale_and_scale_round_trip_keeps_ownership(
    tmp_path: Path,
) -> None:
    application = _application()
    original_font = QFont(application.font())
    original_language = translation_service().language
    surface: CadLoadingStatusSurface | None = None
    try:
        baseline_font = QFont(original_font)
        baseline_font.setPointSizeF(9.0)
        application.setFont(baseline_font)
        translation_service().set_language(UiLanguage.VI_VN)
        manager = UiScaleManager(
            QSettings(
                str(tmp_path / "round-trip.ini"),
                QSettings.Format.IniFormat,
            ),
            application=application,
        )
        surface = CadLoadingStatusSurface(lambda: True)
        surface.show()
        surface.handle_loading_event(_loading(77, CadLoadOrigin.DRAG_DROP))
        manager.apply_widget_tree(surface)

        for language, percent in (
            (UiLanguage.VI_VN, 100),
            (UiLanguage.EN_US, 150),
            (UiLanguage.KO_KR, 200),
            (UiLanguage.VI_VN, 100),
        ):
            _apply_language_and_scale(
                surface,
                manager,
                language,
                percent,
                application,
            )
            assert surface.active_request_id == 77
            assert surface.progress_bar.isVisible()
            assert surface.cancel_button.width() >= surface.cancel_button.sizeHint().width()
            _assert_scale_safe_geometry(surface)

        surface.handle_loading_event(
            _terminal(
                77,
                CadLoadState.FAILED,
                CadLoadError(
                    CadLoadErrorCode.IMPORTER_FAILURE,
                    "typed failure survives locale changes",
                ),
                origin=CadLoadOrigin.DRAG_DROP,
            )
        )
        for language, percent in (
            (UiLanguage.EN_US, 200),
            (UiLanguage.KO_KR, 100),
        ):
            _apply_language_and_scale(
                surface,
                manager,
                language,
                percent,
                application,
            )
            assert surface.active_request_id is None
            assert surface.status_label.text() == "typed failure survives locale changes"
            _assert_scale_safe_geometry(surface)
    finally:
        if surface is not None:
            _dispose(surface, application)
        translation_service().set_language(original_language)
        application.setFont(original_font)


def test_main_window_binds_loading_signal_and_cancel_controller_without_modal_wait(
    tmp_path: Path,
) -> None:
    application = _application()
    service = translation_service()
    with service.using(UiLanguage.VI_VN):
        window = MainWindow(
            ProjectService.create_default(tmp_path / "config"),
            UnavailableCadKernel("WP2 test"),
            UnavailableCadViewportBackend("WP2 test"),
            layout_store=WorkspaceLayoutStore(
                QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
            ),
        )
        events: list[CadLoadEvent] = []
        window.cad_controller.loading_state_changed.connect(events.append)
        request, _ = window.cad_controller._loading_coordinator.begin(
            Path("dialog.step"),
            CadLoadOrigin.OPEN_DIALOG,
            CadFormat.STEP,
            owner_identity="wp2",
        )
        application.processEvents()

        assert window._cad_loading_status.active_request_id == request.request_id
        assert not window._cad_loading_status.cancel_button.isHidden()
        with service.using(UiLanguage.EN_US):
            assert window._cad_loading_status.status_label.text() == "Loading CAD…"
            assert window._cad_loading_status.cancel_button.text() == "Cancel"
        QTest.mouseClick(window._cad_loading_status.cancel_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(window._cad_loading_status.cancel_button, Qt.MouseButton.LeftButton)
        application.processEvents()

        assert [event.state for event in events] == [
            CadLoadState.LOADING,
            CadLoadState.CANCELLED,
        ]
        assert window._cad_loading_status.status_label.text() == "Đã hủy tải CAD."
        source = inspect.getsource(CadLoadingStatusSurface)
        assert "QMessageBox" not in source
        assert ".wait(" not in source
        _dispose(window, application)
