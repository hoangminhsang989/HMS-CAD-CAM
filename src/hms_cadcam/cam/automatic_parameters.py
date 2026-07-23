"""Shared automatic-parameter contract for CAM operation editors.

The contract is deliberately independent from Qt and from any CAD-kernel object.
It can therefore be reused by later CAM strategies without coupling their domain
models to one editor implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from enum import StrEnum
from typing import Any, TypeAlias

from hms_cadcam.cam.domain import ContentFingerprint, DependencyFingerprint


AUTOMATIC_PARAMETER_CONTRACT_KEY = "automatic_parameter_contract"
AUTOMATIC_PARAMETER_CONTRACT_VERSION = 1
_KEY = re.compile(r"[a-z][a-z0-9_.-]{0,127}")

AutomaticPrimitive: TypeAlias = str | int | float | bool | None


class AutomaticParameterMode(StrEnum):
    """Whether the effective value comes from policy or user intent."""

    AUTO = "auto"
    MANUAL = "manual"


class AutomaticParameterStatus(StrEnum):
    """Resolution state exposed explicitly instead of silently guessing."""

    RESOLVED = "resolved"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNSUPPORTED = "unsupported"
    UNRESOLVED = "unresolved"


class CamQualityProfile(StrEnum):
    """Small, shared quality vocabulary for automatic CAM policies."""

    FAST = "fast"
    BALANCED = "balanced"
    HIGH = "high"


def _primitive(value: object, name: str) -> AutomaticPrimitive:
    if value is None or type(value) in {str, int, bool}:
        if isinstance(value, str) and len(value) > 1024:
            raise ValueError(f"{name} is too long")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError(f"{name} must be a finite JSON primitive")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class AutomaticValidationResult:
    """Validation attached to one preserved automatic/manual value."""

    valid: bool
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool) or not isinstance(self.message, str):
            raise TypeError("Automatic validation result is invalid")
        if self.valid and self.message:
            raise ValueError("A valid automatic value cannot carry an error message")

    def to_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "message": self.message}

    @classmethod
    def from_dict(cls, data: object) -> "AutomaticValidationResult":
        if not isinstance(data, dict) or set(data) != {"valid", "message"}:
            raise ValueError("Automatic validation payload is malformed")
        return cls(data["valid"], data["message"])  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AutomaticParameterValue:
    """One resolved value plus provenance, dependencies and override intent."""

    key: str
    mode: AutomaticParameterMode
    resolved_value: AutomaticPrimitive
    source: str
    policy_version: int
    dependency_fingerprint: DependencyFingerprint
    status: AutomaticParameterStatus
    reason: str
    override_value: AutomaticPrimitive = None
    validation: AutomaticValidationResult = AutomaticValidationResult(True)

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or _KEY.fullmatch(self.key) is None:
            raise ValueError("Automatic parameter key is invalid")
        if not isinstance(self.mode, AutomaticParameterMode):
            raise TypeError("Automatic parameter mode is invalid")
        object.__setattr__(
            self, "resolved_value", _primitive(self.resolved_value, "Resolved value")
        )
        object.__setattr__(
            self, "override_value", _primitive(self.override_value, "Override value")
        )
        object.__setattr__(self, "source", _text(self.source, "Automatic source"))
        object.__setattr__(self, "reason", _text(self.reason, "Automatic reason"))
        if type(self.policy_version) is not int or self.policy_version < 1:
            raise ValueError("Automatic policy version is invalid")
        if not isinstance(self.dependency_fingerprint, DependencyFingerprint):
            raise TypeError("Automatic dependency fingerprint is invalid")
        if not isinstance(self.status, AutomaticParameterStatus):
            raise TypeError("Automatic parameter status is invalid")
        if not isinstance(self.validation, AutomaticValidationResult):
            raise TypeError("Automatic validation is invalid")

    @property
    def effective_value(self) -> AutomaticPrimitive:
        """Return user intent in manual mode, otherwise the resolved policy value."""
        if self.mode is AutomaticParameterMode.MANUAL:
            return self.override_value
        return self.resolved_value

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "mode": self.mode.value,
            "resolved_value": self.resolved_value,
            "source": self.source,
            "policy_version": self.policy_version,
            "dependency_fingerprint": self.dependency_fingerprint.to_dict(),
            "status": self.status.value,
            "reason": self.reason,
            "override_value": self.override_value,
            "validation": self.validation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> "AutomaticParameterValue":
        fields = {
            "key",
            "mode",
            "resolved_value",
            "source",
            "policy_version",
            "dependency_fingerprint",
            "status",
            "reason",
            "override_value",
            "validation",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("Automatic parameter payload is malformed")
        return cls(
            data["key"],  # type: ignore[arg-type]
            AutomaticParameterMode(data["mode"]),
            data["resolved_value"],  # type: ignore[arg-type]
            data["source"],  # type: ignore[arg-type]
            data["policy_version"],  # type: ignore[arg-type]
            DependencyFingerprint.from_dict(data["dependency_fingerprint"]),  # type: ignore[arg-type]
            AutomaticParameterStatus(data["status"]),
            data["reason"],  # type: ignore[arg-type]
            data["override_value"],  # type: ignore[arg-type]
            AutomaticValidationResult.from_dict(data["validation"]),
        )


@dataclass(frozen=True, slots=True)
class AutomaticParameterContract:
    """Versioned bundle persisted in an existing operation parameter string."""

    policy_key: str
    policy_version: int
    quality_profile: CamQualityProfile
    values: tuple[AutomaticParameterValue, ...]
    contract_version: int = AUTOMATIC_PARAMETER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.policy_key, str) or _KEY.fullmatch(self.policy_key) is None:
            raise ValueError("Automatic policy key is invalid")
        if type(self.policy_version) is not int or self.policy_version < 1:
            raise ValueError("Automatic policy version is invalid")
        if self.contract_version != AUTOMATIC_PARAMETER_CONTRACT_VERSION:
            raise ValueError("Automatic contract version is unsupported")
        if not isinstance(self.quality_profile, CamQualityProfile):
            raise TypeError("CAM quality profile is invalid")
        if not isinstance(self.values, tuple) or any(
            not isinstance(item, AutomaticParameterValue) for item in self.values
        ):
            raise TypeError("Automatic values must be an immutable typed tuple")
        ordered = tuple(sorted(self.values, key=lambda item: item.key))
        if len({item.key for item in ordered}) != len(ordered):
            raise ValueError("Automatic parameter keys must be unique")
        object.__setattr__(self, "values", ordered)

    def value(self, key: str) -> AutomaticParameterValue:
        """Return a required value by its stable semantic key."""
        try:
            return next(item for item in self.values if item.key == key)
        except StopIteration as error:
            raise KeyError(key) from error

    @property
    def effective_fingerprint(self) -> ContentFingerprint:
        """Hash every effective value, mode, policy and dependency decision."""
        return ContentFingerprint.from_payload(
            {
                "policy_key": self.policy_key,
                "policy_version": self.policy_version,
                "quality_profile": self.quality_profile.value,
                "values": [
                    {
                        "key": item.key,
                        "mode": item.mode.value,
                        "effective_value": item.effective_value,
                        "dependency_fingerprint": item.dependency_fingerprint.to_dict(),
                        "status": item.status.value,
                        "validation": item.validation.to_dict(),
                    }
                    for item in self.values
                ],
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "policy_key": self.policy_key,
            "policy_version": self.policy_version,
            "quality_profile": self.quality_profile.value,
            "values": [item.to_dict() for item in self.values],
        }

    def to_json(self) -> str:
        """Return canonical compact UTF-8 JSON suitable for OperationParameterSet."""
        payload = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(payload) > 4096:
            raise ValueError("Automatic parameter contract exceeds parameter capacity")
        return payload

    @classmethod
    def from_dict(cls, data: object) -> "AutomaticParameterContract":
        fields = {
            "contract_version",
            "policy_key",
            "policy_version",
            "quality_profile",
            "values",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("Automatic contract payload is malformed")
        values = data["values"]
        if not isinstance(values, list):
            raise ValueError("Automatic contract values must be a list")
        return cls(
            data["policy_key"],  # type: ignore[arg-type]
            data["policy_version"],  # type: ignore[arg-type]
            CamQualityProfile(data["quality_profile"]),
            tuple(AutomaticParameterValue.from_dict(item) for item in values),
            data["contract_version"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, payload: str) -> "AutomaticParameterContract":
        if not isinstance(payload, str) or not payload or len(payload) > 4096:
            raise ValueError("Automatic contract JSON is invalid")
        try:
            data: Any = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("Automatic contract JSON is malformed") from error
        return cls.from_dict(data)


__all__ = [
    "AUTOMATIC_PARAMETER_CONTRACT_KEY",
    "AUTOMATIC_PARAMETER_CONTRACT_VERSION",
    "AutomaticParameterContract",
    "AutomaticParameterMode",
    "AutomaticParameterStatus",
    "AutomaticParameterValue",
    "AutomaticValidationResult",
    "CamQualityProfile",
]
