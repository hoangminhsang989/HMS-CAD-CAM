"""Versioned per-user HMS interface profiles below Roaming AppData."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_io import (
    AtomicBytesWriter,
    AtomicJsonWriter,
    AtomicWriteError,
    checksum_value,
)
from hms_cadcam.core.storage_security import validate_storage_write_path


LOGGER = logging.getLogger(__name__)
PROFILE_SCHEMA_VERSION = 1
PROFILE_INDEX_FILENAME = "profiles.json"
PROFILE_FILE_NAMES = (
    "profile.json",
    "ui-state.json",
    "shortcuts.json",
    "quick-access.json",
    "preferences.json",
    "recent-files.json",
)
SUPPORTED_PROFILE_LOCALES = frozenset({"VI_VN", "EN_US", "KO_KR"})


class ProfileError(RuntimeError):
    """Profile persistence or integrity failed closed."""


@dataclass(frozen=True, slots=True)
class UserProfile:
    profile_id: str
    display_name: str
    created_at_utc: str
    updated_at_utc: str
    locale: str
    ui_state: Mapping[str, object]
    shortcuts: Mapping[str, str]
    quick_access: tuple[str, ...]
    preferences: Mapping[str, object]
    recent_files: tuple[str, ...]
    appearance: Mapping[str, object]
    layout_description: str = ""

    @classmethod
    def create(
        cls,
        display_name: str,
        *,
        locale: str = "VI_VN",
        ui_state: Mapping[str, object] | None = None,
        shortcuts: Mapping[str, str] | None = None,
        quick_access: Sequence[str] = (),
        preferences: Mapping[str, object] | None = None,
        recent_files: Sequence[str] = (),
        appearance: Mapping[str, object] | None = None,
        layout_description: str = "",
        profile_id: str | None = None,
    ) -> "UserProfile":
        timestamp = datetime.now(timezone.utc).isoformat()
        return cls(
            profile_id=_profile_id(profile_id or str(uuid4())),
            display_name=_display_name(display_name),
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            locale=_locale(locale),
            ui_state=_json_mapping(ui_state or {}),
            shortcuts=_shortcut_mapping(shortcuts or {}),
            quick_access=_command_ids(quick_access),
            preferences=_json_mapping(preferences or {}),
            recent_files=tuple(str(item) for item in recent_files),
            appearance=_json_mapping(appearance or {}),
            layout_description=str(layout_description).strip(),
        )

    def copy_as(self, display_name: str) -> "UserProfile":
        copied = UserProfile.create(
            display_name,
            locale=self.locale,
            ui_state=self.ui_state,
            shortcuts=self.shortcuts,
            quick_access=self.quick_access,
            preferences=self.preferences,
            recent_files=self.recent_files,
            appearance=self.appearance,
            layout_description=self.layout_description,
        )
        return copied


@dataclass(frozen=True, slots=True)
class ProfileIndexEntry:
    profile_id: str
    display_name: str
    locale: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class ProfilesIndex:
    schema_version: int
    active_profile_id: str
    default_profile_id: str
    profiles: tuple[ProfileIndexEntry, ...]
    checksum: str

    @classmethod
    def create(
        cls,
        profiles: Sequence[UserProfile],
        *,
        active_profile_id: str,
        default_profile_id: str,
    ) -> "ProfilesIndex":
        entries = tuple(
            sorted(
                (
                    ProfileIndexEntry(
                        profile.profile_id,
                        profile.display_name,
                        profile.locale,
                        profile.updated_at_utc,
                    )
                    for profile in profiles
                ),
                key=lambda item: item.profile_id,
            )
        )
        ids = {entry.profile_id for entry in entries}
        active = _profile_id(active_profile_id)
        default = _profile_id(default_profile_id)
        if not entries or active not in ids or default not in ids:
            raise ProfileError("Active/default profile integrity failed")
        body = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "active_profile_id": active,
            "default_profile_id": default,
            "profiles": [_entry_dict(item) for item in entries],
        }
        return cls(
            PROFILE_SCHEMA_VERSION,
            active,
            default,
            entries,
            checksum_value(body),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "active_profile_id": self.active_profile_id,
            "default_profile_id": self.default_profile_id,
            "profiles": [_entry_dict(item) for item in self.profiles],
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProfilesIndex":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "active_profile_id",
            "default_profile_id",
            "profiles",
            "checksum",
        }:
            raise ProfileError("Profile index fields mismatch")
        if value["schema_version"] != PROFILE_SCHEMA_VERSION:
            raise ProfileError("Unsupported profile index version")
        raw_profiles = value["profiles"]
        if not isinstance(raw_profiles, list):
            raise ProfileError("Profile index list is invalid")
        entries: list[ProfileIndexEntry] = []
        for raw in raw_profiles:
            if not isinstance(raw, dict) or set(raw) != {
                "profile_id", "display_name", "locale", "updated_at_utc"
            }:
                raise ProfileError("Profile index entry is invalid")
            entries.append(
                ProfileIndexEntry(
                    _profile_id(str(raw["profile_id"])),
                    _display_name(str(raw["display_name"])),
                    _locale(str(raw["locale"])),
                    _timestamp(str(raw["updated_at_utc"])),
                )
            )
        if len({item.profile_id for item in entries}) != len(entries):
            raise ProfileError("Duplicate profile ID")
        index = cls(
            PROFILE_SCHEMA_VERSION,
            _profile_id(str(value["active_profile_id"])),
            _profile_id(str(value["default_profile_id"])),
            tuple(entries),
            str(value["checksum"]),
        )
        body = index.to_dict()
        body.pop("checksum")
        if checksum_value(body) != index.checksum:
            raise ProfileError("Profile index checksum mismatch")
        ids = {item.profile_id for item in index.profiles}
        if not ids or index.active_profile_id not in ids or index.default_profile_id not in ids:
            raise ProfileError("Profile active/default reference is invalid")
        return index


@dataclass(frozen=True, slots=True)
class ProfileSwitchReport:
    success: bool
    previous_profile_id: str
    target_profile_id: str
    rolled_back: bool
    workspace_mutation_count: int
    project_mutation_count: int
    diagnostics: tuple[str, ...]


class UserProfileService:
    """Own profile CRUD and runtime switching without project side effects."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        json_writer: AtomicJsonWriter | None = None,
        bytes_writer: AtomicBytesWriter | None = None,
    ) -> None:
        self.paths = paths
        self.root = paths.path(AppPathKind.USER_PROFILES)
        self._roaming_root = paths.path(AppPathKind.USER_ROAMING_ROOT)
        self._json_writer = json_writer or AtomicJsonWriter()
        self._bytes_writer = bytes_writer or AtomicBytesWriter()

    @property
    def index_path(self) -> Path:
        return self.root / PROFILE_INDEX_FILENAME

    def bootstrap(
        self,
        *,
        locale: str = "VI_VN",
        ui_state: Mapping[str, object] | None = None,
    ) -> UserProfile:
        self._ensure_root()
        if self.index_path.is_file():
            try:
                index = self.load_index()
                return self.load(index.active_profile_id)
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, ProfileError) as exc:
                recovery = self.root / f"profiles.invalid.{uuid4().hex}.json"
                self._bytes_writer.write(
                    self._roaming_root,
                    recovery,
                    self.index_path.read_bytes(),
                )
                LOGGER.error("Profile index hỏng đã được bảo toàn tại %s: %s", recovery, exc)
        profile = UserProfile.create(
            "Mặc định",
            locale=locale,
            ui_state=ui_state,
            quick_access=("project.new", "project.open", "project.save"),
            layout_description="Bố cục HMS mặc định",
        )
        self._write_profile_files(profile)
        self._write_index(ProfilesIndex.create(
            (profile,),
            active_profile_id=profile.profile_id,
            default_profile_id=profile.profile_id,
        ))
        return profile

    def load_index(self) -> ProfilesIndex:
        return ProfilesIndex.from_dict(
            json.loads(self.index_path.read_text(encoding="utf-8"))
        )

    def profiles(self) -> tuple[UserProfile, ...]:
        index = self.load_index()
        profiles = tuple(self.load(item.profile_id) for item in index.profiles)
        if {item.profile_id for item in profiles} != {
            item.profile_id for item in index.profiles
        }:
            raise ProfileError("Profile index/directory mismatch")
        return profiles

    def load(self, profile_id: str) -> UserProfile:
        identifier = _profile_id(profile_id)
        directory = self.root / identifier
        documents = {
            name: _read_checked_document(directory / name, identifier)
            for name in PROFILE_FILE_NAMES
        }
        metadata = documents["profile.json"]
        return UserProfile(
            profile_id=identifier,
            display_name=_display_name(str(metadata["display_name"])),
            created_at_utc=_timestamp(str(metadata["created_at_utc"])),
            updated_at_utc=_timestamp(str(metadata["updated_at_utc"])),
            locale=_locale(str(metadata["locale"])),
            ui_state=_json_mapping(documents["ui-state.json"]),
            shortcuts=_shortcut_mapping(documents["shortcuts.json"]),
            quick_access=_command_ids(_list_value(documents["quick-access.json"])),
            preferences=_json_mapping(documents["preferences.json"].get("settings", {})),
            recent_files=tuple(str(item) for item in _list_value(documents["recent-files.json"])),
            appearance=_json_mapping(documents["preferences.json"].get("appearance", {})),
            layout_description=str(metadata.get("layout_description", "")),
        )

    def create(self, display_name: str, *, locale: str = "VI_VN") -> UserProfile:
        self.bootstrap(locale=locale)
        profile = UserProfile.create(display_name, locale=locale)
        self._add(profile)
        return profile

    def copy(self, profile_id: str, display_name: str) -> UserProfile:
        copied = self.load(profile_id).copy_as(display_name)
        self._add(copied)
        return copied

    def rename(self, profile_id: str, display_name: str) -> UserProfile:
        profile = self.load(profile_id)
        updated = replace(
            profile,
            display_name=_display_name(display_name),
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        self.save(updated)
        return updated

    def save(self, profile: UserProfile) -> None:
        index = self.load_index()
        if profile.profile_id not in {item.profile_id for item in index.profiles}:
            raise ProfileError("Cannot save a profile outside the index")
        snapshots = self._snapshot_profile(profile.profile_id)
        try:
            self._write_profile_files(profile)
            profiles = tuple(
                profile if item.profile_id == profile.profile_id else self.load(item.profile_id)
                for item in index.profiles
            )
            self._write_index(ProfilesIndex.create(
                profiles,
                active_profile_id=index.active_profile_id,
                default_profile_id=index.default_profile_id,
            ))
        except (OSError, ValueError, TypeError, ProfileError, AtomicWriteError):
            self._restore_snapshot(profile.profile_id, snapshots)
            raise

    def set_default(self, profile_id: str) -> ProfilesIndex:
        index = self.load_index()
        identifier = _profile_id(profile_id)
        if identifier not in {item.profile_id for item in index.profiles}:
            raise ProfileError("Default profile does not exist")
        updated = ProfilesIndex.create(
            self.profiles(),
            active_profile_id=index.active_profile_id,
            default_profile_id=identifier,
        )
        self._write_index(updated)
        return updated

    def import_profile(
        self,
        profile: UserProfile,
        *,
        as_copy: bool = False,
    ) -> UserProfile:
        """Register a validated backup profile without activating it."""
        self.bootstrap(locale=profile.locale)
        imported = (
            profile.copy_as(f"{profile.display_name} — bản sao")
            if as_copy
            else profile
        )
        identifiers = {item.profile_id for item in self.load_index().profiles}
        if imported.profile_id in identifiers:
            raise ProfileError("Restored profile ID already exists")
        self._add(imported)
        return imported

    def delete(self, profile_id: str, *, replacement_active_id: str | None = None) -> None:
        index = self.load_index()
        identifier = _profile_id(profile_id)
        ids = {item.profile_id for item in index.profiles}
        if identifier not in ids:
            raise ProfileError("Profile does not exist")
        if len(ids) == 1:
            raise ProfileError("Cannot delete the final profile")
        active = index.active_profile_id
        if identifier == active:
            if replacement_active_id is None:
                raise ProfileError("Active profile requires a replacement")
            active = _profile_id(replacement_active_id)
            if active == identifier or active not in ids:
                raise ProfileError("Invalid replacement profile")
        default = index.default_profile_id
        if identifier == default:
            default = active
        remaining = tuple(profile for profile in self.profiles() if profile.profile_id != identifier)
        self._write_index(ProfilesIndex.create(
            remaining,
            active_profile_id=active,
            default_profile_id=default,
        ))
        directory = self.root / identifier
        if directory.is_symlink() or not directory.is_dir():
            raise ProfileError("Unsafe profile directory")
        shutil.rmtree(directory)

    def switch(
        self,
        target_profile_id: str,
        *,
        capture_current: Callable[[UserProfile], UserProfile],
        apply_profile: Callable[[UserProfile], None],
        capture_invariants: Callable[[], tuple[object, object]],
    ) -> ProfileSwitchReport:
        index = self.load_index()
        previous = self.load(index.active_profile_id)
        target = self.load(target_profile_id)
        if target.profile_id == previous.profile_id:
            return ProfileSwitchReport(True, previous.profile_id, target.profile_id, False, 0, 0, ())
        live_previous = capture_current(previous)
        self.save(live_previous)
        workspace_before, project_before = capture_invariants()
        try:
            apply_profile(target)
            workspace_after, project_after = capture_invariants()
            workspace_mutation = int(workspace_before != workspace_after)
            project_mutation = int(project_before != project_after)
            if workspace_mutation or project_mutation:
                raise ProfileError("Profile switch mutated workspace/project semantics")
            self._write_index(ProfilesIndex.create(
                self.profiles(),
                active_profile_id=target.profile_id,
                default_profile_id=index.default_profile_id,
            ))
            return ProfileSwitchReport(True, previous.profile_id, target.profile_id, False, 0, 0, ())
        except (OSError, RuntimeError, ValueError, TypeError, ProfileError) as exc:
            try:
                apply_profile(live_previous)
                rolled_back = True
            except (OSError, RuntimeError, ValueError, TypeError, ProfileError):
                rolled_back = False
            return ProfileSwitchReport(
                False,
                previous.profile_id,
                target.profile_id,
                rolled_back,
                int(capture_invariants()[0] != workspace_before),
                int(capture_invariants()[1] != project_before),
                (str(exc),),
            )

    def _add(self, profile: UserProfile) -> None:
        index = self.load_index()
        if profile.profile_id in {item.profile_id for item in index.profiles}:
            raise ProfileError("Duplicate profile ID")
        self._write_profile_files(profile)
        try:
            self._write_index(ProfilesIndex.create(
                (*self.profiles(), profile),
                active_profile_id=index.active_profile_id,
                default_profile_id=index.default_profile_id,
            ))
        except (OSError, ValueError, TypeError, ProfileError, AtomicWriteError):
            shutil.rmtree(self.root / profile.profile_id, ignore_errors=True)
            raise

    def _ensure_root(self) -> None:
        validation = validate_storage_write_path(
            self._roaming_root,
            self.root,
            expect_directory=True,
        )
        if not validation.safe:
            raise ProfileError(f"Unsafe profile root: {validation.code.value}")
        self.root.mkdir(parents=True, exist_ok=True)

    def _write_profile_files(self, profile: UserProfile) -> None:
        self._ensure_root()
        directory = self.root / _profile_id(profile.profile_id)
        validation = validate_storage_write_path(self.root, directory, expect_directory=True)
        if not validation.safe:
            raise ProfileError(f"Unsafe profile directory: {validation.code.value}")
        directory.mkdir(exist_ok=True)
        documents = _profile_documents(profile)
        for filename in PROFILE_FILE_NAMES:
            body = {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "profile_id": profile.profile_id,
                "payload": documents[filename],
            }
            document = {**body, "checksum": checksum_value(body)}
            self._json_writer.write(self._roaming_root, directory / filename, document)
        loaded = self.load(profile.profile_id)
        if loaded.profile_id != profile.profile_id or loaded.display_name != profile.display_name:
            raise ProfileError("Profile read-after-write validation failed")

    def _write_index(self, index: ProfilesIndex) -> None:
        self._json_writer.write(self._roaming_root, self.index_path, index.to_dict())
        if self.load_index() != index:
            raise ProfileError("Profile index read-after-write validation failed")

    def _snapshot_profile(self, profile_id: str) -> dict[str, bytes | None]:
        directory = self.root / _profile_id(profile_id)
        return {
            filename: (directory / filename).read_bytes() if (directory / filename).is_file() else None
            for filename in PROFILE_FILE_NAMES
        }

    def _restore_snapshot(self, profile_id: str, snapshot: Mapping[str, bytes | None]) -> None:
        directory = self.root / _profile_id(profile_id)
        for filename, payload in snapshot.items():
            target = directory / filename
            if payload is None:
                target.unlink(missing_ok=True)
            else:
                self._bytes_writer.write(self._roaming_root, target, payload)


def _profile_documents(profile: UserProfile) -> dict[str, object]:
    return {
        "profile.json": {
            "display_name": profile.display_name,
            "created_at_utc": profile.created_at_utc,
            "updated_at_utc": profile.updated_at_utc,
            "locale": profile.locale,
            "layout_description": profile.layout_description,
        },
        "ui-state.json": dict(profile.ui_state),
        "shortcuts.json": dict(profile.shortcuts),
        "quick-access.json": list(profile.quick_access),
        "preferences.json": {
            "settings": dict(profile.preferences),
            "appearance": dict(profile.appearance),
        },
        "recent-files.json": list(profile.recent_files),
    }


def _read_checked_document(path: Path, profile_id: str) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return _checked_document_value(value, profile_id, path.name)


def _checked_document_value(
    value: object,
    profile_id: str,
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "profile_id", "payload", "checksum"
    }:
        raise ProfileError(f"Profile document fields mismatch: {label}")
    if value["schema_version"] != PROFILE_SCHEMA_VERSION or value["profile_id"] != profile_id:
        raise ProfileError(f"Profile document identity mismatch: {label}")
    body = dict(value)
    checksum = str(body.pop("checksum"))
    if checksum_value(body) != checksum:
        raise ProfileError(f"Profile document checksum mismatch: {label}")
    payload = value["payload"]
    if isinstance(payload, dict):
        return _json_mapping(payload)
    if isinstance(payload, list):
        return {"items": list(payload)}
    raise ProfileError(f"Profile payload type mismatch: {label}")


def profile_from_backup_documents(
    profile_id: str,
    documents: Mapping[str, bytes],
) -> UserProfile:
    """Build one profile from checksummed component documents in memory."""
    identifier = _profile_id(profile_id)
    decoded: dict[str, Mapping[str, object]] = {}
    for filename, payload in documents.items():
        if filename not in PROFILE_FILE_NAMES:
            raise ProfileError("Unknown profile backup component")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProfileError("Malformed profile backup component") from exc
        decoded[filename] = _checked_document_value(value, identifier, filename)
    if "profile.json" not in decoded:
        raise ProfileError("Restored profile metadata is required")
    metadata = decoded["profile.json"]
    preferences = decoded.get("preferences.json", {})
    return UserProfile(
        profile_id=identifier,
        display_name=_display_name(str(metadata["display_name"])),
        created_at_utc=_timestamp(str(metadata["created_at_utc"])),
        updated_at_utc=_timestamp(str(metadata["updated_at_utc"])),
        locale=_locale(str(metadata["locale"])),
        ui_state=_json_mapping(decoded.get("ui-state.json", {})),
        shortcuts=_shortcut_mapping(decoded.get("shortcuts.json", {})),
        quick_access=_command_ids(
            _list_value(decoded.get("quick-access.json", {"items": []}))
        ),
        preferences=_json_mapping(preferences.get("settings", {})),
        recent_files=tuple(
            str(item)
            for item in _list_value(
                decoded.get("recent-files.json", {"items": []})
            )
        ),
        appearance=_json_mapping(preferences.get("appearance", {})),
        layout_description=str(metadata.get("layout_description", "")),
    )


def _list_value(value: Mapping[str, object]) -> list[object]:
    items = value.get("items", [])
    if not isinstance(items, list):
        raise ProfileError("Profile list payload is invalid")
    return items


def _entry_dict(entry: ProfileIndexEntry) -> dict[str, str]:
    return {
        "profile_id": entry.profile_id,
        "display_name": entry.display_name,
        "locale": entry.locale,
        "updated_at_utc": entry.updated_at_utc,
    }


def _profile_id(value: str) -> str:
    try:
        parsed = UUID(str(value))
    except ValueError as exc:
        raise ProfileError("Profile ID must be a UUID") from exc
    canonical = str(parsed)
    if canonical != str(value).lower():
        raise ProfileError("Profile ID must use canonical UUID form")
    return canonical


def _display_name(value: str) -> str:
    name = str(value).strip()
    if not name or len(name) > 120 or any(ord(character) < 32 for character in name):
        raise ProfileError("Profile display name is invalid")
    return name


def _locale(value: str) -> str:
    selected = str(value)
    if selected not in SUPPORTED_PROFILE_LOCALES:
        raise ProfileError("Unsupported profile locale")
    return selected


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ProfileError("Profile timestamp requires timezone")
    return value


def _json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ProfileError("Profile payload must be an object")
    try:
        normalized = json.loads(json.dumps(dict(value), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ProfileError("Profile payload is not JSON-safe") from exc
    if not isinstance(normalized, dict):
        raise ProfileError("Profile payload must remain an object")
    return normalized


def _shortcut_mapping(value: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for command_id, shortcut in value.items():
        key = str(command_id).strip()
        sequence = str(shortcut).strip()
        if not key or not sequence:
            raise ProfileError("Shortcut entry is invalid")
        normalized[key] = sequence
    return normalized


def _command_ids(value: Sequence[object]) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(str(item).strip() for item in value))
    if any(not item for item in result):
        raise ProfileError("Quick Access command ID is invalid")
    return result


__all__ = [
    "PROFILE_FILE_NAMES",
    "PROFILE_INDEX_FILENAME",
    "PROFILE_SCHEMA_VERSION",
    "ProfileError",
    "ProfileIndexEntry",
    "ProfileSwitchReport",
    "ProfilesIndex",
    "UserProfile",
    "UserProfileService",
    "profile_from_backup_documents",
]
