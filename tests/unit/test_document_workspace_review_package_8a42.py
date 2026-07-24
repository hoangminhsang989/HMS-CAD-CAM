"""Static contract tests for the exact Stage 8A.4.2 review harness."""

from pathlib import Path

from tools.create_stage8a42_review_package import (
    JSON_NAMES,
    OUTPUT,
    PNG_NAMES,
)


def test_review_package_declares_exact_43_files() -> None:
    expected_png = {
        f"{index:02d}_{name}"
        for index, name in enumerate(
            (
                "open_source_as_cad_document.png",
                "drag_drop_equivalent_open.png",
                "first_save_suggested_source_folder.png",
                "hms_unicode_spaces_filename.png",
                "hms_invalid_filename.png",
                "open_existing_hms.png",
                "create_cam_project_dialog.png",
                "project_name_physical_preview.png",
                "unsafe_parent_path_blocked.png",
                "cam_project_structure.png",
                "cam_workspace_active.png",
                "create_project_from_hms.png",
                "source_internal_name_metadata.png",
                "working_geometry_unpacked.png",
                "cam_project_save_root.png",
                "unsaved_document_lifecycle.png",
                "project_creation_rollback.png",
                "dpi_150.png",
                "send_geometry_to_cam_command.png",
                "select_target_cam_project.png",
                "invalid_target_project_blocked.png",
                "open_project_non_modal_notification.png",
                "closed_project_pending_transfer.png",
                "pending_detected_after_project_open.png",
                "incoming_geometry_change_preview.png",
                "apply_as_new_model.png",
                "replace_existing_model.png",
                "update_matching_model_version.png",
                "defer_reject_duplicate_request.png",
                "apply_failure_rollback_and_stale.png",
            ),
            start=1,
        )
    }
    assert set(PNG_NAMES) == expected_png
    assert len(PNG_NAMES) == 30
    assert len(JSON_NAMES) == 12
    assert len(PNG_NAMES) + len(JSON_NAMES) + 1 == 43


def test_review_package_uses_required_output_directory() -> None:
    assert OUTPUT.parts[-3:] == (
        "reference_private",
        "DERIVED",
        "UI_STAGE_8A4_2_HMS_CAM_WORKSPACE",
    )


def test_review_harness_source_requires_production_widgets_and_hashes() -> None:
    source = Path(
        "tools/create_stage8a42_review_package.py"
    ).read_text(encoding="utf-8")
    assert "MainWindow" in source
    assert "CamProjectDialog" in source
    assert "production_model_service_widget_only" in source
    assert "png_sha256" in source
    assert "full_widget_capture" in source
    assert "ui_tokens.py" in source
    assert "menu_text_clipping_count" in source
    assert "unapproved_property_label_count" in source
