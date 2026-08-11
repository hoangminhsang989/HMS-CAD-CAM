"""Additive project persistence for Stage18A qualification records."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.post.export_model import NCArtifactManifestEntry, NCArtifactStatus
from hms_cadcam.cam.qualification.codec import artifact_from_dict, dumps
from hms_cadcam.cam.qualification.model import (
    QUALIFICATION_REPORT_VERSION,
    QualificationReport,
    QualifiedNCArtifact,
    canonical_json_bytes,
    sha256_bytes,
)


_logger = logging.getLogger(__name__)
_MANIFEST_FORMAT = "HMS_STAGE18A_QUALIFICATION_MANIFEST"


class QualificationStoreError(RuntimeError):
    """Raised when additive qualification persistence fails closed."""


def _qualification_root(project_root: Path) -> Path:
    if not isinstance(project_root, Path):
        raise TypeError("project_root must be pathlib.Path")
    root = project_root.resolve(strict=True)
    if not root.is_dir() or not root.name.casefold().endswith(".hms"):
        raise QualificationStoreError("Qualification project root is invalid")
    return root / "post" / "qualification"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.stage18a.tmp")
    if temporary.exists():
        raise QualificationStoreError("Stale qualification temporary file exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != payload:
            raise QualificationStoreError("Qualification read-back mismatch")
        os.replace(temporary, path)
    except OSError as error:
        raise QualificationStoreError("Qualification persistence failed") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            _logger.warning("Could not remove qualification temporary file", exc_info=True)


class QualificationArtifactStore:
    """Persist qualification records below ``post/`` without SQLite migration."""

    def save(
        self,
        project_root: Path,
        managed: NCArtifactManifestEntry,
        report: QualificationReport,
    ) -> QualifiedNCArtifact:
        if not isinstance(managed, NCArtifactManifestEntry):
            raise TypeError("managed must be NCArtifactManifestEntry")
        if not isinstance(report, QualificationReport):
            raise TypeError("report must be QualificationReport")
        if managed.status is not NCArtifactStatus.CURRENT:
            raise QualificationStoreError("Only a current managed NC artifact can be qualified")
        if managed.sha256 != report.nc_sha256:
            raise QualificationStoreError("Managed NC and qualification checksums differ")
        root = _qualification_root(project_root)
        project = root.parents[1]
        nc_path = (project / managed.output_relative_path).resolve(strict=True)
        if project not in nc_path.parents or not nc_path.is_file():
            raise QualificationStoreError("Managed NC path escaped the project")
        nc_payload = nc_path.read_bytes()
        if len(nc_payload) != managed.byte_length or sha256_bytes(nc_payload) != managed.sha256:
            raise QualificationStoreError("Managed NC content is stale or tampered")
        identifier = f"qualification-{report.report_fingerprint.digest}"
        artifact = QualifiedNCArtifact(
            identifier,
            str(managed.artifact_id),
            managed.artifact_fingerprint,
            managed.output_relative_path,
            report,
        )
        record_name = f"{report.report_fingerprint.digest}.json"
        record_path = root / record_name
        record_bytes = dumps(artifact)
        _atomic_write(record_path, record_bytes)
        entries = {item.artifact_id: item for item in self.load(project_root)}
        entries[artifact.artifact_id] = artifact
        manifest_payload = {
            "format": _MANIFEST_FORMAT,
            "format_version": QUALIFICATION_REPORT_VERSION,
            "entries": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_fingerprint": item.artifact_fingerprint.to_dict(),
                    "record": f"{item.report.report_fingerprint.digest}.json",
                    "record_sha256": sha256_bytes(dumps(item)),
                    "managed_nc_artifact_id": item.managed_nc_artifact_id,
                    "managed_nc_artifact_fingerprint": item.managed_nc_artifact_fingerprint.to_dict(),
                    "managed_nc_relative_path": item.managed_nc_relative_path,
                    "nc_sha256": item.report.nc_sha256,
                    "qualification_level": item.report.qualification_level.value,
                    "machine_ready": item.report.machine_ready,
                }
                for item in sorted(entries.values(), key=lambda value: value.artifact_id)
            ],
        }
        _atomic_write(root / "manifest.json", canonical_json_bytes(manifest_payload))
        return artifact

    def load(self, project_root: Path) -> tuple[QualifiedNCArtifact, ...]:
        root = _qualification_root(project_root)
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return ()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise QualificationStoreError("Qualification manifest is unreadable") from error
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"format", "format_version", "entries"}
            or manifest["format"] != _MANIFEST_FORMAT
            or manifest["format_version"] != QUALIFICATION_REPORT_VERSION
            or not isinstance(manifest["entries"], list)
        ):
            raise QualificationStoreError("Qualification manifest is malformed")
        expected_fields = {
            "artifact_id", "artifact_fingerprint", "record", "record_sha256",
            "managed_nc_artifact_id", "managed_nc_artifact_fingerprint",
            "managed_nc_relative_path", "nc_sha256", "qualification_level",
            "machine_ready",
        }
        project = root.parents[1]
        artifacts: list[QualifiedNCArtifact] = []
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != expected_fields:
                raise QualificationStoreError("Qualification manifest entry is malformed")
            record_name = entry["record"]
            if (
                not isinstance(record_name, str)
                or "/" in record_name
                or "\\" in record_name
                or not record_name.endswith(".json")
            ):
                raise QualificationStoreError("Qualification record path is invalid")
            record_path = (root / record_name).resolve(strict=True)
            if root not in record_path.parents:
                raise QualificationStoreError("Qualification record escaped its root")
            payload = record_path.read_bytes()
            if sha256_bytes(payload) != entry["record_sha256"]:
                raise QualificationStoreError("Qualification record checksum mismatch")
            try:
                decoded = json.loads(payload.decode("utf-8"))
                artifact = artifact_from_dict(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, CamValidationError) as error:
                raise QualificationStoreError("Qualification record is invalid") from error
            if (
                artifact.artifact_id != entry["artifact_id"]
                or artifact.artifact_fingerprint.to_dict() != entry["artifact_fingerprint"]
                or artifact.managed_nc_artifact_id != entry["managed_nc_artifact_id"]
                or artifact.managed_nc_artifact_fingerprint.to_dict()
                != entry["managed_nc_artifact_fingerprint"]
                or artifact.managed_nc_relative_path != entry["managed_nc_relative_path"]
                or artifact.report.nc_sha256 != entry["nc_sha256"]
                or artifact.report.qualification_level.value != entry["qualification_level"]
                or artifact.report.machine_ready is not entry["machine_ready"]
            ):
                raise QualificationStoreError("Qualification manifest identity mismatch")
            nc_path = (project / artifact.managed_nc_relative_path).resolve(strict=True)
            if project not in nc_path.parents or sha256_bytes(nc_path.read_bytes()) != artifact.report.nc_sha256:
                raise QualificationStoreError("Qualified managed NC is stale or tampered")
            artifacts.append(artifact)
        if len({item.artifact_id for item in artifacts}) != len(artifacts):
            raise CamInvariantError("Qualification manifest contains duplicate artifacts")
        return tuple(sorted(artifacts, key=lambda item: item.artifact_id))

    @staticmethod
    def is_current(
        artifact: QualifiedNCArtifact,
        *,
        contract_fingerprint: object,
        program_fingerprint: object,
        managed_artifact_fingerprint: object,
    ) -> bool:
        """Return false on any profile, program, or managed artifact drift."""

        return (
            isinstance(artifact, QualifiedNCArtifact)
            and artifact.report.machine_contract_fingerprint == contract_fingerprint
            and artifact.report.program_fingerprint == program_fingerprint
            and artifact.managed_nc_artifact_fingerprint == managed_artifact_fingerprint
        )


__all__ = ["QualificationArtifactStore", "QualificationStoreError"]
