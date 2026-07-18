"""UI coordination tests for periodic background autosave."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from hms_cadcam.project.exceptions import ProjectDatabaseError  # noqa: E402
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.project_controller import ProjectUiController  # noqa: E402
from hms_cadcam.ui.project_worker import ProjectTask  # noqa: E402


class _QueuedThreadPool:
    def __init__(self) -> None:
        self.tasks: list[ProjectTask] = []

    def start(self, task: ProjectTask) -> None:
        self.tasks.append(task)


def _controller(tmp_path):
    application = QApplication.instance() or QApplication([])
    service = ProjectService.create_default(tmp_path / "config")
    controller = ProjectUiController(QMainWindow(), service)
    thread_pool = _QueuedThreadPool()
    controller._thread_pool = thread_pool
    return application, controller, service, thread_pool


def test_timer_defaults_to_five_minutes_and_clean_project_is_skipped(tmp_path) -> None:
    _application, controller, service, thread_pool = _controller(tmp_path)
    service.new_project(tmp_path, "Clean Timer")
    controller._bind_autosave_session()

    assert controller.autosave_interval_ms == 5 * 60 * 1000
    assert controller._autosave_timer.isActive()
    controller.request_autosave()
    assert thread_pool.tasks == []


def test_dirty_project_autosaves_in_worker_without_becoming_clean(tmp_path) -> None:
    _application, controller, service, thread_pool = _controller(tmp_path)
    session = service.new_project(tmp_path, "Dirty Worker")
    session.is_dirty = True
    controller._bind_autosave_session()

    controller.request_autosave()

    assert controller.is_autosaving
    assert not controller.is_busy
    assert not controller._autosave_timer.isActive()
    assert not controller.actions["save"].isEnabled()
    thread_pool.tasks.pop().run()

    assert not controller.is_autosaving
    assert session.is_dirty
    assert controller._autosave_timer.isActive()
    assert any((session.root_path / "autosave").iterdir())


def test_change_during_autosave_queues_one_follow_up_snapshot(tmp_path) -> None:
    application, controller, service, thread_pool = _controller(tmp_path)
    session = service.new_project(tmp_path, "Pending Change")
    session.is_dirty = True
    controller._bind_autosave_session()
    controller.request_autosave()

    controller.request_autosave()
    assert controller._autosave_pending
    first = thread_pool.tasks.pop(0)
    first.run()
    application.processEvents()

    assert len(thread_pool.tasks) == 1
    assert controller.is_autosaving
    thread_pool.tasks.pop().run()
    assert not controller.is_autosaving


def test_project_operation_prevents_autosave_from_starting(tmp_path) -> None:
    _application, controller, service, thread_pool = _controller(tmp_path)
    session = service.new_project(tmp_path, "Busy Project")
    session.is_dirty = True
    controller._bind_autosave_session()
    controller._active_task = ProjectTask(lambda: None)

    controller.request_autosave()

    assert controller._autosave_pending
    assert thread_pool.tasks == []
    controller._active_task = None


def test_old_autosave_worker_cannot_affect_new_project_session(tmp_path) -> None:
    _application, controller, service, thread_pool = _controller(tmp_path)
    old_session = service.new_project(tmp_path, "Old Session")
    old_session.is_dirty = True
    controller._bind_autosave_session()
    controller.request_autosave()
    old_task = thread_pool.tasks.pop()

    new_session = service.new_project(tmp_path, "New Session")
    new_session.is_dirty = True
    controller._bind_autosave_session()
    old_task.run()

    assert controller._autosave_project_id == new_session.manifest.project_id
    assert list((new_session.root_path / "autosave").iterdir()) == []
    assert new_session.is_dirty


def test_autosave_error_preserves_dirty_project_and_controller(tmp_path, monkeypatch) -> None:
    _application, controller, service, thread_pool = _controller(tmp_path)
    session = service.new_project(tmp_path, "Autosave Error")
    session.is_dirty = True
    controller._bind_autosave_session()
    messages: list[str] = []
    controller.message.connect(messages.append)

    def fail_autosave(*, expected_project_id=None):
        raise ProjectDatabaseError("simulated autosave failure")

    monkeypatch.setattr(service, "autosave", fail_autosave)
    controller.request_autosave()
    thread_pool.tasks.pop().run()

    assert service.current_project is session
    assert session.is_dirty
    assert not controller.is_autosaving
    assert any("Autosave thất bại" in message for message in messages)


def test_close_project_stops_timer_and_unbinds_session(tmp_path) -> None:
    _application, controller, service, _thread_pool = _controller(tmp_path)
    service.new_project(tmp_path, "Close Timer")
    controller._bind_autosave_session()
    assert controller._autosave_timer.isActive()

    assert controller._try_close_current(discard_changes=False)

    assert not controller._autosave_timer.isActive()
    assert controller._autosave_project_id is None
