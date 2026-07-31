"""Stage 12.3 thread request, validation, fingerprint and cache tests."""

from __future__ import annotations

import pytest

from hms_cadcam.cam.lathe.commands import UpdateLatheParameters
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.toolpath import (
    LatheInMemoryToolpathCache,
    LatheToolpathDiagnosticCode,
    LatheToolpathJobId,
    LatheToolpathRequestBuilder,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId, LatheThreadHand
from tests.unit._lathe_fixtures import complete_operation, service_for
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    same_semantics_request,
    stock_snapshot,
)


def _build(
    strategy_id: LatheStrategyId,
    *,
    parameters: dict[str, object] | None = None,
    stock=None,
):
    service, reference = service_for(strategy_id)
    operation = complete_operation(service, reference, strategy_id)
    if parameters:
        outcome = service.execute(
            UpdateLatheParameters(
                operation.ownership,
                tuple(
                    LatheParameterUpdate(name, value)
                    for name, value in parameters.items()
                ),
                operation.revision,
            )
        )
        assert outcome.accepted and outcome.operation is not None
        operation = outcome.operation
    built = LatheToolpathRequestBuilder().build(
        service=service,
        operation_id=operation.ownership.operation_id,
        expected_revision=operation.revision,
        stock=stock,
        job_id=LatheToolpathJobId.new(),
        request_sequence=1,
    )
    return service, operation, built


@pytest.mark.parametrize(
    ("strategy_id", "algorithm", "stock"),
    (
        (
            LatheStrategyId.OD_THREAD,
            "lathe.od_thread.toolpath.v3",
            stock_snapshot(),
        ),
        (
            LatheStrategyId.ID_THREAD,
            "lathe.id_thread.toolpath.v3",
            stock_snapshot(inner_diameter_mm=10.0),
        ),
    ),
)
def test_builder_accepts_valid_thread_requests(
    strategy_id: LatheStrategyId,
    algorithm: str,
    stock,
) -> None:
    _service, operation, built = _build(strategy_id, stock=stock)
    assert built.accepted and built.request is not None
    assert built.request.algorithm_version == algorithm
    assert built.request.operation.revision == operation.revision
    assert built.request.operation.parameters["pitch_mm"] == 1.5
    assert built.request.operation.parameters["thread_hand"] == "RIGHT"


def test_internal_thread_requires_explicit_bore_without_inference() -> None:
    _service, _operation, built = _build(
        LatheStrategyId.ID_THREAD,
        stock=stock_snapshot(inner_diameter_mm=0.0),
    )
    assert not built.accepted and built.request is None
    assert built.diagnostics[0].code is LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE


@pytest.mark.parametrize(
    ("strategy_id", "parameters", "stock", "code", "field_id"),
    (
        (
            LatheStrategyId.OD_THREAD,
            {"major_diameter_mm": 101.0},
            stock_snapshot(),
            LatheToolpathDiagnosticCode.THREAD_MAJOR_EXCEEDS_STOCK,
            "major_diameter_mm",
        ),
        (
            LatheStrategyId.OD_THREAD,
            {"minor_diameter_mm": 9.0},
            stock_snapshot(inner_diameter_mm=10.0),
            LatheToolpathDiagnosticCode.THREAD_MINOR_BELOW_BORE,
            "minor_diameter_mm",
        ),
        (
            LatheStrategyId.ID_THREAD,
            {"major_diameter_mm": 100.0},
            stock_snapshot(inner_diameter_mm=10.0),
            LatheToolpathDiagnosticCode.THREAD_MAJOR_EXCEEDS_STOCK,
            "major_diameter_mm",
        ),
        (
            LatheStrategyId.ID_THREAD,
            {"minor_diameter_mm": 9.0},
            stock_snapshot(inner_diameter_mm=10.0),
            LatheToolpathDiagnosticCode.THREAD_MINOR_BELOW_BORE,
            "minor_diameter_mm",
        ),
        (
            LatheStrategyId.OD_THREAD,
            {"start_z_mm": -101.0},
            stock_snapshot(),
            LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
            "start_z_mm",
        ),
        (
            LatheStrategyId.ID_THREAD,
            {"end_z_mm": -101.0},
            stock_snapshot(inner_diameter_mm=10.0),
            LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
            "start_z_mm",
        ),
    ),
)
def test_builder_returns_exact_thread_stock_diagnostics(
    strategy_id: LatheStrategyId,
    parameters: dict[str, object],
    stock,
    code: LatheToolpathDiagnosticCode,
    field_id: str,
) -> None:
    _service, _operation, built = _build(
        strategy_id,
        parameters=parameters,
        stock=stock,
    )
    assert not built.accepted and built.request is None
    assert built.diagnostics[0].code is code
    assert built.diagnostics[0].field_id == field_id


@pytest.mark.parametrize(
    ("parameter", "left", "right"),
    (
        ("major_diameter_mm", 20.0, 21.0),
        ("minor_diameter_mm", 18.0, 17.5),
        ("pitch_mm", 1.5, 2.0),
        ("thread_hand", LatheThreadHand.RIGHT, LatheThreadHand.LEFT),
        ("pass_count", 8, 5),
        ("spring_passes", 1, 2),
        ("infeed_angle_deg", 29.0, 0.0),
    ),
)
def test_each_thread_parameter_invalidates_fingerprint_and_cache(
    parameter: str,
    left: object,
    right: object,
) -> None:
    stock = stock_snapshot()
    first = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={parameter: left},
        stock=stock,
    )[2]
    changed = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={parameter: right},
        stock=stock,
    )[2]
    assert first.fingerprint != changed.fingerprint
    assert first.cache_key != changed.cache_key


def test_opposite_hands_have_distinct_identity_but_identical_phase_neutral_xz() -> None:
    right = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={"thread_hand": LatheThreadHand.RIGHT},
        stock=stock_snapshot(),
    )[2]
    left = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={"thread_hand": LatheThreadHand.LEFT},
        stock=stock_snapshot(),
    )[2]
    right_result = generate(right)
    left_result = generate(left)
    assert right.fingerprint != left.fingerprint
    assert right.cache_key != left.cache_key
    assert tuple(
        (item.motion_class, item.start, item.end, item.feed_mm_per_rev)
        for item in right_result.motions
    ) == tuple(
        (item.motion_class, item.start, item.end, item.feed_mm_per_rev)
        for item in left_result.motions
    )


@pytest.mark.parametrize(
    ("strategy_id", "stock"),
    (
        (LatheStrategyId.OD_THREAD, stock_snapshot()),
        (LatheStrategyId.ID_THREAD, stock_snapshot(inner_diameter_mm=10.0)),
    ),
)
def test_thread_cache_hit_reuses_immutable_semantic_result(
    strategy_id: LatheStrategyId,
    stock,
) -> None:
    request = ready_request(strategy_id, stock=stock)[2]
    repeated = same_semantics_request(request)
    result = generate(request)
    cache = LatheInMemoryToolpathCache(max_entries=2)
    cache.put(request, result)
    assert cache.get(repeated) is result
    assert repeated.fingerprint == request.fingerprint
    assert repeated.cache_key == request.cache_key
