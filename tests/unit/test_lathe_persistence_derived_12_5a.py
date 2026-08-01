"""Stage 12.5A five-kind derived-cache acceptance and restore gates."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicPostReadiness
from hms_cadcam.cam.lathe.persistence import (
    LatheCodecError,
    LatheDerivedKind,
    LatheProjectPersistenceService,
    LatheProjectSnapshot,
    LatheRestoreDiagnosticCode,
    LatheSqliteRepository,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.project.database import ProjectDatabase
from tests.unit._lathe_persistence_fixtures import persistence_snapshot


_FINGERPRINT = "a" * 64


def _five_derived(project):  # type: ignore[no-untyped-def]
    service = LatheProjectPersistenceService()
    program = project.programs[0]
    operation = program.operations[0]
    values = (
        service.create_derived_snapshot(
            kind=LatheDerivedKind.ACCEPTED_TOOLPATH,
            program_id=None,
            operation_id=str(operation.ownership.operation_id),
            owner_revision=operation.revision.value,
            schema_version=1,
            algorithm_version="lathe.toolpath.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload={"motions": [], "stable": True, "status": "SUCCESS"},
        ),
        service.create_derived_snapshot(
            kind=LatheDerivedKind.ACCEPTED_PROGRAM_IR,
            program_id=program.identity.program_id,
            operation_id=None,
            owner_revision=program.identity.revision,
            schema_version=1,
            algorithm_version="lathe.program.ir.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload={"blocks": [], "complete": True},
        ),
        service.create_derived_snapshot(
            kind=LatheDerivedKind.NEUTRAL_LISTING,
            program_id=program.identity.program_id,
            operation_id=None,
            owner_revision=program.identity.revision,
            schema_version=1,
            algorithm_version="lathe.neutral.listing.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload={"text": "PROGRAM BEGIN\nPROGRAM END"},
        ),
        service.create_derived_snapshot(
            kind=LatheDerivedKind.BASIC_NC_PREVIEW,
            program_id=program.identity.program_id,
            operation_id=None,
            owner_revision=program.identity.revision,
            schema_version=1,
            algorithm_version="lathe.basic.preview.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload={
                "nc_text": "%\nO0001\nM30\n%",
                "readiness": BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED.value,
            },
        ),
        service.create_derived_snapshot(
            kind=LatheDerivedKind.CONFORMANCE_REVIEW,
            program_id=program.identity.program_id,
            operation_id=None,
            owner_revision=program.identity.revision,
            schema_version=1,
            algorithm_version="lathe.conformance.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload={"findings": [], "status": "CONFORMANT"},
        ),
    )
    return LatheProjectSnapshot(project.programs, values)


def test_exact_five_kinds_persist_and_restore_only_with_complete_tuple(tmp_path) -> None:
    project = _five_derived(
        persistence_snapshot(strategies=(LatheStrategyId.FACE,))
    )
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    repository = LatheSqliteRepository()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, project)
    loaded = repository.load(
        path,
        expected_project_id=project.programs[0].operations[0].ownership.project_id,
    )
    assert loaded.snapshot == project
    assert loaded.diagnostics == ()
    service = LatheProjectPersistenceService(repository)
    for derived in project.derived_snapshots:
        result = service.restore_derived(
            loaded.snapshot,
            kind=derived.kind,
            program_id=derived.program_id,
            operation_id=derived.operation_id,
            owner_revision=derived.owner_revision,
            schema_version=derived.schema_version,
            algorithm_version=derived.algorithm_version,
            dependency_fingerprint=derived.dependency_fingerprint,
        )
        assert result.snapshot == derived
        if derived.kind is LatheDerivedKind.BASIC_NC_PREVIEW:
            assert result.readiness == "BASIC_NC_PREVIEW_READY_UNVERIFIED"


def test_stale_fingerprint_revision_version_and_ownership_drop_only_cache() -> None:
    project = _five_derived(
        persistence_snapshot(strategies=(LatheStrategyId.FACE,))
    )
    service = LatheProjectPersistenceService()
    derived = next(
        item
        for item in project.derived_snapshots
        if item.kind is LatheDerivedKind.ACCEPTED_TOOLPATH
    )
    stale = service.restore_derived(
        project,
        kind=derived.kind,
        program_id=None,
        operation_id=derived.operation_id,
        owner_revision=derived.owner_revision + 1,
        schema_version=derived.schema_version,
        algorithm_version=derived.algorithm_version,
        dependency_fingerprint="b" * 64,
    )
    assert stale.snapshot is None
    assert stale.diagnostics[0].code is LatheRestoreDiagnosticCode.DERIVED_STALE
    version = service.restore_derived(
        project,
        kind=derived.kind,
        program_id=None,
        operation_id=derived.operation_id,
        owner_revision=derived.owner_revision,
        schema_version=2,
        algorithm_version="other.v2",
        dependency_fingerprint=_FINGERPRINT,
    )
    assert version.snapshot is None
    assert version.diagnostics[0].code is LatheRestoreDiagnosticCode.DERIVED_VERSION_MISMATCH
    ownership = service.restore_derived(
        project,
        kind=derived.kind,
        program_id=None,
        operation_id="operation:00000000-0000-4000-8000-000000000099",
        owner_revision=derived.owner_revision,
        schema_version=1,
        algorithm_version=derived.algorithm_version,
        dependency_fingerprint=_FINGERPRINT,
    )
    assert ownership.snapshot is None
    assert ownership.diagnostics[0].code is LatheRestoreDiagnosticCode.DERIVED_OWNERSHIP_MISMATCH
    assert project.programs


def test_corrupt_derived_hash_is_diagnosed_without_losing_authoring(tmp_path) -> None:
    project = _five_derived(
        persistence_snapshot(strategies=(LatheStrategyId.FACE,))
    )
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    repository = LatheSqliteRepository()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, project)
        target = project.derived_snapshots[0].snapshot_id
        connection.execute(
            "UPDATE lathe_derived_snapshots SET content_sha256 = ? "
            "WHERE snapshot_id = ?",
            ("0" * 64, target),
        )
    loaded = repository.load(
        path,
        expected_project_id=project.programs[0].operations[0].ownership.project_id,
    )
    assert loaded.snapshot.programs == project.programs
    assert len(loaded.snapshot.derived_snapshots) == 4
    assert loaded.diagnostics[0].code is LatheRestoreDiagnosticCode.DERIVED_CORRUPT


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (
            LatheDerivedKind.ACCEPTED_TOOLPATH,
            {"motions": [], "stable": False, "status": "FAILED"},
        ),
        (
            LatheDerivedKind.ACCEPTED_PROGRAM_IR,
            {"blocks": [], "complete": False},
        ),
        (
            LatheDerivedKind.BASIC_NC_PREVIEW,
            {"nc_text": "M30", "readiness": "MACHINE_OUTPUT_READY"},
        ),
        (
            LatheDerivedKind.CONFORMANCE_REVIEW,
            {"findings": [], "status": "RUNNING"},
        ),
    ],
)
def test_unstable_partial_machine_ready_and_transient_values_are_rejected(
    kind, payload
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LatheCodecError):
        LatheProjectPersistenceService.create_derived_snapshot(
            kind=kind,
            program_id=None if kind is LatheDerivedKind.ACCEPTED_TOOLPATH else "program",
            operation_id="operation" if kind is LatheDerivedKind.ACCEPTED_TOOLPATH else None,
            owner_revision=0,
            schema_version=1,
            algorithm_version="algorithm.v1",
            dependency_fingerprint=_FINGERPRINT,
            payload=payload,
        )
