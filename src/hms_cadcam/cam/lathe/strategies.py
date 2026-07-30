"""Deterministic metadata-only Lathe Foundation V1 strategy registry."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.cam.lathe.parameters import (
    LatheParameterDescriptor,
    lathe_parameter_schema,
)
from hms_cadcam.cam.lathe.types import (
    LatheGeometryKind,
    LatheStrategyFamily,
    LatheStrategyId,
    LatheToolCapability,
)


@dataclass(frozen=True, slots=True)
class LatheStrategyDefinition:
    """One immutable strategy metadata record; never an executable algorithm."""

    strategy_id: LatheStrategyId
    family_id: LatheStrategyFamily
    allowed_geometry_kinds: tuple[LatheGeometryKind, ...]
    required_tool_capabilities: frozenset[LatheToolCapability]
    parameter_descriptors: tuple[LatheParameterDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe strategy ID is invalid")
        if not isinstance(self.family_id, LatheStrategyFamily):
            raise TypeError("Lathe strategy family is invalid")
        if not isinstance(self.allowed_geometry_kinds, tuple) or any(
            not isinstance(item, LatheGeometryKind)
            for item in self.allowed_geometry_kinds
        ):
            raise TypeError("Lathe allowed geometry kinds must be a typed tuple")
        if not self.allowed_geometry_kinds or len(set(self.allowed_geometry_kinds)) != len(
            self.allowed_geometry_kinds
        ):
            raise ValueError("Lathe allowed geometry kinds must be non-empty and unique")
        if not isinstance(self.required_tool_capabilities, frozenset) or any(
            not isinstance(item, LatheToolCapability)
            for item in self.required_tool_capabilities
        ):
            raise TypeError("Lathe tool capabilities must be a typed frozenset")
        if len(self.required_tool_capabilities) != 1:
            raise ValueError("Each Lathe V1 strategy requires exactly one capability")
        if not isinstance(self.parameter_descriptors, tuple) or any(
            not isinstance(item, LatheParameterDescriptor)
            for item in self.parameter_descriptors
        ):
            raise TypeError("Lathe parameter descriptors must be a typed tuple")


_FAMILY: Mapping[LatheStrategyId, LatheStrategyFamily] = MappingProxyType(
    {
        LatheStrategyId.FACE: LatheStrategyFamily.TURNING,
        LatheStrategyId.OD_ROUGH: LatheStrategyFamily.TURNING,
        LatheStrategyId.OD_FINISH: LatheStrategyFamily.TURNING,
        LatheStrategyId.ID_ROUGH: LatheStrategyFamily.TURNING,
        LatheStrategyId.ID_FINISH: LatheStrategyFamily.TURNING,
        LatheStrategyId.OD_GROOVE: LatheStrategyFamily.GROOVING,
        LatheStrategyId.ID_GROOVE: LatheStrategyFamily.GROOVING,
        LatheStrategyId.PART_OFF: LatheStrategyFamily.GROOVING,
        LatheStrategyId.OD_THREAD: LatheStrategyFamily.THREADING,
        LatheStrategyId.ID_THREAD: LatheStrategyFamily.THREADING,
        LatheStrategyId.AXIAL_DRILL: LatheStrategyFamily.HOLE_MAKING,
    }
)

_GEOMETRY: Mapping[LatheStrategyId, tuple[LatheGeometryKind, ...]] = MappingProxyType(
    {
        LatheStrategyId.FACE: (
            LatheGeometryKind.FACE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.PROFILE,
        ),
        LatheStrategyId.OD_ROUGH: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.CYLINDER,
        ),
        LatheStrategyId.OD_FINISH: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.CYLINDER,
        ),
        LatheStrategyId.ID_ROUGH: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.CYLINDER,
        ),
        LatheStrategyId.ID_FINISH: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.CYLINDER,
        ),
        LatheStrategyId.OD_GROOVE: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.FACE,
        ),
        LatheStrategyId.ID_GROOVE: (
            LatheGeometryKind.PROFILE,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.FACE,
        ),
        LatheStrategyId.PART_OFF: (
            LatheGeometryKind.EDGE,
            LatheGeometryKind.FACE,
            LatheGeometryKind.PROFILE,
        ),
        LatheStrategyId.OD_THREAD: (
            LatheGeometryKind.CYLINDER,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.PROFILE,
        ),
        LatheStrategyId.ID_THREAD: (
            LatheGeometryKind.CYLINDER,
            LatheGeometryKind.EDGE,
            LatheGeometryKind.PROFILE,
        ),
        LatheStrategyId.AXIAL_DRILL: (
            LatheGeometryKind.POINT,
            LatheGeometryKind.AXIS,
            LatheGeometryKind.CYLINDER,
        ),
    }
)

_CAPABILITY: Mapping[LatheStrategyId, LatheToolCapability] = MappingProxyType(
    {
        LatheStrategyId.FACE: LatheToolCapability.FACE_TURNING,
        LatheStrategyId.OD_ROUGH: LatheToolCapability.OD_TURNING,
        LatheStrategyId.OD_FINISH: LatheToolCapability.OD_TURNING,
        LatheStrategyId.ID_ROUGH: LatheToolCapability.ID_TURNING,
        LatheStrategyId.ID_FINISH: LatheToolCapability.ID_TURNING,
        LatheStrategyId.OD_GROOVE: LatheToolCapability.OD_GROOVING,
        LatheStrategyId.ID_GROOVE: LatheToolCapability.ID_GROOVING,
        LatheStrategyId.PART_OFF: LatheToolCapability.PARTING,
        LatheStrategyId.OD_THREAD: LatheToolCapability.OD_THREADING,
        LatheStrategyId.ID_THREAD: LatheToolCapability.ID_THREADING,
        LatheStrategyId.AXIAL_DRILL: LatheToolCapability.AXIAL_DRILLING,
    }
)

LATHE_STRATEGY_REGISTRY: tuple[LatheStrategyDefinition, ...] = tuple(
    LatheStrategyDefinition(
        strategy_id=strategy_id,
        family_id=_FAMILY[strategy_id],
        allowed_geometry_kinds=_GEOMETRY[strategy_id],
        required_tool_capabilities=frozenset({_CAPABILITY[strategy_id]}),
        parameter_descriptors=lathe_parameter_schema(strategy_id).descriptors,
    )
    for strategy_id in LatheStrategyId
)

if len(LATHE_STRATEGY_REGISTRY) != 11 or len(
    {item.strategy_id for item in LATHE_STRATEGY_REGISTRY}
) != 11:
    raise RuntimeError("Lathe V1 strategy registry must contain exactly 11 unique entries")

_BY_ID: Mapping[LatheStrategyId, LatheStrategyDefinition] = MappingProxyType(
    {item.strategy_id: item for item in LATHE_STRATEGY_REGISTRY}
)


def lathe_strategy_definition(
    strategy_id: LatheStrategyId,
) -> LatheStrategyDefinition:
    """Return metadata for one exact typed strategy."""

    if not isinstance(strategy_id, LatheStrategyId):
        raise TypeError("strategy_id must be LatheStrategyId")
    return _BY_ID[strategy_id]


__all__ = [
    "LATHE_STRATEGY_REGISTRY",
    "LatheStrategyDefinition",
    "lathe_strategy_definition",
]
