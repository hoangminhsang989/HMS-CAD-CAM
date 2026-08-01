"""Production Function Editor contracts for Z-Level Stage 8A.3.3."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QComboBox

from hms_cadcam.cam.application import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    CamQualityProfile,
    basic_mill_resources,
)
from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.zlevel import (
    Z_LEVEL_AUTOMATIC_PARAMETER_KEYS,
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
    ZLevelFinishingParameters,
    ZLevelGeometryEvidence,
    calculate_and_publish_z_level_finishing,
)
from hms_cadcam.cam.domain import ArtifactStatus, LengthUnit
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.ui.function_editor import (
    FunctionEditorDraftState,
    FunctionEditorPage,
    ParameterDisclosureLevel,
)
from hms_cadcam.ui.function_editor.strategies.zlevel import (
    Z_LEVEL_EDITOR_ID,
    Z_LEVEL_POST_FAIL_CLOSED_FOOTER,
    Z_LEVEL_POST_GATE_REASON,
    ZLevelEditorContext,
    ZLevelEditorDraftContext,
    build_z_level_schema,
    prepare_z_level_update,
    z_level_applied_values,
    z_level_draft_derived_values,
    z_level_validation_diagnostics,
)
from hms_cadcam.ui.operation_manager_status import z_level_safety_status
from hms_cadcam.ui.operation_manager_projection import (
    OperationManagerProjectionBuilder,
)
from hms_cadcam.ui.operation_manager_types import OperationManagerNodeKind
from tests.manual_stage8a3_3_zlevel_editor import (
    _qa_source_fingerprint,
    validate_review_summary,
)
from tests.unit._parallel_finishing_safety_fixtures import safe_holder_fixture
from tests.unit.test_parallel_finishing_persistence import _snapshot
from tools.audit_vietnamese_ui import (
    duplicate_user_facing_phrase_matches,
    unapproved_user_facing_acronym_matches,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _context(project_id=None) -> tuple[
    ZLevelEditorContext,
    ZLevelEditorDraftContext,
    object,
    object,
    object,
]:
    fixture, holder = safe_holder_fixture(project_id=project_id)
    parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        10.0,
        0.0,
        2.0,
    )
    operation = replace(
        fixture.operation,
        parameters=parameters.to_operation_parameters(),
    )
    setup = _snapshot(fixture, operation).jobs[0].setups[0]
    machine = basic_mill_resources(LengthUnit.MM)[3]
    evidence = ZLevelGeometryEvidence(
        0.0,
        10.0,
        0.0,
        10.0,
        0.0,
        10.0,
        "Hộp bao mặt đã chọn",
    )
    context = ZLevelEditorContext(
        "Gia công tinh theo cao độ Z",
        operation,
        setup,
        fixture.zone.job_id,
        fixture.zone.project_id,
        fixture.zone,
        (fixture.assembly,),
        (fixture.tool,),
        (holder,),
        (machine,),
        geometry_evidence=evidence,
    )
    draft = ZLevelEditorDraftContext(
        fixture.zone.part_surfaces.selection.surfaces,
        geometry_evidence=evidence,
    )
    return context, draft, machine, fixture, holder


def _valid_values(context: ZLevelEditorContext, machine: object) -> dict[str, object]:
    values = z_level_applied_values(context)
    values["machine_id"] = str(machine.machine_id)
    return values


def test_schema_has_basic_workflow_advanced_controls_and_no_expert() -> None:
    context, _draft, machine, _fixture, _holder = _context()
    schema = build_z_level_schema(context)
    basic_sections = {
        section.section_id
        for section in schema.sections
        if section.disclosure_level is ParameterDisclosureLevel.BASIC
    }
    advanced_sections = {
        section.section_id
        for section in schema.sections
        if section.disclosure_level is ParameterDisclosureLevel.ADVANCED
    }
    assert schema.editor_id == Z_LEVEL_EDITOR_ID
    assert {"geometry", "tool", "quality", "automatic_summary"} <= basic_sections
    assert {
        "levels",
        "cut_parameters",
        "contours",
        "linking",
        "capability_safety",
    } <= advanced_sections
    assert all(
        section.disclosure_level is not ParameterDisclosureLevel.EXPERT
        for section in schema.sections
    )
    assert schema.field("orientation").tooltip == (
        "Hướng đường đồng mức theo cấu trúc liên kết hình học."
    )
    assert schema.section("contours").summary.startswith(
        "Hướng đường đồng mức theo cấu trúc liên kết hình học."
    )
    assert schema.field("stepdown_override_enabled").tooltip.endswith(
        "HMS tính lại từ các dữ liệu phụ thuộc hiện hành."
    )
    assert schema.field("clearance_z_mm").label == "Cao độ an toàn"
    assert schema.field("retract_z_mm").label == "Cao độ rút dao"
    assert not any(
        item.severity.name == "ERROR"
        for item in z_level_validation_diagnostics(
            schema,
            context,
            ZLevelEditorDraftContext(
                context.zone.part_surfaces.selection.surfaces,
                geometry_evidence=context.geometry_evidence,
            ),
            _valid_values(context, machine),
        )
    )


def test_orientation_and_post_presentations_are_unambiguous_and_fail_closed() -> None:
    context, _draft, _machine, _fixture, _holder = _context()
    values = z_level_applied_values(context)
    schema = build_z_level_schema(context)
    orientation_labels = dict(schema.field("orientation").choice_labels)

    assert values["orientation_summary"].startswith("Tự động · Đã xác định ·")
    assert "Tự động · Tự động" not in str(values["orientation_summary"])
    assert str(values["orientation_summary"]).endswith(
        "Thuật toán xác định chiều quay theo loại vòng và trục W của Thiết lập."
    )
    assert orientation_labels == {
        "automatic": "Tự động",
        "clockwise": "Cùng chiều kim đồng hồ",
        "counter_clockwise": "Ngược chiều kim đồng hồ",
    }
    assert not unapproved_user_facing_acronym_matches(
        " · ".join(
            (
                str(values["orientation_summary"]),
                *orientation_labels.values(),
            )
        )
    )
    assert Z_LEVEL_POST_FAIL_CLOSED_FOOTER == (
        "Post sản xuất · bị chặn an toàn"
    )
    assert values["post_gate"] == f"Bị chặn · {Z_LEVEL_POST_GATE_REASON}"
    assert schema.field("post_gate").action_id == ""
    assert not duplicate_user_facing_phrase_matches(
        Z_LEVEL_POST_FAIL_CLOSED_FOOTER
    )


def test_z_level_geometry_actions_have_exact_accessibility_contract() -> None:
    application = _application()
    context, _draft, machine, _fixture, _holder = _context()
    schema = build_z_level_schema(context)
    values = _valid_values(context, machine)
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            schema,
            {
                field.field_id: values[field.field_id]
                for field in schema.fields
            },
        )
    )
    expected = {
        "reselect_geometry": (
            "reselect_z_level_faces",
            "Chọn lại",
            "Chọn lại bề mặt gia công",
            "Chọn lại bề mặt gia công từ vùng hiển thị CAD.",
        ),
        "remove_geometry": (
            "remove_z_level_faces",
            "Loại",
            "Loại bề mặt khỏi bản nháp",
            "Loại bề mặt đang chọn khỏi bản nháp.",
        ),
        "clear_geometry": (
            "clear_z_level_faces",
            "Xóa",
            "Xóa toàn bộ lựa chọn bề mặt",
            "Xóa toàn bộ lựa chọn bề mặt trong bản nháp.",
        ),
    }
    try:
        page.show()
        for field_id, (
            action_id,
            text,
            accessible_name,
            description,
        ) in expected.items():
            field = page._ensure_field(field_id)
            button = field.action_button
            interface = QAccessible.queryAccessibleInterface(button)

            assert field.definition.action_id == action_id
            assert button.objectName() == "FunctionEditorFieldAction"
            assert button.text() == text
            assert button.toolTip() == description
            assert button.accessibleName() == accessible_name
            assert button.accessibleDescription() == description
            assert interface is not None
            assert interface.text(QAccessible.Text.Name) == accessible_name
            assert interface.text(QAccessible.Text.Description) == description
            assert all(
                not duplicate_user_facing_phrase_matches(value)
                for value in (
                    button.text(),
                    button.toolTip(),
                    button.accessibleName(),
                    button.accessibleDescription(),
                )
            )
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_automatic_quality_is_deterministic_and_never_weakens_safety_scope() -> None:
    context, draft, machine, _fixture, _holder = _context()
    values = _valid_values(context, machine)
    values["quality_profile"] = CamQualityProfile.FAST.value
    fast = prepare_z_level_update(context, draft, values).automatic_contract
    fast_again = prepare_z_level_update(context, draft, values).automatic_contract
    values["quality_profile"] = CamQualityProfile.HIGH.value
    high = prepare_z_level_update(context, draft, values).automatic_contract

    assert fast == fast_again
    assert tuple(item.key for item in fast.values) == Z_LEVEL_AUTOMATIC_PARAMETER_KEYS
    assert float(high.value("stepdown_mm").effective_value) < float(
        fast.value("stepdown_mm").effective_value
    )
    assert float(high.value("tolerance_mm").effective_value) <= float(
        fast.value("tolerance_mm").effective_value
    )
    assert fast.value("safety_scope").effective_value == (
        high.value("safety_scope").effective_value
    )
    assert fast.value("protected_geometry_scope").effective_value == (
        high.value("protected_geometry_scope").effective_value
    )


def test_quality_combo_signal_updates_model_hash_and_illustration() -> None:
    application = _application()
    context, draft, machine, _fixture, _holder = _context()
    schema = build_z_level_schema(context)
    values = _valid_values(context, machine)
    page = FunctionEditorPage(
        FunctionEditorDraftState(
            schema,
            {
                field.field_id: values[field.field_id]
                for field in schema.fields
            },
            validation_callback=lambda current: z_level_validation_diagnostics(
                schema, context, draft, current
            ),
            draft_transform_callback=lambda current: (
                z_level_draft_derived_values(context, draft, current)
            ),
        )
    )
    try:
        combo = page._field_widgets["quality_profile"].editor
        assert isinstance(combo, QComboBox)
        records = {}
        for profile in (
            CamQualityProfile.FAST,
            CamQualityProfile.BALANCED,
            CamQualityProfile.HIGH,
        ):
            combo.setCurrentIndex(combo.findData(profile.value))
            application.processEvents()
            page.illustration_panel.flush_pending_update()
            contract = prepare_z_level_update(
                context, draft, page.state.values
            ).automatic_contract
            records[profile] = (
                page.state.values["quality_profile"],
                contract.value("stepdown_mm").effective_value,
                contract.value("tolerance_mm").effective_value,
                contract.effective_fingerprint.digest,
                page.illustration_panel.state.quality,
            )
        assert len({item[3] for item in records.values()}) == 3
        assert records[CamQualityProfile.FAST][1] > records[CamQualityProfile.HIGH][1]
        assert records[CamQualityProfile.FAST][2] >= records[CamQualityProfile.HIGH][2]
        assert all(
            item[0] == profile.value and item[4] == profile.value
            for profile, item in records.items()
        )
        fast = records[CamQualityProfile.FAST]
        balanced = records[CamQualityProfile.BALANCED]
        high = records[CamQualityProfile.HIGH]
        for profile, expected_stepdown, expected_tolerance, expected_levels in (
            (CamQualityProfile.FAST, 4.5, 0.01, 4),
            (CamQualityProfile.BALANCED, 3.0, 0.01, 5),
            (CamQualityProfile.HIGH, 1.8, 0.005, 7),
        ):
            combo.setCurrentIndex(combo.findData(profile.value))
            application.processEvents()
            state = page.state.values
            contract = prepare_z_level_update(context, draft, state).automatic_contract
            assert float(contract.value("stepdown_mm").effective_value) == pytest.approx(
                expected_stepdown
            )
            assert float(contract.value("tolerance_mm").effective_value) == pytest.approx(
                expected_tolerance
            )
            assert page._field_widgets["stepdown_summary"].editor.text().startswith(
                f"{expected_stepdown:g} mm"
            )
            assert page._field_widgets["tolerance_summary"].editor.text().startswith(
                f"{expected_tolerance:g} mm"
            )
            assert page._field_widgets["estimated_level_count"].editor.text().startswith(
                str(expected_levels)
            )
            assert int(page.state.values["estimated_level_count"]) == expected_levels
        assert fast[1] > balanced[1] > high[1]
        assert fast[4] != high[4]
        assert "protected_geometry_summary" in page.state.values
    finally:
        page.close()
        page.deleteLater()
        application.processEvents()


def test_manual_override_round_trip_preserves_intent_without_version_bumps() -> None:
    context, draft, machine, _fixture, _holder = _context()
    values = _valid_values(context, machine)
    values.update(
        {
            "stepdown_override_enabled": True,
            "stepdown_mm": "0.75",
            "tolerance_override_enabled": True,
            "tolerance_mm": "0.005",
            "allowance_override_enabled": True,
            "surface_allowance_mm": "0.15",
        }
    )
    update = prepare_z_level_update(context, draft, values)
    payload = dict(update.operation.parameters.values)[
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    ]
    contract = AutomaticParameterContract.from_json(payload)
    restored_parameters = ZLevelFinishingParameters.from_operation_parameters(
        update.operation.parameters
    )
    reopened = replace(
        context,
        operation=update.operation,
        zone=update.zone,
    )
    reopened_values = z_level_applied_values(reopened)

    assert Z_LEVEL_FINISHING_ALGORITHM_VERSION == 2
    assert Z_LEVEL_FINISHING_STRATEGY_VERSION == 1
    assert update.operation.parameters.schema_version == 1
    assert contract.value("stepdown_mm").mode is AutomaticParameterMode.MANUAL
    assert contract.value("stepdown_mm").override_value == 0.75
    assert restored_parameters.stepdown_mm == 0.75
    assert restored_parameters.tolerance_mm == 0.005
    assert restored_parameters.surface_allowance_mm == 0.15
    assert reopened_values["stepdown_override_enabled"] is True
    assert float(reopened_values["stepdown_mm"]) == 0.75


@pytest.mark.parametrize(
    ("updates", "code"),
    (
        (
            {
                "top_override_enabled": True,
                "top_level": "0",
                "bottom_override_enabled": True,
                "bottom_level": "1",
            },
            "z_level.invalid_bounds",
        ),
        (
            {
                "stepdown_override_enabled": True,
                "stepdown_mm": "0",
            },
            "z_level.invalid_manual_override",
        ),
        (
            {
                "top_override_enabled": True,
                "top_level": "100",
                "bottom_override_enabled": True,
                "bottom_level": "0",
                "stepdown_override_enabled": True,
                "stepdown_mm": "0.001",
            },
            "z_level.excessive_level_count",
        ),
    ),
)
def test_invalid_manual_ranges_fail_closed(
    updates: dict[str, object],
    code: str,
) -> None:
    context, draft, machine, _fixture, _holder = _context()
    values = _valid_values(context, machine)
    values.update(updates)
    with pytest.raises(ValueError, match=code):
        prepare_z_level_update(context, draft, values)


def test_missing_holder_and_geometry_never_claim_ready_or_safe() -> None:
    context, draft, machine, _fixture, _holder = _context()
    no_holder = replace(context, holder_definitions=())
    schema = build_z_level_schema(no_holder)
    diagnostics = z_level_validation_diagnostics(
        schema,
        no_holder,
        draft,
        _valid_values(no_holder, machine),
    )
    assert any(
        item.code.startswith("z-level.safety")
        or "holder" in item.message.casefold()
        for item in diagnostics
    )

    no_geometry = replace(
        context,
        zone=None,
        geometry_resolved=False,
        geometry_evidence=None,
    )
    values = z_level_applied_values(no_geometry)
    values["machine_id"] = str(machine.machine_id)
    errors = z_level_validation_diagnostics(
        build_z_level_schema(no_geometry),
        no_geometry,
        ZLevelEditorDraftContext(()),
        values,
    )
    assert any(item.severity.name == "ERROR" for item in errors)
    status = z_level_safety_status(no_geometry.operation, None)
    assert status is not None
    assert status.semantic.value != "current"


def test_workspace_apply_duplicate_save_open_keeps_sqlite_v4_contract(tmp_path) -> None:
    application = _application()
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Z-Level Editor")
    context, _draft, machine, fixture, holder = _context(
        session.manifest.project_id
    )
    operation = replace(
        context.operation,
        setup_id=fixture.zone.setup_id,
    )
    snapshot = replace(
        _snapshot(fixture, operation),
        holder_definitions=(holder,),
        machine_definitions=(machine,),
    )
    service.stage_cam_snapshot(snapshot)
    service.stage_cam3d_config(
        Cam3DProjectConfig(session.manifest.project_id, (fixture.zone,))
    )
    workspace = CamWorkspace(service, lambda: None)
    workspace.bind_project(session)
    assert workspace.select_identity("operation", str(operation.node_id))
    workspace._z_level_draft_evidence[operation.operation_id] = (
        context.geometry_evidence
    )
    production = workspace.production_function_editor_session()
    assert production is not None
    assert production.schema.editor_id == Z_LEVEL_EDITOR_ID
    values = production.applied_mapping()
    values["machine_id"] = str(machine.machine_id)
    values["stepdown_override_enabled"] = True
    values["stepdown_mm"] = "1.25"
    assert not any(
        item.severity.name == "ERROR"
        for item in production.validation_callback(values)
    )
    production.apply_callback(values)
    application.processEvents()

    applied = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert ZLevelFinishingParameters.from_operation_parameters(
        applied.parameters
    ).stepdown_mm == 1.25
    workspace.duplicate_selected_operation()
    application.processEvents()
    operations = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    assert len(operations) == 2
    duplicate = next(item for item in operations if item.node_id != applied.node_id)
    assert duplicate.revision.value == 0
    assert duplicate.artifact_state.status is ArtifactStatus.MISSING
    assert ZLevelFinishingParameters.from_operation_parameters(
        duplicate.parameters
    ).zone_id != ZLevelFinishingParameters.from_operation_parameters(
        applied.parameters
    ).zone_id

    root = session.root_path
    service.save()
    service.close_project(discard_changes=True)
    service.open_project(root)
    assert DATABASE_SCHEMA_VERSION == 5
    assert len(
        service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
    ) == 2
    assert len(service.cam3d_config.zones) == 2
    service.close_project()
    workspace.close()
    workspace.deleteLater()
    application.processEvents()


def test_project_gateway_commits_only_current_z_level_result(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Z-Level Lifecycle")
    context, _draft, _machine, fixture, holder = _context()
    flat_parameters = ZLevelFinishingParameters(
        fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
    )
    operation = replace(
        context.operation,
        parameters=flat_parameters.to_operation_parameters(),
    )
    service.stage_cam_snapshot(
        replace(_snapshot(fixture, operation), holder_definitions=(holder,))
    )
    generation = service.cam_generation

    def current_operation():
        return service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]

    observed: list[ArtifactStatus] = []

    def begin(computing):
        accepted = service.begin_z_level_calculation(
            computing,
            expected_generation=generation,
        )
        observed.append(current_operation().artifact_state.status)
        return accepted

    result = calculate_and_publish_z_level_finishing(
        session.root_path,
        operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder,
        computing_callback=begin,
        current_operation=current_operation,
    )
    assert observed == [ArtifactStatus.COMPUTING]
    assert result.accepted
    assert service.commit_z_level_calculation(
        result,
        expected_generation=generation,
    )
    assert current_operation().artifact_state.status is ArtifactStatus.VALID
    assert len(service.cam_snapshot.artifacts) == 1
    assert not service.commit_z_level_calculation(
        result,
        expected_generation=generation + 1,
    )
    service.close_project(discard_changes=True)


def test_operation_registration_creates_supported_z_level_draft(tmp_path) -> None:
    application = _application()
    source = tmp_path / "z-level-editor.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(tmp_path / "config")
    project = service.create_project_from_source(
        tmp_path,
        "Z-Level Registration",
        source,
    )
    source_id = project.manifest.source_files[0].source_id
    workspace = CamWorkspace(service, lambda: source_id)
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_parallel_resources()
    workspace.add_z_level_operation()
    application.processEvents()

    operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    production = workspace.production_function_editor_session()
    assert operation.strategy_key == "z_level_finishing_3d"
    assert operation.artifact_state.status is ArtifactStatus.MISSING
    assert production is not None
    assert production.schema.editor_id == Z_LEVEL_EDITOR_ID
    projection = OperationManagerProjectionBuilder().build(
        service, service.current_project
    )
    manager_row = next(
        item
        for item in projection.nodes
        if item.kind is OperationManagerNodeKind.OPERATION
    )
    assert manager_row.label == "Gia công tinh theo cao độ Z"
    assert manager_row.secondary_summary.startswith("Tool cầu ")
    assert "0 mặt" in manager_row.secondary_summary
    assert "Tính " in manager_row.secondary_summary
    assert "An toàn " in manager_row.secondary_summary
    assert any(
        item.severity.name == "ERROR"
        for item in production.validation_callback(production.applied_mapping())
    )
    service.close_project(discard_changes=True)
    workspace.close()
    workspace.deleteLater()
    application.processEvents()


def test_review_summary_checker_rejects_stale_qa_source_metadata() -> None:
    current = {
        "focused_baseline": "1 passed",
        "full_baseline": "2 passed, 1 deselected",
        "qa_metadata_source": "explicit_latest_run",
        "qa_source_fingerprint": _qa_source_fingerprint(),
        "total_file_count": 38,
    }
    validate_review_summary(current)
    with pytest.raises(ValueError, match="stale"):
        validate_review_summary(
            {
                **current,
                "qa_source_fingerprint": "0" * 64,
            }
        )
