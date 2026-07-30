"""Qt presenter command, revision and lifecycle acceptance tests for Stage 9A.9."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate, build_lathe_v1_defaults
from hms_cadcam.cam.lathe.types import (
    LatheOperationReadiness,
    LatheStrategyId,
    LatheWorkspaceReadinessState,
)
from hms_cadcam.ui.lathe_presenter import LatheQtCommandResult
from hms_cadcam.viewer.models import SelectionMode

from _lathe_ui_fixtures import presenter_for, selection_context


def test_presenter_initializes_from_facade_with_exact_immutable_snapshot() -> None:
    presenter, _catalog, _reference = presenter_for()
    snapshot = presenter.snapshot
    assert len(snapshot.strategies) == 11
    assert tuple(item.strategy_id for item in snapshot.strategies) == tuple(
        LatheStrategyId
    )
    assert snapshot.operations == ()
    assert (
        snapshot.workspace_readiness.state
        is LatheWorkspaceReadinessState.PRESENTER_ACTIVE
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.closed = True  # type: ignore[misc]
    assert not hasattr(presenter, "_operations")
    assert not hasattr(presenter, "_operation_state")


@pytest.mark.parametrize("strategy_id", tuple(LatheStrategyId))
def test_create_uses_exact_defaults_revision_zero_and_selects_after_success(
    strategy_id: LatheStrategyId,
) -> None:
    presenter, _catalog, _reference = presenter_for(strategy_id)
    result = presenter.create_operation(strategy_id)
    assert result.accepted and result.changed
    operation = presenter.active_operation
    assert operation is not None
    assert operation.strategy_id is strategy_id
    assert operation.revision.value == 0
    assert operation.parameter_values == build_lathe_v1_defaults(strategy_id).values
    assert operation.readiness is LatheOperationReadiness.INCOMPLETE
    assert presenter.snapshot.active_operation_id == operation.ownership.operation_id


def test_parameter_edit_carries_expected_revision_and_refreshes_authority() -> None:
    presenter, _catalog, _reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    operation = presenter.active_operation
    assert operation is not None
    result = presenter.apply_parameter_changes(
        operation.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", 0.35),),
        operation.revision,
    )
    assert result.accepted and result.changed
    refreshed = presenter.active_operation
    assert refreshed is not None
    assert dict(refreshed.parameter_values)["feed_mm_per_rev"] == 0.35
    assert refreshed.revision.value == 1


def test_revision_conflict_refreshes_and_never_overwrites() -> None:
    presenter, _catalog, _reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    original = presenter.active_operation
    assert original is not None
    conflicts: list[LatheQtCommandResult] = []
    presenter.revision_conflict.connect(conflicts.append)
    accepted = presenter.apply_parameter_changes(
        original.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", 0.3),),
        original.revision,
    )
    assert accepted.accepted
    rejected = presenter.apply_parameter_changes(
        original.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", 0.9),),
        original.revision,
    )
    assert not rejected.accepted and not rejected.changed
    assert [item.code for item in rejected.diagnostics] == ["revision_mismatch"]
    assert conflicts == [rejected]
    current = presenter.active_operation
    assert current is not None
    assert dict(current.parameter_values)["feed_mm_per_rev"] == 0.3
    assert current.revision.value == 1


def test_failed_typed_edit_does_not_report_success_or_mutate() -> None:
    presenter, _catalog, _reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    before = presenter.active_operation
    assert before is not None
    result = presenter.apply_parameter_changes(
        before.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", "0.5"),),
        before.revision,
    )
    assert not result.accepted and not result.changed
    after = presenter.active_operation
    assert after == before
    assert result.diagnostics[0].code == "invalid_parameter"


def test_geometry_and_tool_binding_reach_ready_without_calculation_claim() -> None:
    presenter, _catalog, reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    operation = presenter.active_operation
    assert operation is not None
    geometry = presenter.bind_current_geometry(
        operation.ownership.operation_id, operation.revision
    )
    assert geometry.accepted
    operation = presenter.active_operation
    assert operation is not None
    tool = presenter.bind_tool(
        operation.ownership.operation_id, reference, operation.revision
    )
    assert tool.accepted
    operation = presenter.active_operation
    assert operation is not None
    assert operation.readiness is LatheOperationReadiness.READY
    assert all(
        word not in repr(operation).casefold()
        for word in ("toolpath", "g-code", "simulation", "calculated")
    )


def test_geometry_boundary_unavailable_and_incompatible_fail_closed() -> None:
    unavailable, _catalog, _reference = presenter_for(
        selection_provider=lambda: None
    )
    unavailable.create_operation(LatheStrategyId.FACE)
    operation = unavailable.active_operation
    assert operation is not None
    result = unavailable.bind_current_geometry(
        operation.ownership.operation_id, operation.revision
    )
    assert not result.accepted
    assert result.diagnostics[0].code == "lathe.geometry.selection_unavailable"
    incompatible, _catalog, _reference = presenter_for(
        selection_provider=lambda: selection_context(
            mode=SelectionMode.VERTEX
        )
    )
    incompatible.create_operation(LatheStrategyId.FACE)
    operation = incompatible.active_operation
    assert operation is not None
    result = incompatible.bind_current_geometry(
        operation.ownership.operation_id, operation.revision
    )
    assert not result.accepted
    assert "incompatible" in result.diagnostics[0].code


def test_operation_selection_isolation_and_deterministic_delete_selection() -> None:
    presenter, _catalog, _reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    first = presenter.active_operation
    presenter.create_operation(LatheStrategyId.OD_ROUGH)
    second = presenter.active_operation
    assert first is not None and second is not None
    presenter.select_operation(first.ownership.operation_id)
    first = presenter.active_operation
    assert first is not None
    presenter.apply_parameter_changes(
        first.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", 0.44),),
        first.revision,
    )
    presenter.select_operation(second.ownership.operation_id)
    assert dict(presenter.active_operation.parameter_values)["feed_mm_per_rev"] == 0.2
    second = presenter.active_operation
    result = presenter.delete_operation(
        second.ownership.operation_id, second.revision
    )
    assert result.accepted
    assert len(presenter.snapshot.operations) == 1
    assert presenter.snapshot.active_operation_id == first.ownership.operation_id


def test_wrong_operation_identity_and_teardown_are_safe() -> None:
    presenter, _catalog, _reference = presenter_for()
    results: list[LatheQtCommandResult] = []
    presenter.command_completed.connect(results.append)
    missing = presenter.select_operation(OperationId.new())
    assert not missing.accepted
    assert missing.diagnostics[0].code == "operation_not_found"
    presenter.teardown()
    presenter.teardown()
    closed = presenter.create_operation(LatheStrategyId.FACE)
    assert not closed.accepted
    assert closed.diagnostics[0].code == "closed"
    assert len(results) == 2


def test_read_only_and_closed_contexts_reject_every_mutation() -> None:
    presenter, _catalog, _reference = presenter_for()
    presenter.create_operation(LatheStrategyId.FACE)
    operation = presenter.active_operation
    assert operation is not None
    service = presenter.facade.service
    service.set_read_only(True)
    presenter.refresh()
    rejected = presenter.set_enabled(
        operation.ownership.operation_id, False, operation.revision
    )
    assert not rejected.accepted
    assert rejected.diagnostics[0].code == "read_only"
    inspected = presenter.validate_operation(
        operation.ownership.operation_id, operation.revision
    )
    assert inspected.accepted and not inspected.changed
    service.set_read_only(False)
    service.close()
    presenter.refresh()
    closed = presenter.clear_geometry(
        operation.ownership.operation_id, operation.revision
    )
    assert not closed.accepted
    assert closed.diagnostics[0].code == "closed"
