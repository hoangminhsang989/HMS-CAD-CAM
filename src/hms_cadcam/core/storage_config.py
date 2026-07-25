"""Machine/user/built-in configuration precedence and atomic persistence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from pathlib import Path
from typing import Mapping

from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_backup import MachineBackupService
from hms_cadcam.core.storage_io import (
    AtomicBytesWriter,
    AtomicJsonWriter,
    AtomicWriteError,
    MachineResource,
    ResourceFileLock,
    checksum_value,
)

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA_VERSION = 1
MACHINE_CONFIG_FILENAME = "machine-config.json"
USER_PREFERENCES_FILENAME = "preferences.json"


class ConfigurationSource(StrEnum):
    USER_PREFERENCE = "USER_PREFERENCE"
    MACHINE_WIDE = "MACHINE_WIDE"
    BUILTIN_DEFAULT = "BUILTIN_DEFAULT"
    CODE_FALLBACK = "CODE_FALLBACK"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ConfigurationDocument:
    schema_version: int
    document_version: int
    values: Mapping[str, object]
    locked_keys: tuple[str, ...]
    checksum: str

    @classmethod
    def create(
        cls,
        values: Mapping[str, object],
        *,
        document_version: int = 1,
        locked_keys: tuple[str, ...] = (),
    ) -> "ConfigurationDocument":
        normalized = _validate_values(values)
        locked = tuple(sorted(dict.fromkeys(str(key) for key in locked_keys)))
        body = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "document_version": int(document_version),
            "values": normalized,
            "locked_keys": locked,
        }
        return cls(
            schema_version=CONFIG_SCHEMA_VERSION,
            document_version=int(document_version),
            values=normalized,
            locked_keys=locked,
            checksum=checksum_value(body),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "document_version": self.document_version,
            "values": dict(self.values),
            "locked_keys": list(self.locked_keys),
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ConfigurationDocument":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "document_version",
            "values",
            "locked_keys",
            "checksum",
        }:
            raise ValueError("Configuration document fields mismatch")
        if int(value["schema_version"]) != CONFIG_SCHEMA_VERSION:
            raise ValueError("Unsupported configuration schema")
        locked = value["locked_keys"]
        if not isinstance(locked, list) or not all(isinstance(item, str) for item in locked):
            raise TypeError("locked_keys must be a string list")
        document = cls(
            schema_version=CONFIG_SCHEMA_VERSION,
            document_version=int(value["document_version"]),
            values=_validate_values(value["values"]),
            locked_keys=tuple(locked),
            checksum=str(value["checksum"]),
        )
        body = document.to_dict()
        body.pop("checksum")
        if checksum_value(body) != document.checksum:
            raise ValueError("Configuration checksum mismatch")
        return document


@dataclass(frozen=True, slots=True)
class ResolvedConfigurationValue:
    key: str
    value: object | None
    source: ConfigurationSource
    machine_locked: bool
    diagnostic_code: str


class ConfigurationService:
    """Resolve valid values in user → machine → built-in → code order."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        builtin_defaults: Mapping[str, object] | None = None,
        code_fallbacks: Mapping[str, object] | None = None,
        backup_service: MachineBackupService | None = None,
        json_writer: AtomicJsonWriter | None = None,
        bytes_writer: AtomicBytesWriter | None = None,
    ) -> None:
        self.paths = paths
        self._builtin = _validate_values(builtin_defaults or {})
        self._fallbacks = _validate_values(code_fallbacks or {})
        self._backup = backup_service or MachineBackupService(paths)
        self._json_writer = json_writer or AtomicJsonWriter()
        self._bytes_writer = bytes_writer or AtomicBytesWriter()
        self._diagnostics: list[str] = []

    @property
    def machine_path(self) -> Path:
        return self.paths.path(AppPathKind.MACHINE_CONFIG) / MACHINE_CONFIG_FILENAME

    @property
    def user_path(self) -> Path:
        return self.paths.path(AppPathKind.USER_CONFIG) / USER_PREFERENCES_FILENAME

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def resolve(self, key: str) -> ResolvedConfigurationValue:
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError("Configuration key cannot be empty")
        machine = self._read(self.machine_path, "MACHINE_CONFIG_INVALID")
        user = self._read(self.user_path, "USER_CONFIG_INVALID")
        locked = machine is not None and normalized_key in machine.locked_keys
        if not locked and user is not None and normalized_key in user.values:
            return ResolvedConfigurationValue(
                normalized_key,
                user.values[normalized_key],
                ConfigurationSource.USER_PREFERENCE,
                False,
                "USER_VALUE",
            )
        if machine is not None and normalized_key in machine.values:
            return ResolvedConfigurationValue(
                normalized_key,
                machine.values[normalized_key],
                ConfigurationSource.MACHINE_WIDE,
                locked,
                "MACHINE_LOCKED" if locked else "MACHINE_VALUE",
            )
        if normalized_key in self._builtin:
            return ResolvedConfigurationValue(
                normalized_key,
                self._builtin[normalized_key],
                ConfigurationSource.BUILTIN_DEFAULT,
                locked,
                "BUILTIN_VALUE",
            )
        if normalized_key in self._fallbacks:
            return ResolvedConfigurationValue(
                normalized_key,
                self._fallbacks[normalized_key],
                ConfigurationSource.CODE_FALLBACK,
                locked,
                "CODE_FALLBACK",
            )
        return ResolvedConfigurationValue(
            normalized_key,
            None,
            ConfigurationSource.UNRESOLVED,
            locked,
            "UNRESOLVED",
        )

    def write_user_preferences(self, values: Mapping[str, object]) -> str:
        existing = self._read(self.user_path, "USER_CONFIG_INVALID")
        version = 1 if existing is None else existing.document_version + 1
        document = ConfigurationDocument.create(values, document_version=version)
        return self._json_writer.write(
            self.paths.path(AppPathKind.USER_ROAMING_ROOT),
            self.user_path,
            document.to_dict(),
        )

    def write_machine_config(
        self,
        values: Mapping[str, object],
        *,
        locked_keys: tuple[str, ...] = (),
    ) -> str:
        machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        with ResourceFileLock(machine_root, MachineResource.CONFIG):
            existing = self._read(self.machine_path, "MACHINE_CONFIG_INVALID")
            version = 1 if existing is None else existing.document_version + 1
            backup = self._backup.create_backup(
                self.machine_path,
                MachineResource.CONFIG,
                source_version="none" if existing is None else str(existing.document_version),
            )
            document = ConfigurationDocument.create(
                values,
                document_version=version,
                locked_keys=locked_keys,
            )
            try:
                digest = self._json_writer.write(
                    machine_root,
                    self.machine_path,
                    document.to_dict(),
                )
                published = self._read(self.machine_path, "MACHINE_CONFIG_INVALID")
                if published != document:
                    raise AtomicWriteError("Machine config read-after-write mismatch")
                return digest
            except Exception:
                if backup is not None:
                    original = self._backup.restore_bytes(backup)
                    self._bytes_writer.write(machine_root, self.machine_path, original)
                raise

    def _read(self, path: Path, diagnostic: str) -> ConfigurationDocument | None:
        if not path.is_file():
            return None
        try:
            return ConfigurationDocument.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._diagnostics.append(diagnostic)
            LOGGER.warning("Bỏ qua cấu hình không hợp lệ %s: %s", path, exc)
            return None


def _validate_values(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("Configuration values must be an object")
    normalized: dict[str, object] = {}
    for raw_key, item in value.items():
        key = str(raw_key).strip()
        if not key or key != str(raw_key):
            raise ValueError("Configuration keys must be non-empty canonical strings")
        _validate_json_value(item)
        normalized[key] = item
    return normalized


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_value(item)
        return
    raise TypeError("Configuration contains a non-JSON value")


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "ConfigurationDocument",
    "ConfigurationService",
    "ConfigurationSource",
    "MACHINE_CONFIG_FILENAME",
    "ResolvedConfigurationValue",
    "USER_PREFERENCES_FILENAME",
]
