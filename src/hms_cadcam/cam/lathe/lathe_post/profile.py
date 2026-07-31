"""Fail-closed Lathe Post profile and capability registry."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.cam.lathe.lathe_post.identity import canonical_id
from hms_cadcam.cam.lathe.lathe_post.ir import (
    LatheProgramBlockKind,
    LatheProgramIRV1,
    LatheSemanticPlane,
    LatheUnits,
    NEUTRAL_PROFILE_ID,
)


class LathePostUnavailableError(RuntimeError):
    """Raised when a machine-ready Post is requested without a contract."""


@dataclass(frozen=True, slots=True)
class LathePostCapability:
    code: str
    supported: bool
    details: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", canonical_id(self.code, "capability code"))
        if type(self.supported) is not bool:
            raise TypeError("capability support must be bool")


@dataclass(frozen=True, slots=True)
class LathePostProfile:
    profile_id: str
    schema_version: str
    display_name_key: str
    controller_family: str
    machine_model: str | None
    preview_only: bool
    machine_output_supported: bool
    supported_units: tuple[LatheUnits, ...]
    supported_plane: LatheSemanticPlane
    supported_block_kinds: frozenset[LatheProgramBlockKind]
    capabilities: tuple[LathePostCapability, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", canonical_id(self.profile_id, "profile_id"))
        object.__setattr__(self, "schema_version", canonical_id(self.schema_version, "profile schema_version"))
        object.__setattr__(self, "display_name_key", canonical_id(self.display_name_key, "display_name_key"))
        object.__setattr__(self, "controller_family", canonical_id(self.controller_family, "controller_family"))
        if self.machine_model is not None:
            object.__setattr__(self, "machine_model", canonical_id(self.machine_model, "machine_model"))
        if type(self.preview_only) is not bool or type(self.machine_output_supported) is not bool:
            raise TypeError("profile flags must be bool")
        if not isinstance(self.supported_units, tuple) or any(not isinstance(item, LatheUnits) for item in self.supported_units):
            raise TypeError("profile units are invalid")
        if not isinstance(self.supported_plane, LatheSemanticPlane):
            raise TypeError("profile plane is invalid")
        if not isinstance(self.supported_block_kinds, frozenset) or any(not isinstance(item, LatheProgramBlockKind) for item in self.supported_block_kinds):
            raise TypeError("profile block kinds are invalid")
        if not isinstance(self.capabilities, tuple) or any(not isinstance(item, LathePostCapability) for item in self.capabilities):
            raise TypeError("profile capabilities are invalid")
        if self.preview_only and self.machine_output_supported:
            raise ValueError("preview-only profile cannot support machine output")

    @property
    def is_executable(self) -> bool:
        return self.machine_output_supported and not self.preview_only

    @property
    def machine_model_name(self) -> str:
        return self.machine_model or "NONE"


def neutral_preview_profile() -> LathePostProfile:
    return LathePostProfile(
        profile_id=NEUTRAL_PROFILE_ID,
        schema_version="lathe.post.profile.v1",
        display_name_key="lathe.post.profile.neutral_program_preview",
        controller_family="CONTROLLER_NEUTRAL",
        machine_model=None,
        preview_only=True,
        machine_output_supported=False,
        supported_units=(LatheUnits.MILLIMETRES,),
        supported_plane=LatheSemanticPlane.LATHE_XZ_DIAMETER,
        supported_block_kinds=frozenset(LatheProgramBlockKind),
        capabilities=(
            LathePostCapability("semantic_listing", True),
            LathePostCapability("machine_output", False, "No production Post contract is installed"),
        ),
    )


@dataclass(frozen=True, slots=True)
class LathePostProfileRegistry:
    """Immutable registry with one neutral profile and zero production profiles."""

    profiles: tuple[LathePostProfile, ...] = (neutral_preview_profile(),)
    production_profile_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, tuple) or any(not isinstance(item, LathePostProfile) for item in self.profiles):
            raise TypeError("profiles must be immutable typed values")
        if len(self.profiles) != 1 or self.profiles[0].profile_id != NEUTRAL_PROFILE_ID:
            raise ValueError("Stage 12.4A requires exactly one neutral built-in profile")
        if self.production_profile_ids:
            raise ValueError("Stage 12.4A cannot register production profiles")
        object.__setattr__(self, "production_profile_ids", tuple(self.production_profile_ids))

    @property
    def built_in_profiles(self) -> tuple[LathePostProfile, ...]:
        return self.profiles

    @property
    def production_profiles(self) -> tuple[LathePostProfile, ...]:
        return ()

    def get(self, profile_id: str) -> LathePostProfile | None:
        key = str(profile_id).strip()
        return next((item for item in self.profiles if item.profile_id == key), None)

    def neutral_preview(self) -> LathePostProfile:
        return self.profiles[0]

    def request_production_post(self, profile_id: str | None = None) -> LathePostProfile:
        raise LathePostUnavailableError("PRODUCTION_POST_UNAVAILABLE: no machine-specific Lathe Post profile is defined")

    def readiness_for(self, program: LatheProgramIRV1 | None) -> str:
        if program is None:
            return "INCOMPLETE"
        if self.get(program.profile_id) is None:
            return "INVALID"
        return "NEUTRAL_PREVIEW_READY"


DEFAULT_LATHE_POST_PROFILE_REGISTRY = LathePostProfileRegistry()


def lathe_post_profile_registry() -> LathePostProfileRegistry:
    return DEFAULT_LATHE_POST_PROFILE_REGISTRY


PostProfileRegistry = LathePostProfileRegistry
LathePostProfileDescriptor = LathePostProfile


__all__ = [
    "DEFAULT_LATHE_POST_PROFILE_REGISTRY", "LathePostCapability", "LathePostProfile",
    "LathePostProfileDescriptor", "LathePostProfileRegistry", "LathePostUnavailableError",
    "PostProfileRegistry", "lathe_post_profile_registry", "neutral_preview_profile",
]
