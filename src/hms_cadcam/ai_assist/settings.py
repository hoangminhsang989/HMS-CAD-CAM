"""Application-setting value objects for the offline CAM AI-assist foundation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hms_cadcam.ai_assist.policy import AiMode


AI_ASSIST_ENABLED_KEY = "ai_assist/enabled"
AI_ASSIST_MODE_KEY = "ai_assist/mode"
AI_ASSIST_RAM_RATIO_KEY = "ai_assist/ram_ratio_percent"
AI_ASSIST_VRAM_RATIO_KEY = "ai_assist/vram_ratio_percent"
AI_ASSIST_USER_CAP_KEY = "ai_assist/user_cap_bytes"


class SettingsBackend(Protocol):
    """Minimal QSettings-compatible boundary retained for focused tests."""

    def value(self, key: str, default_value: object = None) -> object: ...

    def setValue(self, key: str, value: object) -> None: ...  # noqa: N802

    def sync(self) -> None: ...


def _percent(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(100, max(1, normalized))


def _enabled(value: object) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _cap(value: object) -> int | None:
    if value in (None, "", "auto", "unlimited"):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


@dataclass(frozen=True, slots=True)
class AiAssistSettings:
    """User preferences only; this object does not modify a HMS project."""

    enabled: bool = False
    mode: AiMode = AiMode.AUTO
    ram_ratio_percent: int = 70
    vram_ratio_percent: int = 60
    user_cap_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")
        if not isinstance(self.mode, AiMode):
            raise TypeError("mode must be AiMode")
        for name, value in (
            ("ram_ratio_percent", self.ram_ratio_percent),
            ("vram_ratio_percent", self.vram_ratio_percent),
        ):
            if type(value) is not int or not 1 <= value <= 100:
                raise ValueError(f"{name} must be an integer from 1 through 100")
        if self.user_cap_bytes is not None and (
            type(self.user_cap_bytes) is not int or self.user_cap_bytes < 0
        ):
            raise ValueError("user_cap_bytes must be a non-negative integer or None")


class AiAssistSettingsService:
    """Read and write Stage 13A preferences through the existing QSettings owner."""

    def __init__(self, settings: SettingsBackend) -> None:
        self._settings = settings

    def load(self) -> AiAssistSettings:
        """Load fail-closed preferences; missing or invalid data leaves AI OFF."""

        try:
            mode = AiMode(str(self._settings.value(AI_ASSIST_MODE_KEY, AiMode.AUTO.value)))
        except (TypeError, ValueError, OSError, RuntimeError):
            mode = AiMode.AUTO
        try:
            return AiAssistSettings(
                enabled=_enabled(self._settings.value(AI_ASSIST_ENABLED_KEY, False)),
                mode=mode,
                ram_ratio_percent=_percent(
                    self._settings.value(AI_ASSIST_RAM_RATIO_KEY, 70), 70
                ),
                vram_ratio_percent=_percent(
                    self._settings.value(AI_ASSIST_VRAM_RATIO_KEY, 60), 60
                ),
                user_cap_bytes=_cap(self._settings.value(AI_ASSIST_USER_CAP_KEY, None)),
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return AiAssistSettings()

    def save(self, values: AiAssistSettings) -> bool:
        """Persist complete settings atomically at the QSettings boundary when possible."""

        if not isinstance(values, AiAssistSettings):
            raise TypeError("values must be AiAssistSettings")
        try:
            self._settings.setValue(AI_ASSIST_ENABLED_KEY, values.enabled)
            self._settings.setValue(AI_ASSIST_MODE_KEY, values.mode.value)
            self._settings.setValue(AI_ASSIST_RAM_RATIO_KEY, values.ram_ratio_percent)
            self._settings.setValue(AI_ASSIST_VRAM_RATIO_KEY, values.vram_ratio_percent)
            self._settings.setValue(
                AI_ASSIST_USER_CAP_KEY,
                "auto" if values.user_cap_bytes is None else values.user_cap_bytes,
            )
            self._settings.sync()
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return True


__all__ = [
    "AI_ASSIST_ENABLED_KEY",
    "AI_ASSIST_MODE_KEY",
    "AI_ASSIST_RAM_RATIO_KEY",
    "AI_ASSIST_USER_CAP_KEY",
    "AI_ASSIST_VRAM_RATIO_KEY",
    "AiAssistSettings",
    "AiAssistSettingsService",
    "SettingsBackend",
]
