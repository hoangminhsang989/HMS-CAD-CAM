"""Unit-level project service state tests using real lightweight adapters."""

import pytest

from hms_cadcam.project.constants import SESSION_LOCK_FILENAME
from hms_cadcam.project.exceptions import ProjectDatabaseError, ProjectLockedError, UnsavedChangesError
from hms_cadcam.project.service import ProjectService


def test_service_activates_saves_and_closes_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Service Project")
    assert service.current_project is session
    assert service.has_project
    assert (session.root_path / SESSION_LOCK_FILENAME).is_file()

    session.is_dirty = True
    with pytest.raises(UnsavedChangesError):
        service.close_project()
    service.save()
    assert not service.is_dirty
    service.close_project()
    assert not service.has_project
    assert not (session.root_path / SESSION_LOCK_FILENAME).exists()


def test_failed_open_does_not_replace_current_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    original = service.new_project(tmp_path, "Original")
    with pytest.raises(Exception):
        service.open_project(tmp_path / "Missing.HMS")
    assert service.current_project is original
    assert (original.root_path / SESSION_LOCK_FILENAME).is_file()


def test_two_services_cannot_open_the_same_project_for_writing(tmp_path) -> None:
    first_service = ProjectService.create_default(tmp_path / "config-1")
    session = first_service.new_project(tmp_path, "Exclusive")
    second_service = ProjectService.create_default(tmp_path / "config-2")

    with pytest.raises(ProjectLockedError):
        second_service.open_project(session.root_path)
    assert first_service.current_project is session
    assert (session.root_path / SESSION_LOCK_FILENAME).is_file()


def test_failed_new_project_open_keeps_old_session_and_lock(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    original = service.new_project(tmp_path, "Original Locked")
    creator = ProjectService.create_default(tmp_path / "other-config")
    candidate = creator.new_project(tmp_path, "Broken Candidate")
    creator.close_project()
    (candidate.root_path / "project.db").write_bytes(b"not a database")

    with pytest.raises(ProjectDatabaseError):
        service.open_project(candidate.root_path)

    assert service.current_project is original
    assert (original.root_path / SESSION_LOCK_FILENAME).is_file()
    assert not (candidate.root_path / SESSION_LOCK_FILENAME).exists()
