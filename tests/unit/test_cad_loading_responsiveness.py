"""Qt-offscreen integration hardening for Stage 14A WP3 responsiveness."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

import hms_cadcam.ui.cad_controller as cad_controller_module
from hms_cadcam.cad.models import CadFormat
from hms_cadcam.project.workspace import PreparedDocumentOpen
from hms_cadcam.ui.cad_controller import CadUiController
from hms_cadcam.ui.cad_loading import (
    CadLoadError,
    CadLoadErrorCode,
    CadLoadEvent,
    CadLoadOrigin,
    CadLoadState,
    CadLoadingCoordinator,
    cad_format_for_path,
)
from hms_cadcam.ui.cad_loading_status import CadLoadingStatusSurface


class FakeSignal:
    def __init__(self) -> None:
        self.slots: list[object] = []
        self.values: list[tuple[object, ...]] = []

    def connect(self, slot: object) -> None:
        self.slots.append(slot)

    def disconnect(self, slot: object) -> None:
        if slot not in self.slots:
            raise RuntimeError("slot is not connected")
        self.slots.remove(slot)

    def emit(self, *values: object) -> None:
        self.values.append(values)
        for slot in tuple(self.slots):
            slot(*values)


class FakeDelayedImportTask:
    """A submitted native task that deliberately remains unfinished."""

    def __init__(
        self,
        _kernel: object,
        request_id: int,
        source_path: Path,
        cad_format: CadFormat,
    ) -> None:
        self.request_id = request_id
        self.source_path = source_path
        self.cad_format = cad_format
        self.started = False
        self.finished = False
        self.abandoned = 0
        self.signals = SimpleNamespace(
            progress=FakeSignal(),
            completed=FakeSignal(),
            failed=FakeSignal(),
        )

    def abandon(self) -> None:
        self.abandoned += 1


class FakeNonBlockingThreadPool:
    def __init__(self) -> None:
        self.submitted: list[FakeDelayedImportTask] = []
        self.running: list[FakeDelayedImportTask] = []
        self.queued: list[FakeDelayedImportTask] = []
        self.cleared_tasks: list[FakeDelayedImportTask] = []
        self.cleared = 0

    def start(self, task: FakeDelayedImportTask) -> None:
        self.submitted.append(task)
        if len(self.running) < 1:
            task.started = True
            self.running.append(task)
        else:
            self.queued.append(task)

    def clear(self) -> None:
        self.cleared += 1
        self.cleared_tasks.extend(self.queued)
        self.queued.clear()


class FakeAvailableKernel:
    def is_available(self) -> bool:
        return True


class FakeProjectService:
    def __init__(self) -> None:
        self.discarded: list[PreparedDocumentOpen] = []

    def discard_document_open(self, prepared: PreparedDocumentOpen) -> None:
        self.discarded.append(prepared)


class ControllerHarness:
    def __init__(self, project_service: FakeProjectService) -> None:
        self._closing = False
        self._kernel = FakeAvailableKernel()
        self._project_service = project_service
        self._thread_pool = FakeNonBlockingThreadPool()
        self._active_task = None
        self._active_task_source_id = None
        self._active_task_prepared = None
        self._request_generation = 0
        self._active_document_id = None
        self.loading_state_changed = FakeSignal()
        self.progress_changed = FakeSignal()
        self.message = FakeSignal()
        self.busy_changed = FakeSignal()
        self._loading_coordinator = CadLoadingCoordinator(
            self._publish_loading_event
        )
        self.surface = CadLoadingStatusSurface(self.cancel_active_import)
        self.loading_state_changed.connect(self.surface.handle_loading_event)

    def start_import(
        self,
        source_path: Path,
        cad_format: CadFormat,
        *,
        prepared: PreparedDocumentOpen | None = None,
        origin: CadLoadOrigin = CadLoadOrigin.OPEN_DIALOG,
    ) -> None:
        CadUiController.start_import(
            self,
            source_path,
            cad_format,
            prepared=prepared,
            origin=origin,
        )

    def cancel_active_import(self) -> bool:
        return CadUiController.cancel_active_import(self)

    def shutdown(self) -> None:
        CadUiController.shutdown(self)

    def _publish_loading_event(self, event: CadLoadEvent) -> None:
        CadUiController._publish_loading_event(self, event)

    def _invalidate_active_task(self) -> None:
        CadUiController._invalidate_active_task(self)

    def _discard_prepared(self, prepared: PreparedDocumentOpen | None) -> None:
        CadUiController._discard_prepared(self, prepared)

    def _show_progress(self, request_id: int, status: str) -> None:
        CadUiController._show_progress(self, request_id, status)

    def _finish_import(self, request_id: int, result: object) -> None:
        CadUiController._finish_import(self, request_id, result)

    def _import_failed(self, request_id: int, error: object) -> None:
        CadUiController._import_failed(self, request_id, error)

    def _update_action_states(self) -> None:
        return None

    def _clear_active_document(self) -> None:
        return None


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _prepared(name: str) -> PreparedDocumentOpen:
    session = SimpleNamespace(
        geometry_path=Path(name),
        state=SimpleNamespace(identity=uuid4()),
        extraction_root=Path("runtime") / uuid4().hex,
    )
    return PreparedDocumentOpen.for_session(session)


def _events(controller: ControllerHarness) -> list[CadLoadEvent]:
    return [
        values[0]
        for values in controller.loading_state_changed.values
        if values and isinstance(values[0], CadLoadEvent)
    ]


def _dispose(controller: ControllerHarness, application: QApplication) -> None:
    controller.surface.close()
    controller.surface.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pending_import_keeps_event_loop_live_and_cancel_allows_immediate_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cad_controller_module,
        "CadImportTask",
        FakeDelayedImportTask,
    )
    application = _application()
    service = FakeProjectService()
    controller = ControllerHarness(service)
    first_prepared = _prepared("first.HMS/geometry.step")
    timer_ticks: list[str] = []
    QTimer.singleShot(0, lambda: timer_ticks.append("tick"))

    controller.start_import(
        first_prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=first_prepared,
    )
    first_task = controller._thread_pool.submitted[0]

    assert first_task.started and not first_task.finished
    assert controller.surface.status_label.text() == "Đang tải CAD…"
    assert controller.surface.progress_bar.minimum() == 0
    assert controller.surface.progress_bar.maximum() == 0
    assert controller.surface.active_request_id == first_task.request_id
    application.processEvents()
    assert timer_ticks == ["tick"]

    controller.surface.cancel_button.click()
    assert controller._loading_coordinator.active_request is None
    assert first_task.abandoned == 1
    assert service.discarded == [first_prepared]

    second_prepared = _prepared("second.HMS/geometry.step")
    controller.start_import(
        second_prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=second_prepared,
        origin=CadLoadOrigin.DRAG_DROP,
    )

    assert len(controller._thread_pool.submitted) == 2
    assert not first_task.finished
    assert controller._active_task is controller._thread_pool.submitted[1]
    assert not controller._active_task.started
    assert controller.surface.active_request_id == 2
    assert controller.surface.status_label.text() == "Đang tải CAD…"
    _dispose(controller, application)


def test_supersession_rejects_stale_callbacks_and_disposes_only_old_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cad_controller_module,
        "CadImportTask",
        FakeDelayedImportTask,
    )
    application = _application()
    service = FakeProjectService()
    controller = ControllerHarness(service)
    first_prepared = _prepared("a.HMS/a.step")
    second_prepared = _prepared("b.HMS/b.step")

    controller.start_import(
        first_prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=first_prepared,
        origin=CadLoadOrigin.OPEN_DIALOG,
    )
    first_task = controller._active_task
    first_request = _events(controller)[0].request
    assert first_request is not None
    controller.start_import(
        second_prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=second_prepared,
        origin=CadLoadOrigin.DRAG_DROP,
    )
    second_request = controller._loading_coordinator.active_request
    assert second_request is not None

    assert first_task.abandoned == 1
    assert service.discarded == [first_prepared]
    assert controller._active_task_prepared is second_prepared
    assert second_request.origin is CadLoadOrigin.DRAG_DROP
    assert controller.surface.active_request_id == second_request.request_id

    progress_count = len(controller.progress_changed.values)
    controller._show_progress(first_request.request_id, "stale progress")
    controller._import_failed(first_request.request_id, RuntimeError("stale"))
    controller.loading_state_changed.emit(
        CadLoadEvent(
            first_request,
            CadLoadState.FAILED,
            CadLoadError(CadLoadErrorCode.IMPORTER_FAILURE, "stale failure"),
        )
    )

    assert len(controller.progress_changed.values) == progress_count
    assert controller._loading_coordinator.active_request == second_request
    assert controller.surface.active_request_id == second_request.request_id
    assert controller.surface.status_label.text() == "Đang tải CAD…"

    assert controller.cancel_active_import()
    assert service.discarded == [first_prepared, second_prepared]
    _dispose(controller, application)


def test_rapid_supersession_retains_only_latest_controller_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cad_controller_module,
        "CadImportTask",
        FakeDelayedImportTask,
    )
    application = _application()
    service = FakeProjectService()
    controller = ControllerHarness(service)
    prepared = [_prepared(f"rapid-{index}.HMS/part.step") for index in range(32)]

    for index, item in enumerate(prepared):
        controller.start_import(
            item.session.geometry_path,
            CadFormat.STEP,
            prepared=item,
            origin=(
                CadLoadOrigin.OPEN_DIALOG
                if index % 2 == 0
                else CadLoadOrigin.DRAG_DROP
            ),
        )

    request = controller._loading_coordinator.active_request
    assert request is not None and request.request_id == 32
    assert controller._active_task is controller._thread_pool.submitted[-1]
    assert controller._active_task_prepared is prepared[-1]
    assert controller.surface.active_request_id == request.request_id
    assert service.discarded == prepared[:-1]
    assert len(controller._thread_pool.running) == 1
    assert controller._thread_pool.queued == [controller._active_task]
    assert controller._thread_pool.cleared_tasks == controller._thread_pool.submitted[1:-1]
    assert all(task.abandoned == 1 for task in controller._thread_pool.submitted[:-1])
    assert controller._thread_pool.submitted[-1].abandoned == 0
    assert vars(controller._loading_coordinator).keys() == {
        "_publish",
        "_next_request_id",
        "_active",
    }

    assert controller.cancel_active_import()
    assert service.discarded == prepared
    assert controller._thread_pool.queued == []
    _dispose(controller, application)


def test_shutdown_returns_with_pending_task_and_suppresses_late_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cad_controller_module,
        "CadImportTask",
        FakeDelayedImportTask,
    )
    application = _application()
    service = FakeProjectService()
    controller = ControllerHarness(service)
    prepared = _prepared("shutdown.HMS/geometry.step")
    controller.start_import(
        prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=prepared,
    )
    task = controller._active_task
    request = controller._loading_coordinator.active_request
    assert request is not None
    clears_before_shutdown = controller._thread_pool.cleared
    timer_ticks: list[str] = []
    QTimer.singleShot(0, lambda: timer_ticks.append("after-shutdown"))

    controller.shutdown()

    assert controller._closing
    assert controller._loading_coordinator.active_request is None
    assert task.abandoned == 1 and not task.finished
    assert controller._thread_pool.cleared == clears_before_shutdown + 1
    assert service.discarded == [prepared]
    event_count = len(_events(controller))
    progress_count = len(controller.progress_changed.values)
    controller._show_progress(request.request_id, "late")
    controller._import_failed(request.request_id, RuntimeError("late"))
    assert len(_events(controller)) == event_count
    assert len(controller.progress_changed.values) == progress_count
    application.processEvents()
    assert timer_ticks == ["after-shutdown"]

    source = "\n".join(
        inspect.getsource(method)
        for method in (
            CadUiController.start_import,
            CadUiController.cancel_active_import,
            CadUiController.shutdown,
        )
    )
    assert all(
        forbidden not in source
        for forbidden in (".wait(", ".join(", "sleep(", "QEventLoop")
    )
    assert "setMaxThreadCount(1)" in inspect.getsource(CadUiController.__init__)
    assert "self._thread_pool.clear()" in inspect.getsource(
        CadUiController._invalidate_active_task
    )
    _dispose(controller, application)


def test_dialog_drop_and_supported_formats_keep_one_orchestration_contract() -> None:
    assert cad_format_for_path(Path("part.step")) is CadFormat.STEP
    assert cad_format_for_path(Path("part.brep")) is CadFormat.BREP
    assert cad_format_for_path(Path("part.iges")) is CadFormat.IGES
    assert cad_format_for_path(Path("part.stl")) is CadFormat.STL
    assert cad_format_for_path(Path("part.dwg")) is None
