"""Unit and failure tests for atomic HMS autosave snapshots."""

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    TopologyPath,
    TopologyPathVersion,
)
from hms_cadcam.project.autosave import AutosaveManager, AutosaveMetadata
from hms_cadcam.project.cad_state import CadViewState, PersistentObjectAppearance
from hms_cadcam.project.constants import (
    AUTOSAVE_LATEST_FILENAME,
    AUTOSAVE_METADATA_FILENAME,
    DATABASE_FILENAME,
    MANIFEST_FILENAME,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import (
    AutosaveBusyError,
    AutosaveSnapshotError,
    ProjectDatabaseError,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import utc_now
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.viewer.models import ObjectAppearance


def _manager() -> AutosaveManager:
    return AutosaveManager(ProjectManifestStore(), ProjectValidator(), ProjectDatabase())


def test_clean_session_is_not_autosaved(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Clean")

    assert service.autosave() is None
    assert list((session.root_path / "autosave").iterdir()) == []


def test_autosave_rejects_a_different_expected_project(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Expected Project")
    session.is_dirty = True

    assert service.autosave(expected_project_id=uuid4()) is None
    assert session.is_dirty
    assert list((session.root_path / "autosave").iterdir()) == []


def test_dirty_snapshot_contains_only_manifest_database_and_metadata(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Snapshot")
    source = tmp_path / "part.step"
    source.write_bytes(b"immutable-cad-source")
    service.import_source(source)
    main_manifest = (session.root_path / MANIFEST_FILENAME).read_bytes()
    main_database = (session.root_path / DATABASE_FILENAME).read_bytes()
    session.manifest = replace(
        session.manifest,
        project_name="Unsaved Snapshot Name",
        modified_at=utc_now() + timedelta(seconds=1),
    )
    session.is_dirty = True

    snapshot = service.autosave()

    assert snapshot is not None
    assert {path.name for path in snapshot.path.iterdir()} == {
        AUTOSAVE_METADATA_FILENAME,
        DATABASE_FILENAME,
        MANIFEST_FILENAME,
    }
    assert not (snapshot.path / "source").exists()
    assert not any(path.suffix.casefold() == ".step" for path in snapshot.path.iterdir())
    assert snapshot.metadata.project_id == session.manifest.project_id
    assert ProjectManifestStore().load(snapshot.path).project_name == "Unsaved Snapshot Name"
    assert session.is_dirty
    assert (session.root_path / MANIFEST_FILENAME).read_bytes() == main_manifest
    assert (session.root_path / DATABASE_FILENAME).read_bytes() == main_database
    ProjectDatabase().validate(snapshot.path / DATABASE_FILENAME)

    raw_metadata = json.loads(
        (snapshot.path / AUTOSAVE_METADATA_FILENAME).read_text(encoding="utf-8")
    )
    assert AutosaveMetadata.from_dict(raw_metadata) == snapshot.metadata


def test_latest_snapshot_validates_checksums(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Checksum")
    session.is_dirty = True
    manager = _manager()
    snapshot = manager.create_snapshot(session, uuid4())

    assert manager.load_latest(session.root_path) == snapshot
    (snapshot.path / DATABASE_FILENAME).write_bytes(b"tampered")
    with pytest.raises(AutosaveSnapshotError):
        manager.load_latest(session.root_path)


def test_autosave_database_contains_pending_cad_state_without_cleaning_main(tmp_path) -> None:
    source = tmp_path / "autosave.brep"
    source.write_bytes(b"autosave CAD source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Autosave CAD", source)
    source_id = session.manifest.source_files[0].source_id
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "d" * 32),
    )
    service.stage_cad_view_state(
        CadViewState(
            source_id,
            object_appearances=(
                PersistentObjectAppearance(key, ObjectAppearance(visible=False)),
            ),
        )
    )

    snapshot = service.autosave()

    assert snapshot is not None
    assert session.is_dirty
    with sqlite3.connect(snapshot.path / DATABASE_FILENAME) as connection:
        assert connection.execute(
            "SELECT visible FROM cad_object_appearance"
        ).fetchone()[0] == 0
    with sqlite3.connect(session.root_path / DATABASE_FILENAME) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 0


def test_autosave_failure_preserves_pending_cad_state(tmp_path, monkeypatch) -> None:
    source = tmp_path / "autosave-failure.brep"
    source.write_bytes(b"autosave failure CAD source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Autosave Failure", source)
    source_id = session.manifest.source_files[0].source_id
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "9" * 32),
    )
    state = CadViewState(
        source_id,
        object_appearances=(
            PersistentObjectAppearance(key, ObjectAppearance(visible=False)),
        ),
    )
    service.stage_cad_view_state(state)

    def fail_snapshot_state(*_args, **_kwargs):
        raise ProjectDatabaseError("simulated pending-state snapshot failure")

    monkeypatch.setattr(service._autosave._cad_state_store, "replace_all", fail_snapshot_state)
    with pytest.raises(ProjectDatabaseError, match="simulated"):
        service.autosave()

    assert session.is_dirty
    assert service.cad_view_state(source_id) == state


def test_new_complete_snapshot_atomically_becomes_latest(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "New Latest")
    session.is_dirty = True
    manager = _manager()

    first = manager.create_snapshot(session, uuid4())
    second = manager.create_snapshot(session, uuid4())

    assert first.metadata.snapshot_id != second.metadata.snapshot_id
    assert first.path.is_dir()
    assert manager.load_latest(session.root_path) == second


def test_failed_snapshot_keeps_previous_latest(tmp_path, monkeypatch) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Failure")
    session.is_dirty = True
    database = ProjectDatabase()
    manager = AutosaveManager(ProjectManifestStore(), ProjectValidator(), database)
    first = manager.create_snapshot(session, uuid4())
    pointer = session.root_path / "autosave" / AUTOSAVE_LATEST_FILENAME
    original_pointer = pointer.read_bytes()

    def fail_backup(_source, _destination) -> None:
        raise ProjectDatabaseError("simulated autosave failure")

    monkeypatch.setattr(database, "backup", fail_backup)
    with pytest.raises(ProjectDatabaseError):
        manager.create_snapshot(session, uuid4())

    assert pointer.read_bytes() == original_pointer
    assert first.path.is_dir()
    assert manager.load_latest(session.root_path) == first
    assert not list((session.root_path / "autosave").glob("*.creating"))


def test_pointer_publish_failure_keeps_previous_latest(tmp_path, monkeypatch) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Pointer Failure")
    session.is_dirty = True
    manager = _manager()
    first = manager.create_snapshot(session, uuid4())
    pointer = session.root_path / "autosave" / AUTOSAVE_LATEST_FILENAME
    original_pointer = pointer.read_bytes()

    def fail_pointer(_autosave_root, _snapshot_id) -> None:
        raise OSError("simulated pointer failure")

    monkeypatch.setattr(manager, "_write_latest_pointer", fail_pointer)
    with pytest.raises(AutosaveSnapshotError):
        manager.create_snapshot(session, uuid4())

    assert pointer.read_bytes() == original_pointer
    assert manager.load_latest(session.root_path) == first
    snapshot_directories = [
        path for path in (session.root_path / "autosave").iterdir() if path.is_dir()
    ]
    assert snapshot_directories == [first.path]


def test_two_autosaves_cannot_run_at_the_same_time(tmp_path, monkeypatch) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Concurrent")
    session.is_dirty = True
    database = ProjectDatabase()
    manager = AutosaveManager(ProjectManifestStore(), ProjectValidator(), database)
    original_backup = database.backup
    backup_started = threading.Event()
    allow_backup = threading.Event()
    failures: list[BaseException] = []

    def blocking_backup(source, destination) -> None:
        backup_started.set()
        assert allow_backup.wait(timeout=5)
        original_backup(source, destination)

    def create_first_snapshot() -> None:
        try:
            manager.create_snapshot(session, uuid4())
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(database, "backup", blocking_backup)
    worker = threading.Thread(target=create_first_snapshot)
    worker.start()
    assert backup_started.wait(timeout=5)
    try:
        with pytest.raises(AutosaveBusyError):
            manager.create_snapshot(session, uuid4())
    finally:
        allow_backup.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert failures == []
