"""Tests for conservative staging and temporary-directory cleanup."""

import json
from datetime import timedelta
from uuid import uuid4

from hms_cadcam.project.constants import OWNED_DIRECTORY_METADATA_FILENAME
from hms_cadcam.project.models import utc_now
from hms_cadcam.project.owned_directories import (
    OwnedDirectoryPurpose,
    cleanup_stale_owned_directories,
    write_owned_directory_metadata,
)


def _make_old_owned_directory(parent, name, purpose, *, hostname="local", pid=9999):
    directory = parent / name
    directory.mkdir()
    write_owned_directory_metadata(directory, purpose)
    metadata_path = directory / OWNED_DIRECTORY_METADATA_FILENAME
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["created_at"] = "2000-01-01T00:00:00Z"
    data["hostname"] = hostname
    data["pid"] = pid
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    return directory


def test_cleanup_removes_only_allowlisted_dead_local_directories(tmp_path) -> None:
    staging = _make_old_owned_directory(
        tmp_path,
        ".Part.deadbeef.creating",
        OwnedDirectoryPurpose.STAGING,
    )
    temp = _make_old_owned_directory(
        tmp_path,
        f".{uuid4().hex}.hms-temp",
        OwnedDirectoryPurpose.TEMP,
    )
    active = _make_old_owned_directory(
        tmp_path,
        ".Active.deadbeef.creating",
        OwnedDirectoryPurpose.STAGING,
        pid=1111,
    )
    remote = _make_old_owned_directory(
        tmp_path,
        ".Remote.deadbeef.creating",
        OwnedDirectoryPurpose.STAGING,
        hostname="other-machine",
    )
    recent = tmp_path / ".Recent.deadbeef.creating"
    recent.mkdir()
    write_owned_directory_metadata(recent, OwnedDirectoryPurpose.STAGING)

    removed = cleanup_stale_owned_directories(
        tmp_path,
        timedelta(hours=1),
        now=utc_now(),
        hostname="local",
        pid_checker=lambda pid: pid == 1111,
    )

    assert set(removed) == {staging, temp}
    assert active.exists()
    assert remote.exists()
    assert recent.exists()


def test_cleanup_preserves_user_and_unrecognized_directories(tmp_path) -> None:
    user_directory = tmp_path / "user-data"
    user_directory.mkdir()
    (user_directory / "important.txt").write_text("keep", encoding="utf-8")
    wrong_name = _make_old_owned_directory(
        tmp_path,
        "looks-owned-but-is-not-allowlisted",
        OwnedDirectoryPurpose.STAGING,
    )
    malformed = tmp_path / ".Broken.deadbeef.creating"
    malformed.mkdir()
    (malformed / OWNED_DIRECTORY_METADATA_FILENAME).write_text("{broken", encoding="utf-8")

    removed = cleanup_stale_owned_directories(
        tmp_path,
        timedelta(0),
        hostname="local",
        pid_checker=lambda pid: False,
    )

    assert removed == ()
    assert (user_directory / "important.txt").read_text(encoding="utf-8") == "keep"
    assert wrong_name.exists()
    assert malformed.exists()
