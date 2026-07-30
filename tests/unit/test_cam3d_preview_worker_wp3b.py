from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

import pytest

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DCancelDecision,
    Cam3DInMemoryPreviewCache,
    Cam3DJobExecutionState,
    Cam3DPreviewCompletionState,
    Cam3DPreviewCoordinator,
    Cam3DPreviewDiagnosticCode,
    Cam3DPreviewMesh,
    Cam3DPreviewSource,
    Cam3DSubmissionDecision,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DActiveSetupContext,
    Cam3DCalculationInputSnapshot,
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationPolicy,
    Cam3DCalculationRequestContract,
    Cam3DPreviewCacheKey,
    Cam3DRequestFingerprint,
    Cam3DSessionDecision,
    Cam3DZoneInputSnapshot,
)
from hms_cadcam.cam.cam3d import (
    Cam3DCancelledError,
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DMeshError,
)
from hms_cadcam.cam.domain import (
    DependencyFingerprint,
    LengthUnit,
    Revision,
    SetupId,
    ToolAssemblyId,
    ToolProgramProfileId,
    WcsFrame,
)
from tests.unit._cam3d_fixtures import surface


def _request(
    *,
    ownership: Cam3DCalculationOwnershipKey | None = None,
    generation: int = 4,
    semantic: int = 0,
    job_id: Cam3DCalculationJobId | None = None,
) -> Cam3DCalculationRequestContract:
    if ownership is None:
        ownership = Cam3DCalculationOwnershipKey(
            uuid4(), CadDocumentId("wp3b-document"), uuid4(), SetupId.new()
        )
    setup = Cam3DActiveSetupContext(
        ownership,
        generation,
        Revision(2),
        WcsFrame.identity(LengthUnit.MM),
    )
    selected = surface(
        ownership.project_id,
        ownership.source_id,
        f"face-{semantic}",
        revision=Revision(0),
    )
    zone = Cam3DZoneInputSnapshot(ownership, generation, (selected,))
    inputs = Cam3DCalculationInputSnapshot(
        setup,
        zone,
        ToolAssemblyId.new(),
        DependencyFingerprint.from_payload({"assembly": semantic}),
        ToolProgramProfileId.new(),
        DependencyFingerprint.from_payload({"profile": semantic}),
        0.01 + semantic * 0.001,
        float(semantic),
        None,
        None,
        2.0,
        0.0,
        Cam3DCalculationPolicy(),
    )
    fingerprint = Cam3DRequestFingerprint.from_inputs(inputs)
    return Cam3DCalculationRequestContract(
        job_id or Cam3DCalculationJobId.new(),
        inputs,
        fingerprint,
        Cam3DPreviewCacheKey.from_request_fingerprint(
            fingerprint, inputs.policy
        ),
    )


def _same_semantics(
    request: Cam3DCalculationRequestContract,
    *,
    job_id: Cam3DCalculationJobId | None = None,
) -> Cam3DCalculationRequestContract:
    return replace(request, job_id=job_id or Cam3DCalculationJobId.new())


def _mesh(offset: float = 0.0) -> Cam3DPreviewMesh:
    return Cam3DPreviewMesh(
        (
            (offset, 0.0, 0.0),
            (offset + 1.0, 0.0, 0.0),
            (offset, 1.0, 0.0),
        ),
        ((0, 1, 2),),
        ((0.0, 0.0, 1.0),),
        (offset, 0.0, 0.0, offset + 1.0, 1.0, 0.0),
    )


class _ImmediateTessellator:
    def __init__(self, mesh: Cam3DPreviewMesh | None = None) -> None:
        self.mesh = mesh or _mesh()
        self.calls = 0
        self.threads: list[int] = []
        self._lock = Lock()

    def tessellate(self, request, cancellation):
        import threading

        with self._lock:
            self.calls += 1
            self.threads.append(threading.get_ident())
        if cancellation():
            raise _cancelled_error()
        return self.mesh


class _BlockingTessellator:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cancelled_seen = Event()

    def tessellate(self, request, cancellation):
        self.started.set()
        while not self.release.wait(0.01):
            if cancellation():
                self.cancelled_seen.set()
                raise _cancelled_error()
        if cancellation():
            self.cancelled_seen.set()
            raise _cancelled_error()
        return _mesh()


class _FirstBlockingTessellator:
    def __init__(self, first_job: Cam3DCalculationJobId) -> None:
        self.first_job = first_job
        self.first_started = Event()
        self.first_cancelled = Event()

    def tessellate(self, request, cancellation):
        if request.job_id != self.first_job:
            return _mesh(2.0)
        self.first_started.set()
        while not cancellation():
            self.first_cancelled.wait(0.01)
        self.first_cancelled.set()
        raise _cancelled_error()


class _FailingTessellator:
    def tessellate(self, request, cancellation):
        raise Cam3DMeshError(
            Cam3DDiagnostic(
                Cam3DDiagnosticCode.SURFACE_MISSING,
                Cam3DDiagnosticSeverity.ERROR,
                "raw native failure must not escape",
            )
        )


def _cancelled_error() -> Cam3DCancelledError:
    return Cam3DCancelledError(
        Cam3DDiagnostic(
            Cam3DDiagnosticCode.CANCELLED,
            Cam3DDiagnosticSeverity.WARNING,
            "cancelled",
        )
    )


def _submit_and_wait(
    coordinator: Cam3DPreviewCoordinator,
    request: Cam3DCalculationRequestContract,
):
    delivered = []
    finished = Event()

    def callback(result) -> None:
        delivered.append(result)
        finished.set()

    receipt = coordinator.submit(request, callback=callback)
    assert receipt.accepted
    assert finished.wait(5.0)
    return receipt, delivered[0]


def test_valid_submission_runs_off_caller_thread_and_publishes_immutable_mesh() -> None:
    import threading

    request = _request()
    tessellator = _ImmediateTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    try:
        receipt, result = _submit_and_wait(coordinator, request)
        assert receipt.decision is Cam3DSubmissionDecision.ACCEPTED
        assert receipt.scheduled
        assert tessellator.threads != [threading.get_ident()]
        assert result.state is Cam3DPreviewCompletionState.SUCCEEDED
        assert result.source is Cam3DPreviewSource.WORKER
        assert result.vertex_count == 3 and result.triangle_count == 1
        assert result.mesh == _mesh()
        assert coordinator.delivery_authorized(result)
    finally:
        coordinator.shutdown()


def test_invalid_and_duplicate_submission_are_rejected_before_worker() -> None:
    tessellator = _BlockingTessellator()
    request = _request()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    try:
        invalid = coordinator.submit(object())
        assert not invalid.accepted
        assert invalid.decision is Cam3DSubmissionDecision.INVALID_REQUEST
        first = coordinator.submit(request)
        assert first.accepted and tessellator.started.wait(5.0)
        duplicate = coordinator.submit(request)
        assert not duplicate.accepted
        assert duplicate.decision is Cam3DSubmissionDecision.DUPLICATE_REQUEST
    finally:
        tessellator.release.set()
        coordinator.shutdown()


def test_same_owner_new_job_supersedes_old_and_only_latest_delivers() -> None:
    first = _request()
    second = _request(ownership=first.ownership, semantic=1)
    tessellator = _FirstBlockingTessellator(first.job_id)
    coordinator = Cam3DPreviewCoordinator(tessellator, max_workers=2)
    delivered = []
    latest_finished = Event()
    try:
        coordinator.submit(first, callback=delivered.append)
        assert tessellator.first_started.wait(5.0)
        coordinator.submit(
            second,
            callback=lambda result: (delivered.append(result), latest_finished.set()),
        )
        assert latest_finished.wait(5.0)
        assert tessellator.first_cancelled.wait(5.0)
        assert [item.identity.job_id for item in delivered] == [second.job_id]
        assert coordinator.job_record(first.job_id).state is Cam3DJobExecutionState.DROPPED
        assert coordinator.job_record(first.job_id).publication_decision is (
            Cam3DSessionDecision.SUPERSEDED
        )
    finally:
        coordinator.shutdown()


def test_independent_ownerships_execute_and_cancel_in_isolation() -> None:
    first = _request()
    second = _request()
    tessellator = _FirstBlockingTessellator(first.job_id)
    coordinator = Cam3DPreviewCoordinator(tessellator, max_workers=2)
    second_finished = Event()
    delivered = []
    try:
        coordinator.submit(first, callback=delivered.append)
        assert tessellator.first_started.wait(5.0)
        coordinator.submit(
            second,
            callback=lambda result: (delivered.append(result), second_finished.set()),
        )
        assert second_finished.wait(5.0)
        assert coordinator.cancel(first.job_id) is Cam3DCancelDecision.REQUESTED
        assert tessellator.first_cancelled.wait(5.0)
        assert [item.identity.job_id for item in delivered] == [second.job_id]
        assert coordinator.delivery_authorized(delivered[0])
    finally:
        coordinator.shutdown()


def test_cancel_running_repeated_and_after_complete_are_deterministic() -> None:
    request = _request()
    tessellator = _BlockingTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    try:
        coordinator.submit(request)
        assert tessellator.started.wait(5.0)
        assert coordinator.cancel(request.job_id) is Cam3DCancelDecision.REQUESTED
        assert coordinator.cancel(request.job_id) is Cam3DCancelDecision.ALREADY_CANCELLED
        assert tessellator.cancelled_seen.wait(5.0)
        coordinator.shutdown()
        assert coordinator.job_record(request.job_id).state is Cam3DJobExecutionState.DROPPED
        assert coordinator.cancel(request.job_id) is Cam3DCancelDecision.ALREADY_COMPLETED
    finally:
        tessellator.release.set()
        coordinator.shutdown()


def test_cancel_queued_before_start_never_calls_tessellator_for_that_job() -> None:
    blocker = _BlockingTessellator()
    first = _request()
    second = _request()
    coordinator = Cam3DPreviewCoordinator(blocker, max_workers=1)
    try:
        coordinator.submit(first)
        assert blocker.started.wait(5.0)
        coordinator.submit(second)
        assert coordinator.cancel(second.job_id) is Cam3DCancelDecision.REQUESTED
        record = coordinator.job_record(second.job_id)
        assert record is not None
        blocker.release.set()
        coordinator.shutdown()
        assert coordinator.job_record(second.job_id).state is Cam3DJobExecutionState.DROPPED
    finally:
        blocker.release.set()
        coordinator.shutdown()


def test_close_and_switch_cancel_old_ownership_and_clear_only_its_cache() -> None:
    old = _request()
    new = _request()
    cache = Cam3DInMemoryPreviewCache(4)
    cache.put(new, _mesh(2.0))
    tessellator = _BlockingTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator, cache=cache)
    try:
        coordinator.submit(old)
        assert tessellator.started.wait(5.0)
        cache.put(old, _mesh())
        coordinator.switch_ownership(old.ownership, new.ownership, new.project_generation)
        assert tessellator.cancelled_seen.wait(5.0)
        assert cache.get(old) is None
        assert cache.get(new) == _mesh(2.0)
        assert coordinator.submit(old).decision is Cam3DSubmissionDecision.CLOSED
    finally:
        tessellator.release.set()
        coordinator.shutdown()


def test_cache_hit_retargets_job_and_still_uses_latest_wins_acceptance() -> None:
    request = _request()
    tessellator = _ImmediateTessellator()
    coordinator = Cam3DPreviewCoordinator(tessellator)
    try:
        _submit_and_wait(coordinator, request)
        cached_request = _same_semantics(request)
        receipt, cached = _submit_and_wait(coordinator, cached_request)
        assert receipt.decision is Cam3DSubmissionDecision.CACHE_HIT
        assert not receipt.scheduled
        assert cached.source is Cam3DPreviewSource.CACHE
        assert cached.identity.job_id == cached_request.job_id
        assert tessellator.calls == 1
        coordinator.close_ownership(cached_request.ownership)
        assert not coordinator.delivery_authorized(cached)
    finally:
        coordinator.shutdown()


def test_bounded_fifo_cache_and_semantic_invalidation_are_deterministic() -> None:
    owner = _request().ownership
    first = _request(ownership=owner, semantic=0)
    second = _request(ownership=owner, semantic=1)
    third = _request(semantic=2)
    cache = Cam3DInMemoryPreviewCache(2)
    cache.put(first, _mesh())
    cache.put(third, _mesh(3.0))
    assert cache.get(first) == _mesh()
    cache.put(second, _mesh(2.0))
    assert cache.size == 2
    assert cache.get(first) is None
    assert cache.get(third) == _mesh(3.0)
    assert cache.get(second) == _mesh(2.0)
    assert cache.invalidate_for_request(first) == 1
    assert cache.get(second) is None
    cache.put(second, _mesh(2.0))
    newer_generation = _request(ownership=owner, generation=5, semantic=1)
    assert cache.invalidate_for_request(newer_generation) == 1
    assert cache.get(second) is None
    cache.clear()
    cache.clear()
    assert cache.size == 0


def test_generation_invalidation_requires_explicit_rebind() -> None:
    request = _request()
    newer = _request(ownership=request.ownership, generation=5)
    coordinator = Cam3DPreviewCoordinator(_ImmediateTessellator())
    try:
        _submit_and_wait(coordinator, request)
        rejected = coordinator.submit(newer)
        assert not rejected.accepted
        assert rejected.decision is Cam3DSubmissionDecision.STALE_GENERATION
        coordinator.bind_session(newer.ownership, newer.project_generation)
        _submit_and_wait(coordinator, newer)
    finally:
        coordinator.shutdown()


def test_worker_failure_maps_to_typed_localization_neutral_diagnostic() -> None:
    coordinator = Cam3DPreviewCoordinator(_FailingTessellator())
    try:
        _receipt, result = _submit_and_wait(coordinator, _request())
        assert result.state is Cam3DPreviewCompletionState.FAILED
        assert result.mesh is None
        assert result.diagnostic is not None
        assert result.diagnostic.code is Cam3DPreviewDiagnosticCode.GEOMETRY_UNAVAILABLE
        assert dict(result.diagnostic.details) == {
            "native_code": Cam3DDiagnosticCode.SURFACE_MISSING.value
        }
        assert "raw native failure" not in repr(result)
    finally:
        coordinator.shutdown()


def test_callback_is_outside_lock_and_callback_exception_does_not_corrupt_state() -> None:
    first = _request()
    second = _request()
    coordinator = Cam3DPreviewCoordinator(_ImmediateTessellator(), max_workers=2)
    callback_entered = Event()

    def failing_callback(result) -> None:
        assert coordinator.job_record(result.identity.job_id) is not None
        accepted = coordinator.submit(second)
        assert accepted.accepted
        callback_entered.set()
        raise RuntimeError("expected callback failure")

    try:
        coordinator.submit(first, callback=failing_callback)
        assert callback_entered.wait(5.0)
        receipt, result = _submit_and_wait(coordinator, _same_semantics(second))
        assert receipt.accepted
        assert result.state is Cam3DPreviewCompletionState.SUCCEEDED
        assert coordinator.job_record(first.job_id).callback_invoked
    finally:
        coordinator.shutdown()


@pytest.mark.parametrize(
    "bad_mesh",
    [
        lambda: Cam3DPreviewMesh(
            ((0.0, 0.0, float("nan")), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMesh(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 3),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMesh(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),),
            ((0.0, 0.0, 2.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
    ],
)
def test_preview_mesh_rejects_non_finite_index_and_normal_payloads(bad_mesh) -> None:
    with pytest.raises(ValueError):
        bad_mesh()


def test_core_module_has_no_qt_ocp_filesystem_database_or_process_import() -> None:
    path = Path("src/hms_cadcam/cam/application/cam3d_preview.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith(("PySide6", "OCP")) for name in imported)
    assert {"pathlib", "sqlite3", "multiprocessing", "subprocess"}.isdisjoint(
        {name.split(".")[0] for name in imported}
    )
