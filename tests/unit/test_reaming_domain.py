"""Stage 7B.8.1 Reaming immutable domain and codec tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import (
    DiagnosticCode,
    DrillDepthDefinition,
    DrillGeometryInput,
    FeedRate,
    FeedUnit,
    HoleLocation,
    HolePattern,
    Length,
    LengthUnit,
    Point3,
    ReamingCoolantMode,
    ReamingRetractPolicy,
    ReamingStrategy,
    ReamingValidationError,
    SpindleDirection,
    SpindleSpeed,
    Vector3,
)


def _strategy(unit: LengthUnit = LengthUnit.MM, **changes) -> ReamingStrategy:
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    point = Point3(2 * scale, 3 * scale, 0, unit)
    pattern = HolePattern((HoleLocation(
        point, Vector3(0, 0, 1), point, None, unit,
    ),), unit)
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(pattern, unit),
        depth=DrillDepthDefinition(
            unit, Length(0, unit), Length(-10 * scale, unit)
        ),
        nominal_diameter=Length(8 * scale, unit),
        pre_hole_diameter=Length(7.8 * scale, unit),
        spindle_speed=SpindleSpeed(500),
        feed_per_revolution=FeedRate(
            0.1 * scale,
            FeedUnit.MM_PER_REVOLUTION
            if unit is LengthUnit.MM else FeedUnit.INCH_PER_REVOLUTION,
        ),
        clearance_height=Length(8 * scale, unit),
        retract_height=Length(3 * scale, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        retract_policy=ReamingRetractPolicy.CONTROLLED_FEED,
        coolant=ReamingCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7 * scale, unit),
    )
    values.update(changes)
    return ReamingStrategy(**values)


@pytest.mark.parametrize("unit", (LengthUnit.MM, LengthUnit.INCH))
def test_strategy_is_versioned_deterministic_and_round_trips(unit) -> None:
    strategy = _strategy(unit)

    restored = ReamingStrategy.from_dict(strategy.to_dict())
    parameters = ReamingStrategy.from_operation_parameters(
        strategy.to_operation_parameters()
    )

    assert restored == strategy == parameters
    assert restored.fingerprint == strategy.fingerprint
    assert strategy.stock_per_side.value == pytest.approx(
        (strategy.nominal_diameter.value - strategy.pre_hole_diameter.value) / 2
    )
    assert strategy.feed_per_minute.value == pytest.approx(
        strategy.feed_per_revolution.value * strategy.spindle_speed.value
    )
    payload = strategy.to_dict()
    assert "stock_per_side" not in payload
    assert "feed_per_minute" not in payload


@pytest.mark.parametrize(
    ("change", "code"),
    (
        ({"unit": LengthUnit.UNKNOWN}, DiagnosticCode.REAM_INVALID_PARAMETERS),
        ({"nominal_diameter": Length(0, LengthUnit.MM)},
         DiagnosticCode.REAM_INVALID_PARAMETERS),
        ({"pre_hole_diameter": None}, DiagnosticCode.REAM_PREHOLE_MISSING),
        ({"pre_hole_diameter": Length(0, LengthUnit.MM)},
         DiagnosticCode.REAM_PREHOLE_INVALID),
        ({"pre_hole_diameter": Length(-1, LengthUnit.MM)},
         DiagnosticCode.REAM_PREHOLE_INVALID),
        ({"pre_hole_diameter": Length(8, LengthUnit.MM)},
         DiagnosticCode.REAM_PREHOLE_INVALID),
        ({"pre_hole_diameter": Length(9, LengthUnit.MM)},
         DiagnosticCode.REAM_PREHOLE_INVALID),
        ({"pre_hole_diameter": Length(7.9999999, LengthUnit.MM)},
         DiagnosticCode.REAM_STOCK_INVALID),
        ({"pre_hole_diameter": Length(1.0e-8, LengthUnit.MM)},
         DiagnosticCode.REAM_STOCK_INVALID),
        ({"spindle_speed": None}, DiagnosticCode.REAM_INVALID_PARAMETERS),
        ({"feed_per_revolution": FeedRate(1, FeedUnit.MM_PER_MINUTE)},
         DiagnosticCode.REAM_INVALID_PARAMETERS),
        ({"retract_height": Length(-1, LengthUnit.MM)},
         DiagnosticCode.REAM_UNSAFE_CLEARANCE),
    ),
)
def test_invalid_units_parameters_prehole_stock_and_clearance_fail_closed(
    change, code
) -> None:
    with pytest.raises(ReamingValidationError) as failure:
        replace(_strategy(), **change)
    assert failure.value.code is code


def test_non_finite_payload_and_future_version_are_rejected() -> None:
    strategy = _strategy()
    non_finite = strategy.to_dict()
    non_finite["dwell_seconds"] = float("nan")
    with pytest.raises(ReamingValidationError) as dwell:
        ReamingStrategy.from_dict(non_finite)
    assert dwell.value.code is DiagnosticCode.REAM_INVALID_PARAMETERS

    invalid_depth = strategy.to_dict()
    invalid_depth["depth"]["bottom_z"] = 0.0
    invalid_depth["depth"]["depth"] = 0.0
    with pytest.raises(ReamingValidationError) as depth:
        ReamingStrategy.from_dict(invalid_depth)
    assert depth.value.code is DiagnosticCode.REAM_DEPTH_INVALID

    future = strategy.to_dict()
    future["format_version"] = 2
    with pytest.raises(ReamingValidationError):
        ReamingStrategy.from_dict(future)

    future_strategy = strategy.to_dict()
    future_strategy["strategy_version"] = 2
    with pytest.raises(ReamingValidationError):
        ReamingStrategy.from_dict(future_strategy)

    missing_prehole = strategy.to_dict()
    missing_prehole.pop("pre_hole_diameter")
    with pytest.raises(ReamingValidationError) as missing:
        ReamingStrategy.from_dict(missing_prehole)
    assert missing.value.code is DiagnosticCode.REAM_PREHOLE_MISSING


def test_reaming_reuses_duplicate_safe_canonical_hole_pattern() -> None:
    first = _strategy().geometry.source.locations[0]
    second_point = Point3(5, 3, 0, LengthUnit.MM)
    second = HoleLocation(
        second_point, Vector3(0, 0, 1), second_point, None, LengthUnit.MM
    )
    forward = HolePattern((second, first), LengthUnit.MM)
    reverse = HolePattern((first, second), LengthUnit.MM)
    assert forward == reverse
    assert forward.fingerprint == reverse.fingerprint

    near = (
        HoleLocation(
            Point3(0.49e-8, 0, 0, LengthUnit.MM), Vector3(0, 0, 1),
            Point3(0.49e-8, 0, 0, LengthUnit.MM), None, LengthUnit.MM,
        ),
        HoleLocation(
            Point3(0.51e-8, 0, 0, LengthUnit.MM), Vector3(0, 0, 1),
            Point3(0.51e-8, 0, 0, LengthUnit.MM), None, LengthUnit.MM,
        ),
    )
    with pytest.raises(Exception) as duplicate:
        HolePattern(near, LengthUnit.MM)
    assert getattr(duplicate.value, "code", None) is DiagnosticCode.DRILL_DUPLICATE_LOCATION
