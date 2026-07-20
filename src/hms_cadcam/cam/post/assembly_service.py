"""Latest-wins service for deterministic multi-operation program assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Callable
from uuid import UUID, uuid4, uuid5

from hms_cadcam.cam.domain.ids import (
    PostRequestId,
    ProgramAssemblyResultId,
    ProgramOperationSectionId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.post.assembly_model import (
    ProgramAssemblyDiagnostic,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyPlan,
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyStatistics,
    ProgramAssemblyStatus,
    ProgramOperationSection,
)
from hms_cadcam.cam.post.assembly_validation import (
    validate_assembly_output,
    validate_assembly_plan,
    validate_assembly_request,
)
from hms_cadcam.cam.post.fanuc_robodrill_21i import FanucRobodrill21iAdapter
from hms_cadcam.cam.post.lowering import lower_toolpath
from hms_cadcam.cam.post.model import PostRequest, PostResultStatus
from hms_cadcam.cam.post.service import build_post_input_fingerprint
from hms_cadcam.cam.simulation.model import SimulationStatus


_SECTION_NAMESPACE = UUID("60d2c151-15dc-5ddb-8b8a-7d3100000001")


@dataclass(frozen=True, slots=True)
class ProgramAssemblyComputationToken:
    value: UUID
    generation: int
    project_generation: int
    request_id: object
    input_fingerprint: DependencyFingerprint
    operation_order: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ProgramAssemblyExecution:
    accepted: bool
    result: ProgramAssemblyResult | None
    diagnostics: tuple[ProgramAssemblyDiagnostic, ...]
    status: ProgramAssemblyStatus


def _post_request(
    request: ProgramAssemblyRequest, operation_input
) -> PostRequest:
    return PostRequest(
        project_id=request.project_id,
        operation_id=operation_input.operation_id,
        artifact_id=operation_input.artifact_id,
        post_definition=request.post_definition,
        simulation_gate_policy=request.simulation_gate_policy,
        request_id=PostRequestId.new(),
        program_context=operation_input.program_context,
    )


def build_assembly_input_fingerprint(
    request: ProgramAssemblyRequest,
) -> DependencyFingerprint:
    """Capture all semantic inputs; exclude request token and runtime generation."""
    operation_inputs = []
    for item in request.operations:
        post_request = _post_request(request, item)
        operation_inputs.append(
            {
                "order_index": item.order_index,
                "operation_id": str(item.operation_id),
                "post_input": build_post_input_fingerprint(
                    post_request, item.source_snapshot
                ).to_dict(),
                "binding": item.tool_binding.fingerprint.to_dict(),
                "operation_context": item.operation_context_fingerprint.to_dict(),
            }
        )
    return DependencyFingerprint.from_payload(
        {
            "request": request.identity_payload(),
            "operation_inputs": operation_inputs,
        }
    )


class ProgramAssemblyService:
    """Build and atomically publish one whole program from ordered snapshots."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._generation = 0
        self._latest: dict[tuple[object, ...], ProgramAssemblyComputationToken] = {}
        self._entries: dict[
            tuple[object, ...],
            tuple[ProgramAssemblyComputationToken, ProgramAssemblyResult],
        ] = {}

    @staticmethod
    def _key(request: ProgramAssemblyRequest) -> tuple[object, ...]:
        profile = request.post_definition.production_profile
        return (
            request.project_id,
            request.job_id,
            request.setup_id,
            profile.profile_id if profile is not None else request.post_definition.definition_id,
            request.shared_context.file_name.casefold(),
        )

    def begin(self, request: ProgramAssemblyRequest) -> ProgramAssemblyComputationToken:
        fingerprint = build_assembly_input_fingerprint(request)
        with self._lock:
            self._generation += 1
            token = ProgramAssemblyComputationToken(
                uuid4(),
                self._generation,
                request.project_generation,
                request.request_id,
                fingerprint,
                tuple(item.operation_id for item in request.operations),
            )
            self._latest[self._key(request)] = token
            return token

    def current(self, request: ProgramAssemblyRequest) -> ProgramAssemblyResult | None:
        fingerprint = build_assembly_input_fingerprint(request)
        with self._lock:
            entry = self._entries.get(self._key(request))
            if entry is None:
                return None
            token, result = entry
            if (
                token.input_fingerprint != fingerprint
                or token.project_generation != request.project_generation
            ):
                return None
            return result

    def results(self) -> tuple[ProgramAssemblyResult, ...]:
        with self._lock:
            return tuple(
                item[1]
                for item in sorted(
                    self._entries.values(), key=lambda pair: str(pair[1].result_id)
                )
            )

    def mark_operation_stale(self, operation_id: object) -> None:
        with self._lock:
            stale_keys = tuple(
                key
                for key, (_, result) in self._entries.items()
                if any(
                    section.operation_id == operation_id
                    for section in result.plan.sections
                )
            )
            for key in stale_keys:
                self._entries.pop(key, None)
                self._latest.pop(key, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._generation += 1
            self._latest.clear()
            self._entries.clear()

    def assemble(
        self,
        request: ProgramAssemblyRequest,
        *,
        current_request: Callable[[], ProgramAssemblyRequest] | None = None,
        adapter: FanucRobodrill21iAdapter | None = None,
    ) -> ProgramAssemblyExecution:
        token = self.begin(request)
        diagnostics = list(validate_assembly_request(request))
        if _has_errors(diagnostics):
            return ProgramAssemblyExecution(
                False,
                None,
                tuple(diagnostics),
                ProgramAssemblyStatus.BLOCKED,
            )
        adapter = adapter or FanucRobodrill21iAdapter(request.post_definition)
        sections: list[ProgramOperationSection] = []
        try:
            for operation_input in request.operations:
                post_request = _post_request(request, operation_input)
                post_diagnostics = adapter.validate_request(post_request)
                if any(
                    item.severity is DiagnosticSeverity.ERROR
                    for item in post_diagnostics
                ):
                    diagnostics.extend(
                        _from_post_diagnostic(
                            item,
                            operation_input.operation_id,
                            operation_input.order_index,
                        )
                        for item in post_diagnostics
                    )
                    return ProgramAssemblyExecution(
                        False,
                        None,
                        tuple(diagnostics),
                        ProgramAssemblyStatus.BLOCKED,
                    )
                program = lower_toolpath(
                    post_request, operation_input.source_snapshot
                )
                post_diagnostics = adapter.validate_program_ir(program)
                if any(
                    item.severity is DiagnosticSeverity.ERROR
                    for item in post_diagnostics
                ):
                    diagnostics.extend(
                        _from_post_diagnostic(
                            item,
                            operation_input.operation_id,
                            operation_input.order_index,
                        )
                        for item in post_diagnostics
                    )
                    return ProgramAssemblyExecution(
                        False,
                        None,
                        tuple(diagnostics),
                        ProgramAssemblyStatus.BLOCKED,
                    )
                simulation = operation_input.simulation_result
                section_id = ProgramOperationSectionId(
                    uuid5(
                        _SECTION_NAMESPACE,
                        "|".join(
                            (
                                str(request.project_id),
                                str(operation_input.operation_id),
                                str(operation_input.artifact_id),
                                str(operation_input.order_index),
                                program.program_fingerprint.digest,
                                operation_input.operation_context_fingerprint.digest,
                            )
                        ),
                    )
                )
                section_warnings = tuple(
                    diagnostic
                    for diagnostic in diagnostics
                    if diagnostic.operation_id == operation_input.operation_id
                    and diagnostic.severity is DiagnosticSeverity.WARNING
                )
                sections.append(
                    ProgramOperationSection(
                        section_id=section_id,
                        operation_id=operation_input.operation_id,
                        order_index=operation_input.order_index,
                        artifact_id=operation_input.artifact_id,
                        artifact_fingerprint=operation_input.artifact_fingerprint,
                        tool_assembly_id=operation_input.source_snapshot.assembly.assembly_id,
                        tool_assembly_fingerprint=(
                            operation_input.tool_assembly_fingerprint
                        ),
                        tool_binding=operation_input.tool_binding,
                        operation_context_fingerprint=(
                            operation_input.operation_context_fingerprint
                        ),
                        simulation_fingerprint=(
                            simulation.result_fingerprint
                            if simulation is not None
                            else None
                        ),
                        simulation_status=(
                            simulation.status if simulation is not None else None
                        ),
                        program_ir=program,
                        display_metadata=operation_input.display_metadata,
                        diagnostics=section_warnings,
                    )
                )
            profile = request.post_definition.production_profile
            assert profile is not None
            plan = ProgramAssemblyPlan(
                project_id=request.project_id,
                job_id=request.job_id,
                setup_id=request.setup_id,
                machine_id=request.machine_id,
                machine_fingerprint=request.machine_fingerprint,
                post_definition_id=request.post_definition.definition_id,
                post_definition_fingerprint=request.post_definition.fingerprint,
                production_profile_id=profile.profile_id,
                production_profile_version=profile.profile_version,
                production_profile_fingerprint=profile.fingerprint,
                adapter_key=request.post_definition.adapter_key,
                adapter_version=request.post_definition.adapter_version,
                shared_context=request.shared_context,
                simulation_gate_policy=request.simulation_gate_policy,
                ordering_policy=request.ordering_policy,
                sections=tuple(sections),
            )
            diagnostics.extend(validate_assembly_plan(plan, request.post_definition))
            if _has_errors(diagnostics):
                return ProgramAssemblyExecution(
                    False,
                    None,
                    tuple(diagnostics),
                    ProgramAssemblyStatus.BLOCKED,
                )
            text = adapter.format_assembly(plan, request.post_definition)
            diagnostics.extend(
                validate_assembly_output(
                    text, plan, request.post_definition, adapter
                )
            )
            if _has_errors(diagnostics):
                return ProgramAssemblyExecution(
                    False,
                    None,
                    tuple(diagnostics),
                    ProgramAssemblyStatus.FAILED,
                )
            if not self._still_current(request, token, current_request):
                return _stale_execution("assembly.input_changed")
            payload = text.encode(profile.encoding)
            result = ProgramAssemblyResult(
                result_id=ProgramAssemblyResultId.new(),
                request_id=request.request_id,
                project_id=request.project_id,
                project_generation=request.project_generation,
                input_fingerprint=token.input_fingerprint,
                plan=plan,
                output_checksum=hashlib.sha256(payload).hexdigest(),
                canonical_text=text,
                status=ProgramAssemblyStatus.PUBLISHED,
                diagnostics=tuple(diagnostics),
                statistics=_statistics(plan, text, len(payload)),
            )
            if not self._publish(request, token, result):
                return _stale_execution("assembly.publish_stale")
            return ProgramAssemblyExecution(
                True,
                result,
                tuple(diagnostics),
                ProgramAssemblyStatus.PUBLISHED,
            )
        except Exception as error:
            evidence = " ".join(str(error).split())[:256] or type(error).__name__
            diagnostic = ProgramAssemblyDiagnostic(
                DiagnosticSeverity.ERROR,
                ProgramAssemblyDiagnosticCode.FAILED,
                "assembly.failed",
                evidence=(("error", evidence),),
            )
            return ProgramAssemblyExecution(
                False,
                None,
                (diagnostic,),
                ProgramAssemblyStatus.FAILED,
            )

    def _still_current(
        self,
        request: ProgramAssemblyRequest,
        token: ProgramAssemblyComputationToken,
        current_request: Callable[[], ProgramAssemblyRequest] | None,
    ) -> bool:
        with self._lock:
            if self._latest.get(self._key(request)) != token:
                return False
        try:
            current = current_request() if current_request is not None else request
            return (
                current.request_id == request.request_id
                and current.project_generation == request.project_generation
                and tuple(item.operation_id for item in current.operations)
                == token.operation_order
                and build_assembly_input_fingerprint(current)
                == token.input_fingerprint
            )
        except Exception:
            return False

    def _publish(
        self,
        request: ProgramAssemblyRequest,
        token: ProgramAssemblyComputationToken,
        result: ProgramAssemblyResult,
    ) -> bool:
        with self._lock:
            key = self._key(request)
            if self._latest.get(key) != token:
                return False
            if (
                result.status is not ProgramAssemblyStatus.PUBLISHED
                or result.input_fingerprint != token.input_fingerprint
                or result.project_generation != token.project_generation
            ):
                return False
            existing = self._entries.get(key)
            if existing is not None and existing[0].generation > token.generation:
                return False
            self._entries[key] = (token, result)
            return True


def _statistics(
    plan: ProgramAssemblyPlan, text: str, byte_length: int
) -> ProgramAssemblyStatistics:
    simulations = tuple(section.simulation_status for section in plan.sections)
    return ProgramAssemblyStatistics(
        operation_count=len(plan.sections),
        section_count=len(plan.sections),
        tool_change_count=len(plan.sections),
        record_count=sum(section.program_ir.statistics.record_count for section in plan.sections),
        motion_count=sum(section.program_ir.statistics.motion_count for section in plan.sections),
        pass_count=sum(item is SimulationStatus.PASS for item in simulations),
        warn_count=sum(item is SimulationStatus.WARN for item in simulations),
        optional_missing_count=sum(item is None for item in simulations),
        line_count=len(text.splitlines()),
        byte_length=byte_length,
    )


def _from_post_diagnostic(
    diagnostic, operation_id, section_index: int
) -> ProgramAssemblyDiagnostic:
    code = (
        ProgramAssemblyDiagnosticCode.UNSUPPORTED_TAPPING
        if diagnostic.message_key == "post.fanuc.tapping_unsupported"
        else ProgramAssemblyDiagnosticCode.SECTION_INVALID
    )
    return ProgramAssemblyDiagnostic(
        diagnostic.severity,
        code,
        "assembly.section_invalid",
        operation_id=operation_id,
        section_index=section_index,
        record_index=diagnostic.record_index,
        evidence=(("post_code", diagnostic.code.value),),
    )


def _has_errors(diagnostics: list[ProgramAssemblyDiagnostic]) -> bool:
    return any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)


def _stale_execution(key: str) -> ProgramAssemblyExecution:
    diagnostic = ProgramAssemblyDiagnostic(
        DiagnosticSeverity.ERROR,
        ProgramAssemblyDiagnosticCode.STALE,
        key,
    )
    return ProgramAssemblyExecution(
        False,
        None,
        (diagnostic,),
        ProgramAssemblyStatus.STALE,
    )
