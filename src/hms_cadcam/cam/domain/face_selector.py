"""Persistent CAM face selector independent from CAD runtime identities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hms_cadcam.cam.domain.errors import GeometryReferenceError

_SELECTOR = re.compile(r"hms_face_v1:([0-9a-f]{64}):([0-9a-f]{64})")


@dataclass(frozen=True, slots=True)
class PersistentFaceSelectorV1:
    """Identify one face inside one persistent occurrence/container."""

    container_digest: str
    face_digest: str

    def __post_init__(self) -> None:
        encoded = f"hms_face_v1:{self.container_digest}:{self.face_digest}"
        if not _SELECTOR.fullmatch(encoded):
            raise GeometryReferenceError("Persistent face selector v1 is invalid")

    def __str__(self) -> str:
        return f"hms_face_v1:{self.container_digest}:{self.face_digest}"

    @classmethod
    def parse(cls, value: str) -> "PersistentFaceSelectorV1":
        if not isinstance(value, str):
            raise GeometryReferenceError("Persistent face selector must be text")
        matched = _SELECTOR.fullmatch(value)
        if matched is None:
            raise GeometryReferenceError("Unsupported persistent face selector")
        return cls(matched.group(1), matched.group(2))
