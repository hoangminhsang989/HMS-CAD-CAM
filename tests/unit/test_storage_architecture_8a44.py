"""Stage 8A.4.4 typed storage, bootstrap, security and UI contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.core.paths import (
    APPLICATION_FAMILY,
    DEFAULT_INSTALL_ROOT,
    INSTALL_CHILDREN,
    PROGRAM_DATA_CHILDREN,
    USER_LOCAL_CHILDREN,
    USER_ROAMING_CHILDREN,
    AppPathKind,
    ApplicationPathsService,
    ExpectedOwner,
    KnownFolder,
    PathResolutionMode,
    PathSource,
    StaticKnownFolderProvider,
    StorageScope,
)
from hms_cadcam.core.storage_backup import MachineBackupService
from hms_cadcam.core.storage_config import (
    ConfigurationDocument,
    ConfigurationService,
    ConfigurationSource,
)
from hms_cadcam.core.storage_io import (
    AtomicJsonWriter,
    AtomicWriteError,
    MachineResource,
    ResourceFileLock,
    ResourceLockMetadata,
    StorageLockTimeoutError,
)
from hms_cadcam.core.storage_layout import (
    BootstrapOutcome,
    StorageBootstrapService,
    StorageLayoutManifest,
    StorageLayoutStatus,
)
from hms_cadcam.core.storage_maintenance import UserStorageMaintenanceService
from hms_cadcam.core.storage_migration import (
    LegacyMigrationService,
    MigrationAction,
    MigrationConflict,
    MigrationResourceType,
    MigrationStatus,
)
from hms_cadcam.core.storage_security import (
    PathSecurityCode,
    validate_storage_write_path,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.data_locations import (
    DataLocationsDialog,
    StorageNotificationBar,
)
from hms_cadcam.ui.i18n import (
    LocaleSettingsService,
    TranslationService,
    UiLanguage,
    build_default_catalogs,
    set_translation_service,
    translation_service,
)
from hms_cadcam.ui.localization_audit import (
    RuntimeAuditMetrics,
    _is_mixed,
    audit_locale,
    audit_widget,
)
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tools.create_stage8a44_storage_review_package import (
    JSON_NAMES,
    OUTPUT,
    PNG_NAMES,
    PROFILE_SWITCH_VISUAL_FIELDS,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _qt_application() -> QApplication:
    return _application()


@pytest.fixture
def i18n_service():
    previous = translation_service()
    service = TranslationService(build_default_catalogs())
    set_translation_service(service)
    yield service
    set_translation_service(previous)


@pytest.fixture
def sandbox_paths(tmp_path: Path) -> ApplicationPathsService:
    return ApplicationPathsService.sandbox((tmp_path / "storage").resolve())


def _prepare_install(paths: ApplicationPathsService) -> None:
    paths.path(AppPathKind.INSTALL_ROOT).mkdir(parents=True)


def _production_provider(tmp_path: Path) -> StaticKnownFolderProvider:
    return StaticKnownFolderProvider(
        {
            KnownFolder.PROGRAM_DATA: Path("C:/ProgramData"),
            KnownFolder.ROAMING_APP_DATA: Path("C:/Users/Review/AppData/Roaming"),
            KnownFolder.LOCAL_APP_DATA: Path("C:/Users/Review/AppData/Local"),
            KnownFolder.DOCUMENTS: Path("C:/Users/Review/Documents"),
        }
    )


def test_review_package_contract_is_exact() -> None:
    assert OUTPUT.name == "UI_STAGE_8A4_4_STORAGE_ARCHITECTURE"
    assert OUTPUT.parent.name == "DERIVED"
    assert len(PNG_NAMES) == 40
    assert len(JSON_NAMES) == 16
    assert PNG_NAMES[0] == "01_data_locations_vietnamese.png"
    assert PNG_NAMES[21] == "22_dpi_150_korean.png"
    assert PNG_NAMES[-1] == "40_user_profile_runtime_switch.png"
    assert JSON_NAMES == (
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
    assert PROFILE_SWITCH_VISUAL_FIELDS == (
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


def _hold_resource_lock(machine_root: str, ready, release) -> None:
    with ResourceFileLock(Path(machine_root), MachineResource.CONFIG, timeout_seconds=1):
        ready.set()
        release.wait(5)


def test_typed_scope_and_kind_contract_is_complete() -> None:
    assert tuple(StorageScope) == (
        StorageScope.INSTALL,
        StorageScope.MACHINE_SHARED,
        StorageScope.USER_ROAMING,
        StorageScope.USER_LOCAL,
        StorageScope.DOCUMENT,
        StorageScope.CAM_PROJECT,
        StorageScope.TEST_SANDBOX,
        StorageScope.REVIEW_PRIVATE,
    )
    assert set(AppPathKind) >= {
        AppPathKind.INSTALL_ROOT,
        AppPathKind.PROGRAM_DATA_ROOT,
        AppPathKind.USER_ROAMING_ROOT,
        AppPathKind.USER_LOCAL_ROOT,
        AppPathKind.DOCUMENT_PATH,
        AppPathKind.CAM_PROJECT_ROOT,
    }


def test_production_resolver_uses_fixed_install_and_windows_known_folders(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    service = ApplicationPathsService.production(
        known_folders=_production_provider(tmp_path)
    )
    assert service.path(AppPathKind.INSTALL_ROOT) == DEFAULT_INSTALL_ROOT
    assert service.path(AppPathKind.EXECUTABLE) == Path("C:/HMS-CADCAM/HMS-CADCAM.exe")
    assert service.path(AppPathKind.PROGRAM_DATA_ROOT) == Path("C:/ProgramData/HMS-CADCAM")
    assert service.path(AppPathKind.USER_ROAMING_ROOT) == Path("C:/Users/Review/AppData/Roaming/HMS-CADCAM")
    assert service.path(AppPathKind.USER_LOCAL_ROOT) == Path("C:/Users/Review/AppData/Local/HMS-CADCAM")
    assert cwd not in service.path(AppPathKind.USER_CONFIG).parents


def test_production_roots_cannot_be_overridden(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be overridden"):
        ApplicationPathsService(
            mode=PathResolutionMode.PRODUCTION,
            known_folders=_production_provider(tmp_path),
            install_root=tmp_path,
        )


def test_sandbox_requires_all_absolute_roots(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="all four"):
        ApplicationPathsService(
            mode=PathResolutionMode.TEST_SANDBOX,
            install_root=tmp_path,
        )
    with pytest.raises(ValueError, match="absolute"):
        ApplicationPathsService.sandbox(Path("relative"))


def test_sandbox_result_records_explicit_source_and_owner(
    sandbox_paths: ApplicationPathsService,
) -> None:
    resolved = sandbox_paths.resolve(AppPathKind.TOOL_LIBRARY)
    assert resolved.source is PathSource.TEST_INJECTION
    assert resolved.scope is StorageScope.MACHINE_SHARED
    assert resolved.expected_owner is ExpectedOwner.MACHINE_ADMINISTRATORS
    assert resolved.layout_version == 1


@pytest.mark.parametrize("kind", (AppPathKind.DOCUMENT_PATH, AppPathKind.CAM_PROJECT_ROOT))
def test_document_and_project_paths_require_absolute_user_selection(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    kind: AppPathKind,
) -> None:
    with pytest.raises(ValueError, match="user-selected"):
        sandbox_paths.resolve(kind)
    selected = (tmp_path / ("part.HMS" if kind is AppPathKind.DOCUMENT_PATH else "job")).resolve()
    resolved = sandbox_paths.resolve(kind, selected_path=selected)
    assert resolved.physical_path == selected
    assert resolved.source is PathSource.USER_SELECTION
    assert resolved.scope in {StorageScope.DOCUMENT, StorageScope.CAM_PROJECT}


def test_directory_layout_names_are_exact_ascii_and_space_free() -> None:
    assert tuple(PROGRAM_DATA_CHILDREN.values()) == (
        "Tool-Library",
        "Program-Templates",
        "Posts",
        "Machines",
        "Materials",
        "Config",
        "Schemas",
        "Backups",
    )
    assert tuple(USER_ROAMING_CHILDREN.values()) == ("Config", "UI-State", "Profiles")
    assert tuple(USER_LOCAL_CHILDREN.values()) == ("Cache", "Logs", "Temp", "Crash")
    for name in (*INSTALL_CHILDREN.values(), *PROGRAM_DATA_CHILDREN.values(), *USER_ROAMING_CHILDREN.values(), *USER_LOCAL_CHILDREN.values()):
        assert name.isascii() and " " not in name


@pytest.mark.parametrize(
    ("candidate_name", "code"),
    (
        ("CON", PathSecurityCode.RESERVED_NAME),
        ("bad.", PathSecurityCode.TRAILING_DOT_OR_SPACE),
        ("bad ", PathSecurityCode.TRAILING_DOT_OR_SPACE),
        ("bad:name", PathSecurityCode.INVALID_CHARACTER),
    ),
)
def test_security_rejects_reserved_trailing_and_invalid_names(
    tmp_path: Path,
    candidate_name: str,
    code: PathSecurityCode,
) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    result = validate_storage_write_path(root, root / candidate_name)
    assert not result.safe and result.code is code


def test_security_rejects_traversal_escape_unc_and_long_path(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    assert validate_storage_write_path(root, root / ".." / "escape").code in {
        PathSecurityCode.TRAVERSAL,
        PathSecurityCode.ROOT_ESCAPE,
    }
    assert validate_storage_write_path(root, tmp_path / "outside").code is PathSecurityCode.ROOT_ESCAPE
    assert validate_storage_write_path(Path("//server/share"), Path("//server/share/file")).code is PathSecurityCode.UNC_BLOCKED
    assert validate_storage_write_path(root, root / ("x" * 250)).code is PathSecurityCode.PATH_TOO_LONG


def test_security_rejects_file_directory_and_case_collisions(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    collision = root / "Config"
    collision.write_text("file", encoding="utf-8")
    assert validate_storage_write_path(root, collision, expect_directory=True).code is PathSecurityCode.FILE_DIRECTORY_COLLISION
    collision.unlink()
    (root / "Config").mkdir()
    assert validate_storage_write_path(root, root / "config" / "value.json").code is PathSecurityCode.CASE_COLLISION


def test_security_rejects_symlink_or_reparse_point(tmp_path: Path, monkeypatch) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    candidate = root / "linked" / "file.json"
    import hms_cadcam.core.storage_security as security

    monkeypatch.setattr(security, "_is_reparse", lambda path: path.name == "linked")
    (root / "linked").mkdir()
    assert validate_storage_write_path(root, candidate).code is PathSecurityCode.REPARSE_POINT


def test_bootstrap_empty_layout_is_atomic_and_complete(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    result = StorageBootstrapService(sandbox_paths).bootstrap()
    assert result.outcome is BootstrapOutcome.CREATED
    assert result.inspection.ready
    assert result.manifest_written
    assert len(tuple(path for path in sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT).iterdir() if path.is_dir())) == 8
    assert all(sandbox_paths.path(kind).is_dir() for kind in (*USER_ROAMING_CHILDREN, *USER_LOCAL_CHILDREN))


def test_bootstrap_repairs_partial_layout_and_is_idempotent(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT).mkdir(parents=True)
    sandbox_paths.path(AppPathKind.TOOL_LIBRARY).mkdir()
    service = StorageBootstrapService(sandbox_paths)
    repaired = service.bootstrap()
    repeated = service.bootstrap()
    assert repaired.outcome is BootstrapOutcome.REPAIRED
    assert repeated.outcome is BootstrapOutcome.ALREADY_READY
    assert repeated.created_directories == ()


def test_bootstrap_permission_failure_rolls_back_only_new_directories(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)

    def mkdir(path: Path) -> None:
        if path.name == "Posts":
            raise PermissionError("denied")
        path.mkdir()

    result = StorageBootstrapService(sandbox_paths, mkdir=mkdir).bootstrap()
    assert result.outcome is BootstrapOutcome.ROLLED_BACK
    assert result.diagnostic_code == "PERMISSION_DENIED"
    assert set(result.rolled_back_directories) <= set(result.created_directories)
    assert not sandbox_paths.path(AppPathKind.TOOL_LIBRARY).exists()


def test_bootstrap_blocks_file_directory_collision(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT).mkdir(parents=True)
    sandbox_paths.path(AppPathKind.POSTS).write_text("collision", encoding="utf-8")
    result = StorageBootstrapService(sandbox_paths).bootstrap()
    assert result.outcome is BootstrapOutcome.BLOCKED
    assert result.inspection.status is StorageLayoutStatus.UNSAFE_PATH


def test_layout_manifest_round_trip_and_checksum_validation(
    sandbox_paths: ApplicationPathsService,
) -> None:
    manifest = StorageLayoutManifest.create(sandbox_paths)
    decoded = StorageLayoutManifest.from_dict(manifest.to_dict())
    assert decoded == manifest
    tampered = manifest.to_dict()
    tampered["program_data_root"] = "C:/escape"
    with pytest.raises(ValueError, match="checksum"):
        StorageLayoutManifest.from_dict(tampered)


def test_configuration_precedence_and_locked_machine_policy(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    service = ConfigurationService(
        sandbox_paths,
        builtin_defaults={"theme": "builtin", "safe": True},
        code_fallbacks={"timeout": 10},
    )
    service.write_machine_config(
        {"theme": "machine", "safe": False},
        locked_keys=("safe",),
    )
    service.write_user_preferences({"theme": "user", "safe": True})
    assert service.resolve("theme").source is ConfigurationSource.USER_PREFERENCE
    assert service.resolve("safe").value is False
    assert service.resolve("safe").machine_locked
    assert service.resolve("timeout").source is ConfigurationSource.CODE_FALLBACK


def test_invalid_user_config_falls_back_without_crash(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    service = ConfigurationService(sandbox_paths, builtin_defaults={"theme": "builtin"})
    service.user_path.write_text("{broken", encoding="utf-8")
    resolved = service.resolve("theme")
    assert resolved.value == "builtin"
    assert "USER_CONFIG_INVALID" in service.diagnostics


def test_configuration_checksum_mismatch_is_rejected() -> None:
    document = ConfigurationDocument.create({"value": 1})
    tampered = document.to_dict()
    tampered["values"] = {"value": 2}
    with pytest.raises(ValueError, match="checksum"):
        ConfigurationDocument.from_dict(tampered)


def test_atomic_machine_config_failure_restores_previous_bytes(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    service = ConfigurationService(sandbox_paths)
    service.write_machine_config({"value": "before"})
    before = service.machine_path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace blocked")

    failing = ConfigurationService(
        sandbox_paths,
        json_writer=AtomicJsonWriter(replace=fail_replace),
    )
    with pytest.raises(AtomicWriteError):
        failing.write_machine_config({"value": "after"})
    assert service.machine_path.read_bytes() == before


def test_resource_locks_are_scoped_timeout_and_release(
    sandbox_paths: ApplicationPathsService,
) -> None:
    root = sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT)
    root.mkdir(parents=True)
    with ResourceFileLock(root, MachineResource.CONFIG, timeout_seconds=0.05):
        with ResourceFileLock(root, MachineResource.POSTS, timeout_seconds=0.05):
            assert True
        with pytest.raises(StorageLockTimeoutError):
            ResourceFileLock(root, MachineResource.CONFIG, timeout_seconds=0.01).acquire()
    assert not tuple((root / "Config").glob(".locks/*.lock"))


def test_stale_resource_lock_is_recovered(
    sandbox_paths: ApplicationPathsService,
) -> None:
    root = sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT)
    lock = ResourceFileLock(root, MachineResource.CONFIG, stale_after_seconds=1)
    lock.path.parent.mkdir(parents=True)
    stale = ResourceLockMetadata(
        MachineResource.CONFIG,
        2_000_000_000,
        "old",
        (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "stale",
    )
    lock.path.write_text(json.dumps(stale.to_dict()), encoding="utf-8")
    with lock:
        assert lock.metadata is not None and lock.metadata.token != "stale"


@pytest.mark.serial
def test_second_process_cannot_take_active_resource_lock(
    sandbox_paths: ApplicationPathsService,
) -> None:
    root = sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT)
    root.mkdir(parents=True)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_resource_lock, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(StorageLockTimeoutError):
            ResourceFileLock(root, MachineResource.CONFIG, timeout_seconds=0.05).acquire()
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
    assert process.exitcode == 0


def test_machine_config_write_creates_backup_and_retention(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    backup = MachineBackupService(sandbox_paths, retention_per_resource=2)
    service = ConfigurationService(sandbox_paths, backup_service=backup)
    for value in range(4):
        service.write_machine_config({"value": value})
        time.sleep(0.002)
    records = backup.records(MachineResource.CONFIG)
    assert len(records) == 2
    assert all(backup.validate(record) for record in records)
    assert backup.restore_bytes(records[0])


@pytest.mark.parametrize("name", ("project.db", "part.HMS"))
def test_machine_backup_rejects_project_data(
    sandbox_paths: ApplicationPathsService,
    name: str,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    source = sandbox_paths.path(AppPathKind.MACHINE_CONFIG) / name
    source.write_bytes(b"project")
    with pytest.raises(ValueError, match="outside"):
        MachineBackupService(sandbox_paths).create_backup(
            source,
            MachineResource.CONFIG,
            source_version="1",
        )


def test_migration_preview_copy_verify_and_preserve_source(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    legacy = tmp_path / "legacy-tools"
    legacy.mkdir()
    source = legacy / "tool.json"
    source.write_text('{"tool": 1}', encoding="utf-8")
    service = LegacyMigrationService(sandbox_paths)
    plan = service.scan({MigrationResourceType.TOOL_LIBRARY: (legacy,)})
    assert plan.copy_count == 1
    result = service.execute(plan.items[0])
    assert result.status is MigrationStatus.VERIFIED
    assert source.is_file() and result.target.read_bytes() == source.read_bytes()


def test_migration_detects_duplicate_conflict_and_excludes_project_data(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "same.json").write_text("same", encoding="utf-8")
    (legacy / "conflict.json").write_text("old", encoding="utf-8")
    (legacy / "part.HMS").write_bytes(b"hms")
    target = sandbox_paths.path(AppPathKind.MATERIALS)
    (target / "same.json").write_text("same", encoding="utf-8")
    (target / "conflict.json").write_text("new", encoding="utf-8")
    plan = LegacyMigrationService(sandbox_paths).scan(
        {MigrationResourceType.MATERIALS: (legacy,)}
    )
    conflicts = {item.source.name: item.conflict for item in plan.items}
    assert conflicts == {
        "same.json": MigrationConflict.DUPLICATE,
        "conflict.json": MigrationConflict.TARGET_EXISTS,
        "part.HMS": MigrationConflict.EXCLUDED_PROJECT_DATA,
    }
    assert plan.project_data_excluded_count == 1
    assert all(item.action is not MigrationAction.COPY for item in plan.items)


def test_migration_source_change_blocks_publish(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    source = tmp_path / "legacy.json"
    source.write_text("before", encoding="utf-8")
    service = LegacyMigrationService(sandbox_paths)
    item = service.scan({MigrationResourceType.MACHINE_CONFIG: (source,)}).items[0]
    source.write_text("after", encoding="utf-8")
    assert service.execute(item).status is MigrationStatus.FAILED
    assert not item.target.exists()


def test_migration_blocks_directory_target_and_never_overwrites_racing_file(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    service = LegacyMigrationService(sandbox_paths)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    directory_source = legacy / "occupied"
    directory_source.write_text("source", encoding="utf-8")
    (sandbox_paths.path(AppPathKind.MATERIALS) / "occupied").mkdir()
    directory_item = service.scan(
        {MigrationResourceType.MATERIALS: (directory_source,)}
    ).items[0]
    assert directory_item.conflict is MigrationConflict.TARGET_EXISTS
    assert directory_item.action is MigrationAction.BLOCK

    race_source = legacy / "race.json"
    race_source.write_text("source", encoding="utf-8")
    race_item = service.scan(
        {MigrationResourceType.MATERIALS: (race_source,)}
    ).items[0]
    original_rename = os.rename

    def create_racing_target_then_rename(source: Path, target: Path) -> None:
        Path(target).write_text("concurrent", encoding="utf-8")
        original_rename(source, target)

    monkeypatch.setattr(
        "hms_cadcam.core.storage_migration.os.rename",
        create_racing_target_then_rename,
    )
    result = service.execute(race_item)
    assert result.status is MigrationStatus.FAILED
    assert race_item.target.read_text(encoding="utf-8") == "concurrent"


def test_migration_blocks_symlink_before_reading_project_named_target(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    legacy = tmp_path / "legacy-symlink"
    legacy.mkdir()
    link = legacy / "project.db"
    link.write_bytes(b"must-not-be-migrated")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: (
            True if candidate == link else original_is_symlink(candidate)
        ),
    )
    item = LegacyMigrationService(sandbox_paths).scan(
        {MigrationResourceType.MACHINE_CONFIG: (legacy,)}
    ).items[0]
    assert item.conflict is MigrationConflict.UNSAFE_SOURCE
    assert item.checksum == ""
    assert item.size == 0


def test_cache_cleanup_is_confined_to_user_cache(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    cache = sandbox_paths.path(AppPathKind.CACHE)
    logs = sandbox_paths.path(AppPathKind.LOGS)
    (cache / "nested").mkdir()
    (cache / "nested" / "cache.bin").write_bytes(b"123")
    (logs / "keep.log").write_text("keep", encoding="utf-8")
    result = UserStorageMaintenanceService(sandbox_paths).clear_cache()
    assert result.removed_file_count == 1
    assert not tuple(cache.rglob("*"))
    assert (logs / "keep.log").is_file()


def test_qsettings_locale_remains_in_user_roaming_config(
    sandbox_paths: ApplicationPathsService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    settings = QSettings(
        str(sandbox_paths.path(AppPathKind.USER_CONFIG) / "workspace_ui.ini"),
        QSettings.Format.IniFormat,
    )
    locale = LocaleSettingsService(settings)
    assert locale.save(UiLanguage.KO_KR)
    assert LocaleSettingsService(settings).load() is UiLanguage.KO_KR
    assert sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT) not in Path(settings.fileName()).parents


def test_data_locations_dialog_has_four_fixed_groups_and_no_root_editor(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    _prepare_install(sandbox_paths)
    bootstrap = StorageBootstrapService(sandbox_paths)
    preview = ApplicationPathsService.production(known_folders=_production_provider(tmp_path))
    dialog = DataLocationsDialog(sandbox_paths, bootstrap, production_preview=preview)
    assert dialog.tabs.count() == 4
    assert not dialog.findChildren(QLineEdit)
    assert dialog._models[1].rowCount() == 9
    assert "C:\\ProgramData\\HMS-CADCAM" in dialog._models[1].data(dialog._models[1].index(0, 1))
    dialog.close()


@pytest.mark.parametrize(
    ("language", "title", "tabs"),
    (
        (UiLanguage.VI_VN, "Vị trí dữ liệu", ("Cài đặt chương trình", "Dữ liệu dùng chung", "Dữ liệu người dùng", "Tài liệu và dự án")),
        (UiLanguage.EN_US, "Data locations", ("Program installation", "Shared machine data", "User data", "Documents and projects")),
        (UiLanguage.KO_KR, "데이터 위치", ("프로그램 설치", "컴퓨터 공용 데이터", "사용자 데이터", "문서 및 프로젝트")),
    ),
)
def test_data_locations_dialog_retranslates_three_locales(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
    language: UiLanguage,
    title: str,
    tabs: tuple[str, ...],
) -> None:
    _prepare_install(sandbox_paths)
    dialog = DataLocationsDialog(
        sandbox_paths,
        StorageBootstrapService(sandbox_paths),
        production_preview=ApplicationPathsService.production(known_folders=_production_provider(tmp_path)),
    )
    i18n_service.set_language(language)
    _application().processEvents()
    assert dialog.windowTitle() == title
    assert tuple(dialog.tabs.tabText(index) for index in range(4)) == tabs
    dialog.close()


def test_storage_notification_is_non_modal_and_localized(
    sandbox_paths: ApplicationPathsService,
    i18n_service: TranslationService,
) -> None:
    bar = StorageNotificationBar()
    bar.update_inspection(StorageBootstrapService(sandbox_paths).inspect())
    assert bar.isVisible()
    assert "Dữ liệu dùng chung" in bar.message_label.text()
    i18n_service.set_language(UiLanguage.KO_KR)
    assert "공용 데이터" in bar.message_label.text()
    assert not isinstance(bar, QDialog)
    bar.close()


@pytest.mark.parametrize("language", tuple(UiLanguage))
@pytest.mark.parametrize("dpi_percent", (100, 125, 150))
def test_data_locations_accessibility_and_dpi_layout(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
    dpi_percent: int,
    language: UiLanguage,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    dialog = DataLocationsDialog(
        sandbox_paths,
        StorageBootstrapService(sandbox_paths),
        production_preview=ApplicationPathsService.production(known_folders=_production_provider(tmp_path)),
    )
    i18n_service.set_language(language)
    font = dialog.font()
    font.setPointSizeF(max(8.0, 9.0 * dpi_percent / 100.0))
    dialog.setFont(font)
    dialog.resize(1180, 800)
    dialog.show()
    _application().processEvents()
    metrics = audit_widget(dialog)
    assert metrics.missing_accessible_name_count == 0
    assert metrics.missing_accessible_description_count == 0
    assert metrics.horizontal_scroll_count == 0
    assert metrics.replacement_glyph_count == 0
    assert metrics.tofu_count == 0
    assert not any(_is_mixed(text, language) for text in metrics.texts)
    dialog.close()


def test_vietnamese_semantic_audit_ignores_physical_paths(
    i18n_service: TranslationService,
) -> None:
    report = audit_locale(
        i18n_service,
        UiLanguage.VI_VN,
        visible_keys=("Back", "Next", "Close"),
        runtime=RuntimeAuditMetrics(
            texts=(
                r"C:\HMS-CADCAM\runtime\plugins\HMS-CADCAM.exe",
                "Quay lại",
                "Tiếp tục",
                "Đóng",
            )
        ),
    )
    assert report.unapproved_english_token_count == 0
    assert report.vietnamese_semantic_translation_error_count == 0
    assert report.physical_path_false_positive_count == 0


def test_korean_path_column_keeps_unambiguous_windows_backslash(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    _prepare_install(sandbox_paths)
    StorageBootstrapService(sandbox_paths).bootstrap()
    dialog = DataLocationsDialog(
        sandbox_paths,
        StorageBootstrapService(sandbox_paths),
        production_preview=ApplicationPathsService.production(
            known_folders=_production_provider(tmp_path)
        ),
    )
    i18n_service.set_language(UiLanguage.KO_KR)
    model = dialog._models[1]
    path_index = model.index(0, 1)
    assert "\\" in model.data(path_index)
    font = model.data(path_index, Qt.ItemDataRole.FontRole.value)
    if font is not None:
        assert font.family() == "Segoe UI"
    dialog.close()


def test_main_window_exposes_system_data_locations_and_non_modal_warning(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    reason = "CAD rendering backend is unavailable."
    window = MainWindow(
        service,
        UnavailableCadKernel(reason),
        UnavailableCadViewportBackend(reason),
        layout_store=WorkspaceLayoutStore(
            QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
        ),
        application_paths=sandbox_paths,
        storage_bootstrap=StorageBootstrapService(sandbox_paths),
    )
    assert window._data_locations_action.isEnabled()
    assert window._storage_notification is not None
    assert not window._storage_bootstrap.inspect().ready
    window._show_data_locations()
    assert window._data_locations_dialog is not None
    assert not window._data_locations_dialog.isModal()
    window.close()


def test_project_service_runtime_and_save_suggestion_can_be_injected_by_scope(
    sandbox_paths: ApplicationPathsService,
) -> None:
    config = sandbox_paths.path(AppPathKind.USER_CONFIG)
    runtime = sandbox_paths.path(AppPathKind.TEMP) / "Document-Runtime"
    documents = sandbox_paths.documents_root
    service = ProjectService.create_default(
        config,
        document_runtime_root=runtime,
        default_document_directory=documents,
    )
    assert service.config_dir == config
    assert service._document_container.runtime_root == runtime
    assert runtime not in sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT).parents


def test_resolve_and_inspect_do_not_write_to_cwd_or_production_roots(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    before = tuple(tmp_path.rglob("*"))
    sandbox_paths.all_application_paths()
    StorageBootstrapService(sandbox_paths).inspect()
    after = tuple(tmp_path.rglob("*"))
    assert after == before
    assert not tuple(cwd.iterdir())


def test_project_and_shared_storage_boundaries_are_disjoint(
    sandbox_paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    document = sandbox_paths.resolve(
        AppPathKind.DOCUMENT_PATH,
        selected_path=(tmp_path / "Unicode part 01.HMS").resolve(),
    )
    project = sandbox_paths.resolve(
        AppPathKind.CAM_PROJECT_ROOT,
        selected_path=(tmp_path / "Job Folder").resolve(),
    )
    machine = sandbox_paths.path(AppPathKind.PROGRAM_DATA_ROOT)
    appdata = sandbox_paths.path(AppPathKind.USER_LOCAL_ROOT)
    assert machine not in document.physical_path.parents
    assert appdata not in document.physical_path.parents
    assert machine not in project.physical_path.parents
    assert appdata not in project.physical_path.parents
    assert APPLICATION_FAMILY == "HMS-CADCAM"
