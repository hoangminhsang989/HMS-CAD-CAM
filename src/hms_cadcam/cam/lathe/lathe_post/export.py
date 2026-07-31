"""Explicit, acknowledged, atomic `.NC` export for the basic Lathe Post."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import tempfile

from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicPostDiagnostic, BasicPostDiagnosticCode, BasicPostReadiness
from hms_cadcam.cam.lathe.lathe_post.renderer import BasicNcOutputSnapshot

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BasicNcExportResult:
    success: bool
    destination: Path | None = None
    sha256: str | None = None
    readiness: BasicPostReadiness = BasicPostReadiness.INVALID
    diagnostics: tuple[BasicPostDiagnostic, ...] = ()


def _diag(code: BasicPostDiagnosticCode, subject: str | None = None) -> BasicPostDiagnostic:
    return BasicPostDiagnostic(code.value, f"lathe.basic_post.diagnostic.{code.name.casefold()}", subject)


class BasicNcExportService:
    """Perform only explicit user-selected, acknowledged exports."""

    def export(
        self,
        snapshot: BasicNcOutputSnapshot | None,
        destination: str | Path,
        *,
        acknowledged_unverified: bool,
        overwrite_confirmed: bool = False,
    ) -> BasicNcExportResult:
        if snapshot is None or snapshot.readiness is not BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED:
            return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "snapshot"),))
        if type(acknowledged_unverified) is not bool or not acknowledged_unverified:
            return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.EXPORT_ACK_REQUIRED),))
        try:
            path = Path(destination)
        except (TypeError, ValueError):
            return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.EXPORT_FAILED, "destination"),))
        if path.suffix.upper() != ".NC":
            return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.EXPORT_FAILED, "extension"),))
        try:
            path = path.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            old_bytes = path.read_bytes() if existed else None
            if existed and not overwrite_confirmed:
                return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.OVERWRITE_CONFIRMATION_REQUIRED, str(path)),))
            payload = snapshot.text.encode("ascii")
            expected_sha = hashlib.sha256(payload).hexdigest()
            if expected_sha != snapshot.sha256:
                return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "sha"),))
            fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_sha:
                    raise OSError("temporary NC SHA verification failed")
                os.replace(temporary, path)
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
                    raise OSError("post-write NC SHA verification failed")
            except (OSError, ValueError):
                try:
                    if temporary.exists():
                        temporary.unlink()
                    if existed and old_bytes is not None:
                        restore_fd, restore_name = tempfile.mkstemp(prefix=f".{path.name}.restore.", suffix=".tmp", dir=str(path.parent))
                        restore = Path(restore_name)
                        with os.fdopen(restore_fd, "wb") as handle:
                            handle.write(old_bytes)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(restore, path)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    LOGGER.exception("failed to restore NC destination after export error: %s", path)
                raise
            return BasicNcExportResult(True, path, expected_sha, BasicPostReadiness.BASIC_NC_EXPORT_READY_UNVERIFIED)
        except (OSError, UnicodeError, ValueError) as error:
            LOGGER.warning("basic NC export failed: %s", error)
            return BasicNcExportResult(False, diagnostics=(_diag(BasicPostDiagnosticCode.EXPORT_FAILED, str(path)),))


__all__ = ["BasicNcExportResult", "BasicNcExportService"]
