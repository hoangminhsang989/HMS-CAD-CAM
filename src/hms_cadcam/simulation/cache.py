"""Bounded in-memory cache for immutable R241 simulation artifacts."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Generic, TypeVar

from hms_cadcam.cam.domain.revision import ContentFingerprint

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheKey:
    kind: str
    fingerprint: ContentFingerprint


class BoundedSimulationCache(Generic[T]):
    """Thread-safe LRU by entry count and declared byte weight."""

    def __init__(self, *, maximum_entries: int = 16, maximum_bytes: int = 512 * 1024 * 1024) -> None:
        if maximum_entries <= 0 or maximum_bytes <= 0:
            raise ValueError("Simulation cache bounds must be positive")
        self._maximum_entries = maximum_entries
        self._maximum_bytes = maximum_bytes
        self._entries: OrderedDict[CacheKey, tuple[T, int]] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    @property
    def byte_count(self) -> int:
        with self._lock:
            return self._bytes

    def get(self, key: CacheKey) -> T | None:
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            self._entries.move_to_end(key)
            return item[0]

    def put(self, key: CacheKey, value: T, *, byte_count: int) -> bool:
        if byte_count < 0:
            raise ValueError("Simulation cache byte count cannot be negative")
        if byte_count > self._maximum_bytes:
            return False
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._entries[key] = (value, byte_count)
            self._bytes += byte_count
            while len(self._entries) > self._maximum_entries or self._bytes > self._maximum_bytes:
                _old_key, (_old_value, old_bytes) = self._entries.popitem(last=False)
                self._bytes -= old_bytes
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def discard(self, key: CacheKey) -> None:
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
