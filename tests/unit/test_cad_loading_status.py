"""Focused Qt-offscreen tests for the Stage 14A WP2 CAD loading surface."""

from __future__ import annotations

import inspect
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
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
from hms_cadcam.ui.main_window import MainWindow
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


def test_main_window_binds_loading_signal_and_cancel_controller_without_modal_wait(
    tmp_path: Path,
) -> None:
    application = _application()
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
