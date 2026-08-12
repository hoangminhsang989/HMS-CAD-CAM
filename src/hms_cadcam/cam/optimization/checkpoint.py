"""Atomic phase checkpoints with explicit incomplete states."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class CheckpointState(StrEnum):
    BUILDING = "BUILDING"
    COMPLETE = "COMPLETE"
    STALE = "STALE"
    CORRUPT = "CORRUPT"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    operation_id: str
    phase: str
    fingerprint: str
    state: CheckpointState
    payload_sha256: str
    payload_size: int


class CheckpointStore:
    """Store one checkpoint per operation/phase, never publishing partial data."""

    def path_for(self, project_root: Path, operation_id: str, phase: str) -> Path:
        if not isinstance(project_root, Path) or not operation_id or not phase:
            raise ValueError("Checkpoint identity is invalid")
        return project_root / ".hms" / "cam" / "operations" / operation_id / "checkpoints" / f"{phase}.json"

    def publish(self, project_root: Path, operation_id: str, phase: str, fingerprint: str, payload: bytes) -> CheckpointRecord:
        if not isinstance(payload, bytes) or not fingerprint:
            raise ValueError("Checkpoint payload or fingerprint is invalid")
        target = self.path_for(project_root, operation_id, phase)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()
        record = CheckpointRecord(operation_id, phase, fingerprint, CheckpointState.COMPLETE, digest, len(payload))
        envelope = {"format": "HMS_R246_CHECKPOINT", "format_version": 1, "record": asdict(record), "payload": payload.decode("utf-8")}
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            with temp.open("xb") as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return record

    def load(self, project_root: Path, operation_id: str, phase: str, fingerprint: str) -> tuple[CheckpointRecord, bytes] | None:
        path = self.path_for(project_root, operation_id, phase)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("format") != "HMS_R246_CHECKPOINT" or raw.get("format_version") != 1:
                return None
            data = raw["record"]
            record = CheckpointRecord(data["operation_id"], data["phase"], data["fingerprint"], CheckpointState(data["state"]), data["payload_sha256"], data["payload_size"])
            payload = raw["payload"].encode("utf-8")
            if record.state is not CheckpointState.COMPLETE or record.fingerprint != fingerprint or len(payload) != record.payload_size or hashlib.sha256(payload).hexdigest() != record.payload_sha256:
                return None
            return record, payload
        except (OSError, UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
