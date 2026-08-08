"""Stage15A WP3 cooperative cancellation and publication ordering tests."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest

from hms_cadcam.cad.export_models import (
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
)
from hms_cadcam.cad.export_service import (
    BackendWriteMetadata,
    CadExportService,
    ExportCancellation,
    ExportCancellationState,
    ExportErrorCode,
    ExportRequest,
    ExportResult,
)
from hms_cadcam.cad.models import CadDocumentId


DOCUMENT_ID = CadDocumentId("stage15a-wp3-document")


class _WritingBackend:
    supported_formats = frozenset({ExportFormatId.STEP})
    unavailable_reason = None

    def __init__(self, payload: bytes = b"candidate-output") -> None:
        self.payload = payload
        self.calls = 0

    def write(
        self, request: ExportRequest, temporary_path: Path
    ) -> BackendWriteMetadata:
        self.calls += 1
        temporary_path.write_bytes(self.payload)
        return BackendWriteMetadata("controlled writer", 1)


class _HeldBackend(_WritingBackend):
    def __init__(
        self, entered: Event, release: Event, payload: bytes = b"held"
    ) -> None:
        super().__init__(payload)
        self.entered = entered
        self.release = release

    def write(
        self, request: ExportRequest, temporary_path: Path
    ) -> BackendWriteMetadata:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(5), "test did not release held writer"
        temporary_path.write_bytes(self.payload)
        return BackendWriteMetadata("held writer", 1)


def _request(
    target: Path,
    cancellation: ExportCancellation,
    *,
    overwrite: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS,
) -> ExportRequest:
    profile = replace(
        ExportProfile.default_for(ExportFormatId.STEP),
        overwrite_policy=overwrite,
    )
    return ExportRequest(
        DOCUMENT_ID,
        target,
        profile,
        overwrite_policy=overwrite,
        cancellation=cancellation,
    )


def _run_in_thread(
    service: CadExportService, request: ExportRequest
) -> tuple[Thread, list[ExportResult]]:
    results: list[ExportResult] = []
    worker = Thread(target=lambda: results.append(service.export(request)))
    worker.start()
    return worker, results


def _finish(worker: Thread, results: list[ExportResult]) -> ExportResult:
    worker.join(5)
    assert not worker.is_alive()
    assert len(results) == 1
    return results[0]


def _assert_no_temp(parent: Path) -> None:
    assert not tuple(parent.glob("*.hms-exporting"))


def test_cancellation_gate_accepts_once_and_blocks_commit() -> None:
    cancellation = ExportCancellation()
    assert cancellation.state is ExportCancellationState.ACTIVE
    assert cancellation.request_cancel()
    assert cancellation.request_cancel()
    assert cancellation.state is ExportCancellationState.CANCEL_REQUESTED
    assert not cancellation.begin_commit()
    cancellation.mark_terminal()
    assert cancellation.state is ExportCancellationState.TERMINAL
    assert not cancellation.request_cancel()


def test_commit_gate_wins_before_cancel_and_rejects_cancel() -> None:
    cancellation = ExportCancellation()
    assert cancellation.begin_commit()
    assert cancellation.state is ExportCancellationState.COMMITTING
    assert not cancellation.request_cancel()
    cancellation.mark_terminal()
    assert cancellation.state is ExportCancellationState.TERMINAL


def test_cancel_vs_commit_race_has_exactly_one_winner() -> None:
    for _iteration in range(40):
        cancellation = ExportCancellation()
        barrier = Barrier(3)
        outcomes: dict[str, bool] = {}

        def cancel() -> None:
            barrier.wait()
            outcomes["cancel"] = cancellation.request_cancel()

        def commit() -> None:
            barrier.wait()
            outcomes["commit"] = cancellation.begin_commit()

        cancel_thread = Thread(target=cancel)
        commit_thread = Thread(target=commit)
        cancel_thread.start()
        commit_thread.start()
        barrier.wait()
        cancel_thread.join(5)
        commit_thread.join(5)
        assert outcomes["cancel"] is not outcomes["commit"]
        assert cancellation.state in {
            ExportCancellationState.CANCEL_REQUESTED,
            ExportCancellationState.COMMITTING,
        }


def test_cancel_before_writer_returns_typed_cancelled_without_invocation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "before.step"
    cancellation = ExportCancellation()
    backend = _WritingBackend()
    assert cancellation.request_cancel()
    result = CadExportService(backend).export(_request(target, cancellation))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.CANCELLED
    assert backend.calls == 0
    assert not target.exists()
    assert cancellation.state is ExportCancellationState.TERMINAL
    _assert_no_temp(tmp_path)


def test_cancel_during_opaque_writer_waits_then_cleans_temp(
    tmp_path: Path,
) -> None:
    target = tmp_path / "during.step"
    entered = Event()
    release = Event()
    cancellation = ExportCancellation()
    service = CadExportService(_HeldBackend(entered, release))
    worker, results = _run_in_thread(service, _request(target, cancellation))
    assert entered.wait(5)
    assert cancellation.request_cancel()
    assert worker.is_alive()
    release.set()
    result = _finish(worker, results)
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.CANCELLED
    assert not target.exists()
    _assert_no_temp(tmp_path)


def test_replace_existing_cancel_preserves_original_bytes_exactly(
    tmp_path: Path,
) -> None:
    target = tmp_path / "replace.step"
    target.write_bytes(b"original-exact-bytes")
    entered = Event()
    release = Event()
    cancellation = ExportCancellation()
    request = _request(
        target,
        cancellation,
        overwrite=ExportOverwritePolicy.REPLACE_EXISTING,
    )
    worker, results = _run_in_thread(
        CadExportService(_HeldBackend(entered, release)), request
    )
    assert entered.wait(5)
    assert cancellation.request_cancel()
    release.set()
    result = _finish(worker, results)
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.CANCELLED
    assert target.read_bytes() == b"original-exact-bytes"
    _assert_no_temp(tmp_path)


def test_cancel_preserves_concurrent_fail_if_exists_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "concurrent.step"
    entered = Event()
    release = Event()
    cancellation = ExportCancellation()

    class _ConcurrentHeldBackend(_HeldBackend):
        def write(
            self, request: ExportRequest, temporary_path: Path
        ) -> BackendWriteMetadata:
            request.target_path.write_bytes(b"competing-exact-bytes")
            return super().write(request, temporary_path)

    worker, results = _run_in_thread(
        CadExportService(_ConcurrentHeldBackend(entered, release)),
        _request(target, cancellation),
    )
    assert entered.wait(5)
    assert cancellation.request_cancel()
    release.set()
    result = _finish(worker, results)
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.CANCELLED
    assert target.read_bytes() == b"competing-exact-bytes"
    _assert_no_temp(tmp_path)


def test_cancel_after_hash_started_still_blocks_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "precommit.step"
    hash_entered = Event()
    release_hash = Event()
    cancellation = ExportCancellation()

    def held_hash(path: Path) -> str:
        assert path.is_file()
        hash_entered.set()
        assert release_hash.wait(5)
        return "a" * 64

    monkeypatch.setattr("hms_cadcam.cad.export_service._sha256", held_hash)
    worker, results = _run_in_thread(
        CadExportService(_WritingBackend()), _request(target, cancellation)
    )
    assert hash_entered.wait(5)
    assert cancellation.request_cancel()
    release_hash.set()
    result = _finish(worker, results)
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.CANCELLED
    assert not target.exists()
    _assert_no_temp(tmp_path)


def test_cancel_loses_after_begin_commit_and_publication_stays_truthful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "commit-wins.step"
    publication_entered = Event()
    release_publication = Event()
    cancellation = ExportCancellation()
    real_rename = os.rename

    def held_rename(source: Path, destination: Path) -> None:
        publication_entered.set()
        assert release_publication.wait(5)
        real_rename(source, destination)

    monkeypatch.setattr("hms_cadcam.cad.export_service.os.rename", held_rename)
    worker, results = _run_in_thread(
        CadExportService(_WritingBackend()), _request(target, cancellation)
    )
    assert publication_entered.wait(5)
    assert cancellation.state is ExportCancellationState.COMMITTING
    assert not cancellation.request_cancel()
    release_publication.set()
    result = _finish(worker, results)
    assert result.success
    assert target.read_bytes() == b"candidate-output"
    assert cancellation.state is ExportCancellationState.TERMINAL
    _assert_no_temp(tmp_path)


def test_genuine_writer_failure_is_not_reclassified_as_cancelled(
    tmp_path: Path,
) -> None:
    target = tmp_path / "writer-fails.step"
    entered = Event()
    release = Event()
    cancellation = ExportCancellation()

    class _FailingHeldBackend(_WritingBackend):
        def write(
            self, request: ExportRequest, temporary_path: Path
        ) -> BackendWriteMetadata:
            entered.set()
            assert release.wait(5)
            temporary_path.write_bytes(b"partial")
            raise RuntimeError("controlled native failure")

    worker, results = _run_in_thread(
        CadExportService(_FailingHeldBackend()), _request(target, cancellation)
    )
    assert entered.wait(5)
    assert cancellation.request_cancel()
    release.set()
    result = _finish(worker, results)
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.WRITE_FAILED
    assert not target.exists()
    _assert_no_temp(tmp_path)


def test_cancelled_cleanup_failure_is_explicit_and_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cleanup-fails.step"
    entered = Event()
    release = Event()
    cancellation = ExportCancellation()
    real_unlink = Path.unlink

    def deny_temp_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.endswith(".hms-exporting"):
            raise PermissionError("controlled cancelled cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_temp_unlink)
    worker, results = _run_in_thread(
        CadExportService(_HeldBackend(entered, release)),
        _request(target, cancellation),
    )
    assert entered.wait(5)
    assert cancellation.request_cancel()
    release.set()
    result = _finish(worker, results)
    residue = tuple(tmp_path.glob("*.hms-exporting"))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.TEMP_CLEANUP_FAILED
    assert ExportErrorCode.CANCELLED.value in result.failure.message
    assert not target.exists()
    assert len(residue) == 1
    for path in residue:
        real_unlink(path)
