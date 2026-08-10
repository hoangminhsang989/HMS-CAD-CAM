"""Stage17A Tranche4 Drilling Auto Setup editor and persistence tests."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.automatic_drilling import (
    DRILLING_AUTOMATIC_POLICY_KEY,
    DrillingAutomaticContext,
    resolve_drilling_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
)
from hms_cadcam.cam.domain import (
    DrillingCycle,
    DrillingStrategy,
    LengthUnit,
    OperationParameterSet,
)
from hms_cadcam.ui.function_editor import ParameterDisclosureLevel
from hms_cadcam.ui.function_editor.strategies import common_drilling as drilling_editor
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from tests.unit import test_drilling_ui as drilling_ui


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _operation(service):
    return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]


def _stored(operation) -> AutomaticParameterContract:
    raw = dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    assert isinstance(raw, str)
    return AutomaticParameterContract.from_json(raw)


def test_basic_and_advanced_drilling_auto_setup_fields(tmp_path) -> None:
    _application()
    _service, _session, workspace, *_rest = drilling_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    schema = production.schema
    values = production.applied_mapping()

    basic_ids = {
        field.field_id
        for section in schema.visible_sections(values, ParameterDisclosureLevel.BASIC)
        for field in section.fields
    }
    assert {
        "automatic_summary",
        "automatic_pattern",
        "automatic_target_depth",
        "automatic_reference_plane",
    } <= basic_ids
    assert values["top_z_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert values["final_depth_mode"] == AutomaticParameterMode.MANUAL_OVERRIDE.value
    assert "lỗ" in str(values["automatic_pattern"])

    advanced_ids = {
        field.field_id
        for section in schema.visible_sections(values, ParameterDisclosureLevel.ADVANCED)
        for field in section.fields
    }
    assert {
        "automatic_safe_plane",
        "automatic_spot_depth",
        "automatic_peck",
        "automatic_provenance",
        "top_z_mode",
        "final_depth_mode",
        "retract_height_mode",
        "clearance_height_mode",
    } <= advanced_ids
    top_mode = schema.field("top_z_mode")
    assert AutomaticParameterMode.AUTO.value in top_mode.choices
    assert schema.field("clearance_height_mode").choices == (
        AutomaticParameterMode.MANUAL_OVERRIDE.value,
    )


def test_reset_top_to_auto_persists_contract_and_preserves_process_fields(tmp_path) -> None:
    _application()
    service, _session, workspace, *_rest = drilling_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None and production.draft_transform_callback is not None
    before = DrillingStrategy.from_operation_parameters(_operation(service).parameters)
    values = production.applied_mapping()
    values["top_z_mode"] = AutomaticParameterMode.AUTO.value
    transformed = production.draft_transform_callback(values)
    values.update(transformed)
    assert values["top_z_mode"] == AutomaticParameterMode.AUTO.value
    assert production.apply_callback(values)

    operation = _operation(service)
    after = DrillingStrategy.from_operation_parameters(operation.parameters)
    contract = _stored(operation)
    assert contract.policy_key == DRILLING_AUTOMATIC_POLICY_KEY
    assert contract.value("top_z").mode is AutomaticParameterMode.AUTO
    assert contract.value("final_depth").has_manual_override
    assert after.feed_rate == before.feed_rate
    assert after.spindle_speed == before.spindle_speed
    assert after.cycle == before.cycle
    assert after.dwell_seconds == before.dwell_seconds


def test_manual_override_survives_project_roundtrip(tmp_path) -> None:
    _application()
    service, session, workspace, *_rest = drilling_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    values = production.applied_mapping()
    values["final_depth_mode"] = AutomaticParameterMode.MANUAL_OVERRIDE.value
    values["final_depth"] = "-4.25"
    assert production.apply_callback(values)
    operation = _operation(service)
    assert _stored(operation).value("final_depth").effective_value == -4.25

    service.save()
    service.close_project()
    service.open_project(session.root_path)
    restored = _operation(service)
    assert DrillingStrategy.from_operation_parameters(restored.parameters).final_depth.value == -4.25
    assert _stored(restored).value("final_depth").effective_value == -4.25


def test_legacy_apply_remains_byte_equivalent_and_malformed_metadata_fails_safe(tmp_path) -> None:
    _application()
    service, _session, workspace, *_rest = drilling_ui._workspace(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    before = _operation(service)
    assert AUTOMATIC_PARAMETER_CONTRACT_KEY not in dict(before.parameters.values)
    assert production.apply_callback(production.applied_mapping())
    assert _operation(service) == before

    malformed = OperationParameterSet(
        before.parameters.strategy_key,
        before.parameters.strategy_version,
        before.parameters.values + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, "{"),),
        before.parameters.schema_version,
    )
    broken = replace(before, parameters=malformed)
    assert DrillingStrategy.from_operation_parameters(broken.parameters) == (
        DrillingStrategy.from_operation_parameters(before.parameters)
    )
    with pytest.raises(ValueError, match="malformed"):
        AutomaticParameterContract.from_json("{")


def test_drilling_auto_catalogs_have_parity_no_duplicates_and_localize_runtime() -> None:
    catalog_root = Path("src/hms_cadcam/ui/catalogs")
    names = ("vi_VN.json", "en_US.json", "ko_KR.json")
    required = {
        "Drilling Auto Setup",
        "Auto Setup",
        "Mẫu lỗ tự động",
        "Độ sâu đích",
        "Mặt phẳng tham chiếu",
        "Hình học Spot",
        "Mặt phẳng an toàn",
        "Biên peck thủ công",
        "Chế độ Top",
        "Chế độ độ sâu",
        "Chế độ Retract",
        "Chế độ Clearance",
        "Hole geometry is missing, stale or unresolved.",
        "Peck amount is explicit machining-process intent; no material-less AUTO rule is permitted.",
    }
    catalogs: dict[str, dict[str, str]] = {}
    for name in names:
        raw = (catalog_root / name).read_text(encoding="utf-8")
        assert "\ufffd" not in raw
        pairs: list[tuple[str, str]] = []
        json.loads(raw, object_pairs_hook=lambda items: pairs.extend(items) or dict(items))
        assert len(pairs) == len({key for key, _value in pairs})
        catalogs[name] = json.loads(raw)
        assert required <= set(catalogs[name])
        assert all(catalogs[name][key].strip() for key in required)
    assert tuple(catalogs[names[0]]) == tuple(catalogs[names[1]]) == tuple(catalogs[names[2]])

    contract = resolve_drilling_automatic_contract(
        DrillingAutomaticContext(
            LengthUnit.MM,
            DrillingCycle.DRILL,
            (),
            "geometry",
            False,
            None,
            None,
            None,
            None,
            None,
            None,
            0.0,
            -5.0,
            5.0,
            2.0,
            None,
            1.0e-6,
        )
    )
    service = translation_service()
    with service.using(UiLanguage.EN_US):
        en = drilling_editor._drilling_automatic_presentation(contract, "mm")
    with service.using(UiLanguage.KO_KR):
        ko = drilling_editor._drilling_automatic_presentation(contract, "mm")
    assert "unavailable" in str(en["automatic_pattern"]).lower()
    assert "사용" in str(ko["automatic_pattern"])
