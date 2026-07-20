"""Strict JSON codecs for Stage 7D.3.1 program-assembly contracts."""

from __future__ import annotations

import json
from typing import Any, TypeVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
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
from hms_cadcam.cam.domain.machine import MachineDefinition
from hms_cadcam.cam.domain.operation import DiagnosticSeverity, Operation
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.setup import Setup
from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolAssembly, ToolDefinition
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.post.assembly_model import (
    PROGRAM_ASSEMBLY_FORMAT,
    PROGRAM_ASSEMBLY_PLAN_FORMAT,
    PROGRAM_ASSEMBLY_RESULT_FORMAT,
    PROGRAM_ASSEMBLY_VERSION,
    PROGRAM_OPERATION_SECTION_FORMAT,
    ProgramAssemblyContext,
    ProgramAssemblyDiagnostic,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyOperationInput,
    ProgramAssemblyOrderingPolicy,
    ProgramAssemblyPlan,
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyStatistics,
    ProgramAssemblyStatus,
    ProgramOperationSection,
)
from hms_cadcam.cam.post.codec import (
    _typed_fp,
    definition_from_dict,
    definition_to_dict,
    program_from_dict,
    program_to_dict,
    simulation_gate_policy_from_dict,
    simulation_gate_policy_to_dict,
)
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.post.model import CoordinateMode, Plane
from hms_cadcam.cam.post.profile import (
    ControllerToolBinding,
    CutterCompensationPolicy,
    ProductionProgramContext,
)
from hms_cadcam.cam.simulation.codec import result_from_dict as simulation_result_from_dict
from hms_cadcam.cam.simulation.model import SimulationStatus
from hms_cadcam.cam.toolpath.codec import artifact_from_dict, artifact_to_dict


T = TypeVar("T")
AssemblyCodecValue = (
    ProgramAssemblyRequest
    | ProgramAssemblyPlan
    | ProgramAssemblyResult
    | ProgramOperationSection
)


def _strict(data: Any, format_name: str, fields: set[str]) -> None:
    if not isinstance(data, dict):
        raise CamValidationError(f"{format_name} payload must be an object")
    if data.get("format") != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data.get("format_version")) is not int or data["format_version"] != PROGRAM_ASSEMBLY_VERSION:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")
    if set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload fields are invalid")


def _id(id_type: type[T], value: Any, name: str) -> T:
    if not isinstance(value, str):
        raise CamValidationError(f"{name} must be text")
    try:
        return id_type.parse(value)  # type: ignore[attr-defined, no-any-return]
    except (TypeError, ValueError) as error:
        raise CamValidationError(f"{name} is invalid") from error


def _uuid(value: Any, name: str) -> UUID:
    if not isinstance(value, str):
        raise CamValidationError(f"{name} must be text")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise CamValidationError(f"{name} is invalid") from error
    if parsed.int == 0:
        raise CamValidationError(f"{name} is nil")
    return parsed


def diagnostic_to_dict(value: ProgramAssemblyDiagnostic) -> dict[str, Any]:
    return {
        "format": "HMS_CAM_PROGRAM_ASSEMBLY_DIAGNOSTIC",
        "format_version": value.schema_version,
        "severity": value.severity.value,
        "code": value.code.value,
        "message_key": value.message_key,
        "operation_id": str(value.operation_id) if value.operation_id is not None else None,
        "section_id": str(value.section_id) if value.section_id is not None else None,
        "section_index": value.section_index,
        "record_index": value.record_index,
        "evidence": [list(item) for item in value.evidence],
    }


def diagnostic_from_dict(data: dict[str, Any]) -> ProgramAssemblyDiagnostic:
    format_name = "HMS_CAM_PROGRAM_ASSEMBLY_DIAGNOSTIC"
    _strict(
        data,
        format_name,
        {
            "severity",
            "code",
            "message_key",
            "operation_id",
            "section_id",
            "section_index",
            "record_index",
            "evidence",
        },
    )
    evidence = data["evidence"]
    if not isinstance(evidence, list) or any(
        not isinstance(item, list) or len(item) != 2 for item in evidence
    ):
        raise CamValidationError("Assembly diagnostic evidence payload is invalid")
    try:
        return ProgramAssemblyDiagnostic(
            DiagnosticSeverity(data["severity"]),
            ProgramAssemblyDiagnosticCode(data["code"]),
            data["message_key"],
            _id(OperationId, data["operation_id"], "Operation ID")
            if data["operation_id"] is not None
            else None,
            _id(ProgramOperationSectionId, data["section_id"], "Section ID")
            if data["section_id"] is not None
            else None,
            data["section_index"],
            data["record_index"],
            tuple((item[0], item[1]) for item in evidence),
            data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly diagnostic enum is invalid") from error


def context_from_dict(data: dict[str, Any]) -> ProgramAssemblyContext:
    format_name = "HMS_CAM_PROGRAM_ASSEMBLY_CONTEXT"
    _strict(
        data,
        format_name,
        {
            "file_name",
            "global_metadata",
            "program_identity",
            "unit",
            "coordinate_mode",
            "plane",
            "work_offset_code",
            "newline",
            "encoding",
            "extension",
        },
    )
    metadata = data["global_metadata"]
    if not isinstance(metadata, list) or any(
        not isinstance(item, list) or len(item) != 2 for item in metadata
    ):
        raise CamValidationError("Assembly context metadata payload is invalid")
    try:
        return ProgramAssemblyContext(
            file_name=data["file_name"],
            global_metadata=tuple((item[0], item[1]) for item in metadata),
            program_identity=data["program_identity"],
            unit=LengthUnit(data["unit"]),
            coordinate_mode=CoordinateMode(data["coordinate_mode"]),
            plane=Plane(data["plane"]),
            work_offset_code=data["work_offset_code"],
            newline=data["newline"],
            encoding=data["encoding"],
            extension=data["extension"],
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly context enum is invalid") from error


def source_snapshot_to_dict(value: PostSourceSnapshot) -> dict[str, Any]:
    return {
        "project_id": str(value.project_id),
        "operation": value.operation.to_dict(),
        "artifact": artifact_to_dict(value.artifact),
        "setup": value.setup.to_dict(),
        "assembly": value.assembly.to_dict(),
        "tool": value.tool.to_dict() if value.tool is not None else None,
        "holder": value.holder.to_dict() if value.holder is not None else None,
        "machine": value.machine.to_dict() if value.machine is not None else None,
        "simulation_result": (
            value.simulation_result.to_dict()
            if value.simulation_result is not None
            else None
        ),
        "expected_simulation_input_fingerprint": (
            value.expected_simulation_input_fingerprint.to_dict()
            if value.expected_simulation_input_fingerprint is not None
            else None
        ),
    }


def source_snapshot_from_dict(data: dict[str, Any]) -> PostSourceSnapshot:
    fields = {
        "project_id",
        "operation",
        "artifact",
        "setup",
        "assembly",
        "tool",
        "holder",
        "machine",
        "simulation_result",
        "expected_simulation_input_fingerprint",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("Assembly source snapshot payload is malformed")
    return PostSourceSnapshot(
        project_id=_uuid(data["project_id"], "Source project ID"),
        operation=Operation.from_dict(data["operation"]),
        artifact=artifact_from_dict(data["artifact"]),
        setup=Setup.from_dict(data["setup"]),
        assembly=ToolAssembly.from_dict(data["assembly"]),
        tool=ToolDefinition.from_dict(data["tool"]) if data["tool"] is not None else None,
        holder=(
            HolderDefinition.from_dict(data["holder"])
            if data["holder"] is not None
            else None
        ),
        machine=(
            MachineDefinition.from_dict(data["machine"])
            if data["machine"] is not None
            else None
        ),
        simulation_result=(
            simulation_result_from_dict(data["simulation_result"])
            if data["simulation_result"] is not None
            else None
        ),
        expected_simulation_input_fingerprint=(
            DependencyFingerprint.from_dict(
                data["expected_simulation_input_fingerprint"]
            )
            if data["expected_simulation_input_fingerprint"] is not None
            else None
        ),
    )


def operation_input_to_dict(value: ProgramAssemblyOperationInput) -> dict[str, Any]:
    return {
        "format": "HMS_CAM_PROGRAM_ASSEMBLY_OPERATION_INPUT",
        "format_version": value.schema_version,
        "operation_id": str(value.operation_id),
        "order_index": value.order_index,
        "artifact_id": str(value.artifact_id),
        "artifact_fingerprint": value.artifact_fingerprint.to_dict(),
        "tool_assembly_fingerprint": value.tool_assembly_fingerprint.to_dict(),
        "tool_binding": value.tool_binding.to_dict(),
        "source_snapshot": source_snapshot_to_dict(value.source_snapshot),
        "simulation_result": (
            value.simulation_result.to_dict()
            if value.simulation_result is not None
            else None
        ),
        "program_context": value.program_context.to_dict(),
        "cutter_compensation_policy": value.cutter_compensation_policy.value,
        "display_metadata": [list(item) for item in value.display_metadata],
    }


def operation_input_from_dict(data: dict[str, Any]) -> ProgramAssemblyOperationInput:
    format_name = "HMS_CAM_PROGRAM_ASSEMBLY_OPERATION_INPUT"
    _strict(
        data,
        format_name,
        {
            "operation_id",
            "order_index",
            "artifact_id",
            "artifact_fingerprint",
            "tool_assembly_fingerprint",
            "tool_binding",
            "source_snapshot",
            "simulation_result",
            "program_context",
            "cutter_compensation_policy",
            "display_metadata",
        },
    )
    metadata = data["display_metadata"]
    if not isinstance(metadata, list) or any(
        not isinstance(item, list) or len(item) != 2 for item in metadata
    ):
        raise CamValidationError("Assembly operation metadata payload is invalid")
    try:
        return ProgramAssemblyOperationInput(
            operation_id=_id(OperationId, data["operation_id"], "Operation ID"),
            order_index=data["order_index"],
            artifact_id=_id(ToolpathArtifactId, data["artifact_id"], "Artifact ID"),
            artifact_fingerprint=ContentFingerprint.from_dict(
                data["artifact_fingerprint"]
            ),
            tool_assembly_fingerprint=_typed_fp(
                data["tool_assembly_fingerprint"]
            ),
            tool_binding=ControllerToolBinding.from_dict(data["tool_binding"]),
            source_snapshot=source_snapshot_from_dict(data["source_snapshot"]),
            simulation_result=(
                simulation_result_from_dict(data["simulation_result"])
                if data["simulation_result"] is not None
                else None
            ),
            program_context=ProductionProgramContext.from_dict(data["program_context"]),
            cutter_compensation_policy=CutterCompensationPolicy(
                data["cutter_compensation_policy"]
            ),
            display_metadata=tuple((item[0], item[1]) for item in metadata),
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly operation enum is invalid") from error


def request_to_dict(value: ProgramAssemblyRequest) -> dict[str, Any]:
    return {
        "format": PROGRAM_ASSEMBLY_FORMAT,
        "format_version": value.schema_version,
        "assembly_policy_version": value.assembly_policy_version,
        "project_id": str(value.project_id),
        "project_generation": value.project_generation,
        "job_id": str(value.job_id),
        "setup_id": str(value.setup_id),
        "machine_id": str(value.machine_id),
        "machine_fingerprint": value.machine_fingerprint.to_dict(),
        "post_definition": definition_to_dict(value.post_definition),
        "shared_context": value.shared_context.to_dict(),
        "operations": [operation_input_to_dict(item) for item in value.operations],
        "simulation_gate_policy": simulation_gate_policy_to_dict(
            value.simulation_gate_policy
        ),
        "ordering_policy": value.ordering_policy.value,
        "request_id": str(value.request_id),
    }


def request_from_dict(data: dict[str, Any]) -> ProgramAssemblyRequest:
    _strict(
        data,
        PROGRAM_ASSEMBLY_FORMAT,
        {
            "assembly_policy_version",
            "project_id",
            "project_generation",
            "job_id",
            "setup_id",
            "machine_id",
            "machine_fingerprint",
            "post_definition",
            "shared_context",
            "operations",
            "simulation_gate_policy",
            "ordering_policy",
            "request_id",
        },
    )
    if not isinstance(data["operations"], list):
        raise CamValidationError("Assembly operations payload must be a list")
    try:
        return ProgramAssemblyRequest(
            project_id=_uuid(data["project_id"], "Project ID"),
            project_generation=data["project_generation"],
            job_id=_id(CamJobId, data["job_id"], "Job ID"),
            setup_id=_id(SetupId, data["setup_id"], "Setup ID"),
            machine_id=_id(MachineDefinitionId, data["machine_id"], "Machine ID"),
            machine_fingerprint=ContentFingerprint.from_dict(
                data["machine_fingerprint"]
            ),
            post_definition=definition_from_dict(data["post_definition"]),
            shared_context=context_from_dict(data["shared_context"]),
            operations=tuple(operation_input_from_dict(item) for item in data["operations"]),
            simulation_gate_policy=simulation_gate_policy_from_dict(
                data["simulation_gate_policy"]
            ),
            ordering_policy=ProgramAssemblyOrderingPolicy(data["ordering_policy"]),
            request_id=_id(
                ProgramAssemblyRequestId, data["request_id"], "Assembly request ID"
            ),
            assembly_policy_version=data["assembly_policy_version"],
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly request enum is invalid") from error


def section_to_dict(
    value: ProgramOperationSection, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": PROGRAM_OPERATION_SECTION_FORMAT,
        "format_version": value.schema_version,
        "section_id": str(value.section_id),
        "operation_id": str(value.operation_id),
        "order_index": value.order_index,
        "artifact_id": str(value.artifact_id),
        "artifact_fingerprint": value.artifact_fingerprint.to_dict(),
        "tool_assembly_id": str(value.tool_assembly_id),
        "tool_assembly_fingerprint": value.tool_assembly_fingerprint.to_dict(),
        "tool_binding": value.tool_binding.to_dict(),
        "operation_context_fingerprint": value.operation_context_fingerprint.to_dict(),
        "simulation_fingerprint": (
            value.simulation_fingerprint.to_dict()
            if value.simulation_fingerprint is not None
            else None
        ),
        "simulation_status": (
            value.simulation_status.value if value.simulation_status is not None else None
        ),
        "program_ir": program_to_dict(value.program_ir),
        "display_metadata": [list(item) for item in value.display_metadata],
        "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics],
    }
    if include_fingerprint:
        data["section_fingerprint"] = value.section_fingerprint.to_dict()
    return data


def section_from_dict(data: dict[str, Any]) -> ProgramOperationSection:
    base_fields = {
        "section_id",
        "operation_id",
        "order_index",
        "artifact_id",
        "artifact_fingerprint",
        "tool_assembly_id",
        "tool_assembly_fingerprint",
        "tool_binding",
        "operation_context_fingerprint",
        "simulation_fingerprint",
        "simulation_status",
        "program_ir",
        "display_metadata",
        "diagnostics",
        "section_fingerprint",
    }
    _strict(data, PROGRAM_OPERATION_SECTION_FORMAT, base_fields)
    if not isinstance(data["diagnostics"], list):
        raise CamValidationError("Assembly section diagnostics payload is invalid")
    metadata = data["display_metadata"]
    if not isinstance(metadata, list) or any(
        not isinstance(item, list) or len(item) != 2 for item in metadata
    ):
        raise CamValidationError("Assembly section metadata payload is invalid")
    try:
        return ProgramOperationSection(
            section_id=_id(
                ProgramOperationSectionId, data["section_id"], "Section ID"
            ),
            operation_id=_id(OperationId, data["operation_id"], "Operation ID"),
            order_index=data["order_index"],
            artifact_id=_id(ToolpathArtifactId, data["artifact_id"], "Artifact ID"),
            artifact_fingerprint=ContentFingerprint.from_dict(
                data["artifact_fingerprint"]
            ),
            tool_assembly_id=_id(
                ToolAssemblyId, data["tool_assembly_id"], "Tool assembly ID"
            ),
            tool_assembly_fingerprint=_typed_fp(
                data["tool_assembly_fingerprint"]
            ),
            tool_binding=ControllerToolBinding.from_dict(data["tool_binding"]),
            operation_context_fingerprint=ContentFingerprint.from_dict(
                data["operation_context_fingerprint"]
            ),
            simulation_fingerprint=(
                ContentFingerprint.from_dict(data["simulation_fingerprint"])
                if data["simulation_fingerprint"] is not None
                else None
            ),
            simulation_status=(
                SimulationStatus(data["simulation_status"])
                if data["simulation_status"] is not None
                else None
            ),
            program_ir=program_from_dict(data["program_ir"]),
            display_metadata=tuple((item[0], item[1]) for item in metadata),
            diagnostics=tuple(
                diagnostic_from_dict(item) for item in data["diagnostics"]
            ),
            section_fingerprint=ContentFingerprint.from_dict(
                data["section_fingerprint"]
            ),
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly section enum is invalid") from error


def plan_to_dict(
    value: ProgramAssemblyPlan, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": PROGRAM_ASSEMBLY_PLAN_FORMAT,
        "format_version": value.schema_version,
        "assembly_policy_version": value.assembly_policy_version,
        "project_id": str(value.project_id),
        "job_id": str(value.job_id),
        "setup_id": str(value.setup_id),
        "machine_id": str(value.machine_id),
        "machine_fingerprint": value.machine_fingerprint.to_dict(),
        "post_definition_id": str(value.post_definition_id),
        "post_definition_fingerprint": value.post_definition_fingerprint.to_dict(),
        "production_profile_id": str(value.production_profile_id),
        "production_profile_version": value.production_profile_version,
        "production_profile_fingerprint": value.production_profile_fingerprint.to_dict(),
        "adapter_key": value.adapter_key,
        "adapter_version": value.adapter_version,
        "shared_context": value.shared_context.to_dict(),
        "simulation_gate_policy": simulation_gate_policy_to_dict(
            value.simulation_gate_policy
        ),
        "ordering_policy": value.ordering_policy.value,
        "sections": [section_to_dict(item) for item in value.sections],
    }
    if include_fingerprint:
        data["plan_fingerprint"] = value.plan_fingerprint.to_dict()
    return data


def plan_from_dict(data: dict[str, Any]) -> ProgramAssemblyPlan:
    _strict(
        data,
        PROGRAM_ASSEMBLY_PLAN_FORMAT,
        {
            "assembly_policy_version",
            "project_id",
            "job_id",
            "setup_id",
            "machine_id",
            "machine_fingerprint",
            "post_definition_id",
            "post_definition_fingerprint",
            "production_profile_id",
            "production_profile_version",
            "production_profile_fingerprint",
            "adapter_key",
            "adapter_version",
            "shared_context",
            "simulation_gate_policy",
            "ordering_policy",
            "sections",
            "plan_fingerprint",
        },
    )
    if not isinstance(data["sections"], list):
        raise CamValidationError("Assembly plan sections payload is invalid")
    try:
        return ProgramAssemblyPlan(
            project_id=_uuid(data["project_id"], "Project ID"),
            job_id=_id(CamJobId, data["job_id"], "Job ID"),
            setup_id=_id(SetupId, data["setup_id"], "Setup ID"),
            machine_id=_id(MachineDefinitionId, data["machine_id"], "Machine ID"),
            machine_fingerprint=ContentFingerprint.from_dict(
                data["machine_fingerprint"]
            ),
            post_definition_id=_id(
                PostProcessorDefinitionId,
                data["post_definition_id"],
                "Post definition ID",
            ),
            post_definition_fingerprint=ContentFingerprint.from_dict(
                data["post_definition_fingerprint"]
            ),
            production_profile_id=_id(
                ProductionControllerProfileId,
                data["production_profile_id"],
                "Production profile ID",
            ),
            production_profile_version=data["production_profile_version"],
            production_profile_fingerprint=ContentFingerprint.from_dict(
                data["production_profile_fingerprint"]
            ),
            adapter_key=data["adapter_key"],
            adapter_version=data["adapter_version"],
            shared_context=context_from_dict(data["shared_context"]),
            simulation_gate_policy=simulation_gate_policy_from_dict(
                data["simulation_gate_policy"]
            ),
            ordering_policy=ProgramAssemblyOrderingPolicy(data["ordering_policy"]),
            sections=tuple(section_from_dict(item) for item in data["sections"]),
            plan_fingerprint=ContentFingerprint.from_dict(data["plan_fingerprint"]),
            assembly_policy_version=data["assembly_policy_version"],
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly plan enum is invalid") from error


def statistics_to_dict(value: ProgramAssemblyStatistics) -> dict[str, Any]:
    return {
        "format": "HMS_CAM_PROGRAM_ASSEMBLY_STATISTICS",
        "format_version": value.schema_version,
        "operation_count": value.operation_count,
        "section_count": value.section_count,
        "tool_change_count": value.tool_change_count,
        "record_count": value.record_count,
        "motion_count": value.motion_count,
        "pass_count": value.pass_count,
        "warn_count": value.warn_count,
        "optional_missing_count": value.optional_missing_count,
        "line_count": value.line_count,
        "byte_length": value.byte_length,
    }


def statistics_from_dict(data: dict[str, Any]) -> ProgramAssemblyStatistics:
    format_name = "HMS_CAM_PROGRAM_ASSEMBLY_STATISTICS"
    fields = {
        "operation_count",
        "section_count",
        "tool_change_count",
        "record_count",
        "motion_count",
        "pass_count",
        "warn_count",
        "optional_missing_count",
        "line_count",
        "byte_length",
    }
    _strict(data, format_name, fields)
    return ProgramAssemblyStatistics(
        data["operation_count"],
        data["section_count"],
        data["tool_change_count"],
        data["record_count"],
        data["motion_count"],
        data["pass_count"],
        data["warn_count"],
        data["optional_missing_count"],
        data["line_count"],
        data["byte_length"],
        data["format_version"],
    )


def result_to_dict(
    value: ProgramAssemblyResult, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": PROGRAM_ASSEMBLY_RESULT_FORMAT,
        "format_version": value.schema_version,
        "result_id": str(value.result_id),
        "request_id": str(value.request_id),
        "project_id": str(value.project_id),
        "project_generation": value.project_generation,
        "input_fingerprint": value.input_fingerprint.to_dict(),
        "plan": plan_to_dict(value.plan),
        "output_checksum": value.output_checksum,
        "canonical_text": value.canonical_text,
        "status": value.status.value,
        "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics],
        "statistics": statistics_to_dict(value.statistics),
    }
    if include_fingerprint:
        data["result_fingerprint"] = value.result_fingerprint.to_dict()
    return data


def result_from_dict(data: dict[str, Any]) -> ProgramAssemblyResult:
    _strict(
        data,
        PROGRAM_ASSEMBLY_RESULT_FORMAT,
        {
            "result_id",
            "request_id",
            "project_id",
            "project_generation",
            "input_fingerprint",
            "plan",
            "output_checksum",
            "canonical_text",
            "status",
            "diagnostics",
            "statistics",
            "result_fingerprint",
        },
    )
    if not isinstance(data["diagnostics"], list):
        raise CamValidationError("Assembly result diagnostics payload is invalid")
    try:
        return ProgramAssemblyResult(
            result_id=_id(
                ProgramAssemblyResultId, data["result_id"], "Assembly result ID"
            ),
            request_id=_id(
                ProgramAssemblyRequestId, data["request_id"], "Assembly request ID"
            ),
            project_id=_uuid(data["project_id"], "Project ID"),
            project_generation=data["project_generation"],
            input_fingerprint=DependencyFingerprint.from_dict(data["input_fingerprint"]),
            plan=plan_from_dict(data["plan"]),
            output_checksum=data["output_checksum"],
            canonical_text=data["canonical_text"],
            status=ProgramAssemblyStatus(data["status"]),
            diagnostics=tuple(
                diagnostic_from_dict(item) for item in data["diagnostics"]
            ),
            statistics=statistics_from_dict(data["statistics"]),
            result_fingerprint=ContentFingerprint.from_dict(
                data["result_fingerprint"]
            ),
            schema_version=data["format_version"],
        )
    except ValueError as error:
        raise CamValidationError("Assembly result enum is invalid") from error


def dumps(value: AssemblyCodecValue) -> str:
    if isinstance(value, ProgramAssemblyRequest):
        payload = request_to_dict(value)
    elif isinstance(value, ProgramAssemblyPlan):
        payload = plan_to_dict(value)
    elif isinstance(value, ProgramAssemblyResult):
        payload = result_to_dict(value)
    elif isinstance(value, ProgramOperationSection):
        payload = section_to_dict(value)
    else:
        raise CamValidationError("Unsupported assembly codec value")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(text: str) -> AssemblyCodecValue:
    if not isinstance(text, str):
        raise CamValidationError("Assembly JSON must be text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CamValidationError("Assembly JSON is invalid") from error
    if not isinstance(data, dict):
        raise CamValidationError("Assembly JSON root must be an object")
    format_name = data.get("format")
    if format_name == PROGRAM_ASSEMBLY_FORMAT:
        return request_from_dict(data)
    if format_name == PROGRAM_ASSEMBLY_PLAN_FORMAT:
        return plan_from_dict(data)
    if format_name == PROGRAM_ASSEMBLY_RESULT_FORMAT:
        return result_from_dict(data)
    if format_name == PROGRAM_OPERATION_SECTION_FORMAT:
        return section_from_dict(data)
    raise UnsupportedCamSchemaError("Unsupported assembly JSON format")
