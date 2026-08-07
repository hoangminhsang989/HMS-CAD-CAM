"""Focused Stage 14A WP1 request-owned CAD loading lifecycle tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import hms_cadcam.ui.cad_controller as cad_controller_module
from hms_cadcam.cad.models import CadFormat
from hms_cadcam.project.workspace import DocumentOpenOrigin, PreparedDocumentOpen
from hms_cadcam.ui.cad_controller import CadUiController
from hms_cadcam.ui.cad_loading import (
    CadLoadErrorCode,
    CadLoadOrigin,
    CadLoadState,
    CadLoadingCoordinator,
    cad_format_for_path,
    normalize_import_error,
)
from hms_cadcam.ui.project_controller import ProjectUiController


@dataclass
class FakeWorker:
    """A non-native worker surrogate that records safe late-result disposal."""

    request_id: int
    released: int = 0

    def abandon(self) -> None:
        self.released += 1


class FakeSignal:
    def __init__(self) -> None:
        self._slots: list[object] = []

    def connect(self, slot: object) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: object) -> None:
        if slot not in self._slots:
            raise RuntimeError("slot is not connected")
        self._slots.remove(slot)


class FakeImportTask:
    def __init__(
        self,
        _kernel: object,
        request_id: int,
        _source: Path,
        _cad_format: CadFormat,
    ) -> None:
        self.request_id = request_id
        self.abandoned = 0
        self.signals = SimpleNamespace(
            progress=FakeSignal(),
            completed=FakeSignal(),
            failed=FakeSignal(),
        )

    def abandon(self) -> None:
        self.abandoned += 1


class FakeThreadPool:
    def __init__(self) -> None:
        self.started: list[FakeImportTask] = []
        self.cleared = 0

    def start(self, task: FakeImportTask) -> None:
        self.started.append(task)

    def clear(self) -> None:
        self.cleared += 1


class FakeCadKernel:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available


class SignalRecorder:
    def __init__(self) -> None:
        self.values: list[object] = []

    def emit(self, value: object) -> None:
        self.values.append(value)


class FakeProjectService:
    def __init__(self) -> None:
        self.discarded: list[PreparedDocumentOpen] = []

    def prepare_document_open(self, path: Path) -> PreparedDocumentOpen:
        session = SimpleNamespace(
            geometry_path=path,
            state=SimpleNamespace(identity=uuid4()),
            extraction_root=Path("runtime") / uuid4().hex,
        )
        return PreparedDocumentOpen.for_session(session)

    def discard_document_open(self, prepared: PreparedDocumentOpen) -> None:
        self.discarded.append(prepared)


class FakeProjectControllerHarness:
    def __init__(self) -> None:
        self.is_busy = False
        self.is_autosaving = False
        self._service = FakeProjectService()
        self.operations = []

    def _can_change_project(self) -> bool:
        return True

    def _prepare_workspace_replacement(self) -> bool:
        return True

    def _start_operation(self, operation) -> None:
        self.operations.append(operation)


class FakeCadControllerHarness:
    def __init__(self, project: FakeProjectControllerHarness) -> None:
        self._open_command = lambda path, origin: ProjectUiController.request_open_path(
            project, path, origin
        )
        self._loading_coordinator = CadLoadingCoordinator(lambda _event: None)
        self.message = SignalRecorder()
        self.import_requests = []

    def open_path(
        self,
        path: Path,
        *,
        origin: CadLoadOrigin = CadLoadOrigin.OPEN_DIALOG,
    ) -> bool:
        return CadUiController.open_path(self, path, origin=origin)

    def start_import(
        self,
        source_path: Path,
        cad_format: CadFormat,
        *,
        source_id,
        prepared: PreparedDocumentOpen,
        origin: CadLoadOrigin,
    ) -> None:
        request, _superseded = self._loading_coordinator.begin(
            source_path,
            origin,
            cad_format,
            owner_identity=str(source_id),
        )
        self.import_requests.append((request, prepared))


class FakeProgressControllerHarness:
    def __init__(self, coordinator: CadLoadingCoordinator) -> None:
        self._closing = False
        self._loading_coordinator = coordinator
        self.progress_changed = SignalRecorder()
        self.invalidations = 0

    def _invalidate_active_task(self) -> None:
        self.invalidations += 1

    def _update_action_states(self) -> None:
        return None


class FakeLifecycleCadControllerHarness:
    def __init__(self, project_service: FakeProjectService, *, backend_available: bool) -> None:
        self._closing = False
        self._kernel = FakeCadKernel(backend_available)
        self._project_service = project_service
        self._thread_pool = FakeThreadPool()
        self._loading_coordinator = CadLoadingCoordinator(lambda _event: None)
        self._active_task = None
        self._active_task_source_id = None
        self._active_task_prepared = None
        self._request_generation = 0
        self._active_document_id = None
        self.progress_changed = SignalRecorder()
        self.message = SignalRecorder()
        self.busy_changed = SignalRecorder()

    def _update_action_states(self) -> None:
        return None

    def _clear_active_document(self) -> None:
        return None

    def _publish_loading_event(self, _event: object) -> None:
        return None

    def _discard_prepared(self, prepared: PreparedDocumentOpen | None) -> None:
        CadUiController._discard_prepared(self, prepared)

    def _invalidate_active_task(self) -> None:
        CadUiController._invalidate_active_task(self)

    def _show_progress(self, _request_id: int, _status: str) -> None:
        return None

    def _finish_import(self, _request_id: int, _result: object) -> None:
        return None

    def _import_failed(self, _request_id: int, _error: object) -> None:
        return None


def _prepared(service: FakeProjectService, name: str) -> PreparedDocumentOpen:
    return service.prepare_document_open(Path(name))


def _coordinator():
    events = []
    return CadLoadingCoordinator(events.append), events


def test_valid_request_publishes_loading_before_fake_worker_completion() -> None:
    coordinator, events = _coordinator()

    request, superseded = coordinator.begin(
        Path("part.step"), CadLoadOrigin.OPEN_DIALOG, CadFormat.STEP,
        owner_identity="transient",
    )
    worker = FakeWorker(request.request_id)

    assert superseded is None
    assert events == [events[0]]
    assert events[0].state is CadLoadState.LOADING
    assert events[0].request == request
    assert worker.released == 0
    assert coordinator.succeed(worker.request_id)
    assert [event.state for event in events] == [CadLoadState.LOADING, CadLoadState.SUCCEEDED]


def test_open_dialog_and_drag_drop_share_request_coordinator_with_stable_distinct_ids() -> None:
    coordinator, events = _coordinator()

    first, _ = coordinator.begin(
        Path("first.step"), CadLoadOrigin.OPEN_DIALOG, CadFormat.STEP,
        owner_identity="document-a",
    )
    second, superseded = coordinator.begin(
        Path("second.stl"), CadLoadOrigin.DRAG_DROP, CadFormat.STL,
        owner_identity="document-b",
    )

    assert first.request_id != second.request_id
    assert first.request_id == 1
    assert second.request_id == 2
    assert first.origin is CadLoadOrigin.OPEN_DIALOG
    assert second.origin is CadLoadOrigin.DRAG_DROP
    assert superseded == first
    assert [event.request for event in events] == [first, second]


def test_superseded_worker_progress_success_and_failure_are_stale_and_result_is_released() -> None:
    coordinator, events = _coordinator()
    first, _ = coordinator.begin(
        Path("slow.step"), CadLoadOrigin.OPEN_DIALOG, CadFormat.STEP,
        owner_identity="first",
    )
    stale_worker = FakeWorker(first.request_id)
    second, superseded = coordinator.begin(
        Path("current.brep"), CadLoadOrigin.DRAG_DROP, CadFormat.BREP,
        owner_identity="second",
    )

    assert superseded == first
    stale_worker.abandon()
    assert not coordinator.succeed(first.request_id)
    assert not coordinator.fail(first, normalize_import_error(RuntimeError("late")))
    assert stale_worker.released == 1
    assert coordinator.active_request == second
    assert coordinator.active_request.origin is CadLoadOrigin.DRAG_DROP
    assert [event.request for event in events] == [first, second]


def test_active_success_is_published_exactly_once() -> None:
    coordinator, events = _coordinator()
    request, _ = coordinator.begin(
        Path("part.iges"), CadLoadOrigin.OPEN_DIALOG, CadFormat.IGES,
        owner_identity="only",
    )

    assert coordinator.succeed(request.request_id)
    assert not coordinator.succeed(request.request_id)
    assert [event.state for event in events] == [CadLoadState.LOADING, CadLoadState.SUCCEEDED]


def test_cancelled_controller_ignores_progress_already_queued_for_request() -> None:
    coordinator, _events = _coordinator()
    request, _ = coordinator.begin(
        Path("queued.step"),
        CadLoadOrigin.OPEN_DIALOG,
        CadFormat.STEP,
        owner_identity="queued",
    )
    controller = FakeProgressControllerHarness(coordinator)

    CadUiController._show_progress(controller, request.request_id, "before cancel")
    assert CadUiController.cancel_active_import(controller)
    CadUiController._show_progress(controller, request.request_id, "late progress")

    assert controller.progress_changed.values == ["before cancel"]
    assert controller.invalidations == 1


def test_prepared_open_carries_each_origin_immutably_for_same_path() -> None:
    project = FakeProjectControllerHarness()
    cad = FakeCadControllerHarness(project)
    source = Path("same.step")

    assert cad.open_path(source, origin=CadLoadOrigin.OPEN_DIALOG)
    assert CadUiController.open_dropped_path(cad, source)
    assert len(project.operations) == 2

    open_prepared = project.operations[0]()
    drop_prepared = project.operations[1]()
    assert open_prepared.request_id != drop_prepared.request_id
    assert open_prepared.origin is DocumentOpenOrigin.OPEN_DIALOG
    assert drop_prepared.origin is DocumentOpenOrigin.DRAG_DROP
    with pytest.raises(FrozenInstanceError):
        drop_prepared.origin = DocumentOpenOrigin.OPEN_DIALOG

    CadUiController.open_prepared_document(cad, drop_prepared)
    drop_request = cad.import_requests[-1][0]
    CadUiController.open_prepared_document(cad, open_prepared)
    open_request = cad.import_requests[-1][0]

    assert drop_request.origin is CadLoadOrigin.DRAG_DROP
    assert open_request.origin is CadLoadOrigin.OPEN_DIALOG
    assert drop_request.source_path == open_request.source_path == source


def test_cancel_active_prepared_document_is_discarded_once_and_late_failure_is_ignored() -> None:
    service = FakeProjectService()
    controller = FakeLifecycleCadControllerHarness(service, backend_available=True)
    prepared = _prepared(service, "cancelled.HMS/geometry.step")
    request, _ = controller._loading_coordinator.begin(
        prepared.session.geometry_path,
        CadLoadOrigin.OPEN_DIALOG,
        CadFormat.STEP,
        owner_identity="cancelled",
    )
    task = FakeImportTask(
        controller._kernel,
        request.request_id,
        prepared.session.geometry_path,
        CadFormat.STEP,
    )
    controller._active_task = task
    controller._active_task_prepared = prepared

    assert CadUiController.cancel_active_import(controller)
    CadUiController._import_failed(controller, request.request_id, RuntimeError("late"))

    assert task.abandoned == 1
    assert service.discarded == [prepared]
    assert prepared.session.extraction_root is not None


def test_superseding_import_discards_old_prepared_but_retains_new_until_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cad_controller_module, "CadImportTask", FakeImportTask)
    service = FakeProjectService()
    controller = FakeLifecycleCadControllerHarness(service, backend_available=True)
    first = _prepared(service, "first.HMS/geometry.step")
    second = _prepared(service, "second.HMS/geometry.step")

    CadUiController.start_import(
        controller,
        first.session.geometry_path,
        CadFormat.STEP,
        prepared=first,
    )
    first_task = controller._thread_pool.started[0]
    CadUiController.start_import(
        controller,
        second.session.geometry_path,
        CadFormat.STEP,
        prepared=second,
    )

    assert first_task.abandoned == 1
    assert service.discarded == [first]
    assert controller._active_task_prepared is second

    CadUiController._invalidate_active_task(controller)
    assert service.discarded == [first, second]


def test_bind_project_invalidation_discards_active_prepared_document() -> None:
    service = FakeProjectService()
    controller = FakeLifecycleCadControllerHarness(service, backend_available=True)
    prepared = _prepared(service, "bound.HMS/geometry.step")
    request, _ = controller._loading_coordinator.begin(
        prepared.session.geometry_path,
        CadLoadOrigin.OPEN_DIALOG,
        CadFormat.STEP,
        owner_identity="bound",
    )
    task = FakeImportTask(
        controller._kernel,
        request.request_id,
        prepared.session.geometry_path,
        CadFormat.STEP,
    )
    controller._active_task = task
    controller._active_task_prepared = prepared

    CadUiController.bind_project(controller, None)

    assert task.abandoned == 1
    assert service.discarded == [prepared]
    assert controller._active_task_prepared is None


def test_shutdown_discards_active_prepared_document_without_waiting_for_worker() -> None:
    service = FakeProjectService()
    controller = FakeLifecycleCadControllerHarness(service, backend_available=True)
    prepared = _prepared(service, "shutdown.HMS/geometry.step")
    request, _ = controller._loading_coordinator.begin(
        prepared.session.geometry_path,
        CadLoadOrigin.DRAG_DROP,
        CadFormat.STEP,
        owner_identity="shutdown",
    )
    task = FakeImportTask(
        controller._kernel,
        request.request_id,
        prepared.session.geometry_path,
        CadFormat.STEP,
    )
    controller._active_task = task
    controller._active_task_prepared = prepared

    CadUiController.shutdown(controller)

    assert task.abandoned == 1
    assert controller._thread_pool.cleared == 1
    assert service.discarded == [prepared]
    assert controller._active_task_prepared is None


def test_backend_unavailable_discards_prepared_before_worker_start() -> None:
    service = FakeProjectService()
    controller = FakeLifecycleCadControllerHarness(service, backend_available=False)
    prepared = _prepared(service, "offline.HMS/geometry.step")

    CadUiController.start_import(
        controller,
        prepared.session.geometry_path,
        CadFormat.STEP,
        prepared=prepared,
    )

    assert service.discarded == [prepared]
    assert controller._thread_pool.started == []
    assert controller._active_task is None
    assert controller._active_task_prepared is None


def test_many_terminal_requests_do_not_accumulate_terminal_identity_registry() -> None:
    coordinator = CadLoadingCoordinator(lambda _event: None)

    for index in range(1_000):
        request, _ = coordinator.begin(
            Path(f"part-{index}.step"),
            CadLoadOrigin.OPEN_DIALOG,
            CadFormat.STEP,
            owner_identity=str(index),
        )
        assert coordinator.succeed(request.request_id)

    assert coordinator.active_request is None
    assert vars(coordinator).keys() == {"_publish", "_next_request_id", "_active"}


def test_cancellation_publishes_once_and_late_completion_is_ignored_and_released() -> None:
    coordinator, events = _coordinator()
    request, _ = coordinator.begin(
        Path("cancel.step"), CadLoadOrigin.OPEN_DIALOG, CadFormat.STEP,
        owner_identity="cancelled",
    )
    worker = FakeWorker(request.request_id)

    assert coordinator.cancel_active() == request
    assert coordinator.cancel_active() is None
    assert not coordinator.succeed(request.request_id)
    worker.abandon()
    assert worker.released == 1
    assert [event.state for event in events] == [CadLoadState.LOADING, CadLoadState.CANCELLED]
    assert events[-1].error is not None
    assert events[-1].error.code is CadLoadErrorCode.CANCELLED
    assert events[-1].error.message == "Đã hủy tải CAD."


def test_shutdown_abandons_public_ownership_without_waiting_for_fake_worker() -> None:
    coordinator, events = _coordinator()
    request, _ = coordinator.begin(
        Path("shutdown.stl"), CadLoadOrigin.DRAG_DROP, CadFormat.STL,
        owner_identity="shutdown",
    )
    worker = FakeWorker(request.request_id)

    assert coordinator.abandon_active() == request
    worker.abandon()
    assert coordinator.active_request is None
    assert not coordinator.succeed(request.request_id)
    assert worker.released == 1
    assert [event.state for event in events] == [CadLoadState.LOADING]


def test_unsupported_extension_fails_before_any_fake_worker_launch() -> None:
    coordinator, events = _coordinator()

    error = coordinator.reject_unsupported(Path("unsupported.cadinput"))

    assert coordinator.active_request is None
    assert error.code is CadLoadErrorCode.UNSUPPORTED_FORMAT
    assert error.message == "Định dạng CAD chưa được hỗ trợ."
    assert events[-1].state is CadLoadState.FAILED
    assert events[-1].request is None
    assert events[-1].error == error


def test_backend_unavailable_is_a_typed_recoverable_failure() -> None:
    coordinator, events = _coordinator()

    request = coordinator.reject_backend_unavailable(
        Path("offline.step"), CadLoadOrigin.OPEN_DIALOG, CadFormat.STEP,
        owner_identity="offline",
    )

    assert request.request_id == 1
    assert coordinator.active_request is None
    assert [event.state for event in events] == [CadLoadState.LOADING, CadLoadState.FAILED]
    assert events[-1].error is not None
    assert events[-1].error.code is CadLoadErrorCode.BACKEND_UNAVAILABLE
    assert events[-1].error.message == "Backend CAD hiện không khả dụng."


def test_import_errors_are_normalized_to_unreadable_cancelled_or_unexpected() -> None:
    assert normalize_import_error(FileNotFoundError("missing")).code is CadLoadErrorCode.UNREADABLE_INPUT
    assert normalize_import_error(PermissionError("denied")).code is CadLoadErrorCode.UNREADABLE_INPUT
    assert normalize_import_error(InterruptedError("cancelled")).code is CadLoadErrorCode.CANCELLED
    assert normalize_import_error(RuntimeError("broken reader")).code is CadLoadErrorCode.IMPORTER_FAILURE


def test_supported_format_routing_claims_only_existing_backends() -> None:
    assert cad_format_for_path(Path("model.step")) is CadFormat.STEP
    assert cad_format_for_path(Path("model.stp")) is CadFormat.STEP
    assert cad_format_for_path(Path("model.brep")) is CadFormat.BREP
    assert cad_format_for_path(Path("model.igs")) is CadFormat.IGES
    assert cad_format_for_path(Path("model.stl")) is CadFormat.STL
    assert cad_format_for_path(Path("model.cadinput")) is None
