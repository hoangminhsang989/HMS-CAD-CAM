"""Persistence snapshots that keep infrastructure outside the CAM domain."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hms_cadcam.cam.domain import (
    CamJob, CamJobId, ContentFingerprint, DependencyFingerprint, HolderDefinition,
    MachineDefinition, OperationId, Revision, ToolAssembly, ToolDefinition,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ToolpathArtifactMetadata:
    artifact_id: ToolpathArtifactId
    operation_id: OperationId
    relative_path: str
    checksum_sha256: str
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    size_bytes: int
    schema_version: int
    expected_operation_revision: Revision
    computation_generation: int
    completion_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, ToolpathArtifactId) or not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Toolpath metadata identity is invalid")
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise CamValidationError("Toolpath metadata path is invalid")
        if not isinstance(self.checksum_sha256, str) or not _SHA256.fullmatch(self.checksum_sha256):
            raise CamValidationError("Toolpath metadata checksum is invalid")
        if not isinstance(self.artifact_fingerprint, ContentFingerprint) or not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Toolpath metadata fingerprint is invalid")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise CamValidationError("Toolpath metadata size is invalid")
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise CamValidationError("Toolpath metadata schema version is invalid")
        if not isinstance(self.expected_operation_revision, Revision):
            raise CamValidationError("Toolpath expected operation revision is invalid")
        if type(self.computation_generation) is not int or self.computation_generation <= 0:
            raise CamValidationError("Toolpath computation generation is invalid")
        if not isinstance(self.completion_status, str) or not self.completion_status:
            raise CamValidationError("Toolpath completion metadata is invalid")


@dataclass(frozen=True, slots=True)
class CamProjectSnapshot:
    jobs: tuple[CamJob, ...] = ()
    active_job_id: CamJobId | None = None
    tool_definitions: tuple[ToolDefinition, ...] = ()
    holder_definitions: tuple[HolderDefinition, ...] = ()
    tool_assemblies: tuple[ToolAssembly, ...] = ()
    machine_definitions: tuple[MachineDefinition, ...] = ()
    artifacts: tuple[ToolpathArtifactMetadata, ...] = ()

    def __post_init__(self) -> None:
        typed_groups = (
            (self.jobs, CamJob, "jobs"), (self.tool_definitions, ToolDefinition, "tools"),
            (self.holder_definitions, HolderDefinition, "holders"),
            (self.tool_assemblies, ToolAssembly, "assemblies"),
            (self.machine_definitions, MachineDefinition, "machines"),
            (self.artifacts, ToolpathArtifactMetadata, "artifacts"),
        )
        for values, expected, name in typed_groups:
            if not isinstance(values, tuple) or any(not isinstance(item, expected) for item in values):
                raise CamValidationError(f"CAM project {name} must be an immutable tuple")
        job_ids = tuple(item.job_id for item in self.jobs)
        if len(set(job_ids)) != len(job_ids):
            raise CamInvariantError("CAM job IDs must be unique in a project")
        if self.active_job_id is not None and self.active_job_id not in job_ids:
            raise CamInvariantError("Active CAM job must belong to project")
        identity_groups = (
            tuple(item.tool_id for item in self.tool_definitions),
            tuple(item.holder_id for item in self.holder_definitions),
            tuple(item.assembly_id for item in self.tool_assemblies),
            tuple(item.machine_id for item in self.machine_definitions),
            tuple(item.artifact_id for item in self.artifacts),
            tuple(item.operation_id for item in self.artifacts),
        )
        if any(len(set(values)) != len(values) for values in identity_groups):
            raise CamInvariantError("CAM project snapshot identities must be unique")
        operation_ids = {
            operation.operation_id
            for job in self.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
        }
        if any(item.operation_id not in operation_ids for item in self.artifacts):
            raise CamInvariantError("Toolpath metadata references an unknown operation")

    @property
    def is_empty(self) -> bool:
        return not any((self.jobs, self.tool_definitions, self.holder_definitions,
                        self.tool_assemblies, self.machine_definitions, self.artifacts))
