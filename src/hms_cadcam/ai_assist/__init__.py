"""Offline CAM AI-assist foundation with no model or inference dependency.

Stage 13A deliberately exposes only deterministic resource governance and
lifecycle contracts.  It must remain safe to import while AI is disabled.
"""

from hms_cadcam.ai_assist.lifecycle import (
    AiAssistBroker,
    AiRuntimeReason,
    AiRuntimeState,
    AiRuntimeStatus,
)
from hms_cadcam.ai_assist.controller import AiAssistController
from hms_cadcam.ai_assist.policy import (
    AiMode,
    AiResourcePolicy,
    AiTier,
    ResourceBudget,
)
from hms_cadcam.ai_assist.resources import (
    ProbeStatus,
    RamResourceSnapshot,
    ResourceSnapshot,
    VramResourceSnapshot,
    WindowsResourceProvider,
)
from hms_cadcam.ai_assist.settings import AiAssistSettings, AiAssistSettingsService

__all__ = [
    "AiAssistBroker",
    "AiAssistController",
    "AiAssistSettings",
    "AiAssistSettingsService",
    "AiMode",
    "AiResourcePolicy",
    "AiRuntimeReason",
    "AiRuntimeState",
    "AiRuntimeStatus",
    "AiTier",
    "ProbeStatus",
    "RamResourceSnapshot",
    "ResourceBudget",
    "ResourceSnapshot",
    "VramResourceSnapshot",
    "WindowsResourceProvider",
]
