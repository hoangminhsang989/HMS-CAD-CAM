"""Strongly typed UUID identities for persistent CAM entities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Self
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.errors import CamValidationError

_SERIALIZED_ID = re.compile(
    r"(?P<prefix>[a-z][a-z0-9_]*):"
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})"
)


@dataclass(frozen=True, slots=True)
class _CamUuidId:
    """Base implementation whose equality remains concrete-type aware."""

    value: UUID
    PREFIX: ClassVar[str] = "cam"

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise CamValidationError(f"{type(self).__name__} value must be UUID")
        if self.value.int == 0:
            raise CamValidationError(f"{type(self).__name__} UUID must not be nil")

    @classmethod
    def new(cls) -> Self:
        """Create a new random identity."""
        return cls(uuid4())

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse the canonical prefixed representation."""
        if not isinstance(value, str):
            raise CamValidationError(f"{cls.__name__} text must be a string")
        match = _SERIALIZED_ID.fullmatch(value)
        if match is None or match.group("prefix") != cls.PREFIX:
            raise CamValidationError(f"Invalid {cls.__name__}: {value!r}")
        try:
            parsed = UUID(match.group("uuid"))
        except ValueError as error:
            raise CamValidationError(f"Invalid {cls.__name__}: {value!r}") from error
        return cls(parsed)

    def __str__(self) -> str:
        return f"{self.PREFIX}:{self.value}"


@dataclass(frozen=True, slots=True)
class CamJobId(_CamUuidId):
    """Identity of one CAM job."""

    PREFIX: ClassVar[str] = "cam_job"


@dataclass(frozen=True, slots=True)
class SetupId(_CamUuidId):
    """Identity of one CAM setup."""

    PREFIX: ClassVar[str] = "setup"


@dataclass(frozen=True, slots=True)
class CamNodeId(_CamUuidId):
    """Identity of one node in the future CAM tree."""

    PREFIX: ClassVar[str] = "cam_node"


@dataclass(frozen=True, slots=True)
class OperationId(_CamUuidId):
    """Identity of one future CAM operation."""

    PREFIX: ClassVar[str] = "operation"


@dataclass(frozen=True, slots=True)
class ToolDefinitionId(_CamUuidId):
    """Identity of one tool definition."""

    PREFIX: ClassVar[str] = "tool_definition"


@dataclass(frozen=True, slots=True)
class HolderDefinitionId(_CamUuidId):
    """Identity of one holder definition."""

    PREFIX: ClassVar[str] = "holder_definition"


@dataclass(frozen=True, slots=True)
class ToolAssemblyId(_CamUuidId):
    """Identity of one tool and holder assembly."""

    PREFIX: ClassVar[str] = "tool_assembly"


@dataclass(frozen=True, slots=True)
class MachineDefinitionId(_CamUuidId):
    """Identity of one machine definition."""

    PREFIX: ClassVar[str] = "machine_definition"


@dataclass(frozen=True, slots=True)
class GeometryReferenceId(_CamUuidId):
    """Identity of one editable geometry reference."""

    PREFIX: ClassVar[str] = "geometry_reference"


@dataclass(frozen=True, slots=True)
class FixtureInstanceId(_CamUuidId):
    """Identity of one placed fixture instance."""

    PREFIX: ClassVar[str] = "fixture_instance"


@dataclass(frozen=True, slots=True)
class ToolpathArtifactId(_CamUuidId):
    """Identity of one future toolpath artifact."""

    PREFIX: ClassVar[str] = "toolpath_artifact"


CAM_ID_TYPES: tuple[type[_CamUuidId], ...] = (
    CamJobId,
    SetupId,
    CamNodeId,
    OperationId,
    ToolDefinitionId,
    HolderDefinitionId,
    ToolAssemblyId,
    MachineDefinitionId,
    GeometryReferenceId,
    FixtureInstanceId,
    ToolpathArtifactId,
)
