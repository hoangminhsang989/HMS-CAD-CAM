"""Unit-level project service state tests using real lightweight adapters."""

import pytest

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    TopologyPath,
    TopologyPathVersion,
)
from hms_cadcam.project.cad_state import CadViewState, PersistentObjectAppearance
from hms_cadcam.project.constants import SESSION_LOCK_FILENAME
from hms_cadcam.project.exceptions import ProjectDatabaseError, ProjectLockedError, UnsavedChangesError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor


def _cad_state(source_id) -> CadViewState:
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "c" * 32),
    )
    return CadViewState(
        source_id,
        object_appearances=(
            PersistentObjectAppearance(
                key,
                ObjectAppearance(False, ObjectColor(0.2, 0.3, 0.4), 0.25),
            ),
        ),
    )


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


def test_cad_state_save_open_and_save_as_preserve_logical_source_id(tmp_path) -> None:
    source = tmp_path / "logical.brep"
    source.write_bytes(b"immutable logical CAD source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "CAD State", source)
    source_id = session.manifest.source_files[0].source_id
    state = _cad_state(source_id)

    service.stage_cad_view_state(state)
    assert session.is_dirty
    service.save()
    assert not session.is_dirty
    service.close_project()
    reopened = service.open_project(session.root_path)
    assert service.cad_view_state(source_id) == state

    copied = service.save_as(tmp_path, "CAD State Copy")
    assert copied.manifest.project_id != reopened.manifest.project_id
    assert copied.manifest.source_files[0].source_id == source_id
    assert service.cad_view_state(source_id) == state
    copied_root = copied.root_path
    service.close_project()
    reopened_copy = service.open_project(copied_root)
    assert reopened_copy.manifest.source_files[0].source_id == source_id
    assert service.cad_view_state(source_id) == state


def test_failed_save_keeps_pending_cad_state_dirty_and_out_of_main_db(
    tmp_path,
    monkeypatch,
) -> None:
    import sqlite3

    source = tmp_path / "pending.brep"
    source.write_bytes(b"pending CAD source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Pending", source)
    source_id = session.manifest.source_files[0].source_id
    state = _cad_state(source_id)
    service.stage_cad_view_state(state)

    def fail_manifest_save(*_args, **_kwargs):
        raise OSError("simulated manifest save failure")

    monkeypatch.setattr(service._saver._manifest_store, "save", fail_manifest_save)
    with pytest.raises(OSError, match="simulated"):
        service.save()

    assert session.is_dirty
    assert service.cad_view_state(source_id) == state
    with sqlite3.connect(session.root_path / "project.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM cad_object_appearance"
        ).fetchone()[0] == 0


def test_importing_another_source_does_not_clean_pending_cad_state(tmp_path) -> None:
    first = tmp_path / "first.brep"
    second = tmp_path / "second.brep"
    first.write_bytes(b"first immutable CAD source")
    second.write_bytes(b"second immutable CAD source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Pending Import", first)
    source_id = session.manifest.source_files[0].source_id
    state = _cad_state(source_id)
    service.stage_cad_view_state(state)

    service.import_source(second)

    assert session.is_dirty
    assert service.cad_view_state(source_id) == state
    assert session.manifest.source_files[0].source_id == source_id
    assert session.manifest.source_files[1].source_id != source_id
