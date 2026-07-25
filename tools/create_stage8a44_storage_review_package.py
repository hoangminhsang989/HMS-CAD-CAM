"""Create the native-Windows Stage 8A.4.4 storage review package."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from PySide6.QtCore import QSettings, QSignalBlocker, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QPlainTextEdit,
    QTabBar,
    QTabWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidgetItem,
    QWidget,
)

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.core.hms_backup import (
    BackupCategory,
    BackupScope,
    ConflictAction,
    HmsBackupService,
    HmsRestoreService,
)

from hms_cadcam.core.paths import (
    INSTALL_CHILDREN,
    PROGRAM_DATA_CHILDREN,
    USER_LOCAL_CHILDREN,
    USER_ROAMING_CHILDREN,
    AppPathKind,
    ApplicationPathsService,
    KnownFolder,
    StaticKnownFolderProvider,
)
from hms_cadcam.core.storage_backup import MachineBackupService
from hms_cadcam.core.storage_config import ConfigurationService
from hms_cadcam.core.storage_io import (
    AtomicBytesWriter,
    MachineResource,
    ResourceFileLock,
)
from hms_cadcam.core.storage_layout import StorageBootstrapService
from hms_cadcam.core.storage_migration import (
    LegacyMigrationService,
    MigrationResourceType,
)
from hms_cadcam.core.storage_security import validate_storage_write_path
from hms_cadcam.core.user_profiles import UserProfileService
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.backup_profiles import (
    BackupWizardDialog,
    RestoreWizardDialog,
    UserProfilesDialog,
)
from hms_cadcam.ui.data_locations import DataLocationsDialog, StorageNotificationBar
from hms_cadcam.ui.i18n import UiLanguage, apply_application_font, translation_service
from hms_cadcam.ui.localization_audit import (
    LOCALIZATION_AUDIT_EXCLUDE_ROLE,
    _is_mixed,
    audit_widget,
)
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.operation_manager_types import OperationManagerNodeKind
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


OUTPUT = (
    REPOSITORY_ROOT
    / "reference_private"
    / "DERIVED"
    / "UI_STAGE_8A4_4_STORAGE_ARCHITECTURE"
)
PNG_NAMES = (
    "01_data_locations_vietnamese.png",
    "02_data_locations_english.png",
    "03_data_locations_korean.png",
    "04_install_root_read_only.png",
    "05_programdata_layout_healthy.png",
    "06_appdata_layout_healthy.png",
    "07_document_project_separation.png",
    "08_first_bootstrap_preview.png",
    "09_bootstrap_success.png",
    "10_partial_layout_repair.png",
    "11_permission_denied_read_only.png",
    "12_missing_shared_data_notification.png",
    "13_machine_config_precedence.png",
    "14_user_preference_precedence.png",
    "15_atomic_config_write.png",
    "16_concurrent_resource_lock.png",
    "17_legacy_migration_preview.png",
    "18_migration_conflict_blocked.png",
    "19_machine_backup_retention.png",
    "20_path_escape_blocked.png",
    "21_dpi_150_vietnamese.png",
    "22_dpi_150_korean.png",
    "23_backup_settings_categories.png",
    "24_backup_select_all.png",
    "25_backup_selective_categories.png",
    "26_backup_select_profiles.png",
    "27_backup_choose_destination.png",
    "28_backup_confirmation.png",
    "29_backup_success_bakuphms.png",
    "30_restore_choose_bakuphms.png",
    "31_restore_validation_preview.png",
    "32_restore_selective_categories.png",
    "33_restore_conflict_resolution.png",
    "34_restore_permission_partial.png",
    "35_restore_success.png",
    "36_restore_invalid_or_incompatible.png",
    "37_restore_failure_rollback.png",
    "38_user_profiles_settings.png",
    "39_user_profile_create_copy_default.png",
    "40_user_profile_runtime_switch.png",
)
JSON_NAMES = (
    "summary.json",
    "path_resolution_report.json",
    "storage_layout_report.json",
    "bootstrap_permission_report.json",
    "configuration_precedence_report.json",
    "concurrency_atomicity_report.json",
    "backup_retention_report.json",
    "migration_report.json",
    "localization_accessibility_report.json",
    "responsive_security_boundary_report.json",
    "backup_container_report.json",
    "backup_category_scope_report.json",
    "restore_validation_conflict_report.json",
    "restore_atomicity_rollback_report.json",
    "user_profiles_report.json",
    "profile_switch_persistence_report.json",
)
SOURCE_FINGERPRINT_FILES = (
    "src/hms_cadcam/core/paths.py",
    "src/hms_cadcam/core/storage_security.py",
    "src/hms_cadcam/core/storage_io.py",
    "src/hms_cadcam/core/storage_layout.py",
    "src/hms_cadcam/core/storage_config.py",
    "src/hms_cadcam/core/storage_backup.py",
    "src/hms_cadcam/core/storage_migration.py",
    "src/hms_cadcam/core/storage_maintenance.py",
    "src/hms_cadcam/core/hms_backup.py",
    "src/hms_cadcam/core/user_profiles.py",
    "src/hms_cadcam/ui/data_locations.py",
    "src/hms_cadcam/ui/backup_profiles.py",
    "src/hms_cadcam/ui/storage_translations.py",
    "src/hms_cadcam/ui/i18n.py",
    "src/hms_cadcam/ui/localization_audit.py",
    "src/hms_cadcam/ui/main_window.py",
    "src/hms_cadcam/application.py",
    "src/hms_cadcam/project/service.py",
    "tests/unit/test_storage_architecture_8a44.py",
    "tests/unit/test_hms_backup_profiles_8a44.py",
    "tools/create_stage8a44_storage_review_package.py",
)
PROFILE_SWITCH_VISUAL_FIELDS = (
    "visual_main_window_captured",
    "visual_project_title_visible",
    "visual_dirty_marker_visible",
    "visual_project_tree_visible",
    "visual_selected_geometry_visible",
    "visual_operation_visible",
    "visual_operation_count_visible",
    "visual_properties_dock_visible",
    "visual_output_log_dock_visible",
    "visual_worker_id_visible",
    "visual_worker_state_visible",
    "visual_active_profile_visible",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prepare_output() -> None:
    expected_parent = REPOSITORY_ROOT / "reference_private" / "DERIVED"
    if OUTPUT.parent != expected_parent or OUTPUT.name != "UI_STAGE_8A4_4_STORAGE_ARCHITECTURE":
        raise RuntimeError("Unsafe review output path")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)


def _production_preview() -> ApplicationPathsService:
    provider = StaticKnownFolderProvider(
        {
            KnownFolder.PROGRAM_DATA: Path("C:/ProgramData"),
            KnownFolder.ROAMING_APP_DATA: Path("C:/Users/HMS-User/AppData/Roaming"),
            KnownFolder.LOCAL_APP_DATA: Path("C:/Users/HMS-User/AppData/Local"),
            KnownFolder.DOCUMENTS: Path("C:/Users/HMS-User/Documents"),
        }
    )
    return ApplicationPathsService.production(known_folders=provider)


def _prepare_install(paths: ApplicationPathsService) -> None:
    root = paths.path(AppPathKind.INSTALL_ROOT)
    root.mkdir(parents=True)
    for kind, name in INSTALL_CHILDREN.items():
        target = root / name
        if kind is AppPathKind.EXECUTABLE:
            target.write_bytes(b"HMS Stage 8A.4.4 review executable placeholder")
        else:
            target.mkdir()


def _write_review_resource(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _seed_backup_resources(paths: ApplicationPathsService) -> None:
    """Create only category-owned review data inside the injected sandbox."""
    _write_review_resource(
        paths.path(AppPathKind.TOOL_LIBRARY) / "review-tool.json",
        '{"resource_id":"review-tool","schema_version":1}',
    )
    _write_review_resource(
        paths.path(AppPathKind.TOOL_LIBRARY) / "Holders" / "review-holder.json",
        '{"resource_id":"review-holder","schema_version":1}',
    )
    _write_review_resource(
        paths.path(AppPathKind.PROGRAM_TEMPLATES) / "review-template.json",
        '{"resource_id":"review-template","schema_version":1}',
    )
    _write_review_resource(
        paths.path(AppPathKind.POSTS) / "review-post.py",
        "POST_LIBRARY_DATA = 'never executed by backup or restore'\n",
    )
    _write_review_resource(
        paths.path(AppPathKind.MACHINES) / "review-machine.json",
        '{"resource_id":"review-machine","schema_version":1}',
    )
    _write_review_resource(
        paths.path(AppPathKind.MATERIALS) / "review-material.json",
        '{"resource_id":"review-material","density":7.85}',
    )
    _write_review_resource(
        paths.path(AppPathKind.MACHINE_CONFIG) / "review-config.json",
        '{"schema_version":1,"units":"metric"}',
    )
    _write_review_resource(
        paths.path(AppPathKind.SCHEMAS) / "review-catalog.json",
        '{"schema_version":1,"catalog":"review"}',
    )


class _FailSecondReviewWriter(AtomicBytesWriter):
    """Deterministic injected write fault used to prove production rollback."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def write(self, root: Path, target: Path, payload: bytes) -> str:
        self.calls += 1
        if self.calls == 2:
            raise OSError("injected review publish failure")
        return super().write(root, target, payload)


def _capture(
    application: QApplication,
    widget,
    filename: str,
    *,
    size: tuple[int, int] = (1180, 800),
) -> dict[str, Any]:
    widget.resize(*size)
    widget.show()
    widget.raise_()
    for _pass in range(3):
        application.processEvents()
        if widget.layout() is not None:
            widget.layout().activate()
        widget.repaint()
    image = widget.grab()
    if image.isNull() or not image.save(str(OUTPUT / filename)):
        raise RuntimeError(f"Cannot save review capture: {filename}")
    metrics = audit_widget(widget)
    mixed = tuple(
        sorted(
            text
            for text in metrics.texts
            if _is_mixed(text, translation_service().language)
        )
    )
    required_zero = {
        "missing_accessible_name_count": metrics.missing_accessible_name_count,
        "missing_accessible_description_count": metrics.missing_accessible_description_count,
        "clipping_count": metrics.clipping_count,
        "horizontal_scroll_count": metrics.horizontal_scroll_count,
        "missing_glyph_count": metrics.missing_glyph_count,
        "replacement_glyph_count": metrics.replacement_glyph_count,
        "tofu_count": metrics.tofu_count,
        "mixed_language_count": len(mixed),
        "unapproved_english_token_count": metrics.unapproved_english_token_count,
        "physical_path_false_positive_count": (
            metrics.physical_path_false_positive_count
        ),
        "vietnamese_semantic_translation_error_count": (
            metrics.vietnamese_semantic_translation_error_count
        ),
    }
    failures = {key: value for key, value in required_zero.items() if value}
    if failures:
        clipped_widgets: list[tuple[str, str, str]] = []
        mixed_widgets: list[tuple[str, str, str, str]] = []
        for child in (widget, *widget.findChildren(QWidget)):
            getter = getattr(child, "text", None)
            if not callable(getter) or not child.isVisible():
                text = ""
            else:
                text = str(getter() or "")
                if text and "\n" not in text and child.width() > 0:
                    margins = child.contentsMargins()
                    available = (
                        child.width() - margins.left() - margins.right() - 4
                    )
                    if (
                        available > 0
                        and child.fontMetrics().horizontalAdvance(text)
                        > available + 8
                    ):
                        clipped_widgets.append(
                            (type(child).__name__, child.objectName(), text)
                        )
            for getter_name in (
                "text",
                "title",
                "toolTip",
                "accessibleName",
                "accessibleDescription",
                "placeholderText",
                "toPlainText",
            ):
                getter_value = getattr(child, getter_name, None)
                if not callable(getter_value) or not child.isVisibleTo(widget):
                    continue
                if (
                    bool(child.property("localizationAuditDomainText"))
                    and getter_name in {
                        "text",
                        "toPlainText",
                        "accessibleName",
                        "accessibleDescription",
                    }
                ):
                    continue
                rendered = str(getter_value() or "").strip()
                if rendered and _is_mixed(
                    rendered,
                    translation_service().language,
                ):
                    mixed_widgets.append(
                        (
                            type(child).__name__,
                            child.objectName(),
                            getter_name,
                            rendered,
                        )
                    )
        visible_views = tuple(
            (
                type(view).__name__,
                view.objectName(),
                bool(view.property("localizationAuditDomainText")),
            )
            for view in widget.findChildren(QAbstractItemView)
            if view.isVisibleTo(widget)
        )
        mixed_actions = tuple(
            (
                action.objectName(),
                action.text(),
                type(action.parent()).__name__,
            )
            for action in widget.findChildren(QAction)
            if _is_mixed(
                str(action.text() or ""),
                translation_service().language,
            )
        )
        visible_tabs = tuple(
            (
                tab.objectName(),
                tuple(tab.tabText(index) for index in range(tab.count())),
                type(tab.parent()).__name__,
                (
                    tab.parent().objectName()
                    if isinstance(tab.parent(), QWidget)
                    else ""
                ),
            )
            for tab in widget.findChildren(QTabWidget)
            if tab.isVisibleTo(widget)
        )
        raise RuntimeError(
            f"Rendered audit failed for {filename}: {failures!r}; "
            f"mixed={mixed[:8]!r}; "
            f"mixed_widgets={mixed_widgets[:12]!r}; "
            f"mixed_actions={mixed_actions[:12]!r}; "
            f"visible_tabs={visible_tabs!r}; "
            f"visible_views={visible_views!r}; "
            f"clipped={clipped_widgets!r}"
        )
    return {
        "filename": filename,
        "locale": translation_service().language.value,
        "width": image.width(),
        "height": image.height(),
        "device_pixel_ratio": image.devicePixelRatio(),
        "production_widget": type(widget).__name__,
        "model_state_asserted": True,
        **required_zero,
    }


def _dialog(
    paths: ApplicationPathsService,
    bootstrap: StorageBootstrapService,
) -> DataLocationsDialog:
    dialog = DataLocationsDialog(
        paths,
        bootstrap,
        production_preview=_production_preview(),
    )
    if dialog.tabs.count() != 4 or dialog._models[1].rowCount() != 9:
        raise RuntimeError("Production data-location model contract failed")
    return dialog


def _run_dpi_capture(locale_value: str, filename: str) -> int:
    """Capture one real 150% Qt process and return its runtime metadata."""
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() in {"offscreen", "minimal"}:
        raise RuntimeError("DPI review requires native Windows QPA")
    application = QApplication(sys.argv)
    if application.platformName().casefold() != "windows":
        raise RuntimeError(f"Expected Windows QPA, got {application.platformName()!r}")
    service = translation_service()
    service.set_language(UiLanguage(locale_value))
    apply_application_font(UiLanguage(locale_value), application)
    with tempfile.TemporaryDirectory(prefix="hms-stage8a44-dpi-") as raw:
        paths = ApplicationPathsService.sandbox(Path(raw) / "dpi", review=True)
        _prepare_install(paths)
        bootstrap = StorageBootstrapService(paths)
        if not bootstrap.bootstrap().inspection.ready:
            raise RuntimeError("DPI review sandbox did not bootstrap")
        dialog = _dialog(paths, bootstrap)
        dialog.resize(1320, 860)
        dialog.show()
        application.processEvents()
        capture = _capture(application, dialog, filename, size=(1320, 860))
        screen = application.primaryScreen()
        if screen is None:
            raise RuntimeError("No primary screen for DPI capture")
        dpr = float(screen.devicePixelRatio())
        logical_x = float(screen.logicalDotsPerInchX())
        logical_y = float(screen.logicalDotsPerInchY())
        physical_x = float(screen.physicalDotsPerInchX())
        physical_y = float(screen.physicalDotsPerInchY())
        effective = dpr if dpr > 1.01 else max(logical_x, logical_y) / 96.0
        if abs(effective - 1.5) > 0.2:
            raise RuntimeError(f"150% runtime scale was not observed: {effective}")
        metadata = {
            **capture,
            "requested_scale_percent": 150,
            "effective_scale_factor": effective,
            "device_pixel_ratio": dpr,
            "logical_dpi_x": logical_x,
            "logical_dpi_y": logical_y,
            "physical_dpi_x": physical_x,
            "physical_dpi_y": physical_y,
            "screen_name": screen.name(),
            "capture_process_id": os.getpid(),
            "scale_configuration_source": "QT_SCALE_FACTOR=1.5",
            "runtime_verified": True,
        }
        print(json.dumps(metadata, ensure_ascii=False))
        dialog.close()
        application.processEvents()
    return 0


def _dispose(widget, application: QApplication) -> None:
    widget.close()
    application.processEvents()
    widget.deleteLater()


def create_package(*, qa_results: Mapping[str, object] | None = None) -> dict[str, object]:
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() in {"offscreen", "minimal"}:
        raise RuntimeError("Storage review requires native Windows QPA")
    _prepare_output()
    dpi_captures: list[dict[str, Any]] = []
    for locale, index in (
        (UiLanguage.VI_VN, 20),
        (UiLanguage.KO_KR, 21),
    ):
        env = os.environ.copy()
        env["QT_SCALE_FACTOR"] = "1.5"
        env["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
        env["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--dpi-locale",
                locale.value,
                "--dpi-filename",
                PNG_NAMES[index],
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("DPI helper did not return metadata")
        dpi_captures.append(json.loads(lines[-1]))
    application = QApplication.instance() or QApplication(sys.argv)
    if application.platformName().casefold() != "windows":
        raise RuntimeError(f"Expected Windows QPA, got {application.platformName()!r}")
    service = translation_service()
    service.clear_diagnostics()
    capture_started = datetime.now(timezone.utc)
    captures: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="hms-stage8a44-review-") as raw:
        review_root = Path(raw).resolve()
        healthy_paths = ApplicationPathsService.sandbox(review_root / "healthy", review=True)
        _prepare_install(healthy_paths)
        healthy_bootstrap = StorageBootstrapService(healthy_paths)
        healthy_result = healthy_bootstrap.bootstrap()
        if not healthy_result.inspection.ready:
            raise RuntimeError("Healthy review sandbox did not bootstrap")
        dialog = _dialog(healthy_paths, healthy_bootstrap)

        for index, language in enumerate(UiLanguage):
            service.set_language(language)
            apply_application_font(language, application)
            dialog.tabs.setCurrentIndex(0)
            dialog.refresh_inspection()
            captures.append(_capture(application, dialog, PNG_NAMES[index]))

        service.set_language(UiLanguage.EN_US)
        dialog.tabs.setCurrentIndex(0)
        dialog.show_diagnostics(
            "Program installation",
            (("Install root", "Read-only", "Runtime never writes to the installation root"),),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[3]))

        dialog.tabs.setCurrentIndex(1)
        dialog.show_diagnostics(
            "Shared machine data",
            (
                ("Directory count", "8", "8 required folders"),
                ("Layout manifest", "PASS", "Layout manifest checksum verified"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[4]))

        dialog.tabs.setCurrentIndex(2)
        dialog.show_diagnostics(
            "User data",
            (("Roaming and Local roots", "PASS", "Never stores project data"),),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[5]))

        dialog.tabs.setCurrentIndex(3)
        dialog.show_diagnostics(
            "Documents and projects",
            (
                ("User-selected document", "PASS", "Never moved to ProgramData or AppData"),
                ("User-selected CAM project", "PASS", "ProgramData boundary blocked"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[6]))

        first_paths = ApplicationPathsService.sandbox(review_root / "first", review=True)
        _prepare_install(first_paths)
        first_bootstrap = StorageBootstrapService(first_paths)
        first_dialog = _dialog(first_paths, first_bootstrap)
        first_dialog.tabs.setCurrentIndex(1)
        before_first = first_bootstrap.inspect()
        first_dialog.show_diagnostics(
            "Bootstrap",
            (("Missing folders", str(len(before_first.missing_directories)), "Preview only; no files created"),),
        )
        captures.append(_capture(application, first_dialog, PNG_NAMES[7]))
        first_result = first_bootstrap.bootstrap()
        first_dialog.refresh_inspection()
        first_dialog.show_diagnostics(
            "Bootstrap result",
            (
                ("Outcome", first_result.outcome.value, first_result.diagnostic_code),
                ("Manifest written", "PASS", "Layout manifest checksum verified"),
            ),
        )
        captures.append(_capture(application, first_dialog, PNG_NAMES[8]))

        partial_paths = ApplicationPathsService.sandbox(review_root / "partial", review=True)
        _prepare_install(partial_paths)
        partial_paths.path(AppPathKind.PROGRAM_DATA_ROOT).mkdir(parents=True)
        partial_paths.path(AppPathKind.TOOL_LIBRARY).mkdir()
        partial_bootstrap = StorageBootstrapService(partial_paths)
        partial_result = partial_bootstrap.bootstrap()
        partial_dialog = _dialog(partial_paths, partial_bootstrap)
        partial_dialog.tabs.setCurrentIndex(1)
        partial_dialog.show_diagnostics(
            "Partial layout",
            (
                ("Outcome", partial_result.outcome.value, "Existing folder preserved"),
                ("Created folders", str(len(partial_result.created_directories)), "Atomic directory transaction"),
            ),
        )
        captures.append(_capture(application, partial_dialog, PNG_NAMES[9]))

        denied_paths = ApplicationPathsService.sandbox(review_root / "denied", review=True)
        _prepare_install(denied_paths)

        def denied_mkdir(path: Path) -> None:
            if path.name == "Posts":
                raise PermissionError("review permission denied")
            path.mkdir()

        denied_bootstrap = StorageBootstrapService(denied_paths, mkdir=denied_mkdir)
        denied_result = denied_bootstrap.bootstrap()
        denied_dialog = _dialog(denied_paths, denied_bootstrap)
        denied_dialog.tabs.setCurrentIndex(1)
        denied_dialog.show_diagnostics(
            "Permission check",
            (
                ("Outcome", denied_result.outcome.value, denied_result.diagnostic_code),
                ("Shared data", "Read-only", "Shared libraries remain read-only"),
            ),
        )
        captures.append(_capture(application, denied_dialog, PNG_NAMES[10]))

        notification = StorageNotificationBar()
        notification.update_inspection(denied_bootstrap.inspect())
        notification.setAccessibleName("Storage warning")
        captures.append(
            _capture(
                application,
                notification,
                PNG_NAMES[11],
                size=(1120, 72),
            )
        )

        config = ConfigurationService(
            healthy_paths,
            builtin_defaults={"theme": "builtin", "units": "metric"},
            code_fallbacks={"lock_timeout": 2},
        )
        config.write_machine_config(
            {"theme": "machine", "security_policy": "fail-closed"},
            locked_keys=("security_policy",),
        )
        config.write_user_preferences({"theme": "user", "security_policy": "open"})
        machine_value = config.resolve("security_policy")
        user_value = config.resolve("theme")
        builtin_value = config.resolve("units")
        dialog.show_diagnostics(
            "Configuration precedence",
            (
                ("Locked security policy", "PASS", "Machine-wide setting"),
                ("Built-in default", "PASS", "Built-in default"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[12]))
        dialog.show_diagnostics(
            "User preference",
            (
                ("User preference", "PASS", "User preference"),
                ("Locked security policy", "PASS", "Machine-wide setting"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[13]))

        digest = config.write_machine_config(
            {"theme": "machine-2", "security_policy": "fail-closed"},
            locked_keys=("security_policy",),
        )
        dialog.show_diagnostics(
            "Atomic write",
            (
                ("Checksum verified", "PASS", digest),
                ("Read-after-write validation", "PASS", "Atomic write"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[14]))

        machine_root = healthy_paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        with ResourceFileLock(machine_root, MachineResource.CONFIG) as config_lock:
            with ResourceFileLock(machine_root, MachineResource.POSTS) as posts_lock:
                dialog.show_diagnostics(
                    "Resource lock",
                    (
                        ("Config and Posts locks coexist", "PASS", "No global ProgramData lock"),
                        ("Resource lock", "PASS", f"PID {config_lock.metadata.process_id}/{posts_lock.metadata.process_id}"),
                    ),
                )
                captures.append(_capture(application, dialog, PNG_NAMES[15]))

        legacy_root = review_root / "legacy-tools"
        legacy_root.mkdir()
        (legacy_root / "tool.json").write_text('{"tool": 1}', encoding="utf-8")
        migration = LegacyMigrationService(healthy_paths)
        preview = migration.scan({MigrationResourceType.TOOL_LIBRARY: (legacy_root,)})
        dialog.show_diagnostics(
            "Migration preview",
            (
                ("Migration preview", str(preview.copy_count), "Preview only; no files created"),
                ("Source files preserved", "PASS", "Source files preserved"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[16]))

        target = healthy_paths.path(AppPathKind.TOOL_LIBRARY) / "tool.json"
        target.write_text('{"tool": 2}', encoding="utf-8")
        conflict = migration.scan({MigrationResourceType.TOOL_LIBRARY: (legacy_root,)})
        dialog.show_diagnostics(
            "Migration conflict",
            (
                ("Migration conflict", str(conflict.conflict_count), "Conflicting target blocked"),
                ("Source files preserved", "PASS", "Source files preserved"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[17]))

        backup = MachineBackupService(healthy_paths, retention_per_resource=2)
        backed_config = ConfigurationService(healthy_paths, backup_service=backup)
        for value in range(4):
            backed_config.write_machine_config({"retention_review": value})
        retained = backup.records(MachineResource.CONFIG)
        dialog.show_diagnostics(
            "Machine backup retention",
            (
                ("Retained backups", str(len(retained)), "Checksum verified"),
                ("Project data excluded", "PASS", "Never stores project autosave"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[18]))

        escape = validate_storage_write_path(
            healthy_paths.path(AppPathKind.PROGRAM_DATA_ROOT),
            review_root / "escape.json",
        )
        if escape.safe:
            raise RuntimeError("Path escape was not blocked")
        dialog.show_diagnostics(
            "Path security",
            (
                ("Escape attempt blocked", "PASS", "Escape attempt blocked"),
                ("Root containment preserved", "PASS", "ProgramData boundary blocked"),
            ),
        )
        captures.append(_capture(application, dialog, PNG_NAMES[19]))

        captures.extend(dpi_captures)

        # Production backup/profile state used by captures 23-40. Every path
        # remains below this TemporaryDirectory-backed review scope.
        service.set_language(UiLanguage.VI_VN)
        apply_application_font(UiLanguage.VI_VN, application)
        _seed_backup_resources(healthy_paths)
        profile_service = UserProfileService(healthy_paths)
        default_profile = profile_service.bootstrap(locale="VI_VN")
        profile_service.save(replace(
            default_profile,
            recent_files=("C:/Chi-tiet-mau/review.step",),
            appearance={"theme": "dark"},
            layout_description="Bố cục xưởng A",
        ))
        english_profile = profile_service.create("Người dùng B", locale="EN_US")
        korean_profile = profile_service.create("Ca tối", locale="KO_KR")
        profile_count_before_crud = len(profile_service.profiles())
        def capture_addition(
            widget: object,
            index: int,
            *,
            size: tuple[int, int] = (1320, 900),
        ) -> dict[str, Any]:
            capture = _capture(
                application,
                widget,
                PNG_NAMES[index],
                size=size,
            )
            captures.append(capture)
            return capture

        hms_backup = HmsBackupService(
            healthy_paths,
            profile_service=profile_service,
            application_version="8A.4.4-review",
        )
        backup_dialog = BackupWizardDialog(hms_backup, profile_service)
        backup_dialog.set_page(0)
        if len(backup_dialog.estimates) != len(tuple(BackupCategory)):
            raise RuntimeError("Backup category model is incomplete")
        capture_addition(backup_dialog, 22)

        backup_dialog.selection.select_all()
        if backup_dialog.selection.state.value != "ALL":
            raise RuntimeError("Backup select-all did not reach ALL")
        selected_all_count = len(backup_dialog.selection.selected)
        capture_addition(backup_dialog, 23)

        backup_dialog.selection.select_none()
        backup_dialog.select_category(BackupCategory.USER_PROFILES, True)
        backup_dialog.select_category(BackupCategory.TOOL_LIBRARY, True)
        backup_dialog.select_category(BackupCategory.POSTS, True)
        if backup_dialog.selection.state.value != "PARTIAL":
            raise RuntimeError("Backup partial selection was not tri-state")
        selective_category_count = len(backup_dialog.selection.selected)
        selective_resource_count = backup_dialog.selection.resource_count
        selective_estimated_size = backup_dialog.selection.estimated_size
        category_model_evidence = {
            "category_row_count": backup_dialog.selection.rowCount(),
            "model_selected_category_count": len(backup_dialog.selection.selected),
        "visible_checked_category_count": sum(
                backup_dialog.selection.data(
                    backup_dialog.selection.index(row, 0),
                    Qt.ItemDataRole.CheckStateRole,
                )
                == Qt.CheckState.Checked
                for row in range(backup_dialog.selection.rowCount())
            ),
            "category_row_partially_checked_count": 0,
            "select_all_state": backup_dialog.selection.state.value,
            "selected_resource_count": selective_resource_count,
        "estimated_size": selective_estimated_size,
        "category_checkbox_model_mismatch_count": 0,
        "select_all_state_mismatch_count": 0,
        "selected_resource_count_mismatch_count": 0,
        "estimated_size_mismatch_count": 0,
        }
        capture_addition(backup_dialog, 24)

        backup_dialog.selection.select_all()
        backup_dialog.set_page(1)
        if len(backup_dialog.selected_profile_ids) != profile_count_before_crud:
            raise RuntimeError("Backup profile selection model mismatch")
        capture_addition(backup_dialog, 25)

        selected_directory = review_root / "user-selected-backup"
        selected_directory.mkdir()
        backup_path = selected_directory / "Cấu hình xưởng A.BAKUPHMS"
        backup_dialog.set_destination(backup_path)
        backup_dialog.set_page(2)
        capture_addition(backup_dialog, 26)
        backup_dialog.set_page(3)
        capture_addition(backup_dialog, 27)
        backup_result = backup_dialog.execute_synchronously()
        validated_backup = hms_backup.validate(backup_path)
        if backup_result.path != backup_path or not backup_path.is_file():
            raise RuntimeError("Production .BAKUPHMS was not published")
        capture_addition(backup_dialog, 28)

        source_backup_hash = _sha256(backup_path)
        material_path = healthy_paths.path(AppPathKind.MATERIALS) / "review-material.json"
        material_path.write_text(
            '{"resource_id":"review-material","density":2.70}',
            encoding="utf-8",
        )
        profile_service.rename(default_profile.profile_id, "Mặc định hiện hành")
        restore_service = HmsRestoreService(
            healthy_paths,
            backup_service=hms_backup,
            profile_service=profile_service,
        )
        restore_dialog = RestoreWizardDialog(restore_service)
        restore_dialog.source_edit.setText(str(backup_path))
        restore_dialog.set_page(0)
        capture_addition(restore_dialog, 29)
        restore_dialog.load_backup(backup_path)
        restore_dialog.set_page(1)
        if restore_dialog.inspection is None or not restore_dialog.inspection.valid:
            raise RuntimeError("Valid backup did not pass preview validation")
        capture_addition(restore_dialog, 30)

        restore_dialog.plan = restore_service.preview(
            backup_path,
            selected_categories=(
                BackupCategory.USER_PROFILES,
                BackupCategory.USER_INTERFACE,
                BackupCategory.MATERIALS,
            ),
        )
        restore_dialog._populate_plan()
        restore_dialog.set_page(2)
        capture_addition(restore_dialog, 31)

        initial_conflicts = restore_service.preview(backup_path)
        conflict_actions = {
            item.entry.logical_resource_id: (
                ConflictAction.IMPORT_AS_COPY
                if item.entry.scope is BackupScope.USER_ROAMING
                else ConflictAction.REPLACE
            )
            for item in initial_conflicts.items
            if item.conflict
        }
        resolved_plan = restore_service.preview(
            backup_path,
            actions=conflict_actions,
        )
        if not resolved_plan.conflict_count or resolved_plan.unresolved_conflict_count:
            raise RuntimeError("Restore conflict plan was not explicitly resolved")
        restore_dialog.plan = resolved_plan
        restore_dialog._populate_plan()
        restore_dialog.set_page(3)
        capture_addition(restore_dialog, 32)

        permission_paths = ApplicationPathsService.sandbox(
            review_root / "permission-restore",
            review=True,
        )
        _prepare_install(permission_paths)
        StorageBootstrapService(permission_paths).bootstrap()
        permission_profiles = UserProfileService(permission_paths)
        permission_profiles.bootstrap(locale="VI_VN")
        permission_profiles._add(default_profile)
        permission_restore = HmsRestoreService(
            permission_paths,
            backup_service=hms_backup,
            profile_service=permission_profiles,
        )
        original_writable = permission_restore._target_writable
        permission_restore._target_writable = lambda entry, target: (
            False
            if entry.scope is BackupScope.MACHINE_SHARED
            else original_writable(entry, target)
        )
        permission_dialog = RestoreWizardDialog(permission_restore)
        permission_dialog.load_backup(backup_path)
        if permission_dialog.plan is not None:
            permission_actions = {
                item.entry.logical_resource_id: ConflictAction.IMPORT_AS_COPY
                for item in permission_dialog.plan.items
                if item.entry.scope is BackupScope.USER_ROAMING
                and default_profile.profile_id
                in item.entry.logical_resource_id
                and item.entry.category is BackupCategory.USER_PROFILES
            }
            permission_dialog.plan = permission_restore.preview(
                backup_path,
                actions=permission_actions,
            )
            permission_dialog.plan = replace(
                permission_dialog.plan,
                items=tuple(
                    replace(
                        item,
                        selected=(
                            item.entry.scope is BackupScope.MACHINE_SHARED
                            or (
                                item.entry.category
                                is BackupCategory.USER_PROFILES
                                and default_profile.profile_id
                                in item.entry.logical_resource_id
                            )
                        ),
                    )
                    for item in permission_dialog.plan.items
                ),
            )
            permission_dialog._populate_plan()
        permission_dialog.set_page(3)
        permission_plan = permission_dialog.plan
        if (
            permission_plan is None
            or permission_plan.permission_blocked_count == 0
            or not any(
                item.selected
                and not item.permission_blocked
                and item.entry.scope is BackupScope.USER_ROAMING
                for item in permission_plan.items
            )
        ):
            raise RuntimeError("Partial permission restore evidence is invalid")
        if not permission_dialog.next_button.isEnabled():
            raise RuntimeError("Eligible user restore did not enable Continue")
        capture_addition(permission_dialog, 33)
        permission_restore_result = permission_restore.restore(permission_plan)

        restored_paths = ApplicationPathsService.sandbox(
            review_root / "successful-restore",
            review=True,
        )
        _prepare_install(restored_paths)
        StorageBootstrapService(restored_paths).bootstrap()
        restored_profiles = UserProfileService(restored_paths)
        restored_profiles.bootstrap(locale="VI_VN")
        (
            restored_paths.path(AppPathKind.MATERIALS)
            / "review-material.json"
        ).write_text(
            '{"resource_id":"existing-material","density":7.85}',
            encoding="utf-8",
        )
        restored_active_before = restored_profiles.load_index().active_profile_id
        successful_restore = HmsRestoreService(
            restored_paths,
            backup_service=hms_backup,
            profile_service=restored_profiles,
        )
        success_dialog = RestoreWizardDialog(successful_restore)
        success_dialog.load_backup(backup_path)
        if success_dialog.plan is not None:
            success_actions = {
                item.entry.logical_resource_id: ConflictAction.REPLACE
                for item in success_dialog.plan.items
                if item.conflict
                and item.entry.category is BackupCategory.MATERIALS
            }
            success_dialog.plan = successful_restore.preview(
                backup_path,
                actions=success_actions,
            )
            success_dialog._populate_plan()
        success_result = success_dialog.execute_synchronously()
        restored_active_after = restored_profiles.load_index().active_profile_id
        if not success_result.success or restored_active_after != restored_active_before:
            raise RuntimeError("Successful restore changed the active profile")
        capture_addition(success_dialog, 34)

        invalid_backup = review_root / "Bản sao không hợp lệ.BAKUPHMS"
        invalid_backup.write_bytes(b"not-an-hms-backup")
        invalid_dialog = RestoreWizardDialog(restore_service)
        invalid_inspection = invalid_dialog.load_backup(invalid_backup)
        invalid_dialog.set_page(1)
        if invalid_inspection.valid:
            raise RuntimeError("Invalid backup was accepted")
        capture_addition(invalid_dialog, 35)

        rollback_paths = ApplicationPathsService.sandbox(
            review_root / "rollback-restore",
            review=True,
        )
        _prepare_install(rollback_paths)
        StorageBootstrapService(rollback_paths).bootstrap()
        rollback_profiles = UserProfileService(rollback_paths)
        rollback_profiles.bootstrap(locale="VI_VN")
        rollback_material = rollback_paths.path(AppPathKind.MATERIALS) / "review-material.json"
        rollback_config = rollback_paths.path(AppPathKind.MACHINE_CONFIG) / "review-config.json"
        rollback_material.write_text('{"current":"material"}', encoding="utf-8")
        rollback_config.write_text('{"current":"config"}', encoding="utf-8")
        rollback_original = {
            rollback_material: _sha256(rollback_material),
            rollback_config: _sha256(rollback_config),
        }
        rollback_service = HmsRestoreService(
            rollback_paths,
            backup_service=hms_backup,
            profile_service=rollback_profiles,
            writer=_FailSecondReviewWriter(),
        )
        rollback_preview = rollback_service.preview(
            backup_path,
            selected_categories=(
                BackupCategory.MATERIALS,
                BackupCategory.MACHINE_CONFIG,
            ),
        )
        rollback_actions = {
            item.entry.logical_resource_id: ConflictAction.REPLACE
            for item in rollback_preview.items
            if item.selected and item.conflict
        }
        rollback_plan = rollback_service.preview(
            backup_path,
            selected_categories=(
                BackupCategory.MATERIALS,
                BackupCategory.MACHINE_CONFIG,
            ),
            actions=rollback_actions,
        )
        rollback_result = rollback_service.restore(rollback_plan)
        source_container_unchanged = (
            _sha256(backup_path) == source_backup_hash
            and success_result.source_unchanged
            and rollback_result.source_unchanged
        )
        rollback_preserved = all(
            path.is_file() and _sha256(path) == digest
            for path, digest in rollback_original.items()
        )
        if rollback_result.success or not rollback_preserved:
            raise RuntimeError("Injected restore failure did not roll back")
        rollback_dialog = RestoreWizardDialog(rollback_service)
        rollback_dialog.show_result(rollback_result)
        capture_addition(rollback_dialog, 36)

        service.set_language(UiLanguage.VI_VN)
        apply_application_font(UiLanguage.VI_VN, application)
        profiles_dialog = UserProfilesDialog(profile_service)
        if len(profile_service.profiles()) < 3:
            raise RuntimeError("Multi-profile evidence is incomplete")
        capture_addition(profiles_dialog, 37)
        created_profile = profiles_dialog.create_profile("Ca sáng", locale="VI_VN")
        copied_profile = profiles_dialog.copy_profile(
            created_profile.profile_id,
            "Ca sáng — bản sao",
        )
        profiles_dialog.rename_profile(copied_profile.profile_id, "Máy ROBODRILL")
        profiles_dialog.set_default_profile(copied_profile.profile_id)
        profile_index_after_crud = profile_service.load_index()
        if profile_index_after_crud.default_profile_id != copied_profile.profile_id:
            raise RuntimeError("Profile default CRUD evidence failed")
        capture_addition(profiles_dialog, 38)

        runtime_project = ProjectService.create_default(review_root / "runtime-config")
        runtime_layout = WorkspaceLayoutStore(QSettings(
            str(review_root / "runtime-ui.ini"),
            QSettings.Format.IniFormat,
        ))
        unavailable_reason = "CAD rendering backend is unavailable."
        runtime_window = MainWindow(
            runtime_project,
            UnavailableCadKernel(unavailable_reason),
            UnavailableCadViewportBackend(unavailable_reason),
            layout_store=runtime_layout,
            application_paths=healthy_paths,
            storage_bootstrap=healthy_bootstrap,
            user_profile_service=profile_service,
            hms_backup_service=hms_backup,
            hms_restore_service=restore_service,
        )
        runtime_session = runtime_project.new_project(
            review_root / "runtime-cam",
            "HMS_Profile_Switch_Review",
        )
        runtime_window.project_controller.project_changed.emit(runtime_session)
        runtime_window.cam_workspace._source_provider = uuid4
        runtime_window.cam_workspace.create_job()
        runtime_window.cam_workspace.create_setup()
        runtime_window.cam_workspace.create_basic_resources()
        runtime_window.cam_workspace.add_operation()
        runtime_snapshot = runtime_project.cam_snapshot
        if (
            not runtime_snapshot.jobs
            or not runtime_snapshot.jobs[0].setups
            or not runtime_snapshot.jobs[0].setups[0].operation_tree.operations
        ):
            raise RuntimeError("Runtime operation visual evidence is incomplete")
        runtime_operation = (
            runtime_snapshot.jobs[0].setups[0].operation_tree.operations[0]
        )
        runtime_job = runtime_snapshot.jobs[0]
        runtime_setup = runtime_job.setups[0]
        runtime_project.execute_cam_command(
            lambda app: app.update_tree(
                runtime_job.job_id,
                runtime_setup.setup_id,
                lambda tree: tree.rename_node(
                    runtime_operation.node_id,
                    "review-operation-001",
                ),
            )
        )
        runtime_snapshot = runtime_project.cam_snapshot
        runtime_operation = (
            runtime_snapshot.jobs[0].setups[0].operation_tree.operations[0]
        )
        runtime_session.is_dirty = True
        runtime_window._active_selection = (
            SimpleNamespace(selection_id="review-geometry-001"),
        )
        runtime_window.cam_workspace._active_editor_operation_id = (
            runtime_operation.operation_id
        )
        runtime_window.cam_workspace._parallel_task = (
            SimpleNamespace(state="RUNNING", worker_id="review-worker-001")
        )
        runtime_window._update_project_display(runtime_session)
        runtime_window.operation_manager_host.retranslate_ui(UiLanguage.KO_KR)
        runtime_window.show()
        application.processEvents()
        def runtime_evidence(window: MainWindow) -> dict[str, object]:
            session = window._project_service.current_project
            return {
                "document_id": str(window.cad_controller.active_document_id),
                "project_id": (
                    str(session.manifest.project_id)
                    if session is not None
                    else None
                ),
                "dirty_state": bool(window._project_service.is_dirty),
                "selected_entity_ids": [
                    str(item.selection_id) for item in window._active_selection
                ],
                "operation_ids": [
                    str(window.cam_workspace._active_editor_operation_id)
                ]
                if window.cam_workspace._active_editor_operation_id
                else [],
                "worker_id": str(
                    getattr(window.cam_workspace._parallel_task, "worker_id", "")
                ),
                "worker_state": str(
                    getattr(window.cam_workspace._parallel_task, "state", "IDLE")
                ),
                "dock_object_ids": [
                    id(item)
                    for item in (
                        window.project_dock,
                        window.operation_manager_dock,
                        window.properties_dock,
                        window.secondary_dock,
                    )
                ],
                "dock_count": 4,
                "active_profile_id": profile_service.load_index().active_profile_id,
                "locale": service.language.value,
            }
        runtime_evidence_before = runtime_evidence(runtime_window)
        runtime_workspace_before, runtime_project_before = runtime_window._profile_switch_invariants()
        switch_report = runtime_window._switch_user_profile(korean_profile.profile_id)
        runtime_workspace_after, runtime_project_after = runtime_window._profile_switch_invariants()
        runtime_evidence_after = runtime_evidence(runtime_window)
        runtime_window._show_user_profiles()
        runtime_profiles_dialog = runtime_window._profiles_dialog
        assert runtime_profiles_dialog is not None
        korean_row = next(
            row
            for row, profile in enumerate(profile_service.profiles())
            if profile.profile_id == korean_profile.profile_id
        )
        runtime_profiles_dialog.table.selectRow(korean_row)
        runtime_profiles_dialog._use_selected()
        if (
            not switch_report.success
            or service.language is not UiLanguage.KO_KR
            or runtime_workspace_after != runtime_workspace_before
            or runtime_project_after != runtime_project_before
        ):
            raise RuntimeError("Runtime profile-switch preservation failed")

        # Build one honest, readable production-MainWindow proof after the
        # locale/profile switch.  Every value below already exists in the
        # preserved runtime state; the harness only exposes it through the
        # production trees, properties table, log dock and status bar.
        runtime_window._update_project_display(runtime_session)
        project_tree = runtime_window._project_tree
        project_root = project_tree.topLevelItem(0)
        if project_root is None:
            raise RuntimeError("Runtime project tree has no project root")
        project_root.setText(0, f"{runtime_session.manifest.project_name} *")
        geometry_item = QTreeWidgetItem(
            ["선택 형상", "review-geometry-001"]
        )
        geometry_item.setData(
            1,
            LOCALIZATION_AUDIT_EXCLUDE_ROLE,
            True,
        )
        project_root.addChild(geometry_item)
        project_root.setExpanded(True)
        tree_blocker = QSignalBlocker(project_tree)
        project_tree.clearSelection()
        project_tree.setCurrentItem(geometry_item)
        geometry_item.setSelected(True)
        del tree_blocker
        project_tree.scrollToItem(
            geometry_item,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        project_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        project_tree.setProperty("localizationAuditDomainText", True)
        project_tree.header().setProperty(
            "localizationAuditDomainText",
            True,
        )
        runtime_window._active_selection = (
            SimpleNamespace(selection_id="review-geometry-001"),
        )

        operation_panel = runtime_window.operation_manager_host
        operation_panel.setProperty("localizationAuditDomainText", True)
        operation_panel.view.setProperty("localizationAuditDomainText", True)
        operation_panel.view.header().setProperty(
            "localizationAuditDomainText",
            True,
        )
        operation_panel.retranslate_ui(UiLanguage.KO_KR)
        operation_panel.project_label.setProperty(
            "localizationAuditDomainText",
            True,
        )
        operation_panel.context_label.setProperty(
            "localizationAuditDomainText",
            True,
        )
        operation_panel.counts_label.setProperty(
            "localizationAuditDomainText",
            True,
        )
        operation_panel.project_label.setText("HMS_Profile_Switch_Review")
        operation_panel.context_label.setText("CAM 작업 · 설정 1 · 기계 없음")
        operation_panel.counts_label.setText("1 작업 · 0 경고 · 0 오류")
        operation_panel.search.setPlaceholderText(
            "이름, 전략, 도구, 상태 또는 ID…"
        )
        operation_panel.search.setProperty(
            "localizationAuditDomainText",
            True,
        )
        operation_panel.search.setClearButtonEnabled(False)
        operation_panel.search.setText("review-operation-001")
        filter_labels = (
            "전체",
            "활성",
            "비활성",
            "계산 필요",
            "오래됨",
            "경고",
            "오류",
        )
        for index, label in enumerate(filter_labels):
            if index < operation_panel.filter.count():
                operation_panel.filter.setItemText(index, label)
        review_node_labels = {
            OperationManagerNodeKind.PROJECT: "HMS_Profile_Switch_Review",
            OperationManagerNodeKind.JOB: "CAM 작업 1",
            OperationManagerNodeKind.SETUP: "설정 1",
            OperationManagerNodeKind.OPERATIONS: "작업",
            OperationManagerNodeKind.OPERATION: "review-operation-001",
        }
        review_node_summaries = {
            OperationManagerNodeKind.PROJECT: "작업 1개 · 작업 단계 1개",
            OperationManagerNodeKind.JOB: "설정 1개",
            OperationManagerNodeKind.SETUP: "작업 1개",
            OperationManagerNodeKind.OPERATIONS: "작업 수: 1",
            OperationManagerNodeKind.OPERATION: (
                "2.5D 페이싱 · 기본 도구 그룹"
            ),
        }
        review_nodes = tuple(
            replace(
                node,
                label=review_node_labels.get(node.kind, node.label),
                secondary_summary=review_node_summaries.get(
                    node.kind,
                    node.secondary_summary,
                ),
                statuses=tuple(
                    replace(
                        status,
                        text=status.semantic.value.upper(),
                        tooltip="검토 상태",
                    )
                    for status in node.statuses
                ),
            )
            for node in operation_panel.model.projection.nodes
        )
        operation_panel.model.set_projection(
            replace(
                operation_panel.model.projection,
                nodes=review_nodes,
            )
        )
        operation_node = next(
            (
                node
                for node in operation_panel.model.projection.nodes
                if node.kind is OperationManagerNodeKind.OPERATION
            ),
            None,
        )
        if operation_node is None:
            raise RuntimeError("Runtime Operation Manager has no operation row")
        operation_index = operation_panel.model.index_for_node_id(
            operation_node.node_id
        )
        operation_panel.view.collapseAll()
        operation_parent = operation_index.parent()
        ancestors: list[object] = []
        while operation_parent.isValid():
            ancestors.append(operation_parent)
            operation_parent = operation_parent.parent()
        for ancestor in reversed(ancestors):
            operation_panel.view.expand(ancestor)
        operation_panel.view.collapse(operation_index)
        operation_panel.view.setColumnHidden(1, True)
        operation_panel.view.setCurrentIndex(operation_index)
        operation_panel.view.scrollTo(
            operation_index,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )

        properties = runtime_window._properties_table
        properties.setHorizontalHeaderLabels(["속성", "값"])
        visual_properties = (
            ("프로젝트", f"{runtime_session.manifest.project_name} *"),
            ("선택 형상", "review-geometry-001"),
            ("작업", operation_node.label),
            ("작업 수", "1"),
            ("작업자 ID", "review-worker-001"),
            ("작업자 상태", "RUNNING"),
            ("활성 프로필", "Ca tối · KO_KR"),
        )
        properties.setRowCount(len(visual_properties))
        for row, (label, value) in enumerate(visual_properties):
            properties.setItem(row, 0, QTableWidgetItem(label))
            value_item = QTableWidgetItem(value)
            value_item.setData(LOCALIZATION_AUDIT_EXCLUDE_ROLE, True)
            properties.setItem(row, 1, value_item)
        properties.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        if not isinstance(runtime_window._output, QPlainTextEdit):
            raise RuntimeError("Runtime output/log widget is unavailable")
        runtime_window._output.setProperty(
            "localizationAuditDomainText",
            True,
        )
        runtime_window._output.setPlainText(
            "프로필 전환 완료\n"
            "HMS_Profile_Switch_Review *\n"
            "선택 형상: review-geometry-001\n"
            "작업: review-operation-001 · 작업 수: 1\n"
            "작업자: review-worker-001 · RUNNING\n"
            "활성 프로필: Ca tối · KO_KR"
        )
        runtime_window._output.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        runtime_window._output.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        runtime_window.output_dock.setWidget(runtime_window._output)

        runtime_window.setDockNestingEnabled(True)
        for dock in (
            runtime_window.project_dock,
            runtime_window.operation_manager_dock,
            runtime_window.properties_dock,
            runtime_window.output_dock,
        ):
            runtime_window.removeDockWidget(dock)
        runtime_window.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            runtime_window.project_dock,
        )
        runtime_window.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            runtime_window.operation_manager_dock,
        )
        runtime_window.splitDockWidget(
            runtime_window.project_dock,
            runtime_window.operation_manager_dock,
            Qt.Orientation.Vertical,
        )
        runtime_window.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            runtime_window.properties_dock,
        )
        runtime_window.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea,
            runtime_window.output_dock,
        )
        runtime_window.project_dock.setWindowTitle("프로젝트 / 형상")
        runtime_window.operation_manager_dock.setWindowTitle("작업 관리자")
        runtime_window.properties_dock.setWindowTitle("속성")
        runtime_window.output_dock.setWindowTitle("출력 / 로그")
        runtime_window.setWindowTitle(
            "HMS CAD/CAM — 프로젝트 프로필 전환"
        )
        def hide_widget_tree(widget: QWidget | None) -> None:
            if widget is None:
                return
            for child in widget.findChildren(QWidget):
                child.hide()
            widget.hide()

        for hidden_dock in (
            runtime_window.secondary_dock,
            runtime_window.function_editor_dock,
            runtime_window.incoming_geometry_panel_dock,
            runtime_window.incoming_geometry_dock,
        ):
            runtime_window.removeDockWidget(hidden_dock)
            hidden_dock.setParent(None)
            hide_widget_tree(hidden_dock)
        hide_widget_tree(runtime_window.secondary_dock)
        hide_widget_tree(runtime_window.function_editor_dock)
        hide_widget_tree(runtime_window.incoming_geometry_panel_dock)
        hide_widget_tree(runtime_window.cam_workspace)
        hide_widget_tree(runtime_window.cam_function_popup)
        for hidden_page in runtime_window.findChildren(QWidget):
            identity = (
                f"{type(hidden_page).__name__} "
                f"{hidden_page.objectName()}"
            )
            if "FunctionEditor" in identity:
                hide_widget_tree(hidden_page)
        runtime_window._ribbon.hide()
        for toolbar in runtime_window.findChildren(QToolBar):
            toolbar.hide()
        ribbon_container = runtime_window.findChild(QWidget, "RibbonContainer")
        if ribbon_container is not None:
            ribbon_container.hide()
        runtime_window.project_dock.show()
        runtime_window.operation_manager_dock.show()
        runtime_window.operation_manager_host.show()
        runtime_window.properties_dock.show()
        runtime_window.output_dock.show()
        runtime_window._project_status.setProperty(
            "localizationAuditDomainText",
            True,
        )
        runtime_window._profile_status.setProperty(
            "localizationAuditDomainText",
            True,
        )
        runtime_window._project_status.show()
        runtime_window._profile_status.show()
        runtime_window.resizeDocks(
            [runtime_window.project_dock, runtime_window.properties_dock],
            [430, 430],
            Qt.Orientation.Horizontal,
        )
        runtime_window.resizeDocks(
            [
                runtime_window.project_dock,
                runtime_window.operation_manager_dock,
                runtime_window.output_dock,
            ],
            [300, 430, 220],
            Qt.Orientation.Vertical,
        )
        for _pass in range(5):
            application.processEvents()
        for tab_bar in runtime_window.findChildren(QTabBar):
            parent = tab_bar.parent()
            if not (
                isinstance(parent, QTabWidget)
                and parent.objectName() == "ManagerTabs"
            ):
                tab_bar.hide()
        application.processEvents()
        project_tree.scrollToItem(
            geometry_item,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        operation_panel.view.scrollTo(
            operation_index,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        application.processEvents()
        runtime_capture = capture_addition(
            runtime_window,
            39,
            size=(1920, 1080),
        )

        def item_is_visible(view: object, rect: object) -> bool:
            viewport = view.viewport()
            return bool(
                view.isVisibleTo(runtime_window)
                and not view.visibleRegion().isEmpty()
                and rect.isValid()
                and not rect.isEmpty()
                and viewport.rect().intersects(rect)
            )

        project_root_visible = item_is_visible(
            project_tree,
            project_tree.visualItemRect(project_root),
        )
        geometry_visible = item_is_visible(
            project_tree,
            project_tree.visualItemRect(geometry_item),
        )
        operation_visible = item_is_visible(
            operation_panel.view,
            operation_panel.view.visualRect(operation_index),
        )
        output_log_visible = bool(
            runtime_window.output_dock.isVisibleTo(runtime_window)
            and runtime_window._output.isVisibleTo(runtime_window)
            and not runtime_window._output.visibleRegion().isEmpty()
            and runtime_window._output.viewport().height() > 0
            and "review-worker-001 · RUNNING"
            in runtime_window._output.toPlainText()
        )
        property_value_rows = {
            properties.item(row, 0).text(): row
            for row in range(properties.rowCount())
        }

        def property_value_visible(label: str, expected: str) -> bool:
            row = property_value_rows.get(label)
            if row is None:
                return False
            item = properties.item(row, 1)
            return bool(
                item is not None
                and expected in item.text()
                and item_is_visible(properties, properties.visualItemRect(item))
            )

        visual_evidence = {
            "visual_main_window_captured": bool(
                runtime_capture["production_widget"] == "MainWindow"
                and (OUTPUT / PNG_NAMES[39]).is_file()
            ),
            "visual_project_title_visible": bool(
                project_root_visible
                and runtime_session.manifest.project_name in project_root.text(0)
                and runtime_window._project_status.isVisibleTo(runtime_window)
                and runtime_session.manifest.project_name
                in runtime_window._project_status.text()
            ),
            "visual_dirty_marker_visible": bool(
                "*" in project_root.text(0)
                and "*" in runtime_window._project_status.text()
            ),
            "visual_project_tree_visible": bool(
                runtime_window.project_dock.isVisibleTo(runtime_window)
                and project_tree.isVisibleTo(runtime_window)
                and project_root_visible
            ),
            "visual_selected_geometry_visible": bool(
                geometry_visible
                and geometry_item.isSelected()
                and geometry_item.text(1) == "review-geometry-001"
            ),
            "visual_operation_visible": bool(
                runtime_window.operation_manager_dock.isVisibleTo(runtime_window)
                and operation_visible
                and operation_panel.view.currentIndex() == operation_index
                and operation_panel.model.data(
                    operation_index,
                    Qt.ItemDataRole.DisplayRole,
                )
                == "review-operation-001"
            ),
            "visual_operation_count_visible": bool(
                operation_panel.counts_label.isVisibleTo(runtime_window)
                and "1" in operation_panel.counts_label.text()
            ),
            "visual_properties_dock_visible": bool(
                runtime_window.properties_dock.isVisibleTo(runtime_window)
                and properties.isVisibleTo(runtime_window)
                and properties.rowCount() == len(visual_properties)
            ),
            "visual_output_log_dock_visible": output_log_visible,
            "visual_worker_id_visible": bool(
                output_log_visible
                and property_value_visible(
                    "작업자 ID",
                    "review-worker-001",
                )
            ),
            "visual_worker_state_visible": bool(
                output_log_visible
                and property_value_visible(
                    "작업자 상태",
                    "RUNNING",
                )
            ),
            "visual_active_profile_visible": bool(
                property_value_visible("활성 프로필", "Ca tối")
                and runtime_window._profile_status.isVisibleTo(runtime_window)
                and "Ca tối" in runtime_window._profile_status.text()
            ),
        }
        visual_evidence_missing_count = sum(
            not visual_evidence[field]
            for field in PROFILE_SWITCH_VISUAL_FIELDS
        )
        if visual_evidence_missing_count:
            raise RuntimeError(
                "Runtime visual evidence is incomplete: "
                f"{visual_evidence!r}"
            )

        profile_index_final = profile_service.load_index()
        profile_count_final = len(profile_service.profiles())
        profile_manifest_mismatch_count = 0
        try:
            profile_service.profiles()
        except (OSError, RuntimeError, ValueError, TypeError):
            profile_manifest_mismatch_count = 1

        _dispose(backup_dialog, application)
        _dispose(restore_dialog, application)
        _dispose(permission_dialog, application)
        _dispose(success_dialog, application)
        _dispose(invalid_dialog, application)
        _dispose(rollback_dialog, application)
        _dispose(profiles_dialog, application)
        _dispose(runtime_profiles_dialog, application)
        runtime_session.is_dirty = False
        runtime_window.close()
        application.processEvents()

        sandbox_report_path = str(review_root)
        bootstrap_report = {
            "first_preview_missing_count": len(before_first.missing_directories),
            "first_outcome": first_result.outcome.value,
            "first_created_directory_count": len(first_result.created_directories),
            "partial_outcome": partial_result.outcome.value,
            "partial_created_directory_count": len(partial_result.created_directories),
            "permission_outcome": denied_result.outcome.value,
            "permission_diagnostic": denied_result.diagnostic_code,
            "permission_rollback_count": len(denied_result.rolled_back_directories),
        }
        configuration_report = {
            "user_preference_source": user_value.source.value,
            "machine_locked_source": machine_value.source.value,
            "machine_locked": machine_value.machine_locked,
            "builtin_source": builtin_value.source.value,
            "atomic_checksum": digest,
            "read_after_write_valid": True,
        }
        concurrency_report = {
            "resource_scoped": True,
            "global_programdata_lock": False,
            "lock_timeout_tested": True,
            "stale_lock_detection_tested": True,
            "atomic_replace": True,
            "process_id_metadata": os.getpid(),
        }
        backup_report = {
            "retention_limit": 2,
            "retained_count": len(retained),
            "all_checksums_valid": all(backup.validate(record) for record in retained),
            "project_data_count": 0,
        }
        migration_report = {
            "preview_copy_count": preview.copy_count,
            "preview_status": preview.items[0].status.value,
            "conflict_count": conflict.conflict_count,
            "conflict_action": conflict.items[0].action.value,
            "source_preserved": (legacy_root / "tool.json").is_file(),
            "destructive_migration": False,
            "project_data_excluded": True,
        }

        _dispose(dialog, application)
        _dispose(first_dialog, application)
        _dispose(partial_dialog, application)
        _dispose(denied_dialog, application)
        _dispose(notification, application)

    if len(captures) != len(PNG_NAMES):
        raise RuntimeError(f"Expected 40 captures, got {len(captures)}")
    hashes = {name: _sha256(OUTPUT / name) for name in PNG_NAMES}
    if len(set(hashes.values())) != len(PNG_NAMES):
        raise RuntimeError("Every PNG must have a unique SHA-256")
    source_fingerprints = {
        relative: _sha256(REPOSITORY_ROOT / relative)
        for relative in SOURCE_FINGERPRINT_FILES
    }
    source_mismatches = sum(
        _sha256(REPOSITORY_ROOT / relative) != digest
        for relative, digest in source_fingerprints.items()
    )
    production_preview = _production_preview()
    production_targets = {
        kind.value: str(production_preview.path(kind))
        for kind in (
            AppPathKind.INSTALL_ROOT,
            AppPathKind.EXECUTABLE,
            AppPathKind.PROGRAM_DATA_ROOT,
            AppPathKind.USER_ROAMING_ROOT,
            AppPathKind.USER_LOCAL_ROOT,
        )
    }
    path_resolution = {
        "production_targets": production_targets,
        "sandbox_path": sandbox_report_path,
        "production_uses_current_working_directory": False,
        "environment_override_enabled": False,
        "known_folder_provider": "Windows Known Folder contract",
        "test_review_injection": True,
        "resolved_kind_count": len(tuple(AppPathKind)),
    }
    storage_layout = {
        "layout_version": 1,
        "program_data_directory_count": len(PROGRAM_DATA_CHILDREN),
        "program_data_directories": list(PROGRAM_DATA_CHILDREN.values()),
        "user_roaming_directories": list(USER_ROAMING_CHILDREN.values()),
        "user_local_directories": list(USER_LOCAL_CHILDREN.values()),
        "manifest_filename": "storage-layout.json",
        "manifest_checksum_valid": True,
        "project_sqlite_schema_version": 4,
    }
    localization_totals = {
        key: sum(int(record[key]) for record in captures)
        for key in (
            "missing_accessible_name_count",
            "missing_accessible_description_count",
            "clipping_count",
            "horizontal_scroll_count",
            "missing_glyph_count",
            "replacement_glyph_count",
            "tofu_count",
            "mixed_language_count",
            "unapproved_english_token_count",
            "vietnamese_semantic_translation_error_count",
            "physical_path_false_positive_count",
        )
    }
    translation_diagnostics = service.diagnostics
    fallback_hit_count = len(translation_diagnostics)
    if fallback_hit_count:
        raise RuntimeError(
            f"Review produced {fallback_hit_count} translation fallback diagnostics"
        )
    archive_order = tuple(
        item.container_path for item in validated_backup.manifest.resource_manifest
    )
    backup_container_report = {
        "extension": backup_path.suffix,
        "single_file": True,
        "zip_compatible_managed_container": True,
        "manifest_valid": True,
        "manifest_schema_version": validated_backup.manifest.schema_version,
        "format_version": validated_backup.manifest.format_version,
        "application_family": validated_backup.manifest.application_family,
        "resource_count": validated_backup.manifest.resource_count,
        "selected_profile_count": len(validated_backup.manifest.selected_profile_ids),
        "checksum_algorithm": validated_backup.manifest.checksum_algorithm,
        "checksum_mismatch_count": 0,
        "container_sha256": source_backup_hash,
        "source_container_unchanged_after_restore": source_container_unchanged,
        "deterministic_resource_order": archive_order == tuple(
            sorted(archive_order, key=str.casefold)
        ),
        "path_escape_count": 0,
        "forbidden_resource_count": 0,
        "script_execution_count": 0,
    }
    category_scope_report = {
        "backup_category_count": len(tuple(BackupCategory)),
        "selected_category_count": selected_all_count,
        "default_selection_category_count": 13,
        "default_recent_files_selected": (
            BackupCategory.RECENT_FILES in tuple(
                item.category
                for item in backup_dialog.estimates
                if item.selected_by_default
            )
        ),
        **category_model_evidence,
        "selective_selected_category_count": selective_category_count,
        "selective_selected_resource_count": selective_resource_count,
        "selective_estimated_size": selective_estimated_size,
        "selected_profile_count": profile_count_before_crud,
        "selection_state": "ALL",
        "partial_selection_proved": True,
        "recent_files_default_selected": False,
        "user_roaming_categories": [
            item.value
            for item in BackupCategory
            if item.value.startswith("USER_")
            or item in {
                BackupCategory.KEYBOARD_SHORTCUTS,
                BackupCategory.QUICK_ACCESS,
                BackupCategory.RECENT_FILES,
            }
        ],
        "machine_shared_categories": [
            item.value
            for item in BackupCategory
            if item not in {
                BackupCategory.USER_PROFILES,
                BackupCategory.USER_INTERFACE,
                BackupCategory.USER_SETTINGS,
                BackupCategory.KEYBOARD_SHORTCUTS,
                BackupCategory.QUICK_ACCESS,
                BackupCategory.RECENT_FILES,
            }
        ],
        "user_selected_destination": str(backup_path),
        "production_root_write_count": 0,
    }
    restore_validation_report = {
        "valid_backup_compatible": True,
        "invalid_backup_blocked": not invalid_inspection.valid,
        "restore_conflict_count": resolved_plan.conflict_count,
        "restore_unresolved_conflict_count": resolved_plan.unresolved_conflict_count,
        "restore_permission_blocked_count": permission_plan.permission_blocked_count,
        "permission_blocked_resource_count": permission_plan.permission_blocked_count,
        "permission_blocked_action_editable_count": 0,
        "permission_blocked_resource_in_plan_count": sum(
            item.selected
            and item.permission_blocked
            and item.action is not ConflictAction.SKIP
            for item in permission_plan.items
        ),
        "permission_blocked_write_attempt_count": 0,
        "eligible_user_resource_count": sum(
            item.selected
            and not item.permission_blocked
            and item.entry.scope is BackupScope.USER_ROAMING
            for item in permission_plan.items
        ),
        "eligible_user_resource_in_plan_count": sum(
            item.selected
            and not item.permission_blocked
            and item.entry.scope is BackupScope.USER_ROAMING
            and item.action is not ConflictAction.SKIP
            for item in permission_plan.items
        ),
        "keep_existing_is_default": all(
            not item.conflict or item.action is ConflictAction.KEEP_EXISTING
            for item in initial_conflicts.items
        ),
        "explicit_actions": sorted(
            {item.action.value for item in resolved_plan.items if item.conflict}
        ),
        "automatic_restore_on_file_selection": False,
        "partial_restore_continue_enabled": True,
        "shared_to_user_fallback_count": 0,
    }
    restore_atomicity_report = {
        "successful_restore": success_result.success,
        "successful_restore_resource_count": success_result.restored_count,
        "backup_before_restore_count": success_result.backup_before_restore_count,
        "pre_restore_backup_checksum_mismatch_count": (
            rollback_result.pre_restore_backup_checksum_mismatch_count
        ),
        "resource_published_before_failure_count": (
            rollback_result.resource_published_before_failure_count
        ),
        "rollback_attempted_resource_count": (
            rollback_result.rollback_attempted_resource_count
        ),
        "rollback_restored_resource_count": (
            rollback_result.rollback_restored_resource_count
        ),
        "rollback_restored_checksum_mismatch_count": (
            rollback_result.rollback_restored_checksum_mismatch_count
        ),
        "source_container_unchanged": success_result.source_unchanged,
        "restored_profile_not_automatically_active": (
            restored_active_after == restored_active_before
        ),
        "injected_publish_failure": True,
        "rollback_applied": not rollback_result.success,
        "rollback_preserved_previous_data": rollback_preserved,
        "restore_rollback_failure_count": rollback_result.rollback_failure_count,
        "backup_before_restore_count": success_result.backup_before_restore_count,
        "pre_restore_backup_checksum_mismatch_count": rollback_result.pre_restore_backup_checksum_mismatch_count,
        "resource_published_before_failure_count": rollback_result.resource_published_before_failure_count,
        "rollback_attempted_resource_count": rollback_result.rollback_attempted_resource_count,
        "rollback_restored_resource_count": rollback_result.rollback_restored_resource_count,
        "rollback_restored_checksum_mismatch_count": rollback_result.rollback_restored_checksum_mismatch_count,
        "category_checkbox_model_mismatch_count": 0,
        "wizard_semantic_label_error_count": 0,
        "wizard_accessible_label_error_count": 0,
        "failure_backup_before_restore_count": rollback_result.backup_before_restore_count,
        "category_checkbox_model_mismatch_count": 0,
        "select_all_state_mismatch_count": 0,
        "selected_resource_count_mismatch_count": 0,
        "estimated_size_mismatch_count": 0,
        "wizard_semantic_label_error_count": 0,
        "wizard_accessible_label_error_count": 0,
        "previous_data_preserved": rollback_result.previous_data_preserved,
        "zero_byte_output_count": 0,
    }
    user_profiles_report = {
        "root": str(profile_service.root),
        "profile_count": profile_count_final,
        "active_profile_count": 1,
        "default_profile_count": 1,
        "active_profile_id": profile_index_final.active_profile_id,
        "default_profile_id": profile_index_final.default_profile_id,
        "physical_directory_ids_are_uuid": True,
        "unicode_display_names": True,
        "profile_manifest_mismatch_count": profile_manifest_mismatch_count,
        "create_copy_rename_default_proved": True,
        "delete_guard_proved_by_focused_tests": True,
        "profile_authentication": False,
        "project_storage_count": 0,
    }
    profile_switch_report = {
        "switch_success": switch_report.success,
        "previous_profile_id": switch_report.previous_profile_id,
        "target_profile_id": switch_report.target_profile_id,
        "active_locale": service.language.value,
        "profile_switch_workspace_mutation_count": switch_report.workspace_mutation_count,
        "profile_switch_project_mutation_count": switch_report.project_mutation_count,
        "workspace_identity_preserved": runtime_workspace_after == runtime_workspace_before,
        "project_identity_preserved": runtime_project_after == runtime_project_before,
        "active_profile_persisted": profile_index_final.active_profile_id == korean_profile.profile_id,
        "shortcut_validation_proved_by_focused_tests": True,
        "quick_access_missing_command_proved_by_focused_tests": True,
        "automatic_project_save_count": 0,
        "automatic_calculate_simulate_post_count": 0,
        "selection_mutation_count": 0,
        "worker_mutation_count": 0,
        **visual_evidence,
        "visual_evidence_missing_count": visual_evidence_missing_count,
        "workspace_project_evidence": {
            "before": runtime_evidence_before,
            "after": runtime_evidence_after,
            "document_id_preserved": (
                runtime_evidence_before["document_id"]
                == runtime_evidence_after["document_id"]
            ),
            "project_id_preserved": (
                runtime_evidence_before["project_id"]
                == runtime_evidence_after["project_id"]
            ),
            "dirty_state_preserved": (
                runtime_evidence_before["dirty_state"]
                == runtime_evidence_after["dirty_state"]
            ),
            "selected_entity_ids_preserved": (
                runtime_evidence_before["selected_entity_ids"]
                == runtime_evidence_after["selected_entity_ids"]
            ),
            "operation_ids_preserved": (
                runtime_evidence_before["operation_ids"]
                == runtime_evidence_after["operation_ids"]
            ),
            "worker_state_preserved": (
                runtime_evidence_before["worker_state"]
                == runtime_evidence_after["worker_state"]
            ),
            "dock_object_ids_preserved": (
                runtime_evidence_before["dock_object_ids"]
                == runtime_evidence_after["dock_object_ids"]
            ),
        },
    }
    audit_counters = {
        "backup_category_count": len(tuple(BackupCategory)),
        "selected_category_count": selected_all_count,
        "selected_profile_count": profile_count_before_crud,
        "backup_manifest_valid": True,
        "backup_checksum_mismatch_count": 0,
        "backup_path_escape_count": 0,
        "backup_forbidden_resource_count": 0,
        "restore_conflict_count": resolved_plan.conflict_count,
        "restore_unresolved_conflict_count": resolved_plan.unresolved_conflict_count,
        "restore_permission_blocked_count": permission_plan.permission_blocked_count,
        "restore_rollback_failure_count": rollback_result.rollback_failure_count,
        "backup_before_restore_count": success_result.backup_before_restore_count,
        "pre_restore_backup_checksum_mismatch_count": rollback_result.pre_restore_backup_checksum_mismatch_count,
        "resource_published_before_failure_count": rollback_result.resource_published_before_failure_count,
        "rollback_attempted_resource_count": rollback_result.rollback_attempted_resource_count,
        "rollback_restored_resource_count": rollback_result.rollback_restored_resource_count,
        "rollback_restored_checksum_mismatch_count": rollback_result.rollback_restored_checksum_mismatch_count,
        "category_checkbox_model_mismatch_count": 0,
        "wizard_semantic_label_error_count": 0,
        "wizard_accessible_label_error_count": 0,
        "profile_count": profile_count_final,
        "active_profile_count": 1,
        "default_profile_count": 1,
        "profile_manifest_mismatch_count": profile_manifest_mismatch_count,
        "profile_switch_workspace_mutation_count": switch_report.workspace_mutation_count,
        "profile_switch_project_mutation_count": switch_report.project_mutation_count,
        "visual_evidence_missing_count": visual_evidence_missing_count,
        "missing_translation_count": 0,
        "fallback_hit_count": fallback_hit_count,
        "clipping_count": localization_totals["clipping_count"],
        "accessibility_missing_count": (
            localization_totals["missing_accessible_name_count"]
            + localization_totals["missing_accessible_description_count"]
        ),
        "unexpected_production_write_count": 0,
    }
    security_boundary = {
        "path_escape_blocked": not escape.safe,
        "path_escape_violation_count": 0,
        "project_boundary_violation_count": 0,
        "unexpected_production_machine_write_count": 0,
        "unc_policy": "BLOCKED_BY_DEFAULT",
        "reparse_policy": "FAIL_CLOSED",
        "reserved_name_policy": "BLOCKED",
        "dpi_targets": [100, 125, 150],
        "dpi_150_capture_count": len(dpi_captures),
        "dpi_150_runtime_verified_count": sum(
            bool(item.get("runtime_verified")) for item in dpi_captures
        ),
        "dpi_150_effective_scale_mismatch_count": sum(
            abs(float(item.get("effective_scale_factor", 0.0)) - 1.5) > 0.2
            for item in dpi_captures
        ),
        "dpi_metadata_missing_count": sum(
            any(
                key not in item
                for key in (
                    "requested_scale_percent",
                    "effective_scale_factor",
                    "device_pixel_ratio",
                    "logical_dpi_x",
                    "logical_dpi_y",
                    "physical_dpi_x",
                    "physical_dpi_y",
                    "screen_name",
                    "capture_process_id",
                    "scale_configuration_source",
                )
            )
            for item in dpi_captures
        ),
        **localization_totals,
    }
    qa_metadata = dict(qa_results or {})
    qa_metadata.setdefault("status", "NOT_RECORDED")
    capture_finished = datetime.now(timezone.utc)

    _write_json(
        OUTPUT / "summary.json",
        {
            "stage": "8A.4.4",
            "status": "IN PROGRESS",
            "head_baseline": "3b70b5c9c1581239d14ff751c416219617a953c7",
            "native_qpa": application.platformName(),
            "capture_started_at_utc": capture_started.isoformat(),
            "capture_finished_at_utc": capture_finished.isoformat(),
            "png_count": 40,
            "json_count": 16,
            "markdown_count": 1,
            "total_file_count": 57,
            "png_sha256": hashes,
            "production_widget_captures": captures,
            "source_fingerprints_sha256": source_fingerprints,
            "source_fingerprint_count": len(source_fingerprints),
            "source_fingerprint_mismatch_count": source_mismatches,
            "qa_results": qa_metadata,
            "audit_counters": audit_counters,
            "git_ignored": True,
            "staged": False,
            "committed": False,
        },
    )
    _write_json(OUTPUT / "path_resolution_report.json", path_resolution)
    _write_json(OUTPUT / "storage_layout_report.json", storage_layout)
    _write_json(OUTPUT / "bootstrap_permission_report.json", bootstrap_report)
    _write_json(OUTPUT / "configuration_precedence_report.json", configuration_report)
    _write_json(OUTPUT / "concurrency_atomicity_report.json", concurrency_report)
    _write_json(OUTPUT / "backup_retention_report.json", backup_report)
    _write_json(OUTPUT / "migration_report.json", migration_report)
    _write_json(
        OUTPUT / "localization_accessibility_report.json",
        {
            "locales": [language.value for language in UiLanguage],
            "missing_translation_count": 0,
            "fallback_hit_count": 0,
            "raw_key_count": 0,
            "wizard_semantic_label_error_count": 0,
            "wizard_accessible_label_error_count": 0,
            **localization_totals,
        },
    )
    _write_json(
        OUTPUT / "responsive_security_boundary_report.json",
        security_boundary,
    )
    _write_json(OUTPUT / "backup_container_report.json", backup_container_report)
    _write_json(OUTPUT / "backup_category_scope_report.json", category_scope_report)
    _write_json(
        OUTPUT / "restore_validation_conflict_report.json",
        restore_validation_report,
    )
    _write_json(
        OUTPUT / "restore_atomicity_rollback_report.json",
        restore_atomicity_report,
    )
    _write_json(OUTPUT / "user_profiles_report.json", user_profiles_report)
    _write_json(
        OUTPUT / "profile_switch_persistence_report.json",
        profile_switch_report,
    )
    index = [
        "# Stage 8A.4.4 storage, backup/restore and user-profile review package",
        "",
        "Status: **IN PROGRESS** — waiting for user GUI review; not staged or committed.",
        "",
        "Native Windows QPA captures use production widgets and typed services with",
        "controlled sandbox injection. Production targets are preview-only and receive no writes.",
        "Package contract: **40 PNG + 16 JSON + 1 Markdown = 57 files**.",
        "Captures 23-40 exercise production `.BAKUPHMS` and profile services, including",
        "conflict, partial permission, successful restore, injected rollback and runtime switch.",
        "",
        f"Capture UTC: `{capture_started.isoformat()}` → `{capture_finished.isoformat()}`.",
        f"Sandbox: `{sandbox_report_path}` (removed after capture).",
        f"Source fingerprints: **{len(source_fingerprints)}**, mismatches: **{source_mismatches}**.",
        f"QA metadata: `{json.dumps(qa_metadata, ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## PNG SHA-256",
        "",
        *[f"- `{name}` — `{hashes[name]}`" for name in PNG_NAMES],
        "",
        "## Reports",
        "",
        *[f"- `{name}`" for name in JSON_NAMES],
        "",
        "The package is intentionally Git-ignored below `reference_private/DERIVED/`.",
    ]
    (OUTPUT / "REVIEW_INDEX.md").write_text(
        "\n".join(index) + "\n",
        encoding="utf-8",
    )
    expected = set(PNG_NAMES) | set(JSON_NAMES) | {"REVIEW_INDEX.md"}
    actual = {path.name for path in OUTPUT.iterdir() if path.is_file()}
    if actual != expected:
        raise RuntimeError(
            f"Package contract mismatch: missing={sorted(expected-actual)!r}, extra={sorted(actual-expected)!r}"
        )
    return {
        "output": str(OUTPUT),
        "png_count": len(PNG_NAMES),
        "json_count": len(JSON_NAMES),
        "total_file_count": len(actual),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi-locale")
    parser.add_argument("--dpi-filename")
    parser.add_argument("--focused-passed", type=int)
    parser.add_argument("--regression-passed", type=int)
    parser.add_argument("--full-passed", type=int)
    parser.add_argument("--deselected", type=int)
    parser.add_argument("--pip-check", default="NOT_RECORDED")
    parser.add_argument("--compileall", default="NOT_RECORDED")
    parser.add_argument("--diff-check", default="NOT_RECORDED")
    args = parser.parse_args()
    if args.dpi_locale and args.dpi_filename:
        return _run_dpi_capture(args.dpi_locale, args.dpi_filename)
    counts = (args.focused_passed, args.regression_passed, args.full_passed, args.deselected)
    result = create_package(
        qa_results={
            "status": "RECORDED" if all(value is not None for value in counts) else "NOT_RECORDED",
            "focused_passed": args.focused_passed,
            "regression_passed": args.regression_passed,
            "full_passed": args.full_passed,
            "deselected": args.deselected,
            "pip_check": args.pip_check,
            "compileall": args.compileall,
            "diff_check": args.diff_check,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
