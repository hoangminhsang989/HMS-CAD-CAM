"""Simulation 7C.3 runtime state, progress, cancellation, and stale guards."""

from __future__ import annotations

import dataclasses
from uuid import uuid4

from hms_cadcam.cam.domain import Point3, SimulationRequestId
from hms_cadcam.cam.simulation import (
    CollisionScene,
    CollisionTarget,
    CollisionTargetKind,
    InMemoryAabbBackend,
    SimulationInputSnapshot,
    SimulationIssueCode,
    SimulationProgressPhase,
    SimulationRunController,
    SimulationRunState,
)
from hms_cadcam.cam.toolpath.geometry import Bounds3
from tests.unit.test_simulation_service import _source


def _snapshot():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    return (
        SimulationInputSnapshot(
            operation,
            artifact,
            setup,
            tool,
            assembly,
            holder,
            None,
            request,
        ),
        scene,
    )


def test_successful_run_reports_deterministic_phase_order_and_publishes() -> None:
    snapshot, scene = _snapshot()
    controller = SimulationRunController(progress_interval_seconds=0.0)
    controller.bind_project(uuid4(), 7)
    phases = []
    handle = controller.start(snapshot.request)
    execution = controller.execute(
        handle,
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
        current_request=lambda: snapshot.request,
        progress_callback=lambda value: phases.append(value.phase),
    )

    assert execution.accepted
    assert controller.result(snapshot.operation.operation_id) is execution.result
    assert controller.record(snapshot.operation.operation_id).state is SimulationRunState.COMPLETED
    ordered = tuple(dict.fromkeys(phases))
    assert ordered == (
        SimulationProgressPhase.VALIDATING,
        SimulationProgressPhase.RESOLVING,
        SimulationProgressPhase.SAMPLING,
        SimulationProgressPhase.BROAD_PHASE,
        SimulationProgressPhase.NARROW_PHASE,
        SimulationProgressPhase.BUILDING_RESULT,
        SimulationProgressPhase.PUBLISHING,
    )


def test_collision_fail_is_a_published_result_not_runtime_failure() -> None:
    snapshot, _scene = _snapshot()
    unit = snapshot.artifact.unit
    collision_bounds = Bounds3(
        Point3(-10, -10, 10, unit),
        Point3(20, 20, 120, unit),
    )
    scene = CollisionScene(
        CollisionTarget("stock", CollisionTargetKind.STOCK, collision_bounds)
    )
    controller = SimulationRunController()
    controller.bind_project(uuid4(), 2)
    execution = controller.execute(
        controller.start(snapshot.request),
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )

    assert execution.accepted
    assert execution.result.status.value == "fail"
    assert controller.record(snapshot.operation.operation_id).state is SimulationRunState.COMPLETED


def test_cancel_during_sampling_preserves_previous_result_and_publishes_no_partial() -> None:
    snapshot, scene = _snapshot()
    controller = SimulationRunController(progress_interval_seconds=0.0)
    controller.bind_project(uuid4(), 4)
    first = controller.execute(
        controller.start(snapshot.request),
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    previous = first.result
    request = dataclasses.replace(
        snapshot.request,
        request_id=SimulationRequestId.new(),
    )
    rerun = dataclasses.replace(snapshot, request=request)
    handle = controller.start(request)

    def cancel_on_sampling(progress) -> None:
        if progress.phase is SimulationProgressPhase.SAMPLING:
            controller.cancel(request.operation_id)

    cancelled = controller.execute(
        handle,
        snapshot=rerun,
        scene=scene,
        backend=InMemoryAabbBackend(),
        progress_callback=cancel_on_sampling,
    )

    assert not cancelled.accepted
    assert cancelled.code is SimulationIssueCode.CANCELLED
    assert controller.result(request.operation_id) is previous
    assert controller.record(request.operation_id).state is SimulationRunState.IDLE


def test_cancel_during_collision_phase_has_no_partial_publish() -> None:
    snapshot, scene = _snapshot()
    controller = SimulationRunController(progress_interval_seconds=0.0)
    controller.bind_project(uuid4(), 5)
    cancelled = []

    def cancel_on_narrow(progress) -> None:
        if progress.phase is SimulationProgressPhase.NARROW_PHASE:
            cancelled.append(progress)
            controller.cancel(snapshot.operation.operation_id)

    execution = controller.execute(
        controller.start(snapshot.request),
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
        progress_callback=cancel_on_narrow,
    )
    assert cancelled
    assert not execution.accepted
    assert execution.code is SimulationIssueCode.CANCELLED
    assert controller.result(snapshot.operation.operation_id) is None
    assert controller.record(snapshot.operation.operation_id).state is SimulationRunState.IDLE


def test_operation_invalidation_during_run_marks_stale_and_drops_late_result() -> None:
    snapshot, scene = _snapshot()
    controller = SimulationRunController(progress_interval_seconds=0.0)
    controller.bind_project(uuid4(), 6)

    def invalidate(progress) -> None:
        if progress.phase is SimulationProgressPhase.SAMPLING:
            controller.mark_stale(snapshot.operation.operation_id, "operation disabled")

    execution = controller.execute(
        controller.start(snapshot.request),
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
        progress_callback=invalidate,
    )
    assert not execution.accepted
    assert execution.code is SimulationIssueCode.CANCELLED
    record = controller.record(snapshot.operation.operation_id)
    assert record is not None and record.state is SimulationRunState.STALE
    assert controller.result(snapshot.operation.operation_id) is None


def test_project_switch_during_run_invalidates_late_callbacks_and_result() -> None:
    snapshot, scene = _snapshot()
    first_project = uuid4()
    controller = SimulationRunController(progress_interval_seconds=0.0)
    controller.bind_project(first_project, 9)
    handle = controller.start(snapshot.request)
    callbacks = []

    def switch_project(progress) -> None:
        callbacks.append(progress)
        if progress.phase is SimulationProgressPhase.SAMPLING:
            controller.bind_project(uuid4(), 10)

    execution = controller.execute(
        handle,
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
        progress_callback=switch_project,
    )

    assert not execution.accepted
    assert controller.result(snapshot.operation.operation_id) is None
    assert controller.record(snapshot.operation.operation_id) is None
    count = len(callbacks)
    # An explicitly stale handle cannot inject a new UI progress mutation.
    assert not controller.report_rendering(
        snapshot.request.request_id,
        processed=1,
        total=1,
        callback=callbacks.append,
    )
    assert len(callbacks) == count


def test_progress_callbacks_are_throttled_with_phase_boundaries_preserved() -> None:
    snapshot, scene = _snapshot()
    controller = SimulationRunController(
        progress_interval_seconds=10.0,
        clock=lambda: 1.0,
    )
    controller.bind_project(uuid4(), 1)
    values = []
    execution = controller.execute(
        controller.start(snapshot.request),
        snapshot=snapshot,
        scene=scene,
        backend=InMemoryAabbBackend(),
        progress_callback=values.append,
    )
    assert execution.accepted
    assert len(values) < 20
    assert tuple(dict.fromkeys(item.phase for item in values))[-1] is SimulationProgressPhase.PUBLISHING
