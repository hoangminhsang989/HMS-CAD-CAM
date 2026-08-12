"""Bounded deterministic parallel mapping with stable result ordering."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def deterministic_parallel_map(
    values: Iterable[T], function: Callable[[T], R], *, max_workers: int = 1,
) -> tuple[R, ...]:
    """Evaluate bounded work and publish results in input order.

    A worker must return a complete immutable value; shared artifact writes are
    intentionally outside this helper.
    """
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError("max_workers must be positive")
    items = tuple(values)
    if max_workers == 1 or len(items) < 2:
        return tuple(function(item) for item in items)
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items)), thread_name_prefix="hms-r246") as pool:
        futures = tuple(pool.submit(function, item) for item in items)
        return tuple(future.result() for future in futures)
