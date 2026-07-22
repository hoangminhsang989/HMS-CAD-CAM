"""Qt worker progress, cancellation and close-lifecycle tests for 8A.2.1."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool

from hms_cadcam.cam.cam3d.parallel import (
    ParallelProgress,
    ParallelProgressPhase,
)
from hms_cadcam.ui.parallel_finishing_worker import ParallelFinishingTask
from tests.unit._parallel_finishing_fixtures import planar_fixture


def test_parallel_worker_reports_progress_and_finishes(qtbot) -> None:
    operation_id = planar_fixture().operation.operation_id
    reports = []
    completed = []

    def operation(_cancelled, progress):
        progress(
            ParallelProgress(
                operation_id, ParallelProgressPhase.VALIDATION, 1, 1
            )
        )
        return "ready"

    task = ParallelFinishingTask(operation)
    task.signals.progress.connect(reports.append)
    task.signals.completed.connect(completed.append)
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    with qtbot.waitSignal(task.signals.finished, timeout=5_000):
        pool.start(task)
    assert pool.waitForDone(5_000)
    assert completed == ["ready"]
    assert reports[0].phase is ParallelProgressPhase.VALIDATION
    assert pool.activeThreadCount() == 0


def test_parallel_worker_abandon_cancels_and_suppresses_late_result(qtbot) -> None:
    completed = []

    def operation(cancelled, _progress):
        while not cancelled():
            pass
        return "must-not-publish"

    task = ParallelFinishingTask(operation)
    task.signals.completed.connect(completed.append)
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    with qtbot.waitSignal(task.signals.finished, timeout=5_000):
        pool.start(task)
        task.abandon()
    assert pool.waitForDone(5_000)
    assert task.cancelled
    assert completed == []
    assert pool.activeThreadCount() == 0
