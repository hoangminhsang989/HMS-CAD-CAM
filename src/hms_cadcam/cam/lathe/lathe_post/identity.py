"""Immutable ownership identity for the Lathe controller-neutral program."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def canonical_id(value: object, subject: str) -> str:
    """Return a stable text representation for a domain identity."""

    if isinstance(value, bool) or value is None:
        raise ValueError(f"{subject} must be a non-blank identity")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{subject} must be a non-blank identity")
    return text


def non_negative_int(value: object, subject: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{subject} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class LatheProgramIdentity:
    """Exact immutable owner of one semantic Lathe program revision."""

    project_id: str
    document_id: str
    source_id: str
    source_generation: int
    setup_id: str
    program_id: str
    revision: int

    def __post_init__(self) -> None:
        for field in ("project_id", "document_id", "source_id", "setup_id", "program_id"):
            object.__setattr__(self, field, canonical_id(getattr(self, field), field))
        object.__setattr__(self, "source_generation", non_negative_int(self.source_generation, "source_generation"))
        object.__setattr__(self, "revision", non_negative_int(self.revision, "revision"))

    def to_canonical(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "document_id": self.document_id,
            "source_id": self.source_id,
            "source_generation": self.source_generation,
            "setup_id": self.setup_id,
            "program_id": self.program_id,
            "revision": self.revision,
        }


__all__ = ["LatheProgramIdentity", "canonical_id", "non_negative_int"]
