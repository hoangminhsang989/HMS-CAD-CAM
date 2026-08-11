"""R211 Tapping/Reaming/Boring Auto Setup editor and persistence tests."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.automatic_boring import BORING_AUTOMATIC_POLICY_KEY
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
)
from hms_cadcam.cam.automatic_reaming import REAMING_AUTOMATIC_POLICY_KEY
from hms_cadcam.cam.automatic_tapping import TAPPING_AUTOMATIC_POLICY_KEY
from hms_cadcam.cam.application.boring import BoringGenerationError, BoringGenerator
from hms_cadcam.cam.application.reaming import ReamingGenerationError, ReamingGenerator
from hms_cadcam.cam.application.tapping import TappingGenerationError, TappingGenerator
from hms_cadcam.cam.domain import (
    BoringStrategy,
    DependencyFingerprint,
    OperationParameterSet,
    ReamingStrategy,
    TappingStrategy,
)
from hms_cadcam.ui.function_editor import ParameterDisclosureLevel
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from tests.unit import test_boring_ui as boring_ui
from tests.unit import test_reaming_ui as reaming_ui
from tests.unit import test_tapping_ui as tapping_ui


_CASES = (
    (
        tapping_ui._workspace,
        TAPPING_AUTOMATIC_POLICY_KEY,
        TappingStrategy,
        ("nominal_diameter_mode", "pitch_mode", "hand_mode"),
    ),
    (
        reaming_ui._workspace,
        REAMING_AUTOMATIC_POLICY_KEY,
        ReamingStrategy,
        ("nominal_diameter_mode",),
    ),
    (
        boring_ui._workspace,
        BORING_AUTOMATIC_POLICY_KEY,
        BoringStrategy,
        ("finished_bore_diameter_mode",),
    ),
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _operation(service):
    return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]


def _stored(operation) -> AutomaticParameterContract:
    raw = dict(operation.parameters.values)[AUTOMATIC_PARAMETER_CONTRACT_KEY]
    assert isinstance(raw, str)
    return AutomaticParameterContract.from_json(raw)


@pytest.mark.parametrize(("factory", "_policy", "_strategy", "target_modes"), _CASES)
def test_basic_advanced_hole_completion_status_and_authority_boundary(
    tmp_path,
    factory,
    _policy: str,
    _strategy: type,
    target_modes: tuple[str, ...],
) -> None:
    _application()
    _service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    values = production.applied_mapping()
    schema = production.schema
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
        "automatic_target_feature",
    } <= basic_ids
    advanced_ids = {
        field.field_id
        for section in schema.visible_sections(values, ParameterDisclosureLevel.ADVANCED)
        for field in section.fields
    }
    assert {
        "automatic_safe_plane",
        "automatic_provenance",
        "top_z_mode",
        "final_depth_mode",
        "retract_height_mode",
        "clearance_height_mode",
        *target_modes,
    } <= advanced_ids
    assert AutomaticParameterMode.AUTO.value in schema.field("top_z_mode").choices
    assert schema.field("final_depth_mode").choices == (
        AutomaticParameterMode.MANUAL_OVERRIDE.value,
    )
    for field_id in target_modes:
        assert schema.field(field_id).choices == (
            AutomaticParameterMode.MANUAL_OVERRIDE.value,
        )
    assert "AUTO" in str(values["automatic_summary"])
    assert "lỗ" in str(values["automatic_pattern"])
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "policy", "strategy_type", "target_modes"), _CASES)
def test_reset_reference_plane_to_auto_persists_without_changing_process_intent(
    tmp_path,
    factory,
    policy: str,
    strategy_type: type,
    target_modes: tuple[str, ...],
) -> None:
    _application()
    service, session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None and production.draft_transform_callback is not None
    before = strategy_type.from_operation_parameters(_operation(service).parameters)
    values = production.applied_mapping()
    values["top_z_mode"] = AutomaticParameterMode.AUTO.value
    values.update(production.draft_transform_callback(values))
    assert values["top_z_mode"] == AutomaticParameterMode.AUTO.value
    assert production.apply_callback(values)
    operation = _operation(service)
    after = strategy_type.from_operation_parameters(operation.parameters)
    contract = _stored(operation)
    assert contract.policy_key == policy
    assert contract.value("top_z").mode is AutomaticParameterMode.AUTO
    assert contract.value("final_depth").has_manual_override
    for field_id in target_modes:
        assert contract.value(field_id.removesuffix("_mode")).has_manual_override
    assert after == before

    service.save()
    service.close_project()
    service.open_project(session.root_path)
    restored = _operation(service)
    assert _stored(restored).to_json() == contract.to_json()
    assert strategy_type.from_operation_parameters(restored.parameters) == before
    workspace.deleteLater()


@pytest.mark.parametrize(("factory", "_policy", "_strategy", "_target_modes"), _CASES)
def test_unchanged_legacy_apply_does_not_silently_enable_auto(
    tmp_path,
    factory,
    _policy: str,
    _strategy: type,
    _target_modes: tuple[str, ...],
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None
    before = _operation(service)
    assert AUTOMATIC_PARAMETER_CONTRACT_KEY not in dict(before.parameters.values)
    assert production.apply_callback(production.applied_mapping())
    assert _operation(service) == before
    workspace.deleteLater()


@pytest.mark.parametrize(
    ("factory", "module", "generator_type", "error_type", "needs_holder"),
    (
        (tapping_ui._workspace, tapping_ui, TappingGenerator, TappingGenerationError, False),
        (reaming_ui._workspace, reaming_ui, ReamingGenerator, ReamingGenerationError, False),
        (boring_ui._workspace, boring_ui, BoringGenerator, BoringGenerationError, True),
    ),
)
def test_generator_accepts_current_auto_and_rejects_stale_dependency(
    tmp_path,
    factory,
    module,
    generator_type,
    error_type,
    needs_holder: bool,
) -> None:
    _application()
    service, _session, workspace, *_rest = factory(tmp_path)
    production = workspace.production_function_editor_session()
    assert production is not None and production.draft_transform_callback is not None
    values = production.applied_mapping()
    values["top_z_mode"] = AutomaticParameterMode.AUTO.value
    values.update(production.draft_transform_callback(values))
    assert production.apply_callback(values)
    operation = _operation(service)
    setup = service.cam_snapshot.jobs[0].setups[0]
    strategy_type = {
        TappingGenerator: TappingStrategy,
        ReamingGenerator: ReamingStrategy,
        BoringGenerator: BoringStrategy,
    }[generator_type]
    strategy = strategy_type.from_operation_parameters(operation.parameters)
    assembly = next(
        item
        for item in service.cam_snapshot.tool_assemblies
        if item.assembly_id == operation.tool_assembly.assembly_id
    )
    tool = next(
        item for item in service.cam_snapshot.tool_definitions if item.tool_id == assembly.tool_id
    )
    holder = next(
        (
            item
            for item in service.cam_snapshot.holder_definitions
            if item.holder_id == assembly.holder_id
        ),
        None,
    )
    machine = next(
        item
        for item in service.cam_snapshot.machine_definitions
        if operation.machine_requirement is not None
        and item.machine_id == operation.machine_requirement.machine_id
    )
    resolved = module._resolved(strategy.geometry, strategy.depth)
    generator = generator_type()
    kwargs = {
        "assembly": assembly,
        "tool": tool,
        "machine": machine,
        "resolved_geometry": resolved,
    }
    if needs_holder:
        kwargs["holder"] = holder
    generator.resolve_inputs(operation, setup, **kwargs)

    contract = _stored(operation)
    stale_values = tuple(
        replace(
            item,
            dependency_fingerprint=DependencyFingerprint.from_payload(
                {"r211": "stale"}
            ),
        )
        if item.key == "top_z"
        else item
        for item in contract.values
    )
    stale_contract = replace(contract, values=stale_values)
    parameter_values = tuple(
        (
            key,
            stale_contract.to_json()
            if key == AUTOMATIC_PARAMETER_CONTRACT_KEY
            else value,
        )
        for key, value in operation.parameters.values
    )
    stale_operation = replace(
        operation,
        parameters=OperationParameterSet(
            operation.parameters.strategy_key,
            operation.parameters.strategy_version,
            parameter_values,
            operation.parameters.schema_version,
        ),
    )
    with pytest.raises(error_type, match="Auto Setup"):
        generator.resolve_inputs(stale_operation, setup, **kwargs)

    malformed_parameters = OperationParameterSet(
        operation.parameters.strategy_key,
        operation.parameters.strategy_version,
        tuple(
            (key, "{" if key == AUTOMATIC_PARAMETER_CONTRACT_KEY else value)
            for key, value in operation.parameters.values
        ),
        operation.parameters.schema_version,
    )
    with pytest.raises(error_type, match="Auto Setup"):
        generator.resolve_inputs(
            replace(operation, parameters=malformed_parameters), setup, **kwargs
        )
    workspace.deleteLater()


def test_hole_completion_catalog_parity_and_runtime_localization() -> None:
    catalog_root = Path("src/hms_cadcam/ui/catalogs")
    names = ("vi_VN.json", "en_US.json", "ko_KR.json")
    required = {
        "Hole Completion Auto Setup",
        "Feature đích",
        "Định nghĩa ren",
        "Đường kính hoàn thiện",
        "Đường kính doa đích",
        "Chế độ đường kính",
        "Chế độ bước ren",
        "Chế độ hướng ren",
        "Chế độ đường kính doa",
        "Plain hole diameter does not define thread standard, pitch or nominal diameter.",
        "No authoritative finished-feature diameter is present; source-hole and Tool diameters are not substituted.",
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
    service = translation_service()
    with service.using(UiLanguage.EN_US):
        assert service.translate("Feature đích") == "Target feature"
    with service.using(UiLanguage.KO_KR):
        assert service.translate("Feature đích") == "대상 피처"
