"""Qt-free immutable values for Lathe project persistence V1."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from hms_cadcam.cam.lathe.domain import LatheOperationState
from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.lathe.lathe_post.basic_types import (
    BasicFinalSafeTool,
    BasicPostMetadata,
    BasicToolMapping,
)
from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity

LATHE_PERSISTENCE_SCHEMA_VERSION = 1
MAX_OPERATIONS_PER_PROGRAM = 1000
MAX_OPERATION_PAYLOAD_BYTES = 1024 * 1024
MAX_GEOMETRY_REFERENCES = 64
MAX_MOTIONS = 200_000
MAX_DERIVED_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_BASIC_NC_TEXT_BYTES = 4 * 1024 * 1024
MAX_CONFORMANCE_FINDINGS = 10_000
MAX_JSON_DEPTH = 32
MAX_SEMANTIC_STRING = 512

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_FILE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _semantic_text(value: object, subject: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_SEMANTIC_STRING:
        raise ValueError(f"{subject} must be non-empty bounded text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{subject} contains a control character")
    return value


def _non_negative(value: object, subject: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{subject} must be a non-negative integer")
    return value


class LatheDerivedKind(StrEnum):
    ACCEPTED_TOOLPATH = "accepted_toolpath"
    ACCEPTED_PROGRAM_IR = "accepted_program_ir"
    NEUTRAL_LISTING = "neutral_listing"
    BASIC_NC_PREVIEW = "basic_nc_preview"
    CONFORMANCE_REVIEW = "conformance_review"


@dataclass(frozen=True, slots=True)
class LathePostConfiguration:
    """Safe Basic Post authoring configuration and typed offset mappings."""

    final_safe_tool: BasicFinalSafeTool = BasicFinalSafeTool()
    metadata: BasicPostMetadata = BasicPostMetadata()
    tool_mappings: tuple[BasicToolMapping, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.final_safe_tool, BasicFinalSafeTool):
            raise TypeError("final_safe_tool is invalid")
        if not isinstance(self.metadata, BasicPostMetadata):
            raise TypeError("Post metadata is invalid")
        if not isinstance(self.tool_mappings, tuple) or any(
            not isinstance(item, BasicToolMapping) for item in self.tool_mappings
        ):
            raise TypeError("Post tool mappings must be an immutable typed tuple")
        tool_ids = tuple(item.tool_id for item in self.tool_mappings)
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("Post tool mappings must have unique tool identities")
        _semantic_text(self.metadata.file_stem, "Post file stem")
        if (
            not _SAFE_FILE_STEM.fullmatch(self.metadata.file_stem)
            or ".." in self.metadata.file_stem
        ):
            raise ValueError("Post file stem is not a safe path-neutral name")
        for tool_id, description in self.metadata.tool_descriptions:
            _semantic_text(tool_id, "Post metadata tool identity")
            if len(description) > MAX_SEMANTIC_STRING or any(
                ord(char) < 32 or ord(char) == 127 for char in description
            ):
                raise ValueError("Post tool description is invalid")
        for mapping in self.tool_mappings:
            _semantic_text(mapping.tool_id, "Post mapping tool identity")
            if len(mapping.description) > MAX_SEMANTIC_STRING or any(
                ord(char) < 32 or ord(char) == 127 for char in mapping.description
            ):
                raise ValueError("Post mapping description is invalid")


@dataclass(frozen=True, slots=True)
class LatheProgramState:
    """One immutable authored Lathe program and exact operation order."""

    identity: LatheProgramIdentity
    display_name: str
    operations: tuple[LatheOperationState, ...]
    selected_post_profile_id: str | None = None
    post_config: LathePostConfiguration = LathePostConfiguration()
    persistence_schema_version: int = LATHE_PERSISTENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LatheProgramIdentity):
            raise TypeError("Lathe program identity is invalid")
        try:
            project_id = UUID(self.identity.project_id)
            source_id = UUID(self.identity.source_id)
            program_id = UUID(self.identity.program_id)
            SetupId.parse(self.identity.setup_id)
        except (TypeError, ValueError) as error:
            raise ValueError("Lathe program identity fields are not canonical") from error
        if project_id.int == 0 or source_id.int == 0 or program_id.int == 0:
            raise ValueError("Lathe program UUID identities must be non-nil")
        _semantic_text(self.identity.document_id, "Lathe document identity")
        _semantic_text(self.display_name, "Lathe program display name")
        _semantic_text(
            self.selected_post_profile_id,
            "selected Post profile identity",
            nullable=True,
        )
        if not isinstance(self.operations, tuple) or any(
            not isinstance(item, LatheOperationState) for item in self.operations
        ):
            raise TypeError("Lathe program operations must be an immutable tuple")
        if len(self.operations) > MAX_OPERATIONS_PER_PROGRAM:
            raise ValueError("Lathe program operation bound exceeded")
        operation_ids = tuple(str(item.ownership.operation_id) for item in self.operations)
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("Lathe program operation identities must be unique")
        for operation in self.operations:
            owner = operation.ownership
            if (
                str(owner.project_id) != self.identity.project_id
                or str(owner.document_id) != self.identity.document_id
                or str(owner.source_id) != self.identity.source_id
                or owner.generation != self.identity.source_generation
                or str(owner.setup_id) != self.identity.setup_id
            ):
                raise ValueError("Lathe operation ownership differs from its program")
        if not isinstance(self.post_config, LathePostConfiguration):
            raise TypeError("Lathe Post configuration is invalid")
        if (
            type(self.persistence_schema_version) is not int
            or self.persistence_schema_version != LATHE_PERSISTENCE_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported Lathe persistence schema version")

    def rebind_project(self, project_id: UUID) -> "LatheProgramState":
        """Return authored state rebound to a new Save As project identity."""

        if not isinstance(project_id, UUID) or project_id.int == 0:
            raise ValueError("Save As project identity is invalid")
        new_project = str(project_id)
        identity = replace(self.identity, project_id=new_project)
        operations = tuple(
            replace(
                operation,
                ownership=replace(operation.ownership, project_id=project_id),
            )
            for operation in self.operations
        )
        return replace(self, identity=identity, operations=operations)


@dataclass(frozen=True, slots=True)
class LatheDerivedSnapshot:
    """One bounded optional derived cache with complete restore identity."""

    snapshot_id: str
    kind: LatheDerivedKind
    program_id: str | None
    operation_id: str | None
    owner_revision: int
    schema_version: int
    algorithm_version: str
    dependency_fingerprint: str
    content_sha256: str
    payload_json: str

    def __post_init__(self) -> None:
        _semantic_text(self.snapshot_id, "derived snapshot identity")
        if not isinstance(self.kind, LatheDerivedKind):
            raise TypeError("Lathe derived kind is invalid")
        _semantic_text(self.program_id, "derived program identity", nullable=True)
        _semantic_text(self.operation_id, "derived operation identity", nullable=True)
        if self.kind is LatheDerivedKind.ACCEPTED_TOOLPATH:
            if self.operation_id is None or self.program_id is not None:
                raise ValueError("Accepted toolpath must have one operation owner")
        elif self.program_id is None or self.operation_id is not None:
            raise ValueError("Program-derived snapshot must have one program owner")
        _non_negative(self.owner_revision, "derived owner revision")
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise ValueError("derived schema version must be positive")
        _semantic_text(self.algorithm_version, "derived algorithm version")
        if not isinstance(self.dependency_fingerprint, str) or not _SHA256.fullmatch(
            self.dependency_fingerprint
        ):
            raise ValueError("derived dependency fingerprint must be SHA-256")
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(
            self.content_sha256
        ):
            raise ValueError("derived content hash must be SHA-256")
        if not isinstance(self.payload_json, str):
            raise TypeError("derived payload must be canonical JSON text")
        if len(self.payload_json.encode("utf-8")) > MAX_DERIVED_PAYLOAD_BYTES:
            raise ValueError("derived payload bound exceeded")


@dataclass(frozen=True, slots=True)
class LatheProjectSnapshot:
    """Complete immutable authored state plus optional valid derived caches."""

    programs: tuple[LatheProgramState, ...] = ()
    derived_snapshots: tuple[LatheDerivedSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.programs, tuple) or any(
            not isinstance(item, LatheProgramState) for item in self.programs
        ):
            raise TypeError("Lathe programs must be an immutable typed tuple")
        object.__setattr__(
            self,
            "programs",
            tuple(
                sorted(
                    self.programs,
                    key=lambda item: (
                        item.identity.project_id,
                        item.identity.document_id,
                        item.identity.source_id,
                        item.identity.setup_id,
                        item.identity.program_id,
                    ),
                )
            ),
        )
        program_ids = tuple(item.identity.program_id for item in self.programs)
        owners = tuple(
            (
                item.identity.project_id,
                item.identity.document_id,
                item.identity.source_id,
                item.identity.setup_id,
            )
            for item in self.programs
        )
        if len(set(program_ids)) != len(program_ids) or len(set(owners)) != len(owners):
            raise ValueError("Lathe program identities and owners must be unique")
        if not isinstance(self.derived_snapshots, tuple) or any(
            not isinstance(item, LatheDerivedSnapshot)
            for item in self.derived_snapshots
        ):
            raise TypeError("Lathe derived snapshots must be an immutable typed tuple")
        object.__setattr__(
            self,
            "derived_snapshots",
            tuple(
                sorted(
                    self.derived_snapshots,
                    key=lambda item: (
                        item.kind.value,
                        item.program_id or item.operation_id or "",
                        item.snapshot_id,
                    ),
                )
            ),
        )
        operation_ids = {
            str(operation.ownership.operation_id)
            for program in self.programs
            for operation in program.operations
        }
        program_id_set = set(program_ids)
        cache_keys: set[tuple[LatheDerivedKind, str]] = set()
        for snapshot in self.derived_snapshots:
            owner_id = snapshot.operation_id or snapshot.program_id
            assert owner_id is not None
            if snapshot.operation_id is not None and snapshot.operation_id not in operation_ids:
                raise ValueError("Derived operation owner is not authored")
            if snapshot.program_id is not None and snapshot.program_id not in program_id_set:
                raise ValueError("Derived program owner is not authored")
            key = (snapshot.kind, owner_id)
            if key in cache_keys:
                raise ValueError("Derived owner/kind must be unique")
            cache_keys.add(key)

    def rebind_project(self, project_id: UUID) -> "LatheProjectSnapshot":
        """Rebind authoring for Save As and drop ownership-sensitive caches."""

        return LatheProjectSnapshot(
            tuple(program.rebind_project(project_id) for program in self.programs),
            (),
        )


class LatheRestoreDiagnosticCode(StrEnum):
    DERIVED_CORRUPT = "lathe.persistence.derived_corrupt"
    DERIVED_STALE = "lathe.persistence.derived_stale"
    DERIVED_OWNERSHIP_MISMATCH = "lathe.persistence.derived_ownership_mismatch"
    DERIVED_VERSION_MISMATCH = "lathe.persistence.derived_version_mismatch"
    AUTHORING_INCOMPATIBLE = "lathe.persistence.authoring_incompatible"


@dataclass(frozen=True, slots=True)
class LatheRestoreDiagnostic:
    code: LatheRestoreDiagnosticCode
    subject_id: str
    kind: LatheDerivedKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, LatheRestoreDiagnosticCode):
            raise TypeError("Lathe restore diagnostic code is invalid")
        _semantic_text(self.subject_id, "Lathe diagnostic subject")
        if self.kind is not None and not isinstance(self.kind, LatheDerivedKind):
            raise TypeError("Lathe diagnostic kind is invalid")


@dataclass(frozen=True, slots=True)
class LatheLoadResult:
    snapshot: LatheProjectSnapshot
    diagnostics: tuple[LatheRestoreDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class LatheDerivedRestoreResult:
    snapshot: LatheDerivedSnapshot | None
    diagnostics: tuple[LatheRestoreDiagnostic, ...] = ()
    readiness: str | None = None


__all__ = [
    "LATHE_PERSISTENCE_SCHEMA_VERSION",
    "MAX_BASIC_NC_TEXT_BYTES",
    "MAX_CONFORMANCE_FINDINGS",
    "MAX_DERIVED_PAYLOAD_BYTES",
    "MAX_GEOMETRY_REFERENCES",
    "MAX_JSON_DEPTH",
    "MAX_MOTIONS",
    "MAX_OPERATIONS_PER_PROGRAM",
    "MAX_OPERATION_PAYLOAD_BYTES",
    "MAX_SEMANTIC_STRING",
    "LatheDerivedKind",
    "LatheDerivedRestoreResult",
    "LatheDerivedSnapshot",
    "LatheLoadResult",
    "LathePostConfiguration",
    "LatheProgramState",
    "LatheProjectSnapshot",
    "LatheRestoreDiagnostic",
    "LatheRestoreDiagnosticCode",
]
