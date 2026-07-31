"""Immutable value types shared by the Stage 12.4B basic Lathe Post."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.lathe.lathe_post.identity import canonical_id


class BasicPostReadiness(StrEnum):
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"
    BASIC_NC_PREVIEW_READY_UNVERIFIED = "BASIC_NC_PREVIEW_READY_UNVERIFIED"
    BASIC_NC_EXPORT_READY_UNVERIFIED = "BASIC_NC_EXPORT_READY_UNVERIFIED"
    MACHINE_OUTPUT_READY = "MACHINE_OUTPUT_READY"


class BasicPostDiagnosticCode(StrEnum):
    INVALID_PROFILE = "BASIC_POST_INVALID_PROFILE"
    INVALID_PROGRAM = "BASIC_POST_INVALID_PROGRAM"
    MISSING_TOOL_MAPPING = "BASIC_POST_MISSING_TOOL_MAPPING"
    DUPLICATE_TOOL_MAPPING = "BASIC_POST_DUPLICATE_TOOL_MAPPING"
    INVALID_TOOL_MAPPING = "BASIC_POST_INVALID_TOOL_MAPPING"
    UNSUPPORTED_BLOCK = "BASIC_POST_UNSUPPORTED_BLOCK"
    MISSING_OPERATION_BOUNDARY = "BASIC_POST_OPERATION_BOUNDARY_MISSING"
    INVALID_SPINDLE = "BASIC_POST_INVALID_SPINDLE"
    INVALID_NUMERIC = "BASIC_POST_INVALID_NUMERIC"
    BASIC_POST_DWELL_SYNTAX_UNDEFINED = "BASIC_POST_DWELL_SYNTAX_UNDEFINED"
    THREAD_FEED_MISMATCH = "BASIC_POST_THREAD_FEED_MISMATCH"
    OUTPUT_INVALID = "BASIC_POST_OUTPUT_INVALID"
    EXPORT_ACK_REQUIRED = "BASIC_POST_EXPORT_ACK_REQUIRED"
    OVERWRITE_CONFIRMATION_REQUIRED = "BASIC_POST_OVERWRITE_CONFIRMATION_REQUIRED"
    EXPORT_FAILED = "BASIC_POST_EXPORT_FAILED"


@dataclass(frozen=True, slots=True)
class BasicPostDiagnostic:
    code: str
    message_key: str
    subject: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", canonical_id(self.code, "diagnostic code"))
        object.__setattr__(self, "message_key", canonical_id(self.message_key, "diagnostic message key"))
        if self.subject is not None:
            object.__setattr__(self, "subject", canonical_id(self.subject, "diagnostic subject"))


@dataclass(frozen=True, slots=True)
class BasicFinalSafeTool:
    tool_number: int = 3
    offset_number: int = 3

    def __post_init__(self) -> None:
        for name in ("tool_number", "offset_number"):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= 99:
                raise ValueError(f"{name} must be an integer from 1 to 99")


@dataclass(frozen=True, slots=True)
class BasicToolMapping:
    tool_id: str
    tool_number: int
    geometry_offset_number: int
    wear_offset_number: int | None = None
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", canonical_id(self.tool_id, "tool_id"))
        for name in ("tool_number", "geometry_offset_number"):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= 99:
                raise ValueError(f"{name} must be an integer from 1 to 99")
        if self.wear_offset_number is not None and (type(self.wear_offset_number) is not int or not 0 < self.wear_offset_number <= 99):
            raise ValueError("wear_offset_number must be an integer from 1 to 99 or None")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")
        if not isinstance(self.description, str):
            raise TypeError("description must be text")


@dataclass(frozen=True, slots=True)
class BasicPostMetadata:
    file_stem: str = "program"
    tool_descriptions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.file_stem, str):
            raise TypeError("file_stem must be text")
        if not isinstance(self.tool_descriptions, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(part, str) for part in item)
            for item in self.tool_descriptions
        ):
            raise TypeError("tool_descriptions must be immutable text pairs")


__all__ = [
    "BasicFinalSafeTool", "BasicPostDiagnostic", "BasicPostDiagnosticCode", "BasicPostMetadata",
    "BasicPostReadiness", "BasicToolMapping",
]
