"""Qt-free bounded cache and cooperative latest-wins Lathe coordinator."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import StrEnum
import logging
from threading import Event, RLock

from hms_cadcam.cam.lathe.domain import LatheOwnershipKey
from hms_cadcam.cam.lathe.toolpath.generators import LatheToolpathGeneratorRegistry
from hms_cadcam.cam.lathe.toolpath.model import (
    LatheToolpathDiagnostic,
    LatheToolpathDiagnosticCode,
    LatheToolpathJobId,
    LatheToolpathResult,
    LatheToolpathResultIdentity,
    LatheToolpathResultSource,
    LatheToolpathResultState,
)
from hms_cadcam.cam.lathe.toolpath.request import LatheToolpathRequestV1

logger = logging.getLogger(__name__)


class LatheToolpathCancellationToken:
    """Thread-safe cooperative cancellation state."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


@dataclass(frozen=True, slots=True)
class LatheToolpathCacheEntry:
    ownership: LatheOwnershipKey
    fingerprint_digest: str
    result: LatheToolpathResult

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, LatheOwnershipKey):
            raise TypeError("Lathe cache ownership is invalid")
        if self.fingerprint_digest != self.result.identity.fingerprint.digest:
            raise ValueError("Lathe cache fingerprint does not match result")
        if not self.result.succeeded:
            raise ValueError("Lathe cache accepts only successful results")


class LatheInMemoryToolpathCache:
    """Bounded deterministic FIFO cache with no filesystem boundary."""

    def __init__(self, max_entries: int = 32) -> None:
        if type(max_entries) is not int or max_entries <= 0:
            raise ValueError("Lathe cache capacity must be positive")
        self._max_entries = max_entries
        self._lock = RLock()
        self._entries: OrderedDict[str, LatheToolpathCacheEntry] = OrderedDict()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, request: LatheToolpathRequestV1) -> LatheToolpathResult | None:
        if not isinstance(request, LatheToolpathRequestV1):
            raise TypeError("Lathe cache request is invalid")
        with self._lock:
            entry = self._entries.get(request.cache_key.digest)
            if entry is None:
                return None
            if (
                entry.ownership != request.ownership
                or entry.fingerprint_digest != request.fingerprint.digest
                or entry.result.cache_key != request.cache_key
            ):
                self._entries.pop(request.cache_key.digest, None)
                return None
            return entry.result

    def put(
        self,
        request: LatheToolpathRequestV1,
        result: LatheToolpathResult,
    ) -> LatheToolpathCacheEntry:
        if not isinstance(request, LatheToolpathRequestV1):
            raise TypeError("Lathe cache request is invalid")
        if not isinstance(result, LatheToolpathResult) or not result.succeeded:
            raise TypeError("Lathe cache result must be successful")
        if (
            result.identity.fingerprint != request.fingerprint
            or result.cache_key != request.cache_key
            or result.identity.ownership != request.ownership
        ):
            raise ValueError("Lathe cache result identity is inconsistent")
        entry = LatheToolpathCacheEntry(
            request.ownership,
            request.fingerprint.digest,
            result,
        )
        with self._lock:
            self._entries.pop(request.cache_key.digest, None)
            self._entries[request.cache_key.digest] = entry
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return entry

    def invalidate_for_request(self, request: LatheToolpathRequestV1) -> int:
        """Remove same-owner records that no longer match current semantics."""

        if not isinstance(request, LatheToolpathRequestV1):
            raise TypeError("Lathe cache request is invalid")
        with self._lock:
            keys = tuple(
                key
                for key, entry in self._entries.items()
                if entry.ownership == request.ownership
                and (
                    key != request.cache_key.digest
                    or entry.fingerprint_digest != request.fingerprint.digest
                )
            )
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def invalidate_ownership(self, ownership: LatheOwnershipKey) -> int:
        if not isinstance(ownership, LatheOwnershipKey):
            raise TypeError("Lathe cache ownership is invalid")
        with self._lock:
            keys = tuple(
                key
                for key, entry in self._entries.items()
                if entry.ownership == ownership
            )
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class LatheSubmissionDecision(StrEnum):
    ACCEPTED = "accepted"
    CACHE_HIT = "cache_hit"
    INVALID_REQUEST = "invalid_request"
    DUPLICATE_REQUEST = "duplicate_request"
    CLOSED = "closed"
    OWNERSHIP_CLOSED = "ownership_closed"


@dataclass(frozen=True, slots=True)
class LatheSubmissionReceipt:
    job_id: LatheToolpathJobId | None
    accepted: bool
    decision: LatheSubmissionDecision
    scheduled: bool

    def __post_init__(self) -> None:
        if self.job_id is not None and not isinstance(
            self.job_id, LatheToolpathJobId
        ):
            raise TypeError("Lathe submission job identity is invalid")
        if type(self.accepted) is not bool or type(self.scheduled) is not bool:
            raise TypeError("Lathe submission flags are invalid")
        if not isinstance(self.decision, LatheSubmissionDecision):
            raise TypeError("Lathe submission decision is invalid")


class LatheToolpathJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    DROPPED = "dropped"


class LatheCancelDecision(StrEnum):
    REQUESTED = "requested"
    ALREADY_CANCELLED = "already_cancelled"
    ALREADY_COMPLETED = "already_completed"
    NOT_FOUND = "not_found"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class LatheToolpathJobRecord:
    identity: LatheToolpathResultIdentity
    state: LatheToolpathJobState
    callback_invoked: bool = False


class _PendingJob:
    __slots__ = ("request", "token", "callback", "future")

    def __init__(
        self,
        request: LatheToolpathRequestV1,
        token: LatheToolpathCancellationToken,
        callback: Callable[[LatheToolpathResult], None] | None,
    ) -> None:
        self.request = request
        self.token = token
        self.callback = callback
        self.future: Future[LatheToolpathResult] | None = None


def _result_identity(
    request: LatheToolpathRequestV1,
) -> LatheToolpathResultIdentity:
    return LatheToolpathResultIdentity(
        request.job_id,
        request.request_sequence,
        request.ownership,
        request.operation.revision,
        request.fingerprint,
    )


def _cancelled_result(
    request: LatheToolpathRequestV1,
) -> LatheToolpathResult:
    return LatheToolpathResult(
        _result_identity(request),
        request.strategy_id,
        request.algorithm_version,
        request.cache_key,
        LatheToolpathResultState.CANCELLED,
        LatheToolpathResultSource.WORKER,
        diagnostics=(
            LatheToolpathDiagnostic(LatheToolpathDiagnosticCode.CANCELLED),
        ),
    )


def _failed_result(request: LatheToolpathRequestV1) -> LatheToolpathResult:
    return LatheToolpathResult(
        _result_identity(request),
        request.strategy_id,
        request.algorithm_version,
        request.cache_key,
        LatheToolpathResultState.GENERATION_FAILED,
        LatheToolpathResultSource.WORKER,
        diagnostics=(
            LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.GENERATION_FAILED
            ),
        ),
    )


class LatheToolpathCoordinator:
    """Application-owned worker/cache/latest-wins coordinator."""

    def __init__(
        self,
        registry: LatheToolpathGeneratorRegistry | None = None,
        *,
        max_workers: int = 1,
        cache: LatheInMemoryToolpathCache | None = None,
        executor: ThreadPoolExecutor | None = None,
        max_records: int = 256,
    ) -> None:
        if registry is not None and not isinstance(
            registry, LatheToolpathGeneratorRegistry
        ):
            raise TypeError("Lathe coordinator registry is invalid")
        if type(max_workers) is not int or max_workers <= 0:
            raise ValueError("Lathe coordinator worker count must be positive")
        if executor is not None and not callable(getattr(executor, "submit", None)):
            raise TypeError("Lathe coordinator executor is invalid")
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("Lathe coordinator record capacity must be positive")
        self._registry = registry or LatheToolpathGeneratorRegistry()
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hms-lathe-toolpath",
        )
        self._cache = cache or LatheInMemoryToolpathCache()
        self._max_records = max_records
        self._lock = RLock()
        self._closed = False
        self._closed_ownerships: set[LatheOwnershipKey] = set()
        self._latest: dict[
            LatheOwnershipKey, tuple[LatheToolpathJobId, str]
        ] = {}
        self._cancelled: set[LatheToolpathJobId] = set()
        self._pending: dict[LatheToolpathJobId, _PendingJob] = {}
        self._records: OrderedDict[
            LatheToolpathJobId, LatheToolpathJobRecord
        ] = OrderedDict()

    @property
    def cache(self) -> LatheInMemoryToolpathCache:
        return self._cache

    def bind_ownership(self, ownership: LatheOwnershipKey) -> None:
        if not isinstance(ownership, LatheOwnershipKey):
            raise TypeError("Lathe coordinator ownership is invalid")
        with self._lock:
            if not self._closed:
                self._closed_ownerships.discard(ownership)

    def submit(
        self,
        request: object,
        *,
        callback: Callable[[LatheToolpathResult], None] | None = None,
    ) -> LatheSubmissionReceipt:
        if callback is not None and not callable(callback):
            raise TypeError("Lathe coordinator callback is invalid")
        if not isinstance(request, LatheToolpathRequestV1):
            return LatheSubmissionReceipt(
                None, False, LatheSubmissionDecision.INVALID_REQUEST, False
            )
        token = LatheToolpathCancellationToken()
        pending = _PendingJob(request, token, callback)
        prior_future: Future[LatheToolpathResult] | None = None
        with self._lock:
            if self._closed:
                return LatheSubmissionReceipt(
                    request.job_id, False, LatheSubmissionDecision.CLOSED, False
                )
            if request.ownership in self._closed_ownerships:
                return LatheSubmissionReceipt(
                    request.job_id,
                    False,
                    LatheSubmissionDecision.OWNERSHIP_CLOSED,
                    False,
                )
            latest = self._latest.get(request.ownership)
            if latest == (request.job_id, request.fingerprint.digest):
                return LatheSubmissionReceipt(
                    request.job_id,
                    False,
                    LatheSubmissionDecision.DUPLICATE_REQUEST,
                    False,
                )
            if latest is not None:
                previous = self._pending.get(latest[0])
                if previous is not None:
                    previous.token.cancel()
                    prior_future = previous.future
                    record = self._records.get(latest[0])
                    if record is not None and record.state in {
                        LatheToolpathJobState.QUEUED,
                        LatheToolpathJobState.RUNNING,
                    }:
                        self._records[latest[0]] = replace(
                            record, state=LatheToolpathJobState.CANCELLING
                        )
            self._latest[request.ownership] = (
                request.job_id,
                request.fingerprint.digest,
            )
            self._pending[request.job_id] = pending
            self._records[request.job_id] = LatheToolpathJobRecord(
                _result_identity(request), LatheToolpathJobState.QUEUED
            )
            self._trim_records_locked()
        if prior_future is not None:
            prior_future.cancel()

        self._cache.invalidate_for_request(request)
        cached = self._cache.get(request)
        if cached is not None and not token.cancelled:
            result = cached.with_source_and_identity(
                identity=_result_identity(request),
                source=LatheToolpathResultSource.CACHE,
            )
            self._finish_result(request, result)
            return LatheSubmissionReceipt(
                request.job_id, True, LatheSubmissionDecision.CACHE_HIT, False
            )
        if token.cancelled:
            self._finish_result(request, _cancelled_result(request))
            return LatheSubmissionReceipt(
                request.job_id, True, LatheSubmissionDecision.ACCEPTED, False
            )
        try:
            future = self._executor.submit(self._run_job, request, token)
        except RuntimeError:
            self._finish_result(request, _failed_result(request))
            return LatheSubmissionReceipt(
                request.job_id, True, LatheSubmissionDecision.ACCEPTED, False
            )
        with self._lock:
            current = self._pending.get(request.job_id)
            if current is not pending or self._closed or token.cancelled:
                future.cancel()
            else:
                pending.future = future
        future.add_done_callback(
            lambda completed, request=request: self._finish_future(
                request, completed
            )
        )
        return LatheSubmissionReceipt(
            request.job_id, True, LatheSubmissionDecision.ACCEPTED, True
        )

    def cancel(self, job_id: LatheToolpathJobId) -> LatheCancelDecision:
        if not isinstance(job_id, LatheToolpathJobId):
            return LatheCancelDecision.NOT_FOUND
        future: Future[LatheToolpathResult] | None = None
        with self._lock:
            if self._closed:
                return LatheCancelDecision.CLOSED
            record = self._records.get(job_id)
            pending = self._pending.get(job_id)
            if record is None:
                return LatheCancelDecision.NOT_FOUND
            if record.state in {
                LatheToolpathJobState.COMPLETED,
                LatheToolpathJobState.DROPPED,
            }:
                return LatheCancelDecision.ALREADY_COMPLETED
            if job_id in self._cancelled or (
                pending is not None and pending.token.cancelled
            ):
                return LatheCancelDecision.ALREADY_CANCELLED
            if pending is None:
                return LatheCancelDecision.NOT_FOUND
            pending.token.cancel()
            self._cancelled.add(job_id)
            self._records[job_id] = replace(
                record, state=LatheToolpathJobState.CANCELLING
            )
            future = pending.future
        if future is not None:
            future.cancel()
        return LatheCancelDecision.REQUESTED

    def close_ownership(self, ownership: LatheOwnershipKey) -> None:
        if not isinstance(ownership, LatheOwnershipKey):
            raise TypeError("Lathe coordinator ownership is invalid")
        futures: list[Future[LatheToolpathResult]] = []
        with self._lock:
            self._closed_ownerships.add(ownership)
            self._latest.pop(ownership, None)
            for job_id, pending in tuple(self._pending.items()):
                if pending.request.ownership != ownership:
                    continue
                pending.token.cancel()
                self._cancelled.add(job_id)
                if pending.future is not None:
                    futures.append(pending.future)
                record = self._records.get(job_id)
                if record is not None and record.state in {
                    LatheToolpathJobState.QUEUED,
                    LatheToolpathJobState.RUNNING,
                }:
                    self._records[job_id] = replace(
                        record, state=LatheToolpathJobState.CANCELLING
                    )
        for future in futures:
            future.cancel()
        self._cache.invalidate_ownership(ownership)

    def job_record(
        self, job_id: LatheToolpathJobId
    ) -> LatheToolpathJobRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def delivery_authorized(self, result: LatheToolpathResult) -> bool:
        if not isinstance(result, LatheToolpathResult):
            return False
        with self._lock:
            latest = self._latest.get(result.identity.ownership)
            record = self._records.get(result.identity.job_id)
            return bool(
                not self._closed
                and result.identity.ownership not in self._closed_ownerships
                and latest
                == (
                    result.identity.job_id,
                    result.identity.fingerprint.digest,
                )
                and result.identity.job_id not in self._cancelled
                and record is not None
                and record.state is LatheToolpathJobState.COMPLETED
            )

    def shutdown(self, *, wait: bool = True) -> None:
        futures: list[Future[LatheToolpathResult]] = []
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._closed_ownerships.update(self._latest)
            for job_id, pending in self._pending.items():
                pending.token.cancel()
                self._cancelled.add(job_id)
                if pending.future is not None:
                    futures.append(pending.future)
        for future in futures:
            future.cancel()
        self._cache.clear()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run_job(
        self,
        request: LatheToolpathRequestV1,
        token: LatheToolpathCancellationToken,
    ) -> LatheToolpathResult:
        self._mark_running(request.job_id)
        if token.cancelled:
            return _cancelled_result(request)
        return self._registry.generate(request, lambda: token.cancelled)

    def _finish_future(
        self,
        request: LatheToolpathRequestV1,
        future: Future[LatheToolpathResult],
    ) -> None:
        try:
            result = future.result()
        except CancelledError:
            result = _cancelled_result(request)
        except Exception:
            logger.exception("Unexpected Lathe toolpath future failure")
            result = _failed_result(request)
        self._finish_result(request, result)

    def _finish_result(
        self,
        request: LatheToolpathRequestV1,
        result: LatheToolpathResult,
    ) -> None:
        callback: Callable[[LatheToolpathResult], None] | None = None
        accepted = False
        with self._lock:
            pending = self._pending.get(request.job_id)
            record = self._records.get(request.job_id)
            if pending is None or record is None or record.state in {
                LatheToolpathJobState.COMPLETED,
                LatheToolpathJobState.DROPPED,
            }:
                return
            latest = self._latest.get(request.ownership)
            accepted = bool(
                not self._closed
                and request.ownership not in self._closed_ownerships
                and latest == (request.job_id, request.fingerprint.digest)
                and request.job_id not in self._cancelled
                and result.identity == _result_identity(request)
            )
            self._pending.pop(request.job_id, None)
            self._records[request.job_id] = replace(
                record,
                state=(
                    LatheToolpathJobState.COMPLETED
                    if accepted
                    else LatheToolpathJobState.DROPPED
                ),
            )
            if accepted:
                callback = pending.callback
            self._trim_records_locked()
        if accepted and result.succeeded:
            self._cache.put(request, result)
        if not accepted or callback is None:
            return
        try:
            callback(result)
        except Exception:
            logger.exception("Lathe toolpath result callback failed")
        with self._lock:
            current = self._records.get(request.job_id)
            if current is not None:
                self._records[request.job_id] = replace(
                    current, callback_invoked=True
                )

    def _mark_running(self, job_id: LatheToolpathJobId) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is not None and record.state is LatheToolpathJobState.QUEUED:
                self._records[job_id] = replace(
                    record, state=LatheToolpathJobState.RUNNING
                )

    def _trim_records_locked(self) -> None:
        while len(self._records) > self._max_records:
            removable = next(
                (
                    job_id
                    for job_id, record in self._records.items()
                    if record.state
                    in {
                        LatheToolpathJobState.COMPLETED,
                        LatheToolpathJobState.DROPPED,
                    }
                ),
                None,
            )
            if removable is None:
                return
            self._records.pop(removable, None)
            self._cancelled.discard(removable)


__all__ = [
    "LatheCancelDecision",
    "LatheInMemoryToolpathCache",
    "LatheSubmissionDecision",
    "LatheSubmissionReceipt",
    "LatheToolpathCacheEntry",
    "LatheToolpathCancellationToken",
    "LatheToolpathCoordinator",
    "LatheToolpathJobRecord",
    "LatheToolpathJobState",
]
