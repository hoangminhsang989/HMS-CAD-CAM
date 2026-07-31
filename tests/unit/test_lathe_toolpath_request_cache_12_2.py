"""Stage 12.2 request, readiness, fingerprint, cache and worker tests."""

from __future__ import annotations

from threading import Event

import pytest

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.commands import UpdateLatheParameters
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.toolpath import (
    LatheInMemoryToolpathCache,
    LatheSubmissionDecision,
    LatheToolpathCoordinator,
    LatheToolpathDiagnosticCode,
    LatheToolpathJobId,
    LatheToolpathRequestBuilder,
    strategy_algorithm_version,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheToolCapability
from tests.unit._lathe_fixtures import complete_operation, service_for
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    same_semantics_request,
    stock_snapshot,
)


NEW_STRATEGIES = (
    LatheStrategyId.FACE,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
)


def _valid_parameters(strategy_id: LatheStrategyId) -> dict[str, object]:
    if strategy_id is LatheStrategyId.FACE:
        return {
            "face_z_mm": -2.0,
            "outer_diameter_mm": 80.0,
            "inner_diameter_mm": 0.0,
            "max_depth_of_cut_mm": 0.75,
            "finish_allowance_mm": 0.25,
        }
    return {}


def _valid_stock(strategy_id: LatheStrategyId):
    if strategy_id in {
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.ID_GROOVE,
    }:
        return stock_snapshot(inner_diameter_mm=10.0)
    return stock_snapshot()


def _ready(strategy_id: LatheStrategyId):
    return ready_request(
        strategy_id,
        parameters=_valid_parameters(strategy_id),
        stock=_valid_stock(strategy_id),
    )[2]


def _build(
    strategy_id: LatheStrategyId,
    *,
    parameters: dict[str, object] | None = None,
    stock=None,
):
    service, reference = service_for(strategy_id)
    operation = complete_operation(service, reference, strategy_id)
    updates = {**_valid_parameters(strategy_id), **(parameters or {})}
    if updates:
        outcome = service.execute(
            UpdateLatheParameters(
                operation.ownership,
                tuple(
                    LatheParameterUpdate(name, value)
                    for name, value in updates.items()
                ),
                operation.revision,
            )
        )
        assert outcome.accepted and outcome.operation is not None
        operation = outcome.operation
    result = LatheToolpathRequestBuilder().build(
        service=service,
        operation_id=operation.ownership.operation_id,
        expected_revision=operation.revision,
        stock=stock if stock is not None else _valid_stock(strategy_id),
        job_id=LatheToolpathJobId.new(),
        request_sequence=1,
    )
    return service, operation, result


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_builder_accepts_all_six_new_ready_strategies_with_foundation_capability(
    strategy_id: LatheStrategyId,
) -> None:
    service, operation, built = _build(strategy_id)
    assert built.accepted and built.request is not None
    definition = lathe_strategy_definition(strategy_id)
    assert operation.tool_binding is not None
    assert operation.tool_binding.resolved_capabilities == (
        definition.required_tool_capabilities
    )
    assert built.request.algorithm_version == strategy_algorithm_version(strategy_id)
    assert built.request.operation.revision == operation.revision
    assert service.evaluate(operation.ownership.operation_id).readiness.value == "READY"


@pytest.mark.parametrize(
    "strategy_id",
    (
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.ID_GROOVE,
    ),
)
def test_internal_builder_rejects_missing_bore_without_inference(
    strategy_id: LatheStrategyId,
) -> None:
    _service, _operation, built = _build(
        strategy_id,
        stock=stock_snapshot(inner_diameter_mm=0.0),
    )
    assert not built.accepted and built.request is None
    assert built.diagnostics[0].code is (
        LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE
    )
    assert dict(built.diagnostics[0].details)["strategy_id"] == strategy_id.value


@pytest.mark.parametrize(
    "strategy_id",
    (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD),
)
def test_thread_strategies_delegate_to_stage12_3_request_builder(
    strategy_id: LatheStrategyId,
) -> None:
    stock = stock_snapshot(
        inner_diameter_mm=10.0
        if strategy_id is LatheStrategyId.ID_THREAD
        else 0.0
    )
    _service, _operation, built = _build(strategy_id, stock=stock)
    assert built.accepted and built.request is not None
    assert built.request.algorithm_version.endswith(".toolpath.v3")


@pytest.mark.parametrize(
    ("strategy_id", "parameters", "stock", "field_id"),
    (
        (
            LatheStrategyId.FACE,
            {"face_z_mm": -101.0},
            stock_snapshot(),
            "face_z_mm",
        ),
        (
            LatheStrategyId.FACE,
            {"face_z_mm": -0.1, "finish_allowance_mm": 0.2},
            stock_snapshot(),
            "finish_allowance_mm",
        ),
        (
            LatheStrategyId.FACE,
            {"outer_diameter_mm": 101.0},
            stock_snapshot(),
            "outer_diameter_mm",
        ),
        (
            LatheStrategyId.FACE,
            {"inner_diameter_mm": 5.0},
            stock_snapshot(inner_diameter_mm=10.0),
            "inner_diameter_mm",
        ),
        (
            LatheStrategyId.ID_ROUGH,
            {"target_diameter_mm": 10.0},
            stock_snapshot(inner_diameter_mm=10.0),
            "target_diameter_mm",
        ),
        (
            LatheStrategyId.ID_FINISH,
            {"target_diameter_mm": 100.0},
            stock_snapshot(inner_diameter_mm=10.0),
            "target_diameter_mm",
        ),
        (
            LatheStrategyId.ID_ROUGH,
            {
                "target_diameter_mm": 20.0,
                "radial_stock_to_leave_mm": 6.0,
            },
            stock_snapshot(inner_diameter_mm=10.0),
            "radial_stock_to_leave_mm",
        ),
        (
            LatheStrategyId.ID_ROUGH,
            {"axial_stock_to_leave_mm": 31.0},
            stock_snapshot(inner_diameter_mm=10.0),
            "axial_stock_to_leave_mm",
        ),
        (
            LatheStrategyId.OD_GROOVE,
            {"side_allowance_mm": 2.0},
            stock_snapshot(),
            "side_allowance_mm",
        ),
        (
            LatheStrategyId.ID_GROOVE,
            {"center_z_mm": -99.5},
            stock_snapshot(inner_diameter_mm=10.0),
            "center_z_mm",
        ),
        (
            LatheStrategyId.PART_OFF,
            {"target_diameter_mm": 0.0},
            stock_snapshot(inner_diameter_mm=10.0),
            "target_diameter_mm",
        ),
        (
            LatheStrategyId.PART_OFF,
            {"cutoff_z_mm": -101.0},
            stock_snapshot(),
            "cutoff_z_mm",
        ),
        (
            LatheStrategyId.PART_OFF,
            {"cutoff_z_mm": 0.0, "side_clearance_mm": 0.2},
            stock_snapshot(),
            "side_clearance_mm",
        ),
    ),
)
def test_builder_rejects_stage12_2_stock_envelope_violations(
    strategy_id: LatheStrategyId,
    parameters: dict[str, object],
    stock,
    field_id: str,
) -> None:
    _service, _operation, built = _build(
        strategy_id,
        parameters=parameters,
        stock=stock,
    )
    assert not built.accepted
    assert built.diagnostics[0].code is LatheToolpathDiagnosticCode.INVALID_PARAMETER
    assert built.diagnostics[0].field_id == field_id


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_fingerprint_cache_and_algorithm_version_are_deterministic_for_six(
    strategy_id: LatheStrategyId,
) -> None:
    request = _ready(strategy_id)
    repeated = same_semantics_request(request)
    assert request.fingerprint == repeated.fingerprint
    assert request.cache_key == repeated.cache_key
    assert request.algorithm_version == strategy_algorithm_version(strategy_id)
    result = generate(request)
    cache = LatheInMemoryToolpathCache(max_entries=9)
    cache.put(request, result)
    assert cache.get(repeated) is result


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_parameter_and_stock_changes_invalidate_new_strategy_fingerprint(
    strategy_id: LatheStrategyId,
) -> None:
    baseline = _ready(strategy_id)
    changed_parameters = {
        LatheStrategyId.FACE: {"feed_mm_per_rev": 0.31},
        LatheStrategyId.ID_ROUGH: {"feed_mm_per_rev": 0.31},
        LatheStrategyId.ID_FINISH: {"spring_passes": 1},
        LatheStrategyId.OD_GROOVE: {"max_step_mm": 0.8},
        LatheStrategyId.ID_GROOVE: {"max_step_mm": 0.8},
        LatheStrategyId.PART_OFF: {"max_step_mm": 2.0},
    }[strategy_id]
    changed = ready_request(
        strategy_id,
        parameters={**_valid_parameters(strategy_id), **changed_parameters},
        stock=_valid_stock(strategy_id),
    )[2]
    stock_changed = ready_request(
        strategy_id,
        parameters=_valid_parameters(strategy_id),
        stock=stock_snapshot(
            inner_diameter_mm=(
                10.0
                if strategy_id in {
                    LatheStrategyId.ID_ROUGH,
                    LatheStrategyId.ID_FINISH,
                    LatheStrategyId.ID_GROOVE,
                }
                else 0.0
            ),
            identity="changed-stock",
        ),
    )[2]
    assert changed.fingerprint != baseline.fingerprint
    assert stock_changed.fingerprint != baseline.fingerprint


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_coordinator_runs_and_cache_hits_all_six_through_common_gate(
    strategy_id: LatheStrategyId,
) -> None:
    coordinator = LatheToolpathCoordinator()
    request = _ready(strategy_id)
    first_results = []
    first_done = Event()
    second_results = []
    second_done = Event()
    try:
        first = coordinator.submit(
            request,
            callback=lambda result: (first_results.append(result), first_done.set()),
        )
        assert first.accepted and first_done.wait(5.0)
        repeated = same_semantics_request(request)
        second = coordinator.submit(
            repeated,
            callback=lambda result: (second_results.append(result), second_done.set()),
        )
        assert second.accepted and second_done.wait(5.0)
        assert first.decision is LatheSubmissionDecision.ACCEPTED
        assert second.decision is LatheSubmissionDecision.CACHE_HIT
        assert second_results[0].motions is first_results[0].motions
        assert coordinator.delivery_authorized(second_results[0])
    finally:
        coordinator.shutdown(wait=True)


def test_revision_guard_still_precedes_stage12_2_submission() -> None:
    service, operation, _built = _build(LatheStrategyId.OD_GROOVE)
    rejected = LatheToolpathRequestBuilder().build(
        service=service,
        operation_id=operation.ownership.operation_id,
        expected_revision=Revision(operation.revision.value + 1),
        stock=stock_snapshot(),
        job_id=LatheToolpathJobId.new(),
        request_sequence=2,
    )
    assert rejected.diagnostics[0].code is (
        LatheToolpathDiagnosticCode.REVISION_MISMATCH
    )


def test_exact_capability_mapping_for_six_is_foundation_owned() -> None:
    expected = {
        LatheStrategyId.FACE: LatheToolCapability.FACE_TURNING,
        LatheStrategyId.ID_ROUGH: LatheToolCapability.ID_TURNING,
        LatheStrategyId.ID_FINISH: LatheToolCapability.ID_TURNING,
        LatheStrategyId.OD_GROOVE: LatheToolCapability.OD_GROOVING,
        LatheStrategyId.ID_GROOVE: LatheToolCapability.ID_GROOVING,
        LatheStrategyId.PART_OFF: LatheToolCapability.PARTING,
    }
    assert {
        strategy_id: next(
            iter(lathe_strategy_definition(strategy_id).required_tool_capabilities)
        )
        for strategy_id in NEW_STRATEGIES
    } == expected
