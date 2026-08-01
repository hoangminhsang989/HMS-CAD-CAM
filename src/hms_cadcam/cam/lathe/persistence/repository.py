"""Parameterized SQLite repository for normalized Lathe persistence V1."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.persistence.codecs import (
    LatheCodecError,
    decode_derived_payload,
    decode_operation,
    decode_post_configuration,
    encode_operation,
    encode_post_configuration,
    payload_sha256,
)
from hms_cadcam.cam.lathe.persistence.models import (
    LATHE_PERSISTENCE_SCHEMA_VERSION,
    LatheDerivedKind,
    LatheDerivedSnapshot,
    LatheLoadResult,
    LatheProgramState,
    LatheProjectSnapshot,
    LatheRestoreDiagnostic,
    LatheRestoreDiagnosticCode,
)
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition


class LathePersistenceError(RuntimeError):
    """Base fail-closed persistence error."""


class LatheAuthoringCorruptError(LathePersistenceError):
    """Raised when normalized authored state cannot be restored exactly."""


class LatheSqliteRepository:
    """Load or replace Lathe rows through one caller-owned SQLite boundary."""

    def load(
        self,
        database_path: Path,
        *,
        expected_project_id: UUID | None = None,
        read_only: bool = False,
    ) -> LatheLoadResult:
        connector = self._connect_read_only if read_only else self._connect
        try:
            with closing(connector(database_path)) as connection:
                return self.load_connection(
                    connection,
                    expected_project_id=expected_project_id,
                )
        except sqlite3.Error as error:
            raise LathePersistenceError(str(error)) from error

    def load_connection(
        self,
        connection: sqlite3.Connection,
        *,
        expected_project_id: UUID | None = None,
    ) -> LatheLoadResult:
        if not self._table_exists(connection, "lathe_programs"):
            return LatheLoadResult(LatheProjectSnapshot())
        expected = None if expected_project_id is None else str(expected_project_id)
        try:
            program_rows = connection.execute(
                "SELECT program_id, project_id, document_id, source_id, setup_id, "
                "source_generation, revision, display_name, operation_count, "
                "selected_post_profile_id, post_config_json, "
                "persistence_schema_version FROM lathe_programs "
                "ORDER BY project_id, document_id, source_id, setup_id, program_id"
            ).fetchall()
            binding_rows = {
                str(row[0]): row
                for row in connection.execute(
                    "SELECT operation_id, tool_id, profile_id, assembly_id, "
                    "capability_id, binding_revision FROM lathe_tool_bindings"
                ).fetchall()
            }
            programs: list[LatheProgramState] = []
            consumed_bindings: set[str] = set()
            for row in program_rows:
                program = self._decode_program(
                    connection,
                    row,
                    binding_rows,
                    consumed_bindings,
                    expected,
                )
                programs.append(program)
            if set(binding_rows) != consumed_bindings:
                raise LatheAuthoringCorruptError(
                    "Lathe binding ownership is incomplete"
                )
        except LatheAuthoringCorruptError:
            raise
        except (IndexError, KeyError, TypeError, ValueError, LatheCodecError) as error:
            raise LatheAuthoringCorruptError(
                "Lathe authored state is corrupt or incompatible"
            ) from error

        valid_derived: list[LatheDerivedSnapshot] = []
        diagnostics: list[LatheRestoreDiagnostic] = []
        authored_program_ids = {program.identity.program_id for program in programs}
        authored_operation_ids = {
            str(operation.ownership.operation_id)
            for program in programs
            for operation in program.operations
        }
        derived_rows = connection.execute(
            "SELECT snapshot_id, kind, program_id, operation_id, owner_revision, "
            "schema_version, algorithm_version, dependency_fingerprint, "
            "content_sha256, payload_json FROM lathe_derived_snapshots "
            "ORDER BY kind, COALESCE(program_id, operation_id), snapshot_id"
        ).fetchall()
        for row in derived_rows:
            raw_subject = str(row[0])
            subject = (
                raw_subject
                if raw_subject and len(raw_subject) <= 512
                else "UNKNOWN_DERIVED_SNAPSHOT"
            )
            kind: LatheDerivedKind | None = None
            try:
                kind = LatheDerivedKind(row[1])
                snapshot = LatheDerivedSnapshot(
                    snapshot_id=subject,
                    kind=kind,
                    program_id=row[2],
                    operation_id=row[3],
                    owner_revision=row[4],
                    schema_version=row[5],
                    algorithm_version=row[6],
                    dependency_fingerprint=row[7],
                    content_sha256=row[8],
                    payload_json=row[9],
                )
                if payload_sha256(snapshot.payload_json) != snapshot.content_sha256:
                    raise LatheCodecError("Derived content hash mismatch")
                decode_derived_payload(kind, snapshot.payload_json)
                if (
                    snapshot.program_id is not None
                    and snapshot.program_id not in authored_program_ids
                ) or (
                    snapshot.operation_id is not None
                    and snapshot.operation_id not in authored_operation_ids
                ):
                    raise LatheCodecError("Derived owner is not authored")
                valid_derived.append(snapshot)
            except (TypeError, ValueError, LatheCodecError):
                diagnostics.append(
                    LatheRestoreDiagnostic(
                        LatheRestoreDiagnosticCode.DERIVED_CORRUPT,
                        subject,
                        kind,
                    )
                )
        return LatheLoadResult(
            LatheProjectSnapshot(tuple(programs), tuple(valid_derived)),
            tuple(diagnostics),
        )

    def _decode_program(
        self,
        connection: sqlite3.Connection,
        row: tuple[object, ...],
        binding_rows: dict[str, tuple[object, ...]],
        consumed_bindings: set[str],
        expected_project_id: str | None,
    ) -> LatheProgramState:
        program_id = str(row[0])
        project_id = str(row[1])
        if expected_project_id is not None and project_id != expected_project_id:
            raise LatheAuthoringCorruptError("Lathe project ownership mismatch")
        if row[11] != LATHE_PERSISTENCE_SCHEMA_VERSION:
            raise LatheAuthoringCorruptError("Unsupported Lathe authoring schema")
        operation_rows = connection.execute(
            "SELECT operation_id, position, strategy_id, revision, enabled, "
            "payload_json, parameters_schema_version FROM lathe_operations "
            "WHERE program_id = ? ORDER BY position",
            (program_id,),
        ).fetchall()
        if len(operation_rows) != row[8] or tuple(
            operation_row[1] for operation_row in operation_rows
        ) != tuple(range(len(operation_rows))):
            raise LatheAuthoringCorruptError("Lathe operation order is corrupt")
        operations = []
        for operation_row in operation_rows:
            if operation_row[6] != 1:
                raise LatheAuthoringCorruptError("Unsupported parameter schema")
            operation = decode_operation(operation_row[5])
            operation_id = str(operation.ownership.operation_id)
            if (
                operation_id != operation_row[0]
                or operation.strategy_id.value != operation_row[2]
                or operation.revision.value != operation_row[3]
                or int(operation.enabled) != operation_row[4]
            ):
                raise LatheAuthoringCorruptError(
                    "Lathe normalized operation columns differ from payload"
                )
            binding = operation.tool_binding
            binding_row = binding_rows.get(operation_id)
            if binding is None:
                if binding_row is not None:
                    raise LatheAuthoringCorruptError(
                        "Unexpected Lathe normalized tool binding"
                    )
            else:
                if binding_row is None:
                    raise LatheAuthoringCorruptError(
                        "Required Lathe tool identity is missing"
                    )
                required_capability = next(
                    iter(
                        lathe_strategy_definition(
                            operation.strategy_id
                        ).required_tool_capabilities
                    )
                ).value
                binding_revision = max(
                    binding.tool_revision.value,
                    binding.assembly_revision.value,
                    (
                        binding.profile_revision.value
                        if binding.profile_revision is not None
                        else 0
                    ),
                )
                expected_binding = (
                    operation_id,
                    str(binding.tool_id),
                    None if binding.profile_id is None else str(binding.profile_id),
                    str(binding.assembly_id),
                    required_capability,
                    binding_revision,
                )
                if tuple(binding_row) != expected_binding:
                    raise LatheAuthoringCorruptError(
                        "Lathe normalized tool binding differs from payload"
                    )
                consumed_bindings.add(operation_id)
            operations.append(operation)
        identity = LatheProgramIdentity(
            project_id=project_id,
            document_id=row[2],
            source_id=row[3],
            source_generation=row[5],
            setup_id=row[4],
            program_id=program_id,
            revision=row[6],
        )
        return LatheProgramState(
            identity=identity,
            display_name=row[7],
            operations=tuple(operations),
            selected_post_profile_id=row[9],
            post_config=decode_post_configuration(row[10]),
            persistence_schema_version=row[11],
        )

    def replace_all(
        self,
        connection: sqlite3.Connection,
        snapshot: LatheProjectSnapshot,
    ) -> LatheProjectSnapshot:
        """Replace all staged Lathe state inside the caller transaction."""

        if not isinstance(snapshot, LatheProjectSnapshot):
            raise TypeError("snapshot must be LatheProjectSnapshot")
        connection.execute("DELETE FROM lathe_programs")
        for program in snapshot.programs:
            post_config_json = encode_post_configuration(program.post_config)
            connection.execute(
                "INSERT INTO lathe_programs(program_id, project_id, document_id, "
                "source_id, setup_id, source_generation, revision, display_name, "
                "operation_count, selected_post_profile_id, post_config_json, "
                "persistence_schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    program.identity.program_id,
                    program.identity.project_id,
                    program.identity.document_id,
                    program.identity.source_id,
                    program.identity.setup_id,
                    program.identity.source_generation,
                    program.identity.revision,
                    program.display_name,
                    len(program.operations),
                    program.selected_post_profile_id,
                    post_config_json,
                    program.persistence_schema_version,
                ),
            )
            for position, operation in enumerate(program.operations):
                payload = encode_operation(operation)
                operation_id = str(operation.ownership.operation_id)
                connection.execute(
                    "INSERT INTO lathe_operations(operation_id, program_id, "
                    "position, strategy_id, revision, enabled, payload_json, "
                    "parameters_schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        program.identity.program_id,
                        position,
                        operation.strategy_id.value,
                        operation.revision.value,
                        int(operation.enabled),
                        payload,
                        1,
                    ),
                )
                binding = operation.tool_binding
                if binding is not None:
                    capability = next(
                        iter(
                            lathe_strategy_definition(
                                operation.strategy_id
                            ).required_tool_capabilities
                        )
                    ).value
                    binding_revision = max(
                        binding.tool_revision.value,
                        binding.assembly_revision.value,
                        (
                            binding.profile_revision.value
                            if binding.profile_revision is not None
                            else 0
                        ),
                    )
                    connection.execute(
                        "INSERT INTO lathe_tool_bindings(operation_id, tool_id, "
                        "profile_id, assembly_id, capability_id, binding_revision) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            operation_id,
                            str(binding.tool_id),
                            None
                            if binding.profile_id is None
                            else str(binding.profile_id),
                            str(binding.assembly_id),
                            capability,
                            binding_revision,
                        ),
                    )
        for derived in snapshot.derived_snapshots:
            if payload_sha256(derived.payload_json) != derived.content_sha256:
                raise LathePersistenceError("Derived content hash mismatch")
            decode_derived_payload(derived.kind, derived.payload_json)
            connection.execute(
                "INSERT INTO lathe_derived_snapshots(snapshot_id, kind, "
                "program_id, operation_id, owner_revision, schema_version, "
                "algorithm_version, dependency_fingerprint, content_sha256, "
                "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    derived.snapshot_id,
                    derived.kind.value,
                    derived.program_id,
                    derived.operation_id,
                    derived.owner_revision,
                    derived.schema_version,
                    derived.algorithm_version,
                    derived.dependency_fingerprint,
                    derived.content_sha256,
                    derived.payload_json,
                ),
            )
        return snapshot

    def rebind_project(
        self,
        connection: sqlite3.Connection,
        old_project_id: UUID,
        new_project_id: UUID,
        *,
        staged: LatheProjectSnapshot | None = None,
    ) -> LatheProjectSnapshot | None:
        """Rebind authored ownership on Save As and always drop derived caches."""

        if not self._table_exists(connection, "lathe_programs"):
            return None
        source = staged
        if source is None:
            source = self.load_connection(
                connection,
                expected_project_id=old_project_id,
            ).snapshot
        rebound = source.rebind_project(new_project_id)
        self.replace_all(connection, rebound)
        return rebound

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
                ("table", name),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(database_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _connect_read_only(database_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


__all__ = [
    "LatheAuthoringCorruptError",
    "LathePersistenceError",
    "LatheSqliteRepository",
]
