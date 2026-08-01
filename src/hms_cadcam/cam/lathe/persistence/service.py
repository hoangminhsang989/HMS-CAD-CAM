"""Application facade for authored Lathe state and derived restore gates."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicPostReadiness
from hms_cadcam.cam.lathe.persistence.codecs import (
    decode_derived_payload,
    encode_derived_payload,
    payload_sha256,
)
from hms_cadcam.cam.lathe.persistence.models import (
    LatheDerivedKind,
    LatheDerivedRestoreResult,
    LatheDerivedSnapshot,
    LatheLoadResult,
    LatheProjectSnapshot,
    LatheRestoreDiagnostic,
    LatheRestoreDiagnosticCode,
)
from hms_cadcam.cam.lathe.persistence.repository import LatheSqliteRepository


class LatheProjectPersistenceService:
    """One Qt-free service boundary used by project lifecycle adapters."""

    def __init__(self, repository: LatheSqliteRepository | None = None) -> None:
        self._repository = repository or LatheSqliteRepository()

    def load_project(
        self,
        database_path: Path,
        project_id: UUID,
        *,
        read_only: bool = False,
    ) -> LatheLoadResult:
        return self._repository.load(
            database_path,
            expected_project_id=project_id,
            read_only=read_only,
        )

    def replace_all(
        self,
        connection: sqlite3.Connection,
        snapshot: LatheProjectSnapshot,
    ) -> LatheProjectSnapshot:
        return self._repository.replace_all(connection, snapshot)

    def rebind_project(
        self,
        connection: sqlite3.Connection,
        old_project_id: UUID,
        new_project_id: UUID,
        *,
        staged: LatheProjectSnapshot | None = None,
    ) -> LatheProjectSnapshot | None:
        return self._repository.rebind_project(
            connection,
            old_project_id,
            new_project_id,
            staged=staged,
        )

    @staticmethod
    def create_derived_snapshot(
        *,
        kind: LatheDerivedKind,
        program_id: str | None,
        operation_id: str | None,
        owner_revision: int,
        schema_version: int,
        algorithm_version: str,
        dependency_fingerprint: str,
        payload: Mapping[str, object],
        snapshot_id: str | None = None,
    ) -> LatheDerivedSnapshot:
        """Create only an accepted, bounded, canonical derived cache value."""

        payload_json = encode_derived_payload(kind, payload)
        return LatheDerivedSnapshot(
            snapshot_id=snapshot_id or str(uuid4()),
            kind=kind,
            program_id=program_id,
            operation_id=operation_id,
            owner_revision=owner_revision,
            schema_version=schema_version,
            algorithm_version=algorithm_version,
            dependency_fingerprint=dependency_fingerprint,
            content_sha256=payload_sha256(payload_json),
            payload_json=payload_json,
        )

    @staticmethod
    def restore_derived(
        project: LatheProjectSnapshot,
        *,
        kind: LatheDerivedKind,
        program_id: str | None,
        operation_id: str | None,
        owner_revision: int,
        schema_version: int,
        algorithm_version: str,
        dependency_fingerprint: str,
    ) -> LatheDerivedRestoreResult:
        """Return a cache only when the complete immutable tuple still matches."""

        if not isinstance(project, LatheProjectSnapshot):
            raise TypeError("project must be LatheProjectSnapshot")
        owner_id = operation_id if kind is LatheDerivedKind.ACCEPTED_TOOLPATH else program_id
        candidates = tuple(
            item for item in project.derived_snapshots if item.kind is kind
        )
        exact_owner = next(
            (
                item
                for item in candidates
                if item.program_id == program_id and item.operation_id == operation_id
            ),
            None,
        )
        if exact_owner is None:
            if candidates and owner_id is not None:
                return LatheDerivedRestoreResult(
                    None,
                    (
                        LatheRestoreDiagnostic(
                            LatheRestoreDiagnosticCode.DERIVED_OWNERSHIP_MISMATCH,
                            owner_id,
                            kind,
                        ),
                    ),
                )
            return LatheDerivedRestoreResult(None)
        if (
            exact_owner.owner_revision != owner_revision
            or exact_owner.dependency_fingerprint != dependency_fingerprint
        ):
            return LatheDerivedRestoreResult(
                None,
                (
                    LatheRestoreDiagnostic(
                        LatheRestoreDiagnosticCode.DERIVED_STALE,
                        exact_owner.snapshot_id,
                        kind,
                    ),
                ),
            )
        if (
            exact_owner.schema_version != schema_version
            or exact_owner.algorithm_version != algorithm_version
        ):
            return LatheDerivedRestoreResult(
                None,
                (
                    LatheRestoreDiagnostic(
                        LatheRestoreDiagnosticCode.DERIVED_VERSION_MISMATCH,
                        exact_owner.snapshot_id,
                        kind,
                    ),
                ),
            )
        payload = decode_derived_payload(kind, exact_owner.payload_json)
        readiness = None
        if kind is LatheDerivedKind.BASIC_NC_PREVIEW:
            readiness = BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED.value
            if payload.get("readiness") != readiness:
                return LatheDerivedRestoreResult(
                    None,
                    (
                        LatheRestoreDiagnostic(
                            LatheRestoreDiagnosticCode.DERIVED_CORRUPT,
                            exact_owner.snapshot_id,
                            kind,
                        ),
                    ),
                )
        return LatheDerivedRestoreResult(exact_owner, readiness=readiness)


__all__ = ["LatheProjectPersistenceService"]
