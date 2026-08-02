"""State-machine and zero-worker invariants for the Stage 13A broker."""

from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.ai_assist.lifecycle import AiAssistBroker, AiRuntimeState
from hms_cadcam.ai_assist.policy import AiMode, GIB
from hms_cadcam.ai_assist.resources import (
    ProbeStatus,
    RamResourceSnapshot,
    ResourceSnapshot,
    VramResourceSnapshot,
)
from hms_cadcam.ai_assist.supervisor import NoOpWorkerSupervisor


@dataclass
class FakeClock:
    current_ns: int = 0

    def now_ns(self) -> int:
        return self.current_ns

    def advance_seconds(self, seconds: int) -> None:
        self.current_ns += seconds * 1_000_000_000


def _sample(available: int) -> ResourceSnapshot:
    total = 8 * GIB
    return ResourceSnapshot(
        RamResourceSnapshot(total, available, total - available, None, 1, "fake", ProbeStatus.AVAILABLE),
        VramResourceSnapshot(None, None, 1, "fake", ProbeStatus.UNKNOWN, "test"),
    )


def test_off_and_waiting_states_never_own_a_worker() -> None:
    worker = NoOpWorkerSupervisor()
    broker = AiAssistBroker(supervisor=worker)
    assert broker.status.state is AiRuntimeState.OFF
    assert not worker.has_worker

    broker.configure(capability_enabled=True, master_enabled=True, mode=AiMode.AUTO, user_cap_bytes=None)
    monitoring = broker.observe(_sample(3 * GIB))
    assert monitoring.state is AiRuntimeState.MONITORING
    assert monitoring.budget is not None
    assert not worker.has_worker
    broker.request_task()
    status = broker.observe(_sample(500 * 1024 * 1024))
    assert status.state is AiRuntimeState.WAITING_FOR_RESOURCES
    assert not worker.has_worker


def test_stable_window_grants_one_lazy_worker_lease_then_pressure_releases_it() -> None:
    clock = FakeClock()
    worker = NoOpWorkerSupervisor()
    broker = AiAssistBroker(supervisor=worker, clock=clock)
    broker.configure(capability_enabled=True, master_enabled=True, mode=AiMode.AUTO, user_cap_bytes=None)

    broker.request_task()
    assert broker.observe(_sample(4 * GIB)).state is AiRuntimeState.WAITING_FOR_RESOURCES
    clock.advance_seconds(5)
    assert broker.observe(_sample(4 * GIB)).state is AiRuntimeState.READY
    running = broker.start_ready_task()
    assert running.state is AiRuntimeState.RUNNING
    assert running.worker_started

    assert broker.observe(_sample(100 * 1024 * 1024)).state is AiRuntimeState.RUNNING
    clock.advance_seconds(3)
    pressure = broker.observe(_sample(100 * 1024 * 1024))
    assert pressure.state is AiRuntimeState.WAITING_FOR_RESOURCES
    assert pressure.reason_code == "RESOURCE_PRESSURE"
    assert not worker.has_worker


def test_cancel_and_shutdown_leave_no_worker_or_task_residue() -> None:
    worker = NoOpWorkerSupervisor()
    broker = AiAssistBroker(supervisor=worker)
    broker.configure(capability_enabled=True, master_enabled=True, mode=AiMode.LITE, user_cap_bytes=None)
    broker.request_task()
    cancelled = broker.cancel_task()
    assert cancelled.state is AiRuntimeState.MONITORING
    assert not cancelled.task_requested
    assert not cancelled.worker_started

    stopped = broker.shutdown()
    assert stopped.state is AiRuntimeState.OFF
    assert not stopped.worker_started
