"""Integration tests for crash recovery, rollback, and .replaced restoration."""

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    TopologyPath,
    TopologyPathVersion,
)
from hms_cadcam.project.cad_state import CadViewState, PersistentObjectAppearance
from hms_cadcam.project.constants import (
    AUTOSAVE_METADATA_FILENAME,
    DATABASE_FILENAME,
    MANIFEST_FILENAME,
    RECOVERY_BACKUP_METADATA_FILENAME,
    SESSION_LOCK_FILENAME,
)
from hms_cadcam.project.exceptions import (
    RecoveryRequiredError,
    RecoverySnapshotInvalidError,
    RecoveryTransactionError,
    ReplacedProjectAmbiguousError,
    ReplacedProjectInvalidError,
    ReplacedProjectRecoveryRequiredError,
)
from hms_cadcam.project.models import utc_now
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.models import ObjectAppearance


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_crashed_project(tmp_path):
    owner = ProjectService.create_default(tmp_path / "owner-config")
    session = owner.new_project(tmp_path, "Crash Recovery")
    source = tmp_path / "immutable.step"
    source.write_bytes(b"immutable-source-data")
    owner.import_source(source)
    copied_source = session.root_path / "source" / source.name
    session.manifest = replace(
        session.manifest,
        project_name="Recovered Unsaved State",
        modified_at=utc_now() + timedelta(seconds=1),
    )
    session.is_dirty = True
    snapshot = owner.autosave()
    assert snapshot is not None
    opener = ProjectService.create_default(tmp_path / "opener-config")
    opener._session_locks._pid_checker = lambda _pid: False
    return owner, opener, session, snapshot, copied_source


def test_stale_lock_offers_valid_snapshot_and_recovers_without_source_changes(tmp_path) -> None:
    _owner, opener, session, snapshot, copied_source = _make_crashed_project(tmp_path)
    source_hash = _digest(copied_source)
    main_manifest_before = (session.root_path / MANIFEST_FILENAME).read_bytes()
    main_database_before = (session.root_path / DATABASE_FILENAME).read_bytes()

    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    assessment = raised.value.assessment
    assert assessment.abnormal_close
    assert assessment.snapshot == snapshot

    recovered = opener.recover_project(assessment)

    assert recovered.manifest.project_name == "Recovered Unsaved State"
    assert _digest(copied_source) == source_hash
    assert not recovered.is_dirty
    backups = [path for path in (session.root_path / "backups").iterdir() if path.is_dir()]
    assert len(backups) == 1
    backup = backups[0]
    assert {path.name for path in backup.iterdir()} == {
        DATABASE_FILENAME,
        MANIFEST_FILENAME,
        RECOVERY_BACKUP_METADATA_FILENAME,
    }
    assert (backup / MANIFEST_FILENAME).read_bytes() == main_manifest_before
    assert (backup / DATABASE_FILENAME).read_bytes() == main_database_before
    assert not (backup / "source").exists()


def test_recovery_restores_pending_cad_view_state_from_snapshot(tmp_path) -> None:
    owner = ProjectService.create_default(tmp_path / "owner-cad-config")
    source = tmp_path / "recovery.brep"
    source.write_bytes(b"recovery CAD source")
    session = owner.create_project_from_source(tmp_path, "CAD Recovery", source)
    source_id = session.manifest.source_files[0].source_id
    key = PersistentCadObjectKey(
        source_id,
        CadGeometryKind.BREP,
        TopologyPathVersion.V1,
        TopologyPath("solid:" + "e" * 32),
    )
    state = CadViewState(
        source_id,
        object_appearances=(
            PersistentObjectAppearance(key, ObjectAppearance(visible=False)),
        ),
    )
    owner.stage_cad_view_state(state)
    snapshot = owner.autosave()
    assert snapshot is not None and session.is_dirty

    opener = ProjectService.create_default(tmp_path / "opener-cad-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)

    recovered = opener.recover_project(raised.value.assessment)

    assert not recovered.is_dirty
    assert opener.cad_view_state(source_id) == state


def test_corrupt_snapshot_is_rejected_before_stale_lock_is_replaced(tmp_path) -> None:
    _owner, opener, session, snapshot, _source = _make_crashed_project(tmp_path)
    lock_before = (session.root_path / SESSION_LOCK_FILENAME).read_bytes()
    (snapshot.path / DATABASE_FILENAME).write_bytes(b"corrupt")

    with pytest.raises(RecoverySnapshotInvalidError):
        opener.open_project(session.root_path)

    assert (session.root_path / SESSION_LOCK_FILENAME).read_bytes() == lock_before
    assert opener.current_project is None


def test_recovery_failure_rolls_back_manifest_database_and_preserves_source(
    tmp_path,
    monkeypatch,
) -> None:
    _owner, opener, session, _snapshot, copied_source = _make_crashed_project(tmp_path)
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    original_manifest = (session.root_path / MANIFEST_FILENAME).read_bytes()
    original_database = (session.root_path / DATABASE_FILENAME).read_bytes()
    source_hash = _digest(copied_source)
    original_replace = Path.replace
    failed_once = False

    def fail_first_manifest_publish(self: Path, destination: Path):
        nonlocal failed_once
        if (
            not failed_once
            and destination == session.root_path / MANIFEST_FILENAME
            and self.name.endswith(".recovering")
        ):
            failed_once = True
            raise OSError("simulated manifest publish failure")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", fail_first_manifest_publish)
    with pytest.raises(RecoveryTransactionError):
        opener.recover_project(raised.value.assessment)

    assert (session.root_path / MANIFEST_FILENAME).read_bytes() == original_manifest
    assert (session.root_path / DATABASE_FILENAME).read_bytes() == original_database
    assert _digest(copied_source) == source_hash
    assert opener.current_project is None
    assert not (session.root_path / SESSION_LOCK_FILENAME).exists()
    assert list((session.root_path / "backups").iterdir())


def test_unique_valid_replaced_project_requires_approval_then_restores(tmp_path) -> None:
    creator = ProjectService.create_default(tmp_path / "creator-config")
    session = creator.new_project(tmp_path, "Interrupted Publish")
    source = tmp_path / "part.step"
    source.write_bytes(b"source-remains")
    creator.import_source(source)
    source_hash = _digest(session.root_path / "source" / source.name)
    creator.close_project()
    target = session.root_path
    candidate = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    target.rename(candidate)
    opener = ProjectService.create_default(tmp_path / "opener-config")

    with pytest.raises(ReplacedProjectRecoveryRequiredError) as raised:
        opener.open_project(target)
    assert not target.exists()
    assert candidate.exists()

    restored = opener.restore_replaced_and_open(raised.value.assessment)

    assert restored.root_path == target
    assert target.is_dir()
    assert not candidate.exists()
    assert _digest(target / "source" / source.name) == source_hash


def test_multiple_replaced_candidates_are_preserved_as_ambiguous(tmp_path) -> None:
    creator = ProjectService.create_default(tmp_path / "creator-config")
    session = creator.new_project(tmp_path, "Ambiguous")
    creator.close_project()
    target = session.root_path
    first = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    second = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    target.rename(first)
    shutil.copytree(first, second)
    opener = ProjectService.create_default(tmp_path / "opener-config")

    with pytest.raises(ReplacedProjectAmbiguousError):
        opener.open_project(target)

    assert not target.exists()
    assert first.is_dir()
    assert second.is_dir()


def test_invalid_replaced_candidate_is_preserved(tmp_path) -> None:
    target = tmp_path / "Invalid.HMS"
    candidate = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    candidate.mkdir()
    (candidate / MANIFEST_FILENAME).write_text("{broken", encoding="utf-8")
    opener = ProjectService.create_default(tmp_path / "config")

    with pytest.raises(ReplacedProjectInvalidError):
        opener.open_project(target)

    assert not target.exists()
    assert candidate.is_dir()
    assert (candidate / MANIFEST_FILENAME).read_text(encoding="utf-8") == "{broken"


def test_replaced_directory_is_not_touched_when_valid_target_still_exists(tmp_path) -> None:
    creator = ProjectService.create_default(tmp_path / "creator-config")
    session = creator.new_project(tmp_path, "Existing Target")
    creator.close_project()
    target = session.root_path
    candidate = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    shutil.copytree(target, candidate)
    opener = ProjectService.create_default(tmp_path / "opener-config")

    opened = opener.open_project(target)

    assert opened.root_path == target
    assert candidate.is_dir()


def test_recovery_backup_metadata_is_valid_json(tmp_path) -> None:
    _owner, opener, session, _snapshot, _source = _make_crashed_project(tmp_path)
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    opener.recover_project(raised.value.assessment)
    backup = next(path for path in (session.root_path / "backups").iterdir() if path.is_dir())
    data = json.loads(
        (backup / RECOVERY_BACKUP_METADATA_FILENAME).read_text(encoding="utf-8")
    )
    assert data["format"] == "HMS_RECOVERY_BACKUP"
    assert data["snapshot_id"] == str(raised.value.assessment.snapshot.metadata.snapshot_id)
    assert AUTOSAVE_METADATA_FILENAME not in {path.name for path in backup.iterdir()}
