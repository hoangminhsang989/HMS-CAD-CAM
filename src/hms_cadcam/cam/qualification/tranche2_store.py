"""Additive deterministic persistence for Tranche2 setup/evidence records."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.qualification.evidence_model import (
    LEVEL2_RECORD_FORMAT,
    LEVEL2_RECORD_VERSION,
    Level2QualificationRecord,
)
from hms_cadcam.cam.qualification.model import canonical_json_bytes, sha256_bytes


_logger = logging.getLogger(__name__)
_MANIFEST_FORMAT = "HMS_STAGE18A_LEVEL2_QUALIFICATION_MANIFEST"
_MANIFEST_NAME = "manifest.json"
_MANIFEST_SIDECAR_NAME = "manifest.json.sha256"


class Tranche2StoreError(RuntimeError):
    """Raised when Level2 workflow persistence cannot be trusted."""


def dumps_level2_record(record: Level2QualificationRecord) -> bytes:
    if not isinstance(record, Level2QualificationRecord):
        raise TypeError("record must be Level2QualificationRecord")
    return canonical_json_bytes(record.to_dict())


def loads_level2_record(payload: bytes) -> Level2QualificationRecord:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CamValidationError("Level2 record JSON is invalid") from error
    if not isinstance(data, dict):
        raise CamValidationError("Level2 record root must be an object")
    return Level2QualificationRecord.from_dict(data)


def _qualification_root(project_root: Path) -> Path:
    if not isinstance(project_root, Path):
        raise TypeError("project_root must be pathlib.Path")
    try:
        root = project_root.resolve(strict=True)
    except OSError as error:
        raise Tranche2StoreError("Qualification project root is unavailable") from error
    if not root.is_dir() or not root.name.casefold().endswith(".hms"):
        raise Tranche2StoreError("Qualification project root is invalid")
    return root / "post" / "qualification" / "level2"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.r221.tmp")
    if temporary.exists():
        raise Tranche2StoreError("Stale Tranche2 temporary file exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != payload:
            raise Tranche2StoreError("Tranche2 persistence read-back mismatch")
        os.replace(temporary, path)
    except OSError as error:
        raise Tranche2StoreError("Tranche2 persistence failed") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            _logger.warning("Could not remove Tranche2 temporary file", exc_info=True)


def _manifest_sidecar(payload: bytes) -> bytes:
    return f"{sha256_bytes(payload)}  {_MANIFEST_NAME}\n".encode("utf-8")


class Tranche2QualificationStore:
    """Persist immutable snapshots without changing SQLite schema 5."""

    def save(
        self,
        project_root: Path,
        record: Level2QualificationRecord,
    ) -> Level2QualificationRecord:
        if not isinstance(record, Level2QualificationRecord):
            raise TypeError("record must be Level2QualificationRecord")
        root = _qualification_root(project_root)
        payload = dumps_level2_record(record)
        filename = f"{record.record_id}-{record.fingerprint.digest}.json"
        _atomic_write(root / filename, payload)
        records = {item.record_id: item for item in self.load(project_root)}
        records[record.record_id] = record
        entries = []
        for item in sorted(records.values(), key=lambda value: value.record_id):
            item_payload = dumps_level2_record(item)
            item_filename = f"{item.record_id}-{item.fingerprint.digest}.json"
            entries.append(
                {
                    "record_id": item.record_id,
                    "record_fingerprint": item.fingerprint.to_dict(),
                    "record": item_filename,
                    "record_sha256": sha256_bytes(item_payload),
                    "nc_sha256": item.setup.nc_sha256,
                    "machine_profile_fingerprint": item.setup.machine_profile_fingerprint.to_dict(),
                    "setup_fingerprint": item.setup.fingerprint.to_dict(),
                    "policy_fingerprint": item.policy.fingerprint.to_dict(),
                    "attempt_count": len(item.attempts),
                }
            )
        manifest = {
            "format": _MANIFEST_FORMAT,
            "format_version": LEVEL2_RECORD_VERSION,
            "entries": entries,
        }
        manifest_payload = canonical_json_bytes(manifest)
        _atomic_write(root / _MANIFEST_NAME, manifest_payload)
        _atomic_write(root / _MANIFEST_SIDECAR_NAME, _manifest_sidecar(manifest_payload))
        return record

    def load(self, project_root: Path) -> tuple[Level2QualificationRecord, ...]:
        root = _qualification_root(project_root)
        manifest_path = root / _MANIFEST_NAME
        sidecar_path = root / _MANIFEST_SIDECAR_NAME
        if not manifest_path.exists():
            if sidecar_path.exists():
                raise Tranche2StoreError("Orphan Tranche2 manifest sidecar exists")
            return ()
        try:
            manifest_payload = manifest_path.read_bytes()
            if not sidecar_path.is_file():
                raise Tranche2StoreError("Tranche2 manifest sidecar is missing")
            if sidecar_path.read_bytes() != _manifest_sidecar(manifest_payload):
                raise Tranche2StoreError("Tranche2 manifest sidecar mismatch")
            manifest = json.loads(manifest_payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Tranche2StoreError("Tranche2 manifest is unreadable") from error
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"format", "format_version", "entries"}
            or manifest["format"] != _MANIFEST_FORMAT
            or manifest["format_version"] != LEVEL2_RECORD_VERSION
            or not isinstance(manifest["entries"], list)
        ):
            raise Tranche2StoreError("Tranche2 manifest is malformed")
        fields = {
            "record_id", "record_fingerprint", "record", "record_sha256",
            "nc_sha256", "machine_profile_fingerprint", "setup_fingerprint",
            "policy_fingerprint", "attempt_count",
        }
        records: list[Level2QualificationRecord] = []
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != fields:
                raise Tranche2StoreError("Tranche2 manifest entry is malformed")
            filename = entry["record"]
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not filename.endswith(".json")
            ):
                raise Tranche2StoreError("Tranche2 record path is invalid")
            try:
                path = (root / filename).resolve(strict=True)
                if root.resolve() not in path.parents:
                    raise Tranche2StoreError("Tranche2 record escaped its root")
                payload = path.read_bytes()
            except OSError as error:
                raise Tranche2StoreError("Tranche2 record is unavailable") from error
            if sha256_bytes(payload) != entry["record_sha256"]:
                raise Tranche2StoreError("Tranche2 record checksum mismatch")
            try:
                record = loads_level2_record(payload)
            except CamValidationError as error:
                raise Tranche2StoreError("Tranche2 record is invalid") from error
            if (
                record.record_id != entry["record_id"]
                or record.fingerprint.to_dict() != entry["record_fingerprint"]
                or record.setup.nc_sha256 != entry["nc_sha256"]
                or record.setup.machine_profile_fingerprint.to_dict()
                != entry["machine_profile_fingerprint"]
                or record.setup.fingerprint.to_dict() != entry["setup_fingerprint"]
                or record.policy.fingerprint.to_dict() != entry["policy_fingerprint"]
                or len(record.attempts) != entry["attempt_count"]
            ):
                raise Tranche2StoreError("Tranche2 manifest identity mismatch")
            records.append(record)
        if len({item.record_id for item in records}) != len(records):
            raise CamInvariantError("Tranche2 manifest contains duplicate records")
        return tuple(sorted(records, key=lambda item: item.record_id))

    def export_package(
        self,
        record: Level2QualificationRecord,
        target: Path,
    ) -> tuple[Path, str]:
        """Export deterministic metadata bytes plus a SHA-256 sidecar."""

        if not isinstance(record, Level2QualificationRecord):
            raise TypeError("record must be Level2QualificationRecord")
        if not isinstance(target, Path):
            raise TypeError("target must be pathlib.Path")
        if target.suffix.casefold() != ".json":
            raise Tranche2StoreError("Evidence package target must be a .json file")
        payload = dumps_level2_record(record)
        digest = sha256_bytes(payload)
        _atomic_write(target, payload)
        _atomic_write(target.with_suffix(target.suffix + ".sha256"), f"{digest}  {target.name}\n".encode("utf-8"))
        return target, digest


__all__ = [
    "Tranche2QualificationStore", "Tranche2StoreError", "dumps_level2_record",
    "loads_level2_record",
]
