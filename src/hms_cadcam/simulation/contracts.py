"""Immutable contracts for optional 3-axis machining verification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint

SIMULATION_EVIDENCE_FORMAT = "HMS_MACHINING_SIMULATION_RESULT"
SIMULATION_EVIDENCE_VERSION = 1
SIMULATION_ENGINE_VERSION = "r241.1"


class QualityMode(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    DETAILED = "detailed"


class EngineKind(StrEnum):
    HEIGHTFIELD_3AXIS = "heightfield_3axis"


class ResultState(StrEnum):
    NOT_RUN = "not_run"
    RUNNING = "running"
    PARTIAL = "partial"
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    STALE = "stale"
    CANCELLED = "cancelled"


class CollisionKind(StrEnum):
    NO_COLLISION_FOUND = "no_collision_found"
    TOOL_COLLISION = "tool_collision"
    HOLDER_COLLISION = "holder_collision"
    FIXTURE_COLLISION = "fixture_collision"
    UNVERIFIED_GEOMETRY = "unverified_geometry"


class GougeStatus(StrEnum):
    NO_GOUGE_FOUND = "no_gouge_found"
    GOUGE_DETECTED = "gouge_detected"
    REMAINING_MATERIAL = "remaining_material"
    GEOMETRY_REFERENCE_UNAVAILABLE = "geometry_reference_unavailable"


class OperationCoverage(StrEnum):
    COMPLETE_JOB = "complete_job"
    SELECTED_OPERATIONS = "selected_operations"
    SINGLE_OPERATION = "single_operation"


def _fingerprint(value: ContentFingerprint, name: str) -> None:
    if not isinstance(value, ContentFingerprint):
        raise CamValidationError(f"{name} fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class SimulationSession:
    """Load-bearing session identity; presentation choices are excluded."""

    project_fingerprint: ContentFingerprint
    part_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    wcs_fingerprint: ContentFingerprint
    operation_fingerprints: tuple[tuple[str, ContentFingerprint], ...]
    tool_fingerprints: tuple[tuple[str, ContentFingerprint], ...]
    holder_fingerprints: tuple[tuple[str, ContentFingerprint | None], ...]
    fixture_fingerprints: tuple[tuple[str, ContentFingerprint | None], ...]
    settings_fingerprint: ContentFingerprint
    engine_fingerprint: ContentFingerprint
    coverage: OperationCoverage

    def __post_init__(self) -> None:
        for value, name in (
            (self.project_fingerprint, "project"),
            (self.part_fingerprint, "part"),
            (self.stock_fingerprint, "stock"),
            (self.wcs_fingerprint, "WCS"),
            (self.settings_fingerprint, "settings"),
            (self.engine_fingerprint, "engine"),
        ):
            _fingerprint(value, name)
        if not isinstance(self.coverage, OperationCoverage):
            raise CamValidationError("Simulation coverage is invalid")
        for collection, name, optional in (
            (self.operation_fingerprints, "operation", False),
            (self.tool_fingerprints, "tool", False),
            (self.holder_fingerprints, "holder", True),
            (self.fixture_fingerprints, "fixture", True),
        ):
            if not isinstance(collection, tuple):
                raise CamValidationError(f"Simulation {name} fingerprints must be immutable")
            identities: set[str] = set()
            for identity, fingerprint in collection:
                if not isinstance(identity, str) or not identity or identity in identities:
                    raise CamValidationError(f"Simulation {name} identity is invalid")
                identities.add(identity)
                if fingerprint is None and optional:
                    continue
                _fingerprint(fingerprint, name)  # type: ignore[arg-type]
            if tuple(sorted(collection, key=lambda item: item[0])) != collection:
                raise CamValidationError(f"Simulation {name} fingerprints must be sorted")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_identity_dict())

    def to_identity_dict(self) -> dict[str, Any]:
        def items(values: tuple[tuple[str, ContentFingerprint | None], ...]) -> list[list[Any]]:
            return [[key, None if value is None else value.to_dict()] for key, value in values]

        return {
            "project": self.project_fingerprint.to_dict(),
            "part": self.part_fingerprint.to_dict(),
            "stock": self.stock_fingerprint.to_dict(),
            "wcs": self.wcs_fingerprint.to_dict(),
            "operations": items(self.operation_fingerprints),
            "tools": items(self.tool_fingerprints),
            "holders": items(self.holder_fingerprints),
            "fixtures": items(self.fixture_fingerprints),
            "settings": self.settings_fingerprint.to_dict(),
            "engine": self.engine_fingerprint.to_dict(),
            "coverage": self.coverage.value,
        }


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: str
    duration_seconds: float
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage:
            raise CamValidationError("Simulation timing stage is invalid")
        if not isinstance(self.duration_seconds, (int, float)) or not math.isfinite(
            self.duration_seconds
        ) or self.duration_seconds < 0.0:
            raise CamValidationError("Simulation timing duration is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "duration_seconds": float(self.duration_seconds),
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True, slots=True)
class SimulationEvidence:
    session_fingerprint: ContentFingerprint
    engine: EngineKind
    engine_version: str
    quality_mode: QualityMode
    input_fingerprints: tuple[tuple[str, ContentFingerprint], ...]
    operation_ids: tuple[str, ...]
    coverage: OperationCoverage
    timings: tuple[StageTiming, ...]
    warnings: tuple[str, ...]
    collision_kinds: tuple[CollisionKind, ...]
    gouge_status: GougeStatus
    remaining_stock_available: bool
    result_fingerprint: ContentFingerprint
    state: ResultState
    accuracy_note: str
    format: str = SIMULATION_EVIDENCE_FORMAT
    format_version: int = SIMULATION_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if self.format != SIMULATION_EVIDENCE_FORMAT or self.format_version != SIMULATION_EVIDENCE_VERSION:
            raise CamValidationError("Simulation evidence format is unsupported")
        _fingerprint(self.session_fingerprint, "session")
        _fingerprint(self.result_fingerprint, "result")
        if not isinstance(self.engine, EngineKind) or not isinstance(self.quality_mode, QualityMode):
            raise CamValidationError("Simulation engine or quality is invalid")
        if not isinstance(self.state, ResultState) or not isinstance(self.gouge_status, GougeStatus):
            raise CamValidationError("Simulation evidence state is invalid")
        if not isinstance(self.coverage, OperationCoverage):
            raise CamValidationError("Simulation evidence coverage is invalid")
        if not self.engine_version or not self.accuracy_note:
            raise CamValidationError("Simulation evidence description is incomplete")
        if not self.operation_ids or len(set(self.operation_ids)) != len(self.operation_ids):
            raise CamValidationError("Simulation evidence operation coverage is invalid")
        if any(not isinstance(value, StageTiming) for value in self.timings):
            raise CamValidationError("Simulation evidence timing is invalid")
        if any(not isinstance(value, CollisionKind) for value in self.collision_kinds):
            raise CamValidationError("Simulation collision evidence is invalid")
        if self.coverage is not OperationCoverage.COMPLETE_JOB and self.state is ResultState.PASS:
            raise CamValidationError("Partial operation coverage cannot verify a complete job as PASS")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "format_version": self.format_version,
            "session_fingerprint": self.session_fingerprint.to_dict(),
            "engine": self.engine.value,
            "engine_version": self.engine_version,
            "quality_mode": self.quality_mode.value,
            "input_fingerprints": [[key, value.to_dict()] for key, value in self.input_fingerprints],
            "operation_ids": list(self.operation_ids),
            "coverage": self.coverage.value,
            "timings": [value.to_dict() for value in self.timings],
            "warnings": list(self.warnings),
            "collision_kinds": [value.value for value in self.collision_kinds],
            "gouge_status": self.gouge_status.value,
            "remaining_stock_available": self.remaining_stock_available,
            "result_fingerprint": self.result_fingerprint.to_dict(),
            "state": self.state.value,
            "accuracy_note": self.accuracy_note,
        }


def result_fingerprint(payload: Mapping[str, Any]) -> ContentFingerprint:
    """Hash a result payload after excluding wall-clock and random identities."""

    return ContentFingerprint.from_payload(dict(payload))
