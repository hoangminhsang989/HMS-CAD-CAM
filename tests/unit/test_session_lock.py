"""Unit tests for versioned HMS session-lock ownership."""

import json
from uuid import uuid4

import pytest

from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    SESSION_LOCK_FILENAME,
    SESSION_LOCK_FORMAT,
    SESSION_LOCK_FORMAT_VERSION,
)
from hms_cadcam.project.exceptions import ProjectLockUnknownError
from hms_cadcam.project.session_lock import LockState, SessionLockManager


def test_create_read_and_release_active_lock(tmp_path) -> None:
    project_root = tmp_path / "Part.HMS"
    project_root.mkdir()
    manager = SessionLockManager(hostname="workstation", pid=1234, pid_checker=lambda pid: True)

    metadata = manager.acquire(project_root, project_id := uuid4())
    data = json.loads((project_root / SESSION_LOCK_FILENAME).read_text(encoding="utf-8"))

    assert data == metadata.to_dict()
    assert data["format"] == SESSION_LOCK_FORMAT
    assert data["format_version"] == SESSION_LOCK_FORMAT_VERSION
    assert data["project_id"] == str(project_id)
    assert data["application_version"] == APPLICATION_VERSION
    assert manager.inspect(project_root).state is LockState.ACTIVE
    manager.release(project_root)
    assert not (project_root / SESSION_LOCK_FILENAME).exists()


def test_same_host_dead_pid_is_stale(tmp_path) -> None:
    project_root = tmp_path / "Stale.HMS"
    project_root.mkdir()
    owner = SessionLockManager(hostname="workstation", pid=4321, pid_checker=lambda pid: True)
    owner.acquire(project_root, uuid4())

    inspector = SessionLockManager(hostname="workstation", pid_checker=lambda pid: False)
    assert inspector.inspect(project_root).state is LockState.STALE


def test_other_host_lock_is_unknown_without_pid_check(tmp_path) -> None:
    project_root = tmp_path / "Remote.HMS"
    project_root.mkdir()
    owner = SessionLockManager(hostname="remote-host", pid=7654, pid_checker=lambda pid: True)
    owner.acquire(project_root, uuid4())

    def unexpected_pid_check(_pid: int) -> bool:
        raise AssertionError("PID from another host must not be checked")

    inspector = SessionLockManager(hostname="local-host", pid_checker=unexpected_pid_check)
    assert inspector.inspect(project_root).state is LockState.UNKNOWN


def test_malformed_lock_is_unknown_and_is_not_deleted(tmp_path) -> None:
    project_root = tmp_path / "Broken.HMS"
    project_root.mkdir()
    lock_path = project_root / SESSION_LOCK_FILENAME
    lock_path.write_text("{broken", encoding="utf-8")
    manager = SessionLockManager(hostname="workstation", pid_checker=lambda pid: False)

    assert manager.inspect(project_root).state is LockState.UNKNOWN
    with pytest.raises(ProjectLockUnknownError):
        manager.acquire(project_root, uuid4())
    assert lock_path.read_text(encoding="utf-8") == "{broken"
