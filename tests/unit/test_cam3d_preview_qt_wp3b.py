"""Qt queued-delivery tests for the Stage 9A.8 WP3-B bridge."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Slot
from shiboken6 import isValid

from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DJobExecutionState,
    Cam3DPreviewCoordinator,
    Cam3DPreviewResult,
    Cam3DSubmissionDecision,
)
from hms_cadcam.ui.cam3d_preview_worker import Cam3DQtWorkerBridge
from tests.unit.test_cam3d_preview_worker_wp3b import (
    _BlockingTessellator,
    _ImmediateTessellator,
    _request,
)


class _Receiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[Cam3DPreviewResult] = []
        self.qt_threads: list[QThread] = []
        self.python_threads: list[int] = []

    @Slot(object)
    def accept_preview(self, payload: object) -> None:
        assert isinstance(payload, Cam3DPreviewResult)
        self.results.append(payload)
        self.qt_threads.append(QThread.currentThread())
        self.python_threads.append(threading.get_ident())


def test_queued_delivery_runs_receiver_on_gui_thread(qtbot) -> None:
    caller_thread = threading.get_ident()
    tessellator = _ImmediateTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    bridge = Cam3DQtWorkerBridge(coordinator)
    receiver = _Receiver()
    qtbot.addWidget(receiver) if hasattr(receiver, "show") else None
    bridge.set_receiver(receiver, "accept_preview")
    try:
        with qtbot.waitSignal(bridge.result_ready, timeout=5000) as blocker:
            receipt = bridge.submit(_request())
        qtbot.waitUntil(lambda: len(receiver.results) == 1, timeout=5000)
        assert receipt.accepted and receipt.scheduled
        assert isinstance(blocker.args[0], Cam3DPreviewResult)
        assert receiver.qt_threads == [bridge.thread()]
        assert receiver.python_threads == [caller_thread]
        assert tessellator.threads and tessellator.threads[0] != caller_thread
    finally:
        bridge.shutdown(wait=True)


def test_repeated_receiver_binding_does_not_duplicate_delivery(qtbot) -> None:
    coordinator = Cam3DPreviewCoordinator(_ImmediateTessellator())
    bridge = Cam3DQtWorkerBridge(coordinator)
    receiver = _Receiver()
    bridge.set_receiver(receiver, "accept_preview")
    bridge.set_receiver(receiver, "accept_preview")
    try:
        with qtbot.waitSignal(bridge.result_ready, timeout=5000):
            bridge.submit(_request())
        qtbot.waitUntil(lambda: len(receiver.results) == 1, timeout=5000)
        assert len(receiver.results) == 1
    finally:
        bridge.shutdown(wait=True)


def test_deleted_receiver_is_safe_and_not_retained(qtbot) -> None:
    tessellator = _BlockingTessellator()
    request = _request()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    bridge = Cam3DQtWorkerBridge(coordinator)
    receiver = _Receiver()
    bridge.set_receiver(receiver, "accept_preview")
    bridge.submit(request)
    assert tessellator.started.wait(5.0)
    receiver.deleteLater()
    qtbot.waitUntil(lambda: not isValid(receiver), timeout=5000)
    tessellator.release.set()
    try:
        qtbot.waitUntil(
            lambda: coordinator.job_record(request.job_id).state
            is Cam3DJobExecutionState.COMPLETED,
            timeout=5000,
        )
    finally:
        bridge.shutdown(wait=True)


def test_late_result_after_close_is_dropped_before_qt_signal(qtbot) -> None:
    tessellator = _BlockingTessellator()
    request = _request()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    bridge = Cam3DQtWorkerBridge(coordinator)
    receiver = _Receiver()
    bridge.set_receiver(receiver, "accept_preview")
    emitted: list[object] = []
    bridge.result_ready.connect(emitted.append)
    try:
        bridge.submit(request)
        assert tessellator.started.wait(5.0)
        bridge.close_ownership(request.ownership)
        assert tessellator.cancelled_seen.wait(5.0)
        qtbot.waitUntil(
            lambda: coordinator.job_record(request.job_id).state
            is Cam3DJobExecutionState.DROPPED,
            timeout=5000,
        )
        assert receiver.results == []
        assert emitted == []
    finally:
        tessellator.release.set()
        bridge.shutdown(wait=True)


def test_shutdown_cancels_worker_without_gui_hang_or_thread_leak(qtbot) -> None:
    tessellator = _BlockingTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    bridge = Cam3DQtWorkerBridge(coordinator)
    request = _request()
    bridge.submit(request)
    assert tessellator.started.wait(5.0)
    bridge.shutdown(wait=False)
    assert tessellator.cancelled_seen.wait(5.0)
    qtbot.waitUntil(
        lambda: not any(
            item.name.startswith("hms-cam3d-preview")
            for item in threading.enumerate()
        ),
        timeout=5000,
    )
    assert bridge.submit(_request()).decision is Cam3DSubmissionDecision.CLOSED
