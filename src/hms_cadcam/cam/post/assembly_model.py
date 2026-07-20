"""Immutable contracts for explicit-order multi-operation NC assembly."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import (
    CamJobId,
    MachineDefinitionId,
    OperationId,
    PostProcessorDefinitionId,
    ProductionControllerProfileId,
    ProgramAssemblyRequestId,
    ProgramAssemblyResultId,
    ProgramOperationSectionId,
    SetupId,
    ToolAssemblyId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.post.model import (
    CoordinateMode,
    NCProgramIR,
    Plane,
    PostProcessorDefinition,
    SimulationGatePolicy,
)
from hms_cadcam.cam.post.profile import (
    ControllerToolBinding,
    CutterCompensationPolicy,
    ProductionProgramContext,
    sanitize_comment_fragment,
)
from hms_cadcam.cam.simulation.model import SimulationResult, SimulationStatus


PROGRAM_ASSEMBLY_FORMAT = "HMS_CAM_PROGRAM_ASSEMBLY_REQUEST"
PROGRAM_ASSEMBLY_PLAN_FORMAT = "HMS_CAM_PROGRAM_ASSEMBLY_PLAN"
PROGRAM_OPERATION_SECTION_FORMAT = "HMS_CAM_PROGRAM_OPERATION_SECTION"
PROGRAM_ASSEMBLY_RESULT_FORMAT = "HMS_CAM_PROGRAM_ASSEMBLY_RESULT"
PROGRAM_ASSEMBLY_VERSION = 1
PROGRAM_ASSEMBLY_POLICY_VERSION = 1

_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ProgramAssemblyOrderingPolicy(StrEnum):
    EXPLICIT_OPERATION_ORDER = "explicit_operation_order"


class ProgramAssemblyStatus(StrEnum):
    PUBLISHED = "published"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class ProgramAssemblyDiagnosticCode(StrEnum):
    INVALID_REQUEST = "assembly.invalid_request"
    EMPTY = "assembly.empty"
    DUPLICATE_OPERATION = "assembly.duplicate_operation"
    INVALID_ORDER = "assembly.invalid_order"
    OPERATION_MISSING = "assembly.operation_missing"
    OPERATION_DISABLED = "assembly.operation_disabled"
    OPERATION_INVALID = "assembly.operation_invalid"
    ARTIFACT_MISSING = "assembly.artifact_missing"
    ARTIFACT_STALE = "assembly.artifact_stale"
    SIMULATION_BLOCKED = "assembly.simulation_blocked"
    SETUP_MISMATCH = "assembly.setup_mismatch"
    MACHINE_MISMATCH = "assembly.machine_mismatch"
    PROFILE_MISMATCH = "assembly.profile_mismatch"
    UNIT_MISMATCH = "assembly.unit_mismatch"
    WORK_OFFSET_MISMATCH = "assembly.work_offset_mismatch"
    TOOL_BINDING_MISSING = "assembly.tool_binding_missing"
    TOOL_BINDING_CONFLICT = "assembly.tool_binding_conflict"
    SAFE_Z_INVALID = "assembly.safe_z_invalid"
    COMPENSATION_INVALID = "assembly.compensation_invalid"
    UNSUPPORTED_OPERATION = "assembly.unsupported_operation"
    UNSUPPORTED_TAPPING = "assembly.unsupported_tapping"
    SECTION_INVALID = "assembly.section_invalid"
    OUTPUT_INVALID = "assembly.output_invalid"
    STALE = "assembly.stale"
    CANCELLED = "assembly.cancelled"
    FAILED = "assembly.failed"


def _uuid(value: UUID, name: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise CamValidationError(f"{name} must be a non-nil UUID")
    return value


def _metadata(values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise CamValidationError("Assembly metadata must be an immutable tuple")
    normalized: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise CamValidationError("Assembly metadata entry is invalid")
        key, value = item
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise CamValidationError("Assembly metadata key is invalid")
        normalized.append((key, sanitize_comment_fragment(value, maximum=128)))
    if len({key for key, _ in normalized}) != len(normalized):
        raise CamInvariantError("Assembly metadata keys must be unique")
    return tuple(sorted(normalized))


def _diagnostic_evidence(
    values: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise CamValidationError("Assembly diagnostic evidence is invalid")
    normalized: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise CamValidationError("Assembly diagnostic evidence is invalid")
        key, value = item
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise CamValidationError("Assembly diagnostic evidence key is invalid")
        if not isinstance(value, str) or not value or len(value) > 256:
            raise CamValidationError("Assembly diagnostic evidence value is invalid")
        if any(ord(char) < 32 for char in key + value):
            raise CamValidationError("Assembly diagnostic evidence contains control data")
        normalized.append((key, value))
    if len({key for key, _ in normalized}) != len(normalized):
        raise CamInvariantError("Assembly diagnostic evidence keys must be unique")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class ProgramAssemblyDiagnostic:
    severity: DiagnosticSeverity
    code: ProgramAssemblyDiagnosticCode
    message_key: str
    operation_id: OperationId | None = None
    section_id: ProgramOperationSectionId | None = None
    section_index: int | None = None
    record_index: int | None = None
    evidence: tuple[tuple[str, str], ...] = ()
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly diagnostic version")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise CamValidationError("Assembly diagnostic severity is invalid")
        if not isinstance(self.code, ProgramAssemblyDiagnosticCode):
            raise CamValidationError("Assembly diagnostic code is invalid")
        if not isinstance(self.message_key, str) or _KEY.fullmatch(self.message_key) is None:
            raise CamValidationError("Assembly diagnostic message key is invalid")
        if self.operation_id is not None and not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Assembly diagnostic operation ID is invalid")
        if self.section_id is not None and not isinstance(
            self.section_id, ProgramOperationSectionId
        ):
            raise CamValidationError("Assembly diagnostic section ID is invalid")
        for value in (self.section_index, self.record_index):
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError("Assembly diagnostic index is invalid")
        object.__setattr__(self, "evidence", _diagnostic_evidence(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import diagnostic_to_dict

        return diagnostic_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramAssemblyContext:
    """Program-wide output policy; tool and safe-Z data remain per section."""

    file_name: str
    global_metadata: tuple[tuple[str, str], ...] = ()
    program_identity: str | None = None
    unit: LengthUnit = LengthUnit.MM
    coordinate_mode: CoordinateMode = CoordinateMode.ABSOLUTE
    plane: Plane = Plane.XY
    work_offset_code: str = "G54"
    newline: str = "\r\n"
    encoding: str = "utf-8"
    extension: str = ".fn"
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly context version")
        if (
            not isinstance(self.file_name, str)
            or not self.file_name.casefold().endswith(".fn")
            or any(char in self.file_name for char in "/\\\r\n")
            or len(self.file_name) > 128
        ):
            raise CamValidationError("Assembly file name is invalid")
        object.__setattr__(self, "global_metadata", _metadata(self.global_metadata))
        reserved = {"shl-tech", "filename", "program"}
        if any(key.casefold() in reserved for key, _ in self.global_metadata):
            raise CamValidationError("Assembly metadata contains reserved header keys")
        if self.program_identity is not None:
            object.__setattr__(
                self,
                "program_identity",
                sanitize_comment_fragment(self.program_identity, maximum=128),
            )
        if self.unit is not LengthUnit.MM:
            raise CamValidationError("Assembly v1 requires MM")
        if self.coordinate_mode is not CoordinateMode.ABSOLUTE or self.plane is not Plane.XY:
            raise CamValidationError("Assembly v1 requires absolute XY mode")
        if self.work_offset_code != "G54":
            raise CamValidationError("Assembly v1 requires G54")
        if self.newline != "\r\n" or self.encoding.casefold() != "utf-8":
            raise CamValidationError("Assembly v1 requires UTF-8 CRLF")
        if self.extension.casefold() != ".fn":
            raise CamValidationError("Assembly v1 requires .fn")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM_PROGRAM_ASSEMBLY_CONTEXT",
            "format_version": self.schema_version,
            "file_name": self.file_name,
            "global_metadata": [list(item) for item in self.global_metadata],
            "program_identity": self.program_identity,
            "unit": self.unit.value,
            "coordinate_mode": self.coordinate_mode.value,
            "plane": self.plane.value,
            "work_offset_code": self.work_offset_code,
            "newline": self.newline,
            "encoding": self.encoding,
            "extension": self.extension,
        }


@dataclass(frozen=True, slots=True)
class ProgramAssemblyOperationInput:
    operation_id: OperationId
    order_index: int
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    tool_assembly_fingerprint: ContentFingerprint
    tool_binding: ControllerToolBinding
    source_snapshot: PostSourceSnapshot
    simulation_result: SimulationResult | None
    program_context: ProductionProgramContext
    cutter_compensation_policy: CutterCompensationPolicy
    display_metadata: tuple[tuple[str, str], ...] = ()
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly operation-input version")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Assembly operation ID is invalid")
        if type(self.order_index) is not int or self.order_index < 0:
            raise CamValidationError("Assembly operation order is invalid")
        if not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Assembly artifact ID is invalid")
        if not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Assembly artifact fingerprint is invalid")
        if not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Assembly tool-assembly fingerprint is invalid")
        if not isinstance(self.tool_binding, ControllerToolBinding):
            raise CamValidationError("Assembly tool binding is invalid")
        if not isinstance(self.source_snapshot, PostSourceSnapshot):
            raise CamValidationError("Assembly source snapshot is invalid")
        if self.simulation_result is not None and not isinstance(
            self.simulation_result, SimulationResult
        ):
            raise CamValidationError("Assembly simulation snapshot is invalid")
        if not isinstance(self.program_context, ProductionProgramContext):
            raise CamValidationError("Assembly operation context is invalid")
        if not isinstance(self.cutter_compensation_policy, CutterCompensationPolicy):
            raise CamValidationError("Assembly compensation policy is invalid")
        object.__setattr__(self, "display_metadata", _metadata(self.display_metadata))

    @property
    def operation_context_fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(
            {
                "program_context": self.program_context.to_dict(),
                "cutter_compensation_policy": self.cutter_compensation_policy.value,
                "display_metadata": [list(item) for item in self.display_metadata],
            }
        )

    def identity_payload(self) -> dict[str, Any]:
        simulation = self.simulation_result
        return {
            "format": "HMS_CAM_PROGRAM_ASSEMBLY_OPERATION_INPUT",
            "format_version": self.schema_version,
            "operation_id": str(self.operation_id),
            "order_index": self.order_index,
            "artifact_id": str(self.artifact_id),
            "artifact_fingerprint": self.artifact_fingerprint.to_dict(),
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_binding": self.tool_binding.to_dict(),
            "simulation_fingerprint": (
                simulation.result_fingerprint.to_dict() if simulation is not None else None
            ),
            "simulation_status": simulation.status.value if simulation is not None else None,
            "operation_context_fingerprint": self.operation_context_fingerprint.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import operation_input_to_dict

        return operation_input_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramAssemblyRequest:
    project_id: UUID
    project_generation: int
    job_id: CamJobId
    setup_id: SetupId
    machine_id: MachineDefinitionId
    machine_fingerprint: ContentFingerprint
    post_definition: PostProcessorDefinition
    shared_context: ProgramAssemblyContext
    operations: tuple[ProgramAssemblyOperationInput, ...]
    simulation_gate_policy: SimulationGatePolicy = SimulationGatePolicy()
    ordering_policy: ProgramAssemblyOrderingPolicy = (
        ProgramAssemblyOrderingPolicy.EXPLICIT_OPERATION_ORDER
    )
    request_id: ProgramAssemblyRequestId | None = None
    assembly_policy_version: int = PROGRAM_ASSEMBLY_POLICY_VERSION
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly request version")
        if self.assembly_policy_version != PROGRAM_ASSEMBLY_POLICY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly policy version")
        _uuid(self.project_id, "Assembly project ID")
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise CamValidationError("Assembly project generation is invalid")
        for value, typ, name in (
            (self.job_id, CamJobId, "job"),
            (self.setup_id, SetupId, "setup"),
            (self.machine_id, MachineDefinitionId, "machine"),
        ):
            if not isinstance(value, typ):
                raise CamValidationError(f"Assembly {name} ID is invalid")
        if not isinstance(self.machine_fingerprint, ContentFingerprint):
            raise CamValidationError("Assembly machine fingerprint is invalid")
        if not isinstance(self.post_definition, PostProcessorDefinition):
            raise CamValidationError("Assembly post definition is invalid")
        if not isinstance(self.shared_context, ProgramAssemblyContext):
            raise CamValidationError("Assembly shared context is invalid")
        if not isinstance(self.operations, tuple) or any(
            not isinstance(item, ProgramAssemblyOperationInput) for item in self.operations
        ):
            raise CamValidationError("Assembly operation inputs are invalid")
        if not isinstance(self.simulation_gate_policy, SimulationGatePolicy):
            raise CamValidationError("Assembly simulation gate is invalid")
        if self.ordering_policy is not ProgramAssemblyOrderingPolicy.EXPLICIT_OPERATION_ORDER:
            raise CamValidationError("Assembly ordering policy is unsupported")
        if self.request_id is None:
            object.__setattr__(self, "request_id", ProgramAssemblyRequestId.new())
        if not isinstance(self.request_id, ProgramAssemblyRequestId):
            raise CamValidationError("Assembly request ID is invalid")

    def identity_payload(self) -> dict[str, Any]:
        """Return semantic identity without runtime generation/request token."""
        return {
            "format": PROGRAM_ASSEMBLY_FORMAT,
            "format_version": self.schema_version,
            "assembly_policy_version": self.assembly_policy_version,
            "project_id": str(self.project_id),
            "job_id": str(self.job_id),
            "setup_id": str(self.setup_id),
            "machine_id": str(self.machine_id),
            "machine_fingerprint": self.machine_fingerprint.to_dict(),
            "post_definition_fingerprint": self.post_definition.fingerprint.to_dict(),
            "production_profile_fingerprint": (
                self.post_definition.production_profile.fingerprint.to_dict()
                if self.post_definition.production_profile is not None
                else None
            ),
            "shared_context_fingerprint": self.shared_context.fingerprint.to_dict(),
            "simulation_gate_policy": self.simulation_gate_policy.to_dict(),
            "ordering_policy": self.ordering_policy.value,
            "operations": [item.identity_payload() for item in self.operations],
        }

    @property
    def input_fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.identity_payload())

    @property
    def production_profile_id(self) -> ProductionControllerProfileId | None:
        profile = self.post_definition.production_profile
        return profile.profile_id if profile is not None else None

    @property
    def production_profile_version(self) -> int | None:
        profile = self.post_definition.production_profile
        return profile.profile_version if profile is not None else None

    @property
    def production_profile_fingerprint(self) -> ContentFingerprint | None:
        profile = self.post_definition.production_profile
        return profile.fingerprint if profile is not None else None

    @property
    def shared_program_context(self) -> ProgramAssemblyContext:
        return self.shared_context

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import request_to_dict

        return request_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramOperationSection:
    section_id: ProgramOperationSectionId
    operation_id: OperationId
    order_index: int
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: ContentFingerprint
    tool_binding: ControllerToolBinding
    operation_context_fingerprint: ContentFingerprint
    simulation_fingerprint: ContentFingerprint | None
    simulation_status: SimulationStatus | None
    program_ir: NCProgramIR
    display_metadata: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[ProgramAssemblyDiagnostic, ...] = ()
    section_fingerprint: ContentFingerprint | None = None
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported operation-section version")
        for value, typ, name in (
            (self.section_id, ProgramOperationSectionId, "section"),
            (self.operation_id, OperationId, "operation"),
            (self.artifact_id, ToolpathArtifactId, "artifact"),
            (self.tool_assembly_id, ToolAssemblyId, "tool assembly"),
        ):
            if not isinstance(value, typ):
                raise CamValidationError(f"Program section {name} identity is invalid")
        if type(self.order_index) is not int or self.order_index < 0:
            raise CamValidationError("Program section order is invalid")
        for value in (
            self.artifact_fingerprint,
            self.tool_assembly_fingerprint,
            self.operation_context_fingerprint,
        ):
            if not isinstance(value, ContentFingerprint):
                raise CamValidationError("Program section fingerprint is invalid")
        if not isinstance(self.tool_binding, ControllerToolBinding):
            raise CamValidationError("Program section tool binding is invalid")
        if self.simulation_fingerprint is not None and not isinstance(
            self.simulation_fingerprint, ContentFingerprint
        ):
            raise CamValidationError("Program section simulation fingerprint is invalid")
        if self.simulation_status is not None and not isinstance(
            self.simulation_status, SimulationStatus
        ):
            raise CamValidationError("Program section simulation status is invalid")
        if not isinstance(self.program_ir, NCProgramIR):
            raise CamValidationError("Program section IR is invalid")
        object.__setattr__(self, "display_metadata", _metadata(self.display_metadata))
        if (
            self.program_ir.operation_id != self.operation_id
            or self.program_ir.artifact_id != self.artifact_id
            or self.program_ir.tool_assembly_id != self.tool_assembly_id
        ):
            raise CamInvariantError("Program section provenance differs from its IR")
        if self.program_ir.production_context is None:
            raise CamInvariantError("Program section requires production context")
        if self.program_ir.production_context.tool_binding != self.tool_binding:
            raise CamInvariantError("Program section tool binding differs from its IR")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, ProgramAssemblyDiagnostic) for item in self.diagnostics
        ):
            raise CamValidationError("Program section diagnostics are invalid")
        object.__setattr__(self, "diagnostics", _sort_diagnostics(self.diagnostics))
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.section_fingerprint is None:
            object.__setattr__(self, "section_fingerprint", calculated)
        elif self.section_fingerprint != calculated:
            raise CamInvariantError("Program section fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": PROGRAM_OPERATION_SECTION_FORMAT,
            "format_version": self.schema_version,
            "operation_id": str(self.operation_id),
            "order_index": self.order_index,
            "artifact_id": str(self.artifact_id),
            "artifact_fingerprint": self.artifact_fingerprint.to_dict(),
            "tool_assembly_id": str(self.tool_assembly_id),
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_binding_fingerprint": self.tool_binding.fingerprint.to_dict(),
            "operation_context_fingerprint": self.operation_context_fingerprint.to_dict(),
            "simulation_fingerprint": (
                self.simulation_fingerprint.to_dict()
                if self.simulation_fingerprint is not None
                else None
            ),
            "simulation_status": (
                self.simulation_status.value if self.simulation_status is not None else None
            ),
            "program_ir_fingerprint": self.program_ir.program_fingerprint.to_dict(),
            "display_metadata": [list(item) for item in self.display_metadata],
        }

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import section_to_dict

        return section_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramAssemblyPlan:
    project_id: UUID
    job_id: CamJobId
    setup_id: SetupId
    machine_id: MachineDefinitionId
    machine_fingerprint: ContentFingerprint
    post_definition_id: PostProcessorDefinitionId
    post_definition_fingerprint: ContentFingerprint
    production_profile_id: ProductionControllerProfileId
    production_profile_version: int
    production_profile_fingerprint: ContentFingerprint
    adapter_key: str
    adapter_version: int
    shared_context: ProgramAssemblyContext
    simulation_gate_policy: SimulationGatePolicy
    ordering_policy: ProgramAssemblyOrderingPolicy
    sections: tuple[ProgramOperationSection, ...]
    plan_fingerprint: ContentFingerprint | None = None
    assembly_policy_version: int = PROGRAM_ASSEMBLY_POLICY_VERSION
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly plan version")
        if self.assembly_policy_version != PROGRAM_ASSEMBLY_POLICY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly plan policy version")
        _uuid(self.project_id, "Assembly plan project ID")
        for value, typ in (
            (self.job_id, CamJobId),
            (self.setup_id, SetupId),
            (self.machine_id, MachineDefinitionId),
            (self.post_definition_id, PostProcessorDefinitionId),
            (self.production_profile_id, ProductionControllerProfileId),
        ):
            if not isinstance(value, typ):
                raise CamValidationError("Assembly plan identity is invalid")
        for value in (
            self.machine_fingerprint,
            self.post_definition_fingerprint,
            self.production_profile_fingerprint,
        ):
            if not isinstance(value, ContentFingerprint):
                raise CamValidationError("Assembly plan fingerprint is invalid")
        if type(self.production_profile_version) is not int or self.production_profile_version <= 0:
            raise CamValidationError("Assembly plan profile version is invalid")
        if not isinstance(self.adapter_key, str) or _KEY.fullmatch(self.adapter_key) is None:
            raise CamValidationError("Assembly plan adapter key is invalid")
        if type(self.adapter_version) is not int or self.adapter_version <= 0:
            raise CamValidationError("Assembly plan adapter version is invalid")
        if not isinstance(self.shared_context, ProgramAssemblyContext):
            raise CamValidationError("Assembly plan context is invalid")
        if not isinstance(self.simulation_gate_policy, SimulationGatePolicy):
            raise CamValidationError("Assembly plan simulation gate is invalid")
        if self.ordering_policy is not ProgramAssemblyOrderingPolicy.EXPLICIT_OPERATION_ORDER:
            raise CamValidationError("Assembly plan ordering policy is invalid")
        if not isinstance(self.sections, tuple) or not self.sections or any(
            not isinstance(item, ProgramOperationSection) for item in self.sections
        ):
            raise CamValidationError("Assembly plan sections are invalid")
        if tuple(item.order_index for item in self.sections) != tuple(
            range(len(self.sections))
        ):
            raise CamInvariantError("Assembly plan section order must be explicit and contiguous")
        if len({item.operation_id for item in self.sections}) != len(self.sections):
            raise CamInvariantError("Assembly plan operations must be unique")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.plan_fingerprint is None:
            object.__setattr__(self, "plan_fingerprint", calculated)
        elif self.plan_fingerprint != calculated:
            raise CamInvariantError("Assembly plan fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": PROGRAM_ASSEMBLY_PLAN_FORMAT,
            "format_version": self.schema_version,
            "assembly_policy_version": self.assembly_policy_version,
            "project_id": str(self.project_id),
            "job_id": str(self.job_id),
            "setup_id": str(self.setup_id),
            "machine_id": str(self.machine_id),
            "machine_fingerprint": self.machine_fingerprint.to_dict(),
            "post_definition_id": str(self.post_definition_id),
            "post_definition_fingerprint": self.post_definition_fingerprint.to_dict(),
            "production_profile_id": str(self.production_profile_id),
            "production_profile_version": self.production_profile_version,
            "production_profile_fingerprint": self.production_profile_fingerprint.to_dict(),
            "adapter_key": self.adapter_key,
            "adapter_version": self.adapter_version,
            "shared_context_fingerprint": self.shared_context.fingerprint.to_dict(),
            "simulation_gate_policy": self.simulation_gate_policy.to_dict(),
            "ordering_policy": self.ordering_policy.value,
            "sections": [item.section_fingerprint.to_dict() for item in self.sections],
        }

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import plan_to_dict

        return plan_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramAssemblyStatistics:
    operation_count: int
    section_count: int
    tool_change_count: int
    record_count: int
    motion_count: int
    pass_count: int
    warn_count: int
    optional_missing_count: int
    line_count: int
    byte_length: int
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly statistics version")
        values = (
            self.operation_count,
            self.section_count,
            self.tool_change_count,
            self.record_count,
            self.motion_count,
            self.pass_count,
            self.warn_count,
            self.optional_missing_count,
            self.line_count,
            self.byte_length,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise CamValidationError("Assembly statistics are invalid")
        if self.operation_count != self.section_count:
            raise CamInvariantError("Assembly operation/section counts do not balance")
        if self.tool_change_count != self.section_count:
            raise CamInvariantError("Assembly v1 requires one tool change per section")
        if self.pass_count + self.warn_count + self.optional_missing_count != self.operation_count:
            raise CamInvariantError("Assembly simulation counts do not balance")

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import statistics_to_dict

        return statistics_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProgramAssemblyResult:
    result_id: ProgramAssemblyResultId
    request_id: ProgramAssemblyRequestId
    project_id: UUID
    project_generation: int
    input_fingerprint: DependencyFingerprint
    plan: ProgramAssemblyPlan
    output_checksum: str
    canonical_text: str
    status: ProgramAssemblyStatus
    diagnostics: tuple[ProgramAssemblyDiagnostic, ...]
    statistics: ProgramAssemblyStatistics
    result_fingerprint: ContentFingerprint | None = None
    schema_version: int = PROGRAM_ASSEMBLY_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_ASSEMBLY_VERSION:
            raise UnsupportedCamSchemaError("Unsupported assembly result version")
        if not isinstance(self.result_id, ProgramAssemblyResultId) or not isinstance(
            self.request_id, ProgramAssemblyRequestId
        ):
            raise CamValidationError("Assembly result identity is invalid")
        _uuid(self.project_id, "Assembly result project ID")
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise CamValidationError("Assembly result project generation is invalid")
        if not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Assembly result input fingerprint is invalid")
        if not isinstance(self.plan, ProgramAssemblyPlan) or self.plan.project_id != self.project_id:
            raise CamInvariantError("Assembly result plan is invalid")
        if not isinstance(self.output_checksum, str) or _SHA256.fullmatch(
            self.output_checksum
        ) is None:
            raise CamValidationError("Assembly output checksum is invalid")
        if not isinstance(self.canonical_text, str) or not self.canonical_text:
            raise CamValidationError("Assembly canonical text is invalid")
        if hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest() != self.output_checksum:
            raise CamInvariantError("Assembly output checksum verification failed")
        if self.status is not ProgramAssemblyStatus.PUBLISHED:
            raise CamInvariantError("Persistable assembly result must be published")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, ProgramAssemblyDiagnostic) for item in self.diagnostics
        ):
            raise CamValidationError("Assembly result diagnostics are invalid")
        object.__setattr__(self, "diagnostics", _sort_diagnostics(self.diagnostics))
        if not isinstance(self.statistics, ProgramAssemblyStatistics):
            raise CamValidationError("Assembly result statistics are invalid")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.result_fingerprint is None:
            object.__setattr__(self, "result_fingerprint", calculated)
        elif self.result_fingerprint != calculated:
            raise CamInvariantError("Assembly result fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        """Exclude request/result UUID and project generation from semantic identity."""
        return {
            "format": PROGRAM_ASSEMBLY_RESULT_FORMAT,
            "format_version": self.schema_version,
            "project_id": str(self.project_id),
            "input_fingerprint": self.input_fingerprint.to_dict(),
            "plan_fingerprint": self.plan.plan_fingerprint.to_dict(),
            "ordered_operation_ids": [str(item.operation_id) for item in self.plan.sections],
            "ordered_section_ids": [str(item.section_id) for item in self.plan.sections],
            "ordered_artifact_fingerprints": [
                item.artifact_fingerprint.to_dict() for item in self.plan.sections
            ],
            "ordered_simulation": [
                {
                    "fingerprint": (
                        item.simulation_fingerprint.to_dict()
                        if item.simulation_fingerprint is not None
                        else None
                    ),
                    "status": (
                        item.simulation_status.value
                        if item.simulation_status is not None
                        else None
                    ),
                }
                for item in self.plan.sections
            ],
            "ordered_tool_assembly_fingerprints": [
                item.tool_assembly_fingerprint.to_dict() for item in self.plan.sections
            ],
            "ordered_tool_binding_fingerprints": [
                item.tool_binding.fingerprint.to_dict() for item in self.plan.sections
            ],
            "ordered_operation_context_fingerprints": [
                item.operation_context_fingerprint.to_dict() for item in self.plan.sections
            ],
            "machine_fingerprint": self.plan.machine_fingerprint.to_dict(),
            "production_profile_fingerprint": (
                self.plan.production_profile_fingerprint.to_dict()
            ),
            "shared_context_fingerprint": self.plan.shared_context.fingerprint.to_dict(),
            "assembly_policy_version": self.plan.assembly_policy_version,
            "output_checksum": self.output_checksum,
            "statistics": self.statistics.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.assembly_codec import result_to_dict

        return result_to_dict(self)

    @property
    def ordered_operation_ids(self) -> tuple[OperationId, ...]:
        return tuple(item.operation_id for item in self.plan.sections)

    @property
    def ordered_section_ids(self) -> tuple[ProgramOperationSectionId, ...]:
        return tuple(item.section_id for item in self.plan.sections)

    @property
    def machine_fingerprint(self) -> ContentFingerprint:
        return self.plan.machine_fingerprint

    @property
    def production_profile_fingerprint(self) -> ContentFingerprint:
        return self.plan.production_profile_fingerprint


def _sort_diagnostics(
    diagnostics: tuple[ProgramAssemblyDiagnostic, ...],
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.severity.value,
                item.code.value,
                item.section_index if item.section_index is not None else -1,
                str(item.operation_id) if item.operation_id is not None else "",
                item.record_index if item.record_index is not None else -1,
                item.message_key,
                item.evidence,
            ),
        )
    )
