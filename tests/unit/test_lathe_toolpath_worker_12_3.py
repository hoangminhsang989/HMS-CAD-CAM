"""Stage 12.3 thread coordinator, cache and cancellation integration tests."""

from __future__ import annotations

from threading import Event, get_ident

import pytest

from hms_cadcam.cam.lathe.toolpath import (
    LatheCancelDecision,
    LatheSubmissionDecision,
    LatheToolpathCoordinator,
    LatheToolpathResultSource,
    LatheToolpathResultState,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_toolpath_fixtures import (
    ready_request,
    same_semantics_request,
    stock_snapshot,
)


def _request(strategy_id: LatheStrategyId):
    return ready_request(
        strategy_id,
        stock=stock_snapshot(
            inner_diameter_mm=10.0
            if strategy_id is LatheStrategyId.ID_THREAD
            else 0.0
        ),
    )[2]


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_thread_submission_and_cache_hit_use_common_delivery_gate(
    strategy_id: LatheStrategyId,
) -> None:
    coordinator = LatheToolpathCoordinator()
    request = _request(strategy_id)
    first_values = []
    first_done = Event()
    cached_values = []
    cached_done = Event()
    caller_thread = get_ident()
    try:
        first = coordinator.submit(
            request,
            callback=lambda result: (first_values.append(result), first_done.set()),
        )
        assert first.accepted and first.decision is LatheSubmissionDecision.ACCEPTED
        assert first_done.wait(5.0)
        assert first_values[0].state is LatheToolpathResultState.SUCCESS
        assert first_values[0].source is LatheToolpathResultSource.WORKER
        assert coordinator.delivery_authorized(first_values[0])

        repeated = same_semantics_request(request)
        cached = coordinator.submit(
            repeated,
            callback=lambda result: (cached_values.append(result), cached_done.set()),
        )
        assert cached.accepted and cached.decision is LatheSubmissionDecision.CACHE_HIT
        assert cached_done.wait(5.0)
        assert cached_values[0].source is LatheToolpathResultSource.CACHE
        assert cached_values[0].motions is first_values[0].motions
        assert cached_values[0].thread_passes is first_values[0].thread_passes
        assert coordinator.delivery_authorized(cached_values[0])
        assert caller_thread == get_ident()
    finally:
        coordinator.shutdown(wait=True)


def test_latest_thread_request_wins_for_exact_operation_owner() -> None:
    coordinator = LatheToolpathCoordinator()
    first = _request(LatheStrategyId.OD_THREAD)
    second = same_semantics_request(first)
    values = []
    done = Event()
    try:
        first_receipt = coordinator.submit(first, callback=values.append)
        second_receipt = coordinator.submit(
            second,
            callback=lambda result: (values.append(result), done.set()),
        )
        assert first_receipt.accepted and second_receipt.accepted
        assert done.wait(5.0)
        assert any(item.identity.job_id == second.job_id for item in values)
        assert coordinator.delivery_authorized(
            next(item for item in values if item.identity.job_id == second.job_id)
        )
    finally:
        coordinator.shutdown(wait=True)


def test_cancel_is_exact_repeatable_and_close_is_idempotent() -> None:
    coordinator = LatheToolpathCoordinator()
    request = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={"pass_count": 50_000, "spring_passes": 0},
        stock=stock_snapshot(),
    )[2]
    try:
        receipt = coordinator.submit(request)
        assert receipt.accepted
        first = coordinator.cancel(request.job_id)
        second = coordinator.cancel(request.job_id)
        assert first in {
            LatheCancelDecision.REQUESTED,
            LatheCancelDecision.ALREADY_COMPLETED,
        }
        assert second in {
            LatheCancelDecision.ALREADY_CANCELLED,
            LatheCancelDecision.ALREADY_COMPLETED,
            LatheCancelDecision.NOT_FOUND,
        }
        coordinator.close_ownership(request.ownership)
        coordinator.close_ownership(request.ownership)
    finally:
        coordinator.shutdown(wait=True)
        coordinator.shutdown(wait=True)
