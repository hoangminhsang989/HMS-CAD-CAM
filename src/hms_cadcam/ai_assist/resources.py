"""Immutable, standard-library resource probes for the offline AI foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import ctypes
from ctypes import wintypes
import sys
import time
from typing import Protocol


class ProbeStatus(StrEnum):
    """Confidence of a resource probe; unknown values are never guessed."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    FAILED = "failed"


def _bytes_or_none(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer byte count or None")


@dataclass(frozen=True, slots=True)
class RamResourceSnapshot:
    """One physical-RAM sample; commit headroom is optional and never inferred."""

    total_physical_bytes: int
    available_physical_bytes: int
    used_physical_bytes: int
    available_commit_headroom_bytes: int | None
    sampled_at_monotonic_ns: int
    provider: str
    status: ProbeStatus
    reason_code: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("total_physical_bytes", self.total_physical_bytes),
            ("available_physical_bytes", self.available_physical_bytes),
            ("used_physical_bytes", self.used_physical_bytes),
            ("sampled_at_monotonic_ns", self.sampled_at_monotonic_ns),
        ):
            _bytes_or_none(name, value)
        _bytes_or_none("available_commit_headroom_bytes", self.available_commit_headroom_bytes)
        if self.available_physical_bytes > self.total_physical_bytes:
            raise ValueError("available physical RAM cannot exceed total physical RAM")
        if not self.provider.strip():
            raise ValueError("provider must not be empty")

    @property
    def commit_headroom_is_trustworthy(self) -> bool:
        """Return whether policy may use the independently measured commit value."""

        return self.status is ProbeStatus.AVAILABLE and self.available_commit_headroom_bytes is not None


@dataclass(frozen=True, slots=True)
class VramResourceSnapshot:
    """VRAM sample that refuses to fabricate available memory from total VRAM."""

    total_vram_bytes: int | None
    available_vram_bytes: int | None
    sampled_at_monotonic_ns: int
    provider: str
    status: ProbeStatus
    confidence: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _bytes_or_none("total_vram_bytes", self.total_vram_bytes)
        _bytes_or_none("available_vram_bytes", self.available_vram_bytes)
        _bytes_or_none("sampled_at_monotonic_ns", self.sampled_at_monotonic_ns)
        if (
            self.total_vram_bytes is not None
            and self.available_vram_bytes is not None
            and self.available_vram_bytes > self.total_vram_bytes
        ):
            raise ValueError("available VRAM cannot exceed total VRAM")
        if not self.provider.strip() or not self.confidence.strip():
            raise ValueError("provider and confidence must not be empty")

    @property
    def available_is_trustworthy(self) -> bool:
        """GPU selection is permitted only for explicitly available VRAM."""

        return self.status is ProbeStatus.AVAILABLE and self.available_vram_bytes is not None


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Complete sample passed from a provider to deterministic policy code."""

    ram: RamResourceSnapshot
    vram: VramResourceSnapshot


class ResourceProvider(Protocol):
    """Injectable provider boundary; tests must use a fake instead of hardware."""

    def sample(self, sampled_at_monotonic_ns: int | None = None) -> ResourceSnapshot:
        """Return a resource sample without starting a worker or a timer."""


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class WindowsResourceProvider:
    """Use ``GlobalMemoryStatusEx`` for physical RAM and Windows commit headroom.

    Stage 13A has no reliable standard-library API for available dedicated VRAM.
    The provider therefore reports VRAM as unknown rather than estimating it.
    """

    provider_name = "windows.global_memory_status_ex"

    def sample(self, sampled_at_monotonic_ns: int | None = None) -> ResourceSnapshot:
        """Capture a safe resource sample; a probe failure is returned as data."""

        now = time.monotonic_ns() if sampled_at_monotonic_ns is None else sampled_at_monotonic_ns
        if type(now) is not int or now < 0:
            raise ValueError("sampled_at_monotonic_ns must be a non-negative integer")
        vram = VramResourceSnapshot(
            total_vram_bytes=None,
            available_vram_bytes=None,
            sampled_at_monotonic_ns=now,
            provider="stage13a.no_reliable_vram_probe",
            status=ProbeStatus.UNKNOWN,
            confidence="none",
            reason_code="GPU_RESOURCE_UNKNOWN",
        )
        if sys.platform != "win32":
            return ResourceSnapshot(
                ram=RamResourceSnapshot(
                    total_physical_bytes=0,
                    available_physical_bytes=0,
                    used_physical_bytes=0,
                    available_commit_headroom_bytes=None,
                    sampled_at_monotonic_ns=now,
                    provider=self.provider_name,
                    status=ProbeStatus.UNAVAILABLE,
                    reason_code="WINDOWS_API_UNAVAILABLE",
                ),
                vram=vram,
            )
        try:
            total, available, commit_headroom = self._read_windows_memory()
            return ResourceSnapshot(
                ram=RamResourceSnapshot(
                    total_physical_bytes=total,
                    available_physical_bytes=available,
                    used_physical_bytes=max(0, total - available),
                    available_commit_headroom_bytes=commit_headroom,
                    sampled_at_monotonic_ns=now,
                    provider=self.provider_name,
                    status=ProbeStatus.AVAILABLE,
                ),
                vram=vram,
            )

        except (AttributeError, OSError, ValueError) as error:
            return ResourceSnapshot(
                ram=RamResourceSnapshot(
                    total_physical_bytes=0,
                    available_physical_bytes=0,
                    used_physical_bytes=0,
                    available_commit_headroom_bytes=None,
                    sampled_at_monotonic_ns=now,
                    provider=self.provider_name,
                    status=ProbeStatus.FAILED,
                    reason_code=f"PROBE_FAILED:{type(error).__name__}",
                ),
                vram=vram,
            )

    @staticmethod
    def _read_windows_memory() -> tuple[int, int, int]:
        """Read Windows physical/commit counters behind a narrow test seam."""

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        global_memory_status_ex = kernel32.GlobalMemoryStatusEx
        global_memory_status_ex.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        global_memory_status_ex.restype = wintypes.BOOL
        if not global_memory_status_ex(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(status.ullTotalPhys), int(status.ullAvailPhys), int(status.ullAvailPageFile)


__all__ = [
    "ProbeStatus",
    "RamResourceSnapshot",
    "ResourceProvider",
    "ResourceSnapshot",
    "VramResourceSnapshot",
    "WindowsResourceProvider",
]
