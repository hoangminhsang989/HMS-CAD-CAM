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
class GeometryInputId(_CamUuidId):
    """Identity of one ordered operation geometry input occurrence."""

    PREFIX: ClassVar[str] = "geometry_input"


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
class CamSurfaceSelectionId(_CamUuidId):
    """Identity of one persistent CAM 3D surface selection."""

    PREFIX: ClassVar[str] = "cam_surface_selection"


@dataclass(frozen=True, slots=True)
class MachiningZone3DId(_CamUuidId):
    """Identity of one CAM 3D machining zone."""

    PREFIX: ClassVar[str] = "machining_zone_3d"


@dataclass(frozen=True, slots=True)
class Cam3DGeometrySnapshotId(_CamUuidId):
    """Identity of one immutable CAM 3D geometry snapshot."""

    PREFIX: ClassVar[str] = "cam3d_geometry_snapshot"


@dataclass(frozen=True, slots=True)
class Cam3DCalculationContextId(_CamUuidId):
    """Identity of one CAM 3D calculation context."""

    PREFIX: ClassVar[str] = "cam3d_calculation_context"


@dataclass(frozen=True, slots=True)
class ToolpathArtifactId(_CamUuidId):
    """Identity of one future toolpath artifact."""

    PREFIX: ClassVar[str] = "toolpath_artifact"


@dataclass(frozen=True, slots=True)
class ToolpathEventId(_CamUuidId):
    """Stable identity of one event occurrence in a toolpath artifact."""

    PREFIX: ClassVar[str] = "toolpath_event"


@dataclass(frozen=True, slots=True)
class SimulationRequestId(_CamUuidId):
    """Identity of one simulation request envelope."""

    PREFIX: ClassVar[str] = "simulation_request"


@dataclass(frozen=True, slots=True)
class SimulationResultId(_CamUuidId):
    """Identity of one published simulation result."""

    PREFIX: ClassVar[str] = "simulation_result"


@dataclass(frozen=True, slots=True)
class PostProcessorDefinitionId(_CamUuidId):
    """Identity of one versioned post-processor definition."""

    PREFIX: ClassVar[str] = "post_definition"


@dataclass(frozen=True, slots=True)
class ProductionControllerProfileId(_CamUuidId):
    """Identity of one versioned production-controller profile."""

    PREFIX: ClassVar[str] = "production_controller_profile"


@dataclass(frozen=True, slots=True)
class PostRequestId(_CamUuidId):
    """Identity of one post-processing request envelope."""

    PREFIX: ClassVar[str] = "post_request"


@dataclass(frozen=True, slots=True)
class PostResultId(_CamUuidId):
    """Identity of one post-processing result."""

    PREFIX: ClassVar[str] = "post_result"


@dataclass(frozen=True, slots=True)
class NCProgramId(_CamUuidId):
    """Identity of one controller-neutral single-operation NC program IR."""

    PREFIX: ClassVar[str] = "nc_program"


@dataclass(frozen=True, slots=True)
class ProgramAssemblyRequestId(_CamUuidId):
    """Identity of one multi-operation program-assembly request."""

    PREFIX: ClassVar[str] = "program_assembly_request"


@dataclass(frozen=True, slots=True)
class ProgramAssemblyResultId(_CamUuidId):
    """Identity of one published multi-operation program result."""

    PREFIX: ClassVar[str] = "program_assembly_result"


@dataclass(frozen=True, slots=True)
class ProgramOperationSectionId(_CamUuidId):
    """Identity of one ordered operation section inside an NC program."""

    PREFIX: ClassVar[str] = "program_operation_section"


@dataclass(frozen=True, slots=True)
class NCExportRequestId(_CamUuidId):
    """Identity of one NC file-export request envelope."""

    PREFIX: ClassVar[str] = "nc_export_request"


@dataclass(frozen=True, slots=True)
class NCExportResultId(_CamUuidId):
    """Identity of one NC file-export result."""

    PREFIX: ClassVar[str] = "nc_export_result"


@dataclass(frozen=True, slots=True)
class NCArtifactId(_CamUuidId):
    """Identity of one project-managed production NC artifact."""

    PREFIX: ClassVar[str] = "nc_artifact"


CAM_ID_TYPES: tuple[type[_CamUuidId], ...] = (
    CamJobId,
    SetupId,
    CamNodeId,
    OperationId,
    GeometryInputId,
    ToolDefinitionId,
    HolderDefinitionId,
    ToolAssemblyId,
    MachineDefinitionId,
    GeometryReferenceId,
    FixtureInstanceId,
    CamSurfaceSelectionId,
    MachiningZone3DId,
    Cam3DGeometrySnapshotId,
    Cam3DCalculationContextId,
    ToolpathArtifactId,
    ToolpathEventId,
    SimulationRequestId,
    SimulationResultId,
    PostProcessorDefinitionId,
    ProductionControllerProfileId,
    PostRequestId,
    PostResultId,
    NCProgramId,
    ProgramAssemblyRequestId,
    ProgramAssemblyResultId,
    ProgramOperationSectionId,
    NCExportRequestId,
    NCExportResultId,
    NCArtifactId,
)
