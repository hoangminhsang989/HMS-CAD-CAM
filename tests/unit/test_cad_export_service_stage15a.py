"""Stage15A filesystem, failure, and atomic-publication matrix."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from hms_cadcam.cad.export_models import (
    ExportEntityKind,
    ExportFormatId,
    ExportProfile,
    ExportSelectionRef,
)
from hms_cadcam.cad.export_service import (
    BackendWriteMetadata,
    CadExportService,
    ExportErrorCode,
    ExportOverwritePolicy,
    ExportRequest,
    UnavailableCadExportBackend,
)
from hms_cadcam.cad.models import CadDocumentId


DOCUMENT_ID = CadDocumentId("cad-document-stage15a")


class _Backend:
    supported_formats = frozenset(
        {
            ExportFormatId.STEP,
            ExportFormatId.IGES,
            ExportFormatId.STL,
            ExportFormatId.BREP,
        }
    )
    unavailable_reason = None

    def __init__(self, mode: str = "write") -> None:
        self.mode = mode

    def write(self, request: ExportRequest, temporary_path: Path) -> BackendWriteMetadata:
        if self.mode == "raise":
            temporary_path.write_bytes(b"partial")
            raise RuntimeError("writer exploded")
        if self.mode == "empty":
            temporary_path.touch()
        else:
            temporary_path.write_bytes(
                f"{request.profile.format_id.value}:{len(request.selections)}".encode()
            )
        return BackendWriteMetadata("test writer", max(1, len(request.selections)))


def _request(
    target: Path,
    format_id: ExportFormatId = ExportFormatId.STEP,
    *,
    overwrite: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS,
    selections: tuple[ExportSelectionRef, ...] = (),
) -> ExportRequest:
    profile = replace(
        ExportProfile.default_for(format_id),
        overwrite_policy=overwrite,
    )
    return ExportRequest(
        DOCUMENT_ID,
        target,
        profile,
        selections,
        overwrite,
    )


def _assert_no_temp(parent: Path) -> None:
    assert not tuple(parent.glob("*.hms-exporting"))


def test_success_publishes_nonempty_metadata_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "part.step"
    result = CadExportService(_Backend()).export(_request(target))
    assert result.success
    assert target.read_bytes() == b"step:0"
    assert result.bytes_written == target.stat().st_size
    assert result.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert not result.replaced_existing
    _assert_no_temp(tmp_path)


def test_unsupported_and_profile_mismatch_create_nothing(tmp_path: Path) -> None:
    unsupported = tmp_path / "part.unknown"
    result = CadExportService(_Backend()).export(_request(unsupported))
    assert result.failure.code is ExportErrorCode.UNSUPPORTED_EXTENSION
    mismatch = tmp_path / "part.iges"
    result = CadExportService(_Backend()).export(_request(mismatch))
    assert result.failure.code is ExportErrorCode.PROFILE_EXTENSION_MISMATCH
    assert not unsupported.exists() and not mismatch.exists()
    _assert_no_temp(tmp_path)


def test_backend_unavailable_is_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "part.step"
    result = CadExportService(
        UnavailableCadExportBackend("OCP missing")
    ).export(_request(target))
    assert result.failure.code is ExportErrorCode.BACKEND_UNAVAILABLE
    assert not target.exists()


def test_missing_parent_and_directory_destination_are_typed(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "part.step"
    result = CadExportService(_Backend()).export(_request(missing))
    assert result.failure.code is ExportErrorCode.PARENT_MISSING
    directory = tmp_path / "part.step"
    directory.mkdir()
    result = CadExportService(_Backend()).export(_request(directory))
    assert result.failure.code is ExportErrorCode.DESTINATION_INVALID


def test_existing_output_policy_is_explicit_and_replace_is_atomic(tmp_path: Path) -> None:
    target = tmp_path / "part.step"
    target.write_bytes(b"old")
    denied = CadExportService(_Backend()).export(_request(target))
    assert denied.failure.code is ExportErrorCode.FILE_EXISTS
    assert target.read_bytes() == b"old"
    replaced = CadExportService(_Backend()).export(
        _request(target, overwrite=ExportOverwritePolicy.REPLACE_EXISTING)
    )
    assert replaced.success and replaced.replaced_existing
    assert target.read_bytes() == b"step:0"
    _assert_no_temp(tmp_path)


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("raise", ExportErrorCode.WRITE_FAILED),
        ("empty", ExportErrorCode.EMPTY_OUTPUT),
    ],
)
def test_writer_failure_or_empty_output_preserves_final_and_cleans_temp(
    tmp_path: Path, mode: str, code: ExportErrorCode
) -> None:
    target = tmp_path / "part.step"
    target.write_bytes(b"known-good")
    result = CadExportService(_Backend(mode)).export(
        _request(target, overwrite=ExportOverwritePolicy.REPLACE_EXISTING)
    )
    assert result.failure.code is code
    assert target.read_bytes() == b"known-good"
    _assert_no_temp(tmp_path)


def test_atomic_replace_failure_preserves_final_and_cleans_temp(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "part.step"
    target.write_bytes(b"known-good")

    def deny_replace(_source, _target):
        raise PermissionError("destination is unwritable")

    monkeypatch.setattr("hms_cadcam.cad.export_service.os.replace", deny_replace)
    result = CadExportService(_Backend()).export(
        _request(target, overwrite=ExportOverwritePolicy.REPLACE_EXISTING)
    )
    assert result.failure.code is ExportErrorCode.ATOMIC_REPLACE_FAILED
    assert target.read_bytes() == b"known-good"
    _assert_no_temp(tmp_path)


def test_fail_if_exists_concurrent_create_is_atomic_and_preserves_competing_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "race.step"

    class _ConcurrentCreator(_Backend):
        def write(
            self, request: ExportRequest, temporary_path: Path
        ) -> BackendWriteMetadata:
            request.target_path.write_bytes(b"concurrent-known-good")
            temporary_path.write_bytes(b"candidate-output")
            return BackendWriteMetadata("race writer", 1)

    result = CadExportService(_ConcurrentCreator()).export(_request(target))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.FILE_EXISTS
    assert target.read_bytes() == b"concurrent-known-good"
    _assert_no_temp(tmp_path)


def test_no_replace_publication_primitive_failure_is_typed_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unavailable.step"

    def deny_rename(_source, _target):
        raise PermissionError("no-replace rename publication unavailable")

    monkeypatch.setattr("hms_cadcam.cad.export_service.os.rename", deny_rename)
    result = CadExportService(_Backend()).export(_request(target))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.ATOMIC_PUBLICATION_FAILED
    assert not target.exists()
    _assert_no_temp(tmp_path)


def test_no_replace_publication_supports_unicode_windows_path_and_final_hash(
    tmp_path: Path,
) -> None:
    target = tmp_path / "chi-tiết-한글.step"
    result = CadExportService(_Backend()).export(_request(target))
    assert result.success
    assert result.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.bytes_written == len(target.read_bytes())
    _assert_no_temp(tmp_path)


@pytest.mark.parametrize(
    "overwrite",
    (
        ExportOverwritePolicy.FAIL_IF_EXISTS,
        ExportOverwritePolicy.REPLACE_EXISTING,
    ),
)
def test_metadata_failure_happens_before_publication_and_preserves_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overwrite: ExportOverwritePolicy,
) -> None:
    target = tmp_path / "metadata.step"
    if overwrite is ExportOverwritePolicy.REPLACE_EXISTING:
        target.write_bytes(b"known-good")
    publications: list[tuple[Path, Path]] = []

    def fail_hash(_path: Path) -> str:
        raise OSError("controlled metadata failure")

    def record_publication(source: Path, destination: Path) -> None:
        publications.append((source, destination))

    monkeypatch.setattr("hms_cadcam.cad.export_service._sha256", fail_hash)
    monkeypatch.setattr("hms_cadcam.cad.export_service.os.rename", record_publication)
    monkeypatch.setattr("hms_cadcam.cad.export_service.os.replace", record_publication)
    result = CadExportService(_Backend()).export(
        _request(target, overwrite=overwrite)
    )
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.WRITE_FAILED
    assert publications == []
    if overwrite is ExportOverwritePolicy.REPLACE_EXISTING:
        assert target.read_bytes() == b"known-good"
    else:
        assert not target.exists()
    _assert_no_temp(tmp_path)


def test_cleanup_failure_is_explicitly_typed_and_preserves_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cleanup.step"
    real_unlink = Path.unlink

    def deny_rename(_source: Path, _target: Path) -> None:
        raise PermissionError("controlled publication failure")

    def deny_temporary_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.endswith(".hms-exporting"):
            raise PermissionError("controlled cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("hms_cadcam.cad.export_service.os.rename", deny_rename)
    monkeypatch.setattr(Path, "unlink", deny_temporary_unlink)
    result = CadExportService(_Backend()).export(_request(target))
    residue = tuple(tmp_path.glob("*.hms-exporting"))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.TEMP_CLEANUP_FAILED
    assert ExportErrorCode.ATOMIC_PUBLICATION_FAILED.value in result.failure.message
    assert not target.exists()
    assert len(residue) == 1
    for path in residue:
        real_unlink(path)


def test_fail_if_exists_fails_closed_off_verified_windows_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unsupported-platform.step"
    publications: list[tuple[Path, Path]] = []
    monkeypatch.setattr("hms_cadcam.cad.export_service.os.name", "posix")
    monkeypatch.setattr(
        "hms_cadcam.cad.export_service.os.rename",
        lambda source, destination: publications.append((source, destination)),
    )
    result = CadExportService(_Backend()).export(_request(target))
    assert result.failure is not None
    assert result.failure.code is ExportErrorCode.ATOMIC_PUBLICATION_FAILED
    assert publications == []
    assert not target.exists()
    _assert_no_temp(tmp_path)


def test_unsupported_selected_kind_never_falls_back_to_document(tmp_path: Path) -> None:
    selection = ExportSelectionRef(
        DOCUMENT_ID,
        f"{DOCUMENT_ID}:wire:1",
        ExportEntityKind.WIRE,
    )
    target = tmp_path / "part.stl"
    result = CadExportService(_Backend()).export(
        _request(target, ExportFormatId.STL, selections=(selection,))
    )
    assert result.failure.code is ExportErrorCode.SELECTION_EXPORT_UNAVAILABLE
    assert not target.exists()
