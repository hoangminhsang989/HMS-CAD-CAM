"""Exact schema, metadata, validation, and default tests for Lathe V1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from hms_cadcam.cam.lathe.parameters import (
    COMMON_PARAMETER_IDS,
    LATHE_PARAMETER_SCHEMAS,
    LatheParameterState,
    LatheParameterUpdate,
    LatheParameterValidationError,
    build_lathe_v1_defaults,
    lathe_parameter_schema,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheParameterGroup,
    LatheParameterUnitKind,
    LatheSpindleDirection,
    LatheStrategyId,
    LatheThreadHand,
)


SPECIFIC_IDS = {
    LatheStrategyId.FACE: (
        "face_z_mm", "outer_diameter_mm", "inner_diameter_mm",
        "max_depth_of_cut_mm", "finish_allowance_mm",
    ),
    LatheStrategyId.OD_ROUGH: (
        "start_z_mm", "end_z_mm", "target_diameter_mm", "max_depth_of_cut_mm",
        "radial_stock_to_leave_mm", "axial_stock_to_leave_mm",
    ),
    LatheStrategyId.OD_FINISH: (
        "start_z_mm", "end_z_mm", "target_diameter_mm", "finish_passes", "spring_passes",
    ),
    LatheStrategyId.ID_ROUGH: (
        "start_z_mm", "end_z_mm", "target_diameter_mm", "max_depth_of_cut_mm",
        "radial_stock_to_leave_mm", "axial_stock_to_leave_mm",
    ),
    LatheStrategyId.ID_FINISH: (
        "start_z_mm", "end_z_mm", "target_diameter_mm", "finish_passes", "spring_passes",
    ),
    LatheStrategyId.OD_GROOVE: (
        "center_z_mm", "groove_width_mm", "target_diameter_mm", "max_step_mm", "side_allowance_mm",
    ),
    LatheStrategyId.ID_GROOVE: (
        "center_z_mm", "groove_width_mm", "target_diameter_mm", "max_step_mm", "side_allowance_mm",
    ),
    LatheStrategyId.PART_OFF: (
        "cutoff_z_mm", "target_diameter_mm", "max_step_mm", "side_clearance_mm",
    ),
    LatheStrategyId.OD_THREAD: (
        "start_z_mm", "end_z_mm", "major_diameter_mm", "minor_diameter_mm",
        "pitch_mm", "thread_hand", "pass_count", "spring_passes", "infeed_angle_deg",
    ),
    LatheStrategyId.ID_THREAD: (
        "start_z_mm", "end_z_mm", "major_diameter_mm", "minor_diameter_mm",
        "pitch_mm", "thread_hand", "pass_count", "spring_passes", "infeed_angle_deg",
    ),
    LatheStrategyId.AXIAL_DRILL: (
        "depth_mm", "retract_plane_z_mm", "peck_depth_mm", "dwell_seconds",
    ),
}


EXPECTED_DEFAULTS = {
    LatheStrategyId.FACE: (0.0, 50.0, 0.0, 1.0, 0.2),
    LatheStrategyId.OD_ROUGH: (0.0, -50.0, 40.0, 2.0, 0.5, 0.2),
    LatheStrategyId.OD_FINISH: (0.0, -50.0, 40.0, 1, 0),
    LatheStrategyId.ID_ROUGH: (0.0, -30.0, 20.0, 1.0, 0.3, 0.2),
    LatheStrategyId.ID_FINISH: (0.0, -30.0, 20.0, 1, 0),
    LatheStrategyId.OD_GROOVE: (-20.0, 3.0, 35.0, 1.0, 0.1),
    LatheStrategyId.ID_GROOVE: (-20.0, 3.0, 25.0, 1.0, 0.1),
    LatheStrategyId.PART_OFF: (-50.0, 0.0, 1.0, 0.2),
    LatheStrategyId.OD_THREAD: (0.0, -30.0, 20.0, 18.0, 1.5, LatheThreadHand.RIGHT, 8, 1, 29.0),
    LatheStrategyId.ID_THREAD: (0.0, -30.0, 20.0, 18.0, 1.5, LatheThreadHand.RIGHT, 8, 1, 29.0),
    LatheStrategyId.AXIAL_DRILL: (30.0, 2.0, None, 0.0),
}


def test_every_strategy_has_exact_ordered_schema_and_common_envelope() -> None:
    assert tuple(item.strategy_id for item in LATHE_PARAMETER_SCHEMAS) == tuple(
        LatheStrategyId
    )
    assert len(LATHE_PARAMETER_SCHEMAS) == 11
    for strategy_id in LatheStrategyId:
        schema = lathe_parameter_schema(strategy_id)
        ids = tuple(item.parameter_id for item in schema.descriptors)
        assert ids == (*COMMON_PARAMETER_IDS, *SPECIFIC_IDS[strategy_id])
        assert len(ids) == len(set(ids))
        assert tuple(item.order for item in schema.descriptors) == tuple(
            sorted(item.order for item in schema.descriptors)
        )


def test_parameter_groups_units_and_semantic_keys_are_exact() -> None:
    schema = lathe_parameter_schema(LatheStrategyId.OD_THREAD)
    descriptors = {item.parameter_id: item for item in schema.descriptors}
    assert descriptors["spindle_speed_rpm"].unit_kind is LatheParameterUnitKind.RPM
    assert descriptors["feed_mm_per_rev"].unit_kind is LatheParameterUnitKind.MM_PER_REVOLUTION
    assert descriptors["infeed_angle_deg"].unit_kind is LatheParameterUnitKind.DEGREE
    assert descriptors["spindle_speed_rpm"].group is LatheParameterGroup.BASIC
    assert descriptors["retract_mm"].group is LatheParameterGroup.ADVANCED
    assert descriptors["pass_count"].group is LatheParameterGroup.ADVANCED
    assert descriptors["thread_hand"].enum_values == ("RIGHT", "LEFT")
    for descriptor in schema.descriptors:
        assert descriptor.label_key == f"lathe.parameter.{descriptor.parameter_id}.label"
        assert descriptor.help_key == f"lathe.parameter.{descriptor.parameter_id}.help"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "1.5"])
def test_float_validation_rejects_nonfinite_bool_and_string(bad: object) -> None:
    state = build_lathe_v1_defaults(LatheStrategyId.FACE)
    values = dict(state.values)
    values["outer_diameter_mm"] = bad
    with pytest.raises(LatheParameterValidationError) as raised:
        LatheParameterState.build(LatheStrategyId.FACE, values)
    assert raised.value.diagnostics[0].code is LatheDiagnosticCode.INVALID_PARAMETER


@pytest.mark.parametrize("bad", [True, 1.0, "1", 0, -1])
def test_integer_validation_is_exact_and_constrained(bad: object) -> None:
    state = build_lathe_v1_defaults(LatheStrategyId.OD_FINISH)
    values = dict(state.values)
    values["finish_passes"] = bad
    with pytest.raises(LatheParameterValidationError):
        LatheParameterState.build(LatheStrategyId.OD_FINISH, values)


@pytest.mark.parametrize(
    ("strategy_id", "updates"),
    [
        (LatheStrategyId.FACE, {"inner_diameter_mm": 50.0}),
        (LatheStrategyId.OD_ROUGH, {"end_z_mm": 0.0}),
        (LatheStrategyId.ID_FINISH, {"end_z_mm": 0.0}),
        (LatheStrategyId.OD_THREAD, {"minor_diameter_mm": 20.0}),
        (LatheStrategyId.ID_THREAD, {"end_z_mm": 0.0}),
        (LatheStrategyId.OD_THREAD, {"pitch_mm": 0.0}),
        (LatheStrategyId.ID_THREAD, {"pass_count": 0}),
        (LatheStrategyId.OD_THREAD, {"spring_passes": -1}),
        (LatheStrategyId.ID_THREAD, {"infeed_angle_deg": 90.0}),
    ],
)
def test_cross_field_and_bound_constraints_fail_closed(
    strategy_id: LatheStrategyId, updates: dict[str, object]
) -> None:
    values = dict(build_lathe_v1_defaults(strategy_id).values)
    values.update(updates)
    with pytest.raises(LatheParameterValidationError):
        LatheParameterState.build(strategy_id, values)


def test_optional_peck_and_exact_enum_types() -> None:
    drill = build_lathe_v1_defaults(LatheStrategyId.AXIAL_DRILL)
    assert drill.value("peck_depth_mm") is None
    assert drill.with_updates(
        (LatheParameterUpdate("peck_depth_mm", 2),)
    ).value("peck_depth_mm") == 2.0
    with pytest.raises(LatheParameterValidationError):
        drill.with_updates((LatheParameterUpdate("peck_depth_mm", 0.0),))
    face = build_lathe_v1_defaults(LatheStrategyId.FACE)
    assert face.value("spindle_direction") is LatheSpindleDirection.CW
    with pytest.raises(LatheParameterValidationError):
        face.with_updates((LatheParameterUpdate("spindle_direction", "CW"),))


def test_unknown_missing_and_duplicate_parameter_ids_are_rejected() -> None:
    state = build_lathe_v1_defaults(LatheStrategyId.FACE)
    values = dict(state.values)
    values["extra"] = 1.0
    with pytest.raises(LatheParameterValidationError):
        LatheParameterState.build(LatheStrategyId.FACE, values)
    values = dict(state.values)
    del values["clearance_mm"]
    with pytest.raises(LatheParameterValidationError):
        LatheParameterState.build(LatheStrategyId.FACE, values)
    with pytest.raises(ValueError, match="unique"):
        state.with_updates(
            (
                LatheParameterUpdate("feed_mm_per_rev", 0.1),
                LatheParameterUpdate("feed_mm_per_rev", 0.2),
            )
        )


def test_exact_defaults_are_valid_deterministic_and_immutable() -> None:
    for strategy_id in LatheStrategyId:
        first = build_lathe_v1_defaults(strategy_id)
        second = build_lathe_v1_defaults(strategy_id)
        assert first == second
        assert tuple(first.value(name) for name in SPECIFIC_IDS[strategy_id]) == EXPECTED_DEFAULTS[strategy_id]
        assert tuple(first.value(name) for name in COMMON_PARAMETER_IDS) == (
            1000.0,
            0.2,
            2.0,
            1.0,
            LatheSpindleDirection.CW,
        )
        with pytest.raises(TypeError):
            first.mapping["feed_mm_per_rev"] = 0.5  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.values = ()  # type: ignore[misc]


def test_multi_parameter_update_is_atomic() -> None:
    original = build_lathe_v1_defaults(LatheStrategyId.FACE)
    with pytest.raises(LatheParameterValidationError):
        original.with_updates(
            (
                LatheParameterUpdate("outer_diameter_mm", 80.0),
                LatheParameterUpdate("inner_diameter_mm", 90.0),
            )
        )
    assert original.value("outer_diameter_mm") == 50.0
    assert original.value("inner_diameter_mm") == 0.0
