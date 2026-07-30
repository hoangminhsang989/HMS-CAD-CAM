"""Injected, fail-closed Lathe Tool capability resolution boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from hms_cadcam.cam.domain.ids import (
    ToolAssemblyId,
    ToolDefinitionId,
    ToolProgramProfileId,
)
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.types import LatheToolCapability


@dataclass(frozen=True, slots=True)
class LatheToolReference:
    """Canonical Tool/Profile/Assembly identities requested by a command."""

    tool_id: ToolDefinitionId
    profile_id: ToolProgramProfileId | None
    assembly_id: ToolAssemblyId

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, ToolDefinitionId):
            raise TypeError("Lathe tool_id must be ToolDefinitionId")
        if self.profile_id is not None and not isinstance(
            self.profile_id, ToolProgramProfileId
        ):
            raise TypeError("Lathe profile_id must be ToolProgramProfileId or None")
        if not isinstance(self.assembly_id, ToolAssemblyId):
            raise TypeError("Lathe assembly_id must be ToolAssemblyId")


@dataclass(frozen=True, slots=True)
class LatheToolCapabilityResolution:
    """Immutable resolver evidence; no display-name inference is possible."""

    reference: LatheToolReference
    exists: bool
    current: bool
    capabilities: frozenset[LatheToolCapability] = frozenset()
    tool_revision: Revision | None = None
    profile_revision: Revision | None = None
    assembly_revision: Revision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, LatheToolReference):
            raise TypeError("Lathe capability resolution reference is invalid")
        if type(self.exists) is not bool or type(self.current) is not bool:
            raise TypeError("Lathe capability resolution flags must be bool")
        if not isinstance(self.capabilities, frozenset) or any(
            not isinstance(item, LatheToolCapability) for item in self.capabilities
        ):
            raise TypeError("Resolved Lathe capabilities must be a typed frozenset")
        if not self.exists and (
            self.current
            or self.capabilities
            or any(
                item is not None
                for item in (
                    self.tool_revision,
                    self.profile_revision,
                    self.assembly_revision,
                )
            )
        ):
            raise ValueError("Missing Lathe tool resolution cannot carry evidence")
        if self.exists and (
            not isinstance(self.tool_revision, Revision)
            or not isinstance(self.assembly_revision, Revision)
        ):
            raise ValueError("Existing Lathe tool resolution needs Tool/Assembly revisions")
        if self.reference.profile_id is None and self.profile_revision is not None:
            raise ValueError("Profile revision requires a profile identity")
        if self.reference.profile_id is not None and self.exists and not isinstance(
            self.profile_revision, Revision
        ):
            raise ValueError("Existing profile reference requires a profile revision")

    @classmethod
    def missing(
        cls, reference: LatheToolReference
    ) -> "LatheToolCapabilityResolution":
        return cls(reference, False, False)


class LatheToolCapabilityResolver(Protocol):
    """Persistence-neutral resolver supplied by the canonical Tool registry."""

    def resolve(
        self, reference: LatheToolReference
    ) -> LatheToolCapabilityResolution:
        """Resolve exact typed references without inspecting display names."""
        ...


class FailClosedLatheToolCapabilityResolver:
    """Production-safe default until a canonical registry adapter is injected."""

    def resolve(
        self, reference: LatheToolReference
    ) -> LatheToolCapabilityResolution:
        if not isinstance(reference, LatheToolReference):
            raise TypeError("Lathe capability reference is invalid")
        return LatheToolCapabilityResolution.missing(reference)


class StaticLatheToolCapabilityResolver:
    """Explicit immutable registry useful for composition and production tests."""

    def __init__(
        self, resolutions: tuple[LatheToolCapabilityResolution, ...]
    ) -> None:
        if not isinstance(resolutions, tuple) or any(
            not isinstance(item, LatheToolCapabilityResolution)
            for item in resolutions
        ):
            raise TypeError("Lathe capability resolutions must be a typed tuple")
        if len({item.reference for item in resolutions}) != len(resolutions):
            raise ValueError("Lathe capability references must be unique")
        self._resolutions: Mapping[
            LatheToolReference, LatheToolCapabilityResolution
        ] = MappingProxyType({item.reference: item for item in resolutions})

    def resolve(
        self, reference: LatheToolReference
    ) -> LatheToolCapabilityResolution:
        if not isinstance(reference, LatheToolReference):
            raise TypeError("Lathe capability reference is invalid")
        return self._resolutions.get(
            reference, LatheToolCapabilityResolution.missing(reference)
        )


__all__ = [
    "FailClosedLatheToolCapabilityResolver",
    "LatheToolCapabilityResolution",
    "LatheToolCapabilityResolver",
    "LatheToolReference",
    "StaticLatheToolCapabilityResolver",
]
