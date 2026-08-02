"""Deterministic byte-budget and capability-tier policy for Stage 13A."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import floor
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.ai_assist.resources import ProbeStatus, ResourceSnapshot


MIB = 1024 * 1024
GIB = 1024 * MIB


class AiTier(StrEnum):
    """Capability tiers only; no Stage 13A tier represents a real model."""

    LITE = "AI_LITE"
    STANDARD = "AI_STANDARD"
    ENHANCED = "AI_ENHANCED"


class AiMode(StrEnum):
    """User selection persisted in application settings."""

    AUTO = "auto"
    LITE = "lite"
    STANDARD = "standard"
    ENHANCED = "enhanced"

    @property
    def forced_tier(self) -> AiTier | None:
        return {
            AiMode.AUTO: None,
            AiMode.LITE: AiTier.LITE,
            AiMode.STANDARD: AiTier.STANDARD,
            AiMode.ENHANCED: AiTier.ENHANCED,
        }[self]


class ComputeSelection(StrEnum):
    CPU_ONLY = "CPU_ONLY"
    GPU_NOT_SELECTED = "GPU_NOT_SELECTED"
    GPU_SELECTED = "GPU_SELECTED"


@dataclass(frozen=True, slots=True)
class AiProfile:
    """One policy-defined capability tier with explicit memory limits."""

    tier: AiTier
    minimum_start_bytes: int
    target_bytes: int
    cap_bytes: int
    gpu_preferred: bool = False
    minimum_vram_bytes: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_start_bytes", self.minimum_start_bytes),
            ("target_bytes", self.target_bytes),
            ("cap_bytes", self.cap_bytes),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not self.minimum_start_bytes <= self.target_bytes <= self.cap_bytes:
            raise ValueError("profile budgets must satisfy minimum <= target <= cap")
        if self.minimum_vram_bytes is not None and (
            type(self.minimum_vram_bytes) is not int or self.minimum_vram_bytes < 0
        ):
            raise ValueError("minimum_vram_bytes must be a non-negative integer or None")


DEFAULT_PROFILES: Mapping[AiTier, AiProfile] = MappingProxyType(
    {
        AiTier.LITE: AiProfile(AiTier.LITE, 256 * MIB, 384 * MIB, 768 * MIB),
        AiTier.STANDARD: AiProfile(AiTier.STANDARD, 1 * GIB, 1 * GIB, 3 * GIB),
        AiTier.ENHANCED: AiProfile(AiTier.ENHANCED, 4 * GIB, 4 * GIB, 8 * GIB),
    }
)


@dataclass(frozen=True, slots=True)
class AiResourcePolicy:
    """All tunable resource safety values centralized in a value object."""

    ram_ratio: float = 0.70
    vram_ratio: float = 0.60
    dynamic_reserve_ratio: float = 0.25
    dynamic_reserve_min_bytes: int = 512 * MIB
    dynamic_reserve_max_bytes: int = 2 * GIB
    start_hysteresis_ratio: float = 1.25
    stop_hysteresis_ratio: float = 0.85
    stable_start_window_ns: int = 5_000_000_000
    pressure_window_ns: int = 3_000_000_000
    profiles: Mapping[AiTier, AiProfile] = field(default_factory=lambda: DEFAULT_PROFILES)

    def __post_init__(self) -> None:
        for name, value in (
            ("ram_ratio", self.ram_ratio),
            ("vram_ratio", self.vram_ratio),
            ("dynamic_reserve_ratio", self.dynamic_reserve_ratio),
            ("start_hysteresis_ratio", self.start_hysteresis_ratio),
            ("stop_hysteresis_ratio", self.stop_hysteresis_ratio),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if self.ram_ratio > 1 or self.vram_ratio > 1 or self.dynamic_reserve_ratio > 1:
            raise ValueError("budget ratios cannot exceed 1")
        if self.start_hysteresis_ratio < 1 or not 0 <= self.stop_hysteresis_ratio <= 1:
            raise ValueError("hysteresis ratios are outside the Stage 13A contract")
        for name, value in (
            ("dynamic_reserve_min_bytes", self.dynamic_reserve_min_bytes),
            ("dynamic_reserve_max_bytes", self.dynamic_reserve_max_bytes),
            ("stable_start_window_ns", self.stable_start_window_ns),
            ("pressure_window_ns", self.pressure_window_ns),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.dynamic_reserve_min_bytes > self.dynamic_reserve_max_bytes:
            raise ValueError("dynamic reserve minimum cannot exceed maximum")
        profiles = dict(self.profiles)
        if set(profiles) != set(AiTier):
            raise ValueError("profiles must define every AI tier exactly once")
        if any(profile.tier is not tier for tier, profile in profiles.items()):
            raise ValueError("profile map keys must match profile tiers")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))

    def dynamic_reserve_bytes(self, total_physical_bytes: int) -> int:
        """Return clamped Windows/CAD safety reserve from total physical RAM."""

        if type(total_physical_bytes) is not int or total_physical_bytes < 0:
            raise ValueError("total_physical_bytes must be a non-negative integer")
        requested = floor(total_physical_bytes * self.dynamic_reserve_ratio)
        return min(self.dynamic_reserve_max_bytes, max(self.dynamic_reserve_min_bytes, requested))


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """Auditable intermediate and final byte budgets for the resource-status UI."""

    effective_available_bytes: int
    dynamic_reserve_bytes: int
    ratio_budget_bytes: int
    reserve_budget_bytes: int
    safety_limited_ram_budget_bytes: int
    profile_cap_bytes: int
    user_cap_bytes: int | None
    ai_ram_budget_bytes: int
    vram_budget_bytes: int | None
    compute_selection: ComputeSelection
    reason_code: str | None = None


def _cap_or_none(name: str, value: int | None) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")
    return value


def calculate_budget(
    snapshot: ResourceSnapshot,
    policy: AiResourcePolicy,
    profile: AiProfile,
    *,
    user_configured_cap_bytes: int | None = None,
) -> ResourceBudget:
    """Apply the locked Stage 13A formula without considering total RAM as free RAM."""

    user_cap = _cap_or_none("user_configured_cap_bytes", user_configured_cap_bytes)
    ram = snapshot.ram
    if ram.status is not ProbeStatus.AVAILABLE:
        effective = 0
        reason = "PROBE_FAILED"
    elif ram.commit_headroom_is_trustworthy:
        effective = min(ram.available_physical_bytes, ram.available_commit_headroom_bytes or 0)
        reason = None
    else:
        effective = ram.available_physical_bytes
        reason = None
    reserve = policy.dynamic_reserve_bytes(ram.total_physical_bytes)
    ratio_budget = floor(effective * policy.ram_ratio)
    reserve_budget = max(0, effective - reserve)
    candidates = [ratio_budget, reserve_budget, profile.cap_bytes]
    if user_cap is not None:
        candidates.append(user_cap)
    safety_limited = max(0, min(ratio_budget, reserve_budget))
    final_budget = max(0, min(candidates))

    vram = snapshot.vram
    if vram.available_is_trustworthy:
        vram_budget = floor((vram.available_vram_bytes or 0) * policy.vram_ratio)
        compute = ComputeSelection.GPU_SELECTED if profile.gpu_preferred else ComputeSelection.CPU_ONLY
    else:
        vram_budget = None
        compute = ComputeSelection.CPU_ONLY
        reason = reason or "GPU_RESOURCE_UNKNOWN"
    return ResourceBudget(
        effective_available_bytes=effective,
        dynamic_reserve_bytes=reserve,
        ratio_budget_bytes=ratio_budget,
        reserve_budget_bytes=reserve_budget,
        safety_limited_ram_budget_bytes=safety_limited,
        profile_cap_bytes=profile.cap_bytes,
        user_cap_bytes=user_cap,
        ai_ram_budget_bytes=final_budget,
        vram_budget_bytes=vram_budget,
        compute_selection=compute,
        reason_code=reason,
    )


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    """Tier decision that makes forced-tier waiting explicit."""

    tier: AiTier | None
    profile: AiProfile | None
    budget: ResourceBudget | None
    reason_code: str


def select_profile(
    snapshot: ResourceSnapshot,
    policy: AiResourcePolicy,
    mode: AiMode,
    *,
    user_configured_cap_bytes: int | None = None,
) -> ProfileSelection:
    """Choose the highest fitting automatic tier or wait for a forced tier."""

    if not isinstance(mode, AiMode):
        raise TypeError("mode must be AiMode")
    requested = (mode.forced_tier,) if mode.forced_tier is not None else tuple(reversed(tuple(AiTier)))
    last_budget: ResourceBudget | None = None
    for tier in requested:
        profile = policy.profiles[tier]
        budget = calculate_budget(snapshot, policy, profile, user_configured_cap_bytes=user_configured_cap_bytes)
        last_budget = budget
        if budget.ai_ram_budget_bytes < profile.minimum_start_bytes:
            continue
        if profile.minimum_vram_bytes is not None and (
            budget.vram_budget_bytes is None or budget.vram_budget_bytes < profile.minimum_vram_bytes
        ):
            continue
        return ProfileSelection(tier, profile, budget, f"PROFILE_{tier.name}_SELECTED")
    if snapshot.ram.status is not ProbeStatus.AVAILABLE:
        reason = "PROBE_FAILED"
    elif snapshot.ram.commit_headroom_is_trustworthy and (
        (snapshot.ram.available_commit_headroom_bytes or 0) < snapshot.ram.available_physical_bytes
    ):
        reason = "INSUFFICIENT_COMMIT_HEADROOM"
    else:
        reason = "INSUFFICIENT_AVAILABLE_RAM"
    return ProfileSelection(None, None, last_budget, reason)


__all__ = [
    "AiMode",
    "AiProfile",
    "AiResourcePolicy",
    "AiTier",
    "ComputeSelection",
    "DEFAULT_PROFILES",
    "GIB",
    "MIB",
    "ProfileSelection",
    "ResourceBudget",
    "calculate_budget",
    "select_profile",
]
