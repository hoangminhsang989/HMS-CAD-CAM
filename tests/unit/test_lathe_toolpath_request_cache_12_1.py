"""Request guards, semantic identity and bounded cache tests for Stage 12.1."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.commands import (
    BindLatheGeometry,
    BindLatheTool,
    SetLatheOperationEnabled,
)
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
    LatheInMemoryToolpathCache,
    LatheToolpathDiagnosticCode,
    LatheToolpathJobId,
    LatheToolpathRequestBuilder,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheStrategyId,
    LatheToolCapability,
)
from tests.unit._lathe_fixtures import (
    capability_resolution,
    complete_operation,
    create_operation,
    service_for,
    session,
    stable_uuid,
    tool_reference,
)
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    next_revision,
    ready_request,
    same_semantics_request,
    stock_snapshot,
)


STAGE12_1_EXECUTABLE = (
    LatheStrategyId.OD_ROUGH,
    LatheStrategyId.OD_FINISH,
    LatheStrategyId.AXIAL_DRILL,
)


def _build(
    service: LatheOperationService,
    operation,
    *,
    stock=None,
    revision: Revision | None = None,
):
    return LatheToolpathRequestBuilder().build(
        service=service,
        operation_id=operation.ownership.operation_id,
        expected_revision=revision or operation.revision,
        stock=stock if stock is not None else stock_snapshot(),
        job_id=LatheToolpathJobId.new(),
        request_sequence=1,
    )


@pytest.mark.parametrize("strategy_id", STAGE12_1_EXECUTABLE)
def test_builder_accepts_stage12_1_ready_executable_strategies(
    strategy_id: LatheStrategyId,
) -> None:
    _service, operation, request = ready_request(strategy_id)
    assert request.strategy_id is strategy_id
    assert request.ownership == operation.ownership
    assert request.operation.revision == operation.revision
    assert request.fingerprint.contract_version == 1
    assert request.cache_key.contract_version == 1


def test_builder_rejects_revision_mismatch_stale_ownership_and_stale_stock() -> None:
    service, reference = service_for(LatheStrategyId.OD_ROUGH)
    operation = complete_operation(service, reference, LatheStrategyId.OD_ROUGH)
    mismatch = _build(service, operation, revision=next_revision(operation.revision))
    assert mismatch.diagnostics[0].code is LatheToolpathDiagnosticCode.REVISION_MISMATCH

    service.switch_source(stable_uuid("source/2"), 0)
    stale = _build(service, operation)
    assert stale.diagnostics[0].code is LatheToolpathDiagnosticCode.STALE_OWNERSHIP

    current_service, current_reference = service_for(LatheStrategyId.OD_ROUGH)
    current = complete_operation(
        current_service, current_reference, LatheStrategyId.OD_ROUGH
    )
    stale_stock = _build(
        current_service,
        current,
        stock=stock_snapshot(generation=4),
    )
    assert stale_stock.diagnostics[0].code is LatheToolpathDiagnosticCode.STALE_OWNERSHIP


def test_builder_rejects_disabled_read_only_closed_and_missing_stock() -> None:
    service, reference = service_for(LatheStrategyId.OD_FINISH)
    operation = complete_operation(service, reference, LatheStrategyId.OD_FINISH)
    disabled_outcome = service.execute(
        SetLatheOperationEnabled(operation.ownership, False, operation.revision)
    )
    assert disabled_outcome.operation is not None
    disabled = _build(service, disabled_outcome.operation)
    assert disabled.diagnostics[0].code is LatheToolpathDiagnosticCode.DISABLED_OPERATION

    writable, reference = service_for(LatheStrategyId.OD_FINISH)
    ready = complete_operation(writable, reference, LatheStrategyId.OD_FINISH)
    writable.set_read_only(True)
    read_only = _build(writable, ready)
    assert read_only.diagnostics[0].code is LatheToolpathDiagnosticCode.READ_ONLY
    writable.set_read_only(False)
    missing_stock = LatheToolpathRequestBuilder().build(
        service=writable,
        operation_id=ready.ownership.operation_id,
        expected_revision=ready.revision,
        stock=None,
        job_id=LatheToolpathJobId.new(),
        request_sequence=1,
    )
    assert missing_stock.diagnostics[0].code is LatheToolpathDiagnosticCode.INVALID_STOCK
    writable.close()
    closed = _build(writable, ready)
    assert closed.diagnostics[0].code is LatheToolpathDiagnosticCode.CLOSED


def test_builder_rejects_missing_geometry_and_missing_tool_via_service_readiness() -> None:
    service, _reference = service_for(LatheStrategyId.OD_ROUGH)
    incomplete = create_operation(service, LatheStrategyId.OD_ROUGH)
    missing_geometry = _build(service, incomplete)
    assert missing_geometry.diagnostics[0].code is LatheToolpathDiagnosticCode.MISSING_GEOMETRY

    geometry_kind = lathe_strategy_definition(
        LatheStrategyId.OD_ROUGH
    ).allowed_geometry_kinds[0]
    bound = service.execute(
        BindLatheGeometry(
            incomplete.ownership,
            LatheGeometryBinding(
                geometry_kind,
                ("profile-1",),
                incomplete.ownership.source_id,
                incomplete.ownership.generation,
            ),
            incomplete.revision,
        )
    )
    assert bound.operation is not None
    missing_tool = _build(service, bound.operation)
    assert missing_tool.diagnostics[0].code is LatheToolpathDiagnosticCode.MISSING_TOOL


class _MutableCapabilityResolver:
    def __init__(
        self,
        reference: LatheToolReference,
        capability: LatheToolCapability,
    ) -> None:
        self.reference = reference
        self.capability = capability
        self.current = True

    def resolve(self, reference: LatheToolReference):
        if reference != self.reference:
            return None
        return capability_resolution(
            self.capability,
            reference=reference,
            current=self.current,
        )


def test_builder_copies_resolved_capability_and_service_blocks_mismatch() -> None:
    service, reference = service_for(LatheStrategyId.OD_ROUGH)
    operation = complete_operation(service, reference, LatheStrategyId.OD_ROUGH)
    built = _build(service, operation)
    assert built.request is not None
    assert built.request.operation.tool_binding.resolved_capabilities == frozenset(
        {LatheToolCapability.OD_TURNING}
    )

    incompatible_service, incompatible_reference = service_for(
        LatheStrategyId.OD_ROUGH,
        capability=LatheToolCapability.FACE_TURNING,
    )
    incomplete = create_operation(incompatible_service, LatheStrategyId.OD_ROUGH)
    geometry_kind = lathe_strategy_definition(
        LatheStrategyId.OD_ROUGH
    ).allowed_geometry_kinds[0]
    geometry = incompatible_service.execute(
        BindLatheGeometry(
            incomplete.ownership,
            LatheGeometryBinding(
                geometry_kind,
                ("profile-1",),
                incomplete.ownership.source_id,
                incomplete.ownership.generation,
            ),
            incomplete.revision,
        )
    )
    assert geometry.operation is not None
    rejected_binding = incompatible_service.execute(
        BindLatheTool(
            geometry.operation.ownership,
            incompatible_reference,
            geometry.operation.revision,
        )
    )
    assert not rejected_binding.accepted
    assert rejected_binding.diagnostics[0].code is LatheDiagnosticCode.INCOMPATIBLE_TOOL
    rejected_request = _build(incompatible_service, geometry.operation)
    assert rejected_request.diagnostics[0].code is LatheToolpathDiagnosticCode.MISSING_TOOL


@pytest.mark.parametrize("strategy_id", UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES)
def test_builder_fails_closed_for_thread_strategies(
    strategy_id: LatheStrategyId,
) -> None:
    service, reference = service_for(strategy_id)
    operation = complete_operation(service, reference, strategy_id)
    built = _build(service, operation)
    assert not built.accepted and built.request is None
    assert built.diagnostics[0].code is (
        LatheToolpathDiagnosticCode.THREAD_TOOLPATH_NOT_IMPLEMENTED_V2
    )
    assert dict(built.diagnostics[0].details)["strategy_id"] == strategy_id.value


def test_builder_rejects_target_and_depth_outside_live_stock() -> None:
    rough_service, rough_reference = service_for(LatheStrategyId.OD_ROUGH)
    rough = complete_operation(
        rough_service, rough_reference, LatheStrategyId.OD_ROUGH
    )
    invalid_target = _build(
        rough_service,
        rough,
        stock=stock_snapshot(outer_diameter_mm=30.0),
    )
    assert invalid_target.diagnostics[0].field_id == "target_diameter_mm"

    drill_service, drill_reference = service_for(LatheStrategyId.AXIAL_DRILL)
    drill = complete_operation(
        drill_service, drill_reference, LatheStrategyId.AXIAL_DRILL
    )
    invalid_depth = _build(
        drill_service,
        drill,
        stock=stock_snapshot(back_z_mm=-20.0),
    )
    assert invalid_depth.diagnostics[0].field_id == "depth_mm"


def test_fingerprint_excludes_job_sequence_locale_theme_and_ui_state() -> None:
    _service, _operation, request = ready_request()
    repeated = same_semantics_request(request, request_sequence=999)
    assert request.job_id != repeated.job_id
    assert request.request_sequence != repeated.request_sequence
    assert request.fingerprint == repeated.fingerprint
    assert request.cache_key == repeated.cache_key
    request_fields = {item.name for item in fields(request)}
    assert request_fields.isdisjoint(
        {"language", "locale", "theme", "ui_scale", "timestamp", "actor"}
    )


def test_semantic_parameter_tool_geometry_stock_and_generation_changes_invalidate() -> None:
    _service, _operation, baseline = ready_request()
    _service, _operation, parameter = ready_request(
        parameters={"feed_mm_per_rev": 0.33}
    )
    _service, _operation, tool = ready_request(tool_index=2)
    _service, _operation, geometry = ready_request(entity_ids=("entity-other",))
    _service, _operation, changed_stock = ready_request(
        stock=stock_snapshot(identity="other-stock", outer_diameter_mm=102.0)
    )

    live_generation = session(generation=4)
    generation_service, reference = service_for(
        LatheStrategyId.OD_ROUGH,
        live_session=live_generation,
    )
    generation_operation = complete_operation(
        generation_service,
        reference,
        LatheStrategyId.OD_ROUGH,
    )
    generation_built = LatheToolpathRequestBuilder().build(
        service=generation_service,
        operation_id=generation_operation.ownership.operation_id,
        expected_revision=generation_operation.revision,
        stock=stock_snapshot(generation=4),
        job_id=LatheToolpathJobId.new(),
        request_sequence=1,
    )
    assert generation_built.request is not None

    variants = (
        parameter,
        tool,
        geometry,
        changed_stock,
        generation_built.request,
    )
    assert all(item.fingerprint != baseline.fingerprint for item in variants)
    assert all(item.cache_key != baseline.cache_key for item in variants)


def test_cache_hit_is_exact_immutable_and_same_semantics_can_reuse() -> None:
    _service, _operation, request = ready_request()
    result = generate(request)
    cache = LatheInMemoryToolpathCache(max_entries=2)
    entry = cache.put(request, result)
    repeated = same_semantics_request(request)
    assert entry.result is result
    assert cache.get(repeated) is result
    assert cache.size == 1


def test_cache_fifo_eviction_capacity_and_semantic_invalidation_are_deterministic() -> None:
    cache = LatheInMemoryToolpathCache(max_entries=2)
    requests = tuple(ready_request(operation_index=index)[2] for index in (1, 2, 3))
    for request in requests:
        cache.put(request, generate(request))
    assert cache.size == 2
    assert cache.get(requests[0]) is None
    assert cache.get(requests[1]) is not None
    assert cache.get(requests[2]) is not None

    cache = LatheInMemoryToolpathCache(max_entries=2)
    baseline = ready_request()[2]
    changed = ready_request(parameters={"feed_mm_per_rev": 0.31})[2]
    cache.put(baseline, generate(baseline))
    assert cache.invalidate_for_request(changed) == 1
    assert cache.get(baseline) is None


def test_cache_operations_create_no_disk_artifact(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    request = ready_request()[2]
    cache = LatheInMemoryToolpathCache(max_entries=1)
    cache.put(request, generate(request))
    assert cache.get(same_semantics_request(request)) is not None
    cache.clear()
    assert tuple(tmp_path.iterdir()) == before
