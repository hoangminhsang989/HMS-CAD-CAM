"""Stage 12.5A Save, Save As, autosave, recovery, feature-off, and read-only."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.lathe.persistence import (
    LatheDerivedKind,
    LatheProjectPersistenceService,
    LatheProjectSnapshot,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.project.exceptions import ProjectError, RecoveryRequiredError
from hms_cadcam.project.cad_state import CadViewState
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.models import DisplayMode
from tests.unit._lathe_fixtures import stable_uuid
from tests.unit._lathe_persistence_fixtures import persistence_snapshot


def _authored_for(session):  # type: ignore[no-untyped-def]
    source_id = session.manifest.source_files[0].source_id
    return persistence_snapshot(
        project_id=session.manifest.project_id,
        document_id=CadDocumentId("lathe-project-document"),
        source_id=source_id,
        setup_id=SetupId(stable_uuid("stage12-5a/lifecycle-setup")),
        generation=0,
        strategies=(LatheStrategyId.FACE, LatheStrategyId.OD_FINISH),
    )


def _new_enabled_service(tmp_path, name="Lifecycle"):  # type: ignore[no-untyped-def]
    source = tmp_path / f"{name}.brep"
    source.write_bytes(b"immutable lathe source")
    service = ProjectService.create_default(
        tmp_path / f"{name}-config",
        lathe_persistence_enabled=True,
    )
    session = service.create_project_from_source(tmp_path, name, source)
    return service, session


def test_save_open_and_multiple_cycles_preserve_exact_authored_state(tmp_path) -> None:
    service, session = _new_enabled_service(tmp_path)
    authored = _authored_for(session)
    service.stage_lathe_snapshot(authored)
    service.save()
    database_path = session.root_path / "project.db"
    assert not tuple((session.root_path / "nc").glob("*.NC"))
    for cycle in range(3):
        service.close_project()
        service = ProjectService.create_default(
            tmp_path / f"open-config-{cycle}",
            lathe_persistence_enabled=True,
        )
        reopened = service.open_project(session.root_path)
        assert reopened.lathe_snapshot == authored
        assert reopened.persisted_lathe_snapshot == authored
        assert not reopened.is_dirty
        with closing(sqlite3.connect(database_path)) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_lathe_save_failure_rolls_back_shared_cad_cam_transaction(
    tmp_path, monkeypatch
) -> None:
    service, session = _new_enabled_service(tmp_path, "Atomic")
    authored = _authored_for(session)
    source_id = session.manifest.source_files[0].source_id
    service.stage_cad_view_state(
        CadViewState(source_id, display_mode=DisplayMode.WIREFRAME)
    )
    service.stage_lathe_snapshot(authored)
    original = service._saver._lathe_persistence.replace_all

    def fail_after_lathe_write(connection, snapshot):  # type: ignore[no-untyped-def]
        original(connection, snapshot)
        raise RuntimeError("simulated Lathe transaction failure")

    monkeypatch.setattr(
        service._saver._lathe_persistence,
        "replace_all",
        fail_after_lathe_write,
    )
    with pytest.raises(RuntimeError, match="transaction failure"):
        service.save()
    with closing(sqlite3.connect(session.root_path / "project.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cad_view_state").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM lathe_programs").fetchone() == (0,)
    assert session.is_dirty


def test_save_as_rebinds_authoring_drops_derived_and_creates_no_nc(tmp_path) -> None:
    service, session = _new_enabled_service(tmp_path, "SaveAs")
    authored = _authored_for(session)
    program = authored.programs[0]
    derived = LatheProjectPersistenceService.create_derived_snapshot(
        kind=LatheDerivedKind.NEUTRAL_LISTING,
        program_id=program.identity.program_id,
        operation_id=None,
        owner_revision=program.identity.revision,
        schema_version=1,
        algorithm_version="lathe.neutral.listing.v1",
        dependency_fingerprint="a" * 64,
        payload={"text": "PROGRAM BEGIN\nPROGRAM END"},
    )
    staged = LatheProjectSnapshot(authored.programs, (derived,))
    service.stage_lathe_snapshot(staged)
    service.save()
    old_project_id = session.manifest.project_id

    copied = service.save_as(tmp_path, "SaveAs Copy")

    assert copied.manifest.project_id != old_project_id
    assert copied.lathe_snapshot is not None
    assert copied.lathe_snapshot.derived_snapshots == ()
    rebound = copied.lathe_snapshot.programs[0]
    assert rebound.identity.project_id == str(copied.manifest.project_id)
    assert tuple(item.strategy_id for item in rebound.operations) == (
        LatheStrategyId.FACE,
        LatheStrategyId.OD_FINISH,
    )
    assert all(
        item.ownership.project_id == copied.manifest.project_id
        for item in rebound.operations
    )
    assert not tuple((copied.root_path / "nc").glob("*.NC"))


def test_autosave_database_and_recovery_restore_lathe_in_same_snapshot(tmp_path) -> None:
    service, session = _new_enabled_service(tmp_path, "Autosave")
    authored = _authored_for(session)
    service.stage_lathe_snapshot(authored)
    autosave = service.autosave()
    assert autosave is not None
    autosave_loaded = LatheProjectPersistenceService().load_project(
        autosave.path / "project.db",
        session.manifest.project_id,
        read_only=True,
    )
    assert autosave_loaded.snapshot == authored
    root = session.root_path

    opener = ProjectService.create_default(
        tmp_path / "recovery-config",
        lathe_persistence_enabled=True,
    )
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(root)
    recovered = opener.recover_project(raised.value.assessment)
    assert recovered.lathe_snapshot == authored
    assert recovered.persisted_lathe_snapshot == authored
    assert not recovered.is_dirty
    assert tuple((root / "backups").iterdir())


def test_feature_off_save_preserves_rows_and_save_as_rebinds_opaquely(tmp_path) -> None:
    owner, session = _new_enabled_service(tmp_path, "FeatureOff")
    authored = _authored_for(session)
    owner.stage_lathe_snapshot(authored)
    owner.save()
    root = session.root_path
    owner.close_project()

    disabled = ProjectService.create_default(
        tmp_path / "disabled-config",
        lathe_persistence_enabled=False,
    )
    feature_off = disabled.open_project(root)
    assert feature_off.lathe_snapshot is None
    feature_off.is_dirty = True
    disabled.save()
    with closing(sqlite3.connect(root / "project.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM lathe_programs").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM lathe_operations").fetchone() == (2,)

    copied = disabled.save_as(tmp_path, "FeatureOff Copy")
    assert copied.lathe_snapshot is None
    disabled.close_project()
    inspector = ProjectService.create_default(
        tmp_path / "inspector-config",
        lathe_persistence_enabled=True,
    )
    inspected = inspector.open_project(copied.root_path)
    assert inspected.lathe_snapshot is not None
    assert inspected.lathe_snapshot.programs[0].identity.project_id == str(
        copied.manifest.project_id
    )
    assert inspected.lathe_snapshot.derived_snapshots == ()


def test_read_only_v5_loads_for_inspection_but_cannot_stage_or_save(tmp_path) -> None:
    owner, session = _new_enabled_service(tmp_path, "ReadOnly")
    authored = _authored_for(session)
    owner.stage_lathe_snapshot(authored)
    owner.save()
    root = session.root_path
    owner.close_project()
    database_path = root / "project.db"
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    reader = ProjectService.create_default(
        tmp_path / "reader-config",
        lathe_persistence_enabled=True,
    )
    read_only = reader.open_project(root, read_only=True)
    assert read_only.read_only
    assert read_only.lathe_snapshot == authored
    with pytest.raises(ProjectError, match="Read-only"):
        reader.stage_lathe_snapshot(authored)
    with pytest.raises(ProjectError, match="Read-only"):
        reader.save()
    with pytest.raises(ProjectError, match="Read-only"):
        reader.autosave()
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before
