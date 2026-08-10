"""Stage17A Tranche3 Pocket automatic policy and entry geometry."""

from __future__ import annotations

from dataclasses import replace
import math

import pytest

from hms_cadcam.cam.application import PocketGenerationError, build_pocket_offset_loops
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)
from hms_cadcam.cam.automatic_pocket import (
    POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES,
    PocketAutomaticContext,
    PocketAutomaticEntryPlacement,
    pocket_automatic_entry_loops,
    pocket_geometric_stepover_target,
    resolve_pocket_automatic_contract,
)
from hms_cadcam.cam.domain import LengthUnit, ToolFamily
from tests.unit.test_pocket_strategy import _arc_boundary, _rectangle


def _context(
    *,
    loop=None,
    family: ToolFamily | None = ToolFamily.END_MILL,
    diameter: float | None = 10.0,
    axial: float | None = 20.0,
    stickout: float | None = 15.0,
    depth: float | None = 12.0,
    profile: CamQualityProfile = CamQualityProfile.BALANCED,
    accessible: bool = True,
) -> PocketAutomaticContext:
    source = loop or _rectangle()
    tolerance = 1.0e-7
    loops = ()
    if accessible and diameter is not None and diameter > 0.0:
        try:
            stepover, _lower, _upper, _clamped = pocket_geometric_stepover_target(
                diameter, tolerance, profile
            )
            loops = build_pocket_offset_loops(
                source,
                diameter / 2.0,
                stepover,
                tolerance,
                terminal_coverage_radius=diameter / 2.0,
            )
        except (PocketGenerationError, ValueError):
            loops = ()
    return PocketAutomaticContext(
        LengthUnit.MM,
        family,
        diameter,
        axial,
        stickout,
        depth,
        tolerance,
        source if accessible else None,
        loops,
        "pocket-fingerprint" if accessible else None,
        "outer-loop-fingerprint" if accessible else None,
        None,
        "tool-fingerprint" if family is not None else None,
        "reachable" if loops else "unresolved",
    )


def test_rectangle_policy_is_deterministic_bounded_and_entry_is_revalidated() -> None:
    context = _context()
    first = resolve_pocket_automatic_contract(context)
    second = resolve_pocket_automatic_contract(context)
    assert first.to_json() == second.to_json()
    assert POCKET_AUTOMATIC_SUPPORTED_TOOL_FAMILIES == {ToolFamily.END_MILL}
    stepdown = first.value("stepdown")
    stepover = first.value("stepover")
    assert stepdown.mode is AutomaticParameterMode.AUTO
    assert 0.0 < float(stepdown.effective_value) <= 12.0
    assert float(stepdown.effective_value) <= 15.0
    assert stepover.mode is AutomaticParameterMode.AUTO
    assert 0.0 < float(stepover.effective_value) < 10.0
    placement = PocketAutomaticEntryPlacement(
        int(first.value("entry_loop_index").effective_value),
        int(first.value("entry_segment_index").effective_value),
        float(first.value("entry_point_x").effective_value),
        float(first.value("entry_point_y").effective_value),
        float(first.value("entry_clearance").effective_value),
    )
    reordered = pocket_automatic_entry_loops(
        context.source_loop,
        context.offset_loops,
        placement,
        cutter_radius=5.0,
        tolerance=1.0e-7,
    )
    start = reordered[0].segments[0].start
    assert start.x == pytest.approx(placement.point_x)
    assert start.y == pytest.approx(placement.point_y)
    assert placement.local_clearance >= 5.0 - 1.0e-7


def test_arc_profile_and_stable_tie_break_are_deterministic() -> None:
    context = _context(loop=_arc_boundary())
    contract = resolve_pocket_automatic_contract(context)
    assert contract.value("entry_segment_index").status is AutomaticParameterStatus.RESOLVED
    assert resolve_pocket_automatic_contract(context).to_json() == contract.to_json()
    assert math.isfinite(float(contract.value("entry_point_x").effective_value))
    assert math.isfinite(float(contract.value("entry_point_y").effective_value))


def test_quality_profiles_change_geometric_steps_monotonically() -> None:
    results = {
        profile: resolve_pocket_automatic_contract(
            _context(profile=profile), quality_profile=profile
        )
        for profile in CamQualityProfile
    }
    assert (
        float(results[CamQualityProfile.FAST].value("stepover").effective_value)
        > float(results[CamQualityProfile.BALANCED].value("stepover").effective_value)
        > float(results[CamQualityProfile.HIGH].value("stepover").effective_value)
    )
    assert (
        float(results[CamQualityProfile.FAST].value("stepdown").effective_value)
        > float(results[CamQualityProfile.BALANCED].value("stepdown").effective_value)
        > float(results[CamQualityProfile.HIGH].value("stepdown").effective_value)
    )


def test_depth_axial_stickout_and_strict_diameter_bounds_fail_closed() -> None:
    bounded = resolve_pocket_automatic_contract(
        _context(depth=4.0, axial=7.0, stickout=5.0)
    )
    assert float(bounded.value("stepdown").effective_value) <= 4.0
    too_deep = resolve_pocket_automatic_contract(
        _context(depth=8.0, axial=7.0, stickout=5.0)
    )
    assert too_deep.value("stepdown").status is AutomaticParameterStatus.UNSUPPORTED
    tiny = resolve_pocket_automatic_contract(_context(diameter=1.0e-7))
    assert tiny.value("stepover").status is AutomaticParameterStatus.UNSUPPORTED


@pytest.mark.parametrize(
    "family",
    [
        ToolFamily.BALL_END_MILL,
        ToolFamily.BULL_NOSE_END_MILL,
        ToolFamily.CUSTOM,
        None,
    ],
)
def test_generator_unsupported_tool_families_never_receive_auto_values(
    family: ToolFamily | None,
) -> None:
    contract = resolve_pocket_automatic_contract(_context(family=family))
    assert contract.value("stepdown").status is AutomaticParameterStatus.UNSUPPORTED
    assert contract.value("stepover").status is AutomaticParameterStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("changes", "unsupported"),
    [
        ({"diameter": None}, {"stepdown", "stepover"}),
        ({"axial": None}, {"stepdown"}),
        ({"stickout": None}, {"stepdown"}),
        ({"depth": None}, {"stepdown"}),
        ({"accessible": False}, {"stepdown", "stepover"}),
    ],
)
def test_missing_required_evidence_is_not_applicable(
    changes: dict[str, object],
    unsupported: set[str],
) -> None:
    contract = resolve_pocket_automatic_contract(_context(**changes))
    assert all(
        contract.value(key).status is AutomaticParameterStatus.UNSUPPORTED
        for key in unsupported
    )
    expected_entry = (
        AutomaticParameterStatus.RESOLVED
        if changes == {"depth": None}
        else AutomaticParameterStatus.UNSUPPORTED
    )
    assert contract.value("entry_segment_index").status is expected_entry


def test_tiny_region_and_cutter_too_large_produce_no_accessible_region() -> None:
    with pytest.raises(PocketGenerationError):
        build_pocket_offset_loops(
            _rectangle(5.0, 5.0),
            5.0,
            2.0,
            1.0e-7,
            terminal_coverage_radius=5.0,
        )
    contract = resolve_pocket_automatic_contract(
        _context(loop=_rectangle(5.0, 5.0), accessible=False)
    )
    assert contract.value("entry_segment_index").status is AutomaticParameterStatus.UNSUPPORTED


def test_stale_entry_coordinates_and_invalid_numeric_context_fail_closed() -> None:
    context = _context()
    contract = resolve_pocket_automatic_contract(context)
    placement = PocketAutomaticEntryPlacement(
        int(contract.value("entry_loop_index").effective_value),
        int(contract.value("entry_segment_index").effective_value),
        float(contract.value("entry_point_x").effective_value) + 1.0,
        float(contract.value("entry_point_y").effective_value),
        float(contract.value("entry_clearance").effective_value),
    )
    with pytest.raises(ValueError, match="no longer matches"):
        pocket_automatic_entry_loops(
            context.source_loop,
            context.offset_loops,
            placement,
            cutter_radius=5.0,
            tolerance=1.0e-7,
        )
    with pytest.raises(ValueError):
        replace(context, diameter=float("nan"))


def test_entry_form_and_linking_remain_explicitly_unavailable() -> None:
    contract = resolve_pocket_automatic_contract(_context())
    assert contract.value("entry_form").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert contract.value("linking_mode").mode is AutomaticParameterMode.NOT_APPLICABLE
    assert "center-cutting" in contract.value("entry_form").reason
    assert "stay-down" in contract.value("linking_mode").reason


def test_dependency_fingerprint_changes_for_every_authoritative_input() -> None:
    base = resolve_pocket_automatic_contract(_context()).value("stepdown")
    changed = (
        _context(diameter=8.0),
        _context(axial=18.0),
        _context(stickout=14.0),
        _context(depth=10.0),
        _context(loop=_rectangle(50.0, 30.0)),
    )
    assert all(
        resolve_pocket_automatic_contract(value).value("stepdown").dependency_fingerprint
        != base.dependency_fingerprint
        for value in changed
    )
