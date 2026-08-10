"""Pocket Stage17A AUTO/manual persistence and Function Editor behavior."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from time import perf_counter

import pytest

from hms_cadcam.cam.application import PocketGenerationError, PocketGenerator
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    GeometryFingerprint,
    GeometryResolutionStatus,
    OperationParameterSet,
    ResolvedPocketGeometry,
)
from hms_cadcam.ui.function_editor import (
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from _qt_lifecycle import (
    drain_test_owned_qt_state,
    qt_lifecycle_snapshot,
    top_level_baseline,
)
from hms_cadcam.ui.function_editor.strategies import (
    PocketEditorDraftContext,
    build_pocket_schema,
    pocket_applied_values,
    pocket_draft_transform,
    prepare_pocket_update,
)
from tests.unit.test_pocket_function_editor_9a53 import _application, _context
from tests.unit.test_pocket_ui import _workspace


def _automatic_context():
    context, region, _holder = _context()
    return replace(context, geometry_region=region), region


def _draft(context, region):
    return PocketEditorDraftContext(
        context.geometry_reference,
        geometry_region=region,
    )


def _switch_both_to_auto(context, region):
    draft = _draft(context, region)
    values = dict(pocket_applied_values(context))
    values["stepdown_mode"] = AutomaticParameterMode.AUTO.value
    values["stepover_mode"] = AutomaticParameterMode.AUTO.value
    values.update(pocket_draft_transform(context, draft, values))
    return draft, values, prepare_pocket_update(context, draft, values)


def _stored_contract(operation) -> AutomaticParameterContract:
    raw = dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    assert isinstance(raw, str)
    return AutomaticParameterContract.from_json(raw)


def test_basic_advanced_auto_summary_modes_and_unavailable_entry_form() -> None:
    context, _region = _automatic_context()
    schema = build_pocket_schema(context)
    values = pocket_applied_values(context)
    assert schema.field("automatic_summary").disclosure_level is ParameterDisclosureLevel.BASIC
    assert schema.field("automatic_stepdown").disclosure_level is ParameterDisclosureLevel.BASIC
    assert schema.field("automatic_stepover").disclosure_level is ParameterDisclosureLevel.BASIC
    assert schema.field("automatic_entry_location").disclosure_level is ParameterDisclosureLevel.BASIC
    assert schema.field("stepdown_mode").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert schema.field("stepover_mode").disclosure_level is ParameterDisclosureLevel.ADVANCED
    assert values["stepdown_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert values["stepover_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert "center" in str(values["automatic_entry_form"]).lower() or "cắt tâm" in str(
        values["automatic_entry_form"]
    ).lower()


def test_auto_apply_persists_policy_and_generator_revalidates_entry() -> None:
    context, region = _automatic_context()
    _draft_value, _values, update = _switch_both_to_auto(context, region)
    contract = _stored_contract(update.operation)
    assert contract.value("stepdown").mode is AutomaticParameterMode.AUTO
    assert contract.value("stepover").mode is AutomaticParameterMode.AUTO
    assert (
        contract.value("entry_segment_index").status
        is AutomaticParameterStatus.RESOLVED
    )
    inputs = PocketGenerator().resolve_inputs(
        replace(update.operation, enabled=True),
        context.setup,
        assembly=update.assembly,
        tool=update.tool,
        machine=update.machine,
        resolved_geometry=ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED, region
        ),
    )
    start = inputs.offset_loops[0].segments[0].start
    assert start.x == pytest.approx(
        float(contract.value("entry_point_x").effective_value)
    )
    assert start.y == pytest.approx(
        float(contract.value("entry_point_y").effective_value)
    )


def test_generator_rejects_stale_pocket_dependency_fingerprint() -> None:
    context, region = _automatic_context()
    _draft_value, _values, update = _switch_both_to_auto(context, region)
    stale_region = replace(
        region,
        source_fingerprint=GeometryFingerprint.from_payload(
            {"source": "same-shape-new-revision"}
        ),
    )
    with pytest.raises(PocketGenerationError, match="authoritative evidence"):
        PocketGenerator().resolve_inputs(
            replace(update.operation, enabled=True),
            context.setup,
            assembly=update.assembly,
            tool=update.tool,
            machine=update.machine,
            resolved_geometry=ResolvedPocketGeometry(
                GeometryResolutionStatus.RESOLVED,
                stale_region,
            ),
        )


def test_manual_override_survives_quality_change_and_reset_returns_auto() -> None:
    context, region = _automatic_context()
    _draft_value, _values, auto_update = _switch_both_to_auto(context, region)
    stored = replace(context, operation=auto_update.operation)
    draft = _draft(stored, region)
    values = dict(pocket_applied_values(stored))
    values["stepover_mode"] = AutomaticParameterMode.MANUAL_OVERRIDE.value
    values["stepover"] = "2.25"
    values.update(pocket_draft_transform(stored, draft, values))
    manual_update = prepare_pocket_update(stored, draft, values)
    manual = _stored_contract(manual_update.operation)
    assert manual.value("stepover").mode is AutomaticParameterMode.MANUAL_OVERRIDE
    assert manual.value("stepover").effective_value == pytest.approx(2.25)

    changed = replace(stored, operation=manual_update.operation)
    changed_values = dict(pocket_applied_values(changed))
    changed_values["quality_profile"] = CamQualityProfile.HIGH.value
    changed_values.update(pocket_draft_transform(changed, _draft(changed, region), changed_values))
    assert float(changed_values["stepover"]) == pytest.approx(2.25)
    changed_values["stepover_mode"] = AutomaticParameterMode.AUTO.value
    changed_values.update(pocket_draft_transform(changed, _draft(changed, region), changed_values))
    reset_update = prepare_pocket_update(
        changed, _draft(changed, region), changed_values
    )
    reset = _stored_contract(reset_update.operation)
    assert reset.value("stepover").mode is AutomaticParameterMode.AUTO
    assert float(reset.value("stepover").effective_value) != pytest.approx(2.25)


def test_temporary_geometry_loss_preserves_persisted_auto_intent() -> None:
    context, region = _automatic_context()
    _draft_value, _values, update = _switch_both_to_auto(context, region)
    unavailable = replace(context, operation=update.operation, geometry_region=None)
    values = pocket_applied_values(unavailable)
    assert values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
    assert values["stepover_mode"] == AutomaticParameterMode.AUTO.value
    assert "không khả dụng" in str(values["automatic_stepdown"]).lower() or "unavailable" in str(
        values["automatic_stepdown"]
    ).lower()


def test_project_round_trip_preserves_auto_policy_not_transient_manual(
    tmp_path,
) -> None:
    application = _application()
    service, _session, workspace, _viewer, _selected = _workspace(tmp_path)
    session = workspace.production_function_editor_session()
    assert session is not None
    values = session.applied_mapping()
    assert session.field_action_callback is not None
    selected = session.field_action_callback("select_geometry", values)
    assert selected is not None
    values.update(selected)
    assert session.draft_transform_callback is not None
    values["stepdown_mode"] = AutomaticParameterMode.AUTO.value
    values["stepover_mode"] = AutomaticParameterMode.AUTO.value
    values.update(session.draft_transform_callback(values))
    assert session.apply_callback(values)
    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    before = _stored_contract(applied)
    assert before.value("stepdown").mode is AutomaticParameterMode.AUTO
    assert before.value("stepover").mode is AutomaticParameterMode.AUTO
    assert before.value("stepdown").override_value is None
    service.save()
    root = service.current_project.root_path
    service.close_project()
    service.open_project(root)
    restored = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    after = _stored_contract(restored)
    assert after.to_json() == before.to_json()
    assert restored.artifact_state.status is ArtifactStatus.DIRTY
    workspace.deleteLater()
    application.processEvents()


def test_legacy_and_missing_additive_fields_preserve_manual_intent() -> None:
    context, region = _automatic_context()
    legacy = pocket_applied_values(context)
    assert legacy["stepdown_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert legacy["stepover_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value

    _draft_value, _values, update = _switch_both_to_auto(context, region)
    contract = _stored_contract(update.operation)
    missing = replace(
        contract,
        values=tuple(item for item in contract.values if item.key != "stepover"),
    )
    base = update.strategy.to_operation_parameters()
    parameters = OperationParameterSet(
        base.strategy_key,
        base.strategy_version,
        base.values + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, missing.to_json()),),
        base.schema_version,
    )
    restored = replace(context, operation=replace(update.operation, parameters=parameters))
    restored_values = pocket_applied_values(restored)
    assert (
        restored_values["stepover_mode"]
        == AutomaticParameterMode.MANUAL_OVERRIDE.value
    )


@pytest.mark.parametrize("payload", ["not-json", "{}", 123])
def test_malformed_automatic_metadata_fails_safely(payload: object) -> None:
    context, _region = _automatic_context()
    base = context.operation.parameters
    parameters = OperationParameterSet(
        base.strategy_key,
        base.strategy_version,
        base.values + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, payload),),
        base.schema_version,
    )
    broken = replace(context, operation=replace(context.operation, parameters=parameters))
    with pytest.raises(ValueError, match="automatic metadata|policy identity"):
        pocket_applied_values(broken)


def test_catalog_keys_have_vi_en_ko_parity_utf8_and_no_duplicates() -> None:
    catalog_root = Path("src/hms_cadcam/ui/catalogs")
    required = {
        "Pocket 2D Auto Setup",
        "Bước ngang tự động",
        "Vị trí vào dao",
        "Chế độ stepover",
        "Trạng thái kiểu vào dao",
        "Liên kết tự động",
        "Nguồn gốc thiết lập tự động",
    }
    key_sets = []
    for name in ("vi_VN.json", "en_US.json", "ko_KR.json"):
        raw = (catalog_root / name).read_text(encoding="utf-8")
        pairs = json.loads(raw, object_pairs_hook=list)
        keys = [key for key, _value in pairs]
        assert len(keys) == len(set(keys))
        assert required <= set(keys)
        key_sets.append(set(keys))
    assert key_sets[0] == key_sets[1] == key_sets[2]


def test_pocket_auto_editor_cycles_have_zero_qt_leak_and_no_tail_slowdown(
    qapp,
    record_testsuite_property,
) -> None:
    context, region = _automatic_context()
    baseline_pointers = top_level_baseline(qapp)
    stable = qt_lifecycle_snapshot(qapp)
    durations: list[float] = []
    service = translation_service()

    for cycle in range(24):
        started = perf_counter()
        with service.using(tuple(UiLanguage)[cycle % len(UiLanguage)]):
            draft = _draft(context, region)
            state = FunctionEditorDraftState(
                build_pocket_schema(context),
                pocket_applied_values(context),
                draft_transform_callback=lambda values, draft=draft: (
                    pocket_draft_transform(context, draft, values)
                ),
            )
            page = FunctionEditorPage(state)
            page.resize(300 + (cycle % 4) * 80, 620 + (cycle % 3) * 60)
            page.show()
            qapp.processEvents()
            state.edit(
                "quality_profile",
                ("fast", "balanced", "high")[cycle % 3],
            )
            qapp.processEvents()
            page.close()
            page.deleteLater()
            drain_test_owned_qt_state(qapp, baseline_pointers)
            del page
        durations.append(perf_counter() - started)
        current = qt_lifecycle_snapshot(qapp)
        assert current.top_levels <= stable.top_levels
        assert current.modal_top_levels <= stable.modal_top_levels
        assert current.running_app_threads == 0

    head_max = max(durations[:6])
    tail_max = max(durations[-6:])
    assert tail_max <= max(1.0, head_max * 5.0)
    final = qt_lifecycle_snapshot(qapp)
    assert final.top_levels <= stable.top_levels
    assert final.hidden_top_levels <= stable.hidden_top_levels
    assert final.modal_top_levels <= stable.modal_top_levels
    assert final.running_app_threads == 0
    record_testsuite_property("r205_pocket_cycles", str(len(durations)))
    record_testsuite_property(
        "r205_top_level_delta", str(final.top_levels - stable.top_levels)
    )
    record_testsuite_property(
        "r205_hidden_top_level_delta",
        str(final.hidden_top_levels - stable.hidden_top_levels),
    )
    record_testsuite_property(
        "r205_modal_delta", str(final.modal_top_levels - stable.modal_top_levels)
    )
    record_testsuite_property("r205_running_qthreads", str(final.running_app_threads))
    record_testsuite_property("r205_head_max_seconds", f"{head_max:.6f}")
    record_testsuite_property("r205_tail_max_seconds", f"{tail_max:.6f}")
