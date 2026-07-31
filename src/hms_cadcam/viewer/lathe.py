"""Native-free Lathe XZ path publication values and typed outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import OperationId, SetupId
from hms_cadcam.cam.lathe.toolpath.model import LatheMotionClass

DisplayPoint = tuple[float, float, float]


class LathePreviewPublicationSource(StrEnum):
    WORKER = "worker"
    CACHE = "cache"


class LathePreviewPublicationCode(StrEnum):
    PUBLISHED = "published"
    REPLACED = "replaced"
    CLEARED = "cleared"
    ALREADY_CLEAR = "already_clear"
    INVALID_PAYLOAD = "invalid_payload"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    WRONG_THREAD = "wrong_thread"
    NOT_INITIALIZED = "not_initialized"
    CLOSED = "closed"
    UNAVAILABLE = "unavailable"
    BACKEND_FAILURE = "backend_failure"
    ROLLBACK_FAILURE = "rollback_failure"


@dataclass(frozen=True, slots=True)
class LathePreviewOwnership:
    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    generation: int
    setup_id: SetupId
    operation_id: OperationId

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("Lathe preview project identity is invalid")
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("Lathe preview document identity is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("Lathe preview source identity is invalid")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("Lathe preview generation is invalid")
        if not isinstance(self.setup_id, SetupId):
            raise TypeError("Lathe preview Setup identity is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise TypeError("Lathe preview operation identity is invalid")


def _digest(value: object, subject: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{subject} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class LathePreviewActorIdentity:
    ownership: LathePreviewOwnership
    job_id: str
    request_sequence: int
    request_fingerprint: str
    cache_key: str
    source: LathePreviewPublicationSource

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, LathePreviewOwnership):
            raise TypeError("Lathe preview actor ownership is invalid")
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("Lathe preview actor job identity is invalid")
        object.__setattr__(self, "job_id", self.job_id.strip())
        if type(self.request_sequence) is not int or self.request_sequence < 0:
            raise ValueError("Lathe preview actor request sequence is invalid")
        object.__setattr__(
            self,
            "request_fingerprint",
            _digest(self.request_fingerprint, "Lathe request fingerprint"),
        )
        object.__setattr__(
            self, "cache_key", _digest(self.cache_key, "Lathe cache key")
        )
        if not isinstance(self.source, LathePreviewPublicationSource):
            raise TypeError("Lathe preview publication source is invalid")


def _point(value: object, subject: str) -> DisplayPoint:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{subject} must be an XYZ tuple")
    if any(type(item) is not float or not math.isfinite(item) for item in value):
        raise ValueError(f"{subject} must contain finite floats")
    return value


@dataclass(frozen=True, slots=True)
class LathePreviewSegmentData:
    sequence_index: int
    motion_class: LatheMotionClass
    start: DisplayPoint
    end: DisplayPoint
    semantic_source: str

    def __post_init__(self) -> None:
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise ValueError("Lathe preview segment sequence is invalid")
        if not isinstance(self.motion_class, LatheMotionClass):
            raise TypeError("Lathe preview motion class is invalid")
        start = _point(self.start, "Lathe preview start")
        end = _point(self.end, "Lathe preview end")
        if start == end:
            raise ValueError("Lathe preview segment must not be zero-length")
        if not isinstance(self.semantic_source, str) or not self.semantic_source.strip():
            raise ValueError("Lathe preview semantic source is empty")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "semantic_source", self.semantic_source.strip())


@dataclass(frozen=True, slots=True)
class LathePreviewPublication:
    identity: LathePreviewActorIdentity
    segments: tuple[LathePreviewSegmentData, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LathePreviewActorIdentity):
            raise TypeError("Lathe preview publication identity is invalid")
        if not isinstance(self.segments, tuple) or not self.segments or any(
            not isinstance(item, LathePreviewSegmentData) for item in self.segments
        ):
            raise ValueError("Lathe preview publication segments are invalid")
        indices = tuple(item.sequence_index for item in self.segments)
        if len(set(indices)) != len(indices) or indices != tuple(sorted(indices)):
            raise ValueError("Lathe preview segment sequence is not deterministic")


@dataclass(frozen=True, slots=True)
class LathePreviewPublicationResult:
    code: LathePreviewPublicationCode
    identity: LathePreviewActorIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, LathePreviewPublicationCode):
            raise TypeError("Lathe preview publication code is invalid")
        if self.identity is not None and not isinstance(
            self.identity, LathePreviewActorIdentity
        ):
            raise TypeError("Lathe preview publication identity is invalid")

    @property
    def succeeded(self) -> bool:
        return self.code in {
            LathePreviewPublicationCode.PUBLISHED,
            LathePreviewPublicationCode.REPLACED,
            LathePreviewPublicationCode.CLEARED,
            LathePreviewPublicationCode.ALREADY_CLEAR,
        }

    @property
    def ok(self) -> bool:
        return self.succeeded

    def __bool__(self) -> bool:
        return self.succeeded


__all__ = [
    "DisplayPoint",
    "LathePreviewActorIdentity",
    "LathePreviewOwnership",
    "LathePreviewPublication",
    "LathePreviewPublicationCode",
    "LathePreviewPublicationResult",
    "LathePreviewPublicationSource",
    "LathePreviewSegmentData",
]
