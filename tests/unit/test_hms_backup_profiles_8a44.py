"""Stage 8A.4.4 `.BAKUPHMS`, restore and user-profile contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.core.hms_backup import (
    BACKUP_EXTENSION,
    BACKUP_FORMAT_VERSION,
    BackupCancelled,
    BackupCategory,
    BackupError,
    BackupLimits,
    BackupScope,
    BackupSelectionModel,
    BackupValidationError,
    CompatibilityState,
    ConflictAction,
    HmsBackupService,
    HmsRestoreService,
    SelectionState,
)
from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_io import AtomicBytesWriter, AtomicWriteError
from hms_cadcam.core.storage_backup import PreRestoreBackupService
from hms_cadcam.core.storage_layout import StorageBootstrapService
from hms_cadcam.core.user_profiles import (
    PROFILE_FILE_NAMES,
    PROFILE_SCHEMA_VERSION,
    ProfileError,
    UserProfile,
    UserProfileService,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.backup_profiles import (
    BackupWizardDialog,
    BackupCategoryTableModel,
    RestoreWizardDialog,
    UserProfilesDialog,
)
from hms_cadcam.ui.i18n import (
    TranslationService,
    UiLanguage,
    build_default_catalogs,
    set_translation_service,
    translation_service,
)
from hms_cadcam.ui.localization_audit import _is_mixed, audit_widget
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def qt_application() -> QApplication:
    return _application()


@pytest.fixture
def i18n_service():
    previous = translation_service()
    service = TranslationService(build_default_catalogs())
    set_translation_service(service)
    yield service
    set_translation_service(previous)


@pytest.fixture
def paths(tmp_path: Path) -> ApplicationPathsService:
    service = ApplicationPathsService.sandbox((tmp_path / "storage").resolve())
    service.path(AppPathKind.INSTALL_ROOT).mkdir(parents=True)
    assert StorageBootstrapService(service).bootstrap().inspection.ready
    return service


@pytest.fixture
def profiles(paths: ApplicationPathsService) -> UserProfileService:
    service = UserProfileService(paths)
    service.bootstrap(locale="VI_VN")
    return service


@pytest.fixture
def backup(
    paths: ApplicationPathsService,
    profiles: UserProfileService,
) -> HmsBackupService:
    return HmsBackupService(paths, profile_service=profiles)


def _make_machine_resources(paths: ApplicationPathsService) -> None:
    (paths.path(AppPathKind.TOOL_LIBRARY) / "tool-01.json").write_text(
        '{"resource_id":"tool-01","schema_version":1}', encoding="utf-8"
    )
    holder = paths.path(AppPathKind.TOOL_LIBRARY) / "Holders"
    holder.mkdir()
    (holder / "holder-01.json").write_text(
        '{"resource_id":"holder-01","schema_version":1}', encoding="utf-8"
    )
    (paths.path(AppPathKind.PROGRAM_TEMPLATES) / "template.json").write_text(
        '{"resource_id":"template-01"}', encoding="utf-8"
    )
    (paths.path(AppPathKind.POSTS) / "post.py").write_text(
        "POST_DATA = 1\n", encoding="utf-8"
    )
    (paths.path(AppPathKind.MACHINES) / "machine.json").write_text(
        '{"resource_id":"machine-01"}', encoding="utf-8"
    )
    (paths.path(AppPathKind.MATERIALS) / "steel.json").write_text(
        '{"resource_id":"steel"}', encoding="utf-8"
    )
    (paths.path(AppPathKind.MACHINE_CONFIG) / "machine-config.json").write_text(
        '{"schema_version":1,"values":{"units":"metric"}}', encoding="utf-8"
    )
    (paths.path(AppPathKind.SCHEMAS) / "catalog.json").write_text(
        '{"schema_version":1}', encoding="utf-8"
    )


def _backup_path(tmp_path: Path, name: str = "Cấu hình xưởng A.BAKUPHMS") -> Path:
    return (tmp_path / name).resolve()


def _create_full_backup(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> Path:
    _make_machine_resources(paths)
    profile_ids = tuple(item.profile_id for item in profiles.profiles())
    destination = _backup_path(tmp_path)
    backup.create(
        destination,
        tuple(BackupCategory),
        profile_ids=profile_ids,
        created_locale="VI_VN",
    )
    return destination


def _rewrite_archive(path: Path, transform) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, payload in transform(entries):
            archive.writestr(info, payload)


def test_backup_category_enum_is_exact() -> None:
    assert tuple(item.value for item in BackupCategory) == (
        "USER_PROFILES", "USER_INTERFACE", "USER_SETTINGS",
        "KEYBOARD_SHORTCUTS", "QUICK_ACCESS", "RECENT_FILES",
        "TOOL_LIBRARY", "HOLDER_LIBRARY", "PROGRAM_TEMPLATES", "POSTS",
        "MACHINES", "MATERIALS", "MACHINE_CONFIG", "EXPORTABLE_SCHEMAS",
    )


def test_profile_bootstrap_creates_one_checksummed_default(
    paths: ApplicationPathsService,
) -> None:
    service = UserProfileService(paths)
    profile = service.bootstrap(locale="KO_KR")
    index = service.load_index()
    assert profile.display_name == "Mặc định"
    assert profile.locale == "KO_KR"
    assert index.active_profile_id == index.default_profile_id == profile.profile_id
    assert len(index.checksum) == 64
    directory = paths.path(AppPathKind.USER_PROFILES) / profile.profile_id
    assert directory.name == profile.profile_id
    assert tuple(sorted(path.name for path in directory.iterdir())) == tuple(sorted(PROFILE_FILE_NAMES))


def test_profiles_stay_below_roaming_not_project_or_programdata(
    paths: ApplicationPathsService,
    profiles: UserProfileService,
) -> None:
    root = paths.path(AppPathKind.USER_PROFILES)
    assert root.parent == paths.path(AppPathKind.USER_ROAMING_ROOT)
    assert paths.path(AppPathKind.PROGRAM_DATA_ROOT) not in root.parents
    assert not tuple(root.rglob("project.db"))


def test_profile_create_unicode_rename_preserves_physical_uuid(
    profiles: UserProfileService,
) -> None:
    profile = profiles.create("Người dùng A — 한국어", locale="KO_KR")
    physical = profiles.root / profile.profile_id
    renamed = profiles.rename(profile.profile_id, "Ca tối — Máy ROBODRILL")
    assert renamed.profile_id == profile.profile_id
    assert physical.is_dir()
    assert not (profiles.root / renamed.display_name).exists()


def test_profile_copy_gets_new_stable_id(profiles: UserProfileService) -> None:
    source = profiles.create("Ca sáng", locale="EN_US")
    copied = profiles.copy(source.profile_id, "Ca tối")
    assert copied.profile_id != source.profile_id
    assert copied.locale == source.locale
    assert copied.display_name == "Ca tối"


def test_profile_set_default_and_integrity(profiles: UserProfileService) -> None:
    second = profiles.create("Người dùng B")
    profiles.set_default(second.profile_id)
    index = profiles.load_index()
    assert index.default_profile_id == second.profile_id
    assert index.active_profile_id != second.profile_id


def test_profile_cannot_delete_last(paths: ApplicationPathsService) -> None:
    service = UserProfileService(paths)
    profile = service.bootstrap()
    with pytest.raises(ProfileError, match="final"):
        service.delete(profile.profile_id)


def test_profile_cannot_delete_active_without_replacement(
    profiles: UserProfileService,
) -> None:
    active = profiles.load_index().active_profile_id
    profiles.create("Người dùng B")
    with pytest.raises(ProfileError, match="replacement"):
        profiles.delete(active)


def test_profile_active_delete_with_replacement_keeps_integrity(
    profiles: UserProfileService,
) -> None:
    active = profiles.load_index().active_profile_id
    second = profiles.create("Người dùng B")
    profiles.delete(active, replacement_active_id=second.profile_id)
    index = profiles.load_index()
    assert index.active_profile_id == index.default_profile_id == second.profile_id


def test_corrupt_profile_index_is_preserved_before_fallback(
    paths: ApplicationPathsService,
) -> None:
    service = UserProfileService(paths)
    original = service.bootstrap()
    service.index_path.write_text("{broken", encoding="utf-8")
    recovered = service.bootstrap(locale="EN_US")
    assert recovered.profile_id != original.profile_id
    invalid = tuple(service.root.glob("profiles.invalid.*.json"))
    assert len(invalid) == 1 and invalid[0].read_text(encoding="utf-8") == "{broken"


def test_profile_switch_success_preserves_invariants_and_changes_active(
    profiles: UserProfileService,
) -> None:
    previous = profiles.load(profiles.load_index().active_profile_id)
    target = profiles.create("English", locale="EN_US")
    applied: list[str] = []
    report = profiles.switch(
        target.profile_id,
        capture_current=lambda value: value,
        apply_profile=lambda value: applied.append(value.profile_id),
        capture_invariants=lambda: ("MILL_2D", ("project", True, "selection")),
    )
    assert report.success and not report.rolled_back
    assert applied == [target.profile_id]
    assert profiles.load_index().active_profile_id == target.profile_id
    assert profiles.load_index().active_profile_id != previous.profile_id


def test_profile_switch_apply_failure_rolls_back_and_keeps_active(
    profiles: UserProfileService,
) -> None:
    previous_id = profiles.load_index().active_profile_id
    target = profiles.create("Broken")
    applied: list[str] = []

    def apply(profile: UserProfile) -> None:
        applied.append(profile.profile_id)
        if profile.profile_id == target.profile_id:
            raise ProfileError("apply failed")

    report = profiles.switch(
        target.profile_id,
        capture_current=lambda value: value,
        apply_profile=apply,
        capture_invariants=lambda: ("CAD", ("project", False)),
    )
    assert not report.success and report.rolled_back
    assert applied == [target.profile_id, previous_id]
    assert profiles.load_index().active_profile_id == previous_id


def test_profile_switch_invariant_mutation_rolls_back(profiles: UserProfileService) -> None:
    target = profiles.create("Mutation")
    state = {"calls": 0}

    def invariants():
        state["calls"] += 1
        return ("CAD" if state["calls"] == 1 else "MILL_2D", "project")

    report = profiles.switch(
        target.profile_id,
        capture_current=lambda value: value,
        apply_profile=lambda _value: None,
        capture_invariants=invariants,
    )
    assert not report.success and report.rolled_back


def test_category_selection_all_none_partial_and_recent_privacy(
    backup: HmsBackupService,
) -> None:
    model = BackupSelectionModel(backup.estimate_categories())
    assert BackupCategory.RECENT_FILES not in model.selected
    assert model.state in {SelectionState.PARTIAL, SelectionState.ALL}
    model.select_none()
    assert model.state is SelectionState.NONE
    model.select_all()
    assert model.state is SelectionState.ALL
    model.set_selected(BackupCategory.USER_INTERFACE, False)
    assert model.state is SelectionState.PARTIAL


def test_category_table_model_is_single_check_state_source(
    backup: HmsBackupService,
) -> None:
    model = BackupCategoryTableModel(tuple(backup.estimate_categories()))
    changes: list[tuple[int, int, list[object]]] = []
    model.dataChanged.connect(
        lambda top, bottom, roles: changes.append(
            (top.row(), bottom.row(), list(roles))
        )
    )
    model.select_none()
    index = model.index(
        next(
            row
            for row, item in enumerate(model.estimates)
            if item.category is BackupCategory.USER_INTERFACE
        ),
        0,
    )
    assert model.setData(index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert model.data(index, Qt.ItemDataRole.CheckStateRole) is Qt.CheckState.Checked
    assert model.selected == (
        BackupCategory.USER_PROFILES,
        BackupCategory.USER_INTERFACE,
    )
    assert changes and Qt.ItemDataRole.CheckStateRole in changes[-1][2]
    model.select_all()
    assert all(
        model.data(model.index(row, 0), Qt.ItemDataRole.CheckStateRole)
        in {Qt.CheckState.Checked, Qt.CheckState.Unchecked}
        for row in range(model.rowCount())
    )


def test_selecting_profile_component_adds_profile_dependency(
    backup: HmsBackupService,
) -> None:
    model = BackupSelectionModel(backup.estimate_categories())
    model.select_none()
    model.set_selected(BackupCategory.KEYBOARD_SHORTCUTS, True)
    assert BackupCategory.USER_PROFILES in model.selected
    model.set_selected(BackupCategory.USER_PROFILES, False)
    assert not set(model.selected) & {
        BackupCategory.USER_PROFILES,
        BackupCategory.KEYBOARD_SHORTCUTS,
    }


def test_empty_holder_category_visible_but_unavailable(
    backup: HmsBackupService,
) -> None:
    estimates = {item.category: item for item in backup.estimate_categories()}
    holder = estimates[BackupCategory.HOLDER_LIBRARY]
    assert not holder.available and not holder.selectable
    assert holder.diagnostic_code == "NO_DATA"


def test_pre_restore_backup_record_is_checksum_validated(
    paths: ApplicationPathsService,
) -> None:
    target = paths.path(AppPathKind.MATERIALS) / "pre-restore.json"
    target.write_bytes(b"before")
    service = PreRestoreBackupService(paths)
    record = service.create_backup(
        target,
        resource_id="MATERIALS:pre-restore",
        category=BackupCategory.MATERIALS.value,
        scope=BackupScope.MACHINE_SHARED.value,
        transaction_id=service.new_transaction_id(),
    )
    assert record is not None
    assert record.original_size == 6
    assert record.original_checksum == record.backup_checksum
    assert record.validation_status == "VALID"
    assert service.validate(record)
    assert service.restore_bytes(record) == b"before"


def test_size_estimate_and_resource_count_increase_with_data(
    backup: HmsBackupService,
    paths: ApplicationPathsService,
) -> None:
    before = {item.category: item for item in backup.estimate_categories()}
    (paths.path(AppPathKind.MATERIALS) / "steel.json").write_bytes(b"123456")
    after = {item.category: item for item in backup.estimate_categories()}
    assert after[BackupCategory.MATERIALS].estimated_size == 6
    assert after[BackupCategory.MATERIALS].resource_count == 1
    assert before[BackupCategory.MATERIALS].resource_count == 0


def test_backup_create_round_trip_unicode_space_and_manifest(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    path = _create_full_backup(backup, profiles, paths, tmp_path)
    validated = backup.validate(path)
    manifest = validated.manifest
    assert path.name == "Cấu hình xưởng A.BAKUPHMS"
    assert manifest.schema_version == manifest.format_version == 1
    assert manifest.application_family == "HMS-CADCAM"
    assert manifest.checksum_algorithm == "SHA-256"
    assert manifest.resource_count == len(manifest.resource_manifest)
    assert manifest.uncompressed_size > 0 and manifest.compressed_size > 0
    assert validated.compatibility is CompatibilityState.COMPATIBLE


def test_backup_archive_order_is_deterministic(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    path = _create_full_backup(backup, profiles, paths, tmp_path)
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
    assert names[:2] == ["manifest.json", "checksums.json"]
    assert names[2:] == sorted(names[2:], key=str.casefold)


def test_backup_manifest_has_explicit_user_and_machine_scopes(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    manifest = backup.validate(_create_full_backup(backup, profiles, paths, tmp_path)).manifest
    assert {item.scope for item in manifest.resource_manifest} == {
        BackupScope.USER_ROAMING,
        BackupScope.MACHINE_SHARED,
    }
    assert all(item.relative_path and item.logical_resource_id for item in manifest.resource_manifest)


@pytest.mark.parametrize("name", ("backup.zip", "backup", "backup.HMS"))
def test_backup_rejects_invalid_extension(
    backup: HmsBackupService,
    tmp_path: Path,
    name: str,
) -> None:
    with pytest.raises(BackupError, match="BAKUPHMS"):
        backup.create((tmp_path / name).resolve(), (BackupCategory.USER_PROFILES,))


def test_backup_existing_destination_requires_confirmation(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    destination = _backup_path(tmp_path)
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        backup.create(destination, (BackupCategory.USER_PROFILES,))
    assert destination.read_bytes() == b"keep"


def test_backup_confirmed_existing_destination_is_atomically_replaced(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    destination = _backup_path(tmp_path)
    destination.write_bytes(b"old")
    result = backup.create(
        destination,
        (BackupCategory.USER_PROFILES,),
        overwrite_confirmed=True,
    )
    assert result.file_size > 0
    assert backup.validate(destination).manifest.backup_id == result.manifest.backup_id
    assert destination.read_bytes() != b"old"


def test_backup_cancel_leaves_no_zero_byte_output(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    destination = _backup_path(tmp_path)
    with pytest.raises(BackupCancelled):
        backup.create(
            destination,
            (BackupCategory.USER_PROFILES,),
            cancelled=lambda: True,
        )
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_backup_write_failure_cleans_staging(
    backup: HmsBackupService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = _backup_path(tmp_path)
    monkeypatch.setattr(
        backup,
        "_write_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        backup.create(destination, (BackupCategory.USER_PROFILES,))
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_backup_does_not_include_forbidden_resources(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    root = paths.path(AppPathKind.MATERIALS)
    (root / "project.db").write_bytes(b"project")
    (root / "part.HMS").write_bytes(b"hms")
    (root / "program.nc").write_bytes(b"gcode")
    (root / "plugin.dll").write_bytes(b"binary")
    (root / "password-token.json").write_text('{"token":"secret"}', encoding="utf-8")
    (root / "valid.json").write_text('{"id":"valid"}', encoding="utf-8")
    manifest = backup.validate(_create_full_backup(backup, profiles, paths, tmp_path)).manifest
    names = {item.relative_path.casefold() for item in manifest.resource_manifest}
    assert any(name.endswith("valid.json") for name in names)
    assert not any(
        token in name
        for name in names
        for token in ("project.db", ".hms", ".nc", ".dll", "password", "token")
    )


def test_post_data_is_archived_but_never_executed(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "executed.txt"
    post = paths.path(AppPathKind.POSTS) / "unsafe.py"
    post.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n", encoding="utf-8")
    path = _backup_path(tmp_path)
    backup.create(path, (BackupCategory.POSTS,), profile_ids=())
    backup.validate(path)
    assert not marker.exists()


def test_damaged_archive_and_invalid_extension_fail_closed(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    damaged = _backup_path(tmp_path)
    damaged.write_bytes(b"not-a-zip")
    with pytest.raises(BackupValidationError, match="magic"):
        backup.validate(damaged)
    copy = tmp_path / "backup.zip"
    copy.write_bytes(damaged.read_bytes())
    with pytest.raises(BackupValidationError, match="extension"):
        backup.validate(copy)


def test_wrong_application_family_and_newer_version_report_typed_state(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    original = _create_full_backup(backup, profiles, paths, tmp_path)
    wrong = tmp_path / "wrong.BAKUPHMS"
    wrong.write_bytes(original.read_bytes())

    def wrong_family(entries):
        result = []
        for info, payload in entries:
            if info.filename == "manifest.json":
                value = json.loads(payload)
                value["application_family"] = "OTHER"
                payload = json.dumps(value).encode()
            result.append((info, payload))
        return result

    _rewrite_archive(wrong, wrong_family)
    assert backup.inspect(wrong).compatibility is CompatibilityState.WRONG_PRODUCT
    newer = tmp_path / "newer.BAKUPHMS"
    newer.write_bytes(original.read_bytes())

    def newer_version(entries):
        result = []
        for info, payload in entries:
            if info.filename == "manifest.json":
                value = json.loads(payload)
                value["format_version"] = BACKUP_FORMAT_VERSION + 1
                payload = json.dumps(value).encode()
            result.append((info, payload))
        return result

    _rewrite_archive(newer, newer_version)
    assert backup.inspect(newer).compatibility is CompatibilityState.NEWER_UNSUPPORTED


@pytest.mark.parametrize(
    "malicious_name",
    ("../escape.json", "/absolute.json", "C:/absolute.json", "data/CON.json", "data/trailing. "),
)
def test_archive_path_security_blocks_malicious_entries(
    backup: HmsBackupService,
    tmp_path: Path,
    malicious_name: str,
) -> None:
    path = _backup_path(tmp_path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("checksums.json", "{}")
        archive.writestr(malicious_name, "x")
    with pytest.raises(BackupValidationError):
        backup.validate(path)
    assert not (tmp_path.parent / "escape.json").exists()


def test_archive_symlink_metadata_is_blocked(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    path = _backup_path(tmp_path)
    info = zipfile.ZipInfo("profiles/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("checksums.json", "{}")
        archive.writestr(info, "target")
    with pytest.raises(BackupValidationError, match="Symlink"):
        backup.validate(path)


def test_archive_windows_reparse_metadata_is_blocked(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    path = _backup_path(tmp_path)
    info = zipfile.ZipInfo("profiles/reparse")
    info.create_system = 0
    info.external_attr = 0x400
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("checksums.json", "{}")
        archive.writestr(info, "target")
    with pytest.raises(BackupValidationError, match="reparse"):
        backup.validate(path)


@pytest.mark.parametrize(
    "entries",
    (
        (("data", "file"), ("data/child.json", "child")),
        (("data/child.json", "child"), ("data", "file")),
    ),
)
def test_archive_file_directory_collision_is_order_independent(
    backup: HmsBackupService,
    tmp_path: Path,
    entries: tuple[tuple[str, str], ...],
) -> None:
    path = _backup_path(tmp_path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("checksums.json", "{}")
        for name, payload in entries:
            archive.writestr(name, payload)
    with pytest.raises(BackupValidationError, match="collision"):
        backup.validate(path)


def test_archive_duplicate_and_case_collision_are_blocked(
    backup: HmsBackupService,
    tmp_path: Path,
) -> None:
    path = _backup_path(tmp_path)
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("checksums.json", "{}")
            archive.writestr("data/A.json", "1")
            archive.writestr("data/a.json", "2")
            archive.writestr("data/A.json", "3")
    with pytest.raises(BackupValidationError, match="Duplicate"):
        backup.validate(path)


def test_decompression_ratio_entry_count_and_total_size_limits(
    paths: ApplicationPathsService,
    profiles: UserProfileService,
    tmp_path: Path,
) -> None:
    path = _backup_path(tmp_path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("checksums.json", "{}")
        archive.writestr("data/bomb.txt", b"0" * 100_000)
    ratio = HmsBackupService(paths, profile_service=profiles, limits=BackupLimits(maximum_compression_ratio=2))
    with pytest.raises(BackupValidationError, match="ratio"):
        ratio.validate(path)
    count = HmsBackupService(paths, profile_service=profiles, limits=BackupLimits(maximum_entry_count=2))
    with pytest.raises(BackupValidationError, match="count"):
        count.validate(path)
    total = HmsBackupService(paths, profile_service=profiles, limits=BackupLimits(maximum_total_uncompressed_size=10))
    with pytest.raises(BackupValidationError, match="size"):
        total.validate(path)


def test_checksum_damage_is_detected(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    path = _create_full_backup(backup, profiles, paths, tmp_path)

    def corrupt(entries):
        result = []
        changed = False
        for info, payload in entries:
            if info.filename not in {"manifest.json", "checksums.json"} and not changed:
                payload += b"damage"
                changed = True
            result.append((info, payload))
        return result

    _rewrite_archive(path, corrupt)
    with pytest.raises(BackupValidationError, match="size|checksum"):
        backup.validate(path)


def test_restore_preview_defaults_to_keep_existing_conflict(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    source = paths.path(AppPathKind.MATERIALS) / "steel.json"
    source.write_text('{"id":"backup"}', encoding="utf-8")
    container = _backup_path(tmp_path)
    backup.create(container, (BackupCategory.MATERIALS,))
    source.write_text('{"id":"current"}', encoding="utf-8")
    plan = HmsRestoreService(paths, backup_service=backup, profile_service=profiles).preview(container)
    item = plan.items[0]
    assert item.conflict and item.action is ConflictAction.KEEP_EXISTING
    assert plan.conflict_count == plan.unresolved_conflict_count == 1


def test_restore_keep_existing_does_not_change_current_data(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    target = paths.path(AppPathKind.MATERIALS) / "steel.json"
    target.write_text("backup", encoding="utf-8")
    container = _backup_path(tmp_path)
    backup.create(container, (BackupCategory.MATERIALS,))
    target.write_text("current", encoding="utf-8")
    restore = HmsRestoreService(paths, backup_service=backup, profile_service=profiles)
    result = restore.restore(restore.preview(container))
    assert result.success and result.partial
    assert target.read_text(encoding="utf-8") == "current"


def test_restore_replace_creates_backup_and_keeps_container_unchanged(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    target = paths.path(AppPathKind.MATERIALS) / "steel.json"
    target.write_text("backup", encoding="utf-8")
    container = _backup_path(tmp_path)
    backup.create(container, (BackupCategory.MATERIALS,))
    source_hash = hashlib.sha256(container.read_bytes()).hexdigest()
    target.write_text("current", encoding="utf-8")
    restore = HmsRestoreService(paths, backup_service=backup, profile_service=profiles)
    preview = restore.preview(container)
    actions = {preview.items[0].entry.logical_resource_id: ConflictAction.REPLACE}
    plan = restore.preview(container, actions=actions)
    result = restore.restore(plan)
    assert result.success and result.restored_count == 1
    assert result.backup_before_restore_count == 1
    assert result.pre_restore_backup_checksum_mismatch_count == 0
    assert target.read_text(encoding="utf-8") == "backup"
    assert result.source_unchanged
    assert hashlib.sha256(container.read_bytes()).hexdigest() == source_hash


def test_restore_merge_allowed_and_disallowed(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    config = paths.path(AppPathKind.MACHINE_CONFIG) / "settings.json"
    config.write_text('{"a":1}', encoding="utf-8")
    config_backup = _backup_path(tmp_path, "config.BAKUPHMS")
    backup.create(config_backup, (BackupCategory.MACHINE_CONFIG,))
    config.write_text('{"b":2}', encoding="utf-8")
    restore = HmsRestoreService(paths, backup_service=backup, profile_service=profiles)
    initial = restore.preview(config_backup)
    conflict = next(item for item in initial.items if item.conflict)
    plan = restore.preview(
        config_backup,
        actions={conflict.entry.logical_resource_id: ConflictAction.MERGE},
    )
    assert restore.restore(plan).success
    assert json.loads(config.read_text(encoding="utf-8")) == {"a": 1, "b": 2}

    material = paths.path(AppPathKind.MATERIALS) / "steel.json"
    material.write_text('{"a":1}', encoding="utf-8")
    material_backup = _backup_path(tmp_path, "material.BAKUPHMS")
    backup.create(material_backup, (BackupCategory.MATERIALS,))
    material.write_text('{"b":2}', encoding="utf-8")
    raw = restore.preview(material_backup)
    conflict = next(item for item in raw.items if item.conflict)
    blocked = restore.preview(
        material_backup,
        actions={conflict.entry.logical_resource_id: ConflictAction.MERGE},
    )
    assert next(item for item in blocked.items if item.conflict).diagnostic_code == "MERGE_NOT_SUPPORTED"


def test_restore_permission_denied_machine_allows_user_profile(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _make_machine_resources(paths)
    container = _backup_path(tmp_path)
    active = profiles.load_index().active_profile_id
    backup.create(
        container,
        (BackupCategory.USER_PROFILES, BackupCategory.MATERIALS),
        profile_ids=(active,),
    )
    destination_paths = ApplicationPathsService.sandbox((tmp_path / "destination").resolve())
    destination_paths.path(AppPathKind.INSTALL_ROOT).mkdir(parents=True)
    StorageBootstrapService(destination_paths).bootstrap()
    destination_profiles = UserProfileService(destination_paths)
    destination_profiles.bootstrap()
    destination_backup = HmsBackupService(destination_paths, profile_service=destination_profiles)
    restore = HmsRestoreService(
        destination_paths,
        backup_service=destination_backup,
        profile_service=destination_profiles,
    )
    original = restore._target_writable
    monkeypatch.setattr(
        restore,
        "_target_writable",
        lambda entry, target: False if entry.scope is BackupScope.MACHINE_SHARED else original(entry, target),
    )
    plan = restore.preview(container)
    assert all(
        item.action is ConflictAction.SKIP
        for item in plan.items
        if item.permission_blocked
    )
    result = restore.restore(plan)
    assert result.success and result.partial
    assert result.permission_blocked_count >= 1
    assert len(destination_profiles.profiles()) == 2


def test_restore_profile_import_as_copy_not_automatically_active(
    backup: HmsBackupService,
    profiles: UserProfileService,
    tmp_path: Path,
) -> None:
    active_id = profiles.load_index().active_profile_id
    container = _backup_path(tmp_path)
    backup.create(
        container,
        (BackupCategory.USER_PROFILES, BackupCategory.USER_INTERFACE),
        profile_ids=(active_id,),
    )
    restore = HmsRestoreService(backup.paths, backup_service=backup, profile_service=profiles)
    initial = restore.preview(container)
    actions = {item.entry.logical_resource_id: ConflictAction.IMPORT_AS_COPY for item in initial.items}
    result = restore.restore(restore.preview(container, actions=actions))
    assert result.success
    assert len(profiles.profiles()) == 2
    assert profiles.load_index().active_profile_id == active_id


class _FailSecondWriter(AtomicBytesWriter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def write(self, root: Path, target: Path, payload: bytes) -> str:
        self.calls += 1
        if self.calls == 2:
            raise AtomicWriteError("simulated publish failure")
        return super().write(root, target, payload)


def test_restore_failure_rolls_back_only_planned_resources(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    root = paths.path(AppPathKind.MATERIALS)
    first = root / "a.json"
    second = root / "b.json"
    untouched = root / "untouched.json"
    first.write_text("backup-a", encoding="utf-8")
    second.write_text("backup-b", encoding="utf-8")
    container = _backup_path(tmp_path)
    backup.create(container, (BackupCategory.MATERIALS,))
    first.write_text("current-a", encoding="utf-8")
    second.write_text("current-b", encoding="utf-8")
    untouched.write_text("keep", encoding="utf-8")
    restore = HmsRestoreService(
        paths,
        backup_service=backup,
        profile_service=profiles,
        writer=_FailSecondWriter(),
    )
    initial = restore.preview(container)
    actions = {item.entry.logical_resource_id: ConflictAction.REPLACE for item in initial.items}
    result = restore.restore(restore.preview(container, actions=actions))
    assert not result.success
    assert result.rollback_failure_count == 0
    assert result.resource_published_before_failure_count >= 1
    assert result.rollback_attempted_resource_count >= 1
    assert result.rollback_restored_resource_count >= 1
    assert result.rollback_restored_checksum_mismatch_count == 0
    assert result.previous_data_preserved
    assert first.read_text(encoding="utf-8") == "current-a"
    assert second.read_text(encoding="utf-8") == "current-b"
    assert untouched.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("language", tuple(UiLanguage))
def test_backup_restore_profile_ui_three_locales_accessible(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
    language: UiLanguage,
) -> None:
    i18n_service.set_language(language)
    restore = HmsRestoreService(paths, backup_service=backup, profile_service=profiles)
    widgets = (
        BackupWizardDialog(backup, profiles),
        RestoreWizardDialog(restore),
        UserProfilesDialog(profiles),
    )
    for widget in widgets:
        widget.show()
        _application().processEvents()
        metrics = audit_widget(widget)
        assert metrics.missing_accessible_name_count == 0
        assert metrics.missing_accessible_description_count == 0
        assert metrics.horizontal_scroll_count == 0
        assert metrics.tofu_count == 0
        assert not any(_is_mixed(text, language) for text in metrics.texts)
        widget.close()
    assert not i18n_service.diagnostics


def test_backup_wizard_selection_destination_and_success(
    backup: HmsBackupService,
    profiles: UserProfileService,
    tmp_path: Path,
) -> None:
    dialog = BackupWizardDialog(backup, profiles)
    dialog.selection.select_none()
    dialog.select_category(BackupCategory.USER_PROFILES, True)
    destination = _backup_path(tmp_path, "Người dùng A backup.BAKUPHMS")
    dialog.set_destination(destination)
    result = dialog.execute_synchronously()
    assert result.path == destination and destination.is_file()
    assert dialog.stack.currentIndex() == 4
    dialog.close()


@pytest.mark.parametrize(
    ("language", "back", "next_text"),
    (
        (UiLanguage.VI_VN, "Quay lại", "Tiếp tục"),
        (UiLanguage.EN_US, "Back", "Next"),
        (UiLanguage.KO_KR, "뒤로", "다음"),
    ),
)
def test_wizard_navigation_uses_semantic_labels(
    backup: HmsBackupService,
    profiles: UserProfileService,
    i18n_service: TranslationService,
    language: UiLanguage,
    back: str,
    next_text: str,
) -> None:
    i18n_service.set_language(language)
    dialog = BackupWizardDialog(backup, profiles)
    assert dialog.back_button.text() == back
    assert dialog.next_button.text() == next_text
    assert dialog.back_button.accessibleName() == back
    assert dialog.next_button.accessibleName() == next_text
    assert dialog.back_button.property("wizardSemanticKey") == "wizard.back"
    assert dialog.next_button.property("wizardSemanticKey") == "wizard.next"
    dialog.close()


def test_restore_wizard_never_restores_on_file_selection(
    backup: HmsBackupService,
    profiles: UserProfileService,
    paths: ApplicationPathsService,
    tmp_path: Path,
) -> None:
    target = paths.path(AppPathKind.MATERIALS) / "steel.json"
    target.write_text("backup", encoding="utf-8")
    container = _backup_path(tmp_path)
    backup.create(container, (BackupCategory.MATERIALS,))
    target.write_text("current", encoding="utf-8")
    dialog = RestoreWizardDialog(
        HmsRestoreService(paths, backup_service=backup, profile_service=profiles)
    )
    inspection = dialog.load_backup(container)
    assert inspection.valid and dialog.plan is not None
    assert target.read_text(encoding="utf-8") == "current"
    dialog.close()


def test_user_profiles_dialog_crud_and_default(
    profiles: UserProfileService,
) -> None:
    dialog = UserProfilesDialog(profiles)
    created = dialog.create_profile("Người dùng B", locale="EN_US")
    copied = dialog.copy_profile(created.profile_id, "Người dùng C")
    dialog.rename_profile(copied.profile_id, "Ca tối")
    dialog.set_default_profile(created.profile_id)
    assert dialog.table.rowCount() == 3
    assert profiles.load_index().default_profile_id == created.profile_id
    dialog.delete_profile(copied.profile_id)
    assert dialog.table.rowCount() == 2
    dialog.close()


def test_main_window_profile_switch_preserves_workspace_project_and_docks(
    paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    reason = "CAD rendering backend is unavailable."
    project_service = ProjectService.create_default(tmp_path / "config")
    window = MainWindow(
        project_service,
        UnavailableCadKernel(reason),
        UnavailableCadViewportBackend(reason),
        layout_store=WorkspaceLayoutStore(
            QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
        ),
        application_paths=paths,
        storage_bootstrap=StorageBootstrapService(paths),
    )
    profiles = window._user_profile_service
    assert profiles is not None
    target = profiles.create("English", locale="EN_US")
    before = window._profile_switch_invariants()
    dock_ids = tuple(id(item) for item in (window.project_dock, window.operation_manager_dock, window.properties_dock, window.secondary_dock))
    report = window._switch_user_profile(target.profile_id)
    assert report.success
    assert window._profile_switch_invariants() == before
    assert tuple(id(item) for item in (window.project_dock, window.operation_manager_dock, window.properties_dock, window.secondary_dock)) == dock_ids
    assert i18n_service.language is UiLanguage.EN_US
    window.close()


def test_main_window_invalid_shortcut_rolls_back_profile(
    paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    reason = "CAD rendering backend is unavailable."
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel(reason),
        UnavailableCadViewportBackend(reason),
        layout_store=WorkspaceLayoutStore(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)),
        application_paths=paths,
        storage_bootstrap=StorageBootstrapService(paths),
    )
    profiles = window._user_profile_service
    assert profiles is not None
    target = profiles.create("Broken")
    profiles.save(replace(target, shortcuts={"project.open": "Ctrl+Q"}))
    previous = profiles.load_index().active_profile_id
    report = window._switch_user_profile(target.profile_id)
    assert not report.success and report.rolled_back
    assert profiles.load_index().active_profile_id == previous
    window.close()


def test_quick_access_unknown_command_is_skipped_without_raw_id(
    paths: ApplicationPathsService,
    tmp_path: Path,
    i18n_service: TranslationService,
) -> None:
    reason = "CAD rendering backend is unavailable."
    window = MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel(reason),
        UnavailableCadViewportBackend(reason),
        layout_store=WorkspaceLayoutStore(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)),
        application_paths=paths,
        storage_bootstrap=StorageBootstrapService(paths),
    )
    profiles = window._user_profile_service
    assert profiles is not None
    target = profiles.create("Quick")
    profiles.save(replace(target, quick_access=("missing.command",)))
    before = tuple(window._quick_access_toolbar.actions())
    report = window._switch_user_profile(target.profile_id)
    assert report.success
    assert tuple(window._quick_access_toolbar.actions()) == before
    assert "missing.command" not in " ".join(action.text() for action in before)
    window.close()
