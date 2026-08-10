"""Stage17A Tranche2 Qt-free Contour automatic-policy tests."""

from dataclasses import replace
import math

import pytest

from hms_cadcam.cam.automatic_contour import (
    ContourAutomaticContext,
    ContourAutomaticLeadForm,
    ContourAutomaticLeadPlacement,
    contour_automatic_lead_points,
    resolve_contour_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourSegment,
    ContourSide,
    LengthUnit,
    Point3,
    ToolFamily,
)


def _rectangle(size: float, unit: LengthUnit = LengthUnit.MM) -> ContourLoop:
    half = size / 2.0
    points = (
        Point3(-half, -half, 0.0, unit),
        Point3(half, -half, 0.0, unit),
        Point3(half, half, 0.0, unit),
        Point3(-half, half, 0.0, unit),
    )
    return ContourLoop(
        tuple(
            ContourSegment(
                ContourCurveKind.LINE,
                points[index],
                points[(index + 1) % len(points)],
            )
            for index in range(len(points))
        ),
        ContourOrientation.COUNTERCLOCKWISE,
    )


def _circle(radius: float = 10.0) -> ContourLoop:
    unit = LengthUnit.MM
    left = Point3(-radius, 0.0, 0.0, unit)
    right = Point3(radius, 0.0, 0.0, unit)
    center = Point3(0.0, 0.0, 0.0, unit)
    return ContourLoop(
        (
            ContourSegment(
                ContourCurveKind.ARC, left, right, center, -math.pi
            ),
            ContourSegment(
                ContourCurveKind.ARC, right, left, center, -math.pi
            ),
        ),
        ContourOrientation.CLOCKWISE,
    )


def _context(**changes: object) -> ContourAutomaticContext:
    values: dict[str, object] = {
        "unit": LengthUnit.MM,
        "tool_family": ToolFamily.END_MILL,
        "diameter": 10.0,
        "corner_radius": None,
        "axial_cutting_length": 20.0,
        "assembly_stickout": 30.0,
        "depth_span": 8.0,
        "tolerance": 0.01,
        "side": ContourSide.ON,
        "multiple_depth_passes": True,
        "machining_loop": _rectangle(20.0),
        "source_loop": _rectangle(20.0),
        "profile_fingerprint": "profile-sha256",
        "tool_fingerprint": "tool-sha256",
    }
    values.update(changes)
    return ContourAutomaticContext(**values)  # type: ignore[arg-type]


def test_rectangle_policy_is_deterministic_bounded_and_uses_validated_fallback() -> None:
    first = resolve_contour_automatic_contract(_context())
    second = resolve_contour_automatic_contract(_context())

    assert first.to_json() == second.to_json()
    assert first.effective_fingerprint == second.effective_fingerprint
    stepdown = first.value("stepdown")
    assert stepdown.mode is AutomaticParameterMode.AUTO
    assert stepdown.effective_value == 8.0
    assert 0.0 < float(stepdown.effective_value) <= 8.0
    assert first.value("lead_form").effective_value == (
        ContourAutomaticLeadForm.NORMAL_LINEAR.value
    )
    assert first.value("lead_in_length").status is AutomaticParameterStatus.RESOLVED
    assert first.value("lead_out_length").status is AutomaticParameterStatus.RESOLVED


def test_arc_profile_prefers_tangent_and_revalidates_exact_lead_paths() -> None:
    loop = _circle()
    contract = resolve_contour_automatic_contract(
        _context(machining_loop=loop, source_loop=loop)
    )
    assert contract.value("lead_form").effective_value == (
        ContourAutomaticLeadForm.TANGENT_LINEAR.value
    )
    placement = ContourAutomaticLeadPlacement(
        int(contract.value("entry_segment_index").effective_value),
        ContourAutomaticLeadForm(str(contract.value("lead_form").effective_value)),
        float(contract.value("lead_in_length").effective_value),
        float(contract.value("lead_out_length").effective_value),
        float(contract.value("lead_in_length").upper_bound),
        float(contract.value("lead_out_length").upper_bound),
        contract.value("lead_in_length").clamped,
        contract.value("lead_out_length").clamped,
    )
    reordered, lead_in, lead_out = contour_automatic_lead_points(
        loop, loop, ContourSide.ON, placement
    )
    start = reordered.segments[0].start
    assert math.dist((start.x, start.y), lead_in) == pytest.approx(
        placement.lead_in_length
    )
    assert math.dist((start.x, start.y), lead_out) == pytest.approx(
        placement.lead_out_length
    )
    assert lead_in != lead_out


def test_lead_out_length_is_not_implicitly_mirrored_from_lead_in() -> None:
    loop = _circle()
    contract = resolve_contour_automatic_contract(
        _context(machining_loop=loop, source_loop=loop)
    )
    lead_in_length = float(contract.value("lead_in_length").effective_value)
    placement = ContourAutomaticLeadPlacement(
        int(contract.value("entry_segment_index").effective_value),
        ContourAutomaticLeadForm(str(contract.value("lead_form").effective_value)),
        lead_in_length,
        lead_in_length * 0.5,
        float(contract.value("lead_in_length").upper_bound),
        float(contract.value("lead_out_length").upper_bound),
        False,
        True,
    )
    reordered, lead_in, lead_out = contour_automatic_lead_points(
        loop, loop, ContourSide.ON, placement
    )
    start = reordered.segments[0].start
    assert math.dist((start.x, start.y), lead_in) == pytest.approx(lead_in_length)
    assert math.dist((start.x, start.y), lead_out) == pytest.approx(
        lead_in_length * 0.5
    )


@pytest.mark.parametrize(
    ("profile", "expected"),
    (
        (CamQualityProfile.FAST, 13.0),
        (CamQualityProfile.BALANCED, 10.0),
        (CamQualityProfile.HIGH, 7.0),
    ),
)
def test_quality_policy_changes_stepdown_monotonically(
    profile: CamQualityProfile, expected: float
) -> None:
    contract = resolve_contour_automatic_contract(
        _context(depth_span=15.0), quality_profile=profile
    )
    assert contract.value("stepdown").effective_value == expected


def test_axial_capacity_and_depth_span_are_hard_bounds() -> None:
    bounded = resolve_contour_automatic_contract(
        _context(depth_span=4.0, axial_cutting_length=6.0, assembly_stickout=5.0)
    )
    assert bounded.value("stepdown").effective_value == 2.5
    assert bounded.value("stepdown").upper_bound == 4.0

    unsafe = resolve_contour_automatic_contract(
        _context(depth_span=7.0, axial_cutting_length=6.0, assembly_stickout=5.0)
    )
    assert unsafe.value("stepdown").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert "exceeds" in unsafe.value("stepdown").reason


@pytest.mark.parametrize(
    "changes",
    (
        {"tool_family": ToolFamily.BALL_END_MILL},
        {"tool_family": ToolFamily.CUSTOM},
        {"tool_family": None, "diameter": None, "axial_cutting_length": None},
        {"diameter": None},
        {"depth_span": None},
        {"depth_span": 0.0},
        {"multiple_depth_passes": False},
    ),
)
def test_unsupported_cutter_or_depth_evidence_fails_closed(
    changes: dict[str, object]
) -> None:
    contract = resolve_contour_automatic_contract(_context(**changes))
    assert contract.value("stepdown").mode is AutomaticParameterMode.NOT_APPLICABLE
    if changes.get("multiple_depth_passes") is not False:
        assert contract.value("lead_in_length").mode is (
            AutomaticParameterMode.NOT_APPLICABLE
        ) or changes.get("depth_span") in {None, 0.0}


def test_bull_nose_requires_valid_corner_radius_and_records_geometry() -> None:
    valid = resolve_contour_automatic_contract(
        _context(tool_family=ToolFamily.BULL_NOSE_END_MILL, corner_radius=1.0)
    )
    assert valid.value("stepdown").mode is AutomaticParameterMode.AUTO
    assert ("corner_radius", 1.0) in valid.value("stepdown").inputs

    invalid = resolve_contour_automatic_contract(
        _context(tool_family=ToolFamily.BULL_NOSE_END_MILL, corner_radius=None)
    )
    assert invalid.value("stepdown").mode is AutomaticParameterMode.NOT_APPLICABLE


def test_inside_profile_uses_only_clearance_within_source_boundary() -> None:
    source = _rectangle(20.0)
    machining = _rectangle(16.0)
    contract = resolve_contour_automatic_contract(
        _context(
            side=ContourSide.INSIDE,
            source_loop=source,
            machining_loop=machining,
        )
    )
    assert contract.value("lead_in_length").mode is AutomaticParameterMode.AUTO
    assert 0.0 < float(contract.value("lead_in_length").effective_value) <= float(
        contract.value("lead_in_length").upper_bound
    )
    placement = ContourAutomaticLeadPlacement(
        int(contract.value("entry_segment_index").effective_value),
        ContourAutomaticLeadForm(str(contract.value("lead_form").effective_value)),
        float(contract.value("lead_in_length").effective_value),
        float(contract.value("lead_out_length").effective_value),
        float(contract.value("lead_in_length").upper_bound),
        float(contract.value("lead_out_length").upper_bound),
        contract.value("lead_in_length").clamped,
        contract.value("lead_out_length").clamped,
    )
    contour_automatic_lead_points(
        machining, source, ContourSide.INSIDE, placement
    )


def test_tiny_profile_and_missing_geometry_do_not_emit_negative_or_nan_values() -> None:
    tiny = resolve_contour_automatic_contract(
        _context(machining_loop=_rectangle(0.01), source_loop=_rectangle(0.01))
    )
    assert tiny.value("lead_in_length").mode is AutomaticParameterMode.NOT_APPLICABLE
    missing = resolve_contour_automatic_contract(
        _context(machining_loop=None, source_loop=None, profile_fingerprint=None)
    )
    assert missing.value("stepdown").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert missing.value("entry_segment_index").effective_value is None
    for item in missing.values:
        if isinstance(item.effective_value, float):
            assert math.isfinite(item.effective_value) and item.effective_value >= 0.0


def test_unit_is_part_of_dependency_and_policy_does_not_convert_display_values() -> None:
    inch_source = _rectangle(1.0, LengthUnit.INCH)
    contract = resolve_contour_automatic_contract(
        _context(
            unit=LengthUnit.INCH,
            diameter=0.5,
            axial_cutting_length=1.0,
            assembly_stickout=1.5,
            depth_span=0.25,
            tolerance=0.001,
            machining_loop=inch_source,
            source_loop=inch_source,
        )
    )
    assert contract.value("stepdown").effective_value == 0.25
    assert ("unit", "inch") in contract.value("stepdown").inputs


def test_context_rejects_nan_negative_and_mixed_unit_evidence() -> None:
    with pytest.raises(ValueError):
        _context(diameter=float("nan"))
    with pytest.raises(ValueError):
        _context(depth_span=-1.0)
    with pytest.raises(ValueError):
        _context(unit=LengthUnit.INCH)


def test_provenance_fingerprint_changes_for_every_authoritative_dependency() -> None:
    base = resolve_contour_automatic_contract(_context()).effective_fingerprint
    changes = (
        {"diameter": 8.0},
        {"corner_radius": 0.5},
        {"axial_cutting_length": 18.0},
        {"assembly_stickout": 22.0},
        {"depth_span": 6.0},
        {"tolerance": 0.02},
        {"side": ContourSide.OUTSIDE},
        {"tool_fingerprint": "other-tool"},
        {"profile_fingerprint": "other-profile"},
    )
    for change in changes:
        assert (
            resolve_contour_automatic_contract(_context(**change)).effective_fingerprint
            != base
        )
