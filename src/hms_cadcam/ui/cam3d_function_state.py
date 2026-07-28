"""Typed presentation state for the Stage 9A.8 CAM 3D UI shell.

This module deliberately has no Qt dependency.  It owns only deterministic
state/projection rules; widgets render the returned values and never infer
state from control contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID


class Cam3DUiState(StrEnum):
    """Presentation states visible in the WP1 shell."""

    FEATURE_DISABLED = "feature_disabled"
    EMPTY = "empty"
    READY = "ready"
    READ_ONLY = "read_only"
    SELECTING = "selecting"
    CALCULATING = "calculating"
    CANCELLED = "cancelled"
    STALE = "stale"
    ERROR = "error"


class Cam3DUiReason(StrEnum):
    """Stable reason codes used by the shell and diagnostics placeholder."""

    FEATURE_FLAG_OFF = "feature_flag_off"
    NO_PROJECT = "no_project"
    NO_INPUT = "no_input"
    READY_FOR_INPUT = "ready_for_input"
    PROJECT_READ_ONLY = "project_read_only"
    SELECTION_IN_PROGRESS = "selection_in_progress"
    CALCULATION_IN_PROGRESS = "calculation_in_progress"
    CALCULATION_CANCELLED = "calculation_cancelled"
    PROJECT_STALE = "project_stale"
    VALIDATION_ERROR = "validation_error"


class Cam3DUiCommandPolicy(StrEnum):
    """Presentation-only command policy for the current state."""

    HIDDEN = "hidden"
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    AVAILABLE = "available"


_STATE_LABELS: Final[dict[Cam3DUiState, str]] = {
    Cam3DUiState.FEATURE_DISABLED: "Feature disabled",
    Cam3DUiState.EMPTY: "Machining zone",
    Cam3DUiState.READY: "READY",
    Cam3DUiState.READ_ONLY: "READ_ONLY",
    Cam3DUiState.SELECTING: "Selection",
    Cam3DUiState.CALCULATING: "Calculation Status",
    Cam3DUiState.CANCELLED: "CANCELLED",
    Cam3DUiState.STALE: "STALE",
    Cam3DUiState.ERROR: "ERROR",
}

_REASON_LABELS: Final[dict[Cam3DUiReason, str]] = {
    Cam3DUiReason.FEATURE_FLAG_OFF: "Feature disabled",
    Cam3DUiReason.NO_PROJECT: "Project",
    Cam3DUiReason.NO_INPUT: "Machining zone missing",
    Cam3DUiReason.READY_FOR_INPUT: "Machining zone",
    Cam3DUiReason.PROJECT_READ_ONLY: "READ_ONLY",
    Cam3DUiReason.SELECTION_IN_PROGRESS: "Selection",
    Cam3DUiReason.CALCULATION_IN_PROGRESS: "Calculation Status",
    Cam3DUiReason.CALCULATION_CANCELLED: "CANCELLED",
    Cam3DUiReason.PROJECT_STALE: "STALE",
    Cam3DUiReason.VALIDATION_ERROR: "ERROR",
}

_VALID_REASONS: Final[dict[Cam3DUiState, frozenset[Cam3DUiReason]]] = {
    Cam3DUiState.FEATURE_DISABLED: frozenset({Cam3DUiReason.FEATURE_FLAG_OFF}),
    Cam3DUiState.EMPTY: frozenset({Cam3DUiReason.NO_PROJECT, Cam3DUiReason.NO_INPUT}),
    Cam3DUiState.READY: frozenset({Cam3DUiReason.READY_FOR_INPUT}),
    Cam3DUiState.READ_ONLY: frozenset({Cam3DUiReason.PROJECT_READ_ONLY}),
    Cam3DUiState.SELECTING: frozenset({Cam3DUiReason.SELECTION_IN_PROGRESS}),
    Cam3DUiState.CALCULATING: frozenset({Cam3DUiReason.CALCULATION_IN_PROGRESS}),
    Cam3DUiState.CANCELLED: frozenset({Cam3DUiReason.CALCULATION_CANCELLED}),
    Cam3DUiState.STALE: frozenset({Cam3DUiReason.PROJECT_STALE}),
    Cam3DUiState.ERROR: frozenset({Cam3DUiReason.VALIDATION_ERROR}),
}

_DEFAULT_REASONS: Final[dict[Cam3DUiState, Cam3DUiReason]] = {
    state: next(iter(reasons))
    for state, reasons in _VALID_REASONS.items()
    if state is not Cam3DUiState.EMPTY
}

_ALLOWED_TRANSITIONS: Final[dict[Cam3DUiState, frozenset[Cam3DUiState]]] = {
    Cam3DUiState.FEATURE_DISABLED: frozenset(
        {Cam3DUiState.FEATURE_DISABLED, Cam3DUiState.EMPTY}
    ),
    Cam3DUiState.EMPTY: frozenset(
        {
            Cam3DUiState.EMPTY,
            Cam3DUiState.SELECTING,
            Cam3DUiState.READY,
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.READY: frozenset(
        {
            Cam3DUiState.READY,
            Cam3DUiState.SELECTING,
            Cam3DUiState.CALCULATING,
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.READ_ONLY: frozenset(
        {
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.SELECTING: frozenset(
        {
            Cam3DUiState.SELECTING,
            Cam3DUiState.EMPTY,
            Cam3DUiState.READY,
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.CALCULATING: frozenset(
        {
            Cam3DUiState.CALCULATING,
            Cam3DUiState.CANCELLED,
            Cam3DUiState.READY,
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.CANCELLED: frozenset(
        {
            Cam3DUiState.CANCELLED,
            Cam3DUiState.SELECTING,
            Cam3DUiState.READY,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.STALE: frozenset(
        {
            Cam3DUiState.STALE,
            Cam3DUiState.SELECTING,
            Cam3DUiState.EMPTY,
            Cam3DUiState.ERROR,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
    Cam3DUiState.ERROR: frozenset(
        {
            Cam3DUiState.ERROR,
            Cam3DUiState.SELECTING,
            Cam3DUiState.EMPTY,
            Cam3DUiState.STALE,
            Cam3DUiState.FEATURE_DISABLED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class Cam3DPresentationState:
    """Immutable state consumed by the WP1 shell."""

    state: Cam3DUiState
    reason: Cam3DUiReason
    project_id: UUID | None = None
    project_generation: int | None = None
    read_only: bool = False
    diagnostic_count: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, Cam3DUiState):
            raise TypeError("CAM 3D UI state must be Cam3DUiState")
        if not isinstance(self.reason, Cam3DUiReason):
            raise TypeError("CAM 3D UI reason must be Cam3DUiReason")
        if self.reason not in _VALID_REASONS[self.state]:
            raise ValueError("CAM 3D UI reason is inconsistent with state")
        if self.project_id is not None and not isinstance(self.project_id, UUID):
            raise TypeError("CAM 3D project identity must be UUID or None")
        if self.project_generation is not None and (
            type(self.project_generation) is not int or self.project_generation < 0
        ):
            raise ValueError("CAM 3D project generation must be a non-negative int")
        if (self.project_id is None) != (self.project_generation is None):
            raise ValueError("CAM 3D project identity and generation must be paired")
        if self.state in {
            Cam3DUiState.READY,
            Cam3DUiState.READ_ONLY,
            Cam3DUiState.SELECTING,
            Cam3DUiState.CALCULATING,
            Cam3DUiState.CANCELLED,
        } and self.project_id is None:
            raise ValueError("CAM 3D active state requires project identity")
        if self.state is Cam3DUiState.FEATURE_DISABLED and self.project_id is not None:
            raise ValueError("FEATURE_DISABLED cannot retain project identity")
        if type(self.read_only) is not bool:
            raise TypeError("CAM 3D read_only must be bool")
        if type(self.diagnostic_count) is not int or self.diagnostic_count < 0:
            raise ValueError("CAM 3D diagnostic_count must be non-negative")
        if not isinstance(self.message, str):
            raise TypeError("CAM 3D state message must be str")
        if self.state is Cam3DUiState.READ_ONLY and not self.read_only:
            raise ValueError("READ_ONLY state requires read_only=True")
        if self.state is not Cam3DUiState.READ_ONLY and self.read_only:
            raise ValueError("read_only=True requires READ_ONLY state")

    @classmethod
    def feature_disabled(cls) -> "Cam3DPresentationState":
        return cls(Cam3DUiState.FEATURE_DISABLED, Cam3DUiReason.FEATURE_FLAG_OFF)

    @classmethod
    def empty(
        cls,
        project_id: UUID | None = None,
        generation: int | None = None,
    ) -> "Cam3DPresentationState":
        reason = Cam3DUiReason.NO_PROJECT if project_id is None else Cam3DUiReason.NO_INPUT
        return cls(Cam3DUiState.EMPTY, reason, project_id, generation)

    @classmethod
    def ready(cls, project_id: UUID, generation: int) -> "Cam3DPresentationState":
        return cls(
            Cam3DUiState.READY,
            Cam3DUiReason.READY_FOR_INPUT,
            project_id,
            generation,
        )

    @classmethod
    def for_read_only(cls, project_id: UUID, generation: int) -> "Cam3DPresentationState":
        return cls(
            Cam3DUiState.READ_ONLY,
            Cam3DUiReason.PROJECT_READ_ONLY,
            project_id,
            generation,
            read_only=True,
        )

    @classmethod
    def stale(
        cls,
        project_id: UUID | None = None,
        generation: int | None = None,
    ) -> "Cam3DPresentationState":
        return cls(Cam3DUiState.STALE, Cam3DUiReason.PROJECT_STALE, project_id, generation)

    @classmethod
    def error(
        cls,
        message: str,
        project_id: UUID | None = None,
        generation: int | None = None,
        diagnostic_count: int = 1,
    ) -> "Cam3DPresentationState":
        return cls(
            Cam3DUiState.ERROR,
            Cam3DUiReason.VALIDATION_ERROR,
            project_id,
            generation,
            diagnostic_count=diagnostic_count,
            message=message,
        )

    @property
    def label_key(self) -> str:
        return _STATE_LABELS[self.state]

    @property
    def reason_key(self) -> str:
        return _REASON_LABELS[self.reason]

    @property
    def command_policy(self) -> Cam3DUiCommandPolicy:
        if self.state is Cam3DUiState.FEATURE_DISABLED:
            return Cam3DUiCommandPolicy.HIDDEN
        if self.state is Cam3DUiState.READ_ONLY:
            return Cam3DUiCommandPolicy.READ_ONLY
        if self.state in {
            Cam3DUiState.EMPTY,
            Cam3DUiState.SELECTING,
            Cam3DUiState.STALE,
            Cam3DUiState.ERROR,
            Cam3DUiState.CALCULATING,
            Cam3DUiState.CANCELLED,
        }:
            return Cam3DUiCommandPolicy.DISABLED
        return Cam3DUiCommandPolicy.AVAILABLE

    @property
    def resolved(self) -> bool:
        """Only READY is a resolved/current WP1 state."""

        return self.state is Cam3DUiState.READY

    def transition(
        self,
        target: Cam3DUiState,
        *,
        reason: Cam3DUiReason | None = None,
        message: str | None = None,
        diagnostic_count: int | None = None,
    ) -> "Cam3DPresentationState":
        if not isinstance(target, Cam3DUiState):
            raise TypeError("CAM 3D target state must be Cam3DUiState")
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                f"Invalid CAM 3D UI transition: {self.state.value} -> {target.value}"
            )
        if reason is None:
            next_reason = (
                Cam3DUiReason.NO_PROJECT
                if target is Cam3DUiState.EMPTY and self.project_id is None
                else Cam3DUiReason.NO_INPUT
                if target is Cam3DUiState.EMPTY
                else _DEFAULT_REASONS[target]
            )
        else:
            next_reason = reason
        next_message = self.message if message is None else message
        next_count = (
            self.diagnostic_count
            if diagnostic_count is None
            else diagnostic_count
        )
        next_read_only = target is Cam3DUiState.READ_ONLY
        next_project_id = (
            None if target is Cam3DUiState.FEATURE_DISABLED else self.project_id
        )
        next_generation = (
            None if target is Cam3DUiState.FEATURE_DISABLED else self.project_generation
        )
        return Cam3DPresentationState(
            target,
            next_reason,
            next_project_id,
            next_generation,
            read_only=next_read_only,
            diagnostic_count=next_count,
            message=next_message,
        )


__all__ = [
    "Cam3DPresentationState",
    "Cam3DUiCommandPolicy",
    "Cam3DUiReason",
    "Cam3DUiState",
]
