"""Thin application-service coordinator for Stage 13A settings and broker state."""

from __future__ import annotations

from dataclasses import replace

from hms_cadcam.ai_assist.lifecycle import (
    AiAssistBroker,
    AiRuntimeReason,
    AiRuntimeState,
    AiRuntimeStatus,
)
from hms_cadcam.ai_assist.policy import AiResourcePolicy
from hms_cadcam.ai_assist.resources import ResourceProvider
from hms_cadcam.ai_assist.settings import AiAssistSettings, AiAssistSettingsService


class AiAssistController:
    """Coordinate explicit UI refreshes without creating a polling timer or worker."""

    def __init__(
        self,
        settings_service: AiAssistSettingsService,
        resource_provider: ResourceProvider,
        *,
        capability_enabled: bool,
        broker: AiAssistBroker | None = None,
    ) -> None:
        if type(capability_enabled) is not bool:
            raise TypeError("capability_enabled must be bool")
        self._settings_service = settings_service
        self._resource_provider = resource_provider
        self._capability_enabled = capability_enabled
        self._settings = settings_service.load()
        self._provided_broker = broker
        self._broker: AiAssistBroker | None = None
        if capability_enabled and self._settings.enabled:
            self._broker = broker or AiAssistBroker(capability_enabled=True, master_enabled=True)
            self._configure_broker()

    @property
    def settings(self) -> AiAssistSettings:
        """Return the persisted application preference value object."""

        return self._settings

    @property
    def status(self) -> AiRuntimeStatus:
        """Return current broker state without probing or creating any ownership."""

        if self._broker is not None:
            return self._broker.status
        return AiRuntimeStatus(
            state=AiRuntimeState.OFF,
            reason_code=AiRuntimeReason.AI_DISABLED.value,
            selected_tier=None,
            budget=None,
            worker_started=False,
            task_requested=False,
            capability_enabled=self._capability_enabled,
            master_enabled=self._settings.enabled,
        )

    @property
    def capability_enabled(self) -> bool:
        """Return the immutable process-level feature capability decision."""

        return self._capability_enabled

    def save_settings(self, values: AiAssistSettings) -> bool:
        """Persist preferences and synchronously fail closed if AI becomes disabled."""

        if not isinstance(values, AiAssistSettings):
            raise TypeError("values must be AiAssistSettings")
        if not self._settings_service.save(values):
            return False
        previous = self._broker
        self._settings = values
        if self._capability_enabled and values.enabled:
            if self._broker is None:
                self._broker = self._provided_broker or AiAssistBroker(
                    capability_enabled=True,
                    master_enabled=True,
                )
            self._configure_broker()
        elif previous is not None:
            previous.shutdown()
            self._broker = None
        return True

    def refresh_resource_status(self) -> AiRuntimeStatus:
        """Perform one user-requested resource sample only when master AI is ON."""

        if not self._capability_enabled or not self._settings.enabled or self._broker is None:
            return self.status
        return self._broker.observe(self._resource_provider.sample())

    def shutdown(self) -> AiRuntimeStatus:
        """Release all broker ownership during application close."""

        if self._broker is not None:
            result = self._broker.shutdown()
            self._broker = None
            return result
        return self.status

    def _configure_broker(self) -> None:
        policy = replace(
            AiResourcePolicy(),
            ram_ratio=self._settings.ram_ratio_percent / 100.0,
            vram_ratio=self._settings.vram_ratio_percent / 100.0,
        )
        if self._broker is None:
            return
        self._broker.configure(
            capability_enabled=self._capability_enabled,
            master_enabled=self._settings.enabled,
            mode=self._settings.mode,
            user_cap_bytes=self._settings.user_cap_bytes,
            policy=policy,
        )


__all__ = ["AiAssistController"]
