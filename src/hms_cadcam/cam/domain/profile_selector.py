"""Persistent profile selector independent from CAD runtime identities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hms_cadcam.cam.domain.errors import GeometryReferenceError

_SELECTOR = re.compile(r"hms_profile_v1:([0-9a-f]{64}):(face|wire):([0-9a-f]{64})")


@dataclass(frozen=True, slots=True)
class PersistentProfileSelectorV1:
    """Identify one FACE outer wire or explicit WIRE in a persistent container."""

    container_digest: str
    source_kind: str
    profile_digest: str

    def __post_init__(self) -> None:
        if not _SELECTOR.fullmatch(str(self)):
            raise GeometryReferenceError("Persistent profile selector v1 is invalid")

    def __str__(self) -> str:
        return f"hms_profile_v1:{self.container_digest}:{self.source_kind}:{self.profile_digest}"

    @classmethod
    def parse(cls, value: str) -> "PersistentProfileSelectorV1":
        if not isinstance(value, str):
            raise GeometryReferenceError("Persistent profile selector must be text")
        matched = _SELECTOR.fullmatch(value)
        if matched is None:
            raise GeometryReferenceError("Unsupported persistent profile selector")
        return cls(matched.group(1), matched.group(2), matched.group(3))
