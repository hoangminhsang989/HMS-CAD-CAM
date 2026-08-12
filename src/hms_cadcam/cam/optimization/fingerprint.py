"""Strict, deterministic identity for reusable calculation artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from hms_cadcam.cam.domain.revision import ContentFingerprint


@dataclass(frozen=True, slots=True)
class CalculationFingerprintInput:
    """All semantic inputs that may influence one calculation result.

    Values are intentionally opaque canonical-JSON data.  Callers must supply
    the complete strategy-specific payload; timestamps are never included by
    this type.
    """

    operation_id: str
    operation_type: str
    strategy: Mapping[str, Any]
    geometry: Mapping[str, Any]
    setup: Mapping[str, Any]
    stock: Mapping[str, Any]
    tool: Mapping[str, Any]
    holder: Mapping[str, Any] | None
    boundary: Mapping[str, Any] | None
    parameters: Mapping[str, Any]
    dependencies: tuple[ContentFingerprint, ...]
    engine_id: str
    engine_version: str
    precision_policy: Mapping[str, Any]
    algorithm_version: str

    def payload(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "strategy": dict(self.strategy),
            "geometry": dict(self.geometry),
            "setup": dict(self.setup),
            "stock": dict(self.stock),
            "tool": dict(self.tool),
            "holder": None if self.holder is None else dict(self.holder),
            "boundary": None if self.boundary is None else dict(self.boundary),
            "parameters": dict(self.parameters),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "precision_policy": dict(self.precision_policy),
            "algorithm_version": self.algorithm_version,
        }


@dataclass(frozen=True, slots=True)
class CalculationFingerprint:
    """Versioned calculation input fingerprint and its canonical payload."""

    value: ContentFingerprint
    payload: Mapping[str, Any]

    @classmethod
    def from_input(cls, value: CalculationFingerprintInput) -> "CalculationFingerprint":
        if not isinstance(value, CalculationFingerprintInput):
            raise TypeError("Calculation fingerprint input is invalid")
        payload = value.payload()
        return cls(ContentFingerprint.from_payload(payload), payload)
