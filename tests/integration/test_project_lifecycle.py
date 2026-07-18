"""Integration tests for the complete HMS project lifecycle."""

import hashlib
import json
import sqlite3

from hms_cadcam.project.constants import OWNED_DIRECTORY_METADATA_FILENAME
from hms_cadcam.project.service import ProjectService


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_unicode_create_import_open_and_save_as(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Chi tiết có khoảng trắng.HMS.HMS")
    root = tmp_path / "Chi tiết có khoảng trắng.HMS"

    assert session.root_path == root
    assert {path.name for path in root.iterdir()} == {
        "autosave",
        "backups",
        "project.hms.json",
        "project.db",
        "session.lock",
        "source",
        "temp",
    }
    assert (root / "temp" / OWNED_DIRECTORY_METADATA_FILENAME).is_file()
    source = tmp_path / "nguồn mẫu.step"
    source.write_bytes(b"original cad source\x00\x01")
    original_hash = digest(source)

    service.import_source(source)
    copied = root / "source" / source.name
    assert copied.read_bytes() == source.read_bytes()
    assert digest(source) == original_hash == digest(copied)
    assert service.current_project.manifest.source_files[0].sha256 == original_hash

    service.save()
    project_id = service.current_project.manifest.project_id
    service.close_project()
    reopened = service.open_project(root)
    assert reopened.manifest.project_id == project_id
    assert reopened.manifest.project_name == "Chi tiết có khoảng trắng"

    copied_session = service.save_as(tmp_path, "Bản sao dự án")
    assert copied_session.root_path == tmp_path / "Bản sao dự án.HMS"
    assert copied_session.manifest.project_id != project_id
    assert digest(copied_session.root_path / "source" / source.name) == original_hash
    assert digest(root / "source" / source.name) == original_hash
    with sqlite3.connect(copied_session.root_path / "project.db") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_create_project_from_source_writes_valid_manifest(tmp_path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step-data")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Part", source)
    data = json.loads((session.root_path / "project.hms.json").read_text("utf-8"))
    assert data["format"] == "HMS_PROJECT"
    assert data["format_version"] == 1
    assert data["active_document"] is None
    assert data["source_files"][0]["stored_path"] == "source/part.step"


def test_confirmed_overwrite_replaces_complete_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    first = service.new_project(tmp_path, "Replace Me")
    first_id = first.manifest.project_id
    replacement = service.new_project(tmp_path, "Replace Me", overwrite=True)
    assert replacement.root_path == first.root_path
    assert replacement.manifest.project_id != first_id
    assert {path.name for path in replacement.root_path.iterdir()} == {
        "autosave",
        "backups",
        "project.hms.json",
        "project.db",
        "session.lock",
        "source",
        "temp",
    }
    assert (
        replacement.root_path / "temp" / OWNED_DIRECTORY_METADATA_FILENAME
    ).is_file()
