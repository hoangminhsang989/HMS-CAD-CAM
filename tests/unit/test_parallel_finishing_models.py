"""Domain, frame, bounds and pass-plan tests for Stage 8A.2.1."""

from __future__ import annotations

import dataclasses
import math

import pytest

from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ParallelCutDirection,
    ParallelFinishingError,
    ParallelFinishingParameters,
    ParallelLinkingMode,
    ParallelRegionBounds,
    build_frame_axes,
    build_machining_frame,
    calculate_region_bounds,
    plan_pass_positions,
)
from hms_cadcam.cam.cam3d import PartSurfaceSet
from hms_cadcam.cam.domain import (
    CamValidationError,
    GeometryReferenceId,
    OperationParameterSet,
    Vector3,
)
from tests.unit._parallel_finishing_fixtures import planar_fixture


def test_algorithm_version_records_collision_safety_revision() -> None:
    assert PARALLEL_FINISHING_ALGORITHM_VERSION == 3


def test_parameters_defaults_round_trip_and_fingerprint() -> None:
    fixture = planar_fixture()
    value = ParallelFinishingParameters(fixture.zone.zone_id, 1.25)
    restored = ParallelFinishingParameters.from_operation_parameters(
        value.to_operation_parameters()
    )
    assert restored == value
    assert restored.cut_direction is ParallelCutDirection.ONE_WAY
    assert restored.linking_mode is ParallelLinkingMode.RETRACT_BETWEEN_SEGMENTS
    assert restored.fingerprint == value.fingerprint


def test_parameters_accept_legacy_v1_without_maximum_segment_length() -> None:
    fixture = planar_fixture()
    current = ParallelFinishingParameters(fixture.zone.zone_id, 1.0)
    payload = current.to_operation_parameters()
    legacy = OperationParameterSet(
        payload.strategy_key,
        payload.strategy_version,
        tuple(item for item in payload.values if item[0] != "maximum_segment_length_mm"),
    )
    restored = ParallelFinishingParameters.from_operation_parameters(legacy)
    assert restored.maximum_segment_length_mm == 2.0


@pytest.mark.parametrize(
    "field,value",
    [
        ("stepover_mm", 0.0),
        ("stepover_mm", -1.0),
        ("stepover_mm", math.inf),
        ("feed_rate_mm_per_minute", 0.0),
        ("maximum_segment_length_mm", math.nan),
    ],
)
def test_parameters_reject_invalid_values(field: str, value: float) -> None:
    fixture = planar_fixture()
    current = ParallelFinishingParameters(fixture.zone.zone_id, 1.0)
    with pytest.raises(CamValidationError):
        dataclasses.replace(current, **{field: value})


def test_frame_is_normalized_orthogonal_right_handed_and_deterministic() -> None:
    fixture = planar_fixture()
    first = build_machining_frame(fixture.zone, 30.0, epsilon=1.0e-9)
    second = build_machining_frame(fixture.zone, 390.0, epsilon=1.0e-9)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.u_axis.magnitude == pytest.approx(1.0)
    assert first.v_axis.magnitude == pytest.approx(1.0)
    assert first.w_axis.magnitude == pytest.approx(1.0)
    assert first.u_axis.dot(first.v_axis) == pytest.approx(0.0, abs=1.0e-12)
    assert first.u_axis.cross(first.v_axis).dot(first.w_axis) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "direction,tool_axis",
    [
        (Vector3(0.0, 0.0, 0.0), Vector3(0.0, 0.0, 1.0)),
        (Vector3(0.0, 0.0, 1.0), Vector3(0.0, 0.0, 1.0)),
        (Vector3(1.0, 0.0, 0.0), Vector3(0.0, 0.0, 0.0)),
    ],
)
def test_frame_rejects_zero_or_parallel_direction(
    direction: Vector3, tool_axis: Vector3
) -> None:
    with pytest.raises(ParallelFinishingError) as captured:
        build_frame_axes(direction, tool_axis, 0.0, epsilon=1.0e-9)
    assert captured.value.code.value == "parallel.zero_direction"


def test_region_bounds_use_only_selected_part_faces() -> None:
    fixture = planar_fixture()
    frame = build_machining_frame(fixture.zone, 0.0, epsilon=1.0e-9)
    bounds = calculate_region_bounds(
        fixture.mesh, frame, fixture.zone, padding=0.001
    )
    assert bounds.u_min == pytest.approx(-0.001)
    assert bounds.u_max == pytest.approx(10.001)
    assert bounds.v_min == pytest.approx(0.0)
    assert bounds.v_max == pytest.approx(10.0)


def test_region_bounds_reject_null_mesh_and_missing_selected_face() -> None:
    fixture = planar_fixture()
    frame = build_machining_frame(fixture.zone, 0.0, epsilon=1.0e-9)
    with pytest.raises(ParallelFinishingError) as null_shape:
        calculate_region_bounds(None, frame, fixture.zone, padding=0.001)  # type: ignore[arg-type]
    assert null_shape.value.code.value == "parallel.null_shape"

    selected = fixture.zone.part_surfaces.selection.surfaces[0]
    missing_surface = dataclasses.replace(
        selected,
        geometry=dataclasses.replace(
            selected.geometry,
            reference_id=GeometryReferenceId.new(),
        ),
    )
    missing_zone = dataclasses.replace(
        fixture.zone,
        part_surfaces=PartSurfaceSet(
            dataclasses.replace(
                fixture.zone.part_surfaces.selection,
                surfaces=(missing_surface,),
            )
        ),
    )
    with pytest.raises(ParallelFinishingError) as missing_face:
        calculate_region_bounds(fixture.mesh, frame, missing_zone, padding=0.001)
    assert missing_face.value.code.value == "parallel.missing_face"


def test_pass_positions_include_edges_spacing_and_remainder_without_duplicates() -> None:
    bounds = ParallelRegionBounds(0.0, 10.0, 0.0, 10.0, 0.0, 1.0)
    positions = plan_pass_positions(bounds, 3.0, tolerance=0.001)
    assert positions == (0.0, 3.0, 6.0, 9.0, 10.0)
    assert len(positions) == len(set(positions))


def test_pass_positions_enforce_guardrail() -> None:
    bounds = ParallelRegionBounds(0.0, 1.0, 0.0, 100.0, 0.0, 1.0)
    with pytest.raises(ParallelFinishingError) as captured:
        plan_pass_positions(bounds, 0.01, tolerance=1.0e-6, max_passes=100)
    assert captured.value.code.value == "parallel.limit_exceeded"
