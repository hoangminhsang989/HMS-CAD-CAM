"""Deterministic, checksummed standalone ``.HMS`` CAD document containers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.exceptions import (
    HmsContainerDamagedError,
    HmsContainerError,
    HmsContainerSecurityError,
    ProjectPermissionError,
    SourceFileNotFoundError,
)
from hms_cadcam.project.filesystem import sha256_file
from hms_cadcam.project.models import datetime_from_json, datetime_to_json, utc_now
from hms_cadcam.project.path_policy import ensure_hms_suffix, validate_hms_filename
from hms_cadcam.project.workspace import (
    CadDocumentSession,
    DocumentMode,
    PreparedDocumentOpen,
    SourceProvenance,
    WorkspaceState,
)

DOCUMENT_CONTAINER_FORMAT = "HMS_CAD_DOCUMENT"
DOCUMENT_CONTAINER_VERSION = 1
DOCUMENT_MANIFEST_ENTRY = "manifest.json"
DOCUMENT_METADATA_ENTRY = "document.json"
DOCUMENT_CAD_METADATA_ENTRY = "cad/metadata.json"
DOCUMENT_DISPLAY_STATE_ENTRY = "cad/display-state.json"
DOCUMENT_CHECKSUM_ENTRY = "checksums.json"
MAX_CONTAINER_ENTRIES = 64
MAX_CONTAINER_ENTRY_BYTES = 1024 * 1024 * 1024
MAX_CONTAINER_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _canonical_json(data: object) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _require_json_value(value: object, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _require_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"{path} contains a non-JSON value")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0o600 << 16
    return info


def _is_safe_entry(name: str) -> bool:
    pure = PurePosixPath(name)
    return (
        bool(pure.parts)
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in name
        and not name.startswith(("/", "\\"))
        and ":" not in pure.parts[0]
    )


class HmsDocumentContainer:
    """Create/open/save standalone CAD documents without exposing ZIP to UI."""

    def __init__(self, runtime_root: Path, default_save_directory: Path) -> None:
        self._runtime_root = runtime_root
        self._default_save_directory = default_save_directory
        self._generation = 0

    @property
    def runtime_root(self) -> Path:
        """Return the service-owned extraction/recovery root."""
        return self._runtime_root

    def prepare_source(self, source_path: Path) -> PreparedDocumentOpen:
        """Validate one supported source and prepare unsaved CAD_DOCUMENT state."""
        if not source_path.is_file():
            raise SourceFileNotFoundError(f"File nguồn không tồn tại: {source_path}")
        fingerprint = sha256_file(source_path)
        now = utc_now()
        document_id = uuid4()
        self._generation += 1
        suggested = self.suggested_save_directory(source_path=source_path)
        state = WorkspaceState(
            mode=DocumentMode.CAD_DOCUMENT,
            document_id=document_id,
            project_id=None,
            display_name=source_path.stem,
            physical_path=None,
            source_path=source_path,
            suggested_save_directory=suggested,
            dirty=True,
            read_only=False,
            opened_at=now,
            session_id=uuid4(),
            format_version=DOCUMENT_CONTAINER_VERSION,
            lifecycle_generation=self._generation,
        )
        provenance = SourceProvenance(
            original_filename=source_path.name,
            original_path=source_path,
            internal_filename=source_path.name,
            source_fingerprint=fingerprint,
            imported_at=now,
            importer=source_path.suffix.lower().lstrip(".") or "unknown",
            units="unknown",
            geometry_type=source_path.suffix.lower().lstrip(".") or "unknown",
            read_only=True,
        )
        return PreparedDocumentOpen.for_session(
            CadDocumentSession(
                state=state,
                geometry_path=source_path,
                provenance=provenance,
            )
        )

    def prepare_container(self, container_path: Path) -> PreparedDocumentOpen:
        """Validate and safely extract one existing standalone HMS container."""
        validate_hms_filename(container_path.name)
        if not container_path.is_file():
            raise SourceFileNotFoundError(
                f"Tài liệu HMS không tồn tại: {container_path}"
            )
        extraction_root = self._new_extraction_root()
        try:
            payloads = self._read_validated_payloads(container_path)
            document = self._decode_document(payloads[DOCUMENT_METADATA_ENTRY])
            manifest = self._decode_manifest(payloads[DOCUMENT_MANIFEST_ENTRY])
            geometry_entry = str(document["geometry_entry"])
            if geometry_entry not in payloads:
                raise HmsContainerDamagedError(
                    "Container thiếu dữ liệu hình học được tham chiếu."
                )
            geometry_path = extraction_root / Path(geometry_entry)
            geometry_path.parent.mkdir(parents=True, exist_ok=True)
            geometry_path.write_bytes(payloads[geometry_entry])
            provenance = SourceProvenance.from_dict(
                document["source_provenance"]
            )
            document_id = UUID(str(document["document_id"]))
            container_id = UUID(
                str(document.get("container_id", document_id))
            )
            geometry_version = document.get("geometry_version", 1)
            if type(geometry_version) is not int or geometry_version < 1:
                raise TypeError("geometry_version must be a positive integer")
            if document_id != UUID(str(manifest["document_id"])):
                raise HmsContainerDamagedError(
                    "Document identity không khớp manifest."
                )
            self._generation += 1
            state = WorkspaceState(
                mode=DocumentMode.CAD_DOCUMENT,
                document_id=document_id,
                project_id=None,
                display_name=str(document["display_name"]),
                physical_path=container_path,
                source_path=provenance.original_path,
                suggested_save_directory=self.suggested_save_directory(
                    physical_path=container_path,
                    source_path=provenance.original_path,
                ),
                dirty=False,
                read_only=not os.access(container_path, os.W_OK),
                opened_at=utc_now(),
                session_id=uuid4(),
                format_version=DOCUMENT_CONTAINER_VERSION,
                lifecycle_generation=self._generation,
            )
            cad_metadata = json.loads(
                payloads[DOCUMENT_CAD_METADATA_ENTRY].decode("utf-8")
            )
            display_state = json.loads(
                payloads[DOCUMENT_DISPLAY_STATE_ENTRY].decode("utf-8")
            )
            if not isinstance(cad_metadata, dict) or not isinstance(
                display_state, dict
            ):
                raise TypeError("CAD metadata/display state must be objects")
            recovery = document.get("recovery_metadata")
            if recovery is not None and not isinstance(recovery, dict):
                raise TypeError("recovery_metadata must be an object or null")
            session = CadDocumentSession(
                state=state,
                geometry_path=geometry_path,
                provenance=provenance,
                container_id=container_id,
                geometry_version=geometry_version,
                created_at=datetime_from_json(str(document["created_at"])),
                cad_metadata=cad_metadata,
                display_state=display_state,
                extraction_root=extraction_root,
                recovery_metadata=recovery,
            )
            return PreparedDocumentOpen.for_session(session)
        except Exception:
            self._remove_extraction_root(extraction_root)
            raise

    def save(self, session: CadDocumentSession, target: Path | None = None) -> Path:
        """Atomically save and revalidate a standalone HMS document."""
        destination = target or session.state.physical_path
        if destination is None:
            raise HmsContainerError("Lần lưu đầu tiên cần đường dẫn .HMS.")
        if destination.suffix.casefold() != ".hms":
            destination = destination.with_name(ensure_hms_suffix(destination.name))
        validate_hms_filename(destination.name)
        if not destination.parent.is_dir():
            raise ProjectPermissionError(
                f"Thư mục lưu không tồn tại: {destination.parent}"
            )
        if session.state.read_only and target is None:
            raise ProjectPermissionError("Tài liệu đang mở ở chế độ chỉ đọc.")
        previous_state = session.state
        session.state = session.state.with_changes(
            physical_path=destination,
            suggested_save_directory=destination.parent,
            display_name=destination.stem,
            dirty=False,
            read_only=False,
        )
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.saving"
        )
        validation_root: Path | None = None
        try:
            entries = self._build_entries(session)
            with temporary.open("wb") as raw:
                with zipfile.ZipFile(
                    raw,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                    strict_timestamps=True,
                ) as archive:
                    for name in sorted(entries):
                        archive.writestr(_zip_info(name), entries[name])
                raw.flush()
                os.fsync(raw.fileno())
            if temporary.stat().st_size <= 0:
                raise HmsContainerError("File HMS tạm rỗng.")
            payloads = self._read_validated_payloads(temporary)
            if set(payloads) != set(entries):
                raise HmsContainerDamagedError(
                    "Container tự kiểm tra không khớp danh sách entry."
                )
            os.replace(temporary, destination)
        except PermissionError as error:
            session.state = previous_state
            raise ProjectPermissionError(str(error)) from error
        except (OSError, zipfile.BadZipFile) as error:
            session.state = previous_state
            raise HmsContainerError(str(error)) from error
        except Exception:
            session.state = previous_state
            raise
        finally:
            temporary.unlink(missing_ok=True)
            if validation_root is not None:
                self._remove_extraction_root(validation_root)
        self._generation += 1
        session.state = session.state.with_changes(
            lifecycle_generation=self._generation
        )
        return destination

    def autosave(self, session: CadDocumentSession) -> Path | None:
        """Write a recovery container without changing document dirty state."""
        if not session.state.dirty:
            return None
        recovery_root = self._runtime_root / "recovery"
        recovery_root.mkdir(parents=True, exist_ok=True)
        recovery_path = recovery_root / f"{session.state.session_id}.HMS"
        previous_path = session.state.physical_path
        previous_dirty = session.state.dirty
        previous_name = session.state.display_name
        previous_recovery = session.recovery_metadata
        session.recovery_metadata = {
            "session_id": str(session.state.session_id),
            "source_physical_path": (
                None if previous_path is None else str(previous_path)
            ),
            "created_at": datetime_to_json(utc_now()),
        }
        try:
            self.save(session, recovery_path)
        finally:
            session.state = session.state.with_changes(
                physical_path=previous_path,
                display_name=previous_name,
                dirty=previous_dirty,
            )
            session.recovery_metadata = previous_recovery
        return recovery_path

    def close(self, session: CadDocumentSession) -> None:
        """Release only service-owned extracted data; never touch source/HMS files."""
        if session.extraction_root is not None:
            self._remove_extraction_root(session.extraction_root)

    def suggested_save_directory(
        self,
        *,
        physical_path: Path | None = None,
        source_path: Path | None = None,
        last_valid_directory: Path | None = None,
    ) -> Path:
        """Resolve current HMS, source, last-valid, then safe application fallback."""
        candidates = (
            None if physical_path is None else physical_path.parent,
            None if source_path is None else source_path.parent,
            last_valid_directory,
            self._default_save_directory,
        )
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                if candidate.is_dir() and os.access(candidate, os.W_OK):
                    return candidate
            except OSError:
                continue
        self._default_save_directory.mkdir(parents=True, exist_ok=True)
        return self._default_save_directory

    def _build_entries(self, session: CadDocumentSession) -> dict[str, bytes]:
        if not session.geometry_path.is_file():
            raise SourceFileNotFoundError(
                f"Không tìm thấy hình học làm việc: {session.geometry_path}"
            )
        _require_json_value(session.cad_metadata, path="cad_metadata")
        _require_json_value(session.display_state, path="display_state")
        suffix = session.geometry_path.suffix.lower()
        geometry_entry = f"geometry/model{suffix}"
        document = {
            "format": DOCUMENT_CONTAINER_FORMAT,
            "format_version": DOCUMENT_CONTAINER_VERSION,
            "document_id": str(session.state.identity),
            "container_id": str(session.container_id),
            "geometry_version": session.geometry_version,
            "display_name": session.state.display_name,
            "created_at": datetime_to_json(session.created_at),
            "geometry_entry": geometry_entry,
            "source_provenance": session.provenance.to_dict(),
            "recovery_metadata": session.recovery_metadata,
        }
        entries = {
            DOCUMENT_MANIFEST_ENTRY: _canonical_json(
                {
                    "format": DOCUMENT_CONTAINER_FORMAT,
                    "format_version": DOCUMENT_CONTAINER_VERSION,
                    "document_id": str(session.state.identity),
                    "checksum_algorithm": "sha256",
                    "content_entries": [
                        DOCUMENT_METADATA_ENTRY,
                        DOCUMENT_CAD_METADATA_ENTRY,
                        DOCUMENT_DISPLAY_STATE_ENTRY,
                        geometry_entry,
                    ],
                }
            ),
            DOCUMENT_METADATA_ENTRY: _canonical_json(document),
            DOCUMENT_CAD_METADATA_ENTRY: _canonical_json(session.cad_metadata),
            DOCUMENT_DISPLAY_STATE_ENTRY: _canonical_json(session.display_state),
            geometry_entry: session.geometry_path.read_bytes(),
        }
        checksums = {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in sorted(entries.items())
        }
        entries[DOCUMENT_CHECKSUM_ENTRY] = _canonical_json(
            {"algorithm": "sha256", "entries": checksums}
        )
        return entries

    def _read_validated_payloads(self, path: Path) -> dict[str, bytes]:
        try:
            with zipfile.ZipFile(path, mode="r") as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_CONTAINER_ENTRIES:
                    raise HmsContainerSecurityError(
                        "Số entry trong container vượt giới hạn."
                    )
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    raise HmsContainerSecurityError("Container có entry trùng tên.")
                total = 0
                for info in infos:
                    if not _is_safe_entry(info.filename):
                        raise HmsContainerSecurityError(
                            f"Entry không an toàn: {info.filename}"
                        )
                    mode = info.external_attr >> 16
                    if mode and stat.S_ISLNK(mode):
                        raise HmsContainerSecurityError(
                            "Container không cho phép symbolic link."
                        )
                    if info.flag_bits & 0x1:
                        raise HmsContainerSecurityError(
                            "Container mã hóa không được hỗ trợ."
                        )
                    if info.file_size > MAX_CONTAINER_ENTRY_BYTES:
                        raise HmsContainerSecurityError(
                            "Entry vượt giới hạn tài nguyên."
                        )
                    if (
                        info.file_size > 1024 * 1024
                        and info.compress_size > 0
                        and info.file_size / info.compress_size
                        > MAX_COMPRESSION_RATIO
                    ):
                        raise HmsContainerSecurityError(
                            "Tỷ lệ nén container không an toàn."
                        )
                    total += info.file_size
                if total > MAX_CONTAINER_TOTAL_BYTES:
                    raise HmsContainerSecurityError(
                        "Tổng dữ liệu container vượt giới hạn."
                    )
                required = {
                    DOCUMENT_MANIFEST_ENTRY,
                    DOCUMENT_METADATA_ENTRY,
                    DOCUMENT_CAD_METADATA_ENTRY,
                    DOCUMENT_DISPLAY_STATE_ENTRY,
                    DOCUMENT_CHECKSUM_ENTRY,
                }
                if not required.issubset(names):
                    raise HmsContainerDamagedError(
                        "Container thiếu manifest/metadata/checksum."
                    )
                payloads = {
                    info.filename: archive.read(info)
                    for info in infos
                    if not info.is_dir()
                }
        except zipfile.BadZipFile as error:
            raise HmsContainerDamagedError("File HMS không phải ZIP hợp lệ.") from error
        except PermissionError as error:
            raise ProjectPermissionError(str(error)) from error
        try:
            checksums = json.loads(
                payloads[DOCUMENT_CHECKSUM_ENTRY].decode("utf-8")
            )
            if (
                not isinstance(checksums, dict)
                or checksums.get("algorithm") != "sha256"
                or not isinstance(checksums.get("entries"), dict)
            ):
                raise TypeError("Invalid checksum payload")
            expected = checksums["entries"]
            if set(expected) != set(payloads) - {DOCUMENT_CHECKSUM_ENTRY}:
                raise ValueError("Checksum entry set mismatch")
            for name, digest in expected.items():
                if (
                    not isinstance(name, str)
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or hashlib.sha256(payloads[name]).hexdigest() != digest
                ):
                    raise ValueError(f"Checksum mismatch: {name}")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise HmsContainerDamagedError(
                "Checksum container không hợp lệ hoặc không khớp."
            ) from error
        return payloads

    @staticmethod
    def _decode_manifest(payload: bytes) -> dict[str, Any]:
        try:
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("manifest root must be object")
            if data.get("format") != DOCUMENT_CONTAINER_FORMAT:
                raise ValueError("unsupported document format")
            if data.get("format_version") != DOCUMENT_CONTAINER_VERSION:
                raise ValueError("unsupported document version")
            if not isinstance(data.get("document_id"), str):
                raise TypeError("document_id must be text")
            UUID(data["document_id"])
            return data
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise HmsContainerDamagedError(
                "Manifest tài liệu HMS không hợp lệ."
            ) from error

    @staticmethod
    def _decode_document(payload: bytes) -> dict[str, Any]:
        try:
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("document root must be object")
            required_text = (
                "format",
                "document_id",
                "display_name",
                "created_at",
                "geometry_entry",
            )
            if any(not isinstance(data.get(key), str) for key in required_text):
                raise TypeError("document text fields must be strings")
            if data["format"] != DOCUMENT_CONTAINER_FORMAT:
                raise ValueError("unsupported document format")
            if data.get("format_version") != DOCUMENT_CONTAINER_VERSION:
                raise ValueError("unsupported document version")
            if not _is_safe_entry(data["geometry_entry"]):
                raise ValueError("unsafe geometry entry")
            UUID(data["document_id"])
            datetime_from_json(data["created_at"])
            SourceProvenance.from_dict(data.get("source_provenance"))
            return data
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise HmsContainerDamagedError(
                "Metadata tài liệu HMS không hợp lệ."
            ) from error

    def _new_extraction_root(self) -> Path:
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        return Path(
            tempfile.mkdtemp(prefix="document-", dir=self._runtime_root)
        )

    def _remove_extraction_root(self, extraction_root: Path) -> None:
        try:
            runtime = self._runtime_root.resolve()
            resolved = extraction_root.resolve()
            if resolved.parent != runtime or not resolved.name.startswith("document-"):
                raise HmsContainerSecurityError(
                    "Từ chối xóa thư mục không thuộc runtime tài liệu HMS."
                )
            shutil.rmtree(resolved, ignore_errors=False)
        except FileNotFoundError:
            return
