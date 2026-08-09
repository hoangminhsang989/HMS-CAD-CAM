"""Production Tool Library requests, projections, and schema-v5 capabilities.

This module is persistence-neutral.  Stable identities are deliberately absent
from :class:`ToolDefinitionDraft`; ``CamApplicationService`` is the authority
that supplies them when a command is committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    BallEndGeometry,
    BoringBarGeometry,
    BullNoseGeometry,
    ChamferGeometry,
    CustomCuttingGeometry,
    CylindricalGeometry,
    DEFAULT_TOOL_PROFILE_REGISTRY,
    DrillGeometry,
    HolderDefinition,
    HolderDefinitionId,
    Length,
    LengthUnit,
    Revision,
    ShankGeometry,
    TapGeometry,
    ToolAssembly,
    ToolAssemblyId,
    ToolCommonDefaults,
    ToolCoolantCapability,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    ToolProgramProfile,
    TurningInsertGeometry,
)
from hms_cadcam.cam.persistence.models import CamProjectSnapshot


ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA = "ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA"


class ToolLibrarySort(StrEnum):
    """Deterministic supported list orders."""

    NAME = "name"
    FAMILY = "family"
    PRINCIPAL_SIZE = "principal_size"
    CONFIGURATION_REVISION = "configuration_revision"
    USAGE = "usage"


@dataclass(frozen=True, slots=True)
class ToolDefinitionDraft:
    """Validated, identity-free authoring data for the actual Tool domain.

    ``detail_size`` and ``detail_angle_degrees`` map only to real variant
    fields (corner radius, pitch, maximum bore, tip diameter, nose radius, or
    drill/chamfer angle).  They are never persisted as invented generic data.
    """

    name: str
    family: ToolFamily
    unit: LengthUnit
    principal_size: float
    cutting_length: float
    overall_length: float
    usable_length: float
    shank_diameter: float
    shank_length: float
    detail_size: float | None = None
    detail_angle_degrees: float | None = None
    detail_text: str | None = None
    hand: ToolHand = ToolHand.RIGHT
    coolant_capabilities: tuple[ToolCoolantCapability, ...] = ()
    manufacturer: str | None = None
    model: str | None = None
    common_defaults: ToolCommonDefaults = ToolCommonDefaults()
    create_assembly: bool = True
    assembly_name: str | None = None
    stickout: float | None = None
    gauge_length: float | None = None
    holder_id: HolderDefinitionId | None = None

    def build_tool(
        self,
        tool_id: ToolDefinitionId,
        *,
        revision: Revision = Revision(0),
        configuration_revision: Revision = Revision(0),
        common_defaults: ToolCommonDefaults | None = None,
        program_profiles: tuple[ToolProgramProfile, ...] = (),
    ) -> ToolDefinition:
        """Build one domain Tool after the service has supplied its identity."""
        if not isinstance(tool_id, ToolDefinitionId):
            raise TypeError("Tool identity must be supplied by the application service")
        unit = self.unit
        length = lambda value: Length(float(value), unit)
        principal = length(self.principal_size)
        cutting = length(self.cutting_length)
        detail = None if self.detail_size is None else length(self.detail_size)
        family = self.family
        if family in {
            ToolFamily.END_MILL,
            ToolFamily.FACE_MILL,
            ToolFamily.REAMER,
        }:
            geometry = CylindricalGeometry(principal, cutting)
        elif family is ToolFamily.BALL_END_MILL:
            geometry = BallEndGeometry(principal, cutting)
        elif family is ToolFamily.BULL_NOSE_END_MILL:
            geometry = BullNoseGeometry(
                principal,
                cutting,
                detail if detail is not None else length(self.principal_size / 10.0),
            )
        elif family in {ToolFamily.DRILL, ToolFamily.CENTER_DRILL}:
            geometry = DrillGeometry(
                principal,
                cutting,
                Angle(float(self.detail_angle_degrees or 118.0), AngleUnit.DEGREE),
            )
        elif family is ToolFamily.CHAMFER_MILL:
            geometry = ChamferGeometry(
                principal,
                cutting,
                Angle(float(self.detail_angle_degrees or 90.0), AngleUnit.DEGREE),
                detail if detail is not None else length(0.0),
            )
        elif family is ToolFamily.TAP:
            geometry = TapGeometry(
                principal,
                cutting,
                detail if detail is not None else length(1.0),
                self.hand,
            )
        elif family is ToolFamily.BORING_BAR:
            geometry = BoringBarGeometry(
                principal,
                detail if detail is not None else principal,
                cutting,
                self.hand,
            )
        elif family is ToolFamily.TURNING_INSERT:
            geometry = TurningInsertGeometry(
                principal,
                cutting,
                detail if detail is not None else length(self.principal_size / 10.0),
            )
        elif family is ToolFamily.CUSTOM:
            geometry = CustomCuttingGeometry(
                principal,
                cutting,
                (self.detail_text or "Custom cutting envelope").strip(),
            )
        else:  # pragma: no cover - ToolFamily is closed, retain fail-closed behavior.
            raise ValueError(f"Unsupported Tool family: {family}")
        return ToolDefinition(
            tool_id=tool_id,
            name=self.name,
            family=family,
            unit=unit,
            cutting_geometry=geometry,
            overall_length=length(self.overall_length),
            usable_length=length(self.usable_length),
            shank=ShankGeometry(
                length(self.shank_diameter), length(self.shank_length)
            ),
            revision=revision,
            coolant_capabilities=self.coolant_capabilities,
            manufacturer=self.manufacturer,
            model=self.model,
            common_defaults=(
                self.common_defaults if common_defaults is None else common_defaults
            ),
            program_profiles=program_profiles,
            configuration_revision=configuration_revision,
        )

    def build_assembly(
        self,
        assembly_id: ToolAssemblyId,
        tool: ToolDefinition,
        *,
        holder: HolderDefinition | None = None,
    ) -> ToolAssembly:
        """Build the optional existing-Tool selection identity."""
        if not self.create_assembly:
            raise ValueError("This Tool draft does not request an assembly")
        stickout = float(
            self.stickout if self.stickout is not None else self.usable_length
        )
        gauge = float(
            self.gauge_length
            if self.gauge_length is not None
            else max(stickout, self.overall_length)
        )
        return ToolAssembly.create(
            assembly_id,
            self.assembly_name or f"{self.name} — Assembly",
            tool,
            Length(stickout, self.unit),
            Length(gauge, self.unit),
            holder,
        )

    @classmethod
    def from_tool(cls, tool: ToolDefinition) -> "ToolDefinitionDraft":
        """Project every supported concrete geometry back into authoring fields."""
        geometry = tool.cutting_geometry
        principal = principal_size(tool)
        cutting = geometry.axial_cutting_length.value
        detail_size: float | None = None
        detail_angle: float | None = None
        detail_text: str | None = None
        hand = ToolHand.RIGHT
        if isinstance(geometry, BullNoseGeometry):
            detail_size = geometry.corner_radius.value
        elif isinstance(geometry, DrillGeometry):
            detail_angle = geometry.point_angle.to(AngleUnit.DEGREE).value
        elif isinstance(geometry, ChamferGeometry):
            detail_size = geometry.tip_diameter.value
            detail_angle = geometry.included_angle.to(AngleUnit.DEGREE).value
        elif isinstance(geometry, TapGeometry):
            detail_size = geometry.pitch.value
            hand = geometry.hand
        elif isinstance(geometry, BoringBarGeometry):
            detail_size = geometry.maximum_bore_diameter.value
            hand = geometry.hand
        elif isinstance(geometry, TurningInsertGeometry):
            detail_size = geometry.nose_radius.value
        elif isinstance(geometry, CustomCuttingGeometry):
            detail_text = geometry.description
        return cls(
            name=tool.name,
            family=tool.family,
            unit=tool.unit,
            principal_size=principal,
            cutting_length=cutting,
            overall_length=tool.overall_length.value,
            usable_length=tool.usable_length.value,
            shank_diameter=tool.shank.diameter.value,
            shank_length=tool.shank.length.value,
            detail_size=detail_size,
            detail_angle_degrees=detail_angle,
            detail_text=detail_text,
            hand=hand,
            coolant_capabilities=tool.coolant_capabilities,
            manufacturer=tool.manufacturer,
            model=tool.model,
            common_defaults=tool.common_defaults,
            create_assembly=False,
        )


@dataclass(frozen=True, slots=True)
class ToolUsageLocation:
    """Bounded high-level reference location for one persisted Operation."""

    job_name: str
    setup_name: str
    operation_id: str
    strategy_id: str


@dataclass(frozen=True, slots=True)
class ToolLibraryRecord:
    """Immutable list/detail projection; row position is never identity."""

    tool: ToolDefinition
    principal_size: float
    assembly_ids: tuple[ToolAssemblyId, ...]
    assembly_names: tuple[str, ...]
    holder_texts: tuple[str, ...]
    compatible_strategy_ids: tuple[str, ...]
    usages: tuple[ToolUsageLocation, ...]

    @property
    def referenced(self) -> bool:
        return bool(self.assembly_ids or self.usages)


def principal_size(tool: ToolDefinition) -> float:
    """Return the real principal diameter/envelope field for list/search UX."""
    geometry = tool.cutting_geometry
    for field in (
        "diameter",
        "maximum_diameter",
        "nominal_diameter",
        "minimum_bore_diameter",
        "inscribed_circle",
    ):
        value = getattr(geometry, field, None)
        if isinstance(value, Length):
            return value.value
    raise ValueError(f"Tool geometry has no principal size: {type(geometry).__name__}")


def tool_library_records(
    snapshot: CamProjectSnapshot,
    *,
    query: str = "",
    family: ToolFamily | None = None,
    compatible_strategy_id: str | None = None,
    sort: ToolLibrarySort = ToolLibrarySort.NAME,
    descending: bool = False,
) -> tuple[ToolLibraryRecord, ...]:
    """Search/filter/sort a bounded immutable snapshot without mutating it."""
    if not isinstance(snapshot, CamProjectSnapshot):
        raise TypeError("Tool Library requires a CAM project snapshot")
    needle = query.strip().casefold()
    holders = {item.holder_id: item for item in snapshot.holder_definitions}
    records: list[ToolLibraryRecord] = []
    for tool in snapshot.tool_definitions:
        if family is not None and tool.family is not family:
            continue
        assemblies = tuple(
            item for item in snapshot.tool_assemblies if item.tool_id == tool.tool_id
        )
        compatible = tuple(
            schema.strategy_id
            for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
            if tool.family.value in schema.supported_tool_families
        )
        if (
            compatible_strategy_id is not None
            and compatible_strategy_id not in compatible
        ):
            continue
        assembly_ids = tuple(item.assembly_id for item in assemblies)
        assembly_id_set = set(assembly_ids)
        usages = tuple(
            ToolUsageLocation(
                job.name,
                setup.name,
                str(operation.operation_id),
                operation.strategy_key,
            )
            for job in snapshot.jobs
            for setup in job.setups
            for operation in setup.operation_tree.operations
            if operation.tool_assembly.assembly_id in assembly_id_set
        )
        holder_texts = tuple(
            (
                holders[assembly.holder_id].name
                if assembly.holder_id in holders
                else str(assembly.holder_id)
                if assembly.holder_id is not None
                else ""
            )
            for assembly in assemblies
        )
        record = ToolLibraryRecord(
            tool,
            principal_size(tool),
            assembly_ids,
            tuple(item.name for item in assemblies),
            holder_texts,
            compatible,
            usages,
        )
        haystack = " ".join(
            (
                tool.name,
                str(tool.tool_id),
                tool.family.value,
                f"{record.principal_size:g}",
                *record.assembly_names,
                *(str(item) for item in record.assembly_ids),
                *record.holder_texts,
            )
        ).casefold()
        if not needle or needle in haystack:
            records.append(record)
    key = {
        ToolLibrarySort.NAME: lambda item: (
            item.tool.name.casefold(),
            str(item.tool.tool_id),
        ),
        ToolLibrarySort.FAMILY: lambda item: (
            item.tool.family.value,
            item.tool.name.casefold(),
            str(item.tool.tool_id),
        ),
        ToolLibrarySort.PRINCIPAL_SIZE: lambda item: (
            item.principal_size,
            item.tool.name.casefold(),
            str(item.tool.tool_id),
        ),
        ToolLibrarySort.CONFIGURATION_REVISION: lambda item: (
            item.tool.configuration_revision.value,
            item.tool.name.casefold(),
            str(item.tool.tool_id),
        ),
        ToolLibrarySort.USAGE: lambda item: (
            len(item.usages),
            len(item.assembly_ids),
            item.tool.name.casefold(),
            str(item.tool.tool_id),
        ),
    }[ToolLibrarySort(sort)]
    return tuple(sorted(records, key=key, reverse=bool(descending)))


__all__ = [
    "ARCHIVE_UNAVAILABLE_WITH_CURRENT_SCHEMA",
    "ToolDefinitionDraft",
    "ToolLibraryRecord",
    "ToolLibrarySort",
    "ToolUsageLocation",
    "principal_size",
    "tool_library_records",
]
