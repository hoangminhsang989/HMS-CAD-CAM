"""Stage 7B.7.1 tapping domain, unit and machine compatibility tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.application import basic_mill_resources
from hms_cadcam.cam.domain import (
    CamUnitError,
    DiagnosticCode,
    DrillValidationError,
    DrillDepthDefinition,
    DrillGeometryInput,
    FeedRate,
    FeedUnit,
    HoleLocation,
    HolePattern,
    Length,
    LengthUnit,
    MachineDefinition,
    OperationCapability,
    Point3,
    SpindleDirection,
    SpindleSpeed,
    TappingHand,
    TappingMode,
    TappingStrategy,
    TappingSynchronizationPolicy,
    TappingValidationError,
    Vector3,
)


def _pattern(unit: LengthUnit = LengthUnit.MM) -> HolePattern:
    point = Point3(2, 3, 0, unit)
    return HolePattern((HoleLocation(
        point, Vector3(0, 0, 1), point, Length(8, unit), unit,
    ),), unit)


def _strategy(
    hand: TappingHand = TappingHand.RIGHT_HAND_TAP,
    policy: TappingSynchronizationPolicy = TappingSynchronizationPolicy.RIGID,
    **changes,
) -> TappingStrategy:
    unit = LengthUnit.MM
    values = dict(
        unit=unit,
        geometry=DrillGeometryInput(_pattern(unit), unit),
        depth=DrillDepthDefinition(unit, Length(0, unit), Length(-10, unit)),
        nominal_diameter=Length(8, unit),
        pitch=Length(1.25, unit),
        hand=hand,
        spindle_speed=SpindleSpeed(500),
        clearance_height=Length(8, unit),
        retract_height=Length(3, unit),
        synchronization_policy=policy,
        dwell_seconds=0.0,
        tolerance=Length(1.0e-7, unit),
    )
    values.update(changes)
    return TappingStrategy(**values)


@pytest.mark.parametrize("hand", tuple(TappingHand))
@pytest.mark.parametrize("policy", tuple(TappingSynchronizationPolicy))
def test_strategy_is_versioned_deterministic_and_round_trips(hand, policy) -> None:
    strategy = _strategy(hand, policy)

    assert TappingStrategy.from_dict(strategy.to_dict()) == strategy
    assert TappingStrategy.from_operation_parameters(
        strategy.to_operation_parameters()
    ) == strategy
    assert strategy.fingerprint == TappingStrategy.from_dict(
        strategy.to_dict()
    ).fingerprint
    assert strategy.pitch.value > 0.0


def test_invalid_pitch_rpm_depth_unit_and_future_version_fail_closed() -> None:
    strategy = _strategy()
    for change in (
        {"pitch": Length(0, LengthUnit.MM)},
        {"spindle_speed": None},
        {"unit": LengthUnit.UNKNOWN},
    ):
        with pytest.raises(TappingValidationError) as failure:
            replace(strategy, **change)
        assert failure.value.code is DiagnosticCode.TAP_INVALID_PARAMETERS

    with pytest.raises(TappingValidationError) as depth:
        replace(
            strategy,
            depth=DrillDepthDefinition(
                LengthUnit.MM, Length(0, LengthUnit.MM), Length(-20, LengthUnit.MM)
            ),
            retract_height=Length(-1, LengthUnit.MM),
        )
    assert depth.value.code is DiagnosticCode.TAP_UNSAFE_CLEARANCE

    payload = strategy.to_dict()
    payload["format_version"] = 2
    with pytest.raises(TappingValidationError):
        TappingStrategy.from_dict(payload)

    invalid_depth = strategy.to_dict()
    invalid_depth["depth"]["bottom_z"] = 0.0
    invalid_depth["depth"]["depth"] = 0.0
    with pytest.raises(TappingValidationError) as depth_payload:
        TappingStrategy.from_dict(invalid_depth)
    assert depth_payload.value.code is DiagnosticCode.TAP_DEPTH_INVALID


def test_feed_basis_conversion_is_explicit() -> None:
    metric = FeedRate(1.27, FeedUnit.MM_PER_REVOLUTION)
    assert metric.to(FeedUnit.INCH_PER_REVOLUTION) == FeedRate(
        0.05, FeedUnit.INCH_PER_REVOLUTION
    )
    with pytest.raises(CamUnitError):
        metric.to(FeedUnit.MM_PER_MINUTE)


def test_tapping_reuses_canonical_duplicate_safe_hole_pattern() -> None:
    pattern = _pattern()
    with pytest.raises(DrillValidationError) as duplicate:
        HolePattern((pattern.locations[0], pattern.locations[0]), LengthUnit.MM)
    assert duplicate.value.code is DiagnosticCode.DRILL_DUPLICATE_LOCATION


def test_legacy_machine_payload_and_fingerprint_remain_stable() -> None:
    _tool, _holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    machine = replace(
        machine,
        capabilities=replace(machine.capabilities, tapping=True),
    )
    legacy_payload = machine.to_dict()
    legacy_fingerprint = machine.content_fingerprint

    restored = MachineDefinition.from_dict(legacy_payload)

    assert restored.to_dict() == legacy_payload
    assert restored.content_fingerprint == legacy_fingerprint
    assert restored.capabilities.tapping_modes == ()
    assert restored.spindles[0].directions == ()
    assert not restored.spindles[0].synchronized_feed


def test_extended_machine_payload_changes_normalized_fingerprint() -> None:
    _tool, _holder, _assembly, machine = basic_mill_resources(LengthUnit.MM)
    spindle = replace(
        machine.spindles[0],
        directions=(
            SpindleDirection.CLOCKWISE,
            SpindleDirection.COUNTERCLOCKWISE,
        ),
        synchronized_feed=True,
    )
    capabilities = replace(
        machine.capabilities,
        tapping=True,
        operations=(OperationCapability.MILLING, OperationCapability.TAPPING),
        tapping_modes=(TappingMode.RIGID, TappingMode.FLOATING),
    )
    extended = replace(machine, spindles=(spindle,), capabilities=capabilities)

    restored = MachineDefinition.from_dict(extended.to_dict())

    assert restored == extended
    assert extended.content_fingerprint != machine.content_fingerprint
    future = extended.to_dict()
    future["format_version"] = 2
    with pytest.raises(Exception):
        MachineDefinition.from_dict(future)
