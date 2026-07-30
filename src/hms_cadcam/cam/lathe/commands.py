"""Exact immutable mutation/query commands for Lathe Foundation V1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding, LatheOwnershipKey
from hms_cadcam.cam.lathe.parameters import (
    LatheParameterState,
    LatheParameterUpdate,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId


def _command_common(
    ownership: object, expected_revision: object
) -> tuple[LatheOwnershipKey, Revision]:
    if not isinstance(ownership, LatheOwnershipKey):
        raise TypeError("Lathe command ownership must be LatheOwnershipKey")
    if not isinstance(expected_revision, Revision):
        raise TypeError("Lathe command expected_revision must be Revision")
    return ownership, expected_revision


@dataclass(frozen=True, slots=True)
class CreateLatheOperation:
    ownership: LatheOwnershipKey
    strategy_id: LatheStrategyId
    parameter_state: LatheParameterState
    expected_revision: Revision = Revision(0)
    enabled: bool = True

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if self.expected_revision != Revision(0):
            raise ValueError("Lathe create expected_revision must be zero")
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe create strategy_id is invalid")
        if not isinstance(self.parameter_state, LatheParameterState) or (
            self.parameter_state.strategy_id is not self.strategy_id
        ):
            raise ValueError("Lathe create parameter state must match strategy")
        if type(self.enabled) is not bool:
            raise TypeError("Lathe create enabled flag must be bool")


@dataclass(frozen=True, slots=True)
class UpdateLatheParameters:
    ownership: LatheOwnershipKey
    updates: tuple[LatheParameterUpdate, ...]
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if not isinstance(self.updates, tuple) or not self.updates or any(
            not isinstance(item, LatheParameterUpdate) for item in self.updates
        ):
            raise TypeError("Lathe parameter command requires typed updates")
        if len({item.parameter_id for item in self.updates}) != len(self.updates):
            raise ValueError("Lathe parameter update IDs must be unique")


@dataclass(frozen=True, slots=True)
class ChangeLatheStrategy:
    ownership: LatheOwnershipKey
    strategy_id: LatheStrategyId
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe strategy command strategy_id is invalid")


@dataclass(frozen=True, slots=True)
class BindLatheGeometry:
    ownership: LatheOwnershipKey
    binding: LatheGeometryBinding
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if not isinstance(self.binding, LatheGeometryBinding):
            raise TypeError("Lathe geometry command binding is invalid")


@dataclass(frozen=True, slots=True)
class ClearLatheGeometry:
    ownership: LatheOwnershipKey
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)


@dataclass(frozen=True, slots=True)
class BindLatheTool:
    ownership: LatheOwnershipKey
    reference: LatheToolReference
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if not isinstance(self.reference, LatheToolReference):
            raise TypeError("Lathe tool command reference is invalid")


@dataclass(frozen=True, slots=True)
class ClearLatheTool:
    ownership: LatheOwnershipKey
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)


@dataclass(frozen=True, slots=True)
class SetLatheOperationEnabled:
    ownership: LatheOwnershipKey
    enabled: bool
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)
        if type(self.enabled) is not bool:
            raise TypeError("Lathe operation enabled flag must be bool")


@dataclass(frozen=True, slots=True)
class DeleteLatheOperation:
    ownership: LatheOwnershipKey
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)


@dataclass(frozen=True, slots=True)
class ValidateLatheOperation:
    ownership: LatheOwnershipKey
    expected_revision: Revision

    def __post_init__(self) -> None:
        _command_common(self.ownership, self.expected_revision)


LatheCommand: TypeAlias = (
    CreateLatheOperation
    | UpdateLatheParameters
    | ChangeLatheStrategy
    | BindLatheGeometry
    | ClearLatheGeometry
    | BindLatheTool
    | ClearLatheTool
    | SetLatheOperationEnabled
    | DeleteLatheOperation
    | ValidateLatheOperation
)


__all__ = [
    "BindLatheGeometry",
    "BindLatheTool",
    "ChangeLatheStrategy",
    "ClearLatheGeometry",
    "ClearLatheTool",
    "CreateLatheOperation",
    "DeleteLatheOperation",
    "LatheCommand",
    "SetLatheOperationEnabled",
    "UpdateLatheParameters",
    "ValidateLatheOperation",
]
