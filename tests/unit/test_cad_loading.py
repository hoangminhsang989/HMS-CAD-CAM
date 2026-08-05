"""Focused Stage 14A WP1 request-owned CAD loading lifecycle tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hms_cadcam.cad.models import CadFormat
from hms_cadcam.ui.cad_loading import (
    CadLoadErrorCode,
    CadLoadOrigin,
    CadLoadState,
    CadLoadingCoordinator,
    cad_format_for_path,
    normalize_import_error,
)


@dataclass
class FakeWorker:
    """A non-native worker surrogate that records safe late-result disposal."""

    request_id: int
    released: int = 0

    def abandon(self) -> None:
        self.released += 1


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