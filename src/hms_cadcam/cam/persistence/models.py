"""Persistence snapshots that keep infrastructure outside the CAM domain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hms_cadcam.cam.domain import (
    CamJob, CamJobId, ContentFingerprint, DependencyFingerprint, HolderDefinition,
    MachineDefinition, OperationId, Revision, ToolAssembly, ToolDefinition,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _canonical_json_object(value: object, *, subject: str) -> dict[str, Any]:
    """Copy a JSON object only after proving it has canonical fingerprint input."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CamValidationError(f"{subject} is invalid")
    try:
        # Fingerprinting supplies both the finite-number rule and a fail-closed
        # recursive JSON compatibility check.  Reconstructing through its
        # canonical representation keeps frozen records independent of caller
        # owned nested dictionaries.
        import json

        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=True,
                                    separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError) as error:
        raise CamValidationError(f"{subject} is invalid") from error


@dataclass(frozen=True, slots=True)
class MaterialStateSuccessorPublication:
    """Complete, self-sealed R271 successor publication retained by R272.

    This is intentionally payload evidence, not a MaterialState object or a
    trust token.  Reopen code must still use the MaterialState store decoder to
    reconstruct machining authority.
    """

    consumer_operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    semantic_material_removal_fingerprint: ContentFingerprint
    parent_state_fingerprint: ContentFingerprint
    parent_state_content_seal: ContentFingerprint
    successor_state_fingerprint: ContentFingerprint
    successor_state_content_seal: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    engine_version: str
    precision: dict[str, Any]
    publication_fingerprint: ContentFingerprint
    status: str = "COMPLETE"

    def __post_init__(self) -> None:
        if not isinstance(self.consumer_operation_id, OperationId) or not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Material-state successor publication identity is invalid")
        for value in (
            self.artifact_fingerprint, self.semantic_material_removal_fingerprint,
            self.parent_state_fingerprint, self.parent_state_content_seal,
            self.successor_state_fingerprint, self.successor_state_content_seal,
            self.setup_fingerprint, self.stock_fingerprint, self.publication_fingerprint,
        ):
            if not isinstance(value, ContentFingerprint):
                raise CamValidationError("Material-state successor publication fingerprint is invalid")
        if not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Material-state successor publication input fingerprint is invalid")
        if not isinstance(self.engine_version, str) or not self.engine_version:
            raise CamValidationError("Material-state successor publication engine is invalid")
        if self.status != "COMPLETE":
            raise CamValidationError("Material-state successor publication must be COMPLETE")
        precision = _canonical_json_object(self.precision, subject="Material-state successor publication precision")
        object.__setattr__(self, "precision", precision)
        if self.publication_fingerprint != ContentFingerprint.from_payload(self._fingerprint_payload()):
            raise CamValidationError("Material-state successor publication fingerprint does not match its evidence")

    @classmethod
    def create(
        cls, *, consumer_operation_id: OperationId, artifact_id: ToolpathArtifactId,
        artifact_fingerprint: ContentFingerprint, input_fingerprint: DependencyFingerprint,
        semantic_material_removal_fingerprint: ContentFingerprint,
        parent_state_fingerprint: ContentFingerprint, parent_state_content_seal: ContentFingerprint,
        successor_state_fingerprint: ContentFingerprint, successor_state_content_seal: ContentFingerprint,
        setup_fingerprint: ContentFingerprint, stock_fingerprint: ContentFingerprint,
        engine_version: str, precision: dict[str, Any],
    ) -> "MaterialStateSuccessorPublication":
        """Create the only valid COMPLETE evidence record from explicit facts."""
        canonical_precision = _canonical_json_object(precision, subject="Material-state successor publication precision")
        payload = cls._make_fingerprint_payload(
            consumer_operation_id, artifact_id, artifact_fingerprint, input_fingerprint,
            semantic_material_removal_fingerprint, parent_state_fingerprint,
            parent_state_content_seal, successor_state_fingerprint,
            successor_state_content_seal, setup_fingerprint, stock_fingerprint,
            engine_version, canonical_precision,
        )
        return cls(
            consumer_operation_id, artifact_id, artifact_fingerprint, input_fingerprint,
            semantic_material_removal_fingerprint, parent_state_fingerprint,
            parent_state_content_seal, successor_state_fingerprint,
            successor_state_content_seal, setup_fingerprint, stock_fingerprint,
            engine_version, canonical_precision, ContentFingerprint.from_payload(payload),
        )

    @staticmethod
    def _make_fingerprint_payload(
        consumer_operation_id: OperationId, artifact_id: ToolpathArtifactId,
        artifact_fingerprint: ContentFingerprint, input_fingerprint: DependencyFingerprint,
        semantic_material_removal_fingerprint: ContentFingerprint,
        parent_state_fingerprint: ContentFingerprint, parent_state_content_seal: ContentFingerprint,
        successor_state_fingerprint: ContentFingerprint, successor_state_content_seal: ContentFingerprint,
        setup_fingerprint: ContentFingerprint, stock_fingerprint: ContentFingerprint,
        engine_version: str, precision: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "format": "HMS_CAM_MATERIAL_STATE_SUCCESSOR_PUBLICATION", "format_version": 1,
            "status": "COMPLETE", "consumer_operation_id": str(consumer_operation_id),
            "artifact_id": str(artifact_id), "artifact_fingerprint": artifact_fingerprint.to_dict(),
            "input_fingerprint": input_fingerprint.to_dict(),
            "semantic_material_removal_fingerprint": semantic_material_removal_fingerprint.to_dict(),
            "parent_state_fingerprint": parent_state_fingerprint.to_dict(),
            "parent_state_content_seal": parent_state_content_seal.to_dict(),
            "successor_state_fingerprint": successor_state_fingerprint.to_dict(),
            "successor_state_content_seal": successor_state_content_seal.to_dict(),
            "setup_fingerprint": setup_fingerprint.to_dict(),
            "stock_fingerprint": stock_fingerprint.to_dict(), "engine_version": engine_version,
            "precision": precision,
        }

    def _fingerprint_payload(self) -> dict[str, Any]:
        return self._make_fingerprint_payload(
            self.consumer_operation_id, self.artifact_id, self.artifact_fingerprint,
            self.input_fingerprint, self.semantic_material_removal_fingerprint,
            self.parent_state_fingerprint, self.parent_state_content_seal,
            self.successor_state_fingerprint, self.successor_state_content_seal,
            self.setup_fingerprint, self.stock_fingerprint, self.engine_version, self.precision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self._fingerprint_payload(), "publication_fingerprint": self.publication_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MaterialStateSuccessorPublication":
        fields = {
            "format", "format_version", "status", "consumer_operation_id", "artifact_id",
            "artifact_fingerprint", "input_fingerprint", "semantic_material_removal_fingerprint",
            "parent_state_fingerprint", "parent_state_content_seal", "successor_state_fingerprint",
            "successor_state_content_seal", "setup_fingerprint", "stock_fingerprint",
            "engine_version", "precision", "publication_fingerprint",
        }
        if not isinstance(data, dict) or set(data) != fields or data.get("format") != "HMS_CAM_MATERIAL_STATE_SUCCESSOR_PUBLICATION" or data.get("format_version") != 1:
            raise CamValidationError("Material-state successor publication schema is invalid")
        try:
            return cls(
                OperationId.parse(data["consumer_operation_id"]), ToolpathArtifactId.parse(data["artifact_id"]),
                ContentFingerprint.from_dict(data["artifact_fingerprint"]),
                DependencyFingerprint.from_dict(data["input_fingerprint"]),
                ContentFingerprint.from_dict(data["semantic_material_removal_fingerprint"]),
                ContentFingerprint.from_dict(data["parent_state_fingerprint"]),
                ContentFingerprint.from_dict(data["parent_state_content_seal"]),
                ContentFingerprint.from_dict(data["successor_state_fingerprint"]),
                ContentFingerprint.from_dict(data["successor_state_content_seal"]),
                ContentFingerprint.from_dict(data["setup_fingerprint"]),
                ContentFingerprint.from_dict(data["stock_fingerprint"]), data["engine_version"], data["precision"],
                ContentFingerprint.from_dict(data["publication_fingerprint"]), data["status"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CamValidationError("Material-state successor publication payload is invalid") from error


@dataclass(frozen=True, slots=True)
class MaterialStateDependency:
    """Persisted provenance edge from a Rest consumer to its state producer."""
    consumer_operation_id: OperationId
    producer_operation_id: OperationId
    parent_state_fingerprint: ContentFingerprint
    producer_toolpath_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    engine_version: str
    precision: dict
    successor_publication: MaterialStateSuccessorPublication | None = None
    producer_operation_authority_fingerprint: ContentFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.consumer_operation_id, OperationId) or not isinstance(self.producer_operation_id, OperationId):
            raise CamValidationError("Material-state dependency operation identity is invalid")
        for value in (self.parent_state_fingerprint, self.producer_toolpath_fingerprint,
                      self.setup_fingerprint, self.stock_fingerprint):
            if not isinstance(value, ContentFingerprint):
                raise CamValidationError("Material-state dependency fingerprint is invalid")
        if not isinstance(self.engine_version, str) or not self.engine_version:
            raise CamValidationError("Material-state dependency engine is invalid")
        precision = _canonical_json_object(self.precision, subject="Material-state dependency precision")
        object.__setattr__(self, "precision", precision)
        if self.successor_publication is not None:
            publication = self.successor_publication
            if not isinstance(self.producer_operation_authority_fingerprint, ContentFingerprint):
                raise CamValidationError("Material-state producer operation authority is invalid")
            if not isinstance(publication, MaterialStateSuccessorPublication):
                raise CamValidationError("Material-state successor publication is invalid")
            if publication.consumer_operation_id != self.consumer_operation_id:
                raise CamInvariantError("Material-state successor publication consumer does not match dependency")
            if publication.parent_state_fingerprint != self.parent_state_fingerprint:
                raise CamInvariantError("Material-state successor publication parent does not match dependency")
            if publication.setup_fingerprint != self.setup_fingerprint or publication.stock_fingerprint != self.stock_fingerprint:
                raise CamInvariantError("Material-state successor publication setup/stock does not match dependency")
            if publication.engine_version != self.engine_version or publication.precision != self.precision:
                raise CamInvariantError("Material-state successor publication engine/precision does not match dependency")

    def to_dict(self) -> dict:
        legacy = {"format": "HMS_CAM_MATERIAL_STATE_DEPENDENCY", "format_version": 1,
                "consumer_operation_id": str(self.consumer_operation_id),
                "producer_operation_id": str(self.producer_operation_id),
                "parent_state_fingerprint": self.parent_state_fingerprint.to_dict(),
                "producer_toolpath_fingerprint": self.producer_toolpath_fingerprint.to_dict(),
                "setup_fingerprint": self.setup_fingerprint.to_dict(),
                "stock_fingerprint": self.stock_fingerprint.to_dict(),
                "engine_version": self.engine_version, "precision": _canonical_json_object(self.precision, subject="Material-state dependency precision")}
        if self.successor_publication is None:
            return legacy
        return {
            **legacy,
            "format_version": 2,
            "successor_publication": self.successor_publication.to_dict(),
            "producer_operation_authority_fingerprint": (
                self.producer_operation_authority_fingerprint.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MaterialStateDependency":
        fields = {"format", "format_version", "consumer_operation_id", "producer_operation_id",
                  "parent_state_fingerprint", "producer_toolpath_fingerprint", "setup_fingerprint",
                  "stock_fingerprint", "engine_version", "precision"}
        if not isinstance(data, dict) or data.get("format") != "HMS_CAM_MATERIAL_STATE_DEPENDENCY":
            raise CamValidationError("Material-state dependency schema is invalid")
        version = data.get("format_version")
        if version == 1 and set(data) == fields:
            publication = None
        elif version == 2 and set(data) == fields | {
            "successor_publication", "producer_operation_authority_fingerprint",
        }:
            publication = MaterialStateSuccessorPublication.from_dict(data["successor_publication"])
        else:
            raise CamValidationError("Material-state dependency schema is invalid")
        try:
            return cls(OperationId.parse(data["consumer_operation_id"]), OperationId.parse(data["producer_operation_id"]),
                       ContentFingerprint.from_dict(data["parent_state_fingerprint"]), ContentFingerprint.from_dict(data["producer_toolpath_fingerprint"]),
                       ContentFingerprint.from_dict(data["setup_fingerprint"]), ContentFingerprint.from_dict(data["stock_fingerprint"]),
                       data["engine_version"], data["precision"], publication,
                       (ContentFingerprint.from_dict(data["producer_operation_authority_fingerprint"])
                        if publication is not None else None))
        except (KeyError, TypeError, ValueError) as error:
            raise CamValidationError("Material-state dependency payload is invalid") from error


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
    material_state_dependencies: tuple[MaterialStateDependency, ...] = ()

    def __post_init__(self) -> None:
        typed_groups = (
            (self.jobs, CamJob, "jobs"), (self.tool_definitions, ToolDefinition, "tools"),
            (self.holder_definitions, HolderDefinition, "holders"),
            (self.tool_assemblies, ToolAssembly, "assemblies"),
            (self.machine_definitions, MachineDefinition, "machines"),
            (self.artifacts, ToolpathArtifactMetadata, "artifacts"),
            (self.material_state_dependencies, MaterialStateDependency, "material-state dependencies"),
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
            tuple(
                item.consumer_operation_id
                for item in self.material_state_dependencies
            ),
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
        if any(item.consumer_operation_id not in operation_ids or item.producer_operation_id not in operation_ids
               for item in self.material_state_dependencies):
            raise CamInvariantError("Material-state dependency references an unknown operation")

    @property
    def is_empty(self) -> bool:
        return not any((self.jobs, self.tool_definitions, self.holder_definitions,
                        self.tool_assemblies, self.machine_definitions, self.artifacts))
