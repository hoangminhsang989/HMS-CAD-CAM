"""Typed Stage 12 foundation and Stage 9A.9 workspace readiness."""

from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.cam.lathe.application import LatheOperationService, LatheServiceSession
from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolver
from hms_cadcam.cam.lathe.strategies import LATHE_STRATEGY_REGISTRY
from hms_cadcam.cam.lathe.types import (
    LatheStage9A9State,
    LatheWorkspaceReadinessReason,
    LatheWorkspaceReadinessState,
)


@dataclass(frozen=True, slots=True)
class LatheWorkspaceReadiness:
    """Immutable workspace gate, distinct from operation readiness."""

    state: LatheWorkspaceReadinessState
    reason: LatheWorkspaceReadinessReason
    foundation_available: bool
    presenter_active: bool
    stage_9a9: LatheStage9A9State

    def __post_init__(self) -> None:
        if not isinstance(self.state, LatheWorkspaceReadinessState):
            raise TypeError("Lathe workspace readiness state is invalid")
        if not isinstance(self.reason, LatheWorkspaceReadinessReason):
            raise TypeError("Lathe workspace readiness reason is invalid")
        if type(self.foundation_available) is not bool or type(
            self.presenter_active
        ) is not bool:
            raise TypeError("Lathe workspace readiness flags must be bool")
        if not isinstance(self.stage_9a9, LatheStage9A9State):
            raise TypeError("Lathe Stage 9A.9 state is invalid")
        expected = {
            LatheWorkspaceReadinessState.FOUNDATION_UNAVAILABLE: (
                False,
                False,
                LatheWorkspaceReadinessReason.FOUNDATION_NOT_READY,
                LatheStage9A9State.BLOCKED,
            ),
            LatheWorkspaceReadinessState.FOUNDATION_READY: (
                True,
                False,
                LatheWorkspaceReadinessReason.PRESENTER_NOT_IMPLEMENTED,
                LatheStage9A9State.BLOCKED,
            ),
            LatheWorkspaceReadinessState.PRESENTER_IMPLEMENTATION_ALLOWED: (
                True,
                False,
                LatheWorkspaceReadinessReason.PRESENTER_NOT_IMPLEMENTED,
                LatheStage9A9State.UNBLOCKED_FOR_IMPLEMENTATION,
            ),
            LatheWorkspaceReadinessState.PRESENTER_ACTIVE: (
                True,
                True,
                LatheWorkspaceReadinessReason.NONE,
                LatheStage9A9State.COMPLETE,
            ),
        }[self.state]
        actual = (
            self.foundation_available,
            self.presenter_active,
            self.reason,
            self.stage_9a9,
        )
        if actual != expected:
            raise ValueError("Lathe workspace readiness fields are inconsistent")

    @classmethod
    def unavailable(cls) -> "LatheWorkspaceReadiness":
        return cls(
            LatheWorkspaceReadinessState.FOUNDATION_UNAVAILABLE,
            LatheWorkspaceReadinessReason.FOUNDATION_NOT_READY,
            False,
            False,
            LatheStage9A9State.BLOCKED,
        )

    @classmethod
    def foundation_ready(cls) -> "LatheWorkspaceReadiness":
        return cls(
            LatheWorkspaceReadinessState.FOUNDATION_READY,
            LatheWorkspaceReadinessReason.PRESENTER_NOT_IMPLEMENTED,
            True,
            False,
            LatheStage9A9State.BLOCKED,
        )

    @classmethod
    def implementation_allowed(cls) -> "LatheWorkspaceReadiness":
        return cls(
            LatheWorkspaceReadinessState.PRESENTER_IMPLEMENTATION_ALLOWED,
            LatheWorkspaceReadinessReason.PRESENTER_NOT_IMPLEMENTED,
            True,
            False,
            LatheStage9A9State.UNBLOCKED_FOR_IMPLEMENTATION,
        )


@dataclass(frozen=True, slots=True)
class LatheFoundationProvider:
    """Explicit service provider proving successful foundation construction."""

    service: LatheOperationService

    def __post_init__(self) -> None:
        if not isinstance(self.service, LatheOperationService):
            raise TypeError("Lathe foundation provider service is invalid")
        if len(LATHE_STRATEGY_REGISTRY) != 11:
            raise RuntimeError("Lathe foundation strategy registry is incomplete")

    @property
    def readiness(self) -> LatheWorkspaceReadiness:
        return LatheWorkspaceReadiness.implementation_allowed()


def create_lathe_foundation_provider(
    session: LatheServiceSession,
    *,
    capability_resolver: LatheToolCapabilityResolver | None = None,
) -> LatheFoundationProvider:
    """Construct the runtime foundation without creating any presenter or UI."""

    return LatheFoundationProvider(
        LatheOperationService(
            session, capability_resolver=capability_resolver
        )
    )


STAGE12_LATHE_WORKSPACE_READINESS = LatheWorkspaceReadiness.implementation_allowed()


__all__ = [
    "LatheFoundationProvider",
    "LatheWorkspaceReadiness",
    "STAGE12_LATHE_WORKSPACE_READINESS",
    "create_lathe_foundation_provider",
]
