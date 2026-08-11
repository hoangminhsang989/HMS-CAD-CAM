"""Additive schema-5-compatible persistence for Tranche3 release records."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.model import canonical_json_bytes, sha256_bytes
from hms_cadcam.cam.qualification.offline_model import (
    NCReleaseCandidate,
    OfflineNCVerificationSession,
    OperatorAcknowledgement,
    OperatorReview,
    PackageStatus,
    ReleaseAssessment,
)


LOGGER = logging.getLogger(__name__)
RECORD_FORMAT = "HMS_STAGE18A_TRANCHE3_RELEASE_RECORD"
MANIFEST_FORMAT = "HMS_STAGE18A_TRANCHE3_RELEASE_MANIFEST"


class OfflineReleaseStoreError(RuntimeError):
    """Raised when persisted Tranche3 identity cannot be trusted."""


@dataclass(frozen=True, slots=True)
class OfflineReleaseRecord:
    record_id: str
    session: OfflineNCVerificationSession
    candidate: NCReleaseCandidate
    review: OperatorReview
    acknowledgement: OperatorAcknowledgement
    assessment: ReleaseAssessment
    package_id: ContentFingerprint
    package_status: PackageStatus
    stale_reasons: tuple[str, ...]
    record_fingerprint: ContentFingerprint | None = None
    format_version: int = 1

    def __post_init__(self) -> None:
        if self.format_version != 1 or not isinstance(self.record_id, str) or not self.record_id.strip():
            raise CamValidationError("Offline release record identity is invalid")
        if not isinstance(self.session, OfflineNCVerificationSession):
            raise CamValidationError("Offline release session is invalid")
        if not isinstance(self.candidate, NCReleaseCandidate):
            raise CamValidationError("Offline release candidate is invalid")
        if self.candidate.verification_session_fingerprint != self.session.session_fingerprint:
            raise CamInvariantError("Release candidate does not bind the verification session")
        if not isinstance(self.review, OperatorReview) or not isinstance(
            self.acknowledgement, OperatorAcknowledgement
        ):
            raise CamValidationError("Offline operator records are invalid")
        if self.review.release_candidate_fingerprint != self.candidate.candidate_fingerprint:
            raise CamInvariantError("Operator review is detached from release candidate")
        if self.acknowledgement.release_candidate_fingerprint != self.candidate.candidate_fingerprint:
            raise CamInvariantError("Operator acknowledgement is detached from release candidate")
        if not isinstance(self.assessment, ReleaseAssessment):
            raise CamValidationError("Offline release assessment is invalid")
        if not isinstance(self.package_id, ContentFingerprint) or not isinstance(self.package_status, PackageStatus):
            raise CamValidationError("Offline handoff package identity is invalid")
        if tuple(sorted(set(self.stale_reasons))) != self.stale_reasons:
            raise CamInvariantError("Offline stale reasons must be unique and ordered")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.record_fingerprint is None:
            object.__setattr__(self, "record_fingerprint", calculated)
        elif self.record_fingerprint != calculated:
            raise CamInvariantError("Offline release record fingerprint mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": RECORD_FORMAT, "format_version": self.format_version,
            "record_id": self.record_id, "session": self.session.to_dict(),
            "candidate": self.candidate.to_dict(), "review": self.review.to_dict(),
            "acknowledgement": self.acknowledgement.to_dict(),
            "assessment": self.assessment.to_dict(), "package_id": self.package_id.to_dict(),
            "package_status": self.package_status.value, "stale_reasons": list(self.stale_reasons),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_payload(), "record_fingerprint": self.record_fingerprint.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OfflineReleaseRecord":
        fields = {
            "format", "format_version", "record_id", "session", "candidate", "review",
            "acknowledgement", "assessment", "package_id", "package_status",
            "stale_reasons", "record_fingerprint",
        }
        if (
            not isinstance(data, dict) or set(data) != fields or data["format"] != RECORD_FORMAT
            or not isinstance(data["stale_reasons"], list)
        ):
            raise CamValidationError("Offline release record payload is malformed")
        try:
            package_status = PackageStatus(data["package_status"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Offline package status is invalid") from error
        return cls(
            data["record_id"], OfflineNCVerificationSession.from_dict(data["session"]),
            NCReleaseCandidate.from_dict(data["candidate"]),
            OperatorReview.from_dict(data["review"]),
            OperatorAcknowledgement.from_dict(data["acknowledgement"]),
            ReleaseAssessment.from_dict(data["assessment"]),
            ContentFingerprint.from_dict(data["package_id"]), package_status,
            tuple(data["stale_reasons"]), ContentFingerprint.from_dict(data["record_fingerprint"]),
            data["format_version"],
        )


def dumps_release_record(record: OfflineReleaseRecord) -> bytes:
    if not isinstance(record, OfflineReleaseRecord):
        raise TypeError("record must be OfflineReleaseRecord")
    return canonical_json_bytes(record.to_dict())


def loads_release_record(payload: bytes) -> OfflineReleaseRecord:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CamValidationError("Offline release JSON is invalid") from error
    return OfflineReleaseRecord.from_dict(data)


def _root(project_root: Path) -> Path:
    if not isinstance(project_root, Path):
        raise TypeError("project_root must be pathlib.Path")
    try:
        resolved = project_root.resolve(strict=True)
    except OSError as error:
        raise OfflineReleaseStoreError("Project root is unavailable") from error
    if not resolved.is_dir() or not resolved.name.casefold().endswith(".hms"):
        raise OfflineReleaseStoreError("Project root is invalid")
    return resolved / "post" / "qualification" / "tranche3"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.r223.tmp")
    if temporary.exists():
        raise OfflineReleaseStoreError("Stale Tranche3 temporary file exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != payload:
            raise OfflineReleaseStoreError("Tranche3 persistence read-back mismatch")
        os.replace(temporary, path)
    except OSError as error:
        raise OfflineReleaseStoreError("Tranche3 persistence failed") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not remove Tranche3 temporary file", exc_info=True)


class OfflineReleaseStore:
    """Persist immutable snapshots under existing project artifact storage."""

    def save(self, project_root: Path, record: OfflineReleaseRecord) -> OfflineReleaseRecord:
        root = _root(project_root)
        payload = dumps_release_record(record)
        filename = f"{record.record_id}-{record.record_fingerprint.digest}.json"
        _atomic_write(root / filename, payload)
        current = {item.record_id: item for item in self.load(project_root)}
        current[record.record_id] = record
        entries = []
        for item in sorted(current.values(), key=lambda value: value.record_id):
            item_payload = dumps_release_record(item)
            item_filename = f"{item.record_id}-{item.record_fingerprint.digest}.json"
            entries.append(
                {
                    "record_id": item.record_id, "record": item_filename,
                    "record_sha256": sha256_bytes(item_payload),
                    "record_fingerprint": item.record_fingerprint.to_dict(),
                    "candidate_fingerprint": item.candidate.candidate_fingerprint.to_dict(),
                    "package_id": item.package_id.to_dict(), "package_status": item.package_status.value,
                }
            )
        manifest = {
            "format": MANIFEST_FORMAT, "format_version": 1, "sqlite_schema": 5,
            "entries": entries,
        }
        manifest_bytes = canonical_json_bytes(manifest)
        _atomic_write(root / "manifest.json", manifest_bytes)
        _atomic_write(
            root / "manifest.json.sha256",
            f"{sha256_bytes(manifest_bytes)}  manifest.json\n".encode("utf-8"),
        )
        return record

    def load(self, project_root: Path) -> tuple[OfflineReleaseRecord, ...]:
        root = _root(project_root)
        manifest_path = root / "manifest.json"
        sidecar_path = root / "manifest.json.sha256"
        if not manifest_path.exists():
            if sidecar_path.exists():
                raise OfflineReleaseStoreError("Orphan Tranche3 manifest sidecar exists")
            return ()
        try:
            payload = manifest_path.read_bytes()
            if sidecar_path.read_bytes() != f"{sha256_bytes(payload)}  manifest.json\n".encode("utf-8"):
                raise OfflineReleaseStoreError("Tranche3 manifest sidecar mismatch")
            manifest = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OfflineReleaseStoreError("Tranche3 manifest is unreadable") from error
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"format", "format_version", "sqlite_schema", "entries"}
            or manifest["format"] != MANIFEST_FORMAT or manifest["format_version"] != 1
            or manifest["sqlite_schema"] != 5 or not isinstance(manifest["entries"], list)
        ):
            raise OfflineReleaseStoreError("Tranche3 manifest is malformed")
        records: list[OfflineReleaseRecord] = []
        entry_fields = {
            "record_id", "record", "record_sha256", "record_fingerprint",
            "candidate_fingerprint", "package_id", "package_status",
        }
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != entry_fields:
                raise OfflineReleaseStoreError("Tranche3 manifest entry is malformed")
            filename = entry["record"]
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise OfflineReleaseStoreError("Tranche3 record path is invalid")
            try:
                path = (root / filename).resolve(strict=True)
                if root.resolve() not in path.parents:
                    raise OfflineReleaseStoreError("Tranche3 record escaped its root")
                record_payload = path.read_bytes()
            except OSError as error:
                raise OfflineReleaseStoreError("Tranche3 record is unavailable") from error
            if sha256_bytes(record_payload) != entry["record_sha256"]:
                raise OfflineReleaseStoreError("Tranche3 record checksum mismatch")
            try:
                record = loads_release_record(record_payload)
            except (CamValidationError, CamInvariantError) as error:
                raise OfflineReleaseStoreError("Tranche3 record is invalid") from error
            if (
                record.record_id != entry["record_id"]
                or record.record_fingerprint.to_dict() != entry["record_fingerprint"]
                or record.candidate.candidate_fingerprint.to_dict() != entry["candidate_fingerprint"]
                or record.package_id.to_dict() != entry["package_id"]
                or record.package_status.value != entry["package_status"]
            ):
                raise OfflineReleaseStoreError("Tranche3 manifest identity mismatch")
            records.append(record)
        if len({item.record_id for item in records}) != len(records):
            raise OfflineReleaseStoreError("Tranche3 manifest contains duplicate records")
        return tuple(sorted(records, key=lambda item: item.record_id))


__all__ = [
    "OfflineReleaseRecord", "OfflineReleaseStore", "OfflineReleaseStoreError",
    "dumps_release_record", "loads_release_record",
]
