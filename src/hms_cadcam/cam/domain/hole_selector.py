"""Persistent selector for one drilling VERTEX or circular EDGE."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hms_cadcam.cam.domain.errors import GeometryReferenceError

_SELECTOR = re.compile(
    r"hms_drill_v1:([0-9a-f]{64}):(vertex|circular_edge):([0-9a-f]{64})"
)


@dataclass(frozen=True, slots=True)
class PersistentHoleSelectorV1:
    """Identify native geometry without retaining a runtime topology index."""

    container_digest: str
    source_kind: str
    geometry_digest: str

    def __post_init__(self) -> None:
        if not _SELECTOR.fullmatch(str(self)):
            raise GeometryReferenceError("Persistent drilling selector v1 is invalid")

    def __str__(self) -> str:
        return (
            f"hms_drill_v1:{self.container_digest}:"
            f"{self.source_kind}:{self.geometry_digest}"
        )

    @classmethod
    def parse(cls, value: str) -> "PersistentHoleSelectorV1":
        if not isinstance(value, str):
            raise GeometryReferenceError("Persistent drilling selector must be text")
        matched = _SELECTOR.fullmatch(value)
        if matched is None:
            raise GeometryReferenceError("Unsupported persistent drilling selector")
        return cls(matched.group(1), matched.group(2), matched.group(3))
