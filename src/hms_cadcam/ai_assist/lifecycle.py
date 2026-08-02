"""Deterministic AI resource broker and state machine without Qt or polling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import time
from typing import Protocol

from hms_cadcam.ai_assist.policy import (
    AiMode,
    AiResourcePolicy,
    AiTier,
    ProfileSelection,
    ResourceBudget,
    calculate_budget,
    select_profile,
)
from hms_cadcam.ai_assist.resources import ResourceSnapshot
from hms_cadcam.ai_assist.supervisor import NoOpWorkerSupervisor, WorkerSupervisor


class AiRuntimeState(StrEnum):
    OFF = "OFF"
    MONITORING = "MONITORING"
    WAITING_FOR_RESOURCES = "WAITING_FOR_RESOURCES"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSING_FOR_RESOURCE_PRESSURE = "PAUSING_FOR_RESOURCE_PRESSURE"
    PAUSED_FOR_RESOURCE_PRESSURE = "PAUSED_FOR_RESOURCE_PRESSURE"
    RELEASING = "RELEASING"
    ERROR = "ERROR"


class AiRuntimeReason(StrEnum):
    AI_DISABLED = "AI_DISABLED"
    NO_TASK_REQUESTED = "NO_TASK_REQUESTED"
    INSUFFICIENT_AVAILABLE_RAM = "INSUFFICIENT_AVAILABLE_RAM"
    INSUFFICIENT_COMMIT_HEADROOM = "INSUFFICIENT_COMMIT_HEADROOM"
    INSUFFICIENT_VRAM = "INSUFFICIENT_VRAM"
    GPU_RESOURCE_UNKNOWN = "GPU_RESOURCE_UNKNOWN"
    CPU_ONLY_SELECTED = "CPU_ONLY_SELECTED"
    WAITING_STABILITY_WINDOW = "WAITING_STABILITY_WINDOW"
    PROFILE_LITE_SELECTED = "PROFILE_LITE_SELECTED"
    PROFILE_STANDARD_SELECTED = "PROFILE_STANDARD_SELECTED"
    PROFILE_ENHANCED_SELECTED = "PROFILE_ENHANCED_SELECTED"
    RESOURCE_PRESSURE = "RESOURCE_PRESSURE"
    WORKER_NOT_STARTED = "WORKER_NOT_STARTED"
    WORKER_RELEASED = "WORKER_RELEASED"
    PROBE_FAILED = "PROBE_FAILED"
    POLICY_ERROR = "POLICY_ERROR"


class MonotonicClock(Protocol):
    """Injection boundary that lets tests advance time without sleeping."""

    def now_ns(self) -> int:
        """Return a monotonic timestamp in integer nanoseconds."""


class SystemMonotonicClock:
    """Production clock; it does not schedule a timer by itself."""

    def now_ns(self) -> int:
        """Return the current monotonic timestamp."""

        return time.monotonic_ns()


@dataclass(frozen=True, slots=True)
class AiRuntimeStatus:
    """Immutable broker projection used by UI and tests."""

    state: AiRuntimeState
    reason_code: str
    selected_tier: AiTier | None
    budget: ResourceBudget | None
    worker_started: bool
    task_requested: bool
    capability_enabled: bool
    master_enabled: bool


class AiAssistBroker:
    """Grant a lazy worker permission only after stable, safe resources exist."""

    def __init__(
        self,
        *,
        policy: AiResourcePolicy | None = None,
        supervisor: WorkerSupervisor | None = None,
        clock: MonotonicClock | None = None,
        capability_enabled: bool = True,
        master_enabled: bool = False,
        mode: AiMode = AiMode.AUTO,
        user_cap_bytes: int | None = None,
    ) -> None:
        if type(capability_enabled) is not bool or type(master_enabled) is not bool:
            raise TypeError("capability_enabled and master_enabled must be bool")
        if not isinstance(mode, AiMode):
            raise TypeError("mode must be AiMode")
        self._policy = policy or AiResourcePolicy()
        self._supervisor = supervisor or NoOpWorkerSupervisor()
        self._clock = clock or SystemMonotonicClock()
        self._capability_enabled = capability_enabled
        self._master_enabled = master_enabled
        self._mode = mode
        self._user_cap_bytes = user_cap_bytes
        self._task_requested = False
        self._state = AiRuntimeState.OFF
        self._reason = AiRuntimeReason.AI_DISABLED.value
        self._selection: ProfileSelection | None = None
        self._last_budget: ResourceBudget | None = None
        self._sufficient_since_ns: int | None = None
        self._pressure_since_ns: int | None = None
        self._apply_disabled_state()

    @property
    def status(self) -> AiRuntimeStatus:
        """Return a side-effect-free view of broker state."""

        budget = self._selection.budget if self._selection is not None else self._last_budget
        tier = self._selection.tier if self._selection is not None else None
        return AiRuntimeStatus(
            state=self._state,
            reason_code=self._reason,
            selected_tier=tier,
            budget=budget,
            worker_started=self._supervisor.has_worker,
            task_requested=self._task_requested,
            capability_enabled=self._capability_enabled,
            master_enabled=self._master_enabled,
        )

    def configure(
        self,
        *,
        capability_enabled: bool,
        master_enabled: bool,
        mode: AiMode,
        user_cap_bytes: int | None,
        policy: AiResourcePolicy | None = None,
    ) -> AiRuntimeStatus:
        """Apply settings; disabling always releases ownership synchronously."""

        if type(capability_enabled) is not bool or type(master_enabled) is not bool:
            raise TypeError("capability_enabled and master_enabled must be bool")
        if not isinstance(mode, AiMode):
            raise TypeError("mode must be AiMode")
        if user_cap_bytes is not None and (type(user_cap_bytes) is not int or user_cap_bytes < 0):
            raise ValueError("user_cap_bytes must be a non-negative integer or None")
        self._capability_enabled = capability_enabled
        self._master_enabled = master_enabled
        self._mode = mode
        self._user_cap_bytes = user_cap_bytes
        if policy is not None:
            self._policy = policy
        if not capability_enabled or not master_enabled:
            self._task_requested = False
            self._release("AI_DISABLED")
            self._apply_disabled_state()
        elif not self._task_requested:
            self._state = AiRuntimeState.MONITORING
            self._reason = AiRuntimeReason.NO_TASK_REQUESTED.value
        return self.status

    def request_task(self) -> AiRuntimeStatus:
        """Record an explicit future analysis request; no hardware probe occurs here."""

        if not self._capability_enabled or not self._master_enabled:
            self._apply_disabled_state()
            return self.status
        self._task_requested = True
        self._state = AiRuntimeState.MONITORING
        self._reason = AiRuntimeReason.WAITING_STABILITY_WINDOW.value
        self._sufficient_since_ns = None
        self._pressure_since_ns = None
        return self.status

    def observe(self, snapshot: ResourceSnapshot) -> AiRuntimeStatus:
        """Evaluate one caller-triggered sample; it never starts periodic polling."""

        if not isinstance(snapshot, ResourceSnapshot):
            raise TypeError("snapshot must be ResourceSnapshot")
        if not self._capability_enabled or not self._master_enabled:
            self._apply_disabled_state()
            return self.status
        if not self._task_requested:
            try:
                self._last_budget = calculate_budget(
                    snapshot,
                    self._policy,
                    self._policy.profiles[AiTier.LITE],
                    user_configured_cap_bytes=self._user_cap_bytes,
                )
            except (TypeError, ValueError, OverflowError):
                self._last_budget = None
                self._state = AiRuntimeState.ERROR
                self._reason = AiRuntimeReason.POLICY_ERROR.value
                return self.status
            self._state = AiRuntimeState.MONITORING
            self._reason = AiRuntimeReason.NO_TASK_REQUESTED.value
            return self.status
        now = self._clock.now_ns()
        try:
            if (
                self._state is AiRuntimeState.RUNNING
                and self._selection is not None
                and self._selection.profile is not None
                and self._selection.tier is not None
            ):
                running_selection = ProfileSelection(
                    self._selection.tier,
                    self._selection.profile,
                    calculate_budget(
                        snapshot,
                        self._policy,
                        self._selection.profile,
                        user_configured_cap_bytes=self._user_cap_bytes,
                    ),
                    self._selection.reason_code,
                )
                self._selection = running_selection
                self._observe_pressure(running_selection, now)
                return self.status
            selection = select_profile(
                snapshot,
                self._policy,
                self._mode,
                user_configured_cap_bytes=self._user_cap_bytes,
            )
        except (TypeError, ValueError, OverflowError):
            self._selection = None
            self._state = AiRuntimeState.ERROR
            self._reason = AiRuntimeReason.POLICY_ERROR.value
            return self.status
        self._selection = selection
        self._last_budget = selection.budget
        if selection.profile is None or selection.budget is None:
            self._sufficient_since_ns = None
            self._wait(selection.reason_code)
            return self.status
        start_threshold = int(selection.profile.minimum_start_bytes * self._policy.start_hysteresis_ratio)
        if selection.budget.ai_ram_budget_bytes < start_threshold:
            self._sufficient_since_ns = None
            self._wait(AiRuntimeReason.INSUFFICIENT_AVAILABLE_RAM.value)
            return self.status
        if self._sufficient_since_ns is None:
            self._sufficient_since_ns = now
        if now - self._sufficient_since_ns < self._policy.stable_start_window_ns:
            self._wait(AiRuntimeReason.WAITING_STABILITY_WINDOW.value)
            return self.status
        self._state = AiRuntimeState.READY
        self._reason = selection.reason_code
        return self.status

    def start_ready_task(self) -> AiRuntimeStatus:
        """Start the supervisor only from READY and only with a selected profile."""

        if self._state is not AiRuntimeState.READY or self._selection is None or self._selection.tier is None:
            return self.status
        self._state = AiRuntimeState.STARTING
        result = self._supervisor.start(self._selection.tier)
        if result.started:
            self._state = AiRuntimeState.RUNNING
            self._reason = self._selection.reason_code
        else:
            self._state = AiRuntimeState.ERROR
            self._reason = result.reason_code or AiRuntimeReason.WORKER_NOT_STARTED.value
        return self.status

    def cancel_task(self) -> AiRuntimeStatus:
        """Cancel a request and release any no-op/future worker ownership."""

        self._task_requested = False
        self._supervisor.cancel()
        self._release(AiRuntimeReason.WORKER_RELEASED.value)
        if self._capability_enabled and self._master_enabled:
            self._state = AiRuntimeState.MONITORING
            self._reason = AiRuntimeReason.NO_TASK_REQUESTED.value
        else:
            self._apply_disabled_state()
        return self.status

    def shutdown(self) -> AiRuntimeStatus:
        """Release all ownership and guarantee OFF without a timer/thread residue."""

        self._task_requested = False
        self._supervisor.shutdown()
        self._apply_disabled_state()
        return self.status

    def _observe_pressure(self, selection: ProfileSelection, now: int) -> None:
        assert selection.profile is not None and selection.budget is not None
        stop_threshold = int(selection.profile.minimum_start_bytes * self._policy.stop_hysteresis_ratio)
        if selection.budget.ai_ram_budget_bytes >= stop_threshold:
            self._pressure_since_ns = None
            return
        if self._pressure_since_ns is None:
            self._pressure_since_ns = now
            return
        if now - self._pressure_since_ns < self._policy.pressure_window_ns:
            return
        self._state = AiRuntimeState.PAUSING_FOR_RESOURCE_PRESSURE
        self._reason = AiRuntimeReason.RESOURCE_PRESSURE.value
        self._supervisor.cancel()
        self._state = AiRuntimeState.PAUSED_FOR_RESOURCE_PRESSURE
        self._state = AiRuntimeState.RELEASING
        self._release(AiRuntimeReason.RESOURCE_PRESSURE.value)
        self._state = AiRuntimeState.WAITING_FOR_RESOURCES

    def _wait(self, reason: str) -> None:
        self._state = AiRuntimeState.WAITING_FOR_RESOURCES
        self._reason = reason

    def _release(self, reason: str) -> None:
        if self._supervisor.has_worker:
            self._state = AiRuntimeState.RELEASING
            self._supervisor.release(graceful_timeout_seconds=2.0)
        self._reason = reason

    def _apply_disabled_state(self) -> None:
        self._sufficient_since_ns = None
        self._pressure_since_ns = None
        self._selection = None
        self._last_budget = None
        self._state = AiRuntimeState.OFF
        self._reason = AiRuntimeReason.AI_DISABLED.value


__all__ = [
    "AiAssistBroker",
    "AiRuntimeReason",
    "AiRuntimeState",
    "AiRuntimeStatus",
    "MonotonicClock",
    "SystemMonotonicClock",
]
