"""QSettings-only preferences for the Stage 13B advisor; loading starts nothing."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from hms_cadcam.ai_assist.cutting_advisor import RecommendationProfile
ADVISOR_ENABLED_KEY="ai_assist/stage13b/advisor_enabled"; PROFILE_KEY="ai_assist/stage13b/default_profile"; TIMEOUT_KEY="ai_assist/stage13b/worker_timeout_seconds"
class Backend(Protocol):
 def value(self,key:str,defaultValue:object=None)->object: ...
 def setValue(self,key:str,value:object)->None: ...
 def sync(self)->None: ...
@dataclass(frozen=True,slots=True)
class AdvisorSettings: enabled:bool=False; profile:RecommendationProfile=RecommendationProfile.BALANCED; timeout_seconds:float=5.0
class AdvisorSettingsService:
 def __init__(self,settings:Backend)->None:self._settings=settings
 def load(self)->AdvisorSettings:
  try:return AdvisorSettings(bool(self._settings.value(ADVISOR_ENABLED_KEY,False)),RecommendationProfile(str(self._settings.value(PROFILE_KEY,"BALANCED"))),max(1.0,min(30.0,float(self._settings.value(TIMEOUT_KEY,5.0)))))
  except (TypeError,ValueError,OSError,RuntimeError):return AdvisorSettings()
 def save(self,value:AdvisorSettings)->bool:
  try:self._settings.setValue(ADVISOR_ENABLED_KEY,value.enabled);self._settings.setValue(PROFILE_KEY,value.profile.value);self._settings.setValue(TIMEOUT_KEY,value.timeout_seconds);self._settings.sync();return True
  except (OSError,RuntimeError,TypeError,ValueError):return False
