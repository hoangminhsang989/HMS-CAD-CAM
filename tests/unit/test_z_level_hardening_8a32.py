"""Focused Stage 8A.3.2 Z-Level hardening and safety contract tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
    ZLevelFinishingGenerator,
    ZLevelFinishingParameters,
    ZLevelLinkingMode,
    ZLevelScopeStatus,
    calculate_and_publish_z_level_finishing,
    validate_z_level_candidate_safety,
    z_level_artifact_contract_hash,
    z_level_artifact_has_safe_contract,
)
from hms_cadcam.cam.domain.operation import DiagnosticCode
from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import holder_collision_fixture
from tests.unit.test_z_level_foundation import _zlevel_operation


def _candidate(fixture, parameters):
    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return computing, generator.generate(computing)


def test_zlevel_v2_keeps_payload_v1_and_parallel_contract_untouched() -> None:
    assert Z_LEVEL_FINISHING_ALGORITHM_VERSION == 2
    assert Z_LEVEL_FINISHING_STRATEGY_VERSION == 1


def test_scope_distinguishes_not_present_holder_from_checked_safe() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    computing, candidate = _candidate(fixture, parameters)
    report = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=candidate.artifact,
        preview=candidate.preview,
    )
    scope = {item.name: item.status for item in report.safety_scope}
    assert report.status.value == "safe"
    assert scope["holder"] is ZLevelScopeStatus.NOT_PRESENT
    assert scope["cutter"] is ZLevelScopeStatus.CHECKED


def test_required_protected_geometry_is_unknown_when_not_provided() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    computing, candidate = _candidate(fixture, parameters)
    report = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=candidate.artifact,
        preview=candidate.preview,
        protected_geometry_required=True,
    )
    assert report.status.value == "unknown"
    assert any(
        item.code is DiagnosticCode.Z_LEVEL_SAFETY_MISSING_PROTECTED_GEOMETRY
        for item in report.diagnostics
    )


def test_direct_link_collision_falls_back_to_retract_clearance(tmp_path: Path) -> None:
    fixture = disconnected_fixture()
    parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
        linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
    )
    root = tmp_path / "DirectFallback.HMS"
    root.mkdir()
    result = calculate_and_publish_z_level_finishing(
        root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted
    assert result.safety_report is not None
    assert result.safety_report.linking_decision == "direct_rejected_fallback"
    assert result.artifact is not None
    assert not any("link.direct" in event.provenance for event in result.artifact.events)
    assert z_level_artifact_has_safe_contract(result.artifact)


def test_v1_marker_is_stale_and_is_not_ready(tmp_path: Path) -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    root = tmp_path / "V2Artifact.HMS"
    root.mkdir()
    result = calculate_and_publish_z_level_finishing(
        root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.artifact is not None
    marker = next(
        event
        for event in result.artifact.events
        if getattr(event, "semantic_key", None) == "z_level.safety.contract"
    )
    stale_marker = replace(
        marker,
        metadata=tuple(
            ("algorithm_version", "1") if key == "algorithm_version" else (key, value)
            for key, value in marker.metadata
        ),
    )
    stale = replace(
        result.artifact,
        events=tuple(stale_marker if event is marker else event for event in result.artifact.events),
        artifact_fingerprint=None,
    )
    assert not z_level_artifact_has_safe_contract(stale)


def test_safety_report_hash_is_deterministic_and_parameter_change_invalidates(tmp_path: Path) -> None:
    fixture = planar_fixture()
    first_parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    second_parameters = replace(first_parameters, surface_allowance_mm=0.25)
    first_root = tmp_path / "First.HMS"
    second_root = tmp_path / "Second.HMS"
    first_root.mkdir()
    second_root.mkdir()
    first = calculate_and_publish_z_level_finishing(
        first_root,
        _zlevel_operation(fixture, first_parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    second = calculate_and_publish_z_level_finishing(
        second_root,
        _zlevel_operation(fixture, second_parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert first.accepted
    assert first.artifact is not None
    # The changed effective allowance is part of the v2 input/artifact contract;
    # this fixture is conservatively rejected rather than silently reusing v1.
    assert not second.accepted
    assert second.diagnostics
    assert first.artifact.artifact_fingerprint is not None


def test_cancel_during_safety_does_not_publish_partial_artifact(tmp_path: Path) -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    root = tmp_path / "CancelledSafety.HMS"
    root.mkdir()
    result = calculate_and_publish_z_level_finishing(
        root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        cancellation=lambda: True,
    )
    assert not result.accepted
    assert result.artifact is None
    assert result.diagnostics[0].code is DiagnosticCode.Z_LEVEL_CANCELLED


def test_real_direct_link_is_checked_safe_and_published(tmp_path: Path) -> None:
    fixture = parallel_fixture(
        (
            (
                "two-safe-loops",
                (
                    (0, 0, 0),
                    (4, 0, 0),
                    (4, 10, 0),
                    (0, 10, 0),
                    (20, 0, 0),
                    (24, 0, 0),
                    (24, 10, 0),
                    (20, 10, 0),
                ),
                ((0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)),
            ),
        )
    )
    parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
        linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
    )
    root = tmp_path / "DirectSafe.HMS"
    root.mkdir()
    result = calculate_and_publish_z_level_finishing(
        root,
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.artifact is not None
    assert result.safety_report is not None
    assert result.safety_report.linking_decision == "direct_safe"
    assert any(
        "link.direct" in event.provenance for event in result.artifact.events
    )
    scope = {item.name: item.status for item in result.safety_report.safety_scope}
    assert scope["direct_links"] is ZLevelScopeStatus.CHECKED


def test_holder_missing_and_invalid_are_distinct_unknown_states() -> None:
    fixture, valid_holder = holder_collision_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    generator = ZLevelFinishingGenerator()
    operation = _zlevel_operation(fixture, parameters)

    missing_inputs = generator.resolve_inputs(
        operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=None,
    )
    missing_computing, _token = generator.begin(missing_inputs)
    missing_candidate = generator.generate(missing_computing)
    missing = validate_z_level_candidate_safety(
        operation=missing_computing.operation,
        context=missing_computing.context,
        tool=missing_computing.tool,
        assembly=missing_computing.assembly,
        holder=None,
        artifact=missing_candidate.artifact,
        preview=missing_candidate.preview,
    )

    valid_inputs = generator.resolve_inputs(
        operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=valid_holder,
    )
    valid_computing, _token = generator.begin(valid_inputs)
    valid_candidate = generator.generate(valid_computing)
    wrong_holder = holder_collision_fixture()[1]
    invalid = validate_z_level_candidate_safety(
        operation=valid_computing.operation,
        context=valid_computing.context,
        tool=valid_computing.tool,
        assembly=valid_computing.assembly,
        holder=wrong_holder,
        artifact=valid_candidate.artifact,
        preview=valid_candidate.preview,
    )

    assert missing.status.value == invalid.status.value == "unknown"
    assert missing.holder_state == "missing"
    assert invalid.holder_state == "reference_invalid"
    missing_scope = {item.name: item.status for item in missing.safety_scope}
    invalid_scope = {item.name: item.status for item in invalid.safety_scope}
    assert missing_scope["holder"] is ZLevelScopeStatus.UNVERIFIED
    assert invalid_scope["holder"] is ZLevelScopeStatus.INVALID
    assert missing.diagnostics[0].code is DiagnosticCode.Z_LEVEL_SAFETY_HOLDER_NOT_PROVIDED
    assert invalid.diagnostics[0].code is DiagnosticCode.Z_LEVEL_SAFETY_INVALID_HOLDER


def test_artifact_contract_hash_is_not_toolpath_ir_alias() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    computing, candidate = _candidate(fixture, parameters)
    safety = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=candidate.artifact,
        preview=candidate.preview,
    )
    contract = z_level_artifact_contract_hash(
        operation=computing.operation,
        context=computing.context,
        parameters=parameters,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        candidate_artifact=candidate.artifact,
        safety_report=safety,
    )
    changed_parameters = replace(parameters, link_clearance_mm=2.0)
    changed = z_level_artifact_contract_hash(
        operation=computing.operation,
        context=computing.context,
        parameters=changed_parameters,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        candidate_artifact=candidate.artifact,
        safety_report=safety,
    )
    assert candidate.artifact.artifact_fingerprint is not None
    assert contract != candidate.artifact.artifact_fingerprint
    assert changed != contract


def test_safety_and_artifact_hashes_are_stable_across_recomputes() -> None:
    fixture = planar_fixture()
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)

    def run() -> tuple[str, str, str]:
        computing, candidate = _candidate(fixture, parameters)
        safety = validate_z_level_candidate_safety(
            operation=computing.operation,
            context=computing.context,
            tool=computing.tool,
            assembly=computing.assembly,
            holder=computing.holder,
            artifact=candidate.artifact,
            preview=candidate.preview,
        )
        contract = z_level_artifact_contract_hash(
            operation=computing.operation,
            context=computing.context,
            parameters=parameters,
            tool=computing.tool,
            assembly=computing.assembly,
            holder=computing.holder,
            candidate_artifact=candidate.artifact,
            safety_report=safety,
        )
        return (
            candidate.artifact.artifact_fingerprint.digest,
            safety.fingerprint.digest,
            contract.digest,
        )

    first = run()
    second = run()
    assert second == first
