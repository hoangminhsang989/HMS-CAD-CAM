"""Fail-closed CAD export orchestration with same-directory atomic publication."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from hms_cadcam.cad.export_models import (
    EXPORT_CAPABILITIES,
    ExportCapability,
    ExportCapabilityClass,
    ExportEntityKind,
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
    ExportSelectionRef,
    capability_for_path,
)
from hms_cadcam.cad.kernel import CadKernel
from hms_cadcam.cad.models import CadDocumentId


LOGGER = logging.getLogger(__name__)


class ExportErrorCode(StrEnum):
    UNSUPPORTED_EXTENSION = "export3d.unsupported_extension"
    BACKEND_UNAVAILABLE = "export3d.backend_unavailable"
    PROFILE_EXTENSION_MISMATCH = "export3d.profile_extension_mismatch"
    INVALID_PROFILE = "export3d.invalid_profile"
    INVALID_DOCUMENT = "export3d.invalid_document"
    INVALID_SELECTION = "export3d.invalid_selection"
    SELECTION_EXPORT_UNAVAILABLE = "export3d.selection_unavailable"
    PARENT_MISSING = "export3d.parent_missing"
    DESTINATION_INVALID = "export3d.destination_invalid"
    FILE_EXISTS = "export3d.file_exists"
    WRITE_FAILED = "export3d.write_failed"
    EMPTY_OUTPUT = "export3d.empty_output"
    ATOMIC_REPLACE_FAILED = "export3d.atomic_replace_failed"
    ATOMIC_PUBLICATION_FAILED = "export3d.atomic_publication_failed"
    TEMP_CLEANUP_FAILED = "export3d.temp_cleanup_failed"


class CadExportDocumentError(ValueError):
    """The request no longer identifies a kernel-owned document."""


class CadExportSelectionError(ValueError):
    """The request selection is stale, malformed, or not resolvable."""


class CadExportProfileError(ValueError):
    """The typed profile is incompatible with the resolved source geometry."""


@dataclass(frozen=True, slots=True)
class ExportFailure:
    code: ExportErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class ExportRequest:
    document_id: CadDocumentId
    target_path: Path
    profile: ExportProfile
    selections: tuple[ExportSelectionRef, ...] = ()
    overwrite_policy: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS

    def __post_init__(self) -> None:
        if not isinstance(self.target_path, Path):
            raise TypeError("CAD export target must be pathlib.Path")
        if not isinstance(self.profile, ExportProfile):
            raise TypeError("CAD export profile must be ExportProfile")
        if not isinstance(self.overwrite_policy, ExportOverwritePolicy):
            raise TypeError("CAD export overwrite policy is invalid")
        if self.overwrite_policy is not self.profile.overwrite_policy:
            raise ValueError(
                "CAD export request policy must match the serialized profile policy"
            )
        if any(item.document_id != self.document_id for item in self.selections):
            raise ValueError("Every export selection must belong to the document")
        identities = tuple(item.selection_id for item in self.selections)
        if len(identities) != len(set(identities)):
            raise ValueError("CAD export selections must be unique")


@dataclass(frozen=True, slots=True)
class BackendWriteMetadata:
    backend: str
    entity_count: int

    def __post_init__(self) -> None:
        if not self.backend.strip() or self.entity_count <= 0:
            raise ValueError("CAD export backend metadata is invalid")


@dataclass(frozen=True, slots=True)
class ExportResult:
    success: bool
    target_path: Path
    format_id: ExportFormatId
    elapsed_seconds: float
    bytes_written: int = 0
    sha256: str | None = None
    backend: str | None = None
    entity_count: int = 0
    replaced_existing: bool = False
    failure: ExportFailure | None = None

    def __post_init__(self) -> None:
        if self.elapsed_seconds < 0.0:
            raise ValueError("CAD export elapsed time cannot be negative")
        if self.success:
            if (
                self.bytes_written <= 0
                or self.sha256 is None
                or self.backend is None
                or self.entity_count <= 0
                or self.failure is not None
            ):
                raise ValueError("Successful CAD export result is inconsistent")
        elif self.failure is None or self.bytes_written != 0 or self.sha256 is not None:
            raise ValueError("Failed CAD export result is inconsistent")


class CadExportBackend(Protocol):
    """Native boundary consumed by the filesystem publication service."""

    @property
    def supported_formats(self) -> frozenset[ExportFormatId]: ...

    @property
    def unavailable_reason(self) -> str | None: ...

    def write(self, request: ExportRequest, temporary_path: Path) -> BackendWriteMetadata:
        """Write only to the supplied unpublished path or raise."""


class UnavailableCadExportBackend:
    def __init__(self, reason: str) -> None:
        self._reason = reason.strip() or "CAD export backend is unavailable."

    @property
    def supported_formats(self) -> frozenset[ExportFormatId]:
        return frozenset()

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    def write(self, request: ExportRequest, temporary_path: Path) -> BackendWriteMetadata:
        raise RuntimeError(self._reason)


class CadExportService:
    """Validate requests, isolate native writes, and publish completed files atomically."""

    def __init__(self, backend: CadExportBackend) -> None:
        self._backend = backend

    @classmethod
    def create_for_kernel(cls, kernel: CadKernel) -> "CadExportService":
        """Load OCP lazily so the application's unavailable-kernel fallback still starts."""
        if not kernel.is_available():
            status = kernel.get_status()
            return cls(
                UnavailableCadExportBackend(
                    status.error or "OCP CAD kernel is unavailable."
                )
            )
        try:
            from hms_cadcam.cad.ocp.exporter import OcpCadExportBackend

            return cls(OcpCadExportBackend(kernel))
        except (ImportError, OSError, TypeError) as error:
            LOGGER.warning("OCP export backend is unavailable: %s", error)
            return cls(UnavailableCadExportBackend(str(error)))

    def capabilities(self) -> tuple[ExportCapability, ...]:
        """Return registry entries adjusted only for actual runtime availability."""
        supported = self._backend.supported_formats
        reason = self._backend.unavailable_reason or "Native writer is unavailable."
        return tuple(
            capability
            if not capability.available or capability.format_id in supported
            else replace(
                capability,
                classification=(
                    ExportCapabilityClass.ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE
                ),
                available=False,
                backend=None,
                entity_kinds=frozenset(),
                unavailable_reason=reason,
            )
            for capability in EXPORT_CAPABILITIES.values()
        )

    def capability(self, format_id: ExportFormatId) -> ExportCapability:
        return next(item for item in self.capabilities() if item.format_id is format_id)

    def export(self, request: ExportRequest) -> ExportResult:
        """Execute one validated request without ever publishing a partial final file."""
        started = perf_counter()
        target = request.target_path.resolve(strict=False)
        capability = capability_for_path(target)
        if capability is None:
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.UNSUPPORTED_EXTENSION,
                f"Unsupported CAD export extension: {target.suffix or '<none>'}",
            )
        if capability.format_id is not request.profile.format_id:
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.PROFILE_EXTENSION_MISMATCH,
                "Export profile format does not match the destination extension.",
            )
        runtime_capability = self.capability(capability.format_id)
        if not runtime_capability.available:
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.BACKEND_UNAVAILABLE,
                runtime_capability.unavailable_reason or "Export backend unavailable.",
            )
        if request.selections:
            unsupported = tuple(
                item.entity_kind
                for item in request.selections
                if item.entity_kind not in runtime_capability.entity_kinds
            )
            if unsupported:
                return self._failure(
                    request,
                    target,
                    started,
                    ExportErrorCode.SELECTION_EXPORT_UNAVAILABLE,
                    "The selected geometry kind is not supported by this writer.",
                )
        parent = target.parent
        if not parent.is_dir():
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.PARENT_MISSING,
                "Export destination parent does not exist or is not a directory.",
            )
        if target.exists() and target.is_dir():
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.DESTINATION_INVALID,
                "Export destination is a directory.",
            )
        existed = target.exists()
        if existed and request.overwrite_policy is ExportOverwritePolicy.FAIL_IF_EXISTS:
            return self._failure(
                request,
                target,
                started,
                ExportErrorCode.FILE_EXISTS,
                "Export destination already exists.",
            )
        temporary = parent / (
            f".{target.stem}.{uuid4().hex}{target.suffix}.hms-exporting"
        )
        try:
            metadata = self._backend.write(request, temporary)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                return self._failure_with_cleanup(
                    request,
                    target,
                    temporary,
                    started,
                    ExportErrorCode.EMPTY_OUTPUT,
                    "Native writer produced no export data.",
                )
            size = temporary.stat().st_size
            digest = _sha256(temporary)
        except CadExportSelectionError as error:
            return self._failure_with_cleanup(
                request,
                target,
                temporary,
                started,
                ExportErrorCode.INVALID_SELECTION,
                str(error),
            )
        except CadExportDocumentError as error:
            return self._failure_with_cleanup(
                request,
                target,
                temporary,
                started,
                ExportErrorCode.INVALID_DOCUMENT,
                str(error),
            )
        except CadExportProfileError as error:
            return self._failure_with_cleanup(
                request,
                target,
                temporary,
                started,
                ExportErrorCode.INVALID_PROFILE,
                str(error),
            )
        except Exception as error:
            LOGGER.exception("Native CAD export writer failed for %s", target)
            return self._failure_with_cleanup(
                request,
                target,
                temporary,
                started,
                ExportErrorCode.WRITE_FAILED,
                f"Native writer failed: {error}",
            )

        if request.overwrite_policy is ExportOverwritePolicy.FAIL_IF_EXISTS:
            if os.name != "nt":
                return self._failure_with_cleanup(
                    request,
                    target,
                    temporary,
                    started,
                    ExportErrorCode.ATOMIC_PUBLICATION_FAILED,
                    "Atomic no-overwrite publication is unsupported on this platform.",
                )
            try:
                os.rename(temporary, target)
            except FileExistsError:
                return self._failure_with_cleanup(
                    request,
                    target,
                    temporary,
                    started,
                    ExportErrorCode.FILE_EXISTS,
                    "Export destination appeared before no-overwrite publication.",
                )
            except OSError as error:
                LOGGER.error("No-overwrite CAD export publication failed: %s", error)
                return self._failure_with_cleanup(
                    request,
                    target,
                    temporary,
                    started,
                    ExportErrorCode.ATOMIC_PUBLICATION_FAILED,
                    f"No safe create-if-absent publication is available: {error}",
                )
        else:
            try:
                os.replace(temporary, target)
            except OSError as error:
                LOGGER.error("Atomic CAD export replacement failed: %s", error)
                return self._failure_with_cleanup(
                    request,
                    target,
                    temporary,
                    started,
                    ExportErrorCode.ATOMIC_REPLACE_FAILED,
                    f"Could not replace the completed export: {error}",
                )

        return ExportResult(
            True,
            target,
            request.profile.format_id,
            perf_counter() - started,
            bytes_written=size,
            sha256=digest,
            backend=metadata.backend,
            entity_count=metadata.entity_count,
            replaced_existing=existed,
        )

    @classmethod
    def _failure_with_cleanup(
        cls,
        request: ExportRequest,
        target: Path,
        temporary: Path,
        started: float,
        code: ExportErrorCode,
        message: str,
    ) -> ExportResult:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            LOGGER.error(
                "CAD export temporary cleanup failed for %s: %s",
                temporary,
                cleanup_error,
            )
            return cls._failure(
                request,
                target,
                started,
                ExportErrorCode.TEMP_CLEANUP_FAILED,
                f"{code.value}: {message} Temporary cleanup failed: {cleanup_error}",
            )
        return cls._failure(request, target, started, code, message)

    @staticmethod
    def _failure(
        request: ExportRequest,
        target: Path,
        started: float,
        code: ExportErrorCode,
        message: str,
    ) -> ExportResult:
        return ExportResult(
            False,
            target,
            request.profile.format_id,
            perf_counter() - started,
            failure=ExportFailure(code, message),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
