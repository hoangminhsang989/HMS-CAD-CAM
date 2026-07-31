"""Qt-free immutable Lathe Program IR V1 and typed semantic payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Any

from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity, canonical_id
from hms_cadcam.cam.lathe.toolpath.model import LatheXZPoint


PROGRAM_IR_VERSION = "lathe.program.ir.v1"
PROGRAM_ASSEMBLER_VERSION = "lathe.program.assembler.v1"
NEUTRAL_LISTING_VERSION = "lathe.neutral.listing.v1"
NEUTRAL_PROFILE_ID = "hms.lathe.neutral_program_preview.v1"


class LatheProgramBlockKind(StrEnum):
    PROGRAM_BEGIN = "PROGRAM_BEGIN"
    SET_UNITS = "SET_UNITS"
    SET_PLANE = "SET_PLANE"
    TOOL_INTENT = "TOOL_INTENT"
    SPINDLE_INTENT = "SPINDLE_INTENT"
    COOLANT_INTENT = "COOLANT_INTENT"
    RAPID_MOTION = "RAPID_MOTION"
    LINEAR_CUT_MOTION = "LINEAR_CUT_MOTION"
    LEAD_IN_MOTION = "LEAD_IN_MOTION"
    LEAD_OUT_MOTION = "LEAD_OUT_MOTION"
    THREAD_CUT_INTENT = "THREAD_CUT_INTENT"
    DWELL_INTENT = "DWELL_INTENT"
    OPERATION_BEGIN = "OPERATION_BEGIN"
    OPERATION_END = "OPERATION_END"
    PROGRAM_END = "PROGRAM_END"


PROGRAM_BLOCK_KINDS = frozenset(LatheProgramBlockKind)


class LatheUnits(StrEnum):
    MILLIMETRES = "MILLIMETRES"


class LatheSemanticPlane(StrEnum):
    LATHE_XZ_DIAMETER = "LATHE_XZ_DIAMETER"


class LatheSpindleAction(StrEnum):
    START = "START"
    STOP = "STOP"


class LatheSpindleDirection(StrEnum):
    CW = "CW"
    CCW = "CCW"


@dataclass(frozen=True, slots=True)
class ProgramBeginPayload:
    identity: LatheProgramIdentity


@dataclass(frozen=True, slots=True)
class UnitsPayload:
    units: LatheUnits = LatheUnits.MILLIMETRES


@dataclass(frozen=True, slots=True)
class PlanePayload:
    plane: LatheSemanticPlane = LatheSemanticPlane.LATHE_XZ_DIAMETER


@dataclass(frozen=True, slots=True)
class OperationPayload:
    operation_id: str
    strategy_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ToolIntentPayload:
    tool_id: str
    profile_id: str | None
    assembly_id: str
    tool_revision: int | None
    profile_revision: int | None
    assembly_revision: int | None
    resolved_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", canonical_id(self.tool_id, "tool_id"))
        object.__setattr__(self, "assembly_id", canonical_id(self.assembly_id, "assembly_id"))
        if self.profile_id is not None:
            object.__setattr__(self, "profile_id", canonical_id(self.profile_id, "profile_id"))
        for field in ("tool_revision", "profile_revision", "assembly_revision"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field} must be a non-negative integer or None")
        if not isinstance(self.resolved_capabilities, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.resolved_capabilities
        ):
            raise TypeError("resolved_capabilities must be immutable text")


@dataclass(frozen=True, slots=True)
class SpindleIntentPayload:
    action: LatheSpindleAction
    direction: LatheSpindleDirection | None = None
    speed_rpm: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, LatheSpindleAction):
            raise TypeError("spindle action is invalid")
        if self.action is LatheSpindleAction.START:
            if not isinstance(self.direction, LatheSpindleDirection):
                raise ValueError("spindle START requires direction")
            if not isinstance(self.speed_rpm, (int, float)) or isinstance(self.speed_rpm, bool):
                raise ValueError("spindle START requires a numeric speed")
            if not math.isfinite(float(self.speed_rpm)) or float(self.speed_rpm) <= 0.0:
                raise ValueError("spindle START speed must be finite and positive")
            object.__setattr__(self, "speed_rpm", float(self.speed_rpm))
        elif self.speed_rpm is not None:
            raise ValueError("spindle STOP must not require speed")


@dataclass(frozen=True, slots=True)
class MotionPayload:
    start: LatheXZPoint
    end: LatheXZPoint
    feed_mm_per_rev: float | None
    strategy_id: str
    pass_index: int | None
    toolpath_fingerprint: str
    metadata: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.start, LatheXZPoint) or not isinstance(self.end, LatheXZPoint):
            raise TypeError("motion points are invalid")
        if self.start.distance_to(self.end) <= 1.0e-9:
            raise ValueError("motion cannot be zero-length")
        if self.feed_mm_per_rev is not None:
            if not isinstance(self.feed_mm_per_rev, (int, float)) or isinstance(self.feed_mm_per_rev, bool):
                raise ValueError("motion feed is invalid")
            if not math.isfinite(float(self.feed_mm_per_rev)) or float(self.feed_mm_per_rev) <= 0.0:
                raise ValueError("motion feed must be finite and positive")
            object.__setattr__(self, "feed_mm_per_rev", float(self.feed_mm_per_rev))
        if type(self.pass_index) is not int and self.pass_index is not None:
            raise ValueError("pass_index is invalid")
        object.__setattr__(self, "strategy_id", canonical_id(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "toolpath_fingerprint", canonical_id(self.toolpath_fingerprint, "toolpath_fingerprint"))
        if not isinstance(self.metadata, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or (item[1] is not None and type(item[1]) not in {str, int, float, bool})
            for item in self.metadata
        ):
            raise TypeError("motion metadata must be immutable pairs")
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata)))


@dataclass(frozen=True, slots=True)
class ThreadCutIntentPayload(MotionPayload):
    thread_strategy: str = ""
    pitch_mm: float = 0.0
    thread_hand: str = ""
    spring_pass: bool = False
    cumulative_radial_depth_mm: float = 0.0
    cutting_diameter_mm: float = 0.0
    infeed_angle_deg: float = 0.0
    phase_neutral: bool = True
    algorithm_version: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "thread_strategy", canonical_id(self.thread_strategy, "thread_strategy"))
        for field in ("pitch_mm", "cumulative_radial_depth_mm", "cutting_diameter_mm", "infeed_angle_deg"):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{field} must be finite")
        if self.pitch_mm <= 0.0:
            raise ValueError("thread pitch must be positive")
        if type(self.spring_pass) is not bool or type(self.phase_neutral) is not bool:
            raise TypeError("thread flags must be bool")
        object.__setattr__(self, "thread_hand", canonical_id(self.thread_hand, "thread_hand"))
        object.__setattr__(self, "algorithm_version", canonical_id(self.algorithm_version, "algorithm_version"))


@dataclass(frozen=True, slots=True)
class DwellPayload:
    position: LatheXZPoint
    duration_seconds: float
    strategy_id: str
    toolpath_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.position, LatheXZPoint) or not math.isfinite(float(self.duration_seconds)) or self.duration_seconds <= 0.0:
            raise ValueError("dwell payload is invalid")
        object.__setattr__(self, "duration_seconds", float(self.duration_seconds))


@dataclass(frozen=True, slots=True)
class LatheProgramDiagnostic:
    code: str
    message_key: str
    subject: str | None = None
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", canonical_id(self.code, "diagnostic code"))
        object.__setattr__(self, "message_key", canonical_id(self.message_key, "diagnostic message key"))
        if self.subject is not None:
            object.__setattr__(self, "subject", canonical_id(self.subject, "diagnostic subject"))
        if not isinstance(self.context, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(v, str) for v in item)
            for item in self.context
        ):
            raise TypeError("diagnostic context is invalid")


@dataclass(frozen=True, slots=True)
class LatheProgramBlock:
    sequence_index: int
    kind: LatheProgramBlockKind
    payload: object
    operation_id: str | None
    semantic_source: str

    def __post_init__(self) -> None:
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise ValueError("block sequence index is invalid")
        if not isinstance(self.kind, LatheProgramBlockKind):
            raise TypeError("block kind must be an exact semantic kind")
        if self.operation_id is not None:
            object.__setattr__(self, "operation_id", canonical_id(self.operation_id, "operation_id"))
        object.__setattr__(self, "semantic_source", canonical_id(self.semantic_source, "semantic_source"))
        if isinstance(self.payload, (dict, list, set, bytearray)):
            raise TypeError("block payload must be an immutable typed value")


def _encode(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, LatheProgramIdentity):
        return value.to_canonical()
    if isinstance(value, LatheXZPoint):
        return {"x_diameter_mm": value.x_diameter_mm, "z_mm": value.z_mm}
    if isinstance(value, LatheProgramBlockKind):
        return value.value
    if is_dataclass(value):
        return {key: _encode(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    return value


@dataclass(frozen=True, slots=True)
class LatheProgramIRV1:
    identity: LatheProgramIdentity
    blocks: tuple[LatheProgramBlock, ...]
    profile_id: str = NEUTRAL_PROFILE_ID
    schema_version: str = PROGRAM_IR_VERSION
    assembler_version: str = PROGRAM_ASSEMBLER_VERSION
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LatheProgramIdentity):
            raise TypeError("program identity is invalid")
        if not isinstance(self.blocks, tuple) or any(not isinstance(item, LatheProgramBlock) for item in self.blocks):
            raise TypeError("program blocks must be immutable typed values")
        if tuple(item.sequence_index for item in self.blocks) != tuple(range(len(self.blocks))):
            raise ValueError("program block sequence must be contiguous")
        if self.schema_version != PROGRAM_IR_VERSION or self.assembler_version != PROGRAM_ASSEMBLER_VERSION:
            raise ValueError("unsupported program IR version")
        object.__setattr__(self, "profile_id", canonical_id(self.profile_id, "profile_id"))
        if self.fingerprint and (len(self.fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.fingerprint)):
            raise ValueError("program fingerprint must be a lowercase SHA-256 digest")

    def to_canonical(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "assembler_version": self.assembler_version,
            "profile_id": self.profile_id,
            "identity": self.identity.to_canonical(),
            "blocks": [
                {
                    "sequence_index": block.sequence_index,
                    "kind": block.kind.value,
                    "operation_id": block.operation_id,
                    "semantic_source": block.semantic_source,
                    "payload": _encode(block.payload),
                }
                for block in self.blocks
            ],
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def semantic_fingerprint(self) -> str:
        encoded = json.dumps(self.to_canonical(include_fingerprint=False), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


# Friendly aliases used by callers that do not need the V1 suffix.
ProgramBlockKind = LatheProgramBlockKind
LatheProgramBlock = LatheProgramBlock
LatheProgramIR = LatheProgramIRV1


__all__ = [
    "DwellPayload", "LatheProgramBlock", "LatheProgramBlockKind", "LatheProgramDiagnostic",
    "LatheProgramIR", "LatheProgramIRV1", "LatheSemanticPlane", "LatheSpindleAction",
    "LatheSpindleDirection", "LatheUnits", "MotionPayload", "NEUTRAL_LISTING_VERSION",
    "NEUTRAL_PROFILE_ID", "OperationPayload", "PlanePayload", "PROGRAM_ASSEMBLER_VERSION",
    "PROGRAM_BLOCK_KINDS", "PROGRAM_IR_VERSION", "ProgramBlockKind", "ProgramBeginPayload",
    "SpindleIntentPayload", "ThreadCutIntentPayload", "ToolIntentPayload", "UnitsPayload",
]
