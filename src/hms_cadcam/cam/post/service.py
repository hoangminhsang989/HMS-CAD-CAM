"""Runtime-only latest-wins post execution registry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Callable
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.ids import PostResultId
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.post.adapter import PostProcessorAdapter
from hms_cadcam.cam.post.dummy import CanonicalDummyAdapter
from hms_cadcam.cam.post.lowering import PostSourceSnapshot, lower_toolpath
from hms_cadcam.cam.post.model import (
    FeedModeRecord, PostDiagnostic, PostDiagnosticCode, PostRequest, PostResult,
    PostResultStatus,
)
from hms_cadcam.cam.post.validation import validate_request


@dataclass(frozen=True, slots=True)
class PostComputationToken:
    value: UUID
    generation: int
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class PostExecution:
    accepted: bool
    result: PostResult | None
    diagnostics: tuple[PostDiagnostic, ...]
    status: PostResultStatus


def build_post_input_fingerprint(request: PostRequest, source: PostSourceSnapshot) -> DependencyFingerprint:
    """Build the identity fingerprint from current semantic inputs only."""
    simulation = source.simulation_result
    payload = {
        "algorithm_version": request.algorithm_version,
        "project_id": str(source.project_id),
        "operation": {"id": str(source.operation.operation_id), "revision": source.operation.revision.to_dict(), "enabled": source.operation.enabled, "strategy_key": source.operation.strategy_key, "strategy_version": source.operation.strategy_version},
        "artifact": {"id": str(source.artifact.artifact_id), "fingerprint": source.artifact.artifact_fingerprint.to_dict() if source.artifact.artifact_fingerprint else None, "input_fingerprint": source.artifact.input_fingerprint.to_dict()},
        "setup": {"id": str(source.setup.setup_id), "revision": source.setup.revision.to_dict(), "wcs": source.setup.wcs.to_dict(), "work_offset": source.setup.work_offset.to_dict()},
        "assembly": source.assembly.to_dict(),
        "tool": source.tool.to_dict() if source.tool else None,
        "holder": source.holder.to_dict() if source.holder else None,
        "machine": source.machine.to_dict() if source.machine else None,
        "post_definition": request.post_definition.identity_payload(),
        "lowering_policy": request.lowering_policy.to_dict(),
        "simulation_gate_policy": request.simulation_gate_policy.to_dict(),
        "simulation_fingerprint": simulation.result_fingerprint.to_dict() if simulation else None,
        "expected_simulation_input_fingerprint": source.expected_simulation_input_fingerprint.to_dict() if source.expected_simulation_input_fingerprint else None,
    }
    if request.program_context is not None:
        payload["production_program_context"] = request.program_context.to_dict()
    return DependencyFingerprint.from_payload(payload)


class PostRuntimeService:
    """In-memory registry with atomic latest-wins publication semantics."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._generation = 0
        self._entries: dict[tuple[UUID, object, object, object], tuple[PostComputationToken, PostResult]] = {}
        self._latest: dict[tuple[UUID, object, object, object], PostComputationToken] = {}

    @staticmethod
    def _key(request: PostRequest) -> tuple[UUID, object, object, object]:
        return (request.project_id, request.operation_id, request.artifact_id, request.post_definition.definition_id)

    def begin(self, request: PostRequest, source: PostSourceSnapshot) -> PostComputationToken:
        fingerprint = build_post_input_fingerprint(request, source)
        with self._lock:
            self._generation += 1
            token = PostComputationToken(uuid4(), self._generation, fingerprint)
            key = self._key(request)
            for variant in tuple(existing_key for existing_key in self._entries if existing_key[:3] == key[:3] and existing_key != key):
                self._entries.pop(variant, None)
                self._latest.pop(variant, None)
            previous = self._entries.get(key)
            if previous is not None and previous[0].input_fingerprint != fingerprint:
                self._entries.pop(key, None)
            self._latest[key] = token
            return token

    def current(self, request: PostRequest) -> PostResult | None:
        with self._lock:
            entry = self._entries.get(self._key(request))
            return entry[1] if entry else None

    def results(self) -> tuple[PostResult, ...]:
        with self._lock:
            return tuple(item[1] for item in sorted(self._entries.values(), key=lambda pair: str(pair[1].result_id)))

    def publish(self, request: PostRequest, token: PostComputationToken, result: PostResult, *, current_input: DependencyFingerprint | None = None) -> bool:
        with self._lock:
            if token.input_fingerprint != (current_input or token.input_fingerprint):
                return False
            key = self._key(request)
            if self._latest.get(key) != token:
                return False
            if (result.operation_id != request.operation_id or result.artifact_id != request.artifact_id or
                    result.post_definition_id != request.post_definition.definition_id or
                    result.status is not PostResultStatus.PUBLISHED):
                return False
            existing = self._entries.get(key)
            if existing is not None and existing[0].generation > token.generation:
                return False
            self._entries[key] = (token, result)
            return True

    def mark_stale(self, operation_id: object) -> None:
        with self._lock:
            self._entries = {key: value for key, value in self._entries.items() if key[1] != operation_id}
            self._latest = {key: value for key, value in self._latest.items() if key[1] != operation_id}

    def invalidate_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._latest.clear()

    def clear(self) -> None:
        self.invalidate_all()

    def post(self, request: PostRequest, source: PostSourceSnapshot, adapter: PostProcessorAdapter | None = None, *, current_source: Callable[[], PostSourceSnapshot] | None = None) -> PostExecution:
        if adapter is None:
            if request.post_definition.adapter_key == "canonical_dummy":
                adapter = CanonicalDummyAdapter()
            elif request.post_definition.adapter_key == "fanuc_robodrill_21i_worknc_v1":
                from hms_cadcam.cam.post.fanuc_robodrill_21i import FanucRobodrill21iAdapter
                adapter = FanucRobodrill21iAdapter(request.post_definition)
            else:
                diagnostic = PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.adapter_unavailable")
                return PostExecution(False, None, (diagnostic,), PostResultStatus.BLOCKED)
        token = self.begin(request, source)
        try:
            diagnostics = list(validate_request(request, request.post_definition))
            diagnostics.extend(adapter.validate_request(request))
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.INVALID_REQUEST, "post.request_validation_failed", error, PostResultStatus.BLOCKED)
        if _has_errors(diagnostics):
            return PostExecution(False, None, tuple(diagnostics), PostResultStatus.BLOCKED)
        try:
            program = lower_toolpath(request, source)
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.LOWERING_FAILED, "post.lowering_failed", error, PostResultStatus.BLOCKED)
        try:
            diagnostics.extend(program.diagnostics)
            diagnostics.extend(adapter.validate_program_ir(program))
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.VALIDATION_FAILED, "post.program_validation_failed", error, PostResultStatus.BLOCKED)
        if _has_errors(diagnostics):
            return PostExecution(False, None, tuple(diagnostics), PostResultStatus.BLOCKED)
        try:
            program = adapter.lower_program_ir(program)
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.LOWERING_FAILED, "post.adapter_lowering_failed", error, PostResultStatus.BLOCKED)
        try:
            text = adapter.format_program(program, request.post_definition)
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.FORMAT_FAILED, "post.format_failed", error, PostResultStatus.FAILED)
        try:
            diagnostics.extend(adapter.validate_output(text, program, request.post_definition))
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.VALIDATION_FAILED, "post.output_validation_failed", error, PostResultStatus.FAILED)
        if _has_errors(diagnostics):
            return PostExecution(False, None, tuple(diagnostics), PostResultStatus.FAILED)
        try:
            current = current_source() if current_source else source
            if build_post_input_fingerprint(request, current) != token.input_fingerprint:
                return PostExecution(False, None, (PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.STALE_RESULT, "post.input_changed"),), PostResultStatus.STALE)
            output_checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
            sim = source.simulation_result
            profile = request.post_definition.production_profile
            context = request.program_context
            feed_modes = tuple(sorted({record.mode for record in program.records if isinstance(record, FeedModeRecord)}, key=lambda item: item.value))
            result = PostResult.create(result_id=PostResultId.new(), project_id=source.project_id, operation_id=source.operation.operation_id, artifact_id=source.artifact.artifact_id, artifact_fingerprint=source.artifact.artifact_fingerprint, input_fingerprint=token.input_fingerprint, post_definition_id=request.post_definition.definition_id, post_definition_version=request.post_definition.definition_version, post_definition_fingerprint=request.post_definition.fingerprint, setup_id=source.setup.setup_id, setup_revision=source.setup.revision, setup_fingerprint=ContentFingerprint.from_payload(source.setup.to_dict()), tool_assembly_id=source.assembly.assembly_id, tool_assembly_fingerprint=source.assembly.content_fingerprint, tool_fingerprint=source.tool.content_fingerprint if source.tool else None, holder_id=source.holder.holder_id if source.holder else None, holder_fingerprint=source.holder.content_fingerprint if source.holder else None, machine_id=source.machine.machine_id if source.machine else None, machine_fingerprint=source.machine.content_fingerprint if source.machine else None, simulation_fingerprint=sim.result_fingerprint if sim else None, program_ir_fingerprint=program.program_fingerprint, output_checksum=output_checksum, canonical_text=text, status=PostResultStatus.PUBLISHED, diagnostics=tuple(diagnostics), statistics=program.statistics, production_profile_id=profile.profile_id if profile else None, production_profile_version=profile.profile_version if profile else None, production_profile_fingerprint=profile.fingerprint if profile else None, tool_binding_fingerprint=context.tool_binding.fingerprint if context else None, program_context_fingerprint=context.fingerprint if context else None, validated_unit=program.unit if profile else None, validated_feed_modes=feed_modes if profile else ())
            if not self.publish(request, token, result, current_input=build_post_input_fingerprint(request, current)):
                return PostExecution(False, None, (PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.STALE_RESULT, "post.publish_stale"),), PostResultStatus.STALE)
            return PostExecution(True, result, tuple(diagnostics), PostResultStatus.PUBLISHED)
        except Exception as error:
            return _failed_execution(PostDiagnosticCode.FAILED, "post.failed", error, PostResultStatus.FAILED)


def _failed_execution(code: PostDiagnosticCode, key: str, error: Exception, status: PostResultStatus) -> PostExecution:
    evidence = " ".join(str(error).split())[:256] or type(error).__name__
    diagnostic = PostDiagnostic(DiagnosticSeverity.ERROR, code, key, evidence=(("error", evidence),))
    return PostExecution(False, None, (diagnostic,), status)


def _has_errors(diagnostics: list[PostDiagnostic]) -> bool:
    return any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)
