"""SQLite v4 repository for editable CAM aggregates and artifact metadata."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from hms_cadcam.cam.domain import (
    ArtifactStatus, CamJob, CamJobId, CamNode, CamNodeId, ContentFingerprint, DependencyEdge,
    DependencyFingerprint, DependencyGraph, DiagnosticCode, DiagnosticSeverity, DirtyReason, HolderDefinition,
    MachineDefinition, Operation, OperationId, OperationTree, Revision, Setup,
    SetupId, ToolAssembly, ToolDefinition, ToolpathArtifactId, ValidationDiagnostic,
)
from hms_cadcam.cam.persistence.codecs import decode_json, encode_json
from hms_cadcam.cam.persistence.errors import CamPersistenceError, CamPersistencePayloadError
from hms_cadcam.cam.persistence.models import CamProjectSnapshot, ToolpathArtifactMetadata


def normalize_restart_snapshot(snapshot: CamProjectSnapshot) -> CamProjectSnapshot:
    """Turn non-resumable COMPUTING states into DIRTY snapshots without tokens."""
    jobs: list[CamJob] = []
    for job in snapshot.jobs:
        setups: list[Setup] = []
        for setup in job.setups:
            operations = tuple(_normalize_operation(item) for item in setup.operation_tree.operations)
            tree = OperationTree(setup.setup_id, setup.operation_tree.root_id,
                setup.operation_tree.nodes, operations, setup.operation_tree.dependency_graph,
                setup.operation_tree.revision)
            setups.append(replace(setup, operation_tree=tree))
        jobs.append(CamJob(job.job_id, job.name, revision=job.revision,
                           setups=tuple(setups), active_setup_id=job.active_setup_id))
    return CamProjectSnapshot(tuple(jobs), snapshot.active_job_id, snapshot.tool_definitions,
        snapshot.holder_definitions, snapshot.tool_assemblies, snapshot.machine_definitions,
        snapshot.artifacts)


def _normalize_operation(operation: Operation) -> Operation:
    if operation.artifact_state.status is not ArtifactStatus.COMPUTING:
        return operation
    diagnostic = ValidationDiagnostic(DiagnosticSeverity.WARNING, DiagnosticCode.COMPUTATION_INTERRUPTED,
        "Interrupted computation cannot resume after project persistence")
    diagnostics = operation.diagnostics
    if not any(item.code is DiagnosticCode.COMPUTATION_INTERRUPTED for item in diagnostics):
        diagnostics = (*diagnostics, diagnostic)
    return replace(operation,
        artifact_state=operation.artifact_state.mark_dirty(DirtyReason.UPSTREAM_CHANGED),
        diagnostics=diagnostics)


class CamSqliteRepository:
    """Replace/load one complete CAM persistence snapshot transactionally."""

    def load(self, database_path: Path) -> CamProjectSnapshot:
        try:
            connection = sqlite3.connect(database_path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                return self.load_connection(connection)
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise CamPersistenceError("CAM SQLite load failed") from error
        except CamPersistenceError:
            raise
        except Exception as error:
            raise CamPersistencePayloadError("CAM aggregate payload is invalid") from error

    def load_connection(self, connection: sqlite3.Connection) -> CamProjectSnapshot:
        """Load all-or-nothing from an already migrated project connection."""
        try:
            state = connection.execute("SELECT active_job_id FROM cam_project_state WHERE singleton_id=1").fetchone()
            jobs: list[CamJob] = []
            for job_row in connection.execute("SELECT * FROM cam_jobs ORDER BY position"):
                setups: list[Setup] = []
                for setup_row in connection.execute("SELECT * FROM cam_setups WHERE job_id=? ORDER BY position", (job_row["job_id"],)):
                    setup_id = SetupId.parse(setup_row["setup_id"])
                    nodes = tuple(CamNode.from_dict(decode_json(row["payload_json"])) for row in
                        connection.execute("SELECT payload_json FROM cam_nodes WHERE setup_id=? ORDER BY position", (str(setup_id),)))
                    operations = tuple(_normalize_operation(Operation.from_dict(decode_json(row["payload_json"]))) for row in
                        connection.execute("SELECT payload_json FROM cam_operations WHERE setup_id=? ORDER BY position", (str(setup_id),)))
                    edges = tuple(DependencyEdge.from_dict(decode_json(row["payload_json"])) for row in
                        connection.execute("SELECT payload_json FROM cam_dependencies WHERE setup_id=? ORDER BY position", (str(setup_id),)))
                    graph = DependencyGraph(tuple(item.operation_id for item in operations), edges)
                    tree = OperationTree(setup_id, CamNodeId.parse(setup_row["tree_root_id"]), nodes,
                        operations, graph, Revision.from_dict(decode_json(setup_row["tree_revision_json"])))
                    setup_payload = decode_json(setup_row["payload_json"])
                    setup_payload["operation_tree"] = tree.to_dict()
                    setups.append(Setup.from_dict(setup_payload))
                jobs.append(CamJob(CamJobId.parse(job_row["job_id"]), job_row["name"],
                    revision=Revision.from_dict(decode_json(job_row["revision_json"])),
                    setups=tuple(setups), active_setup_id=SetupId.parse(job_row["active_setup_id"]) if job_row["active_setup_id"] else None))
            active = CamJobId.parse(state["active_job_id"]) if state and state["active_job_id"] else None
            tools = self._load_payloads(connection, "cam_tool_definitions", ToolDefinition.from_dict)
            holders = self._load_payloads(connection, "cam_holder_definitions", HolderDefinition.from_dict)
            assemblies = self._load_payloads(connection, "cam_tool_assemblies", ToolAssembly.from_dict)
            machines = self._load_payloads(connection, "cam_machine_definitions", MachineDefinition.from_dict)
            artifacts = tuple(self._metadata_from_row(row) for row in
                connection.execute("SELECT * FROM toolpath_artifacts ORDER BY operation_id"))
            return normalize_restart_snapshot(CamProjectSnapshot(tuple(jobs), active, tools, holders,
                assemblies, machines, artifacts))
        except sqlite3.Error as error:
            raise CamPersistenceError("CAM SQLite load failed") from error
        except CamPersistenceError:
            raise
        except Exception as error:
            raise CamPersistencePayloadError("CAM aggregate payload is invalid") from error

    @staticmethod
    def _load_payloads(connection: sqlite3.Connection, table: str, decoder):
        return tuple(decoder(decode_json(row["payload_json"])) for row in
                     connection.execute(f"SELECT payload_json FROM {table} ORDER BY position"))

    def replace_all(self, connection: sqlite3.Connection, snapshot: CamProjectSnapshot) -> CamProjectSnapshot:
        """Replace editable CAM state and metadata in the caller's transaction."""
        normalized = normalize_restart_snapshot(snapshot)
        for table in ("toolpath_artifacts", "cam_dependencies", "cam_operations", "cam_nodes",
                      "cam_setups", "cam_jobs", "cam_project_state", "cam_tool_definitions",
                      "cam_holder_definitions", "cam_tool_assemblies", "cam_machine_definitions"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute("INSERT INTO cam_project_state(singleton_id, active_job_id) VALUES(1, ?)",
                           (str(normalized.active_job_id) if normalized.active_job_id else None,))
        for job_position, job in enumerate(normalized.jobs):
            connection.execute("INSERT INTO cam_jobs VALUES(?,?,?,?,?)", (str(job.job_id), job_position,
                job.name, encode_json(job.revision.to_dict()), str(job.active_setup_id) if job.active_setup_id else None))
            for setup_position, setup in enumerate(job.setups):
                tree = setup.operation_tree
                payload = setup.to_dict()
                payload.pop("operation_tree")
                connection.execute("INSERT INTO cam_setups VALUES(?,?,?,?,?,?)", (str(setup.setup_id), str(job.job_id),
                    setup_position, encode_json(payload), str(tree.root_id), encode_json(tree.revision.to_dict())))
                for position, node in enumerate(tree.nodes):
                    connection.execute("INSERT INTO cam_nodes VALUES(?,?,?,?)",
                        (str(node.node_id), str(setup.setup_id), position, encode_json(node.to_dict())))
                for position, operation in enumerate(tree.operations):
                    connection.execute("INSERT INTO cam_operations VALUES(?,?,?,?)",
                        (str(operation.operation_id), str(setup.setup_id), position, encode_json(operation.to_dict())))
                for position, edge in enumerate(tree.dependency_graph.edges):
                    connection.execute("INSERT INTO cam_dependencies VALUES(?,?,?)",
                        (str(setup.setup_id), position, encode_json(edge.to_dict())))
        self._write_payloads(connection, "cam_tool_definitions", "definition_id", normalized.tool_definitions,
                             lambda item: str(item.tool_id))
        self._write_payloads(connection, "cam_holder_definitions", "definition_id", normalized.holder_definitions,
                             lambda item: str(item.holder_id))
        self._write_payloads(connection, "cam_tool_assemblies", "assembly_id", normalized.tool_assemblies,
                             lambda item: str(item.assembly_id))
        self._write_payloads(connection, "cam_machine_definitions", "machine_id", normalized.machine_definitions,
                             lambda item: str(item.machine_id))
        for metadata in normalized.artifacts:
            connection.execute("INSERT INTO toolpath_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                str(metadata.artifact_id), str(metadata.operation_id), metadata.relative_path,
                metadata.checksum_sha256, encode_json(metadata.artifact_fingerprint.to_dict()),
                encode_json(metadata.input_fingerprint.to_dict()), metadata.size_bytes, metadata.schema_version,
                encode_json(metadata.expected_operation_revision.to_dict()), metadata.computation_generation,
                metadata.completion_status))
        return normalized

    @staticmethod
    def _write_payloads(connection, table: str, id_column: str, values: tuple, identity) -> None:
        for position, value in enumerate(values):
            connection.execute(f"INSERT INTO {table}({id_column},position,payload_json) VALUES(?,?,?)",
                               (identity(value), position, encode_json(value.to_dict())))

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> ToolpathArtifactMetadata:
        return ToolpathArtifactMetadata(ToolpathArtifactId.parse(row["artifact_id"]), OperationId.parse(row["operation_id"]),
            row["relative_path"], row["checksum_sha256"], ContentFingerprint.from_dict(decode_json(row["artifact_fingerprint_json"])),
            DependencyFingerprint.from_dict(decode_json(row["input_fingerprint_json"])), row["size_bytes"],
            row["artifact_schema_version"], Revision.from_dict(decode_json(row["expected_operation_revision_json"])),
            row["computation_generation"], row["completion_status"])
