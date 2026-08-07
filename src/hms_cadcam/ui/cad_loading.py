"""Request-owned CAD loading lifecycle state without native geometry ownership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from hms_cadcam.cad.models import CadFormat


class CadLoadOrigin(Enum):
    """Identify the UI request path without persisting it."""

    OPEN_DIALOG = "open_dialog"
    DRAG_DROP = "drag_drop"


class CadLoadState(Enum):
    """Public lifecycle states for one CAD loading request."""

    LOADING = "loading"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CadLoadErrorCode(Enum):
    """Recoverable error categories presented by the loading boundary."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    UNREADABLE_INPUT = "unreadable_input"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    CANCELLED = "cancelled"
    IMPORTER_FAILURE = "importer_failure"


@dataclass(frozen=True, slots=True)
class CadLoadRequest:
    """An immutable runtime-only identity for a supported CAD load request."""

    request_id: int
    source_path: Path
    origin: CadLoadOrigin
    cad_format: CadFormat
    owner_identity: str


@dataclass(frozen=True, slots=True)
class CadLoadError:
    """A recoverable error retaining a diagnostic cause for logs and support."""

    code: CadLoadErrorCode
    message: str
    cause: str | None = None


@dataclass(frozen=True, slots=True)
class CadLoadEvent:
    """A public state projection that never carries native geometry."""

    request: CadLoadRequest | None
    state: CadLoadState
    error: CadLoadError | None = None


def cad_format_for_path(path: Path) -> CadFormat | None:
    """Return the existing supported reader route for one file suffix."""

    suffix = path.suffix.casefold()
    if suffix in {".step", ".stp"}:
        return CadFormat.STEP
    if suffix in {".brep", ".brp"}:
        return CadFormat.BREP
    if suffix in {".iges", ".igs"}:
        return CadFormat.IGES
    if suffix == ".stl":
        return CadFormat.STL
    return None


def normalize_import_error(error: object) -> CadLoadError:
    """Map worker exceptions to deterministic recoverable loading errors."""

    cause = str(error) or type(error).__name__
    if isinstance(error, (InterruptedError, KeyboardInterrupt)):
        return CadLoadError(CadLoadErrorCode.CANCELLED, "CAD loading was cancelled.", cause)
    if isinstance(error, (FileNotFoundError, PermissionError, IsADirectoryError, OSError)):
        return CadLoadError(
            CadLoadErrorCode.UNREADABLE_INPUT,
            "Selected CAD input is unreadable.",
            cause,
        )
    return CadLoadError(
        CadLoadErrorCode.IMPORTER_FAILURE,
        "CAD importer could not complete the request.",
        cause,
    )

class CadLoadingCoordinator:
    """Own one public request lifecycle and suppress stale terminal callbacks."""

    def __init__(self, publish: Callable[[CadLoadEvent], None]) -> None:
        self._publish = publish
        self._next_request_id = 0
        self._active: CadLoadRequest | None = None

    @property
    def active_request(self) -> CadLoadRequest | None:
        """Return the sole request allowed to publish public state."""

        return self._active

    def begin(
        self,
        source_path: Path,
        origin: CadLoadOrigin,
        cad_format: CadFormat,
        *,
        owner_identity: str,
    ) -> tuple[CadLoadRequest, CadLoadRequest | None]:
        """Synchronously publish loading and return the request it superseded."""

        superseded = self._active
        self._next_request_id += 1
        request = CadLoadRequest(
            self._next_request_id,
            source_path,
            origin,
            cad_format,
            owner_identity,
        )
        self._active = request
        self._publish(CadLoadEvent(request, CadLoadState.LOADING))
        return request, superseded

    def reject_unsupported(self, source_path: Path) -> CadLoadError:
        """Publish an immediate failure before any worker can be launched."""

        error = CadLoadError(
            CadLoadErrorCode.UNSUPPORTED_FORMAT,
            "Định dạng CAD chưa được hỗ trợ.",
            str(source_path),
        )
        self._publish(CadLoadEvent(None, CadLoadState.FAILED, error))
        return error

    def reject_backend_unavailable(
        self,
        source_path: Path,
        origin: CadLoadOrigin,
        cad_format: CadFormat,
        *,
        owner_identity: str,
    ) -> CadLoadRequest:
        """Publish a typed failure without creating a worker task."""

        request, _superseded = self.begin(
            source_path, origin, cad_format, owner_identity=owner_identity
        )
        self.fail(
            request,
            CadLoadError(
                CadLoadErrorCode.BACKEND_UNAVAILABLE,
                "Backend CAD hiện không khả dụng.",
                "CadKernel.is_available returned false",
            ),
        )
        return request

    def is_active(self, request_id: int) -> bool:
        """Return whether a callback still owns public lifecycle state."""

        return self._active is not None and self._active.request_id == request_id

    def succeed(self, request_id: int) -> bool:
        """Publish one success only when the request remains active."""

        return self._terminal(request_id, CadLoadState.SUCCEEDED)

    def fail(self, request: CadLoadRequest, error: CadLoadError) -> bool:
        """Publish one typed failure only when the request remains active."""

        return self._terminal(request.request_id, CadLoadState.FAILED, error)

    def cancel_active(self) -> CadLoadRequest | None:
        """Terminally cancel the public owner once without waiting for native work."""

        request = self._active
        if request is None:
            return None
        self._terminal(
            request.request_id,
            CadLoadState.CANCELLED,
            CadLoadError(CadLoadErrorCode.CANCELLED, "Đã hủy tải CAD."),
        )
        return request

    def abandon_active(self) -> CadLoadRequest | None:
        """Drop public ownership during shutdown; native work may finish later."""

        request = self._active
        if request is not None:
            self._active = None
        return request

    def _terminal(
        self,
        request_id: int,
        state: CadLoadState,
        error: CadLoadError | None = None,
    ) -> bool:
        request = self._active
        if request is None or request.request_id != request_id:
            return False
        self._active = None
        self._publish(CadLoadEvent(request, state, error))
        return True
