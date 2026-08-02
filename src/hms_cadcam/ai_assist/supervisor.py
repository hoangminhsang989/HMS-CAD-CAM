"""Lazy worker ownership contracts; Stage 13A never launches a model process."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hms_cadcam.ai_assist.policy import AiTier


@dataclass(frozen=True, slots=True)
class WorkerStartResult:
    """Outcome of an explicitly permitted worker start request."""

    started: bool
    worker_id: str | None = None
    process_id: int | None = None
    reason_code: str | None = None


class WorkerSupervisor(Protocol):
    """Own at most one future AI worker and expose deterministic release semantics."""

    @property
    def has_worker(self) -> bool:
        """Return whether an owned worker contract is active."""

    def start(self, tier: AiTier) -> WorkerStartResult:
        """Start only after a broker has granted resource permission."""

    def cancel(self) -> None:
        """Request cooperative cancellation for an owned worker."""

    def release(self, *, graceful_timeout_seconds: float) -> None:
        """Release worker ownership, using the supplied hard-timeout contract."""

    def shutdown(self) -> None:
        """Release the worker during application shutdown."""


class NoOpWorkerSupervisor:
    """Production-safe Stage 13A supervisor that owns no process or model.

    It records one logical permission lease for lifecycle QA only.  It never
    imports subprocess, creates a thread, opens a model file, or allocates VRAM.
    A later stage must replace this only behind the same protocol.
    """

    def __init__(self) -> None:
        self._active_tier: AiTier | None = None

    @property
    def has_worker(self) -> bool:
        """Return logical ownership, not a process or a model residency claim."""

        return self._active_tier is not None

    @property
    def active_tier(self) -> AiTier | None:
        """Return the active logical lease for diagnostics."""

        return self._active_tier

    def start(self, tier: AiTier) -> WorkerStartResult:
        """Acquire a single no-op lease; duplicate starts remain fail-closed."""

        if not isinstance(tier, AiTier):
            raise TypeError("tier must be AiTier")
        if self._active_tier is not None:
            return WorkerStartResult(False, reason_code="WORKER_ALREADY_OWNED")
        self._active_tier = tier
        return WorkerStartResult(True, worker_id="stage13a-noop", process_id=None)

    def cancel(self) -> None:
        """Cancel has no external process effect in the Stage 13A implementation."""

    def release(self, *, graceful_timeout_seconds: float) -> None:
        """Release the logical lease without creating or waiting on a process."""

        if graceful_timeout_seconds < 0:
            raise ValueError("graceful_timeout_seconds must be non-negative")
        self._active_tier = None

    def shutdown(self) -> None:
        """Guarantee no worker ownership residue at application close."""

        self._active_tier = None


__all__ = ["NoOpWorkerSupervisor", "WorkerStartResult", "WorkerSupervisor"]
