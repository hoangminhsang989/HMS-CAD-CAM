"""Integration tests for controlled failures and staging cleanup."""

import json
from pathlib import Path

import pytest

from hms_cadcam.project.creator import ProjectCreator
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import (
    DatabaseMissingError,
    ManifestDecodeError,
    ProjectAlreadyExistsError,
    ProjectDatabaseError,
    ProjectPermissionError,
    ProjectTransactionError,
    SourceFileNotFoundError,
    UnsupportedFormatVersionError,
    UnsupportedProjectFormatError,
)
from hms_cadcam.project.filesystem import publish_directory
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.validator import ProjectValidator


def test_missing_source_existing_project_and_invalid_manifest(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    with pytest.raises(SourceFileNotFoundError):
        service.create_project_from_source(tmp_path, "Missing Source", tmp_path / "none.step")
    assert not (tmp_path / "Missing Source.HMS").exists()

    session = service.new_project(tmp_path, "Existing")
    with pytest.raises(ProjectAlreadyExistsError):
        service.new_project(tmp_path, "Existing")

    manifest_path = session.root_path / "project.hms.json"
    manifest_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ManifestDecodeError):
        service.open_project(session.root_path)


def test_unsupported_manifest_and_missing_database(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Version Test")
    manifest_path = session.root_path / "project.hms.json"
    data = json.loads(manifest_path.read_text("utf-8"))
    data["format_version"] = 99
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedFormatVersionError):
        service.open_project(session.root_path)

    data["format_version"] = 1
    data["format"] = "OTHER"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedProjectFormatError):
        service.open_project(session.root_path)

    data["format"] = "HMS_PROJECT"
    data["format_version"] = 1
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    (session.root_path / "project.db").unlink()
    with pytest.raises(DatabaseMissingError):
        service.open_project(session.root_path)


def test_corrupt_database_is_rejected(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Corrupt DB")
    (session.root_path / "project.db").write_bytes(b"not a database")
    with pytest.raises(ProjectDatabaseError):
        service.open_project(session.root_path)


@pytest.mark.parametrize("invalid_version", ["1", 1.5, True])
def test_manifest_version_requires_a_json_integer(tmp_path, invalid_version) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Strict Manifest")
    manifest_path = session.root_path / "project.hms.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["format_version"] = invalid_version
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ManifestDecodeError):
        service.open_project(session.root_path)


def test_creation_failure_removes_staging_and_final_project(tmp_path, monkeypatch) -> None:
    store = ProjectManifestStore()
    validator = ProjectValidator()
    database = ProjectDatabase()
    creator = ProjectCreator(store, validator, database)

    def fail_database(_path) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(database, "initialize", fail_database)
    with pytest.raises(OSError):
        creator.create(tmp_path, "Transactional")
    assert not (tmp_path / "Transactional.HMS").exists()
    assert not list(tmp_path.glob(".Transactional.*.creating"))


def test_permission_failure_does_not_create_project(tmp_path, monkeypatch) -> None:
    import hms_cadcam.project.filesystem as filesystem_module

    def deny_staging(*_args, **_kwargs):
        raise PermissionError("simulated permission failure")

    monkeypatch.setattr(filesystem_module.tempfile, "mkdtemp", deny_staging)
    service = ProjectService.create_default(tmp_path / "config")
    with pytest.raises(ProjectPermissionError):
        service.new_project(tmp_path, "No Permission")
    assert not (tmp_path / "No Permission.HMS").exists()


def test_overwrite_failure_restores_existing_target(tmp_path, monkeypatch) -> None:
    target = tmp_path / "Target.HMS"
    target.mkdir()
    (target / "marker.txt").write_text("original", encoding="utf-8")
    staging = tmp_path / ".Target.staging"
    staging.mkdir()
    (staging / "marker.txt").write_text("replacement", encoding="utf-8")
    original_rename = Path.rename

    def fail_staging_publish(self: Path, destination: Path):
        if self == staging:
            raise OSError("simulated publish failure")
        return original_rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_staging_publish)
    with pytest.raises(ProjectTransactionError):
        publish_directory(staging, target, overwrite=True)
    assert (target / "marker.txt").read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(".Target.HMS.*.replaced"))
