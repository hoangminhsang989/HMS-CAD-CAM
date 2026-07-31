"""Public-flow fixtures for Stage 12.1 Lathe Toolpath Preview V1 tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.commands import BindLatheGeometry, UpdateLatheParameters
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding, LatheOperationState
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.toolpath.generators import LatheToolpathGeneratorRegistry
from hms_cadcam.cam.lathe.toolpath.model import (
    LathePathSegment,
    LatheToolpathJobId,
    LatheToolpathResult,
)
from hms_cadcam.cam.lathe.toolpath.request import (
    LatheToolpathRequestBuilder,
    LatheToolpathRequestV1,
)
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_fixtures import (
    complete_operation,
    service_for,
    stable_uuid,
    tool_reference,
)


def stock_snapshot(
    *,
    outer_diameter_mm: float = 100.0,
    inner_diameter_mm: float = 0.0,
    front_z_mm: float = 0.0,
    back_z_mm: float = -100.0,
    generation: int = 3,
    source_label: str = "source/1",
    identity: str = "stage12-1-stock",
) -> LatheStockSnapshotV1:
    return LatheStockSnapshotV1(
        identity,
        stable_uuid(source_label),
        generation,
        outer_diameter_mm,
        inner_diameter_mm,
        front_z_mm,
        back_z_mm,
    )


def ready_request(
    strategy_id: LatheStrategyId = LatheStrategyId.OD_ROUGH,
    *,
    parameters: Mapping[str, object] | None = None,
    stock: LatheStockSnapshotV1 | None = None,
    operation_index: int = 1,
    tool_index: int = 1,
    entity_ids: tuple[str, ...] | None = None,
    job_id: LatheToolpathJobId | None = None,
    request_sequence: int = 1,
) -> tuple[LatheOperationService, LatheOperationState, LatheToolpathRequestV1]:
    """Build through Stage 12 service commands and the production request builder."""

    reference = tool_reference(tool_index)
    service, reference = service_for(strategy_id, reference=reference)
    operation = complete_operation(
        service,
        reference,
        strategy_id,
        index=operation_index,
    )
    if entity_ids is not None:
        assert operation.geometry_binding is not None
        rebound = service.execute(
            BindLatheGeometry(
                operation.ownership,
                LatheGeometryBinding(
                    operation.geometry_binding.kind,
                    entity_ids,
                    operation.ownership.source_id,
                    operation.ownership.generation,
                ),
                operation.revision,
            )
        )
        if not rebound.accepted or rebound.operation is None:
            raise AssertionError("Fixture geometry rebind failed")
        operation = rebound.operation
    if parameters:
        updated = service.execute(
            UpdateLatheParameters(
                operation.ownership,
                tuple(
                    LatheParameterUpdate(parameter_id, value)
                    for parameter_id, value in parameters.items()
                ),
                operation.revision,
            )
        )
        if not updated.accepted or updated.operation is None:
            raise AssertionError("Fixture parameter update failed")
        operation = updated.operation
    selected_stock = stock or stock_snapshot()
    built = LatheToolpathRequestBuilder().build(
        service=service,
        operation_id=operation.ownership.operation_id,
        expected_revision=operation.revision,
        stock=selected_stock,
        job_id=job_id or LatheToolpathJobId.new(),
        request_sequence=request_sequence,
    )
    if not built.accepted or built.request is None:
        codes = ",".join(item.code.value for item in built.diagnostics)
        raise AssertionError(f"Fixture request build failed: {codes}")
    return service, operation, built.request


def same_semantics_request(
    request: LatheToolpathRequestV1,
    *,
    request_sequence: int | None = None,
) -> LatheToolpathRequestV1:
    return replace(
        request,
        job_id=LatheToolpathJobId.new(),
        request_sequence=(
            request.request_sequence + 1
            if request_sequence is None
            else request_sequence
        ),
    )


def generate(request: LatheToolpathRequestV1) -> LatheToolpathResult:
    return LatheToolpathGeneratorRegistry().generate(request, lambda: False)


def segments(result: LatheToolpathResult) -> tuple[LathePathSegment, ...]:
    return tuple(
        event for event in result.motions if isinstance(event, LathePathSegment)
    )


def next_revision(revision: Revision) -> Revision:
    return Revision(revision.value + 1)


__all__ = [
    "generate",
    "next_revision",
    "ready_request",
    "same_semantics_request",
    "segments",
    "stock_snapshot",
]
