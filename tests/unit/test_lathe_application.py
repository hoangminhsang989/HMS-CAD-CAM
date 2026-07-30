"""Atomic command, service, Tool compatibility, and lifecycle tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.capabilities import (
    LatheToolCapabilityResolution,
    StaticLatheToolCapabilityResolver,
)
from hms_cadcam.cam.lathe.commands import (
    BindLatheGeometry,
    BindLatheTool,
    ChangeLatheStrategy,
    ClearLatheGeometry,
    ClearLatheTool,
    CreateLatheOperation,
    DeleteLatheOperation,
    SetLatheOperationEnabled,
    UpdateLatheParameters,
    ValidateLatheOperation,
)
from hms_cadcam.cam.lathe.domain import LatheGeometryBinding
from hms_cadcam.cam.lathe.parameters import (
    LatheParameterUpdate,
    build_lathe_v1_defaults,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheGeometryKind,
    LatheOperationReadiness,
    LatheStrategyId,
    LatheToolCapability,
)
from tests.unit._lathe_fixtures import (
    capability_resolution,
    complete_operation,
    create_operation,
    operation_id,
    ownership,
    service_for,
    session,
    setup_id,
    stable_uuid,
    tool_reference,
)


def test_create_query_list_duplicate_and_operation_isolation() -> None:
    service, _reference = service_for()
    first = create_operation(service, index=1)
    second = create_operation(service, LatheStrategyId.OD_ROUGH, index=2)
    assert service.list_operations() == (first, second)
    assert service.query(operation_id(1)) == first
    duplicate = service.execute(
        CreateLatheOperation(
            first.ownership,
            first.strategy_id,
            first.parameter_state,
        )
    )
    assert not duplicate.accepted
    assert duplicate.diagnostics[0].code is LatheDiagnosticCode.DUPLICATE_OPERATION
    changed = service.execute(
        UpdateLatheParameters(
            first.ownership,
            (LatheParameterUpdate("feed_mm_per_rev", 0.25),),
            first.revision,
        )
    )
    assert changed.accepted
    assert service.query(operation_id(2)) == second


def test_atomic_multi_parameter_update_and_revision_policy() -> None:
    service, _reference = service_for()
    original = create_operation(service)
    failed = service.execute(
        UpdateLatheParameters(
            original.ownership,
            (
                LatheParameterUpdate("outer_diameter_mm", 80.0),
                LatheParameterUpdate("inner_diameter_mm", 90.0),
            ),
            original.revision,
        )
    )
    assert not failed.accepted
    assert service.query(operation_id()) == original
    assert service.query(operation_id()).revision == Revision(0)
    accepted = service.execute(
        UpdateLatheParameters(
            original.ownership,
            (
                LatheParameterUpdate("outer_diameter_mm", 80.0),
                LatheParameterUpdate("inner_diameter_mm", 20.0),
            ),
            original.revision,
        )
    )
    assert accepted.accepted and accepted.operation is not None
    assert accepted.operation.revision == Revision(1)
    assert accepted.operation.parameter_state.value("outer_diameter_mm") == 80.0


def test_expected_revision_mismatch_preserves_state() -> None:
    service, _reference = service_for()
    original = create_operation(service)
    outcome = service.execute(
        SetLatheOperationEnabled(original.ownership, False, Revision(99))
    )
    assert not outcome.accepted
    assert outcome.diagnostics[0].code is LatheDiagnosticCode.REVISION_MISMATCH
    assert service.query(operation_id()) == original


def test_geometry_bind_and_clear_are_atomic_and_fail_closed() -> None:
    service, _reference = service_for()
    original = create_operation(service)
    incompatible = LatheGeometryBinding(
        LatheGeometryKind.POINT,
        ("point-1",),
        original.ownership.source_id,
        original.ownership.generation,
    )
    failed = service.execute(
        BindLatheGeometry(original.ownership, incompatible, original.revision)
    )
    assert not failed.accepted
    assert failed.diagnostics[0].code is LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY
    assert service.query(operation_id()) == original
    stale = LatheGeometryBinding(
        LatheGeometryKind.FACE,
        ("face-1",),
        original.ownership.source_id,
        original.ownership.generation + 1,
    )
    failed_stale = service.execute(
        BindLatheGeometry(original.ownership, stale, original.revision)
    )
    assert not failed_stale.accepted
    valid = replace(stale, generation=original.ownership.generation)
    bound = service.execute(
        BindLatheGeometry(original.ownership, valid, original.revision)
    )
    assert bound.accepted and bound.operation is not None
    cleared = service.execute(
        ClearLatheGeometry(bound.operation.ownership, bound.operation.revision)
    )
    assert cleared.accepted and cleared.operation is not None
    assert cleared.operation.geometry_binding is None
    assert cleared.operation.revision == Revision(2)


def test_default_resolver_missing_and_incompatible_tool_preserve_prior_state() -> None:
    reference = tool_reference()
    missing_service = LatheOperationService(session())
    original = create_operation(missing_service)
    missing = missing_service.execute(
        BindLatheTool(original.ownership, reference, original.revision)
    )
    assert not missing.accepted
    assert missing.diagnostics[0].code is LatheDiagnosticCode.MISSING_TOOL
    assert missing_service.query(operation_id()) == original

    incompatible_resolution = capability_resolution(
        LatheToolCapability.OD_TURNING, reference=reference
    )
    incompatible_service = LatheOperationService(
        session(),
        capability_resolver=StaticLatheToolCapabilityResolver(
            (incompatible_resolution,)
        ),
    )
    incompatible_state = create_operation(incompatible_service)
    incompatible = incompatible_service.execute(
        BindLatheTool(
            incompatible_state.ownership,
            reference,
            incompatible_state.revision,
        )
    )
    assert not incompatible.accepted
    assert incompatible.diagnostics[0].code is LatheDiagnosticCode.INCOMPATIBLE_TOOL
    assert incompatible_service.query(operation_id()) == incompatible_state


def test_valid_capability_accepts_immutable_binding_and_clear() -> None:
    service, reference = service_for()
    original = create_operation(service)
    bound = service.execute(
        BindLatheTool(original.ownership, reference, original.revision)
    )
    assert bound.accepted and bound.operation is not None
    assert bound.operation.tool_binding is not None
    assert bound.operation.tool_binding.resolved_capabilities == frozenset(
        {LatheToolCapability.FACE_TURNING}
    )
    with pytest.raises(FrozenInstanceError):
        bound.operation.tool_binding.tool_id = reference.tool_id  # type: ignore[misc]
    cleared = service.execute(
        ClearLatheTool(bound.operation.ownership, bound.operation.revision)
    )
    assert cleared.accepted and cleared.operation is not None
    assert cleared.operation.tool_binding is None


def test_strategy_change_retains_common_resets_specific_and_revalidates_bindings() -> None:
    service, reference = service_for()
    state = complete_operation(service, reference)
    updated = service.execute(
        UpdateLatheParameters(
            state.ownership,
            (LatheParameterUpdate("feed_mm_per_rev", 0.33),),
            state.revision,
        )
    )
    assert updated.accepted and updated.operation is not None
    changed = service.execute(
        ChangeLatheStrategy(
            updated.operation.ownership,
            LatheStrategyId.OD_ROUGH,
            updated.operation.revision,
        )
    )
    assert changed.accepted and changed.operation is not None
    assert changed.operation.parameter_state.value("feed_mm_per_rev") == 0.33
    assert changed.operation.parameter_state.value("end_z_mm") == -50.0
    assert changed.operation.geometry_binding is not None
    assert changed.operation.tool_binding is not None
    assert changed.evaluation is not None
    assert changed.evaluation.readiness is LatheOperationReadiness.INVALID
    assert {
        LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY,
        LatheDiagnosticCode.INCOMPATIBLE_TOOL,
    }.issubset({item.code for item in changed.evaluation.diagnostics})


def test_enable_validate_delete_and_deleted_operation_unavailable() -> None:
    service, _reference = service_for()
    state = create_operation(service)
    disabled = service.execute(
        SetLatheOperationEnabled(state.ownership, False, state.revision)
    )
    assert disabled.accepted and disabled.operation is not None
    assert disabled.operation.revision == Revision(1)
    validated = service.execute(
        ValidateLatheOperation(
            disabled.operation.ownership, disabled.operation.revision
        )
    )
    assert validated.accepted and not validated.changed
    assert validated.operation == disabled.operation
    deleted = service.execute(
        DeleteLatheOperation(
            disabled.operation.ownership, disabled.operation.revision
        )
    )
    assert deleted.accepted and deleted.deleted and deleted.operation is not None
    assert deleted.operation.revision == Revision(2)
    assert service.list_operations() == ()
    with pytest.raises(KeyError):
        service.query(operation_id())


def test_read_only_and_closed_reject_mutations_but_validate_is_a_query() -> None:
    service, _reference = service_for()
    state = create_operation(service)
    service.set_read_only(True)
    rejected = service.execute(
        SetLatheOperationEnabled(state.ownership, False, state.revision)
    )
    assert not rejected.accepted
    assert rejected.diagnostics[0].code is LatheDiagnosticCode.READ_ONLY
    validated = service.execute(
        ValidateLatheOperation(state.ownership, state.revision)
    )
    assert validated.accepted and not validated.changed
    service.set_read_only(False)
    first_close = service.close()
    second_close = service.close()
    assert first_close is second_close
    closed = service.execute(
        SetLatheOperationEnabled(state.ownership, False, state.revision)
    )
    assert not closed.accepted
    assert closed.diagnostics[0].code is LatheDiagnosticCode.CLOSED


def test_setup_source_and_generation_transitions_make_prior_ownership_stale() -> None:
    for transition in ("setup", "source", "generation"):
        service, _reference = service_for()
        state = create_operation(service)
        if transition == "setup":
            service.switch_setup(setup_id(2))
        elif transition == "source":
            service.switch_source(stable_uuid("source/2"), 0)
        else:
            service.increment_generation()
        evaluation = service.evaluate(state.ownership.operation_id)
        assert evaluation.readiness is LatheOperationReadiness.INVALID
        assert LatheDiagnosticCode.STALE_OWNERSHIP in {
            item.code for item in evaluation.diagnostics
        }
        mutation = service.execute(
            SetLatheOperationEnabled(state.ownership, False, state.revision)
        )
        assert not mutation.accepted
        assert mutation.diagnostics[0].code is LatheDiagnosticCode.STALE_OWNERSHIP


def test_project_document_mismatch_and_cross_session_leakage_are_absent() -> None:
    service, _reference = service_for()
    foreign_project = replace(ownership(), project_id=stable_uuid("project/foreign"))
    rejected = service.execute(
        CreateLatheOperation(
            foreign_project,
            LatheStrategyId.FACE,
            build_lathe_v1_defaults(LatheStrategyId.FACE),
        )
    )
    assert not rejected.accepted
    assert rejected.diagnostics[0].code is LatheDiagnosticCode.STALE_OWNERSHIP
    foreign_document = replace(ownership(index=2), document_id=replace(session().document_id, value="other"))
    rejected_document = service.execute(
        CreateLatheOperation(
            foreign_document,
            LatheStrategyId.FACE,
            build_lathe_v1_defaults(LatheStrategyId.FACE),
        )
    )
    assert not rejected_document.accepted
    create_operation(service)
    other, _reference = service_for()
    assert other.list_operations() == ()


def test_commands_and_resolver_results_are_immutable_and_name_free() -> None:
    key = ownership()
    command = SetLatheOperationEnabled(key, False, Revision(0))
    with pytest.raises(FrozenInstanceError):
        command.enabled = True  # type: ignore[misc]
    reference = tool_reference()
    resolution = LatheToolCapabilityResolution(
        reference,
        True,
        True,
        frozenset({LatheToolCapability.FACE_TURNING}),
        Revision(0),
        None,
        Revision(0),
    )
    assert not hasattr(reference, "display_name")
    assert not hasattr(resolution, "display_name")
