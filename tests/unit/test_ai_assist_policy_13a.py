"""Focused deterministic resource-policy tests for Stage 13A."""

from __future__ import annotations

import pytest

from hms_cadcam.ai_assist.policy import (
    AiMode,
    AiResourcePolicy,
    AiTier,
    ComputeSelection,
    GIB,
    MIB,
    calculate_budget,
    select_profile,
)
from hms_cadcam.ai_assist.resources import (
    ProbeStatus,
    RamResourceSnapshot,
    ResourceSnapshot,
    VramResourceSnapshot,
    WindowsResourceProvider,
)


def _snapshot(
    *,
    total: int = 16 * GIB,
    available: int = 10 * GIB,
    commit: int | None = None,
    ram_status: ProbeStatus = ProbeStatus.AVAILABLE,
    vram_available: int | None = None,
) -> ResourceSnapshot:
    return ResourceSnapshot(
        RamResourceSnapshot(
            total_physical_bytes=total,
            available_physical_bytes=available,
            used_physical_bytes=total - available,
            available_commit_headroom_bytes=commit,
            sampled_at_monotonic_ns=1,
            provider="fake",
            status=ram_status,
        ),
        VramResourceSnapshot(
            total_vram_bytes=2 * GIB if vram_available is not None else None,
            available_vram_bytes=vram_available,
            sampled_at_monotonic_ns=1,
            provider="fake",
            status=ProbeStatus.AVAILABLE if vram_available is not None else ProbeStatus.UNKNOWN,
            confidence="test",
        ),
    )


def test_ten_gib_effective_available_has_seven_gib_ratio_budget_before_caps() -> None:
    policy = AiResourcePolicy()
    profile = policy.profiles[AiTier.ENHANCED]
    budget = calculate_budget(_snapshot(), policy, profile)

    assert budget.effective_available_bytes == 10 * GIB
    assert budget.ratio_budget_bytes == 7 * GIB
    assert budget.dynamic_reserve_bytes == 2 * GIB
    assert budget.reserve_budget_bytes == 8 * GIB
    assert budget.safety_limited_ram_budget_bytes == 7 * GIB
    assert budget.ai_ram_budget_bytes == 7 * GIB


def test_budget_uses_available_not_total_and_commit_falls_back_when_unknown() -> None:
    policy = AiResourcePolicy()
    profile = policy.profiles[AiTier.LITE]
    budget = calculate_budget(_snapshot(total=64 * GIB, available=600 * MIB), policy, profile)

    assert budget.effective_available_bytes == 600 * MIB
    assert budget.ratio_budget_bytes == 420 * MIB
    assert budget.ai_ram_budget_bytes == 0


def test_commit_headroom_constrains_effective_available_when_trustworthy() -> None:
    policy = AiResourcePolicy()
    budget = calculate_budget(
        _snapshot(available=10 * GIB, commit=5 * GIB),
        policy,
        policy.profiles[AiTier.ENHANCED],
    )
    assert budget.effective_available_bytes == 5 * GIB
    assert budget.ratio_budget_bytes == int(3.5 * GIB)


def test_four_gib_machine_can_select_lite_when_current_resources_allow_it() -> None:
    policy = AiResourcePolicy()
    selection = select_profile(
        _snapshot(total=4 * GIB, available=int(1.5 * GIB)), policy, AiMode.AUTO
    )
    assert selection.tier is AiTier.LITE
    assert selection.budget is not None
    assert selection.budget.ai_ram_budget_bytes >= 256 * MIB
    assert selection.budget.compute_selection is ComputeSelection.CPU_ONLY


def test_low_memory_and_forced_enhanced_wait_instead_of_silently_downgrading() -> None:
    policy = AiResourcePolicy()
    auto = select_profile(_snapshot(total=4 * GIB, available=700 * MIB), policy, AiMode.AUTO)
    forced = select_profile(_snapshot(total=16 * GIB, available=2 * GIB), policy, AiMode.ENHANCED)

    assert auto.tier is None
    assert auto.reason_code == "INSUFFICIENT_AVAILABLE_RAM"
    assert forced.tier is None
    assert forced.reason_code == "INSUFFICIENT_AVAILABLE_RAM"


def test_unknown_vram_never_selects_gpu_or_infers_available_memory() -> None:
    policy = AiResourcePolicy()
    budget = calculate_budget(_snapshot(), policy, policy.profiles[AiTier.LITE])

    assert budget.vram_budget_bytes is None
    assert budget.compute_selection is ComputeSelection.CPU_ONLY
    assert budget.reason_code == "GPU_RESOURCE_UNKNOWN"


def test_windows_probe_failure_is_returned_as_safe_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = WindowsResourceProvider()
    monkeypatch.setattr(
        provider,
        "_read_windows_memory",
        lambda: (_ for _ in ()).throw(OSError("denied")),
    )
    sample = provider.sample(7)
    assert sample.ram.status is ProbeStatus.FAILED
    assert sample.ram.available_physical_bytes == 0
    assert sample.vram.status is ProbeStatus.UNKNOWN


@pytest.mark.parametrize("available", (0, 1, 1 << 100))
def test_budget_never_overflows_or_becomes_negative(available: int) -> None:
    policy = AiResourcePolicy()
    budget = calculate_budget(
        _snapshot(total=max(available, 4 * GIB), available=available),
        policy,
        policy.profiles[AiTier.LITE],
        user_configured_cap_bytes=0,
    )
    assert budget.ai_ram_budget_bytes == 0
    assert budget.reserve_budget_bytes >= 0
