"""Presenter-neutral DTO, facade, and workspace-readiness tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.presenter import (
    LatheOperationSnapshot,
    LathePresenterFacade,
    LathePresenterSnapshot,
    LatheStrategyDescriptor,
)
from hms_cadcam.cam.lathe.readiness import (
    LatheWorkspaceReadiness,
    STAGE12_LATHE_WORKSPACE_READINESS,
    create_lathe_foundation_provider,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheStage9A9State,
    LatheStrategyId,
    LatheWorkspaceReadinessReason,
    LatheWorkspaceReadinessState,
)
from tests.unit._lathe_fixtures import operation_id, service_for, session


def test_foundation_unavailable_before_provider_and_unblocked_after_creation() -> None:
    unavailable = LatheWorkspaceReadiness.unavailable()
    assert unavailable.state is LatheWorkspaceReadinessState.FOUNDATION_UNAVAILABLE
    assert unavailable.reason is LatheWorkspaceReadinessReason.FOUNDATION_NOT_READY
    assert unavailable.stage_9a9 is LatheStage9A9State.BLOCKED
    provider = create_lathe_foundation_provider(session())
    assert provider.service.list_operations() == ()
    assert provider.readiness.state is LatheWorkspaceReadinessState.PRESENTER_IMPLEMENTATION_ALLOWED
    assert provider.readiness.reason is LatheWorkspaceReadinessReason.PRESENTER_NOT_IMPLEMENTED
    assert provider.readiness.stage_9a9 is LatheStage9A9State.UNBLOCKED_FOR_IMPLEMENTATION
    assert not provider.readiness.presenter_active


def test_stage12_default_readiness_never_claims_presenter_active() -> None:
    readiness = STAGE12_LATHE_WORKSPACE_READINESS
    assert readiness.state is LatheWorkspaceReadinessState.PRESENTER_IMPLEMENTATION_ALLOWED
    assert readiness.reason.value == "presenter_not_implemented"
    assert readiness.stage_9a9.value == "UNBLOCKED_FOR_IMPLEMENTATION"
    assert not readiness.presenter_active
    with pytest.raises(FrozenInstanceError):
        readiness.presenter_active = True  # type: ignore[misc]


def test_presenter_lists_exact_strategy_and_parameter_metadata() -> None:
    service, _reference = service_for()
    facade = LathePresenterFacade(service)
    descriptors = facade.list_strategies()
    assert len(descriptors) == 11
    assert tuple(item.strategy_id for item in descriptors) == tuple(LatheStrategyId)
    assert all(isinstance(item, LatheStrategyDescriptor) for item in descriptors)
    assert facade.strategy_metadata(LatheStrategyId.FACE) == descriptors[0]
    for descriptor in descriptors:
        assert descriptor.parameters
        assert len({item.parameter_id for item in descriptor.parameters}) == len(
            descriptor.parameters
        )
        assert all(item.label_key.startswith("lathe.parameter.") for item in descriptor.parameters)


def test_presenter_snapshot_is_immutable_and_contains_no_ui_objects() -> None:
    service, _reference = service_for()
    facade = LathePresenterFacade(service)
    created = facade.create_operation(operation_id(), LatheStrategyId.FACE)
    assert created.accepted
    snapshot = facade.snapshot()
    assert isinstance(snapshot, LathePresenterSnapshot)
    assert len(snapshot.operations) == 1
    assert isinstance(snapshot.operations[0], LatheOperationSnapshot)
    assert snapshot.active_operation_id == operation_id()
    assert snapshot.workspace_readiness == STAGE12_LATHE_WORKSPACE_READINESS
    with pytest.raises(FrozenInstanceError):
        snapshot.operations = ()  # type: ignore[misc]
    assert not hasattr(snapshot, "widget")
    assert not hasattr(snapshot, "signal")
    assert not hasattr(snapshot.operations[0], "ocp_shape")


def test_presenter_facade_delegates_typed_command_workflow() -> None:
    service, reference = service_for()
    facade = LathePresenterFacade(service)
    created = facade.create_operation(operation_id(), LatheStrategyId.FACE)
    assert created.accepted and created.operation is not None
    updated = facade.apply_parameter_changes(
        operation_id(),
        (LatheParameterUpdate("feed_mm_per_rev", 0.3),),
        created.operation.revision,
    )
    assert updated.accepted and updated.operation is not None
    geometry = LatheGeometryBinding(
        LatheGeometryKind.FACE,
        ("face-presenter",),
        updated.operation.ownership.source_id,
        updated.operation.ownership.generation,
    )
    geometry_outcome = facade.bind_geometry(
        operation_id(), geometry, updated.operation.revision
    )
    assert geometry_outcome.accepted and geometry_outcome.operation is not None
    tool_outcome = facade.bind_tool(
        operation_id(), reference, geometry_outcome.operation.revision
    )
    assert tool_outcome.accepted and tool_outcome.operation is not None
    assert facade.validate_operation(
        operation_id(), tool_outcome.operation.revision
    ).accepted
    assert facade.query_diagnostics(operation_id()) == ()
    assert facade.operation_snapshot(operation_id()).revision == Revision(3)
    disabled = facade.set_enabled(
        operation_id(), False, tool_outcome.operation.revision
    )
    assert disabled.accepted and disabled.operation is not None
    assert LatheDiagnosticCode.DISABLED_OPERATION in {
        item.code for item in facade.query_diagnostics(operation_id())
    }
    deleted = facade.delete_operation(operation_id(), disabled.operation.revision)
    assert deleted.accepted and deleted.deleted
    assert facade.snapshot().active_operation_id is None


def test_presenter_change_clear_and_rejected_outcomes_remain_typed() -> None:
    service, _reference = service_for()
    facade = LathePresenterFacade(service)
    created = facade.create_operation(operation_id(), LatheStrategyId.FACE)
    assert created.operation is not None
    rejected = facade.apply_parameter_change(
        operation_id(), "feed_mm_per_rev", 0.0, created.operation.revision
    )
    assert not rejected.accepted
    assert rejected.operation == created.operation
    assert rejected.diagnostics[0].code is LatheDiagnosticCode.INVALID_PARAMETER
    changed = facade.change_strategy(
        operation_id(), LatheStrategyId.AXIAL_DRILL, created.operation.revision
    )
    assert changed.accepted and changed.operation is not None
    assert changed.operation.strategy_id is LatheStrategyId.AXIAL_DRILL
    geometry_cleared = facade.clear_geometry(
        operation_id(), changed.operation.revision
    )
    assert geometry_cleared.accepted and geometry_cleared.operation is not None
    tool_cleared = facade.clear_tool(
        operation_id(), geometry_cleared.operation.revision
    )
    assert tool_cleared.accepted


def test_presenter_selection_is_explicit_and_missing_operation_fails() -> None:
    service, _reference = service_for()
    facade = LathePresenterFacade(service)
    with pytest.raises(KeyError):
        facade.select_active_operation(operation_id(99))
    facade.create_operation(operation_id(), LatheStrategyId.FACE)
    facade.select_active_operation(None)
    assert facade.snapshot().active_operation_id is None
