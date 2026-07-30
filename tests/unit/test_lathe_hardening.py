"""Self-review hardening tests for deep immutability and resolver boundaries."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.capabilities import LatheToolCapabilityResolution
from hms_cadcam.cam.lathe.commands import BindLatheTool
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.presenter import LatheOperationSnapshot, LathePresenterFacade
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheStrategyId,
    LatheToolCapability,
)
from tests.unit._lathe_fixtures import (
    capability_resolution,
    create_operation,
    operation_id,
    service_for,
    session,
    tool_reference,
)


@pytest.mark.parametrize("mutable", [[], {}, set()])
def test_parameter_update_rejects_mutable_payloads(mutable: object) -> None:
    with pytest.raises(TypeError, match="immutable primitive"):
        LatheParameterUpdate("feed_mm_per_rev", mutable)


class _InvalidResolver:
    def resolve(self, _reference: object) -> object:
        return object()


def test_invalid_resolver_result_fails_closed_without_partial_assignment() -> None:
    service = LatheOperationService(session(), capability_resolver=_InvalidResolver())
    original = create_operation(service)
    outcome = service.execute(
        BindLatheTool(original.ownership, tool_reference(), original.revision)
    )
    assert not outcome.accepted
    assert outcome.diagnostics[0].code is LatheDiagnosticCode.MISSING_TOOL
    assert service.query(operation_id()) == original


class _FalseyResolver:
    def __init__(self, resolution: LatheToolCapabilityResolution) -> None:
        self._resolution = resolution

    def __bool__(self) -> bool:
        return False

    def resolve(self, _reference: object) -> LatheToolCapabilityResolution:
        return self._resolution


def test_explicit_falsey_resolver_is_not_replaced_by_default() -> None:
    reference = tool_reference()
    resolution = capability_resolution(
        LatheToolCapability.FACE_TURNING, reference=reference
    )
    service = LatheOperationService(
        session(), capability_resolver=_FalseyResolver(resolution)
    )
    original = create_operation(service)
    outcome = service.execute(
        BindLatheTool(original.ownership, reference, original.revision)
    )
    assert outcome.accepted


def test_operation_snapshot_rejects_mutable_parameter_values() -> None:
    service, _reference = service_for()
    facade = LathePresenterFacade(service)
    facade.create_operation(operation_id(), LatheStrategyId.FACE)
    snapshot = facade.operation_snapshot(operation_id())
    assert isinstance(snapshot, LatheOperationSnapshot)
    with pytest.raises(TypeError, match="immutable typed pairs"):
        replace(snapshot, parameter_values=(("feed_mm_per_rev", []),))
