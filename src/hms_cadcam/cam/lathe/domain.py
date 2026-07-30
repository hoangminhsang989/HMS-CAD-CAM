"""Immutable Lathe ownership, bindings, aggregate, readiness, and snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import (
    OperationId,
    SetupId,
    ToolAssemblyId,
    ToolDefinitionId,
    ToolProgramProfileId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolution
from hms_cadcam.cam.lathe.parameters import (
    LatheParameterState,
    decode_canonical_parameter_values,
)
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.types import (
    LatheDiagnostic,
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheOperationReadiness,
    LatheStrategyId,
    LatheToolCapability,
    ordered_lathe_diagnostics,
)

LATHE_SNAPSHOT_SCHEMA_VERSION = "lathe.foundation.snapshot.v1"


def _non_nil_uuid(value: object, subject: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{subject} must be a non-nil UUID")
    return value


def _generation(value: object, subject: str = "generation") -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{subject} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class LatheOwnershipKey:
    """Exact immutable ownership for one operation in one live context."""

    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    generation: int
    setup_id: SetupId
    operation_id: OperationId

    def __post_init__(self) -> None:
        _non_nil_uuid(self.project_id, "project_id")
        if not isinstance(self.document_id, CadDocumentId) or not str(
            self.document_id
        ).strip():
            raise ValueError("document_id must be a non-blank CadDocumentId")
        _non_nil_uuid(self.source_id, "source_id")
        _generation(self.generation)
        if not isinstance(self.setup_id, SetupId):
            raise TypeError("setup_id must be SetupId")
        if not isinstance(self.operation_id, OperationId):
            raise TypeError("operation_id must be OperationId")


@dataclass(frozen=True, slots=True)
class LatheGeometryBinding:
    """Ordered kernel-neutral geometry identity, never a viewport/OCP object."""

    kind: LatheGeometryKind
    entity_ids: tuple[str, ...]
    source_id: UUID
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LatheGeometryKind):
            raise TypeError("Lathe geometry kind is invalid")
        if not isinstance(self.entity_ids, tuple) or not self.entity_ids:
            raise ValueError("Lathe geometry entity_ids must be a non-empty tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.entity_ids):
            raise ValueError("Lathe geometry entity IDs must be non-blank strings")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("Lathe geometry entity IDs must be unique")
        _non_nil_uuid(self.source_id, "geometry source_id")
        _generation(self.generation, "geometry generation")


@dataclass(frozen=True, slots=True)
class LatheToolBinding:
    """Resolved canonical Tool/Profile/Assembly reference snapshot."""

    tool_id: ToolDefinitionId
    profile_id: ToolProgramProfileId | None
    assembly_id: ToolAssemblyId
    resolved_capabilities: frozenset[LatheToolCapability]
    tool_revision: Revision
    profile_revision: Revision | None
    assembly_revision: Revision

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, ToolDefinitionId):
            raise TypeError("Lathe tool binding tool_id is invalid")
        if self.profile_id is not None and not isinstance(
            self.profile_id, ToolProgramProfileId
        ):
            raise TypeError("Lathe tool binding profile_id is invalid")
        if not isinstance(self.assembly_id, ToolAssemblyId):
            raise TypeError("Lathe tool binding assembly_id is invalid")
        if not isinstance(self.resolved_capabilities, frozenset) or any(
            not isinstance(item, LatheToolCapability)
            for item in self.resolved_capabilities
        ):
            raise TypeError("Lathe resolved capabilities must be a typed frozenset")
        if not isinstance(self.tool_revision, Revision) or not isinstance(
            self.assembly_revision, Revision
        ):
            raise TypeError("Lathe Tool/Assembly revisions are invalid")
        if self.profile_id is None and self.profile_revision is not None:
            raise ValueError("Lathe profile revision requires profile identity")
        if self.profile_id is not None and not isinstance(
            self.profile_revision, Revision
        ):
            raise ValueError("Lathe profile identity requires profile revision")

    @classmethod
    def from_resolution(
        cls, resolution: LatheToolCapabilityResolution
    ) -> "LatheToolBinding":
        """Create a binding only from complete, current resolver evidence."""

        if not isinstance(resolution, LatheToolCapabilityResolution):
            raise TypeError("Lathe Tool capability resolution is invalid")
        if not resolution.exists or not resolution.current:
            raise ValueError("Lathe Tool capability resolution is not current")
        assert resolution.tool_revision is not None
        assert resolution.assembly_revision is not None
        return cls(
            resolution.reference.tool_id,
            resolution.reference.profile_id,
            resolution.reference.assembly_id,
            resolution.capabilities,
            resolution.tool_revision,
            resolution.profile_revision,
            resolution.assembly_revision,
        )


@dataclass(frozen=True, slots=True)
class LatheOperationState:
    """Immutable minimum Lathe operation aggregate."""

    ownership: LatheOwnershipKey
    strategy_id: LatheStrategyId
    parameter_state: LatheParameterState
    geometry_binding: LatheGeometryBinding | None = None
    tool_binding: LatheToolBinding | None = None
    enabled: bool = True
    diagnostics: tuple[LatheDiagnostic, ...] = ()
    revision: Revision = Revision(0)

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, LatheOwnershipKey):
            raise TypeError("Lathe operation ownership is invalid")
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe operation strategy is invalid")
        if not isinstance(self.parameter_state, LatheParameterState) or (
            self.parameter_state.strategy_id is not self.strategy_id
        ):
            raise ValueError("Lathe parameter state must match the strategy")
        if self.geometry_binding is not None and not isinstance(
            self.geometry_binding, LatheGeometryBinding
        ):
            raise TypeError("Lathe geometry binding is invalid")
        if self.tool_binding is not None and not isinstance(
            self.tool_binding, LatheToolBinding
        ):
            raise TypeError("Lathe tool binding is invalid")
        if type(self.enabled) is not bool:
            raise TypeError("Lathe enabled flag must be bool")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("Lathe diagnostics must be an immutable typed tuple")
        object.__setattr__(
            self, "diagnostics", ordered_lathe_diagnostics(self.diagnostics)
        )
        if not isinstance(self.revision, Revision):
            raise TypeError("Lathe revision must be Revision")


@dataclass(frozen=True, slots=True)
class LatheOperationEvaluation:
    readiness: LatheOperationReadiness
    diagnostics: tuple[LatheDiagnostic, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, LatheOperationReadiness):
            raise TypeError("Lathe operation readiness is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("Lathe evaluation diagnostics are invalid")
        object.__setattr__(
            self, "diagnostics", ordered_lathe_diagnostics(self.diagnostics)
        )


def evaluate_lathe_operation(
    operation: LatheOperationState,
    *,
    project_id: UUID,
    document_id: CadDocumentId,
    source_id: UUID,
    generation: int,
    setup_id: SetupId | None,
    read_only: bool,
    closed: bool,
) -> LatheOperationEvaluation:
    """Evaluate structural readiness against one explicit live session."""

    if not isinstance(operation, LatheOperationState):
        raise TypeError("operation must be LatheOperationState")
    _non_nil_uuid(project_id, "project_id")
    if not isinstance(document_id, CadDocumentId):
        raise TypeError("document_id must be CadDocumentId")
    _non_nil_uuid(source_id, "source_id")
    _generation(generation)
    if setup_id is not None and not isinstance(setup_id, SetupId):
        raise TypeError("setup_id must be SetupId or None")
    if type(read_only) is not bool or type(closed) is not bool:
        raise TypeError("read_only and closed must be bool")

    diagnostics = list(operation.diagnostics)
    if closed:
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.CLOSED))
    if read_only:
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.READ_ONLY))
    ownership = operation.ownership
    if setup_id is None:
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.MISSING_SETUP))
    if (
        ownership.project_id != project_id
        or ownership.document_id != document_id
        or ownership.source_id != source_id
        or ownership.generation != generation
        or (setup_id is not None and ownership.setup_id != setup_id)
    ):
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.STALE_OWNERSHIP))
    if not operation.enabled:
        diagnostics.append(
            LatheDiagnostic(
                LatheDiagnosticCode.DISABLED_OPERATION, DiagnosticSeverity.INFO
            )
        )

    definition = lathe_strategy_definition(operation.strategy_id)
    geometry = operation.geometry_binding
    if geometry is None:
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.MISSING_GEOMETRY))
    elif geometry.source_id != ownership.source_id or (
        geometry.generation != ownership.generation
    ):
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.STALE_OWNERSHIP))
    elif geometry.kind not in definition.allowed_geometry_kinds:
        diagnostics.append(
            LatheDiagnostic(LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY)
        )

    tool = operation.tool_binding
    if tool is None:
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.MISSING_TOOL))
    elif not definition.required_tool_capabilities.issubset(
        tool.resolved_capabilities
    ):
        diagnostics.append(LatheDiagnostic(LatheDiagnosticCode.INCOMPATIBLE_TOOL))

    ordered = ordered_lathe_diagnostics(diagnostics)
    invalid_codes = {
        LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY,
        LatheDiagnosticCode.INCOMPATIBLE_TOOL,
        LatheDiagnosticCode.INVALID_PARAMETER,
        LatheDiagnosticCode.STALE_OWNERSHIP,
        LatheDiagnosticCode.READ_ONLY,
        LatheDiagnosticCode.CLOSED,
        LatheDiagnosticCode.UNKNOWN_STRATEGY,
        LatheDiagnosticCode.REVISION_MISMATCH,
    }
    if any(item.code in invalid_codes for item in ordered):
        readiness = LatheOperationReadiness.INVALID
    elif ordered:
        readiness = LatheOperationReadiness.INCOMPLETE
    else:
        readiness = LatheOperationReadiness.READY
    return LatheOperationEvaluation(readiness, ordered)


def lathe_operation_to_canonical_mapping(
    operation: LatheOperationState,
) -> dict[str, object]:
    """Encode one deterministic JSON-compatible in-memory mapping."""

    if not isinstance(operation, LatheOperationState):
        raise TypeError("operation must be LatheOperationState")
    ownership = operation.ownership
    geometry = operation.geometry_binding
    tool = operation.tool_binding
    capability_order = {item: index for index, item in enumerate(LatheToolCapability)}
    return {
        "schema_version": LATHE_SNAPSHOT_SCHEMA_VERSION,
        "ownership": {
            "project_id": str(ownership.project_id),
            "document_id": str(ownership.document_id),
            "source_id": str(ownership.source_id),
            "generation": ownership.generation,
            "setup_id": str(ownership.setup_id),
            "operation_id": str(ownership.operation_id),
        },
        "strategy_id": operation.strategy_id.value,
        "parameter_state": [
            {"parameter_id": key, "value": value}
            for key, value in operation.parameter_state.canonical_values()
        ],
        "geometry_binding": (
            None
            if geometry is None
            else {
                "kind": geometry.kind.value,
                "entity_ids": list(geometry.entity_ids),
                "source_id": str(geometry.source_id),
                "generation": geometry.generation,
            }
        ),
        "tool_binding": (
            None
            if tool is None
            else {
                "tool_id": str(tool.tool_id),
                "profile_id": str(tool.profile_id) if tool.profile_id is not None else None,
                "assembly_id": str(tool.assembly_id),
                "resolved_capabilities": [
                    item.value
                    for item in sorted(
                        tool.resolved_capabilities, key=capability_order.__getitem__
                    )
                ],
                "tool_revision": tool.tool_revision.value,
                "profile_revision": (
                    tool.profile_revision.value
                    if tool.profile_revision is not None
                    else None
                ),
                "assembly_revision": tool.assembly_revision.value,
            }
        ),
        "enabled": operation.enabled,
        "diagnostics": [
            {
                "code": item.code.value,
                "severity": item.severity.value,
                "field_id": item.field_id,
                "parameters": [
                    {"key": key, "value": value} for key, value in item.parameters
                ],
            }
            for item in operation.diagnostics
        ],
        "revision": operation.revision.value,
    }


def lathe_operation_from_canonical_mapping(
    data: Mapping[str, object],
) -> LatheOperationState:
    """Strictly decode one in-memory canonical mapping with no persistence."""

    _exact_mapping(
        data,
        {
            "schema_version",
            "ownership",
            "strategy_id",
            "parameter_state",
            "geometry_binding",
            "tool_binding",
            "enabled",
            "diagnostics",
            "revision",
        },
        "Lathe operation snapshot",
    )
    if data["schema_version"] != LATHE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("Unsupported Lathe operation snapshot schema")
    ownership_data = _exact_mapping(
        data["ownership"],
        {
            "project_id",
            "document_id",
            "source_id",
            "generation",
            "setup_id",
            "operation_id",
        },
        "Lathe ownership",
    )
    try:
        ownership = LatheOwnershipKey(
            UUID(_text(ownership_data["project_id"], "project_id")),
            CadDocumentId(_text(ownership_data["document_id"], "document_id")),
            UUID(_text(ownership_data["source_id"], "source_id")),
            ownership_data["generation"],
            SetupId.parse(_text(ownership_data["setup_id"], "setup_id")),
            OperationId.parse(
                _text(ownership_data["operation_id"], "operation_id")
            ),
        )
        strategy_id = LatheStrategyId(data["strategy_id"])
    except (TypeError, ValueError) as error:
        raise ValueError("Canonical Lathe identity or strategy is invalid") from error
    parameter_state = decode_canonical_parameter_values(
        strategy_id, data["parameter_state"]
    )

    geometry_data = data["geometry_binding"]
    geometry: LatheGeometryBinding | None = None
    if geometry_data is not None:
        geometry_map = _exact_mapping(
            geometry_data,
            {"kind", "entity_ids", "source_id", "generation"},
            "Lathe geometry binding",
        )
        entity_ids = geometry_map["entity_ids"]
        if not isinstance(entity_ids, list) or any(
            not isinstance(item, str) for item in entity_ids
        ):
            raise ValueError("Canonical Lathe entity IDs are malformed")
        try:
            geometry = LatheGeometryBinding(
                LatheGeometryKind(geometry_map["kind"]),
                tuple(entity_ids),
                UUID(_text(geometry_map["source_id"], "geometry source_id")),
                geometry_map["generation"],
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Canonical Lathe geometry binding is invalid") from error

    tool_data = data["tool_binding"]
    tool: LatheToolBinding | None = None
    if tool_data is not None:
        tool_map = _exact_mapping(
            tool_data,
            {
                "tool_id",
                "profile_id",
                "assembly_id",
                "resolved_capabilities",
                "tool_revision",
                "profile_revision",
                "assembly_revision",
            },
            "Lathe tool binding",
        )
        raw_capabilities = tool_map["resolved_capabilities"]
        if not isinstance(raw_capabilities, list):
            raise ValueError("Canonical Lathe tool capabilities are malformed")
        raw_profile_id = tool_map["profile_id"]
        if raw_profile_id is not None and not isinstance(raw_profile_id, str):
            raise ValueError("Canonical Lathe profile ID is malformed")
        try:
            tool = LatheToolBinding(
                ToolDefinitionId.parse(_text(tool_map["tool_id"], "tool_id")),
                (
                    ToolProgramProfileId.parse(raw_profile_id)
                    if raw_profile_id is not None
                    else None
                ),
                ToolAssemblyId.parse(
                    _text(tool_map["assembly_id"], "assembly_id")
                ),
                frozenset(LatheToolCapability(item) for item in raw_capabilities),
                Revision(tool_map["tool_revision"]),
                (
                    Revision(tool_map["profile_revision"])
                    if tool_map["profile_revision"] is not None
                    else None
                ),
                Revision(tool_map["assembly_revision"]),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Canonical Lathe tool binding is invalid") from error

    raw_diagnostics = data["diagnostics"]
    if not isinstance(raw_diagnostics, list):
        raise ValueError("Canonical Lathe diagnostics are malformed")
    diagnostics: list[LatheDiagnostic] = []
    for raw in raw_diagnostics:
        diagnostic_map = _exact_mapping(
            raw,
            {"code", "severity", "field_id", "parameters"},
            "Lathe diagnostic",
        )
        raw_parameters = diagnostic_map["parameters"]
        if not isinstance(raw_parameters, list) or any(
            not isinstance(item, dict) or set(item) != {"key", "value"}
            for item in raw_parameters
        ):
            raise ValueError("Canonical Lathe diagnostic parameters are malformed")
        try:
            diagnostics.append(
                LatheDiagnostic(
                    LatheDiagnosticCode(diagnostic_map["code"]),
                    DiagnosticSeverity(diagnostic_map["severity"]),
                    diagnostic_map["field_id"],
                    tuple((item["key"], item["value"]) for item in raw_parameters),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Canonical Lathe diagnostic is invalid") from error
    return LatheOperationState(
        ownership=ownership,
        strategy_id=strategy_id,
        parameter_state=parameter_state,
        geometry_binding=geometry,
        tool_binding=tool,
        enabled=data["enabled"],
        diagnostics=tuple(diagnostics),
        revision=Revision(data["revision"]),
    )


def _exact_mapping(
    value: object, fields: set[str], subject: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{subject} fields are malformed")
    return value


def _text(value: object, subject: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{subject} must be a non-empty string")
    return value


__all__ = [
    "LATHE_SNAPSHOT_SCHEMA_VERSION",
    "LatheGeometryBinding",
    "LatheOperationEvaluation",
    "LatheOperationState",
    "LatheOwnershipKey",
    "LatheToolBinding",
    "evaluate_lathe_operation",
    "lathe_operation_from_canonical_mapping",
    "lathe_operation_to_canonical_mapping",
]
