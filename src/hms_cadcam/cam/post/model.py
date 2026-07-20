"""Controller-neutral single-operation post-processing contracts.

The post package deliberately owns a second IR.  Toolpath events remain the
only source of motion; this module adds program-level state and typed records
without introducing controller syntax or native objects.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, TypeAlias
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamInvariantError, CamUnitError, CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    MachineDefinitionId,
    NCProgramId,
    OperationId,
    PostProcessorDefinitionId,
    ProductionControllerProfileId,
    PostRequestId,
    PostResultId,
    SetupId,
    ToolAssemblyId,
    ToolDefinitionId,
    HolderDefinitionId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.machine import MachineKind, OperationCapability, SpindleDirection, TappingMode
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.setup import WorkOffset
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, LengthUnit, SpindleSpeed
from hms_cadcam.cam.toolpath.events import (
    CoolantState,
    FeedMode,
    MotionClass,
    SpindleState,
)
from hms_cadcam.cam.toolpath.geometry import Pose, validate_arc

if TYPE_CHECKING:
    from hms_cadcam.cam.post.profile import ProductionControllerProfile, ProductionProgramContext


POST_FORMAT = "HMS_CAM_POST"
POST_VERSION = 1
NC_PROGRAM_FORMAT = "HMS_CAM_NC_PROGRAM_IR"
NC_PROGRAM_VERSION = 1
POST_RESULT_FORMAT = "HMS_CAM_POST_RESULT"
LOWERING_POLICY_FORMAT = "HMS_CAM_POST_LOWERING_POLICY"
SIMULATION_GATE_FORMAT = "HMS_CAM_POST_SIMULATION_GATE"

_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_AXIS = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,31}")


class PostDiagnosticCode(StrEnum):
    INVALID_REQUEST = "post.invalid_request"
    SOURCE_MISSING = "post.source_missing"
    SOURCE_STALE = "post.source_stale"
    SOURCE_INVALID = "post.source_invalid"
    MIXED_PROVENANCE = "post.mixed_provenance"
    MACHINE_INCOMPATIBLE = "post.machine_incompatible"
    TOOL_MISSING = "post.tool_missing"
    TOOL_STALE = "post.tool_stale"
    SETUP_INVALID = "post.setup_invalid"
    UNIT_MISMATCH = "post.unit_mismatch"
    UNSUPPORTED_MOTION = "post.unsupported_motion"
    UNSUPPORTED_FEED_MODE = "post.unsupported_feed_mode"
    UNSUPPORTED_CYCLE = "post.unsupported_cycle"
    UNSUPPORTED_SPINDLE = "post.unsupported_spindle"
    UNSUPPORTED_COOLANT = "post.unsupported_coolant"
    ARC_INVALID = "post.arc_invalid"
    RAPID_UNSAFE = "post.rapid_unsafe"
    SIMULATION_MISSING = "post.simulation_missing"
    SIMULATION_STALE = "post.simulation_stale"
    SIMULATION_FAILED = "post.simulation_failed"
    LOWERING_FAILED = "post.lowering_failed"
    FORMAT_FAILED = "post.format_failed"
    VALIDATION_FAILED = "post.validation_failed"
    STALE_RESULT = "post.stale_result"
    CANCELLED = "post.cancelled"
    FAILED = "post.failed"


class PostResultStatus(StrEnum):
    PUBLISHED = "published"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class CoordinateMode(StrEnum):
    ABSOLUTE = "absolute"
    INCREMENTAL = "incremental"


class Plane(StrEnum):
    XY = "xy"
    YZ = "yz"
    ZX = "zx"


class ArcCenterFormat(StrEnum):
    IJK = "ijk"
    RADIUS = "radius"


class SimulationGateMode(StrEnum):
    REQUIRE_PASS = "require_pass"
    ALLOW_WARN = "allow_warn"
    OPTIONAL = "optional"


class NCRecordKind(StrEnum):
    PROGRAM_BEGIN = "program_begin"
    UNITS = "units"
    COORDINATE_MODE = "coordinate_mode"
    PLANE = "plane"
    WORK_OFFSET = "work_offset"
    TOOL_ACTIVATION = "tool_activation"
    FEED_MODE = "feed_mode"
    FEED_VALUE = "feed_value"
    SPINDLE_DIRECTION = "spindle_direction"
    SPINDLE_START = "spindle_start"
    SPINDLE_STOP = "spindle_stop"
    COOLANT = "coolant"
    RAPID = "rapid"
    LINEAR = "linear"
    ARC = "arc"
    DWELL = "dwell"
    SEMANTIC_MARKER = "semantic_marker"
    PROGRAM_END = "program_end"


def _finite(value: float, name: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or (non_negative and normalized < 0.0):
        raise CamValidationError(f"{name} must be finite and valid")
    return normalized


def _text(value: str, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    return value.strip()


def _tuple_text(values: tuple[str, ...], name: str, *, pattern: re.Pattern[str] = _KEY) -> tuple[str, ...]:
    if not isinstance(values, tuple) or any(not isinstance(value, str) or pattern.fullmatch(value) is None for value in values):
        raise CamValidationError(f"{name} is invalid")
    normalized = tuple(sorted(set(values)))
    if len(normalized) != len(values):
        raise CamInvariantError(f"{name} must be unique")
    return normalized


def _evidence(values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, tuple) or len(item) != 2 or
        not all(isinstance(value, str) and value for value in item)
        for item in values
    ):
        raise CamValidationError("Post evidence is invalid")
    normalized = tuple(sorted(values))
    if len({key for key, _ in normalized}) != len(normalized):
        raise CamInvariantError("Post evidence keys must be unique")
    forbidden = {"native", "native_handle", "path", "output_path", "runtime", "timestamp", "created_at"}
    if any(key.casefold() in forbidden or any(ord(char) < 32 for char in key + value)
           for key, value in normalized):
        raise CamValidationError("Post evidence contains runtime/native or control data")
    return normalized


def _uuid(value: UUID, name: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise CamValidationError(f"{name} must be a non-nil UUID")
    return value


@dataclass(frozen=True, slots=True)
class PostDiagnostic:
    severity: DiagnosticSeverity
    code: PostDiagnosticCode
    message_key: str
    operation_id: OperationId | None = None
    artifact_id: ToolpathArtifactId | None = None
    event_index: int | None = None
    record_index: int | None = None
    evidence: tuple[tuple[str, str], ...] = ()
    schema_version: int = POST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported post diagnostic version")
        if not isinstance(self.severity, DiagnosticSeverity) or not isinstance(self.code, PostDiagnosticCode):
            raise CamValidationError("Post diagnostic enums are invalid")
        object.__setattr__(self, "message_key", _text(self.message_key, "Diagnostic message key", maximum=256))
        if _KEY.fullmatch(self.message_key) is None:
            raise CamValidationError("Diagnostic message key is invalid")
        if self.operation_id is not None and not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Post diagnostic operation ID is invalid")
        if self.artifact_id is not None and not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Post diagnostic artifact ID is invalid")
        for value, name in ((self.event_index, "event_index"), (self.record_index, "record_index")):
            if value is not None and (type(value) is not int or value < 0):
                raise CamValidationError(f"Post diagnostic {name} is invalid")
        object.__setattr__(self, "evidence", _evidence(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import diagnostic_to_dict
        return diagnostic_to_dict(self)


@dataclass(frozen=True, slots=True)
class PostStatistics:
    record_count: int
    motion_count: int
    rapid_count: int
    linear_count: int
    arc_count: int
    dwell_count: int
    total_rapid_length: float
    total_cutting_length: float
    total_link_length: float
    total_retract_length: float
    total_arc_length: float
    dwell_seconds: float
    schema_version: int = POST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported post statistics version")
        counts = (self.record_count, self.motion_count, self.rapid_count, self.linear_count, self.arc_count, self.dwell_count)
        if any(type(value) is not int or value < 0 for value in counts):
            raise CamValidationError("Post statistics counts are invalid")
        values = (self.total_rapid_length, self.total_cutting_length, self.total_link_length,
                  self.total_retract_length, self.total_arc_length, self.dwell_seconds)
        if any(_finite(value, "Post statistic", non_negative=True) != value for value in values):
            raise CamValidationError("Post statistics values are invalid")
        if self.motion_count != self.rapid_count + self.linear_count + self.arc_count:
            raise CamInvariantError("Post motion statistics do not balance")

    @classmethod
    def calculate(cls, records: tuple["NCRecord", ...]) -> "PostStatistics":
        rapid = cutting = link = retract = arc = dwell = 0.0
        counts = {kind: 0 for kind in (NCRecordKind.RAPID, NCRecordKind.LINEAR, NCRecordKind.ARC, NCRecordKind.DWELL)}
        for record in records:
            counts[record.kind] = counts.get(record.kind, 0) + 1
            if isinstance(record, RapidMotionRecord):
                rapid += record.length
            elif isinstance(record, (LinearMotionRecord, ArcMotionRecord)):
                if record.motion_class is MotionClass.CUTTING:
                    cutting += record.length
                elif record.motion_class is MotionClass.LINK:
                    link += record.length
                elif record.motion_class is MotionClass.RETRACT:
                    retract += record.length
                if isinstance(record, ArcMotionRecord):
                    arc += record.length
            elif isinstance(record, DwellRecord):
                dwell += record.duration_seconds
        return cls(len(records), counts.get(NCRecordKind.RAPID, 0) + counts.get(NCRecordKind.LINEAR, 0) + counts.get(NCRecordKind.ARC, 0),
                   counts.get(NCRecordKind.RAPID, 0), counts.get(NCRecordKind.LINEAR, 0), counts.get(NCRecordKind.ARC, 0),
                   counts.get(NCRecordKind.DWELL, 0), rapid, cutting, link, retract, arc, dwell)

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import statistics_to_dict
        return statistics_to_dict(self)


@dataclass(frozen=True, slots=True)
class LoweringPolicy:
    preserve_motion: bool = True
    preserve_semantic_markers: bool = True
    allow_canned_cycles: bool = False
    allow_arc_to_line: bool = False
    schema_version: int = POST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported lowering policy version")
        if any(type(value) is not bool for value in (self.preserve_motion, self.preserve_semantic_markers, self.allow_canned_cycles, self.allow_arc_to_line)):
            raise CamValidationError("Lowering policy flags must be boolean")
        if not self.preserve_motion or self.allow_canned_cycles or self.allow_arc_to_line:
            raise CamInvariantError("Post v1 lowering policy cannot drop, optimize or approximate motion")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"format": LOWERING_POLICY_FORMAT, "format_version": self.schema_version,
                "preserve_motion": self.preserve_motion, "preserve_semantic_markers": self.preserve_semantic_markers,
                "allow_canned_cycles": self.allow_canned_cycles, "allow_arc_to_line": self.allow_arc_to_line}


@dataclass(frozen=True, slots=True)
class SimulationGatePolicy:
    mode: SimulationGateMode = SimulationGateMode.REQUIRE_PASS
    schema_version: int = POST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation gate policy version")
        if not isinstance(self.mode, SimulationGateMode):
            raise CamValidationError("Simulation gate mode is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"format": SIMULATION_GATE_FORMAT, "format_version": self.schema_version, "mode": self.mode.value}


_DEFAULT_STRATEGIES = (
    "facing_2_5d", "contour_2d", "pocket_2_5d", "drilling_v1", "tapping_v1", "reaming_v1", "boring_v1",
)


@dataclass(frozen=True, slots=True)
class PostProcessorCapabilities:
    supported_machine_kinds: tuple[MachineKind, ...] = (MachineKind.MILL,)
    supported_axes: tuple[str, ...] = ("X", "Y", "Z")
    supported_units: tuple[LengthUnit, ...] = (LengthUnit.MM, LengthUnit.INCH)
    supported_feed_modes: tuple[FeedMode, ...] = (FeedMode.UNITS_PER_MINUTE, FeedMode.UNITS_PER_REVOLUTION)
    supported_spindle_directions: tuple[SpindleDirection, ...] = (SpindleDirection.CLOCKWISE, SpindleDirection.COUNTERCLOCKWISE)
    supported_coolant_modes: tuple[CoolantState, ...] = (CoolantState.OFF, CoolantState.FLOOD, CoolantState.MIST, CoolantState.THROUGH_TOOL)
    supported_arc_planes: tuple[Plane, ...] = (Plane.XY,)
    arc_center_formats: tuple[ArcCenterFormat, ...] = (ArcCenterFormat.IJK,)
    supported_operation_strategies: tuple[str, ...] = _DEFAULT_STRATEGIES
    supported_operation_capabilities: tuple[OperationCapability, ...] = (OperationCapability.MILLING, OperationCapability.DRILLING, OperationCapability.TAPPING)
    work_offset_supported: bool = True
    tool_activation_supported: bool = True
    tapping_synchronization: bool = False
    tapping_modes: tuple[TappingMode, ...] = ()
    minimum_rpm: float | None = None
    maximum_rpm: float | None = None
    maximum_feed: float | None = None
    schema_version: int = POST_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported post capabilities version")
        for values, typ, name in (
            (self.supported_machine_kinds, MachineKind, "machine kinds"),
            (self.supported_units, LengthUnit, "units"),
            (self.supported_feed_modes, FeedMode, "feed modes"),
            (self.supported_spindle_directions, SpindleDirection, "spindle directions"),
            (self.supported_coolant_modes, CoolantState, "coolant modes"),
            (self.supported_arc_planes, Plane, "arc planes"),
            (self.arc_center_formats, ArcCenterFormat, "arc center formats"),
            (self.supported_operation_capabilities, OperationCapability, "operation capabilities"),
            (self.tapping_modes, TappingMode, "tapping modes"),
        ):
            if not isinstance(values, tuple) or any(not isinstance(item, typ) for item in values):
                raise CamValidationError(f"Supported {name} are invalid")
            if len(set(values)) != len(values):
                raise CamInvariantError(f"Supported {name} must be unique")
        object.__setattr__(self, "supported_axes", _tuple_text(self.supported_axes, "supported axes", pattern=_AXIS))
        object.__setattr__(self, "supported_operation_strategies", _tuple_text(self.supported_operation_strategies, "supported strategies"))
        if LengthUnit.UNKNOWN in self.supported_units or FeedMode.INVERSE_TIME in self.supported_feed_modes:
            raise CamUnitError("Post capabilities cannot advertise UNKNOWN or inverse-time units")
        if any(strategy not in _DEFAULT_STRATEGIES for strategy in self.supported_operation_strategies):
            raise CamValidationError("Post capabilities contain an unsupported strategy key")
        if not isinstance(self.work_offset_supported, bool) or not isinstance(self.tool_activation_supported, bool) or not isinstance(self.tapping_synchronization, bool):
            raise CamValidationError("Post capability flags are invalid")
        if self.tapping_modes and not self.tapping_synchronization:
            raise CamInvariantError("Tapping modes require tapping synchronization")
        for value, name in ((self.minimum_rpm, "minimum RPM"), (self.maximum_rpm, "maximum RPM"), (self.maximum_feed, "maximum feed")):
            if value is not None:
                _finite(value, name, non_negative=True)
        if self.minimum_rpm is not None and self.maximum_rpm is not None and self.minimum_rpm > self.maximum_rpm:
            raise CamInvariantError("Post RPM limits are inverted")

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import capabilities_to_dict
        return capabilities_to_dict(self)


@dataclass(frozen=True, slots=True)
class PostProcessorDefinition:
    definition_id: PostProcessorDefinitionId
    definition_version: int
    adapter_key: str
    adapter_version: int
    capabilities: PostProcessorCapabilities
    numeric_precision: int = 17
    newline: str = "\n"
    encoding: str = "utf-8"
    maximum_line_length: int = 1024
    maximum_program_size: int = 8 * 1024 * 1024
    allow_comments: bool = True
    comment_prefix: str = ";"
    display_name: str | None = None
    schema_version: int = POST_VERSION
    production_profile: ProductionControllerProfile | None = None

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION or type(self.definition_version) is not int or self.definition_version <= 0 or type(self.adapter_version) is not int or self.adapter_version <= 0:
            raise UnsupportedCamSchemaError("Unsupported post definition version")
        if not isinstance(self.definition_id, PostProcessorDefinitionId) or not isinstance(self.capabilities, PostProcessorCapabilities):
            raise CamValidationError("Post definition identity/capabilities are invalid")
        object.__setattr__(self, "adapter_key", _KEY.fullmatch(self.adapter_key).group(0) if isinstance(self.adapter_key, str) and _KEY.fullmatch(self.adapter_key) else (_ for _ in ()).throw(CamValidationError("Post adapter key is invalid")))
        if type(self.numeric_precision) is not int or not 1 <= self.numeric_precision <= 17:
            raise CamValidationError("Post numeric precision is invalid")
        if self.newline not in {"\n", "\r\n"} or not isinstance(self.encoding, str) or self.encoding.casefold() != "utf-8":
            raise CamValidationError("Post text policy must be UTF-8 with normalized newline")
        if type(self.maximum_line_length) is not int or self.maximum_line_length <= 0 or type(self.maximum_program_size) is not int or self.maximum_program_size <= 0:
            raise CamValidationError("Post text size policy is invalid")
        if not isinstance(self.allow_comments, bool) or not isinstance(self.comment_prefix, str) or not self.comment_prefix or any(char in self.comment_prefix for char in "\r\n"):
            raise CamValidationError("Post comment policy is invalid")
        if self.display_name is not None:
            object.__setattr__(self, "display_name", _text(self.display_name, "Post display name", maximum=255))
        if self.production_profile is not None:
            from hms_cadcam.cam.post.profile import ProductionControllerProfile
            if not isinstance(self.production_profile, ProductionControllerProfile):
                raise CamValidationError("Production controller profile is invalid")
            if self.production_profile.adapter_key != self.adapter_key or self.production_profile.adapter_version != self.adapter_version:
                raise CamInvariantError("Production profile and adapter identity differ")
            if self.newline != self.production_profile.newline or self.encoding.casefold() != self.production_profile.encoding.casefold():
                raise CamInvariantError("Production profile and definition text policies differ")
            if self.maximum_line_length != self.production_profile.maximum_line_length or self.maximum_program_size != self.production_profile.maximum_program_size:
                raise CamInvariantError("Production profile and definition size policies differ")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        data = self.to_dict().copy()
        data.pop("display_name", None)
        data.pop("definition_id", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import definition_to_dict
        return definition_to_dict(self)


@dataclass(frozen=True, slots=True)
class PostRequest:
    project_id: UUID
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    post_definition: PostProcessorDefinition
    lowering_policy: LoweringPolicy = LoweringPolicy()
    simulation_gate_policy: SimulationGatePolicy = SimulationGatePolicy()
    request_id: PostRequestId = None  # type: ignore[assignment]
    algorithm_version: int = POST_VERSION
    program_context: ProductionProgramContext | None = None

    def __post_init__(self) -> None:
        if self.algorithm_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported post request version")
        _uuid(self.project_id, "Project ID")
        if not isinstance(self.operation_id, OperationId) or not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Post request source identity is invalid")
        if not isinstance(self.post_definition, PostProcessorDefinition) or not isinstance(self.lowering_policy, LoweringPolicy) or not isinstance(self.simulation_gate_policy, SimulationGatePolicy):
            raise CamValidationError("Post request policy/definition is invalid")
        if self.request_id is None:
            object.__setattr__(self, "request_id", PostRequestId.new())
        if not isinstance(self.request_id, PostRequestId):
            raise CamValidationError("Post request ID is invalid")
        if self.program_context is not None:
            from hms_cadcam.cam.post.profile import ProductionProgramContext
            if not isinstance(self.program_context, ProductionProgramContext):
                raise CamValidationError("Production program context is invalid")
            if self.post_definition.production_profile is None:
                raise CamInvariantError("Production context requires a production profile")
        elif self.post_definition.production_profile is not None:
            raise CamInvariantError("Production profile requires a program context")

    @property
    def input_policy_fingerprint(self) -> DependencyFingerprint:
        payload = {
            "algorithm_version": self.algorithm_version,
            "project_id": str(self.project_id),
            "operation_id": str(self.operation_id),
            "artifact_id": str(self.artifact_id),
            "post_definition": self.post_definition.fingerprint.to_dict(),
            "lowering_policy": self.lowering_policy.fingerprint.to_dict(),
            "simulation_gate_policy": self.simulation_gate_policy.fingerprint.to_dict(),
        }
        if self.program_context is not None:
            payload["program_context"] = self.program_context.to_dict()
        return DependencyFingerprint.from_payload(payload)

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import request_to_dict
        return request_to_dict(self)


@dataclass(frozen=True, slots=True)
class NCRecord:
    sequence_index: int
    kind: ClassVar[NCRecordKind]

    def __post_init__(self) -> None:
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise CamValidationError("NC record sequence is invalid")


@dataclass(frozen=True, slots=True)
class ProgramBeginRecord(NCRecord):
    metadata: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[NCRecordKind] = NCRecordKind.PROGRAM_BEGIN

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "metadata", _evidence(self.metadata))


@dataclass(frozen=True, slots=True)
class UnitsRecord(NCRecord):
    unit: LengthUnit = LengthUnit.MM
    kind: ClassVar[NCRecordKind] = NCRecordKind.UNITS

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("NC units cannot be UNKNOWN")


@dataclass(frozen=True, slots=True)
class CoordinateModeRecord(NCRecord):
    mode: CoordinateMode = CoordinateMode.ABSOLUTE
    kind: ClassVar[NCRecordKind] = NCRecordKind.COORDINATE_MODE

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.mode, CoordinateMode):
            raise CamValidationError("NC coordinate mode is invalid")


@dataclass(frozen=True, slots=True)
class PlaneRecord(NCRecord):
    plane: Plane = Plane.XY
    kind: ClassVar[NCRecordKind] = NCRecordKind.PLANE

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.plane, Plane):
            raise CamValidationError("NC plane is invalid")


@dataclass(frozen=True, slots=True)
class WorkOffsetRecord(NCRecord):
    work_offset: WorkOffset | None = None
    kind: ClassVar[NCRecordKind] = NCRecordKind.WORK_OFFSET

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.work_offset, WorkOffset):
            raise CamValidationError("NC work offset is invalid")


@dataclass(frozen=True, slots=True)
class ToolActivationRecord(NCRecord):
    tool_assembly_id: ToolAssemblyId = None  # type: ignore[assignment]
    tool_assembly_fingerprint: ContentFingerprint = None  # type: ignore[assignment]
    tool_id: ToolDefinitionId | None = None
    holder_id: HolderDefinitionId | None = None
    kind: ClassVar[NCRecordKind] = NCRecordKind.TOOL_ACTIVATION

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_assembly_id, ToolAssemblyId) or not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("NC tool activation identity is invalid")
        if self.tool_id is not None and not isinstance(self.tool_id, ToolDefinitionId):
            raise CamValidationError("NC tool activation tool ID is invalid")
        if self.holder_id is not None and not isinstance(self.holder_id, HolderDefinitionId):
            raise CamValidationError("NC tool activation holder ID is invalid")


@dataclass(frozen=True, slots=True)
class FeedModeRecord(NCRecord):
    mode: FeedMode = FeedMode.UNITS_PER_MINUTE
    kind: ClassVar[NCRecordKind] = NCRecordKind.FEED_MODE

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.mode is FeedMode.INVERSE_TIME:
            raise CamValidationError("Inverse-time feed is unsupported in post v1")


@dataclass(frozen=True, slots=True)
class FeedValueRecord(NCRecord):
    feed_rate: FeedRate = None  # type: ignore[assignment]
    kind: ClassVar[NCRecordKind] = NCRecordKind.FEED_VALUE

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.feed_rate, FeedRate):
            raise CamValidationError("NC feed value is invalid")


@dataclass(frozen=True, slots=True)
class SpindleDirectionRecord(NCRecord):
    direction: SpindleDirection = SpindleDirection.CLOCKWISE
    kind: ClassVar[NCRecordKind] = NCRecordKind.SPINDLE_DIRECTION

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.direction, SpindleDirection):
            raise CamValidationError("NC spindle direction is invalid")


@dataclass(frozen=True, slots=True)
class SpindleStartRecord(NCRecord):
    direction: SpindleDirection = SpindleDirection.CLOCKWISE
    speed: SpindleSpeed = None  # type: ignore[assignment]
    kind: ClassVar[NCRecordKind] = NCRecordKind.SPINDLE_START

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.direction, SpindleDirection) or not isinstance(self.speed, SpindleSpeed):
            raise CamValidationError("NC spindle start is invalid")


@dataclass(frozen=True, slots=True)
class SpindleStopRecord(NCRecord):
    kind: ClassVar[NCRecordKind] = NCRecordKind.SPINDLE_STOP


@dataclass(frozen=True, slots=True)
class CoolantRecord(NCRecord):
    state: CoolantState = CoolantState.OFF
    kind: ClassVar[NCRecordKind] = NCRecordKind.COOLANT

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.state, CoolantState):
            raise CamValidationError("NC coolant state is invalid")


@dataclass(frozen=True, slots=True)
class RapidMotionRecord(NCRecord):
    start: Pose = None  # type: ignore[assignment]
    end: Pose = None  # type: ignore[assignment]
    motion_class: MotionClass = MotionClass.NON_CUTTING
    rapid_rate: FeedRate | None = None
    provenance: str = "motion.rapid"
    kind: ClassVar[NCRecordKind] = NCRecordKind.RAPID

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.start, Pose) or not isinstance(self.end, Pose) or not isinstance(self.motion_class, MotionClass):
            raise CamValidationError("NC rapid motion is invalid")
        if self.start.position.unit is not self.end.position.unit:
            raise CamUnitError("NC rapid motion unit mismatch")
        if self.rapid_rate is not None and not isinstance(self.rapid_rate, FeedRate):
            raise CamValidationError("NC rapid rate is invalid")
        object.__setattr__(self, "provenance", _text(self.provenance, "NC rapid provenance", maximum=256))

    @property
    def length(self) -> float:
        from hms_cadcam.cam.toolpath.geometry import distance
        return distance(self.start.position, self.end.position)


@dataclass(frozen=True, slots=True)
class LinearMotionRecord(NCRecord):
    start: Pose = None  # type: ignore[assignment]
    end: Pose = None  # type: ignore[assignment]
    feed_rate: FeedRate = None  # type: ignore[assignment]
    motion_class: MotionClass = MotionClass.CUTTING
    provenance: str = "motion.linear"
    engagement: tuple[tuple[str, str], ...] = ()
    kind: ClassVar[NCRecordKind] = NCRecordKind.LINEAR

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.start, Pose) or not isinstance(self.end, Pose) or not isinstance(self.feed_rate, FeedRate) or not isinstance(self.motion_class, MotionClass):
            raise CamValidationError("NC linear motion is invalid")
        if self.start.position.unit is not self.end.position.unit:
            raise CamUnitError("NC linear motion unit mismatch")
        object.__setattr__(self, "provenance", _text(self.provenance, "NC linear provenance", maximum=256))
        object.__setattr__(self, "engagement", _evidence(self.engagement))

    @property
    def length(self) -> float:
        from hms_cadcam.cam.toolpath.geometry import distance
        return distance(self.start.position, self.end.position)


@dataclass(frozen=True, slots=True)
class ArcMotionRecord(NCRecord):
    start: Pose = None  # type: ignore[assignment]
    end: Pose = None  # type: ignore[assignment]
    center: Point3 = None  # type: ignore[assignment]
    plane_normal: Vector3 = None  # type: ignore[assignment]
    sweep_radians: float = 0.0
    feed_rate: FeedRate = None  # type: ignore[assignment]
    motion_class: MotionClass = MotionClass.CUTTING
    provenance: str = "motion.arc"
    kind: ClassVar[NCRecordKind] = NCRecordKind.ARC

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.start, Pose) or not isinstance(self.end, Pose) or not isinstance(self.center, Point3) or not isinstance(self.plane_normal, Vector3) or not isinstance(self.feed_rate, FeedRate) or not isinstance(self.motion_class, MotionClass):
            raise CamValidationError("NC arc motion is invalid")
        try:
            validate_arc(self.start, self.end, self.center, self.plane_normal, self.sweep_radians)
        except (CamValidationError, CamInvariantError):
            raise
        object.__setattr__(self, "sweep_radians", _finite(self.sweep_radians, "NC arc sweep"))
        object.__setattr__(self, "provenance", _text(self.provenance, "NC arc provenance", maximum=256))

    @property
    def length(self) -> float:
        return math.sqrt((self.start.position.x - self.center.x) ** 2 + (self.start.position.y - self.center.y) ** 2 + (self.start.position.z - self.center.z) ** 2) * abs(self.sweep_radians)


@dataclass(frozen=True, slots=True)
class DwellRecord(NCRecord):
    duration_seconds: float = 0.0
    provenance: str = "process.dwell"
    kind: ClassVar[NCRecordKind] = NCRecordKind.DWELL

    def __post_init__(self) -> None:
        super().__post_init__()
        if not math.isfinite(float(self.duration_seconds)) or self.duration_seconds <= 0.0:
            raise CamValidationError("NC dwell must be finite and positive")
        object.__setattr__(self, "duration_seconds", float(self.duration_seconds))
        object.__setattr__(self, "provenance", _text(self.provenance, "NC dwell provenance", maximum=256))


@dataclass(frozen=True, slots=True)
class SemanticMarkerRecord(NCRecord):
    semantic_key: str = "semantic.marker"
    message: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    provenance: str = "semantic.marker"
    kind: ClassVar[NCRecordKind] = NCRecordKind.SEMANTIC_MARKER

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "semantic_key", _KEY.fullmatch(self.semantic_key).group(0) if isinstance(self.semantic_key, str) and _KEY.fullmatch(self.semantic_key) else (_ for _ in ()).throw(CamValidationError("NC semantic key is invalid")))
        if self.message is not None:
            object.__setattr__(self, "message", _text(self.message, "NC marker message", maximum=4096))
        object.__setattr__(self, "metadata", _evidence(self.metadata))
        object.__setattr__(self, "provenance", _text(self.provenance, "NC marker provenance", maximum=256))


@dataclass(frozen=True, slots=True)
class ProgramEndRecord(NCRecord):
    kind: ClassVar[NCRecordKind] = NCRecordKind.PROGRAM_END


NCRecordUnion: TypeAlias = (
    ProgramBeginRecord | UnitsRecord | CoordinateModeRecord | PlaneRecord | WorkOffsetRecord |
    ToolActivationRecord | FeedModeRecord | FeedValueRecord | SpindleDirectionRecord |
    SpindleStartRecord | SpindleStopRecord | CoolantRecord | RapidMotionRecord |
    LinearMotionRecord | ArcMotionRecord | DwellRecord | SemanticMarkerRecord | ProgramEndRecord
)


@dataclass(frozen=True, slots=True)
class NCProgramIR:
    program_id: NCProgramId
    project_id: UUID
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    strategy_key: str
    strategy_version: int
    unit: LengthUnit
    coordinate_mode: CoordinateMode
    plane: Plane
    setup_id: SetupId
    setup_revision: Revision
    wcs: WcsFrame
    work_offset: WorkOffset
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: ContentFingerprint
    records: tuple[NCRecordUnion, ...]
    diagnostics: tuple[PostDiagnostic, ...]
    statistics: PostStatistics
    program_fingerprint: ContentFingerprint | None = None
    schema_version: int = NC_PROGRAM_VERSION
    production_context: ProductionProgramContext | None = None

    def __post_init__(self) -> None:
        if self.schema_version != NC_PROGRAM_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NCProgramIR version")
        _uuid(self.project_id, "Program project ID")
        for value, typ, name in ((self.program_id, NCProgramId, "program"), (self.operation_id, OperationId, "operation"), (self.artifact_id, ToolpathArtifactId, "artifact"), (self.setup_id, SetupId, "setup"), (self.tool_assembly_id, ToolAssemblyId, "tool assembly")):
            if not isinstance(value, typ):
                raise CamValidationError(f"Program {name} identity is invalid")
        if not isinstance(self.artifact_fingerprint, ContentFingerprint) or not isinstance(self.tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Program provenance fingerprint is invalid")
        if not isinstance(self.strategy_key, str) or _KEY.fullmatch(self.strategy_key) is None or type(self.strategy_version) is not int or self.strategy_version <= 0:
            raise CamValidationError("Program strategy identity is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN or not isinstance(self.coordinate_mode, CoordinateMode) or not isinstance(self.plane, Plane):
            raise CamValidationError("Program unit/mode/plane is invalid")
        if not isinstance(self.setup_revision, Revision) or not isinstance(self.wcs, WcsFrame) or (self.wcs.origin.unit is not self.unit):
            raise CamValidationError("Program setup/WCS provenance is invalid")
        if not isinstance(self.work_offset, WorkOffset):
            raise CamValidationError("Program work offset is invalid")
        if self.production_context is not None:
            from hms_cadcam.cam.post.profile import ProductionProgramContext
            if not isinstance(self.production_context, ProductionProgramContext):
                raise CamValidationError("Program production context is invalid")
            if self.production_context.safe_z.unit is not self.unit:
                raise CamUnitError("Program production-context unit mismatch")
        if not isinstance(self.records, tuple) or any(not isinstance(item, NCRecord) for item in self.records):
            raise CamValidationError("Program records must be an immutable typed tuple")
        if any(record.sequence_index != index for index, record in enumerate(self.records)):
            raise CamInvariantError("Program record sequence must be contiguous")
        if not self.records or not isinstance(self.records[0], ProgramBeginRecord) or not isinstance(self.records[-1], ProgramEndRecord):
            raise CamInvariantError("Program must have explicit begin and end records")
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, PostDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Program diagnostics are invalid")
        normalized = tuple(sorted(self.diagnostics, key=lambda item: (item.severity.value, item.code.value, item.event_index if item.event_index is not None else -1, item.record_index if item.record_index is not None else -1, item.evidence)))
        object.__setattr__(self, "diagnostics", normalized)
        calculated = PostStatistics.calculate(self.records)
        if calculated != self.statistics:
            raise CamInvariantError("Program statistics do not match records")
        from hms_cadcam.cam.post.codec import compute_program_fingerprint
        calculated_fp = compute_program_fingerprint(self)
        if self.program_fingerprint is None:
            object.__setattr__(self, "program_fingerprint", calculated_fp)
        elif self.program_fingerprint != calculated_fp:
            raise CamInvariantError("Program fingerprint verification failed")

    @classmethod
    def create(cls, **kwargs: Any) -> "NCProgramIR":
        records = tuple(kwargs.pop("records"))
        kwargs["records"] = records
        kwargs.setdefault("statistics", PostStatistics.calculate(records))
        kwargs.setdefault("diagnostics", ())
        kwargs.setdefault("program_fingerprint", None)
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import program_to_dict
        return program_to_dict(self)


@dataclass(frozen=True, slots=True)
class PostResult:
    result_id: PostResultId
    project_id: UUID
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    post_definition_id: PostProcessorDefinitionId
    post_definition_version: int
    post_definition_fingerprint: ContentFingerprint
    setup_id: SetupId
    setup_revision: Revision
    setup_fingerprint: ContentFingerprint
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: ContentFingerprint
    tool_fingerprint: ContentFingerprint | None
    holder_id: HolderDefinitionId | None
    holder_fingerprint: ContentFingerprint | None
    machine_id: MachineDefinitionId | None
    machine_fingerprint: ContentFingerprint | None
    simulation_fingerprint: ContentFingerprint | None
    program_ir_fingerprint: ContentFingerprint | None
    output_checksum: str | None
    canonical_text: str | None
    status: PostResultStatus
    diagnostics: tuple[PostDiagnostic, ...]
    statistics: PostStatistics
    result_fingerprint: ContentFingerprint | None = None
    schema_version: int = POST_VERSION
    production_profile_id: ProductionControllerProfileId | None = None
    production_profile_version: int | None = None
    production_profile_fingerprint: ContentFingerprint | None = None
    tool_binding_fingerprint: ContentFingerprint | None = None
    program_context_fingerprint: ContentFingerprint | None = None
    validated_unit: LengthUnit | None = None
    validated_feed_modes: tuple[FeedMode, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != POST_VERSION:
            raise UnsupportedCamSchemaError("Unsupported post result version")
        _uuid(self.project_id, "Result project ID")
        for value, typ, name in ((self.result_id, PostResultId, "result"), (self.operation_id, OperationId, "operation"), (self.artifact_id, ToolpathArtifactId, "artifact"), (self.post_definition_id, PostProcessorDefinitionId, "post definition"), (self.setup_id, SetupId, "setup"), (self.tool_assembly_id, ToolAssemblyId, "tool assembly")):
            if not isinstance(value, typ):
                raise CamValidationError(f"Result {name} identity is invalid")
        for value, name in ((self.artifact_fingerprint, "artifact"), (self.input_fingerprint, "input"), (self.post_definition_fingerprint, "post definition"), (self.setup_fingerprint, "setup"), (self.tool_assembly_fingerprint, "tool assembly")):
            if not isinstance(value, (ContentFingerprint, DependencyFingerprint)):
                raise CamValidationError(f"Result {name} fingerprint is invalid")
        if self.tool_fingerprint is not None and not isinstance(self.tool_fingerprint, ContentFingerprint):
            raise CamValidationError("Result tool fingerprint is invalid")
        if (self.holder_id is None) != (self.holder_fingerprint is None) or (self.machine_id is None) != (self.machine_fingerprint is None):
            raise CamInvariantError("Result optional provenance must be paired")
        if self.holder_id is not None and not isinstance(self.holder_id, HolderDefinitionId):
            raise CamValidationError("Result holder identity is invalid")
        if self.machine_id is not None and not isinstance(self.machine_id, MachineDefinitionId):
            raise CamValidationError("Result machine identity is invalid")
        for value, name in ((self.holder_fingerprint, "holder"), (self.machine_fingerprint, "machine"), (self.simulation_fingerprint, "simulation"), (self.program_ir_fingerprint, "program")):
            if value is not None and not isinstance(value, ContentFingerprint):
                raise CamValidationError(f"Result {name} fingerprint is invalid")
        if not isinstance(self.setup_revision, Revision) or type(self.post_definition_version) is not int or self.post_definition_version <= 0:
            raise CamValidationError("Result revisions are invalid")
        if not isinstance(self.status, PostResultStatus) or not isinstance(self.statistics, PostStatistics):
            raise CamValidationError("Result status/statistics are invalid")
        if self.canonical_text is not None and not isinstance(self.canonical_text, str):
            raise CamValidationError("Result canonical text is invalid")
        if self.output_checksum is not None and (not isinstance(self.output_checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", self.output_checksum)):
            raise CamValidationError("Result output checksum is invalid")
        if self.canonical_text is not None and self.output_checksum is not None and hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest() != self.output_checksum:
            raise CamInvariantError("Result output checksum verification failed")
        if self.status is PostResultStatus.PUBLISHED and (self.canonical_text is None or self.output_checksum is None or self.program_ir_fingerprint is None):
            raise CamInvariantError("Published result requires canonical output and IR fingerprint")
        production_values = (
            self.production_profile_id, self.production_profile_version,
            self.production_profile_fingerprint, self.tool_binding_fingerprint,
            self.program_context_fingerprint, self.validated_unit,
        )
        if any(value is not None for value in production_values):
            if any(value is None for value in production_values):
                raise CamInvariantError("Production result provenance must be complete")
            if not isinstance(self.production_profile_id, ProductionControllerProfileId) or type(self.production_profile_version) is not int or self.production_profile_version <= 0:
                raise CamValidationError("Production result profile identity is invalid")
            if any(not isinstance(value, ContentFingerprint) for value in (self.production_profile_fingerprint, self.tool_binding_fingerprint, self.program_context_fingerprint)):
                raise CamValidationError("Production result fingerprint is invalid")
            if not isinstance(self.validated_unit, LengthUnit) or self.validated_unit is LengthUnit.UNKNOWN:
                raise CamValidationError("Production result validated unit is invalid")
            if not self.validated_feed_modes:
                raise CamInvariantError("Production result requires validated feed modes")
        elif self.validated_feed_modes:
            raise CamInvariantError("Neutral result cannot carry production feed metadata")
        if not isinstance(self.validated_feed_modes, tuple) or any(not isinstance(item, FeedMode) for item in self.validated_feed_modes) or len(set(self.validated_feed_modes)) != len(self.validated_feed_modes):
            raise CamValidationError("Result validated feed modes are invalid")
        object.__setattr__(self, "validated_feed_modes", tuple(sorted(self.validated_feed_modes, key=lambda item: item.value)))
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, PostDiagnostic) for item in self.diagnostics):
            raise CamValidationError("Result diagnostics are invalid")
        object.__setattr__(self, "diagnostics", tuple(sorted(self.diagnostics, key=lambda item: (item.severity.value, item.code.value, item.event_index if item.event_index is not None else -1, item.record_index if item.record_index is not None else -1, item.evidence))))
        from hms_cadcam.cam.post.codec import compute_result_fingerprint
        calculated = compute_result_fingerprint(self)
        if self.result_fingerprint is None:
            object.__setattr__(self, "result_fingerprint", calculated)
        elif self.result_fingerprint != calculated:
            raise CamInvariantError("Post result fingerprint verification failed")

    @classmethod
    def create(cls, **kwargs: Any) -> "PostResult":
        kwargs.setdefault("result_fingerprint", None)
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.codec import result_to_dict
        return result_to_dict(self)


PostRecord = NCRecordUnion
