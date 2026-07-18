"""Unit-level project service state tests using real lightweight adapters."""

import pytest

from hms_cadcam.project.exceptions import UnsavedChangesError
from hms_cadcam.project.service import ProjectService


def test_service_activates_saves_and_closes_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Service Project")
    assert service.current_project is session
    assert service.has_project

    session.is_dirty = True
    with pytest.raises(UnsavedChangesError):
        service.close_project()
    service.save()
    assert not service.is_dirty
    service.close_project()
    assert not service.has_project


def test_failed_open_does_not_replace_current_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    original = service.new_project(tmp_path, "Original")
    with pytest.raises(Exception):
        service.open_project(tmp_path / "Missing.HMS")
    assert service.current_project is original
