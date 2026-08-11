"""Stage18A Tranche2 setup transform and physical-readiness regressions."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.qualification import (
    AuthorityClass,
    AxisTravelLimit,
    ClearanceState,
    Coordinate3D,
    EnvelopeDimensions,
    FixtureVerificationState,
    MachineTravelContract,
    Orientation3D,
    PartialCoordinate3D,
    PhysicalTravelState,
    PlacementState,
    ToolReachState,
    WorkOffsetTransform,
    calculate_physical_readiness,
    dumps_level2_record,
    loads_level2_record,
    validate_physical_travel,
)
from tests.unit._stage18a_tranche2_fixtures import (
    level2_record,
    setup_qualification,
    travel_contract,
)


def test_setup_contract_and_level2_record_round_trip_deterministically():
    record = level2_record()
    restored = loads_level2_record(dumps_level2_record(record))

    assert restored == record
    assert dumps_level2_record(restored) == dumps_level2_record(record)
    assert record.setup.work_offset_transform.work_offset == "G54"
    assert record.setup.fixture.verification_state is FixtureVerificationState.OWNER_CONFIRMED


def test_g54_never_assumes_zero_translation_and_g55_is_rejected():
    transform = WorkOffsetTransform(
        "G54", PartialCoordinate3D(), Orientation3D(), "not supplied", AuthorityClass.UNVERIFIED
    )
    state, points = validate_physical_travel(
        (Coordinate3D(0, 0, 0),), transform, travel_contract()
    )
    assert state is PhysicalTravelState.SETUP_TRANSFORM_INVALID
    assert points == ()

    with pytest.raises(CamValidationError, match="G54"):
        replace(transform, work_offset="G55")


def test_program_setup_machine_coordinate_limit_chain_passes_with_authority():
    setup = setup_qualification()
    state, points = validate_physical_travel(
        (Coordinate3D(0, 0, 0), Coordinate3D(100, 80, 40)),
        setup.work_offset_transform,
        travel_contract(),
    )

    assert state is PhysicalTravelState.PHYSICAL_TRAVEL_STATICALLY_VALIDATED
    assert points[0] == Coordinate3D(100, 100, 50)
    assert points[1] == Coordinate3D(200, 180, 90)


def test_physical_endpoints_unavailable_is_not_synthesized_from_spans():
    setup = setup_qualification()
    state, points = validate_physical_travel(
        (Coordinate3D(0, 0, 0),), setup.work_offset_transform, travel_contract(complete=False)
    )

    assert state is PhysicalTravelState.PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE
    assert points == ()


def test_machine_point_outside_authoritative_endpoints_is_blocked():
    setup = setup_qualification()
    limits = MachineTravelContract(
        AxisTravelLimit(0, 150), AxisTravelLimit(0, 150), AxisTravelLimit(0, 80),
        "owner measured", AuthorityClass.OWNER_CONFIRMED,
    )
    state, _ = validate_physical_travel(
        (Coordinate3D(100, 80, 40),), setup.work_offset_transform, limits
    )
    assert state is PhysicalTravelState.PHYSICAL_TRAVEL_OUTSIDE_LIMITS


def test_stock_outside_table_and_fixture_unknown_remain_explicit():
    setup = setup_qualification(fixture_verified=False, with_clearance=False)
    stock = replace(
        setup.stock,
        dimensions=EnvelopeDimensions(200, 200, 40),
        origin_machine_mm=PartialCoordinate3D(600, 300, 0),
    )
    setup = replace(setup, stock=stock)
    result = calculate_physical_readiness(
        setup, (Coordinate3D(0, 0, 0),), travel_contract(),
        table_width_mm=650, table_depth_mm=400,
    )

    assert PlacementState.PLACEMENT_OUTSIDE_TABLE_ENVELOPE in result.placement_states
    assert PlacementState.FIXTURE_PLACEMENT_UNVERIFIED in result.placement_states
    assert "PLACEMENT_OUTSIDE_TABLE_ENVELOPE" in result.blockers


def test_tool_reach_and_unknown_holder_clearance_fail_closed():
    setup = setup_qualification(sufficient_reach=False, with_clearance=False)
    result = calculate_physical_readiness(
        setup, (Coordinate3D(0, 0, 0),), travel_contract(),
        table_width_mm=650, table_depth_mm=400,
    )

    assert result.tool_reach_states == ((1, ToolReachState.TOOL_REACH_INSUFFICIENT),)
    assert result.clearance_state is ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED
    assert any("TOOL_REACH_INSUFFICIENT" in item for item in result.blockers)
    assert "HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED" in result.missing


def test_clearance_binding_becomes_unverified_after_fixture_or_tool_change():
    setup = setup_qualification()
    fixture = replace(setup.fixture, fixture_id="changed-fixture")
    changed_fixture = replace(setup, fixture=fixture)
    fixture_result = calculate_physical_readiness(
        changed_fixture, (Coordinate3D(0, 0, 0),), travel_contract(),
        table_width_mm=650, table_depth_mm=400,
    )
    changed_tool = replace(
        setup.tools[0], holder_fingerprint=ContentFingerprint.from_payload({"changed": "holder"})
    )
    tool_result = calculate_physical_readiness(
        replace(setup, tools=(changed_tool,)), (Coordinate3D(0, 0, 0),), travel_contract(),
        table_width_mm=650, table_depth_mm=400,
    )

    assert fixture_result.clearance_state is ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED
    assert tool_result.clearance_state is ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED
