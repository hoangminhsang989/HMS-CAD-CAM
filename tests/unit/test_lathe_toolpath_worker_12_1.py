"""Qt-free worker, cancellation, latest-wins and cache acceptance tests."""

from __future__ import annotations

from threading import Event, Lock, Thread, enumerate as enumerate_threads, get_ident

from hms_cadcam.cam.lathe.toolpath import (
    AxialDrillToolpathGenerator,
    LatheCancelDecision,
    LatheSubmissionDecision,
    LatheToolpathCancelledError,
    LatheToolpathCoordinator,
    LatheToolpathGeneratorRegistry,
    LatheToolpathJobState,
    OdFinishToolpathGenerator,
    OdRoughToolpathGenerator,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import (
    ready_request,
    same_semantics_request,
)


class _RecordingRoughGenerator:
    strategy_id = LatheStrategyId.OD_ROUGH

    def __init__(self) -> None:
        self.thread_ids: list[int] = []
        self.lock = Lock()
        self.delegate = OdRoughToolpathGenerator()

    def generate(self, request, cancellation):
        with self.lock:
            self.thread_ids.append(get_ident())
        return self.delegate.generate(request, cancellation)


class _FirstBlockingRoughGenerator:
    strategy_id = LatheStrategyId.OD_ROUGH

    def __init__(self, first_job_id) -> None:
        self.first_job_id = first_job_id
        self.started = Event()
        self.cancelled_seen = Event()
        self.release = Event()
        self.delegate = OdRoughToolpathGenerator()

    def generate(self, request, cancellation):
        if request.job_id != self.first_job_id:
            return self.delegate.generate(request, cancellation)
        self.started.set()
        while not self.release.wait(0.01):
            if cancellation():
                self.cancelled_seen.set()
                raise LatheToolpathCancelledError("cancelled")
        if cancellation():
            self.cancelled_seen.set()
            raise LatheToolpathCancelledError("cancelled")
        return self.delegate.generate(request, cancellation)


def _registry(rough) -> LatheToolpathGeneratorRegistry:
    return LatheToolpathGeneratorRegistry(
        (
            rough,
            OdFinishToolpathGenerator(),
            AxialDrillToolpathGenerator(),
        )
    )


def _submit_and_wait(coordinator: LatheToolpathCoordinator, request):
    delivered = []
    finished = Event()

    def callback(result) -> None:
        delivered.append(result)
        finished.set()

    receipt = coordinator.submit(request, callback=callback)
    assert receipt.accepted
    assert finished.wait(5.0)
    assert len(delivered) == 1
    return receipt, delivered[0]


def test_valid_submission_runs_off_caller_thread_and_delivers_once() -> None:
    request = ready_request()[2]
    generator = _RecordingRoughGenerator()
    coordinator = LatheToolpathCoordinator(_registry(generator))
    caller_thread = get_ident()
    try:
        receipt, result = _submit_and_wait(coordinator, request)
        assert receipt.decision is LatheSubmissionDecision.ACCEPTED
        assert receipt.scheduled
        assert generator.thread_ids and generator.thread_ids != [caller_thread]
        assert result.succeeded
        assert coordinator.delivery_authorized(result)
        record = coordinator.job_record(request.job_id)
        assert record is not None and record.state is LatheToolpathJobState.COMPLETED
    finally:
        coordinator.shutdown()


def test_invalid_and_duplicate_request_are_rejected_before_second_worker() -> None:
    request = ready_request()[2]
    blocker = _FirstBlockingRoughGenerator(request.job_id)
    coordinator = LatheToolpathCoordinator(_registry(blocker))
    try:
        invalid = coordinator.submit(object())
        assert not invalid.accepted
        assert invalid.decision is LatheSubmissionDecision.INVALID_REQUEST
        first = coordinator.submit(request)
        assert first.accepted and blocker.started.wait(5.0)
        duplicate = coordinator.submit(request)
        assert not duplicate.accepted
        assert duplicate.decision is LatheSubmissionDecision.DUPLICATE_REQUEST
    finally:
        blocker.release.set()
        coordinator.shutdown()


def test_latest_wins_drops_old_same_owner_and_delivers_only_new_result() -> None:
    first = ready_request()[2]
    second = ready_request(parameters={"feed_mm_per_rev": 0.31})[2]
    blocker = _FirstBlockingRoughGenerator(first.job_id)
    coordinator = LatheToolpathCoordinator(_registry(blocker), max_workers=2)
    delivered = []
    second_finished = Event()
    try:
        coordinator.submit(first, callback=delivered.append)
        assert blocker.started.wait(5.0)
        coordinator.submit(
            second,
            callback=lambda result: (delivered.append(result), second_finished.set()),
        )
        assert second_finished.wait(5.0)
        assert blocker.cancelled_seen.wait(5.0)
        assert tuple(item.identity.job_id for item in delivered) == (second.job_id,)
        first_record = coordinator.job_record(first.job_id)
        assert first_record is not None
        assert first_record.state is LatheToolpathJobState.DROPPED
        assert coordinator.delivery_authorized(delivered[0])
    finally:
        blocker.release.set()
        coordinator.shutdown()


def test_different_operation_ownerships_execute_and_cancel_in_isolation() -> None:
    first = ready_request(operation_index=1)[2]
    second = ready_request(operation_index=2)[2]
    blocker = _FirstBlockingRoughGenerator(first.job_id)
    coordinator = LatheToolpathCoordinator(_registry(blocker), max_workers=2)
    delivered = []
    second_finished = Event()
    try:
        coordinator.submit(first, callback=delivered.append)
        assert blocker.started.wait(5.0)
        coordinator.submit(
            second,
            callback=lambda result: (delivered.append(result), second_finished.set()),
        )
        assert second_finished.wait(5.0)
        assert coordinator.cancel(first.job_id) is LatheCancelDecision.REQUESTED
        assert coordinator.cancel(first.job_id) is LatheCancelDecision.ALREADY_CANCELLED
        assert blocker.cancelled_seen.wait(5.0)
        assert tuple(item.identity.job_id for item in delivered) == (second.job_id,)
    finally:
        blocker.release.set()
        coordinator.shutdown()


def test_close_ownership_cancels_blocks_submission_then_explicit_bind_reopens() -> None:
    first = ready_request()[2]
    blocker = _FirstBlockingRoughGenerator(first.job_id)
    coordinator = LatheToolpathCoordinator(_registry(blocker), max_workers=2)
    try:
        coordinator.submit(first)
        assert blocker.started.wait(5.0)
        coordinator.close_ownership(first.ownership)
        assert blocker.cancelled_seen.wait(5.0)
        blocked = coordinator.submit(same_semantics_request(first))
        assert not blocked.accepted
        assert blocked.decision is LatheSubmissionDecision.OWNERSHIP_CLOSED
        coordinator.bind_ownership(first.ownership)
        accepted, result = _submit_and_wait(
            coordinator,
            same_semantics_request(first),
        )
        assert accepted.accepted and result.succeeded
    finally:
        blocker.release.set()
        coordinator.shutdown()


def test_explicit_cancel_is_cooperative_repeated_and_never_delivers_partial() -> None:
    request = ready_request()[2]
    blocker = _FirstBlockingRoughGenerator(request.job_id)
    coordinator = LatheToolpathCoordinator(_registry(blocker))
    delivered = []
    try:
        coordinator.submit(request, callback=delivered.append)
        assert blocker.started.wait(5.0)
        assert coordinator.cancel(request.job_id) is LatheCancelDecision.REQUESTED
        assert coordinator.cancel(request.job_id) is LatheCancelDecision.ALREADY_CANCELLED
        assert blocker.cancelled_seen.wait(5.0)
        assert delivered == []
        assert not coordinator.delivery_authorized(
            _submit_cancelled_shape_is_not_needed(request)
        )
    finally:
        blocker.release.set()
        coordinator.shutdown()


def _submit_cancelled_shape_is_not_needed(request):
    """Return a real successful-shaped result whose cancelled job is unauthorized."""

    return OdRoughToolpathGenerator().generate(request, lambda: False)


def test_cache_hit_uses_new_job_identity_and_same_acceptance_callback_gate() -> None:
    request = ready_request()[2]
    coordinator = LatheToolpathCoordinator()
    try:
        first_receipt, first = _submit_and_wait(coordinator, request)
        repeated = same_semantics_request(request)
        second_receipt, second = _submit_and_wait(coordinator, repeated)
        assert first_receipt.decision is LatheSubmissionDecision.ACCEPTED
        assert second_receipt.decision is LatheSubmissionDecision.CACHE_HIT
        assert not second_receipt.scheduled
        assert second.identity.job_id == repeated.job_id
        assert second.identity.job_id != first.identity.job_id
        assert second.motions is first.motions
        assert coordinator.delivery_authorized(second)
    finally:
        coordinator.shutdown()


def test_callback_is_invoked_outside_coordinator_lock() -> None:
    request = ready_request()[2]
    coordinator = LatheToolpathCoordinator()
    callback_finished = Event()
    probe_observations: list[bool] = []

    def callback(_result) -> None:
        probe_finished = Event()

        def cross_thread_query() -> None:
            coordinator.job_record(request.job_id)
            probe_finished.set()

        thread = Thread(target=cross_thread_query, name="lathe-callback-lock-probe")
        thread.start()
        probe_observations.append(probe_finished.wait(2.0))
        thread.join(timeout=2.0)
        callback_finished.set()

    try:
        coordinator.submit(request, callback=callback)
        assert callback_finished.wait(5.0)
        assert probe_observations == [True]
    finally:
        coordinator.shutdown()


def test_records_are_bounded_and_old_completed_identity_is_evicted() -> None:
    first = ready_request()[2]
    coordinator = LatheToolpathCoordinator(max_records=3)
    jobs = []
    try:
        for _index in range(8):
            request = same_semantics_request(first)
            jobs.append(request.job_id)
            _submit_and_wait(coordinator, request)
        assert coordinator.job_record(jobs[0]) is None
        assert coordinator.job_record(jobs[-1]) is not None
    finally:
        coordinator.shutdown()


def test_shutdown_is_idempotent_rejects_new_work_and_leaves_no_owned_thread() -> None:
    baseline = {
        thread.ident
        for thread in enumerate_threads()
        if thread.name.startswith("hms-lathe-toolpath")
    }
    coordinator = LatheToolpathCoordinator()
    request = ready_request()[2]
    _submit_and_wait(coordinator, request)
    coordinator.shutdown(wait=True)
    coordinator.shutdown(wait=True)
    after = {
        thread.ident
        for thread in enumerate_threads()
        if thread.name.startswith("hms-lathe-toolpath")
    }
    assert after == baseline
    rejected = coordinator.submit(same_semantics_request(request))
    assert not rejected.accepted
    assert rejected.decision is LatheSubmissionDecision.CLOSED
    assert coordinator.cancel(request.job_id) is LatheCancelDecision.CLOSED
