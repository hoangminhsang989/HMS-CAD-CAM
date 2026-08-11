"""R211 Stage17A Tranche5 pure hole-completion AUTO policy tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.automatic_boring import (
    BoringAutomaticContext,
    merge_boring_automatic_intent,
    resolve_boring_automatic_contract,
    validate_boring_automatic_contract,
)
from hms_cadcam.cam.automatic_hole_geometry import (
    HoleGeometryContext,
    analyze_hole_geometry,
)
from hms_cadcam.cam.automatic_parameters import (
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
)
from hms_cadcam.cam.automatic_reaming import (
    ReamingAutomaticContext,
    merge_reaming_automatic_intent,
    resolve_reaming_automatic_contract,
    validate_reaming_automatic_contract,
)
from hms_cadcam.cam.automatic_tapping import (
    TappingAutomaticContext,
    TappingThreadEvidence,
    merge_tapping_automatic_intent,
    resolve_tapping_automatic_contract,
    validate_tapping_automatic_contract,
)
from hms_cadcam.cam.domain import (
    HoleLocation,
    Length,
    LengthUnit,
    Point3,
    ToolFamily,
    Vector3,
)


def _hole(
    x: float,
    y: float,
    *,
    plane: float = 0.0,
    axis: Vector3 | None = None,
    diameter: float | None = None,
) -> HoleLocation:
    unit = LengthUnit.MM
    return HoleLocation(
        Point3(x, y, plane, unit),
        axis or Vector3(0.0, 0.0, 1.0),
        Point3(0.0, 0.0, plane, unit),
        None if diameter is None else Length(diameter, unit),
        unit,
    )


def _geometry(**changes: object) -> HoleGeometryContext:
    values: dict[str, object] = {
        "unit": LengthUnit.MM,
        "hole_locations": (_hole(10.0, 0.0), _hole(0.0, 0.0)),
        "geometry_fingerprint": "hole-selection-sha256",
        "geometry_resolved": True,
        "tolerance": 1.0e-6,
    }
    values.update(changes)
    return HoleGeometryContext(**values)  # type: ignore[arg-type]


def _tapping(**changes: object) -> TappingAutomaticContext:
    values: dict[str, object] = {
        "geometry": _geometry(),
        "tool_family": ToolFamily.TAP,
        "tool_fingerprint": "tap-sha256",
        "tool_nominal_diameter": 6.0,
        "tool_pitch": 1.0,
        "tool_hand": "right",
        "tool_threaded_length": 15.0,
        "assembly_stickout": 20.0,
        "manual_top_z": 0.0,
        "manual_final_depth": -8.0,
        "manual_clearance_height": 5.0,
        "manual_retract_height": 2.0,
        "manual_nominal_diameter": 6.0,
        "manual_pitch": 1.0,
        "manual_hand": "right",
    }
    values.update(changes)
    return TappingAutomaticContext(**values)  # type: ignore[arg-type]


def _reaming(**changes: object) -> ReamingAutomaticContext:
    values: dict[str, object] = {
        "geometry": _geometry(),
        "tool_family": ToolFamily.REAMER,
        "tool_fingerprint": "reamer-sha256",
        "tool_diameter": 10.0,
        "tool_axial_cutting_length": 20.0,
        "assembly_stickout": 25.0,
        "manual_top_z": 0.0,
        "manual_final_depth": -10.0,
        "manual_clearance_height": 5.0,
        "manual_retract_height": 2.0,
        "manual_nominal_diameter": 10.0,
    }
    values.update(changes)
    return ReamingAutomaticContext(**values)  # type: ignore[arg-type]


def _boring(**changes: object) -> BoringAutomaticContext:
    values: dict[str, object] = {
        "geometry": _geometry(),
        "tool_family": ToolFamily.BORING_BAR,
        "tool_fingerprint": "boring-tool-sha256",
        "holder_fingerprint": "holder-sha256",
        "tool_minimum_bore_diameter": 8.0,
        "tool_maximum_bore_diameter": 30.0,
        "tool_axial_cutting_length": 25.0,
        "assembly_stickout": 30.0,
        "manual_top_z": 0.0,
        "manual_final_depth": -12.0,
        "manual_clearance_height": 5.0,
        "manual_retract_height": 2.0,
        "manual_finished_bore_diameter": 20.0,
    }
    values.update(changes)
    return BoringAutomaticContext(**values)  # type: ignore[arg-type]


def test_shared_geometry_order_bounds_spacing_and_fingerprint_are_deterministic() -> None:
    first = analyze_hole_geometry(_geometry())
    second = analyze_hole_geometry(
        _geometry(hole_locations=tuple(reversed(_geometry().hole_locations)))
    )
    assert first.eligible
    assert first.normalized_centres == ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    assert first.bounding_box == (0.0, 0.0, 10.0, 0.0)
    assert first.minimum_spacing == pytest.approx(10.0)
    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"geometry_resolved": False}, "stale"),
        ({"geometry_fingerprint": None}, "fingerprint"),
        ({"hole_locations": ()}, "At least one"),
        ({"hole_locations": (_hole(0.0, 0.0), _hole(0.0, 0.0))}, "Duplicate"),
        (
            {
                "hole_locations": (
                    _hole(0.0, 0.0),
                    _hole(2.0, 0.0, axis=Vector3(0.0, 1.0, 0.0)),
                )
            },
            "mixed",
        ),
        (
            {"hole_locations": (_hole(0.0, 0.0), _hole(2.0, 0.0, plane=1.0))},
            "plane",
        ),
    ),
)
def test_shared_geometry_fail_closed_matrix(
    changes: dict[str, object], reason: str
) -> None:
    result = analyze_hole_geometry(_geometry(**changes))
    assert not result.eligible
    assert reason.lower() in result.reason.lower()


def test_plain_holes_do_not_create_thread_definition_or_thread_depth() -> None:
    plain_depth = _geometry(
        authoritative_depth_ranges=((0.0, -8.0), (0.0, -8.0)),
        depth_source="plain_hole_depth",
    )
    contract = resolve_tapping_automatic_contract(_tapping(geometry=plain_depth))
    assert contract.value("top_z").mode is AutomaticParameterMode.AUTO
    assert contract.value("final_depth").has_manual_override
    assert contract.value("nominal_diameter").has_manual_override
    assert contract.value("pitch").has_manual_override
    assert contract.value("hand").has_manual_override
    assert "Plain-hole depth" in contract.value("final_depth").reason


def test_tapping_explicit_thread_metadata_and_thread_depth_are_conditional_auto() -> None:
    geometry = _geometry(
        authoritative_depth_ranges=((0.0, -8.0), (0.0, -8.0)),
        depth_source="thread_feature_depth",
    )
    evidence = TappingThreadEvidence(
        6.0,
        1.0,
        "right",
        "cad_thread_feature_v1",
        "thread_feature_depth",
    )
    contract = resolve_tapping_automatic_contract(
        _tapping(geometry=geometry, thread_evidence=evidence)
    )
    assert contract.value("final_depth").effective_value == -8.0
    assert contract.value("nominal_diameter").effective_value == 6.0
    assert contract.value("pitch").effective_value == 1.0
    assert contract.value("hand").effective_value == "right"
    assert contract.value("thread_source").effective_value == "cad_thread_feature_v1"


def test_tapping_thread_metadata_cannot_relabel_unbound_plain_hole_depth() -> None:
    geometry = _geometry(
        authoritative_depth_ranges=((0.0, -8.0), (0.0, -8.0)),
        depth_source="plain_hole_depth",
    )
    evidence = TappingThreadEvidence(
        6.0,
        1.0,
        "right",
        "cad_thread_feature_v1",
        "thread_feature_depth",
    )
    contract = resolve_tapping_automatic_contract(
        _tapping(geometry=geometry, thread_evidence=evidence)
    )
    assert contract.value("final_depth").has_manual_override
    assert contract.value("depth_source").effective_value == "thread_depth_absent"
    assert "not authoritative threaded-feature depth" in contract.value(
        "final_depth"
    ).reason


def test_grouped_incompatible_threaded_depths_fail_closed() -> None:
    geometry = _geometry(
        authoritative_depth_ranges=((0.0, -8.0), (0.0, -9.0)),
        depth_source="thread_feature_depth",
    )
    evidence = TappingThreadEvidence(
        6.0,
        1.0,
        "right",
        "cad_thread_feature_v1",
        "thread_feature_depth",
    )
    contract = resolve_tapping_automatic_contract(
        _tapping(geometry=geometry, thread_evidence=evidence)
    )
    assert contract.value("final_depth").has_manual_override
    assert contract.value("depth_source").effective_value == "incompatible_group"


@pytest.mark.parametrize(
    "changes",
    (
        {"tool_family": ToolFamily.DRILL},
        {"tool_fingerprint": None},
        {"tool_pitch": 1.25},
        {"tool_hand": "left"},
    ),
)
def test_tapping_tool_family_and_explicit_thread_compatibility_fail_closed(
    changes: dict[str, object]
) -> None:
    evidence = TappingThreadEvidence(6.0, 1.0, "right", "cad_thread_feature_v1")
    contract = resolve_tapping_automatic_contract(
        _tapping(thread_evidence=evidence, **changes)
    )
    assert contract.value("top_z").has_manual_override
    assert contract.value("nominal_diameter").has_manual_override


def test_reaming_source_hole_diameter_is_classified_but_not_used_as_target() -> None:
    geometry = _geometry(
        hole_locations=(_hole(0.0, 0.0, diameter=10.0), _hole(10.0, 0.0, diameter=10.0))
    )
    contract = resolve_reaming_automatic_contract(_reaming(geometry=geometry))
    assert contract.value("diameter_source").effective_value == "unqualified_source_hole_diameter"
    assert contract.value("nominal_diameter").has_manual_override
    assert contract.value("nominal_diameter").effective_value == 10.0


def test_reaming_authoritative_target_and_depth_are_auto_and_tool_bounded() -> None:
    geometry = _geometry(
        authoritative_depth_ranges=((0.0, -12.0), (0.0, -12.0)),
        depth_source="finished_feature_depth",
        authoritative_finished_diameters=(10.0, 10.0),
        diameter_source="finished_feature_diameter",
    )
    contract = resolve_reaming_automatic_contract(_reaming(geometry=geometry))
    assert contract.value("final_depth").effective_value == -12.0
    assert contract.value("nominal_diameter").effective_value == 10.0

    wrong_tool = resolve_reaming_automatic_contract(
        _reaming(geometry=geometry, tool_diameter=9.5)
    )
    assert wrong_tool.value("nominal_diameter").has_manual_override

    too_deep = resolve_reaming_automatic_contract(
        _reaming(
            geometry=replace(
                geometry,
                authoritative_depth_ranges=((0.0, -30.0), (0.0, -30.0)),
            )
        )
    )
    assert too_deep.value("final_depth").has_manual_override


def test_grouped_reaming_diameter_and_depth_mismatch_never_use_first_min_or_average() -> None:
    geometry = _geometry(
        authoritative_depth_ranges=((0.0, -10.0), (0.0, -12.0)),
        depth_source="finished_feature_depth",
        authoritative_finished_diameters=(10.0, 10.1),
        diameter_source="finished_feature_diameter",
    )
    contract = resolve_reaming_automatic_contract(_reaming(geometry=geometry))
    assert contract.value("final_depth").has_manual_override
    assert contract.value("nominal_diameter").has_manual_override
    assert contract.value("depth_source").effective_value == "incompatible_group"
    assert contract.value("diameter_source").effective_value == "incompatible_group"


def test_boring_target_uses_finished_feature_and_tool_range_only() -> None:
    geometry = _geometry(
        authoritative_finished_diameters=(20.0, 20.0),
        diameter_source="finished_bore_feature",
    )
    contract = resolve_boring_automatic_contract(_boring(geometry=geometry))
    target = contract.value("finished_bore_diameter")
    assert target.mode is AutomaticParameterMode.AUTO
    assert target.effective_value == 20.0
    assert target.lower_bound == 8.0
    assert target.upper_bound == 30.0

    out_of_range = resolve_boring_automatic_contract(
        _boring(
            geometry=replace(
                geometry,
                authoritative_finished_diameters=(40.0, 40.0),
            )
        )
    )
    assert out_of_range.value("finished_bore_diameter").has_manual_override


def test_grouped_incompatible_finished_bores_fail_closed() -> None:
    geometry = _geometry(
        authoritative_finished_diameters=(20.0, 21.0),
        diameter_source="finished_bore_feature",
    )
    contract = resolve_boring_automatic_contract(_boring(geometry=geometry))
    assert contract.value("finished_bore_diameter").has_manual_override
    assert contract.value("diameter_source").effective_value == "incompatible_group"


def test_boring_missing_holder_or_tool_family_fails_closed_without_radial_inference() -> None:
    missing_holder = resolve_boring_automatic_contract(_boring(holder_fingerprint=None))
    wrong_family = resolve_boring_automatic_contract(_boring(tool_family=ToolFamily.REAMER))
    assert missing_holder.value("top_z").has_manual_override
    assert wrong_family.value("top_z").has_manual_override
    assert "radial" not in {item.key for item in missing_holder.values}
    assert "allowance" not in {item.key for item in missing_holder.values}


@pytest.mark.parametrize(
    ("resolve", "context", "merge", "user_key"),
    (
        (
            resolve_tapping_automatic_contract,
            _tapping(),
            merge_tapping_automatic_intent,
            "top_z",
        ),
        (
            resolve_reaming_automatic_contract,
            _reaming(),
            merge_reaming_automatic_intent,
            "top_z",
        ),
        (
            resolve_boring_automatic_contract,
            _boring(),
            merge_boring_automatic_intent,
            "top_z",
        ),
    ),
)
def test_legacy_manual_reset_auto_and_temporary_evidence_loss(
    resolve: object,
    context: object,
    merge: object,
    user_key: str,
) -> None:
    current = resolve(context)  # type: ignore[operator]
    user_keys = {
        item.key: item.effective_value
        for item in current.values
        if item.key in {
            "top_z",
            "final_depth",
            "clearance_height",
            "retract_height",
            "nominal_diameter",
            "pitch",
            "hand",
            "finished_bore_diameter",
        }
    }
    legacy = merge(current, None, user_keys)  # type: ignore[operator]
    assert legacy.value(user_key).has_manual_override
    reset = merge(  # type: ignore[operator]
        current,
        legacy,
        user_keys,
        requested_modes={user_key: AutomaticParameterMode.AUTO},
    )
    assert reset.value(user_key).mode is AutomaticParameterMode.AUTO

    missing_geometry = replace(
        context,
        geometry=replace(context.geometry, geometry_resolved=False),  # type: ignore[attr-defined]
    )
    unavailable = resolve(missing_geometry)  # type: ignore[operator]
    preserved = merge(unavailable, reset, user_keys)  # type: ignore[operator]
    value = preserved.value(user_key)
    assert value.mode is AutomaticParameterMode.AUTO
    assert value.status is AutomaticParameterStatus.UNRESOLVED


@pytest.mark.parametrize(
    ("resolve", "context", "validate"),
    (
        (resolve_tapping_automatic_contract, _tapping(), validate_tapping_automatic_contract),
        (resolve_reaming_automatic_contract, _reaming(), validate_reaming_automatic_contract),
        (resolve_boring_automatic_contract, _boring(), validate_boring_automatic_contract),
    ),
)
def test_contract_roundtrip_and_stale_dependency_rejection(
    resolve: object,
    context: object,
    validate: object,
) -> None:
    current = resolve(context)  # type: ignore[operator]
    restored = AutomaticParameterContract.from_json(current.to_json())
    validate(restored, current)  # type: ignore[operator]
    stale = resolve(  # type: ignore[operator]
        replace(context, tool_fingerprint="changed-tool-fingerprint")
    )
    with pytest.raises(ValueError, match="stale"):
        validate(restored, stale)  # type: ignore[operator]


def test_safe_planes_require_explicit_authority() -> None:
    manual = resolve_reaming_automatic_contract(_reaming())
    assert manual.value("clearance_height").has_manual_override
    geometry = _geometry(
        safe_retract_height=2.0,
        safe_clearance_height=5.0,
        safe_plane_source="verified_stock_fixture_envelope",
    )
    automatic = resolve_reaming_automatic_contract(_reaming(geometry=geometry))
    assert automatic.value("retract_height").mode is AutomaticParameterMode.AUTO
    assert automatic.value("clearance_height").mode is AutomaticParameterMode.AUTO
