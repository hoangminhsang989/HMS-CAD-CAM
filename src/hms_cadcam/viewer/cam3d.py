"""Native-free CAM 3D preview publication values and typed outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import SetupId

PreviewPoint = tuple[float, float, float]
PreviewTriangle = tuple[int, int, int]
PreviewBounds = tuple[float, float, float, float, float, float]


class Cam3DPreviewPublicationSource(StrEnum):
    """Whether an accepted immutable mesh came from work or memory cache."""

    WORKER = "worker"
    CACHE = "cache"


class Cam3DPreviewPublicationCode(StrEnum):
    """Localization-neutral result of one viewport publication operation."""

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
class Cam3DPreviewOwnership:
    """Pure viewer copy of the WP3 project/document/source/Setup owner."""

    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    setup_id: SetupId

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("CAM 3D preview project identity is invalid")
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("CAM 3D preview document identity is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("CAM 3D preview source identity is invalid")
        if not isinstance(self.setup_id, SetupId):
            raise TypeError("CAM 3D preview Setup identity is invalid")


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class Cam3DPreviewActorIdentity:
    """Semantic identity retained while a native actor is alive."""

    ownership: Cam3DPreviewOwnership
    project_generation: int
    job_id: str
    request_fingerprint: str
    cache_key: str
    source: Cam3DPreviewPublicationSource

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, Cam3DPreviewOwnership):
            raise TypeError("CAM 3D preview actor ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D preview actor generation is invalid")
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("CAM 3D preview actor job identity is invalid")
        object.__setattr__(self, "job_id", self.job_id.strip())
        object.__setattr__(
            self,
            "request_fingerprint",
            _digest(self.request_fingerprint, "CAM 3D request fingerprint"),
        )
        object.__setattr__(
            self,
            "cache_key",
            _digest(self.cache_key, "CAM 3D preview cache key"),
        )
        if not isinstance(self.source, Cam3DPreviewPublicationSource):
            raise TypeError("CAM 3D preview actor source is invalid")


def _finite_float(value: object, name: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite float")
    return value


@dataclass(frozen=True, slots=True)
class Cam3DPreviewMeshData:
    """Immutable triangle data accepted by every viewport backend."""

    vertices: tuple[PreviewPoint, ...]
    triangles: tuple[PreviewTriangle, ...]
    triangle_normals: tuple[PreviewPoint, ...]
    bounds: PreviewBounds

    def __post_init__(self) -> None:
        if not isinstance(self.vertices, tuple) or not self.vertices:
            raise ValueError("CAM 3D preview vertices must be non-empty")
        if not isinstance(self.triangles, tuple) or not self.triangles:
            raise ValueError("CAM 3D preview triangles must be non-empty")
        if not isinstance(self.triangle_normals, tuple) or len(
            self.triangle_normals
        ) != len(self.triangles):
            raise ValueError("CAM 3D preview triangle normals are incomplete")
        for point in self.vertices:
            if not isinstance(point, tuple) or len(point) != 3:
                raise ValueError("CAM 3D preview vertex shape is invalid")
            for value in point:
                _finite_float(value, "CAM 3D preview vertex")
        for triangle in self.triangles:
            if (
                not isinstance(triangle, tuple)
                or len(triangle) != 3
                or len(set(triangle)) != 3
                or any(type(index) is not int for index in triangle)
                or any(index < 0 or index >= len(self.vertices) for index in triangle)
            ):
                raise ValueError("CAM 3D preview triangle index is invalid")
        for normal in self.triangle_normals:
            if not isinstance(normal, tuple) or len(normal) != 3:
                raise ValueError("CAM 3D preview normal shape is invalid")
            values = tuple(
                _finite_float(value, "CAM 3D preview normal") for value in normal
            )
            if not math.isclose(
                math.sqrt(sum(value * value for value in values)),
                1.0,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            ):
                raise ValueError("CAM 3D preview normal must be unit length")
        if not isinstance(self.bounds, tuple) or len(self.bounds) != 6:
            raise ValueError("CAM 3D preview bounds are invalid")
        bounds = tuple(
            _finite_float(value, "CAM 3D preview bound") for value in self.bounds
        )
        expected = (
            min(item[0] for item in self.vertices),
            min(item[1] for item in self.vertices),
            min(item[2] for item in self.vertices),
            max(item[0] for item in self.vertices),
            max(item[1] for item in self.vertices),
            max(item[2] for item in self.vertices),
        )
        if bounds != expected:
            raise ValueError("CAM 3D preview bounds do not match vertices")

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)


@dataclass(frozen=True, slots=True)
class Cam3DPreviewPublication:
    """Immutable mesh and semantic identity accepted by a viewport backend."""

    identity: Cam3DPreviewActorIdentity
    mesh: Cam3DPreviewMeshData

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Cam3DPreviewActorIdentity):
            raise TypeError("CAM 3D preview publication identity is invalid")
        if type(self.mesh) is not Cam3DPreviewMeshData:
            raise TypeError("CAM 3D preview publication mesh is invalid")


@dataclass(frozen=True, slots=True)
class Cam3DPreviewPublicationResult:
    """Typed success/failure without a raw exception or native handle."""

    code: Cam3DPreviewPublicationCode
    identity: Cam3DPreviewActorIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, Cam3DPreviewPublicationCode):
            raise TypeError("CAM 3D preview publication code is invalid")
        if self.identity is not None and not isinstance(
            self.identity, Cam3DPreviewActorIdentity
        ):
            raise TypeError("CAM 3D preview publication identity is invalid")

    @property
    def succeeded(self) -> bool:
        """Return whether the requested state is now true."""

        return self.code in {
            Cam3DPreviewPublicationCode.PUBLISHED,
            Cam3DPreviewPublicationCode.REPLACED,
            Cam3DPreviewPublicationCode.CLEARED,
            Cam3DPreviewPublicationCode.ALREADY_CLEAR,
        }

    @property
    def ok(self) -> bool:
        """Short alias used by UI/application adapters."""

        return self.succeeded

    def __bool__(self) -> bool:
        return self.succeeded


__all__ = [
    "Cam3DPreviewActorIdentity",
    "Cam3DPreviewMeshData",
    "Cam3DPreviewOwnership",
    "Cam3DPreviewPublication",
    "Cam3DPreviewPublicationCode",
    "Cam3DPreviewPublicationResult",
    "Cam3DPreviewPublicationSource",
]