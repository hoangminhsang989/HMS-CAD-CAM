"""Stage17A Tranche4 Qt-free Drilling automatic geometry-policy tests."""

from dataclasses import replace
import math

import pytest

from hms_cadcam.cam.automatic_drilling import (
    DrillingAutomaticContext,
    analyze_drilling_pattern,
    merge_drilling_automatic_intent,
    resolve_drilling_automatic_contract,
    validate_drilling_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
)
from hms_cadcam.cam.domain import (
    DrillingCycle,
    HoleLocation,
    LengthUnit,
    Point3,
    ToolFamily,
    Vector3,
)


def _hole(x: float, y: float, *, axis: Vector3 | None = None, plane: float = 0.0) -> HoleLocation:
    unit = LengthUnit.MM
    return HoleLocation(
        Point3(x, y, plane, unit),
        axis or Vector3(0.0, 0.0, 1.0),
        Point3(0.0, 0.0, plane, unit),
        None,
        unit,
    )


def _context(**changes: object) -> DrillingAutomaticContext:
    values: dict[str, object] = {
        "unit": LengthUnit.MM,
        "cycle": DrillingCycle.DRILL,
        "hole_locations": (_hole(10.0, 0.0), _hole(0.0, 0.0), _hole(5.0, 0.0)),
        "geometry_fingerprint": "geometry-sha256",
        "geometry_resolved": True,
        "tool_family": ToolFamily.DRILL,
        "tool_fingerprint": "tool-sha256",
        "tool_diameter": 6.0,
        "axial_cutting_length": 20.0,
        "assembly_stickout": 25.0,
        "tool_point_angle_degrees": 118.0,
        "manual_top_z": 0.0,
        "manual_final_depth": -10.0,
        "manual_clearance_height": 5.0,
        "manual_retract_height": 2.0,
        "manual_peck_depth": None,
        "tolerance": 1.0e-6,
    }
    values.update(changes)
    return DrillingAutomaticContext(**values)  # type: ignore[arg-type]


def test_one_hole_and_multi_hole_patterns_are_deterministic() -> None:
    one = analyze_drilling_pattern(_context(hole_locations=(_hole(2.0, 3.0),)))
    assert one.eligible and one.count == 1 and one.minimum_spacing is None

    context = _context()
    first = analyze_drilling_pattern(context)
    second = analyze_drilling_pattern(
        replace(context, hole_locations=tuple(reversed(context.hole_locations)))
    )
    assert first.eligible and first.count == 3
    assert first.normalized_centres == ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    assert first.minimum_spacing == pytest.approx(5.0)
    assert first.bounding_box == (0.0, 0.0, 10.0, 0.0)
    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"geometry_resolved": False}, "stale"),
        ({"hole_locations": ()}, "At least one"),
        ({"geometry_fingerprint": None}, "fingerprint"),
        ({"hole_locations": (_hole(0.0, 0.0), _hole(0.0, 0.0))}, "Duplicate"),
        (
            {
                "hole_locations": (
                    _hole(0.0, 0.0),
                    _hole(1.0, 0.0, axis=Vector3(0.0, 1.0, 0.0)),
                )
            },
            "mixed",
        ),
    ),
)
def test_invalid_patterns_fail_closed(changes: dict[str, object], reason: str) -> None:
    result = analyze_drilling_pattern(_context(**changes))
    assert not result.eligible
    assert reason.lower() in result.reason.lower()


def test_pattern_and_top_plane_auto_are_provenance_backed() -> None:
    contract = resolve_drilling_automatic_contract(_context())
    assert contract.value("pattern_count").effective_value == 3
    assert contract.value("top_z").mode is AutomaticParameterMode.AUTO
    assert contract.value("top_z").effective_value == 0.0
    assert contract.value("final_depth").mode is AutomaticParameterMode.MANUAL_OVERRIDE
    assert contract.value("depth_source").effective_value == "manual"
    assert contract.value("clearance_height").has_manual_override
    assert "safe-plane" in contract.value("clearance_height").reason


def test_authoritative_common_feature_depth_is_auto_and_capacity_bounded() -> None:
    ranges = ((0.0, -8.0),) * 3
    contract = resolve_drilling_automatic_contract(
        _context(authoritative_depth_ranges=ranges)
    )
    assert contract.value("final_depth").mode is AutomaticParameterMode.AUTO
    assert contract.value("final_depth").effective_value == -8.0
    assert contract.value("depth_source").effective_value == "feature_geometry"

    too_deep = resolve_drilling_automatic_contract(
        _context(authoritative_depth_ranges=((0.0, -30.0),) * 3)
    )
    assert too_deep.value("final_depth").has_manual_override

    mixed = resolve_drilling_automatic_contract(
        _context(authoritative_depth_ranges=((0.0, -8.0), (0.0, -9.0), (0.0, -8.0)))
    )
    assert mixed.value("final_depth").has_manual_override


def test_spot_geometry_uses_explicit_target_and_center_drill_angle_only() -> None:
    context = _context(
        cycle=DrillingCycle.SPOT_DRILL,
        tool_family=ToolFamily.CENTER_DRILL,
        tool_diameter=10.0,
        tool_point_angle_degrees=90.0,
        spot_target_diameter=4.0,
    )
    contract = resolve_drilling_automatic_contract(context)
    assert contract.value("spot_depth").effective_value == pytest.approx(2.0)
    assert contract.value("final_depth").effective_value == pytest.approx(-2.0)
    assert contract.value("depth_source").effective_value == "spot_geometry"

    missing = resolve_drilling_automatic_contract(replace(context, spot_target_diameter=None))
    assert missing.value("spot_depth").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert missing.value("final_depth").has_manual_override

    invalid = resolve_drilling_automatic_contract(replace(context, spot_target_diameter=12.0))
    assert invalid.value("spot_depth").status is AutomaticParameterStatus.UNSUPPORTED

    bounded = resolve_drilling_automatic_contract(
        replace(context, axial_cutting_length=1.0, assembly_stickout=1.0)
    )
    assert bounded.value("spot_depth").mode is AutomaticParameterMode.NOT_APPLICABLE


@pytest.mark.parametrize(
    "value", (0.0, -1.0, float("nan"), float("inf"), True, "invalid")
)
def test_peck_remains_manual_and_invalid_values_are_rejected(value: object) -> None:
    contract = resolve_drilling_automatic_contract(
        _context(cycle=DrillingCycle.PECK_DRILL, manual_peck_depth=value)
    )
    peck = contract.value("peck_depth")
    assert peck.mode is AutomaticParameterMode.MANUAL_OVERRIDE
    assert not peck.validation.valid
    assert peck.override_value is None or math.isfinite(float(peck.override_value))


def test_positive_peck_is_manual_and_never_material_less_auto() -> None:
    contract = resolve_drilling_automatic_contract(
        _context(cycle=DrillingCycle.PECK_DRILL, manual_peck_depth=2.0)
    )
    peck = contract.value("peck_depth")
    assert peck.mode is AutomaticParameterMode.MANUAL_OVERRIDE
    assert peck.effective_value == 2.0
    assert peck.validation.valid
    assert "process intent" in peck.reason


def test_legacy_manual_override_reset_and_dependency_recompute() -> None:
    base = resolve_drilling_automatic_contract(_context())
    manual = merge_drilling_automatic_intent(
        base,
        None,
        {
            "top_z": 0.0,
            "final_depth": -10.0,
            "clearance_height": 5.0,
            "retract_height": 2.0,
        },
    )
    assert all(manual.value(key).has_manual_override for key in ("top_z", "final_depth"))

    reset = merge_drilling_automatic_intent(
        base,
        manual,
        {
            "top_z": 0.0,
            "final_depth": -10.0,
            "clearance_height": 5.0,
            "retract_height": 2.0,
        },
        requested_modes={"top_z": AutomaticParameterMode.AUTO},
    )
    assert reset.value("top_z").mode is AutomaticParameterMode.AUTO
    assert reset.value("final_depth").has_manual_override

    changed = resolve_drilling_automatic_contract(_context(tool_fingerprint="other-tool"))
    assert (
        changed.value("top_z").dependency_fingerprint
        != base.value("top_z").dependency_fingerprint
    )


def test_contract_roundtrip_and_stale_generator_revalidation() -> None:
    current = resolve_drilling_automatic_contract(_context())
    restored = AutomaticParameterContract.from_json(current.to_json())
    assert restored == current
    validate_drilling_automatic_contract(restored, current)

    changed = resolve_drilling_automatic_contract(
        _context(hole_locations=(_hole(0.0, 0.0), _hole(6.0, 0.0)))
    )
    with pytest.raises(ValueError, match="stale"):
        validate_drilling_automatic_contract(restored, changed)


@pytest.mark.parametrize(
    "changes",
    (
        {"tool_fingerprint": "changed-tool"},
        {"geometry_fingerprint": "changed-geometry"},
        {"hole_locations": (_hole(0.0, 0.0),)},
        {"hole_locations": (_hole(0.0, 0.0, plane=1.0),)},
        {
            "hole_locations": (
                _hole(0.0, 0.0),
                _hole(1.0, 0.0, axis=Vector3(0.0, 1.0, 0.0)),
            )
        },
    ),
)
def test_tool_geometry_count_plane_and_axis_changes_reject_stored_auto(
    changes: dict[str, object],
) -> None:
    stored = resolve_drilling_automatic_contract(_context())
    current = resolve_drilling_automatic_contract(_context(**changes))
    with pytest.raises(ValueError, match="stale"):
        validate_drilling_automatic_contract(stored, current)


def test_temporary_missing_geometry_preserves_auto_intent_without_inventing_value() -> None:
    current = resolve_drilling_automatic_contract(_context())
    stored = merge_drilling_automatic_intent(
        current,
        None,
        {
            "top_z": 0.0,
            "final_depth": -10.0,
            "clearance_height": 5.0,
            "retract_height": 2.0,
        },
        requested_modes={"top_z": AutomaticParameterMode.AUTO},
    )
    unavailable = resolve_drilling_automatic_contract(
        _context(geometry_resolved=False)
    )
    merged = merge_drilling_automatic_intent(
        unavailable,
        AutomaticParameterContract.from_json(stored.to_json()),
        {
            "top_z": 0.0,
            "final_depth": -10.0,
            "clearance_height": 5.0,
            "retract_height": 2.0,
        },
    )
    top = merged.value("top_z")
    assert top.mode is AutomaticParameterMode.AUTO
    assert top.status is AutomaticParameterStatus.UNRESOLVED
    assert top.resolved_value == 0.0
    assert "preserved" in top.reason


def test_missing_or_wrong_tool_never_receives_auto_geometry_values() -> None:
    missing = resolve_drilling_automatic_contract(
        _context(tool_family=None, tool_fingerprint=None, tool_diameter=None)
    )
    assert missing.value("pattern_count").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert missing.value("top_z").has_manual_override

    wrong = resolve_drilling_automatic_contract(_context(tool_family=ToolFamily.END_MILL))
    assert wrong.value("pattern_count").status is AutomaticParameterStatus.UNSUPPORTED


def test_nonfinite_context_values_fail_before_policy_resolution() -> None:
    with pytest.raises(ValueError):
        _context(tool_diameter=float("nan"))
    with pytest.raises(ValueError):
        _context(tolerance=float("inf"))
    assert math.isfinite(float(resolve_drilling_automatic_contract(_context()).value("top_z").effective_value))
