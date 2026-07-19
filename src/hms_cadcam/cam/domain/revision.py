"""Versioned revisions and deterministic content fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from hms_cadcam.cam.domain.errors import CamValidationError

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ALGORITHM = re.compile(r"[a-z][a-z0-9_-]{1,31}")


@dataclass(frozen=True, slots=True, order=True)
class Revision:
    """A monotonic non-negative domain revision."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value < 0:
            raise CamValidationError("Revision must be a non-negative integer")

    def next(self) -> "Revision":
        """Return the following revision."""
        return Revision(self.value + 1)

    def to_dict(self) -> dict[str, int]:
        """Serialize this revision."""
        return {"value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Revision":
        """Deserialize one complete revision payload."""
        if not isinstance(data, dict) or set(data) != {"value"}:
            raise CamValidationError("Revision payload is malformed")
        return cls(data["value"])


@dataclass(frozen=True, slots=True)
class ContentFingerprint:
    """Opaque digest carrying its algorithm identity and version."""

    algorithm: str
    algorithm_version: int
    digest: str
    KIND: ClassVar[str] = "content"

    def __post_init__(self) -> None:
        if not isinstance(self.algorithm, str) or not _ALGORITHM.fullmatch(
            self.algorithm
        ):
            raise CamValidationError("Fingerprint algorithm is invalid")
        if type(self.algorithm_version) is not int or self.algorithm_version <= 0:
            raise CamValidationError("Fingerprint algorithm version must be positive")
        if not isinstance(self.digest, str) or not self.digest:
            raise CamValidationError("Fingerprint digest must not be empty")
        if self.algorithm == "sha256" and not _SHA256.fullmatch(self.digest):
            raise CamValidationError("SHA-256 fingerprint digest is invalid")

    @classmethod
    def from_payload(cls, payload: Any) -> Self:
        """Create a SHA-256 v1 digest from canonical JSON-compatible input."""
        try:
            encoded = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        except (TypeError, ValueError) as error:
            raise CamValidationError("Fingerprint input is not canonical JSON data") from error
        return cls("sha256", 1, hashlib.sha256(encoded).hexdigest())

    def to_dict(self) -> dict[str, str | int]:
        """Serialize this fingerprint with its concrete semantic kind."""
        return {
            "kind": self.KIND,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize a strictly typed fingerprint."""
        required = {"kind", "algorithm", "algorithm_version", "digest"}
        if not isinstance(data, dict) or set(data) != required:
            raise CamValidationError("Fingerprint payload is malformed")
        if data["kind"] != cls.KIND:
            raise CamValidationError(f"Expected {cls.KIND} fingerprint")
        return cls(data["algorithm"], data["algorithm_version"], data["digest"])


@dataclass(frozen=True, slots=True)
class GeometryFingerprint(ContentFingerprint):
    """Fingerprint of referenced geometry content."""

    KIND: ClassVar[str] = "geometry"


@dataclass(frozen=True, slots=True)
class DependencyFingerprint(ContentFingerprint):
    """Fingerprint of a future derived-artifact dependency set."""

    KIND: ClassVar[str] = "dependency"
