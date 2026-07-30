"""Fail-closed Tool and CAD-selection adapters for the Stage 9A.9 Lathe UI."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.tool_profiles import ToolProfileValidationState
from hms_cadcam.cam.domain.tooling import (
    HolderDefinition,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolFamily,
    assess_tool_assembly,
)
from hms_cadcam.cam.lathe.capabilities import (
    LatheToolCapabilityResolution,
    LatheToolCapabilityResolver,
    LatheToolReference,
)
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.types import (
    LatheGeometryKind,
    LatheStrategyId,
    LatheToolCapability,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode


class LatheGeometrySelectionError(ValueError):
    """The current native-free viewer selection cannot be bound exactly."""


@dataclass(frozen=True, slots=True)
class LatheSelectionContext:
    """Live selection facts guarded by document, source and generation."""

    document_id: CadDocumentId
    source_id: UUID
    generation: int
    selections: tuple[SelectionMetadata, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("Lathe selection document_id is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("Lathe selection source_id must be a non-nil UUID")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("Lathe selection generation is invalid")
        if not isinstance(self.selections, tuple) or any(
            not isinstance(item, SelectionMetadata) for item in self.selections
        ):
            raise TypeError("Lathe selections must be an immutable typed tuple")


_SELECTION_KIND = MappingProxyType(
    {
        SelectionMode.FACE: LatheGeometryKind.FACE,
        SelectionMode.EDGE: LatheGeometryKind.EDGE,
        SelectionMode.WIRE: LatheGeometryKind.PROFILE,
        SelectionMode.VERTEX: LatheGeometryKind.POINT,
    }
)


def lathe_geometry_from_selection(
    context: LatheSelectionContext,
    strategy_id: LatheStrategyId,
    *,
    expected_document_id: CadDocumentId,
    expected_source_id: UUID,
    expected_generation: int,
) -> LatheGeometryBinding:
    """Build one exact OCP-free binding without guessing surface semantics."""

    if not isinstance(context, LatheSelectionContext):
        raise TypeError("context must be LatheSelectionContext")
    if not isinstance(strategy_id, LatheStrategyId):
        raise TypeError("strategy_id must be LatheStrategyId")
    if (
        context.document_id != expected_document_id
        or context.source_id != expected_source_id
        or context.generation != expected_generation
    ):
        raise LatheGeometrySelectionError("lathe.geometry.selection_stale")
    if not context.selections:
        raise LatheGeometrySelectionError("lathe.geometry.selection_empty")
    if any(item.document_id != context.document_id for item in context.selections):
        raise LatheGeometrySelectionError("lathe.geometry.selection_stale")
    entity_ids = tuple(item.selection_id.strip() for item in context.selections)
    if any(not item for item in entity_ids):
        raise LatheGeometrySelectionError("lathe.geometry.selection_empty")
    if len(set(entity_ids)) != len(entity_ids):
        raise LatheGeometrySelectionError("lathe.geometry.selection_duplicate")
    try:
        kinds = tuple(_SELECTION_KIND[item.topology] for item in context.selections)
    except KeyError as error:
        raise LatheGeometrySelectionError(
            "lathe.geometry.selection_kind_unavailable"
        ) from error
    if len(set(kinds)) != 1:
        raise LatheGeometrySelectionError("lathe.geometry.selection_mixed")
    kind = kinds[0]
    if kind not in lathe_strategy_definition(strategy_id).allowed_geometry_kinds:
        raise LatheGeometrySelectionError("lathe.geometry.selection_incompatible")
    return LatheGeometryBinding(
        kind,
        entity_ids,
        context.source_id,
        context.generation,
    )


@dataclass(frozen=True, slots=True)
class LatheToolChoice:
    """One canonical Tool/Profile/Assembly choice rendered by the Qt UI."""

    reference: LatheToolReference
    display_name: str
    capabilities: frozenset[LatheToolCapability]
    current: bool

    def __post_init__(self) -> None:
        if not isinstance(self.reference, LatheToolReference):
            raise TypeError("Lathe Tool choice reference is invalid")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("Lathe Tool choice display name is empty")
        if not isinstance(self.capabilities, frozenset) or any(
            not isinstance(item, LatheToolCapability) for item in self.capabilities
        ):
            raise TypeError("Lathe Tool choice capabilities are invalid")
        if type(self.current) is not bool:
            raise TypeError("Lathe Tool choice current flag is invalid")

    def supports(self, capability: LatheToolCapability) -> bool:
        """Return whether current typed evidence proves one exact capability."""

        return self.current and capability in self.capabilities


class ProjectLatheToolCatalog(LatheToolCapabilityResolver):
    """Resolve current canonical project tooling without a second Tool database."""

    def __init__(
        self,
        tools: tuple[ToolDefinition, ...] = (),
        holders: tuple[HolderDefinition, ...] = (),
        assemblies: tuple[ToolAssembly, ...] = (),
        *,
        explicit_capabilities: Mapping[
            LatheToolReference, frozenset[LatheToolCapability]
        ] | None = None,
    ) -> None:
        self._explicit_capabilities = MappingProxyType(
            dict(explicit_capabilities or {})
        )
        if any(
            not isinstance(reference, LatheToolReference)
            or not isinstance(capabilities, frozenset)
            or any(
                not isinstance(item, LatheToolCapability)
                for item in capabilities
            )
            for reference, capabilities in self._explicit_capabilities.items()
        ):
            raise TypeError("Explicit Lathe Tool capabilities are invalid")
        self.replace_snapshot(tools, holders, assemblies)

    def replace_snapshot(
        self,
        tools: tuple[ToolDefinition, ...],
        holders: tuple[HolderDefinition, ...],
        assemblies: tuple[ToolAssembly, ...],
    ) -> None:
        """Replace only immutable project resource snapshots."""

        if not isinstance(tools, tuple) or any(
            not isinstance(item, ToolDefinition) for item in tools
        ):
            raise TypeError("Lathe Tool catalog tools are invalid")
        if not isinstance(holders, tuple) or any(
            not isinstance(item, HolderDefinition) for item in holders
        ):
            raise TypeError("Lathe Tool catalog holders are invalid")
        if not isinstance(assemblies, tuple) or any(
            not isinstance(item, ToolAssembly) for item in assemblies
        ):
            raise TypeError("Lathe Tool catalog assemblies are invalid")
        self._tools = {item.tool_id: item for item in tools}
        self._holders = {item.holder_id: item for item in holders}
        self._assemblies = {item.assembly_id: item for item in assemblies}

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        """Return the current immutable Tool snapshots in source order."""

        return tuple(self._tools.values())

    @property
    def holders(self) -> tuple[HolderDefinition, ...]:
        """Return the current immutable Holder snapshots in source order."""

        return tuple(self._holders.values())

    @property
    def assemblies(self) -> tuple[ToolAssembly, ...]:
        """Return the current immutable Assembly snapshots in source order."""

        return tuple(self._assemblies.values())

    def choices(self) -> tuple[LatheToolChoice, ...]:
        """List deterministic canonical assembly/profile choices."""

        choices: list[LatheToolChoice] = []
        ordered = sorted(
            self._assemblies.values(),
            key=lambda item: (item.name.casefold(), str(item.assembly_id)),
        )
        for assembly in ordered:
            tool = self._tools.get(assembly.tool_id)
            if tool is None:
                continue
            profiles = (None, *tool.program_profiles)
            for profile in profiles:
                reference = LatheToolReference(
                    tool.tool_id,
                    None if profile is None else profile.profile_id,
                    assembly.assembly_id,
                )
                resolution = self.resolve(reference)
                profile_suffix = (
                    "" if profile is None else f" · {profile.display_name}"
                )
                choices.append(
                    LatheToolChoice(
                        reference,
                        f"{assembly.name} · {tool.name}{profile_suffix}",
                        resolution.capabilities,
                        resolution.current,
                    )
                )
        return tuple(choices)

    def resolve(
        self, reference: LatheToolReference
    ) -> LatheToolCapabilityResolution:
        """Resolve exact current revisions and fail closed on missing evidence."""

        if not isinstance(reference, LatheToolReference):
            raise TypeError("Lathe Tool reference is invalid")
        tool = self._tools.get(reference.tool_id)
        assembly = self._assemblies.get(reference.assembly_id)
        if tool is None or assembly is None or assembly.tool_id != reference.tool_id:
            return LatheToolCapabilityResolution.missing(reference)
        holder = (
            None
            if assembly.holder_id is None
            else self._holders.get(assembly.holder_id)
        )
        evidence = ToolAssemblyEvidence(
            tool_exists=True,
            tool_revision=tool.revision,
            tool_fingerprint=tool.content_fingerprint,
            tool_unit=tool.unit,
            holder_exists=holder is not None,
            holder_revision=None if holder is None else holder.revision,
            holder_fingerprint=(
                None if holder is None else holder.content_fingerprint
            ),
            holder_unit=None if holder is None else holder.unit,
        )
        current = assess_tool_assembly(assembly, evidence) is ToolAssemblyStatus.VALID
        profile = None
        if reference.profile_id is not None:
            profile = next(
                (
                    item
                    for item in tool.program_profiles
                    if item.profile_id == reference.profile_id
                ),
                None,
            )
            if profile is None:
                return LatheToolCapabilityResolution.missing(reference)
            current = current and (
                profile.enabled
                and profile.validation_state
                is ToolProfileValidationState.CONFIGURED
                and profile.source_tool_revision == tool.revision
                and profile.source_tool_fingerprint == tool.content_fingerprint
            )
        capabilities = set(self._explicit_capabilities.get(reference, frozenset()))
        if tool.family in {ToolFamily.DRILL, ToolFamily.CENTER_DRILL}:
            capabilities.add(LatheToolCapability.AXIAL_DRILLING)
        if profile is not None:
            try:
                strategy_id = LatheStrategyId(profile.strategy_id)
            except ValueError:
                strategy_id = None
            if strategy_id is not None:
                capabilities.update(
                    lathe_strategy_definition(strategy_id).required_tool_capabilities
                )
        return LatheToolCapabilityResolution(
            reference,
            True,
            current,
            frozenset(capabilities),
            tool.revision,
            None if profile is None else profile.revision,
            assembly.revision,
        )


__all__ = [
    "LatheGeometrySelectionError",
    "LatheSelectionContext",
    "LatheToolChoice",
    "ProjectLatheToolCatalog",
    "lathe_geometry_from_selection",
]
