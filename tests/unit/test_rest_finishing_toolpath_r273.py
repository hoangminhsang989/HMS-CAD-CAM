"""R273 sealed lifecycle, replay, fingerprint and cancellation tests."""

from __future__ import annotations

from copy import copy
from dataclasses import replace

import pytest

from hms_cadcam.cam.application.rest_finishing_lifecycle import (
    RestFinishingLifecycleContext,
    RestFinishingLifecyclePreparation,
    RestFinishingLifecycleStatus,
    generate_rest_finishing_3axis,
    prepare_rest_finishing_3axis,
)
from hms_cadcam.cam.application.rest_finishing_toolpath import (
    RestFinishingCandidate,
    RestFinishingPrepared,
    require_rest_finishing_candidate,
    require_rest_finishing_prepared,
)
import hms_cadcam.cam.application.rest_finishing_toolpath as finishing_toolpath
import hms_cadcam.cam.application.rest_finishing_geometry as finishing_geometry
from hms_cadcam.cam.domain import Length, MachineEvidence
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.rest_finishing import RestFinishingDiagnosticCode, RestFinishingValidationError
from hms_cadcam.cam.material_state import (
    MaterialStatePrecisionPolicy,
    calculate_material_state,
    material_state_setup_fingerprint,
)
from hms_cadcam.cam.toolpath import LinearMove, MotionClass, compute_material_removal_fingerprint

from test_rest_finishing_core_r273 import (
    _inputs,
    _parameters,
    _with_exact_r273_parameters,
)


def _context(inputs=None, *, cancellation=None) -> RestFinishingLifecycleContext:
    values = inputs or _inputs(cancellation=cancellation)
    callback = values.cancellation if cancellation is None else cancellation
    assert values.cancellation is callback
    return RestFinishingLifecycleContext(
        values.setup,
        values.parameters,
        values.profile_selection,
        values.material_candidates,
        values.producer_completion,
        values.producer_dependency,
        values.producer_parent_state,
        values.producer_validation_certificate,
        values.dependency_graph,
        values.assembly,
        values.assembly_evidence,
        values.tool,
        values.machine,
        values.machine_requirement,
        values.machine_evidence,
        values.consumer_operation_id,
        values.profile_resolver,
        callback,
    )


def _success(inputs=None):
    preparation = prepare_rest_finishing_3axis(_context(inputs))
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    assert preparation.prepared is not None
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.SUCCESS, result.message
    assert result.candidate is not None
    return preparation, result.candidate


def test_success_is_deterministic_and_successor_matches_independent_full_replay() -> None:
    preparation = prepare_rest_finishing_3axis(_context())
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    first = generate_rest_finishing_3axis(preparation)
    second = generate_rest_finishing_3axis(preparation)
    assert first.status is second.status is RestFinishingLifecycleStatus.SUCCESS
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.artifact == second.candidate.artifact
    assert first.candidate.successor_state == second.candidate.successor_state
    assert first.candidate.candidate_fingerprint == second.candidate.candidate_fingerprint
    candidate = first.candidate
    replay = calculate_material_state(
        stock=preparation.context.setup.stock,
        artifact=candidate.artifact,
        tool=preparation.context.tool,
        parent=candidate.prepared.predecessor_state,
        setup_fingerprint=material_state_setup_fingerprint(preparation.context.setup),
        precision=MaterialStatePrecisionPolicy(),
    ).state
    assert replay == candidate.successor_state
    assert candidate.successor_state.parent_fingerprint == candidate.prepared.predecessor_state.fingerprint
    assert candidate.semantic_material_removal_fingerprint == compute_material_removal_fingerprint(candidate.artifact)


def test_each_level_consumes_current_cumulative_replay_and_exact_stepdown_law() -> None:
    inputs = _inputs(parameters=_parameters(max_stepdown=10.0))
    _preparation, candidate = _success(inputs)
    levels = candidate.level_plans
    assert tuple(level.tip_z for level in levels) == candidate.prepared.plan.levels
    assert len(levels) >= 2
    assert levels[0].state_fingerprint == candidate.prepared.predecessor_state.fingerprint
    assert len({level.state_fingerprint for level in levels}) == len(levels)
    cutting_z = {
        event.end.position.z
        for event in candidate.artifact.events
        if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING
    }
    assert cutting_z == set(candidate.prepared.plan.levels)


def test_typed_no_work_has_zero_artifact_and_zero_successor() -> None:
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=1.99, allowance=0.0),
        complete=True,
    )
    preparation = prepare_rest_finishing_3axis(_context(inputs))
    assert preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    assert preparation.prepared is None
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    assert result.candidate is None


def test_no_work_is_revalidated_and_cannot_hide_post_prepare_state_tamper() -> None:
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=1.99, allowance=0.0),
        complete=True,
    )
    preparation = prepare_rest_finishing_3axis(_context(inputs))
    assert preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    state = inputs.material_candidates[0].state
    heights = list(state.top_heights)
    heights[0] += 1.0
    object.__setattr__(state, "top_heights", tuple(heights))
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_no_work_retained_plan_nested_tamper_is_rejected() -> None:
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=1.99, allowance=0.0),
        complete=True,
    )
    preparation = prepare_rest_finishing_3axis(_context(inputs))
    assert preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    assert preparation.plan is not None
    object.__setattr__(preparation.plan, "target_cells", ())
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_no_work_retained_plan_signed_zero_tamper_is_rejected() -> None:
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=1.99, allowance=0.0),
        complete=True,
    )
    preparation = prepare_rest_finishing_3axis(_context(inputs))
    assert preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
    assert preparation.plan is not None
    point = preparation.plan.target_path.loop.segments[0].start
    assert point.z == 0.0 and point.z.hex() == "0x0.0p+0"
    object.__setattr__(point, "z", -0.0)
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_cancellation_at_prepare_and_generate_never_returns_partial_candidate() -> None:
    immediate = {"cancelled": False}
    immediate_callback = lambda: immediate["cancelled"]
    immediate_context = _context(cancellation=immediate_callback)
    immediate["cancelled"] = True
    cancelled = prepare_rest_finishing_3axis(immediate_context)
    assert cancelled.status is RestFinishingLifecycleStatus.FAILURE
    assert cancelled.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert cancelled.prepared is None

    flag = {"cancelled": False}
    preparation = prepare_rest_finishing_3axis(
        _context(cancellation=lambda: flag["cancelled"])
    )
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    flag["cancelled"] = True
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert result.candidate is None


def test_cancellation_after_region_computation_cannot_become_no_work(
    monkeypatch,
) -> None:
    flag = {"cancelled": False}
    cancellation = lambda: flag["cancelled"]
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=0.0, allowance=0.0),
        complete=True,
        cancellation=cancellation,
    )
    actual = finishing_geometry._cells_inside

    def cancel_after_region(state, loop, cancellation=None):
        result = actual(state, loop, cancellation)
        flag["cancelled"] = True
        return result

    monkeypatch.setattr(finishing_geometry, "_cells_inside", cancel_after_region)
    preparation = prepare_rest_finishing_3axis(
        _context(inputs, cancellation=cancellation)
    )
    assert preparation.status is RestFinishingLifecycleStatus.FAILURE
    assert preparation.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert preparation.prepared is None


def test_cancellation_after_raster_computation_cannot_become_no_work(
    monkeypatch,
) -> None:
    flag = {"cancelled": False}
    cancellation = lambda: flag["cancelled"]
    inputs = _inputs(
        parameters=_parameters(nominal_target_z=0.0, allowance=0.0),
        complete=True,
        cancellation=cancellation,
    )
    actual = finishing_geometry._raster_positions

    def cancel_after_raster(loop, stepover, cancellation=None):
        result = actual(loop, stepover, cancellation)
        flag["cancelled"] = True
        return result

    monkeypatch.setattr(
        finishing_geometry,
        "_raster_positions",
        cancel_after_raster,
    )
    preparation = prepare_rest_finishing_3axis(
        _context(inputs, cancellation=cancellation)
    )
    assert preparation.status is RestFinishingLifecycleStatus.FAILURE
    assert preparation.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert preparation.prepared is None


def test_copied_replaced_or_directly_constructed_prepared_cannot_mint_success() -> None:
    preparation = prepare_rest_finishing_3axis(_context())
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    assert preparation.prepared is not None
    forged_values = (
        copy(preparation.prepared),
        replace(preparation.prepared),
        RestFinishingPrepared(
            preparation.prepared.inputs,
            preparation.prepared.plan,
            preparation.prepared.predecessor_state,
            preparation.prepared.base_operation,
            preparation.prepared.computing_operation,
            preparation.prepared.input_fingerprint,
            preparation.prepared.computation_token,
        ),
    )
    for forged in forged_values:
        with pytest.raises(RestFinishingValidationError) as captured:
            require_rest_finishing_prepared(forged)
        assert captured.value.code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID

    forged_lifecycle = replace(preparation)
    result = generate_rest_finishing_3axis(forged_lifecycle)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_copied_replaced_or_directly_constructed_candidate_is_not_authority() -> None:
    _preparation, candidate = _success()
    forged_values = (
        copy(candidate),
        replace(candidate),
        RestFinishingCandidate(
            candidate.prepared,
            candidate.artifact,
            candidate.successor_state,
            candidate.successor_provenance,
            candidate.level_plans,
        ),
    )
    for forged in forged_values:
        with pytest.raises(RestFinishingValidationError) as captured:
            require_rest_finishing_candidate(forged)
        assert captured.value.code is RestFinishingDiagnosticCode.SUCCESSOR_INVALID


def test_registered_candidate_cannot_launder_unregistered_prepared() -> None:
    _preparation, candidate = _success()
    replacement = replace(candidate.prepared)
    assert replacement == candidate.prepared and replacement is not candidate.prepared
    object.__setattr__(candidate, "prepared", replacement)
    with pytest.raises(RestFinishingValidationError) as captured:
        require_rest_finishing_candidate(candidate)
    assert captured.value.code is RestFinishingDiagnosticCode.SUCCESSOR_INVALID


def test_registered_candidate_nested_level_tamper_is_rejected() -> None:
    _preparation, candidate = _success()
    object.__setattr__(candidate.level_plans[0], "work_cells", ())
    with pytest.raises(RestFinishingValidationError) as captured:
        require_rest_finishing_candidate(candidate)
    assert captured.value.code is RestFinishingDiagnosticCode.SUCCESSOR_INVALID


def test_feed_only_change_preserves_semantic_removal_but_changes_full_identity() -> None:
    first_inputs = _inputs()
    first_preparation, first = _success(first_inputs)
    changed = replace(
        first_inputs.parameters,
        cutting_feed_rate=replace(first_inputs.parameters.cutting_feed_rate, value=450.0),
        plunge_feed_rate=replace(first_inputs.parameters.plunge_feed_rate, value=120.0),
    )
    second_inputs = _with_exact_r273_parameters(first_inputs, changed)
    _second_preparation, second = _success(second_inputs)
    assert first.artifact.artifact_fingerprint != second.artifact.artifact_fingerprint
    assert first.full_toolpath_artifact_fingerprint != second.full_toolpath_artifact_fingerprint
    assert first.semantic_material_removal_fingerprint == second.semantic_material_removal_fingerprint
    assert first_preparation.context.parameters.fingerprint != changed.fingerprint


def test_direct_lifecycle_preparation_constructor_is_rejected() -> None:
    valid = prepare_rest_finishing_3axis(_context())
    forged = RestFinishingLifecyclePreparation(
        valid.status,
        valid.context,
        valid.plan,
        valid.prepared,
        valid.diagnostic_code,
        valid.message,
    )
    result = generate_rest_finishing_3axis(forged)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID


def test_lifecycle_callback_substitution_after_prepare_is_rejected() -> None:
    first = lambda: False
    second = lambda: False
    preparation = prepare_rest_finishing_3axis(_context(cancellation=first))
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    object.__setattr__(preparation.context, "cancellation", second)
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_lifecycle_same_byte_context_substitution_after_prepare_is_rejected() -> None:
    preparation = prepare_rest_finishing_3axis(_context())
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    original = preparation.context.setup
    replacement = replace(original)
    assert replacement == original and replacement is not original
    object.__setattr__(preparation.context, "setup", replacement)
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


@pytest.mark.parametrize(
    "authority_path",
    (
        "tool.cutting_geometry",
        "machine.capabilities",
        "setup.wcs",
        "parameters.final_stock_allowance",
    ),
)
def test_lifecycle_nested_same_byte_context_substitution_is_rejected(
    authority_path: str,
) -> None:
    preparation = prepare_rest_finishing_3axis(_context())
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    context = preparation.context
    if authority_path == "tool.cutting_geometry":
        object.__setattr__(
            context.tool,
            "cutting_geometry",
            replace(context.tool.cutting_geometry),
        )
    elif authority_path == "machine.capabilities":
        object.__setattr__(
            context.machine,
            "capabilities",
            replace(context.machine.capabilities),
        )
    elif authority_path == "setup.wcs":
        object.__setattr__(context.setup, "wcs", replace(context.setup.wcs))
    else:
        object.__setattr__(
            context.parameters,
            "final_stock_allowance",
            replace(context.parameters.final_stock_allowance),
        )
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert result.candidate is None


def test_unsafe_entry_and_low_clearance_link_fail_without_partial_candidate() -> None:
    entry_preparation = prepare_rest_finishing_3axis(
        _context(
            _inputs(
                parameters=_parameters(nominal_target_z=1.5, allowance=0.0),
            )
        )
    )
    assert entry_preparation.status is RestFinishingLifecycleStatus.FAILURE
    assert entry_preparation.diagnostic_code is (
        RestFinishingDiagnosticCode.UNREACHABLE_FINISHING_MATERIAL
    )
    assert entry_preparation.prepared is None

    low = _parameters()
    low = replace(
        low,
        clearance_height=Length(37.0, low.unit),
        retract_height=Length(36.5, low.unit),
    )
    link_preparation = prepare_rest_finishing_3axis(_context(_inputs(parameters=low)))
    assert link_preparation.status is RestFinishingLifecycleStatus.PREPARED
    link = generate_rest_finishing_3axis(link_preparation)
    assert link.status is RestFinishingLifecycleStatus.FAILURE
    assert link.diagnostic_code is RestFinishingDiagnosticCode.LINK_UNSAFE
    assert link.candidate is None


def test_every_positive_gap_is_separated_by_retract_and_reentry() -> None:
    _preparation, candidate = _success()
    cuts = [
        (index, event)
        for index, event in enumerate(candidate.artifact.events)
        if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING
    ]
    assert len(cuts) == sum(len(level.spans) for level in candidate.level_plans)
    for (first_index, _first), (second_index, _second) in zip(cuts, cuts[1:]):
        motions = candidate.artifact.events[first_index + 1:second_index]
        assert any(
            isinstance(event, LinearMove)
            and event.motion_class is MotionClass.RETRACT
            for event in motions
        )
        assert any(
            isinstance(event, LinearMove)
            and event.motion_class is MotionClass.LINK
            for event in motions
        )


@pytest.mark.parametrize("cancel_replay", (1, 2))
def test_cancellation_during_intermediate_or_final_replay_has_no_candidate(
    monkeypatch,
    cancel_replay: int,
) -> None:
    flag = {"value": False}
    preparation = prepare_rest_finishing_3axis(
        _context(cancellation=lambda: flag["value"])
    )
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    actual = finishing_toolpath.calculate_material_state
    calls = {"count": 0}

    def controlled_calculate(**kwargs):
        calls["count"] += 1
        if calls["count"] == cancel_replay:
            flag["value"] = True
            assert kwargs["cancellation"]()
            raise CamValidationError("injected replay cancellation")
        return actual(**kwargs)

    monkeypatch.setattr(
        finishing_toolpath,
        "calculate_material_state",
        controlled_calculate,
    )
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert result.candidate is None


def test_cancellation_during_final_completeness_check_has_no_candidate(monkeypatch) -> None:
    flag = {"value": False}
    preparation = prepare_rest_finishing_3axis(
        _context(cancellation=lambda: flag["value"])
    )
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    actual = finishing_toolpath.derive_rest_finishing_level
    calls = {"count": 0}

    def controlled_derive(plan, state, tip_z, cancellation=None):
        calls["count"] += 1
        if calls["count"] == 2:
            flag["value"] = True
        return actual(plan, state, tip_z, cancellation)

    monkeypatch.setattr(
        finishing_toolpath,
        "derive_rest_finishing_level",
        controlled_derive,
    )
    result = generate_rest_finishing_3axis(preparation)
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert result.candidate is None


def test_cancellation_during_fourth_final_candidate_replay_has_no_success(
    monkeypatch,
) -> None:
    flag = {"value": False}
    cancellation = lambda: flag["value"]
    preparation = prepare_rest_finishing_3axis(
        _context(cancellation=cancellation)
    )
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    actual = finishing_toolpath.calculate_material_state
    calls = {"count": 0}

    def controlled_calculate(**kwargs):
        calls["count"] += 1
        if calls["count"] == 4:
            flag["value"] = True
        return actual(**kwargs)

    monkeypatch.setattr(
        finishing_toolpath,
        "calculate_material_state",
        controlled_calculate,
    )
    result = generate_rest_finishing_3axis(preparation)
    assert calls["count"] == 4
    assert result.status is RestFinishingLifecycleStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert result.candidate is None


def test_finishing_tolerance_cannot_skip_intermediate_stepdown_staging() -> None:
    inputs = _inputs(
        parameters=_parameters(
            nominal_target_z=2.0,
            allowance=0.0,
            tolerance=15.0,
            max_stepdown=10.0,
        )
    )
    preparation, candidate = _success(inputs)
    assert preparation.prepared is not None
    actual_levels = tuple(level.tip_z for level in candidate.level_plans)
    assert actual_levels[:4] == (40.0, 30.0, 20.0, 10.0)
    assert all(
        upper - lower <= inputs.parameters.max_stepdown.value
        + candidate.prepared.predecessor_state.precision.tolerance
        for upper, lower in zip((50.0, *actual_levels[:-1]), actual_levels, strict=True)
    )


def test_relevant_plan_authority_mutation_invalidates_process_minted_prepared() -> None:
    preparation = prepare_rest_finishing_3axis(_context())
    assert preparation.status is RestFinishingLifecycleStatus.PREPARED
    assert preparation.prepared is not None
    parameters = preparation.prepared.plan.authority.parameters
    object.__setattr__(
        parameters,
        "final_stock_allowance",
        Length(parameters.final_stock_allowance.value + 0.1, parameters.unit),
    )
    with pytest.raises(RestFinishingValidationError) as captured:
        require_rest_finishing_prepared(preparation.prepared)
    assert captured.value.code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID


def test_small_positive_volume_is_not_compared_to_length_tolerance() -> None:
    assert finishing_toolpath._removed_volume_is_positive(1.0e-12)
    assert not finishing_toolpath._removed_volume_is_positive(0.0)
    assert not finishing_toolpath._removed_volume_is_positive(float("nan"))
