"""Persistent, strict per-format defaults for interactive 3D export."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from types import MappingProxyType
from typing import Mapping, Protocol

from PySide6.QtCore import QSettings

from hms_cadcam.cad.export_models import (
    EXPORT_CAPABILITIES,
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
)


LOGGER = logging.getLogger(__name__)
EXPORT_DEFAULTS_NAMESPACE = "export3d/defaults"
PERSISTED_EXPORT_FORMATS: tuple[ExportFormatId, ...] = (
    ExportFormatId.STEP,
    ExportFormatId.IGES,
    ExportFormatId.STL,
    ExportFormatId.BREP,
)


class ExportDefaultsSettingsBackend(Protocol):
    """QSettings-compatible boundary used by the defaults service."""

    def contains(self, key: str) -> bool: ...

    def value(self, key: str, default_value: object = None) -> object: ...

    def setValue(self, key: str, value: object) -> None: ...  # noqa: N802

    def remove(self, key: str) -> None: ...

    def sync(self) -> None: ...


class ExportDefaultsPersistenceError(RuntimeError):
    """Persistent export defaults could not be committed safely."""


@dataclass(frozen=True, slots=True)
class ExportDefaultsIssue:
    """One corrupt or incompatible value replaced by a factory working value."""

    format_id: ExportFormatId
    key: str
    reason: str


@dataclass(frozen=True, slots=True)
class ExportDefaultsSnapshot:
    """Typed load result with separately reportable persistence diagnostics."""

    profiles: Mapping[ExportFormatId, ExportProfile]
    issues: tuple[ExportDefaultsIssue, ...] = ()


def export_default_key(format_id: ExportFormatId) -> str:
    """Return the canonical QSettings key for one persistent format."""

    if format_id not in PERSISTED_EXPORT_FORMATS:
        raise ValueError("The export format has no persistent default profile")
    return f"{EXPORT_DEFAULTS_NAMESPACE}/{format_id.value}"


def factory_export_profiles() -> dict[ExportFormatId, ExportProfile]:
    """Return fresh deterministic factory profiles for supported writers."""

    return {
        format_id: ExportProfile.default_for(format_id)
        for format_id in PERSISTED_EXPORT_FORMATS
    }


class ExportDefaultsSettingsService:
    """Load and explicitly apply strict profile JSON through shared QSettings.

    Interactive defaults deliberately require ``FAIL_IF_EXISTS``. Replacement is
    a request-local decision made only after the existing export confirmation UI.
    Invalid values are reported and represented by factory working values, but
    are never rewritten until an explicit Apply or Reset action.
    """

    def __init__(self, settings: ExportDefaultsSettingsBackend | None = None) -> None:
        self._settings = settings or QSettings("HMS", "HMS CAD-CAM")

    @property
    def settings(self) -> ExportDefaultsSettingsBackend:
        """Expose the injected shared backend for focused certification."""

        return self._settings

    def load(self) -> ExportDefaultsSnapshot:
        """Load all supported profiles without mutating persistent storage."""

        profiles = factory_export_profiles()
        issues: list[ExportDefaultsIssue] = []
        for format_id in PERSISTED_EXPORT_FORMATS:
            key = export_default_key(format_id)
            if not self._settings.contains(key):
                continue
            raw = self._settings.value(key)
            try:
                if not isinstance(raw, str):
                    raise TypeError("stored profile must be JSON text")
                profile = ExportProfile.from_json(raw)
                if profile.format_id is not format_id:
                    raise ValueError("stored profile format does not match its key")
                if profile.overwrite_policy is not ExportOverwritePolicy.FAIL_IF_EXISTS:
                    raise ValueError(
                        "interactive defaults require fail_if_exists overwrite policy"
                    )
            except (TypeError, ValueError, RecursionError) as error:
                issue = ExportDefaultsIssue(format_id, key, str(error))
                issues.append(issue)
                LOGGER.warning(
                    "Mặc định Xuất 3D lưu trữ tại %s không hợp lệ: %s",
                    key,
                    error,
                )
                continue
            profiles[format_id] = profile
        return ExportDefaultsSnapshot(
            MappingProxyType(profiles),
            tuple(issues),
        )

    def apply(self, profiles: Mapping[ExportFormatId, ExportProfile]) -> None:
        """Persist one complete validated set after an explicit user action."""

        normalized = self._validate_complete_profiles(profiles)
        previous = {
            export_default_key(format_id): (
                self._settings.contains(export_default_key(format_id)),
                self._settings.value(export_default_key(format_id)),
            )
            for format_id in PERSISTED_EXPORT_FORMATS
        }
        try:
            for format_id in PERSISTED_EXPORT_FORMATS:
                self._settings.setValue(
                    export_default_key(format_id),
                    normalized[format_id].to_json(),
                )
            self._settings.sync()
            status = getattr(self._settings, "status", None)
            if callable(status) and status() != QSettings.Status.NoError:
                raise OSError(f"QSettings sync status: {status().name}")
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._restore(previous)
            raise ExportDefaultsPersistenceError(
                "3D export defaults could not be saved"
            ) from error

    @staticmethod
    def _validate_complete_profiles(
        profiles: Mapping[ExportFormatId, ExportProfile],
    ) -> dict[ExportFormatId, ExportProfile]:
        if set(profiles) != set(PERSISTED_EXPORT_FORMATS):
            raise ValueError("Persistent export defaults must contain exactly four formats")
        normalized: dict[ExportFormatId, ExportProfile] = {}
        for format_id in PERSISTED_EXPORT_FORMATS:
            profile = profiles[format_id]
            if not isinstance(profile, ExportProfile):
                raise TypeError("Persistent export default must be ExportProfile")
            if profile.format_id is not format_id:
                raise ValueError("Persistent export default format mismatch")
            if not EXPORT_CAPABILITIES[format_id].available:
                raise ValueError("Unavailable formats cannot have persistent defaults")
            if profile.overwrite_policy is not ExportOverwritePolicy.FAIL_IF_EXISTS:
                raise ValueError(
                    "Persistent interactive defaults require fail_if_exists"
                )
            normalized[format_id] = profile
        return normalized

    def _restore(self, previous: Mapping[str, tuple[bool, object]]) -> None:
        try:
            for key, (existed, value) in previous.items():
                if existed:
                    self._settings.setValue(key, value)
                else:
                    self._settings.remove(key)
            self._settings.sync()
        except (OSError, RuntimeError, TypeError, ValueError):
            LOGGER.exception("Could not restore QSettings after export-default failure")


__all__ = [
    "EXPORT_DEFAULTS_NAMESPACE",
    "PERSISTED_EXPORT_FORMATS",
    "ExportDefaultsIssue",
    "ExportDefaultsPersistenceError",
    "ExportDefaultsSettingsService",
    "ExportDefaultsSnapshot",
    "export_default_key",
    "factory_export_profiles",
]
