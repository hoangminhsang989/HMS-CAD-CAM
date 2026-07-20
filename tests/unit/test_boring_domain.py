"""Stage 7B.9.1 Boring strategy domain and codec tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import (
    BoringCoolantMode,
    BoringStrategy,
    BoringValidationError,
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
    SpindleDirection,
    SpindleSpeed,
    Vector3,
)


def _pattern(unit: LengthUnit) -> HolePattern:
    values = tuple(
        HoleLocation(
            Point3(x, y, 0, unit),
            Vector3(0, 0, 1),
            Point3(x, y, 0, unit),
            None,
            unit,
        )
        for x, y in ((4, 2), (0, 0))
    )
    return HolePattern(values, unit)


def _strategy(
    unit: LengthUnit = LengthUnit.MM,
    **changes,
) -> BoringStrategy:
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit is LengthUnit.MM
        else FeedUnit.INCH_PER_REVOLUTION
    )
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(_pattern(unit), unit),
        depth=DrillDepthDefinition(
            unit, Length(0, unit), Length(-10, unit)
        ),
        finished_bore_diameter=Length(20, unit),
        pre_bore_diameter=Length(18, unit),
        spindle_rpm=SpindleSpeed(600),
        feed_per_revolution=FeedRate(0.1, feed_unit),
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        spindle_direction=SpindleDirection.CLOCKWISE,
        coolant=BoringCoolantMode.OFF,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return BoringStrategy(**values)


@pytest.mark.parametrize("unit", (LengthUnit.MM, LengthUnit.INCH))
def test_boring_strategy_is_versioned_deterministic_and_round_trips(unit) -> None:
    strategy = _strategy(unit)

    restored = BoringStrategy.from_dict(strategy.to_dict())
    parameters = BoringStrategy.from_operation_parameters(
        strategy.to_operation_parameters()
    )

    assert restored == strategy == parameters
    assert restored.fingerprint == strategy.fingerprint
    assert strategy.radial_stock.value == pytest.approx(1.0)
    assert strategy.feed_per_minute.value == pytest.approx(60.0)
    payload = strategy.to_dict()
    assert payload["strategy_key"] == "boring_v1"
    assert payload["strategy_version"] == 1
    assert "radial_stock" not in payload
    assert "feed_per_minute" not in payload


@pytest.mark.parametrize(
    ("change", "code"),
    (
        ({"unit": LengthUnit.UNKNOWN}, DiagnosticCode.BORE_INVALID_PARAMETERS),
        (
            {"finished_bore_diameter": Length(0, LengthUnit.MM)},
            DiagnosticCode.BORE_INVALID_PARAMETERS,
        ),
        ({"pre_bore_diameter": None}, DiagnosticCode.BORE_PREBORE_MISSING),
        (
            {"pre_bore_diameter": Length(0, LengthUnit.MM)},
            DiagnosticCode.BORE_PREBORE_INVALID,
        ),
        (
            {"pre_bore_diameter": Length(-1, LengthUnit.MM)},
            DiagnosticCode.BORE_PREBORE_INVALID,
        ),
        (
            {"pre_bore_diameter": Length(20, LengthUnit.MM)},
            DiagnosticCode.BORE_PREBORE_INVALID,
        ),
        (
            {"pre_bore_diameter": Length(20 - 1.0e-7, LengthUnit.MM)},
            DiagnosticCode.BORE_STOCK_INVALID,
        ),
        (
            {"pre_bore_diameter": Length(1.0e-7, LengthUnit.MM)},
            DiagnosticCode.BORE_STOCK_INVALID,
        ),
        ({"spindle_rpm": None}, DiagnosticCode.BORE_INVALID_PARAMETERS),
        (
            {"feed_per_revolution": FeedRate(1, FeedUnit.MM_PER_MINUTE)},
            DiagnosticCode.BORE_INVALID_PARAMETERS,
        ),
        (
            {"retract_height": Length(-1, LengthUnit.MM)},
            DiagnosticCode.BORE_UNSAFE_CLEARANCE,
        ),
        ({"dwell_seconds": -1}, DiagnosticCode.BORE_INVALID_PARAMETERS),
    ),
)
def test_boring_invalid_parameters_fail_closed(change, code) -> None:
    with pytest.raises(BoringValidationError) as failure:
        replace(_strategy(), **change)
    assert failure.value.code is code


def test_boring_non_finite_depth_and_future_versions_are_rejected() -> None:
    strategy = _strategy()
    for field in (
        "finished_bore_diameter",
        "pre_bore_diameter",
        "spindle_rpm",
        "feed_per_revolution",
        "dwell_seconds",
        "tolerance",
    ):
        payload = strategy.to_dict()
        payload[field] = float("nan")
        with pytest.raises(BoringValidationError):
            BoringStrategy.from_dict(payload)

    invalid_depth = strategy.to_dict()
    invalid_depth["depth"]["bottom_z"] = 0.0
    invalid_depth["depth"]["depth"] = 0.0
    with pytest.raises(BoringValidationError) as depth:
        BoringStrategy.from_dict(invalid_depth)
    assert depth.value.code is DiagnosticCode.BORE_DEPTH_INVALID

    future_format = strategy.to_dict()
    future_format["format_version"] = 2
    with pytest.raises(BoringValidationError):
        BoringStrategy.from_dict(future_format)

    future_strategy = strategy.to_dict()
    future_strategy["strategy_version"] = 2
    with pytest.raises(BoringValidationError):
        BoringStrategy.from_dict(future_strategy)

    missing_prebore = strategy.to_dict()
    missing_prebore.pop("pre_bore_diameter")
    with pytest.raises(BoringValidationError) as missing:
        BoringStrategy.from_dict(missing_prebore)
    assert missing.value.code is DiagnosticCode.BORE_PREBORE_MISSING


def test_canonical_multi_hole_order_is_part_of_boring_fingerprint() -> None:
    strategy = _strategy()
    locations = strategy.geometry.source.locations
    reversed_pattern = HolePattern(tuple(reversed(locations)), LengthUnit.MM)
    reversed_strategy = replace(
        strategy,
        geometry=DrillGeometryInput(reversed_pattern, LengthUnit.MM),
    )

    assert reversed_strategy.geometry == strategy.geometry
    assert reversed_strategy.fingerprint == strategy.fingerprint
