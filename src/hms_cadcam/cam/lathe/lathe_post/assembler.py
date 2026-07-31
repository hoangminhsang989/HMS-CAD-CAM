"""Semantic Lathe toolpath-to-Program-IR assembly and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity, canonical_id
from hms_cadcam.cam.lathe.lathe_post.ir import (
    DwellPayload,
    LatheProgramBlock,
    LatheProgramBlockKind,
    LatheProgramDiagnostic,
    LatheProgramIRV1,
    LatheSemanticPlane,
    LatheSpindleAction,
    LatheSpindleDirection,
    LatheUnits,
    MotionPayload,
    OperationPayload,
    PlanePayload,
    ProgramBeginPayload,
    SpindleIntentPayload,
    ThreadCutIntentPayload,
    ToolIntentPayload,
    UnitsPayload,
)
from hms_cadcam.cam.lathe.lathe_post.profile import LathePostProfile, lathe_post_profile_registry
from hms_cadcam.cam.lathe.toolpath.model import LatheDwellEvent, LatheMotionClass, LathePathSegment


class LatheProgramDiagnosticCode(StrEnum):
    EMPTY_OPERATION_LIST = "EMPTY_OPERATION_LIST"
    PROGRAM_OWNERSHIP_STALE = "PROGRAM_OWNERSHIP_STALE"
    OPERATION_OWNERSHIP_MISMATCH = "OPERATION_OWNERSHIP_MISMATCH"
    OPERATION_REVISION_MISMATCH = "OPERATION_REVISION_MISMATCH"
    MISSING_TOOLPATH = "MISSING_TOOLPATH"
    TOOLPATH_NOT_SUCCESS = "TOOLPATH_NOT_SUCCESS"
    TOOLPATH_OWNERSHIP_MISMATCH = "TOOLPATH_OWNERSHIP_MISMATCH"
    TOOLPATH_REVISION_MISMATCH = "TOOLPATH_REVISION_MISMATCH"
    TOOLPATH_FINGERPRINT_MISMATCH = "TOOLPATH_FINGERPRINT_MISMATCH"
    DUPLICATE_OPERATION = "DUPLICATE_OPERATION"
    MISSING_TOOL_BINDING = "MISSING_TOOL_BINDING"
    INVALID_MOTION = "INVALID_MOTION"
    NON_FINITE_COORDINATE = "NON_FINITE_COORDINATE"
    INVALID_FEED = "INVALID_FEED"
    THREAD_FEED_MISMATCH = "THREAD_FEED_MISMATCH"
    UNSUPPORTED_UNITS = "UNSUPPORTED_UNITS"
    UNSUPPORTED_PLANE = "UNSUPPORTED_PLANE"
    SEQUENCE_INVALID = "SEQUENCE_INVALID"
    MACHINE_SPECIFIC_BLOCK = "MACHINE_SPECIFIC_BLOCK"
    PRODUCTION_POST_UNAVAILABLE = "PRODUCTION_POST_UNAVAILABLE"
    PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
    INVALID_SPINDLE = "INVALID_SPINDLE"
    PARTIAL_ASSEMBLY = "PARTIAL_ASSEMBLY"


@dataclass(frozen=True, slots=True)
class LatheOperationProgramInput:
    """Optional explicit adapter for one ordered operation/result pair."""

    operation: object
    toolpath_result: object | None = None
    tool_binding: object | None = None
    spindle_direction: str | None = None
    spindle_speed_rpm: float | None = None


@dataclass(frozen=True, slots=True)
class LatheProgramAssemblyResult:
    program: LatheProgramIRV1 | None
    diagnostics: tuple[LatheProgramDiagnostic, ...]
    fingerprint: str | None = None

    @property
    def ir(self) -> LatheProgramIRV1 | None:
        return self.program

    @property
    def accepted(self) -> bool:
        return self.program is not None and not self.diagnostics

    @property
    def success(self) -> bool:
        return self.accepted


def _value(obj: object, *names: str, default: object = None) -> object:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _revision(value: object) -> int | None:
    if value is None:
        return None
    raw = _value(value, "value", default=value)
    return raw if type(raw) is int else None


def _operation_id(operation: object) -> str:
    ownership = _value(operation, "ownership")
    return canonical_id(_value(ownership, "operation_id", default=_value(operation, "operation_id")), "operation_id")


def _ownership(operation: object) -> object:
    return _value(operation, "ownership", default=operation)


def _operation_strategy(operation: object) -> str:
    return canonical_id(_text(_value(operation, "strategy_id")), "strategy_id")


def _operation_revision(operation: object) -> int | None:
    return _revision(_value(operation, "revision"))


def _canonical_owner(identity: LatheProgramIdentity, owner: object) -> bool:
    return (
        _text(_value(owner, "project_id")) == identity.project_id
        and _text(_value(owner, "document_id")) == identity.document_id
        and _text(_value(owner, "source_id")) == identity.source_id
        and _value(owner, "generation", default=_value(owner, "source_generation")) == identity.source_generation
        and _text(_value(owner, "setup_id")) == identity.setup_id
    )


def _params(operation: object) -> dict[str, object]:
    raw = _value(operation, "parameter_values", default=None)
    if raw is None:
        state = _value(operation, "parameter_state", default=None)
        raw = _value(state, "values", default=())
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        return {str(key): value for key, value in raw}
    except (TypeError, ValueError):
        return {}


def _spindle_for(operation: object, explicit: LatheOperationProgramInput | None = None) -> tuple[str, float]:
    params = _params(operation)
    direction = _text(explicit.spindle_direction if explicit else params.get("spindle_direction", "CW"))
    speed = explicit.spindle_speed_rpm if explicit and explicit.spindle_speed_rpm is not None else params.get("spindle_speed_rpm", 1.0)
    if direction not in {"CW", "CCW"}:
        raise ValueError("invalid spindle direction")
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(float(speed)) or float(speed) <= 0.0:
        raise ValueError("invalid spindle speed")
    return direction, float(speed)


def _result_fingerprint(result: object) -> str:
    identity = _value(result, "identity")
    fingerprint = _value(identity, "fingerprint", default=_value(result, "fingerprint"))
    return canonical_id(_value(fingerprint, "digest", default=fingerprint), "toolpath fingerprint")


def _result_owner(result: object) -> object:
    identity = _value(result, "identity")
    return _value(identity, "ownership", default=_value(result, "ownership"))


def _result_revision(result: object) -> int | None:
    identity = _value(result, "identity")
    return _revision(_value(identity, "operation_revision", default=_value(result, "operation_revision")))


def _result_success(result: object) -> bool:
    succeeded = _value(result, "succeeded", default=None)
    if succeeded is not None:
        return bool(succeeded)
    state = _text(_value(result, "state", default=""))
    return state.upper() == "SUCCESS"


def _result_motions(result: object) -> tuple[object, ...]:
    raw = _value(result, "motions", default=())
    return tuple(raw) if isinstance(raw, Iterable) else ()


def _metadata_pairs(raw: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(raw, tuple):
        return ()
    return tuple((str(key), value) for key, value in raw if isinstance(key, str))


def _meta(metadata: tuple[tuple[str, object], ...], key: str, default: object = None) -> object:
    return next((value for name, value in metadata if name == key), default)


def _thread_pass(result: object, pass_index: int | None) -> object | None:
    passes = _value(result, "thread_pass_metadata", "thread_passes", default=())
    if pass_index is None:
        return None
    return next((item for item in passes if _value(item, "pass_index") == pass_index), None)


class LatheProgramAssemblerV1:
    """One synchronous, deterministic assembly coordinator for Lathe IR."""

    def __init__(self, profile: LathePostProfile | None = None) -> None:
        self.profile = profile or lathe_post_profile_registry().neutral_preview()
        if not isinstance(self.profile, LathePostProfile):
            raise TypeError("profile must be LathePostProfile")

    def assemble(
        self,
        identity: LatheProgramIdentity,
        operations: Sequence[object] | Iterable[object],
        results: Mapping[object, object] | Sequence[object] | None = None,
        tool_bindings: Mapping[object, object] | None = None,
        spindle_intents: Mapping[object, object] | None = None,
        profile: LathePostProfile | None = None,
        *,
        accepted_results: Mapping[object, object] | Sequence[object] | None = None,
        selected_profile: LathePostProfile | None = None,
    ) -> LatheProgramAssemblyResult:
        if accepted_results is not None:
            results = accepted_results
        if selected_profile is not None:
            profile = selected_profile
        if not isinstance(identity, LatheProgramIdentity):
            raise TypeError("identity must be LatheProgramIdentity")
        ordered = tuple(operations)
        selected_profile = profile or self.profile
        diagnostics: list[LatheProgramDiagnostic] = []
        if not ordered:
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.EMPTY_OPERATION_LIST))
        if (not isinstance(selected_profile, LathePostProfile) or selected_profile.profile_id != self.profile.profile_id or selected_profile.is_executable):
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.PROFILE_UNAVAILABLE))
        result_map = self._result_map(ordered, results)
        seen: set[str] = set()
        blocks: list[LatheProgramBlock] = []
        if not diagnostics:
            blocks.extend(
                [
                    LatheProgramBlock(0, LatheProgramBlockKind.PROGRAM_BEGIN, ProgramBeginPayload(identity), None, "program"),
                    LatheProgramBlock(1, LatheProgramBlockKind.SET_UNITS, UnitsPayload(), None, "program"),
                    LatheProgramBlock(2, LatheProgramBlockKind.SET_PLANE, PlanePayload(), None, "program"),
                ]
            )
        for index, raw_operation in enumerate(ordered):
            operation = raw_operation.operation if isinstance(raw_operation, LatheOperationProgramInput) else raw_operation
            explicit = raw_operation if isinstance(raw_operation, LatheOperationProgramInput) else None
            operation_id = "?"
            try:
                operation_id = _operation_id(operation)
                if operation_id in seen:
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.DUPLICATE_OPERATION, operation_id))
                seen.add(operation_id)
                owner = _ownership(operation)
                if not _canonical_owner(identity, owner):
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.OPERATION_OWNERSHIP_MISMATCH, operation_id))
                result = explicit.toolpath_result if explicit and explicit.toolpath_result is not None else result_map.get(operation_id)
                if result is None:
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.MISSING_TOOLPATH, operation_id))
                    continue
                if not _result_success(result):
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.TOOLPATH_NOT_SUCCESS, operation_id))
                    continue
                if not _canonical_owner(identity, _result_owner(result)):
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.TOOLPATH_OWNERSHIP_MISMATCH, operation_id))
                if _result_revision(result) is not None and _result_revision(result) != _operation_revision(operation):
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.TOOLPATH_REVISION_MISMATCH, operation_id))
                tool = explicit.tool_binding if explicit and explicit.tool_binding is not None else _value(operation, "tool_binding")
                if tool is None and tool_bindings is not None:
                    tool = tool_bindings.get(operation_id) or tool_bindings.get(_value(owner, "operation_id"))
                if tool is None:
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.MISSING_TOOL_BINDING, operation_id))
                    continue
                strategy = _operation_strategy(operation)
                fingerprint = _result_fingerprint(result)
                expected_fingerprint = _value(operation, "toolpath_fingerprint", "accepted_toolpath_fingerprint", "result_fingerprint")
                if expected_fingerprint is not None and _text(_value(expected_fingerprint, "digest", default=expected_fingerprint)) != fingerprint:
                    diagnostics.append(self._diag(LatheProgramDiagnosticCode.TOOLPATH_FINGERPRINT_MISMATCH, operation_id))
                    continue
                if not blocks:
                    continue
                start_index = len(blocks)
                blocks.append(LatheProgramBlock(start_index, LatheProgramBlockKind.OPERATION_BEGIN, OperationPayload(operation_id, strategy, _operation_revision(operation) or 0), operation_id, operation_id))
                blocks.append(LatheProgramBlock(len(blocks), LatheProgramBlockKind.TOOL_INTENT, self._tool_payload(tool), operation_id, operation_id))
                direction, speed = _spindle_for(operation, explicit)
                if spindle_intents and operation_id in spindle_intents:
                    custom = spindle_intents[operation_id]
                    direction = _text(_value(custom, "direction", default=direction))
                    speed = float(_value(custom, "speed_rpm", "rpm", default=speed))
                blocks.append(LatheProgramBlock(len(blocks), LatheProgramBlockKind.SPINDLE_INTENT, SpindleIntentPayload(LatheSpindleAction.START, LatheSpindleDirection(direction), speed), operation_id, operation_id))
                for event in _result_motions(result):
                    blocks.append(self._motion_block(len(blocks), event, strategy, fingerprint, result, operation_id))
                blocks.append(LatheProgramBlock(len(blocks), LatheProgramBlockKind.SPINDLE_INTENT, SpindleIntentPayload(LatheSpindleAction.STOP), operation_id, operation_id))
                blocks.append(LatheProgramBlock(len(blocks), LatheProgramBlockKind.OPERATION_END, OperationPayload(operation_id, strategy, _operation_revision(operation) or 0), operation_id, operation_id))
            except (TypeError, ValueError, KeyError, AttributeError) as error:
                code = LatheProgramDiagnosticCode.INVALID_MOTION if "motion" in str(error).casefold() else LatheProgramDiagnosticCode.INVALID_SPINDLE
                diagnostics.append(self._diag(code, operation_id, str(error)))
        if diagnostics:
            return LatheProgramAssemblyResult(None, tuple(diagnostics), None)
        blocks.append(LatheProgramBlock(len(blocks), LatheProgramBlockKind.PROGRAM_END, OperationPayload(identity.program_id, "PROGRAM", identity.revision), None, "program"))
        program = LatheProgramIRV1(identity, tuple(blocks), selected_profile.profile_id)
        digest = program.semantic_fingerprint()
        program = LatheProgramIRV1(identity, program.blocks, selected_profile.profile_id, fingerprint=digest)
        return LatheProgramAssemblyResult(program, (), digest)

    def validate(self, program: LatheProgramIRV1 | None) -> tuple[LatheProgramDiagnostic, ...]:
        if program is None:
            return (self._diag(LatheProgramDiagnosticCode.PARTIAL_ASSEMBLY),)
        diagnostics: list[LatheProgramDiagnostic] = []
        blocks = program.blocks
        if not blocks or blocks[0].kind is not LatheProgramBlockKind.PROGRAM_BEGIN:
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.SEQUENCE_INVALID))
        if not blocks or blocks[-1].kind is not LatheProgramBlockKind.PROGRAM_END:
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.SEQUENCE_INVALID))
        if sum(item.kind is LatheProgramBlockKind.SET_UNITS for item in blocks) != 1:
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.UNSUPPORTED_UNITS))
        if sum(item.kind is LatheProgramBlockKind.SET_PLANE for item in blocks) != 1:
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.UNSUPPORTED_PLANE))
        if program.fingerprint != program.semantic_fingerprint():
            diagnostics.append(self._diag(LatheProgramDiagnosticCode.TOOLPATH_FINGERPRINT_MISMATCH))
        return tuple(diagnostics)

    def _result_map(self, operations: tuple[object, ...], results: Mapping[object, object] | Sequence[object] | None) -> dict[str, object]:
        if results is None:
            return { _operation_id(item.operation if isinstance(item, LatheOperationProgramInput) else item): _value(item, "toolpath_result", "result") for item in operations if _value(item, "toolpath_result", "result") is not None }
        if isinstance(results, Mapping):
            return {str(key): value for key, value in results.items()}
        return {
            _operation_id(item.operation if isinstance(item, LatheOperationProgramInput) else item): result
            for item, result in zip(operations, results)
        }

    def _tool_payload(self, tool: object) -> ToolIntentPayload:
        capabilities = _value(tool, "resolved_capabilities", "capabilities", default=frozenset())
        return ToolIntentPayload(
            _text(_value(tool, "tool_id")),
            None if _value(tool, "profile_id") is None else _text(_value(tool, "profile_id")),
            _text(_value(tool, "assembly_id")),
            _revision(_value(tool, "tool_revision")),
            _revision(_value(tool, "profile_revision")),
            _revision(_value(tool, "assembly_revision")),
            tuple(sorted(_text(item) for item in capabilities)),
        )

    def _motion_block(self, sequence: int, event: object, strategy: str, fingerprint: str, result: object, operation_id: str) -> LatheProgramBlock:
        if isinstance(event, LatheDwellEvent):
            payload = DwellPayload(event.position, event.duration_seconds, strategy, fingerprint)
            return LatheProgramBlock(sequence, LatheProgramBlockKind.DWELL_INTENT, payload, operation_id, event.semantic_source)
        if not isinstance(event, LathePathSegment):
            raise ValueError("invalid motion event")
        if not math.isfinite(event.start.x_diameter_mm) or not math.isfinite(event.start.z_mm) or not math.isfinite(event.end.x_diameter_mm) or not math.isfinite(event.end.z_mm):
            raise ValueError("non-finite motion coordinate")
        metadata = _metadata_pairs(event.metadata)
        pass_index = _meta(metadata, "pass_index")
        pass_number = int(pass_index) if isinstance(pass_index, int) else None
        kind = {
            LatheMotionClass.RAPID: LatheProgramBlockKind.RAPID_MOTION,
            LatheMotionClass.CUTTING: LatheProgramBlockKind.LINEAR_CUT_MOTION,
            LatheMotionClass.LEAD_IN: LatheProgramBlockKind.LEAD_IN_MOTION,
            LatheMotionClass.LEAD_OUT: LatheProgramBlockKind.LEAD_OUT_MOTION,
        }.get(event.motion_class)
        if kind is None:
            raise ValueError("invalid motion class")
        if strategy in {"lathe.od_thread.v1", "lathe.id_thread.v1"} and event.motion_class is LatheMotionClass.CUTTING:
            pass_data = _thread_pass(result, pass_number)
            pitch = float(_value(pass_data, "pitch_mm", default=_meta(metadata, "pitch_mm", event.feed_mm_per_rev)))
            if event.feed_mm_per_rev is None or not math.isclose(float(event.feed_mm_per_rev), pitch, rel_tol=0.0, abs_tol=1.0e-9):
                raise ValueError("thread feed differs from pitch")
            payload = ThreadCutIntentPayload(
                event.start,
                event.end,
                event.feed_mm_per_rev,
                strategy,
                pass_number,
                fingerprint,
                metadata,
                thread_strategy=strategy.split(".")[1].upper(),
                pitch_mm=pitch,
                thread_hand=_text(_value(pass_data, "thread_hand", default=_meta(metadata, "thread_hand", "RIGHT"))),
                spring_pass=_value(pass_data, "spring_pass_index", default=_meta(metadata, "spring_pass_index")) is not None,
                cumulative_radial_depth_mm=float(_value(pass_data, "cumulative_radial_depth_mm", default=_meta(metadata, "cumulative_radial_depth_mm", 0.001))),
                cutting_diameter_mm=float(_value(pass_data, "cutting_diameter_mm", default=_meta(metadata, "cutting_diameter_mm", event.end.x_diameter_mm))),
                infeed_angle_deg=float(_value(pass_data, "infeed_angle_deg", default=_meta(metadata, "infeed_angle_deg", 0.0))),
                phase_neutral=bool(_value(pass_data, "phase_neutral", default=_meta(metadata, "phase_neutral", True))),
                algorithm_version=_text(_value(pass_data, "strategy_algorithm_version", default=_value(result, "algorithm_version", default="lathe.thread.toolpath.v3"))),
            )
            return LatheProgramBlock(sequence, LatheProgramBlockKind.THREAD_CUT_INTENT, payload, operation_id, event.semantic_source)
        payload = MotionPayload(event.start, event.end, event.feed_mm_per_rev, strategy, pass_number, fingerprint, metadata)
        return LatheProgramBlock(sequence, kind, payload, operation_id, event.semantic_source)

    @staticmethod
    def _diag(code: LatheProgramDiagnosticCode, subject: str | None = None, detail: str | None = None) -> LatheProgramDiagnostic:
        context = () if detail is None else (("detail", detail),)
        return LatheProgramDiagnostic(code.value, f"lathe.program.diagnostic.{code.value.casefold()}", subject, context)


LatheProgramAssembler = LatheProgramAssemblerV1
ProgramAssemblyResult = LatheProgramAssemblyResult


__all__ = [
    "LatheOperationProgramInput", "LatheProgramAssembler", "LatheProgramAssemblerV1",
    "LatheProgramAssemblyResult", "LatheProgramDiagnosticCode", "ProgramAssemblyResult",
]


LatheProgramAssemblerV1.assemble_program = LatheProgramAssemblerV1.assemble
