"""Immutable, Qt/OCP-free Lathe Toolpath Preview V1/V2/V3 values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import ClassVar, TypeAlias
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.domain import LatheOwnershipKey
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheThreadHand

LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM = 1.0e-9
LATHE_TOOLPATH_ALGORITHM_VERSION = "lathe.toolpath.preview.v1"
LATHE_FACE_ALGORITHM_VERSION = "lathe.face.toolpath.v2"
LATHE_OD_ROUGH_ALGORITHM_VERSION = "lathe.od_rough.toolpath.v1"
LATHE_OD_FINISH_ALGORITHM_VERSION = "lathe.od_finish.toolpath.v1"
LATHE_ID_ROUGH_ALGORITHM_VERSION = "lathe.id_rough.toolpath.v2"
LATHE_ID_FINISH_ALGORITHM_VERSION = "lathe.id_finish.toolpath.v2"
LATHE_OD_GROOVE_ALGORITHM_VERSION = "lathe.od_groove.toolpath.v2"
LATHE_ID_GROOVE_ALGORITHM_VERSION = "lathe.id_groove.toolpath.v2"
LATHE_PART_OFF_ALGORITHM_VERSION = "lathe.part_off.toolpath.v2"
LATHE_OD_THREAD_ALGORITHM_VERSION = "lathe.od_thread.toolpath.v3"
LATHE_ID_THREAD_ALGORITHM_VERSION = "lathe.id_thread.toolpath.v3"
LATHE_AXIAL_DRILL_ALGORITHM_VERSION = "lathe.axial_drill.toolpath.v1"
LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY = "lathe.thread.toolpath.preview.v3"

JsonScalar: TypeAlias = str | int | float | bool | None
LatheMetadata: TypeAlias = tuple[tuple[str, JsonScalar], ...]


def finite_number(value: object, subject: str) -> float:
    """Normalize a strict finite numeric input and reject bool."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{subject} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{subject} must be finite")
    return 0.0 if normalized == 0.0 else normalized


def normalized_metadata(value: object) -> LatheMetadata:
    """Return sorted unique immutable JSON-scalar metadata."""

    if not isinstance(value, tuple):
        raise TypeError("Lathe metadata must be an immutable tuple")
    normalized: list[tuple[str, JsonScalar]] = []
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0].strip()
        ):
            raise TypeError("Lathe metadata entries must be keyed pairs")
        key, raw = item
        if raw is not None and type(raw) not in {str, int, float, bool}:
            raise TypeError("Lathe metadata values must be JSON scalars")
        if type(raw) is float and not math.isfinite(raw):
            raise ValueError("Lathe metadata float values must be finite")
        normalized.append((key.strip(), raw))
    ordered = tuple(sorted(normalized, key=lambda item: item[0]))
    if len({key for key, _raw in ordered}) != len(ordered):
        raise ValueError("Lathe metadata keys must be unique")
    return ordered


@dataclass(frozen=True, slots=True)
class LatheThreadPassMetadata:
    """One deterministic thread pass without controller or render objects."""

    pass_index: int
    cutting_pass_count: int
    spring_pass_index: int | None
    cumulative_radial_depth_mm: float
    cutting_diameter_mm: float
    pitch_mm: float
    thread_hand: LatheThreadHand
    infeed_angle_deg: float
    phase_neutral: bool
    synchronized_feed_mm_per_rev: float
    strategy_algorithm_version: str

    def __post_init__(self) -> None:
        if type(self.pass_index) is not int or self.pass_index < 0:
            raise ValueError("Lathe thread pass index is invalid")
        if type(self.cutting_pass_count) is not int or self.cutting_pass_count < 1:
            raise ValueError("Lathe thread cutting-pass count is invalid")
        if self.spring_pass_index is None:
            if self.pass_index >= self.cutting_pass_count:
                raise ValueError("Lathe cutting pass index exceeds its schedule")
        elif (
            type(self.spring_pass_index) is not int
            or self.spring_pass_index < 0
            or self.pass_index
            != self.cutting_pass_count + self.spring_pass_index
        ):
            raise ValueError("Lathe spring-pass identity is invalid")
        depth = finite_number(
            self.cumulative_radial_depth_mm,
            "Lathe thread cumulative radial depth",
        )
        diameter = finite_number(
            self.cutting_diameter_mm,
            "Lathe thread cutting diameter",
        )
        pitch = finite_number(self.pitch_mm, "Lathe thread pitch")
        infeed = finite_number(self.infeed_angle_deg, "Lathe thread infeed angle")
        synchronized_feed = finite_number(
            self.synchronized_feed_mm_per_rev,
            "Lathe thread synchronized feed",
        )
        if depth <= 0.0 or diameter <= 0.0 or pitch <= 0.0:
            raise ValueError("Lathe thread pass dimensions must be positive")
        if not isinstance(self.thread_hand, LatheThreadHand):
            raise TypeError("Lathe thread hand metadata is invalid")
        if not 0.0 <= infeed < 90.0:
            raise ValueError("Lathe thread infeed metadata is invalid")
        if self.phase_neutral is not True:
            raise ValueError("Lathe thread pass must be phase-neutral")
        if synchronized_feed != pitch:
            raise ValueError("Lathe thread synchronized feed must equal pitch")
        if self.strategy_algorithm_version not in {
            LATHE_OD_THREAD_ALGORITHM_VERSION,
            LATHE_ID_THREAD_ALGORITHM_VERSION,
        }:
            raise ValueError("Lathe thread pass algorithm version is invalid")
        object.__setattr__(self, "cumulative_radial_depth_mm", depth)
        object.__setattr__(self, "cutting_diameter_mm", diameter)
        object.__setattr__(self, "pitch_mm", pitch)
        object.__setattr__(self, "infeed_angle_deg", infeed)
        object.__setattr__(self, "synchronized_feed_mm_per_rev", synchronized_feed)

    def canonical_metadata(self) -> LatheMetadata:
        """Return the immutable JSON-scalar representation used by motions."""

        return normalized_metadata(
            (
                ("cumulative_radial_depth_mm", self.cumulative_radial_depth_mm),
                ("cutting_diameter_mm", self.cutting_diameter_mm),
                ("cutting_pass_count", self.cutting_pass_count),
                ("infeed_angle_deg", self.infeed_angle_deg),
                ("pass_index", self.pass_index),
                ("phase_neutral", self.phase_neutral),
                ("pitch_mm", self.pitch_mm),
                ("spring_pass_index", self.spring_pass_index),
                (
                    "strategy_algorithm_version",
                    self.strategy_algorithm_version,
                ),
                (
                    "synchronized_feed_mm_per_rev",
                    self.synchronized_feed_mm_per_rev,
                ),
                ("thread_hand", self.thread_hand.value),
            )
        )


@dataclass(frozen=True, slots=True, order=True)
class LatheToolpathJobId:
    """Opaque identity for one explicit preview attempt."""

    value: UUID
    PREFIX: ClassVar[str] = "lathe_toolpath_job"

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID) or self.value.int == 0:
            raise ValueError("Lathe toolpath job identity is invalid")

    @classmethod
    def new(cls) -> "LatheToolpathJobId":
        return cls(uuid4())

    def __str__(self) -> str:
        return f"{self.PREFIX}:{self.value}"


def _digest(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{subject} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class LatheToolpathFingerprint:
    contract_version: int
    digest: str

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version <= 0:
            raise ValueError("Lathe fingerprint contract version is invalid")
        object.__setattr__(
            self, "digest", _digest(self.digest, "Lathe toolpath fingerprint")
        )


@dataclass(frozen=True, slots=True)
class LatheToolpathCacheKey:
    contract_version: int
    digest: str

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version <= 0:
            raise ValueError("Lathe cache-key contract version is invalid")
        object.__setattr__(self, "digest", _digest(self.digest, "Lathe cache key"))


@dataclass(frozen=True, slots=True)
class LatheXZPoint:
    """One setup-local Lathe point; X is diameter in millimetres."""

    x_diameter_mm: float
    z_mm: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "x_diameter_mm",
            finite_number(self.x_diameter_mm, "Lathe X diameter"),
        )
        object.__setattr__(self, "z_mm", finite_number(self.z_mm, "Lathe Z"))

    def distance_to(self, other: "LatheXZPoint") -> float:
        if not isinstance(other, LatheXZPoint):
            raise TypeError("Lathe distance target must be LatheXZPoint")
        return math.hypot(
            other.x_diameter_mm - self.x_diameter_mm,
            other.z_mm - self.z_mm,
        )


class LatheMotionClass(StrEnum):
    RAPID = "RAPID"
    CUTTING = "CUTTING"
    LEAD_IN = "LEAD_IN"
    LEAD_OUT = "LEAD_OUT"


@dataclass(frozen=True, slots=True)
class LathePathSegment:
    sequence_index: int
    motion_class: LatheMotionClass
    start: LatheXZPoint
    end: LatheXZPoint
    semantic_source: str
    feed_mm_per_rev: float | None = None
    metadata: LatheMetadata = ()

    def __post_init__(self) -> None:
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise ValueError("Lathe segment sequence must be non-negative")
        if not isinstance(self.motion_class, LatheMotionClass):
            raise TypeError("Lathe segment motion class is invalid")
        if not isinstance(self.start, LatheXZPoint) or not isinstance(
            self.end, LatheXZPoint
        ):
            raise TypeError("Lathe segment endpoints are invalid")
        if self.start.distance_to(self.end) <= LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM:
            raise ValueError("Lathe segment must not be zero-length")
        if not isinstance(self.semantic_source, str) or not self.semantic_source.strip():
            raise ValueError("Lathe segment semantic source is empty")
        object.__setattr__(self, "semantic_source", self.semantic_source.strip())
        if self.motion_class is LatheMotionClass.RAPID:
            if self.feed_mm_per_rev is not None:
                raise ValueError("Rapid Lathe segment cannot carry cutting feed")
        else:
            feed = finite_number(self.feed_mm_per_rev, "Lathe segment feed")
            if feed <= 0.0:
                raise ValueError("Lathe segment feed must be positive")
            object.__setattr__(self, "feed_mm_per_rev", feed)
        object.__setattr__(self, "metadata", normalized_metadata(self.metadata))

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


@dataclass(frozen=True, slots=True)
class LatheDwellEvent:
    sequence_index: int
    position: LatheXZPoint
    duration_seconds: float
    semantic_source: str
    metadata: LatheMetadata = ()

    def __post_init__(self) -> None:
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise ValueError("Lathe dwell sequence must be non-negative")
        if not isinstance(self.position, LatheXZPoint):
            raise TypeError("Lathe dwell position is invalid")
        duration = finite_number(self.duration_seconds, "Lathe dwell duration")
        if duration <= 0.0:
            raise ValueError("Lathe dwell duration must be positive")
        object.__setattr__(self, "duration_seconds", duration)
        if not isinstance(self.semantic_source, str) or not self.semantic_source.strip():
            raise ValueError("Lathe dwell semantic source is empty")
        object.__setattr__(self, "semantic_source", self.semantic_source.strip())
        object.__setattr__(self, "metadata", normalized_metadata(self.metadata))


LatheToolpathEvent: TypeAlias = LathePathSegment | LatheDwellEvent


@dataclass(frozen=True, slots=True)
class LatheToolpathBounds:
    min_x_diameter_mm: float
    min_z_mm: float
    max_x_diameter_mm: float
    max_z_mm: float

    def __post_init__(self) -> None:
        values = tuple(
            finite_number(value, "Lathe toolpath bound")
            for value in (
                self.min_x_diameter_mm,
                self.min_z_mm,
                self.max_x_diameter_mm,
                self.max_z_mm,
            )
        )
        if values[0] > values[2] or values[1] > values[3]:
            raise ValueError("Lathe toolpath bounds are inverted")
        (
            min_x,
            min_z,
            max_x,
            max_z,
        ) = values
        object.__setattr__(self, "min_x_diameter_mm", min_x)
        object.__setattr__(self, "min_z_mm", min_z)
        object.__setattr__(self, "max_x_diameter_mm", max_x)
        object.__setattr__(self, "max_z_mm", max_z)

    @classmethod
    def from_events(
        cls, events: tuple[LatheToolpathEvent, ...]
    ) -> "LatheToolpathBounds":
        if not isinstance(events, tuple) or not events:
            raise ValueError("Lathe toolpath bounds require events")
        points: list[LatheXZPoint] = []
        for event in events:
            if isinstance(event, LathePathSegment):
                points.extend((event.start, event.end))
            elif isinstance(event, LatheDwellEvent):
                points.append(event.position)
            else:
                raise TypeError("Lathe toolpath event is invalid")
        return cls(
            min(point.x_diameter_mm for point in points),
            min(point.z_mm for point in points),
            max(point.x_diameter_mm for point in points),
            max(point.z_mm for point in points),
        )


class LatheToolpathDiagnosticCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    TOOLPATH_NOT_IMPLEMENTED_V1 = "toolpath_not_implemented_v1"
    THREAD_TOOLPATH_NOT_IMPLEMENTED_V2 = "thread_toolpath_not_implemented_v2"
    INVALID_STOCK = "invalid_stock"
    MISSING_INTERNAL_BORE = "missing_internal_bore"
    INVALID_PARAMETER = "invalid_parameter"
    STALE_OWNERSHIP = "stale_ownership"
    REVISION_MISMATCH = "revision_mismatch"
    OPERATION_NOT_READY = "operation_not_ready"
    DISABLED_OPERATION = "disabled_operation"
    READ_ONLY = "read_only"
    CLOSED = "closed"
    MISSING_GEOMETRY = "missing_geometry"
    MISSING_TOOL = "missing_tool"
    INCOMPATIBLE_GEOMETRY = "incompatible_geometry"
    INCOMPATIBLE_TOOL = "incompatible_tool"
    CANCELLED = "cancelled"
    GENERATION_FAILED = "generation_failed"
    NOMINAL_CENTERLINE_PREVIEW = "nominal_centerline_preview"
    NOMINAL_FACING_CENTERLINE_PREVIEW = "nominal_facing_centerline_preview"
    NOMINAL_INTERNAL_CENTERLINE_PREVIEW = "nominal_internal_centerline_preview"
    NOMINAL_MULTI_PLUNGE_GROOVE_PREVIEW = (
        "nominal_multi_plunge_groove_preview"
    )
    NOMINAL_INTERNAL_MULTI_PLUNGE_GROOVE_PREVIEW = (
        "nominal_internal_multi_plunge_groove_preview"
    )
    NOMINAL_PART_OFF_CENTERLINE_PREVIEW = (
        "nominal_part_off_centerline_preview"
    )
    PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW = (
        "phase_neutral_synchronized_centerline_preview"
    )
    THREAD_FEED_DERIVED_FROM_PITCH = "thread_feed_derived_from_pitch"
    NOMINAL_INFEED_ANGLE_METADATA_ONLY = "nominal_infeed_angle_metadata_only"
    NOT_MACHINE_READY = "not_machine_ready"
    INVALID_THREAD_DIAMETER_ORDER = "invalid_thread_diameter_order"
    THREAD_MAJOR_EXCEEDS_STOCK = "thread_major_exceeds_stock"
    THREAD_MINOR_BELOW_BORE = "thread_minor_below_bore"
    INVALID_PITCH = "invalid_pitch"
    INVALID_PASS_COUNT = "invalid_pass_count"
    INVALID_SPRING_PASSES = "invalid_spring_passes"
    INVALID_INFEED_ANGLE = "invalid_infeed_angle"
    THREAD_RANGE_OUTSIDE_STOCK = "thread_range_outside_stock"
    INCOMPATIBLE_THREAD_TOOL = "incompatible_thread_tool"
    INCOMPATIBLE_THREAD_GEOMETRY = "incompatible_thread_geometry"
    PUBLICATION_FAILED = "publication_failed"
    STALE_RESULT_DROPPED = "stale_result_dropped"


@dataclass(frozen=True, slots=True)
class LatheToolpathDiagnostic:
    code: LatheToolpathDiagnosticCode
    field_id: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, LatheToolpathDiagnosticCode):
            raise TypeError("Lathe toolpath diagnostic code is invalid")
        if self.field_id is not None and (
            not isinstance(self.field_id, str) or not self.field_id.strip()
        ):
            raise ValueError("Lathe toolpath diagnostic field is invalid")
        if not isinstance(self.details, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            for item in self.details
        ):
            raise TypeError("Lathe toolpath diagnostic details are invalid")
        ordered = tuple(sorted(self.details))
        if len({key for key, _value in ordered}) != len(ordered):
            raise ValueError("Lathe toolpath diagnostic detail keys are duplicated")
        object.__setattr__(self, "details", ordered)


class LatheToolpathResultState(StrEnum):
    SUCCESS = "SUCCESS"
    CANCELLED = "CANCELLED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_STRATEGY = "UNSUPPORTED_STRATEGY"
    GENERATION_FAILED = "GENERATION_FAILED"


class LatheToolpathResultSource(StrEnum):
    WORKER = "worker"
    CACHE = "cache"


@dataclass(frozen=True, slots=True)
class LatheToolpathResultIdentity:
    job_id: LatheToolpathJobId
    request_sequence: int
    ownership: LatheOwnershipKey
    operation_revision: Revision
    fingerprint: LatheToolpathFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, LatheToolpathJobId):
            raise TypeError("Lathe result job identity is invalid")
        if type(self.request_sequence) is not int or self.request_sequence < 0:
            raise ValueError("Lathe result request sequence is invalid")
        if not isinstance(self.ownership, LatheOwnershipKey):
            raise TypeError("Lathe result ownership is invalid")
        if not isinstance(self.operation_revision, Revision):
            raise TypeError("Lathe result operation revision is invalid")
        if not isinstance(self.fingerprint, LatheToolpathFingerprint):
            raise TypeError("Lathe result fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class LatheToolpathResult:
    identity: LatheToolpathResultIdentity
    strategy_id: LatheStrategyId
    algorithm_version: str
    cache_key: LatheToolpathCacheKey
    state: LatheToolpathResultState
    source: LatheToolpathResultSource
    motions: tuple[LatheToolpathEvent, ...] = ()
    bounds: LatheToolpathBounds | None = None
    pass_count: int = 0
    cutting_length_mm: float = 0.0
    rapid_length_mm: float = 0.0
    diagnostics: tuple[LatheToolpathDiagnostic, ...] = ()
    generation_metadata: LatheMetadata = ()
    thread_pass_metadata: tuple[LatheThreadPassMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LatheToolpathResultIdentity):
            raise TypeError("Lathe toolpath result identity is invalid")
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe toolpath result strategy is invalid")
        if not isinstance(self.algorithm_version, str) or not self.algorithm_version:
            raise ValueError("Lathe result algorithm version is empty")
        if not isinstance(self.cache_key, LatheToolpathCacheKey):
            raise TypeError("Lathe result cache key is invalid")
        if not isinstance(self.state, LatheToolpathResultState):
            raise TypeError("Lathe result state is invalid")
        if not isinstance(self.source, LatheToolpathResultSource):
            raise TypeError("Lathe result source is invalid")
        if not isinstance(self.motions, tuple) or any(
            not isinstance(item, (LathePathSegment, LatheDwellEvent))
            for item in self.motions
        ):
            raise TypeError("Lathe result motions are invalid")
        if tuple(item.sequence_index for item in self.motions) != tuple(
            range(len(self.motions))
        ):
            raise ValueError("Lathe result motion sequence is not contiguous")
        if type(self.pass_count) is not int or self.pass_count < 0:
            raise ValueError("Lathe result pass count is invalid")
        cutting = finite_number(self.cutting_length_mm, "Lathe cutting length")
        rapid = finite_number(self.rapid_length_mm, "Lathe rapid length")
        if cutting < 0.0 or rapid < 0.0:
            raise ValueError("Lathe result lengths must be non-negative")
        object.__setattr__(self, "cutting_length_mm", cutting)
        object.__setattr__(self, "rapid_length_mm", rapid)
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheToolpathDiagnostic)
            for item in self.diagnostics
        ):
            raise TypeError("Lathe result diagnostics are invalid")
        object.__setattr__(
            self,
            "diagnostics",
            tuple(
                sorted(
                    set(self.diagnostics),
                    key=lambda item: (item.code.value, item.field_id or "", item.details),
                )
            ),
        )
        object.__setattr__(
            self, "generation_metadata", normalized_metadata(self.generation_metadata)
        )
        if not isinstance(self.thread_pass_metadata, tuple) or any(
            not isinstance(item, LatheThreadPassMetadata)
            for item in self.thread_pass_metadata
        ):
            raise TypeError("Lathe thread pass metadata is invalid")
        thread_strategy = self.strategy_id in {
            LatheStrategyId.OD_THREAD,
            LatheStrategyId.ID_THREAD,
        }
        if self.state is LatheToolpathResultState.SUCCESS and thread_strategy:
            if len(self.thread_pass_metadata) != self.pass_count:
                raise ValueError("Lathe thread result pass metadata is incomplete")
            if tuple(item.pass_index for item in self.thread_pass_metadata) != tuple(
                range(self.pass_count)
            ):
                raise ValueError("Lathe thread result pass metadata is unordered")
            if any(
                item.strategy_algorithm_version != self.algorithm_version
                for item in self.thread_pass_metadata
            ):
                raise ValueError("Lathe thread result algorithm metadata differs")
        elif self.thread_pass_metadata:
            raise ValueError("Only successful thread results may expose pass metadata")

        if self.state is LatheToolpathResultState.SUCCESS:
            if not self.motions or self.bounds is None or self.pass_count <= 0:
                raise ValueError("Successful Lathe result is incomplete")
            if self.bounds != LatheToolpathBounds.from_events(self.motions):
                raise ValueError("Lathe result bounds do not match motions")
            segments = tuple(
                item for item in self.motions if isinstance(item, LathePathSegment)
            )
            expected_cutting = sum(
                item.length_mm
                for item in segments
                if item.motion_class is not LatheMotionClass.RAPID
            )
            expected_rapid = sum(
                item.length_mm
                for item in segments
                if item.motion_class is LatheMotionClass.RAPID
            )
            if not math.isclose(
                self.cutting_length_mm,
                expected_cutting,
                rel_tol=0.0,
                abs_tol=LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM,
            ) or not math.isclose(
                self.rapid_length_mm,
                expected_rapid,
                rel_tol=0.0,
                abs_tol=LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM,
            ):
                raise ValueError("Lathe result statistics do not match motions")
        elif (
            self.motions
            or self.bounds is not None
            or self.pass_count != 0
            or cutting != 0.0
            or rapid != 0.0
            or not self.diagnostics
        ):
            raise ValueError("Non-success Lathe result must not expose a partial path")

    @property
    def succeeded(self) -> bool:
        return self.state is LatheToolpathResultState.SUCCESS

    def with_source_and_identity(
        self,
        *,
        identity: LatheToolpathResultIdentity,
        source: LatheToolpathResultSource,
    ) -> "LatheToolpathResult":
        """Reuse an immutable semantic result for a new accepted cache-hit job."""

        if not self.succeeded:
            raise ValueError("Only successful Lathe results can be reused")
        return LatheToolpathResult(
            identity,
            self.strategy_id,
            self.algorithm_version,
            self.cache_key,
            self.state,
            source,
            self.motions,
            self.bounds,
            self.pass_count,
            self.cutting_length_mm,
            self.rapid_length_mm,
            self.diagnostics,
            self.generation_metadata,
            self.thread_pass_metadata,
        )

    @property
    def thread_passes(self) -> tuple[LatheThreadPassMetadata, ...]:
        """Return the typed per-pass schedule for thread previews."""

        return self.thread_pass_metadata


__all__ = [
    "JsonScalar",
    "LATHE_AXIAL_DRILL_ALGORITHM_VERSION",
    "LATHE_FACE_ALGORITHM_VERSION",
    "LATHE_ID_FINISH_ALGORITHM_VERSION",
    "LATHE_ID_GROOVE_ALGORITHM_VERSION",
    "LATHE_ID_ROUGH_ALGORITHM_VERSION",
    "LATHE_ID_THREAD_ALGORITHM_VERSION",
    "LATHE_OD_FINISH_ALGORITHM_VERSION",
    "LATHE_OD_GROOVE_ALGORITHM_VERSION",
    "LATHE_OD_ROUGH_ALGORITHM_VERSION",
    "LATHE_OD_THREAD_ALGORITHM_VERSION",
    "LATHE_PART_OFF_ALGORITHM_VERSION",
    "LATHE_TOOLPATH_ALGORITHM_VERSION",
    "LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM",
    "LATHE_THREAD_TOOLPATH_PREVIEW_CAPABILITY",
    "LatheDwellEvent",
    "LatheMetadata",
    "LatheMotionClass",
    "LathePathSegment",
    "LatheToolpathBounds",
    "LatheToolpathCacheKey",
    "LatheToolpathDiagnostic",
    "LatheToolpathDiagnosticCode",
    "LatheToolpathEvent",
    "LatheToolpathFingerprint",
    "LatheToolpathJobId",
    "LatheToolpathResult",
    "LatheToolpathResultIdentity",
    "LatheToolpathResultSource",
    "LatheToolpathResultState",
    "LatheThreadPassMetadata",
    "LatheXZPoint",
    "finite_number",
    "normalized_metadata",
]
