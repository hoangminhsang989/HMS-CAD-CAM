"""Immutable numeric, motion, result and stock contracts for Stage 12.1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from enum import Enum
from uuid import UUID

import pytest

from hms_cadcam.cam.domain import CylinderStock, Length, LengthUnit, WcsFrame
from hms_cadcam.cam.lathe.toolpath import (
    LATHE_AXIAL_DRILL_ALGORITHM_VERSION,
    LATHE_OD_FINISH_ALGORITHM_VERSION,
    LATHE_OD_ROUGH_ALGORITHM_VERSION,
    LATHE_TOOLPATH_ALGORITHM_VERSION,
    LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM,
    LatheDwellEvent,
    LatheMotionClass,
    LathePathSegment,
    LatheStockSnapshotV1,
    LatheToolpathDiagnostic,
    LatheToolpathDiagnosticCode,
    LatheToolpathResultState,
    LatheXZPoint,
    lathe_stock_from_cylinder,
)
from tests.unit._lathe_fixtures import setup_id, stable_uuid
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    same_semantics_request,
    stock_snapshot,
)


def _assert_native_free(value: object, seen: set[int] | None = None) -> None:
    visited = seen if seen is not None else set()
    if id(value) in visited:
        return
    visited.add(id(value))
    module = type(value).__module__
    assert not module.startswith(("PySide6", "shiboken6", "OCP"))
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_native_free(getattr(value, field.name), visited)
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_native_free(key, visited)
            _assert_native_free(item, visited)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            _assert_native_free(item, visited)
    elif isinstance(value, (str, int, float, bool, type(None), UUID, Enum)):
        return


def test_algorithm_versions_and_tolerance_are_exact_and_stable() -> None:
    assert LATHE_TOOLPATH_ALGORITHM_VERSION == "lathe.toolpath.preview.v1"
    assert LATHE_OD_ROUGH_ALGORITHM_VERSION == "lathe.od_rough.toolpath.v1"
    assert LATHE_OD_FINISH_ALGORITHM_VERSION == "lathe.od_finish.toolpath.v1"
    assert LATHE_AXIAL_DRILL_ALGORITHM_VERSION == "lathe.axial_drill.toolpath.v1"
    assert LATHE_TOOLPATH_NUMERIC_TOLERANCE_MM == 1.0e-9


def test_xz_point_uses_diameter_and_is_immutable() -> None:
    point = LatheXZPoint(40, -12.5)
    assert point == LatheXZPoint(40.0, -12.5)
    assert point.x_diameter_mm == 40.0
    assert point.z_mm == -12.5
    assert point.distance_to(LatheXZPoint(44.0, -9.5)) == 5.0
    with pytest.raises(FrozenInstanceError):
        point.z_mm = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("value", (True, False))
def test_xz_point_rejects_bool_as_number(value: bool) -> None:
    with pytest.raises(TypeError, match="real number"):
        LatheXZPoint(value, 0.0)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_xz_point_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        LatheXZPoint(1.0, value)


def test_motion_classes_are_exact_and_segments_validate_feed_and_length() -> None:
    assert tuple(item.value for item in LatheMotionClass) == (
        "RAPID",
        "CUTTING",
        "LEAD_IN",
        "LEAD_OUT",
    )
    start = LatheXZPoint(50.0, 0.0)
    end = LatheXZPoint(40.0, 0.0)
    segment = LathePathSegment(
        0,
        LatheMotionClass.LEAD_IN,
        start,
        end,
        "pass.0.lead",
        0.2,
        (("pass_index", 0),),
    )
    assert segment.length_mm == 10.0
    assert segment.metadata == (("pass_index", 0),)
    with pytest.raises(FrozenInstanceError):
        segment.motion_class = LatheMotionClass.RAPID  # type: ignore[misc]
    with pytest.raises(ValueError, match="zero-length"):
        replace(segment, end=start)
    with pytest.raises(ValueError, match="Rapid"):
        replace(segment, motion_class=LatheMotionClass.RAPID)
    with pytest.raises(ValueError, match="positive"):
        replace(segment, feed_mm_per_rev=0.0)


def test_metadata_is_sorted_unique_immutable_and_json_scalar_only() -> None:
    segment = LathePathSegment(
        0,
        LatheMotionClass.CUTTING,
        LatheXZPoint(50.0, 0.0),
        LatheXZPoint(50.0, -1.0),
        "cut",
        0.1,
        (("z", 2), ("a", "one")),
    )
    assert segment.metadata == (("a", "one"), ("z", 2))
    with pytest.raises(ValueError, match="unique"):
        replace(segment, metadata=(("a", 1), ("a", 2)))
    with pytest.raises(TypeError, match="JSON"):
        replace(segment, metadata=(("bad", object()),))


def test_axial_dwell_is_typed_positive_and_not_a_fake_line() -> None:
    event = LatheDwellEvent(
        2,
        LatheXZPoint(0.0, -30.0),
        0.25,
        "axial_drill.final_dwell",
    )
    assert event.duration_seconds == 0.25
    assert not isinstance(event, LathePathSegment)
    with pytest.raises(ValueError, match="positive"):
        replace(event, duration_seconds=0.0)


def test_success_result_is_deterministic_and_terminal_result_cannot_leak_partial_path() -> None:
    _service, _operation, request = ready_request()
    first = generate(request)
    second = generate(same_semantics_request(request))
    assert first.succeeded and second.succeeded
    assert first.motions == second.motions
    assert first.bounds == second.bounds
    assert first.pass_count == second.pass_count
    assert first.cutting_length_mm == second.cutting_length_mm
    assert first.rapid_length_mm == second.rapid_length_mm
    assert first.cache_key == second.cache_key
    with pytest.raises(FrozenInstanceError):
        first.pass_count = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="partial"):
        replace(
            first,
            state=LatheToolpathResultState.CANCELLED,
            diagnostics=(
                LatheToolpathDiagnostic(LatheToolpathDiagnosticCode.CANCELLED),
            ),
        )


def test_request_and_result_graphs_are_qt_ocp_native_free() -> None:
    _service, _operation, request = ready_request()
    result = generate(request)
    _assert_native_free(request)
    _assert_native_free(result)


def test_valid_stock_snapshot_has_normalized_direction_length_and_payload() -> None:
    stock = stock_snapshot()
    assert stock.axial_direction == -1.0
    assert stock.axial_length_mm == 100.0
    assert stock.canonical_payload()["outer_diameter_mm"] == 100.0
    with pytest.raises(FrozenInstanceError):
        stock.generation = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("outer_diameter_mm", 0.0, ValueError),
        ("outer_diameter_mm", -1.0, ValueError),
        ("outer_diameter_mm", True, TypeError),
        ("inner_diameter_mm", -0.1, ValueError),
        ("inner_diameter_mm", 100.0, ValueError),
        ("front_z_mm", float("nan"), ValueError),
        ("back_z_mm", float("inf"), ValueError),
    ),
)
def test_stock_snapshot_rejects_invalid_numeric_envelope(
    field: str, value: object, error: type[Exception]
) -> None:
    values: dict[str, object] = {
        "stock_identity": "stock",
        "source_id": stable_uuid("source/1"),
        "generation": 3,
        "outer_diameter_mm": 100.0,
        "inner_diameter_mm": 0.0,
        "front_z_mm": 0.0,
        "back_z_mm": -100.0,
    }
    values[field] = value
    with pytest.raises(error):
        LatheStockSnapshotV1(**values)  # type: ignore[arg-type]


def test_stock_snapshot_rejects_equal_front_back_nil_source_and_bad_generation() -> None:
    with pytest.raises(ValueError, match="must differ"):
        stock_snapshot(front_z_mm=1.0, back_z_mm=1.0)
    with pytest.raises(ValueError, match="source"):
        LatheStockSnapshotV1("stock", UUID(int=0), 0, 10.0, 0.0, 0.0, -1.0)
    with pytest.raises(ValueError, match="generation"):
        LatheStockSnapshotV1(
            "stock", stable_uuid("source/1"), -1, 10.0, 0.0, 0.0, -1.0
        )


def test_cylinder_stock_adapter_is_deterministic_and_does_not_modify_source() -> None:
    cylinder = CylinderStock(
        Length(80.0, LengthUnit.MM),
        Length(120.0, LengthUnit.MM),
        WcsFrame.identity(LengthUnit.MM),
    )
    before = cylinder.to_dict()
    first = lathe_stock_from_cylinder(
        cylinder,
        setup_id=setup_id(),
        source_id=stable_uuid("source/1"),
        generation=3,
    )
    second = lathe_stock_from_cylinder(
        cylinder,
        setup_id=setup_id(),
        source_id=stable_uuid("source/1"),
        generation=3,
    )
    assert first == second
    assert first.outer_diameter_mm == 80.0
    assert first.inner_diameter_mm == 0.0
    assert (first.front_z_mm, first.back_z_mm) == (0.0, -120.0)
    assert first.stock_identity.startswith("lathe-stock-sha256:")
    assert cylinder.to_dict() == before
