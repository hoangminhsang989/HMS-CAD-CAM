"""Project-local, atomic transfer of exact geometry from HMS documents to CAM."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    AUTOSAVE_DIRECTORY,
    BACKUPS_DIRECTORY,
    CAM_WORKSPACE_MANIFEST_FILENAME,
    DATABASE_FILENAME,
    DATABASE_SCHEMA_VERSION,
    INCOMING_GEOMETRY_APPLIED_DIRECTORY,
    INCOMING_GEOMETRY_DIRECTORY,
    INCOMING_GEOMETRY_FAILED_DIRECTORY,
    INCOMING_GEOMETRY_PENDING_DIRECTORY,
    INCOMING_GEOMETRY_REJECTED_DIRECTORY,
    INCOMING_GEOMETRY_STAGING_DIRECTORY,
    PROJECT_FORMAT_VERSION,
    REPLACED_DIRECTORY,
    SOURCE_DIRECTORY,
    TEMP_DIRECTORY,
    WORKING_GEOMETRY_DIRECTORY,
)
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import (
    GeometryTransferDuplicateError,
    GeometryTransferIntegrityError,
    GeometryTransferTargetError,
    ProjectDatabaseError,
    ProjectPermissionError,
    UnsafeWorkspacePathError,
)
from hms_cadcam.project.filesystem import copy_source_verified, sha256_file
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import (
    ProjectManifest,
    ProjectSession,
    SourceFileRecord,
    UnitSystem,
    datetime_from_json,
    datetime_to_json,
    utc_now,
)
from hms_cadcam.project.path_policy import (
    normalize_internal_source_filename,
    validate_existing_cam_root_path,
)
from hms_cadcam.project.session_lock import LockState, SessionLockManager
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.project.workspace import CadDocumentSession, DocumentMode

logger = logging.getLogger(__name__)

GEOMETRY_TRANSFER_SCHEMA_VERSION = 1
GEOMETRY_TRANSFER_CHECKSUM_ALGORITHM = "sha256"
REQUEST_DIRECTORY_PREFIX = "request-"
REQUEST_METADATA_FILENAME = "request.json"
REQUEST_CHECKSUM_FILENAME = "checksums.json"
REQUEST_GEOMETRY_DIRECTORY = "geometry"
REQUEST_PREVIEW_DIRECTORY = "preview"
APPLYING_SUFFIX = ".applying"
GEOMETRY_APPLY_EVIDENCE_FILENAME = "apply-transaction.json"
MAX_TRANSFER_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
MINIMUM_FREE_SPACE_BYTES = 64 * 1024 * 1024
_SHA256 = frozenset("0123456789abcdef")
_REQUIRED_PROJECT_DIRECTORIES = (
    SOURCE_DIRECTORY,
    WORKING_GEOMETRY_DIRECTORY,
    AUTOSAVE_DIRECTORY,
    BACKUPS_DIRECTORY,
    TEMP_DIRECTORY,
    REPLACED_DIRECTORY,
)
_INBOX_STATE_DIRECTORIES = (
    INCOMING_GEOMETRY_STAGING_DIRECTORY,
    INCOMING_GEOMETRY_PENDING_DIRECTORY,
    INCOMING_GEOMETRY_APPLIED_DIRECTORY,
    INCOMING_GEOMETRY_REJECTED_DIRECTORY,
    INCOMING_GEOMETRY_FAILED_DIRECTORY,
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256 for character in value)
    )


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _replace_fsynced(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_existing_file(path: Path) -> None:
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


class GeometryTransferStatus(StrEnum):
    """Persistent lifecycle for one incoming geometry request."""

    STAGING = "staging"
    PENDING = "pending"
    DEFERRED = "deferred"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"

    @property
    def display_text(self) -> str:
        return {
            GeometryTransferStatus.STAGING: "Đang chuẩn bị",
            GeometryTransferStatus.PENDING: "Chờ xử lý",
            GeometryTransferStatus.DEFERRED: "Để sau",
            GeometryTransferStatus.APPLYING: "Đang cập nhật",
            GeometryTransferStatus.APPLIED: "Đã áp dụng",
            GeometryTransferStatus.REJECTED: "Đã bỏ qua",
            GeometryTransferStatus.FAILED: "Lỗi",
        }[self]


class GeometryRequestedAction(StrEnum):
    """Sender intent; an unsafe apply choice is never implied."""

    UNSPECIFIED = "unspecified"

    @property
    def display_text(self) -> str:
        return "Chưa chọn cách cập nhật"


class GeometryRepresentation(StrEnum):
    """Accuracy contract carried with the payload."""

    EXACT_BREP = "exact_brep"
    EXACT_EXCHANGE = "exact_exchange"
    MESH_ONLY = "mesh_only"

    @property
    def display_text(self) -> str:
        return {
            GeometryRepresentation.EXACT_BREP: "Hình học BRep chính xác",
            GeometryRepresentation.EXACT_EXCHANGE: "Hình học trao đổi chính xác",
            GeometryRepresentation.MESH_ONLY: "Dữ liệu lưới chỉ để xem",
        }[self]

    @property
    def exact_for_cam(self) -> bool:
        return self is not GeometryRepresentation.MESH_ONLY

    @classmethod
    def for_path(cls, path: Path) -> "GeometryRepresentation":
        suffix = path.suffix.casefold()
        if suffix in {".brep", ".brp"}:
            return cls.EXACT_BREP
        if suffix in {".step", ".stp", ".iges", ".igs"}:
            return cls.EXACT_EXCHANGE
        if suffix == ".stl":
            return cls.MESH_ONLY
        raise GeometryTransferIntegrityError(
            "Định dạng hình học trong tài liệu HMS chưa được hỗ trợ để nạp."
        )


class GeometryApplyChoice(StrEnum):
    """Explicit user-approved ways to incorporate an incoming model."""

    ADD_NEW = "add_new"
    REPLACE_EXISTING = "replace_existing"
    UPDATE_MATCHING = "update_matching"

    @property
    def display_text(self) -> str:
        return {
            GeometryApplyChoice.ADD_NEW: "Thêm làm mô hình mới",
            GeometryApplyChoice.REPLACE_EXISTING: "Thay thế mô hình hiện tại",
            GeometryApplyChoice.UPDATE_MATCHING: (
                "Cập nhật phiên bản mô hình tương ứng"
            ),
        }[self]


class GeometryApplyPhase(StrEnum):
    PREPARED = "prepared"
    FILES_COMMITTED = "files_committed"
    PERSISTED = "persisted"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class CamProjectTargetInspection:
    """UI-safe validation result for one selected CAM project root."""

    root_path: Path
    project_name: str | None
    project_id: UUID | None
    workspace_version: int | None
    valid: bool
    reason: str
    active_session_detected: bool = False

    @property
    def status_text(self) -> str:
        return "Dự án hợp lệ" if self.valid else "Dự án CAM không hợp lệ"


@dataclass(frozen=True, slots=True)
class GeometryTransferRequest:
    """Strict metadata for one complete geometry payload."""

    schema_version: int
    request_id: UUID
    source_document_id: UUID
    source_container_id: UUID
    source_hms_path: Path
    source_display_name: str
    source_original_filename: str
    source_container_version: int
    source_geometry_version: int
    source_geometry_fingerprint: str
    source_container_fingerprint: str
    target_project_id: UUID
    target_workspace_version: int
    created_at_utc: datetime
    created_by_application_version: str
    geometry_units: str
    geometry_representation: GeometryRepresentation
    solid_count: int
    face_count: int
    edge_count: int
    requested_action: GeometryRequestedAction
    status: GeometryTransferStatus
    checksum_algorithm: str
    payload_checksum: str
    metadata_checksum: str
    payload_filename: str
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != GEOMETRY_TRANSFER_SCHEMA_VERSION:
            raise GeometryTransferIntegrityError(
                "Phiên bản yêu cầu nạp 3D chưa được hỗ trợ."
            )
        if any(
            not isinstance(value, UUID) or value.int == 0
            for value in (
                self.request_id,
                self.source_document_id,
                self.source_container_id,
                self.target_project_id,
            )
        ):
            raise GeometryTransferIntegrityError(
                "Định danh yêu cầu nạp 3D không hợp lệ."
            )
        if (
            not self.source_hms_path.is_absolute()
            or self.source_hms_path.suffix.casefold() != ".hms"
        ):
            raise GeometryTransferIntegrityError(
                "Đường dẫn tài liệu HMS nguồn không hợp lệ."
            )
        text_values = (
            self.source_display_name,
            self.source_original_filename,
            self.created_by_application_version,
            self.geometry_units,
            self.payload_filename,
        )
        if any(not isinstance(value, str) or not value.strip() for value in text_values):
            raise GeometryTransferIntegrityError(
                "Metadata văn bản của yêu cầu nạp 3D không hợp lệ."
            )
        if Path(self.payload_filename).name != self.payload_filename:
            raise GeometryTransferIntegrityError(
                "Tên payload hình học không được chứa đường dẫn."
            )
        if (
            type(self.source_container_version) is not int
            or self.source_container_version < 1
            or type(self.source_geometry_version) is not int
            or self.source_geometry_version < 1
            or type(self.target_workspace_version) is not int
            or self.target_workspace_version < 1
        ):
            raise GeometryTransferIntegrityError(
                "Phiên bản tài liệu/workspace không hợp lệ."
            )
        if self.created_at_utc.tzinfo is None:
            raise GeometryTransferIntegrityError(
                "Thời gian yêu cầu phải có múi giờ UTC."
            )
        if any(
            type(value) is not int or value < 0
            for value in (self.solid_count, self.face_count, self.edge_count)
        ):
            raise GeometryTransferIntegrityError(
                "Thống kê topology không hợp lệ."
            )
        if not isinstance(self.geometry_representation, GeometryRepresentation):
            raise GeometryTransferIntegrityError(
                "Kiểu biểu diễn hình học không hợp lệ."
            )
        if not isinstance(self.requested_action, GeometryRequestedAction):
            raise GeometryTransferIntegrityError(
                "Hành động yêu cầu ban đầu không hợp lệ."
            )
        if not isinstance(self.status, GeometryTransferStatus):
            raise GeometryTransferIntegrityError(
                "Trạng thái yêu cầu nạp 3D không hợp lệ."
            )
        if self.checksum_algorithm != GEOMETRY_TRANSFER_CHECKSUM_ALGORITHM:
            raise GeometryTransferIntegrityError(
                "Thuật toán checksum chưa được hỗ trợ."
            )
        if not all(
            _valid_sha256(value)
            for value in (
                self.source_geometry_fingerprint,
                self.source_container_fingerprint,
                self.payload_checksum,
                self.metadata_checksum,
            )
        ):
            raise GeometryTransferIntegrityError(
                "Fingerprint/checksum yêu cầu nạp 3D không hợp lệ."
            )
        if self.error_message is not None and (
            not isinstance(self.error_message, str)
            or not self.error_message.strip()
        ):
            raise GeometryTransferIntegrityError(
                "Metadata lỗi của yêu cầu không hợp lệ."
            )

    def to_dict(self, *, include_metadata_checksum: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "schema_version": self.schema_version,
            "request_id": str(self.request_id),
            "source_document_id": str(self.source_document_id),
            "source_container_id": str(self.source_container_id),
            "source_hms_path": str(self.source_hms_path),
            "source_display_name": self.source_display_name,
            "source_original_filename": self.source_original_filename,
            "source_container_version": self.source_container_version,
            "source_geometry_version": self.source_geometry_version,
            "source_geometry_fingerprint": self.source_geometry_fingerprint,
            "source_container_fingerprint": self.source_container_fingerprint,
            "target_project_id": str(self.target_project_id),
            "target_workspace_version": self.target_workspace_version,
            "created_at_utc": datetime_to_json(self.created_at_utc),
            "created_by_application_version": self.created_by_application_version,
            "geometry_units": self.geometry_units,
            "geometry_representation": self.geometry_representation.value,
            "solid_count": self.solid_count,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "requested_action": self.requested_action.value,
            "status": self.status.value,
            "checksum_algorithm": self.checksum_algorithm,
            "payload_checksum": self.payload_checksum,
            "payload_filename": self.payload_filename,
            "error_message": self.error_message,
        }
        if include_metadata_checksum:
            data["metadata_checksum"] = self.metadata_checksum
        return data

    @classmethod
    def from_dict(cls, value: object) -> "GeometryTransferRequest":
        if not isinstance(value, dict):
            raise GeometryTransferIntegrityError(
                "request.json phải là một JSON object."
            )
        required = {
            "schema_version",
            "request_id",
            "source_document_id",
            "source_container_id",
            "source_hms_path",
            "source_display_name",
            "source_original_filename",
            "source_container_version",
            "source_geometry_version",
            "source_geometry_fingerprint",
            "source_container_fingerprint",
            "target_project_id",
            "target_workspace_version",
            "created_at_utc",
            "created_by_application_version",
            "geometry_units",
            "geometry_representation",
            "solid_count",
            "face_count",
            "edge_count",
            "requested_action",
            "status",
            "checksum_algorithm",
            "payload_checksum",
            "metadata_checksum",
            "payload_filename",
            "error_message",
        }
        if set(value) != required:
            raise GeometryTransferIntegrityError(
                "request.json thiếu hoặc thừa trường metadata."
            )
        try:
            return cls(
                schema_version=value["schema_version"],  # type: ignore[arg-type]
                request_id=UUID(value["request_id"]),  # type: ignore[arg-type]
                source_document_id=UUID(  # type: ignore[arg-type]
                    value["source_document_id"]
                ),
                source_container_id=UUID(  # type: ignore[arg-type]
                    value["source_container_id"]
                ),
                source_hms_path=Path(value["source_hms_path"]),  # type: ignore[arg-type]
                source_display_name=value["source_display_name"],  # type: ignore[arg-type]
                source_original_filename=value["source_original_filename"],  # type: ignore[arg-type]
                source_container_version=value["source_container_version"],  # type: ignore[arg-type]
                source_geometry_version=value["source_geometry_version"],  # type: ignore[arg-type]
                source_geometry_fingerprint=value["source_geometry_fingerprint"],  # type: ignore[arg-type]
                source_container_fingerprint=value["source_container_fingerprint"],  # type: ignore[arg-type]
                target_project_id=UUID(value["target_project_id"]),  # type: ignore[arg-type]
                target_workspace_version=value["target_workspace_version"],  # type: ignore[arg-type]
                created_at_utc=datetime_from_json(value["created_at_utc"]),  # type: ignore[arg-type]
                created_by_application_version=value[  # type: ignore[arg-type]
                    "created_by_application_version"
                ],
                geometry_units=value["geometry_units"],  # type: ignore[arg-type]
                geometry_representation=GeometryRepresentation(
                    value["geometry_representation"]
                ),
                solid_count=value["solid_count"],  # type: ignore[arg-type]
                face_count=value["face_count"],  # type: ignore[arg-type]
                edge_count=value["edge_count"],  # type: ignore[arg-type]
                requested_action=GeometryRequestedAction(
                    value["requested_action"]
                ),
                status=GeometryTransferStatus(value["status"]),
                checksum_algorithm=value["checksum_algorithm"],  # type: ignore[arg-type]
                payload_checksum=value["payload_checksum"],  # type: ignore[arg-type]
                metadata_checksum=value["metadata_checksum"],  # type: ignore[arg-type]
                payload_filename=value["payload_filename"],  # type: ignore[arg-type]
                error_message=value["error_message"],  # type: ignore[arg-type]
            )
        except GeometryTransferIntegrityError:
            raise
        except (TypeError, ValueError) as error:
            raise GeometryTransferIntegrityError(
                "Metadata yêu cầu nạp 3D không hợp lệ."
            ) from error

    def with_status(
        self,
        status: GeometryTransferStatus,
        *,
        error_message: str | None = None,
    ) -> "GeometryTransferRequest":
        candidate = replace(
            self,
            status=status,
            error_message=error_message,
            metadata_checksum="0" * 64,
        )
        checksum = hashlib.sha256(
            _canonical_json(candidate.to_dict(include_metadata_checksum=False))
        ).hexdigest()
        return replace(candidate, metadata_checksum=checksum)


@dataclass(frozen=True, slots=True)
class GeometryAssetSummary:
    source_id: UUID
    display_name: str
    geometry_version: int
    units: str
    representation: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class IncomingGeometryPreview:
    """Native-free preview used by the non-modal UI."""

    request: GeometryTransferRequest
    current_assets: tuple[GeometryAssetSummary, ...]
    deterministic_match_id: UUID | None
    update_matching_allowed: bool
    update_matching_reason: str
    affected_operation_ids: tuple[str, ...]
    simulation_post_warning: str


@dataclass(frozen=True, slots=True)
class GeometryApplyResult:
    request: GeometryTransferRequest
    choice: GeometryApplyChoice
    source_id: UUID
    affected_operation_ids: tuple[str, ...]
    working_geometry_path: Path
    project_root: Path


@dataclass(frozen=True, slots=True)
class ClaimedGeometryRequest:
    request: GeometryTransferRequest
    request_path: Path
    payload_path: Path


@dataclass(frozen=True, slots=True)
class GeometryApplyPlan:
    """Filesystem/manifest plan computed before any project data is changed."""

    request: GeometryTransferRequest
    choice: GeometryApplyChoice
    source_id: UUID
    replaced_record: SourceFileRecord | None
    record: SourceFileRecord
    manifest: ProjectManifest
    source_path: Path
    working_path: Path
    previous_working_path: Path | None


class GeometryTransferInbox:
    """Validate targets and own every project-local request state transition."""

    def __init__(
        self,
        manifest_store: ProjectManifestStore,
        validator: ProjectValidator,
        database: ProjectDatabase,
        session_locks: SessionLockManager,
    ) -> None:
        self._manifest_store = manifest_store
        self._validator = validator
        self._database = database
        self._session_locks = session_locks

    def inspect_target(
        self,
        project_root: Path,
        *,
        required_payload_bytes: int = 0,
    ) -> CamProjectTargetInspection:
        """Validate a CAM root without acquiring or bypassing its session lock."""
        root = Path(project_root)
        project_name: str | None = None
        project_id: UUID | None = None
        workspace_version: int | None = None
        active_lock = False
        try:
            validate_existing_cam_root_path(root)
            if root.is_symlink() or bool(
                getattr(os.path, "isjunction", lambda _path: False)(root)
            ):
                raise GeometryTransferTargetError(
                    "Project root không được là liên kết hoặc junction."
                )
            manifest_path = root / CAM_WORKSPACE_MANIFEST_FILENAME
            if not manifest_path.is_file():
                raise GeometryTransferTargetError(
                    "Project root thiếu manifest.json."
                )
            manifest = self._manifest_store.load(root)
            project_name = manifest.project_name
            project_id = manifest.project_id
            workspace_version = manifest.format_version
            self._validator.validate_project_directory_name(root)
            self._validator.validate_references(root, manifest)
            if manifest.format_version != PROJECT_FORMAT_VERSION:
                raise GeometryTransferTargetError(
                    "Workspace version chưa được hỗ trợ."
                )
            if any(not (root / name).is_dir() for name in _REQUIRED_PROJECT_DIRECTORIES):
                raise GeometryTransferTargetError(
                    "Cấu trúc thư mục dự án CAM chưa đầy đủ."
                )
            if any(
                (root / name).is_symlink()
                for name in _REQUIRED_PROJECT_DIRECTORIES
            ):
                raise GeometryTransferTargetError(
                    "Cấu trúc dự án CAM chứa liên kết không an toàn."
                )
            database_path = root / manifest.database
            self._database.validate(database_path)
            if (
                self._database.current_schema_version(database_path)
                != DATABASE_SCHEMA_VERSION
            ):
                raise GeometryTransferTargetError(
                    "project.db chưa ở schema hiện hành được hỗ trợ."
                )
            self._database.validate_project_identity(
                database_path,
                manifest.project_id,
            )
            if any(root.glob(".*.recovering")):
                raise GeometryTransferTargetError(
                    "Dự án có migration/recovery transaction chưa hoàn tất."
                )
            if any(
                (root / name).exists()
                for name in ("corrupt", "corrupt.json", ".corrupt")
            ):
                raise GeometryTransferTargetError(
                    "Dự án đang bị đánh dấu hỏng dữ liệu."
                )
            applying = (
                root
                / INCOMING_GEOMETRY_DIRECTORY
                / INCOMING_GEOMETRY_STAGING_DIRECTORY
            )
            if applying.is_dir() and any(
                candidate.name.endswith(APPLYING_SUFFIX)
                for candidate in applying.iterdir()
            ):
                raise GeometryTransferTargetError(
                    "Dự án có yêu cầu nạp 3D đang phục hồi."
                )
            lock = self._session_locks.inspect(root)
            if lock is not None:
                if lock.state is LockState.UNKNOWN:
                    raise GeometryTransferTargetError(
                        "Không xác định được chủ sở hữu khóa dự án."
                    )
                active_lock = lock.state is LockState.ACTIVE
            required = max(
                MINIMUM_FREE_SPACE_BYTES,
                max(0, required_payload_bytes) * 3,
            )
            if shutil.disk_usage(root).free < required:
                raise GeometryTransferTargetError(
                    "Không đủ dung lượng trống để nạp dữ liệu 3D an toàn."
                )
            self._probe_write_access(root)
        except (
            GeometryTransferTargetError,
            ProjectDatabaseError,
            ProjectPermissionError,
            UnsafeWorkspacePathError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            return CamProjectTargetInspection(
                root_path=root,
                project_name=project_name,
                project_id=project_id,
                workspace_version=workspace_version,
                valid=False,
                reason=str(error) or "Dự án CAM không hợp lệ.",
                active_session_detected=active_lock,
            )
        return CamProjectTargetInspection(
            root_path=root,
            project_name=project_name,
            project_id=project_id,
            workspace_version=workspace_version,
            valid=True,
            reason=(
                "Dự án hợp lệ; yêu cầu sẽ được ghi vào vùng chờ."
                if not active_lock
                else (
                    "Dự án đang mở trong HMS; chỉ vùng chờ được cập nhật."
                )
            ),
            active_session_detected=active_lock,
        )

    def send(
        self,
        document: CadDocumentSession,
        project_root: Path,
    ) -> GeometryTransferRequest:
        """Publish one complete request through staging and atomic rename."""
        if (
            document.state.mode is not DocumentMode.CAD_DOCUMENT
            or document.state.physical_path is None
            or not document.state.physical_path.is_file()
        ):
            raise GeometryTransferTargetError(
                "Hãy lưu tài liệu HMS trước khi nạp 3D sang dự án CAM."
            )
        if not document.geometry_path.is_file():
            raise GeometryTransferIntegrityError(
                "Tài liệu không có hình học hợp lệ để nạp."
            )
        source_container = document.state.physical_path
        before_container_fingerprint = sha256_file(source_container)
        payload_size = document.geometry_path.stat().st_size
        if payload_size <= 0 or payload_size > MAX_TRANSFER_PAYLOAD_BYTES:
            raise GeometryTransferIntegrityError(
                "Dung lượng hình học không hợp lệ hoặc vượt giới hạn."
            )
        target = self.inspect_target(
            project_root,
            required_payload_bytes=payload_size,
        )
        if not target.valid or target.project_id is None:
            raise GeometryTransferTargetError(target.reason)
        geometry_fingerprint = sha256_file(document.geometry_path)
        duplicate = self.find_equivalent(
            target.root_path,
            source_document_id=document.state.identity,
            geometry_fingerprint=geometry_fingerprint,
            target_project_id=target.project_id,
            payload_checksum=geometry_fingerprint,
        )
        if duplicate is not None:
            raise GeometryTransferDuplicateError(duplicate)
        self.ensure_structure(target.root_path)
        request_id = uuid4()
        staging_root = (
            target.root_path
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_STAGING_DIRECTORY
        )
        pending_root = (
            target.root_path
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_PENDING_DIRECTORY
        )
        staging = staging_root / f"{REQUEST_DIRECTORY_PREFIX}{request_id}.tmp"
        pending = pending_root / f"{REQUEST_DIRECTORY_PREFIX}{request_id}"
        try:
            staging.mkdir()
            geometry_directory = staging / REQUEST_GEOMETRY_DIRECTORY
            preview_directory = staging / REQUEST_PREVIEW_DIRECTORY
            geometry_directory.mkdir()
            preview_directory.mkdir()
            payload_name = normalize_internal_source_filename(
                f"{document.state.display_name}{document.geometry_path.suffix}"
            )
            payload_path = geometry_directory / payload_name
            copied_size, copied_checksum = copy_source_verified(
                document.geometry_path,
                payload_path,
            )
            _fsync_existing_file(payload_path)
            if copied_size != payload_size or copied_checksum != geometry_fingerprint:
                raise GeometryTransferIntegrityError(
                    "Bản sao hình học trong vùng chờ không khớp nguồn."
                )
            solid_count, face_count, edge_count = _document_topology_counts(
                document
            )
            provisional = GeometryTransferRequest(
                schema_version=GEOMETRY_TRANSFER_SCHEMA_VERSION,
                request_id=request_id,
                source_document_id=document.state.identity,
                source_container_id=document.container_id,
                source_hms_path=source_container.resolve(),
                source_display_name=document.state.display_name,
                source_original_filename=document.provenance.original_filename,
                source_container_version=document.state.format_version,
                source_geometry_version=document.geometry_version,
                source_geometry_fingerprint=geometry_fingerprint,
                source_container_fingerprint=before_container_fingerprint,
                target_project_id=target.project_id,
                target_workspace_version=target.workspace_version
                or PROJECT_FORMAT_VERSION,
                created_at_utc=utc_now(),
                created_by_application_version=APPLICATION_VERSION,
                geometry_units=str(
                    document.cad_metadata.get(
                        "units",
                        document.provenance.units,
                    )
                ),
                geometry_representation=GeometryRepresentation.for_path(
                    document.geometry_path
                ),
                solid_count=solid_count,
                face_count=face_count,
                edge_count=edge_count,
                requested_action=GeometryRequestedAction.UNSPECIFIED,
                status=GeometryTransferStatus.STAGING,
                checksum_algorithm=GEOMETRY_TRANSFER_CHECKSUM_ALGORITHM,
                payload_checksum=copied_checksum,
                metadata_checksum="0" * 64,
                payload_filename=payload_name,
            )
            request = provisional.with_status(GeometryTransferStatus.PENDING)
            _write_fsynced(
                staging / REQUEST_METADATA_FILENAME,
                _canonical_json(request.to_dict()),
            )
            _write_fsynced(
                staging / REQUEST_CHECKSUM_FILENAME,
                _canonical_json(_checksums_payload(request)),
            )
            self.validate_request_directory(
                staging,
                expected_project_id=target.project_id,
                expected_request_id=request_id,
            )
            final_target = self.inspect_target(
                target.root_path,
                required_payload_bytes=payload_size,
            )
            if (
                not final_target.valid
                or final_target.project_id != target.project_id
            ):
                raise GeometryTransferTargetError(
                    "Project ID hoặc trạng thái dự án đã thay đổi khi đang nạp."
                )
            if sha256_file(source_container) != before_container_fingerprint:
                raise GeometryTransferIntegrityError(
                    "Tài liệu HMS nguồn đã thay đổi trong khi chuẩn bị yêu cầu."
                )
            os.replace(staging, pending)
            logger.info(
                "Đã nạp dữ liệu 3D vào vùng chờ %s của dự án %s",
                request.request_id,
                target.project_id,
            )
            return request
        except Exception:
            self._remove_owned_staging(staging, staging_root)
            raise

    def ensure_structure(self, project_root: Path) -> Path:
        """Create only the recognized inbox layout below a validated project."""
        incoming = project_root / INCOMING_GEOMETRY_DIRECTORY
        incoming.mkdir(exist_ok=True)
        if incoming.is_symlink() or not incoming.is_dir():
            raise GeometryTransferTargetError(
                "incoming-geometry phải là thư mục thật trong project root."
            )
        for name in _INBOX_STATE_DIRECTORIES:
            directory = incoming / name
            directory.mkdir(exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise GeometryTransferTargetError(
                    f"Thư mục vùng chờ không an toàn: {name}"
                )
        return incoming

    def scan(
        self,
        project_root: Path,
        project_id: UUID,
    ) -> tuple[GeometryTransferRequest, ...]:
        """Return complete pending/deferred requests; staging is never scanned."""
        pending = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_PENDING_DIRECTORY
        )
        if not pending.is_dir():
            return ()
        requests: list[GeometryTransferRequest] = []
        for candidate in sorted(pending.iterdir(), key=lambda item: item.name):
            if not candidate.is_dir() or not candidate.name.startswith(
                REQUEST_DIRECTORY_PREFIX
            ):
                continue
            try:
                request = self.validate_request_directory(
                    candidate,
                    expected_project_id=project_id,
                )
            except GeometryTransferIntegrityError:
                logger.warning(
                    "Bỏ qua yêu cầu nạp 3D chưa hoàn chỉnh hoặc bị hỏng: %s",
                    candidate,
                    exc_info=True,
                )
                continue
            if request.status in {
                GeometryTransferStatus.PENDING,
                GeometryTransferStatus.DEFERRED,
            }:
                requests.append(request)
        return tuple(
            sorted(requests, key=lambda item: (item.created_at_utc, str(item.request_id)))
        )

    def find_equivalent(
        self,
        project_root: Path,
        *,
        source_document_id: UUID,
        geometry_fingerprint: str,
        target_project_id: UUID,
        payload_checksum: str,
    ) -> GeometryTransferRequest | None:
        """Find one equivalent pending/deferred/applying request."""
        for request in self.scan(project_root, target_project_id):
            if (
                request.source_document_id == source_document_id
                and request.source_geometry_fingerprint == geometry_fingerprint
                and request.target_project_id == target_project_id
                and request.payload_checksum == payload_checksum
            ):
                return request
        staging = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_STAGING_DIRECTORY
        )
        if staging.is_dir():
            for candidate in staging.iterdir():
                if not candidate.name.endswith(APPLYING_SUFFIX):
                    continue
                try:
                    request = self.validate_request_directory(
                        candidate,
                        expected_project_id=target_project_id,
                    )
                except GeometryTransferIntegrityError:
                    continue
                if (
                    request.source_document_id == source_document_id
                    and request.source_geometry_fingerprint
                    == geometry_fingerprint
                    and request.payload_checksum == payload_checksum
                ):
                    return request
        return None

    def request(
        self,
        project_root: Path,
        request_id: UUID,
    ) -> GeometryTransferRequest:
        """Load one request from every durable user-visible state."""
        for state_directory in (
            INCOMING_GEOMETRY_PENDING_DIRECTORY,
            INCOMING_GEOMETRY_APPLIED_DIRECTORY,
            INCOMING_GEOMETRY_REJECTED_DIRECTORY,
            INCOMING_GEOMETRY_FAILED_DIRECTORY,
        ):
            candidate = (
                project_root
                / INCOMING_GEOMETRY_DIRECTORY
                / state_directory
                / f"{REQUEST_DIRECTORY_PREFIX}{request_id}"
            )
            if candidate.is_dir():
                return self.validate_request_directory(candidate)
        applying = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_STAGING_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{request_id}{APPLYING_SUFFIX}"
        )
        if applying.is_dir():
            return self.validate_request_directory(applying)
        raise GeometryTransferIntegrityError(
            "Không tìm thấy yêu cầu nạp 3D trong dự án."
        )

    def defer(
        self,
        project_root: Path,
        request_id: UUID,
    ) -> GeometryTransferRequest:
        directory = self._pending_request_directory(project_root, request_id)
        request = self.validate_request_directory(directory)
        if request.status not in {
            GeometryTransferStatus.PENDING,
            GeometryTransferStatus.DEFERRED,
        }:
            raise GeometryTransferIntegrityError(
                "Chỉ yêu cầu đang chờ mới có thể để sau."
            )
        return self._update_request_status(
            directory,
            request,
            GeometryTransferStatus.DEFERRED,
        )

    def reject(
        self,
        project_root: Path,
        request_id: UUID,
    ) -> GeometryTransferRequest:
        directory = self._pending_request_directory(project_root, request_id)
        request = self.validate_request_directory(directory)
        if request.status not in {
            GeometryTransferStatus.PENDING,
            GeometryTransferStatus.DEFERRED,
        }:
            raise GeometryTransferIntegrityError(
                "Chỉ yêu cầu đang chờ mới có thể bỏ qua."
            )
        changed = self._update_request_status(
            directory,
            request,
            GeometryTransferStatus.REJECTED,
        )
        target = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_REJECTED_DIRECTORY
            / directory.name
        )
        os.replace(directory, target)
        return changed

    def claim(
        self,
        project_root: Path,
        request_id: UUID,
    ) -> ClaimedGeometryRequest:
        """Claim one request by atomic rename out of the scanner-visible inbox."""
        pending = self._pending_request_directory(project_root, request_id)
        request = self.validate_request_directory(pending)
        if request.status not in {
            GeometryTransferStatus.PENDING,
            GeometryTransferStatus.DEFERRED,
        }:
            raise GeometryTransferIntegrityError(
                "Yêu cầu không còn ở trạng thái có thể cập nhật."
            )
        applying = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_STAGING_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{request_id}{APPLYING_SUFFIX}"
        )
        try:
            os.replace(pending, applying)
        except FileNotFoundError as error:
            raise GeometryTransferIntegrityError(
                "Yêu cầu đã được một tiến trình khác xử lý."
            ) from error
        changed = self._update_request_status(
            applying,
            request,
            GeometryTransferStatus.APPLYING,
        )
        payload = (
            applying
            / REQUEST_GEOMETRY_DIRECTORY
            / changed.payload_filename
        )
        return ClaimedGeometryRequest(changed, applying, payload)

    def finish_applied(
        self,
        project_root: Path,
        claim: ClaimedGeometryRequest,
    ) -> GeometryTransferRequest:
        changed = self._update_request_status(
            claim.request_path,
            claim.request,
            GeometryTransferStatus.APPLIED,
        )
        destination = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_APPLIED_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{changed.request_id}"
        )
        os.replace(claim.request_path, destination)
        return changed

    def apply_plan(
        self,
        session: ProjectSession,
        request: GeometryTransferRequest,
        choice: GeometryApplyChoice,
        target_source_id: UUID | None,
        *,
        payload_size: int,
    ) -> GeometryApplyPlan:
        """Build one explicit, fail-closed asset update plan."""
        if request.target_project_id != session.manifest.project_id:
            raise GeometryTransferIntegrityError(
                "Yêu cầu nạp 3D thuộc dự án khác."
            )
        if type(payload_size) is not int or not 0 < payload_size <= MAX_TRANSFER_PAYLOAD_BYTES:
            raise GeometryTransferIntegrityError(
                "Dung lượng payload hình học không hợp lệ."
            )
        if not request.geometry_representation.exact_for_cam:
            raise GeometryTransferIntegrityError(
                "Không đủ dữ liệu chính xác để dùng cho CAM."
            )
        if request.geometry_units not in {
            UnitSystem.MILLIMETER.value,
            UnitSystem.INCH.value,
        }:
            raise GeometryTransferIntegrityError(
                "Đơn vị hình học nguồn chưa được xác định; từ chối cập nhật CAM."
            )
        if request.geometry_units != session.manifest.units.value:
            raise GeometryTransferIntegrityError(
                "Đơn vị hình học không tương thích với dự án CAM."
            )
        if not isinstance(choice, GeometryApplyChoice):
            raise GeometryTransferIntegrityError(
                "Cách cập nhật hình học chưa được chọn."
            )
        by_id = {
            record.source_id: record
            for record in session.manifest.source_files
        }
        match = self.deterministic_match(session.manifest, request)
        replaced_record: SourceFileRecord | None = None
        if choice is GeometryApplyChoice.ADD_NEW:
            if target_source_id is not None:
                raise GeometryTransferIntegrityError(
                    "Thêm mô hình mới không được chọn model đích."
                )
            source_id = uuid4()
        elif choice is GeometryApplyChoice.REPLACE_EXISTING:
            if target_source_id is None or target_source_id not in by_id:
                raise GeometryTransferIntegrityError(
                    "Hãy chọn rõ mô hình hiện tại cần thay thế."
                )
            source_id = target_source_id
            replaced_record = by_id[source_id]
        else:
            if match is None:
                raise GeometryTransferIntegrityError(
                    "Không đủ nguồn gốc hoặc định danh đối tượng để cập nhật "
                    "phiên bản tương ứng."
                )
            if target_source_id is not None and target_source_id != match.source_id:
                raise GeometryTransferIntegrityError(
                    "Mô hình đích không khớp định danh đã xác minh."
                )
            source_id = match.source_id
            replaced_record = match
        internal_base = normalize_internal_source_filename(
            request.payload_filename
        )
        suffix = Path(internal_base).suffix
        stem = Path(internal_base).stem
        internal_name = (
            f"{stem}-{request.request_id.hex[:12]}{suffix}"
        )
        source_relative = f"{SOURCE_DIRECTORY}/{internal_name}"
        working_relative = (
            f"{WORKING_GEOMETRY_DIRECTORY}/{internal_name}"
        )
        previous_version = (
            0 if replaced_record is None else replaced_record.geometry_version
        )
        geometry_version = max(
            request.source_geometry_version,
            previous_version + 1,
        )
        record = SourceFileRecord(
            source_id=source_id,
            original_name=request.source_original_filename,
            stored_path=source_relative,
            size_bytes=payload_size,
            sha256=request.payload_checksum,
            imported_at=utc_now(),
            original_path=str(request.source_hms_path),
            internal_filename=internal_name,
            importer=Path(internal_name).suffix.lstrip(".") or "unknown",
            units=request.geometry_units,
            geometry_type=Path(internal_name).suffix.lstrip(".") or "unknown",
            read_only=True,
            working_geometry_path=working_relative,
            geometry_version=geometry_version,
            source_document_id=request.source_document_id,
            source_container_id=request.source_container_id,
            source_geometry_fingerprint=request.source_geometry_fingerprint,
            source_container_fingerprint=request.source_container_fingerprint,
            transfer_request_id=request.request_id,
            geometry_representation=request.geometry_representation.value,
        )
        if replaced_record is None:
            records = (*session.manifest.source_files, record)
        else:
            records = tuple(
                record if item.source_id == source_id else item
                for item in session.manifest.source_files
            )
        manifest = replace(
            session.manifest,
            source_files=records,
            modified_at=utc_now(),
        )
        previous_working = (
            None
            if replaced_record is None
            or replaced_record.working_geometry_path is None
            else session.root_path / Path(replaced_record.working_geometry_path)
        )
        return GeometryApplyPlan(
            request=request,
            choice=choice,
            source_id=source_id,
            replaced_record=replaced_record,
            record=record,
            manifest=manifest,
            source_path=session.root_path / Path(source_relative),
            working_path=session.root_path / Path(working_relative),
            previous_working_path=previous_working,
        )

    def fail_claim(
        self,
        project_root: Path,
        claim: ClaimedGeometryRequest,
        message: str,
    ) -> GeometryTransferRequest:
        changed = self._update_request_status(
            claim.request_path,
            claim.request,
            GeometryTransferStatus.FAILED,
            error_message=message,
        )
        destination = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_FAILED_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{changed.request_id}"
        )
        os.replace(claim.request_path, destination)
        return changed

    def return_claim_to_pending(
        self,
        project_root: Path,
        claim: ClaimedGeometryRequest,
        message: str,
    ) -> GeometryTransferRequest:
        """Return a rolled-back interrupted apply to a retryable pending state."""
        changed = self._update_request_status(
            claim.request_path,
            claim.request,
            GeometryTransferStatus.PENDING,
            error_message=message,
        )
        destination = (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_PENDING_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{changed.request_id}"
        )
        os.replace(claim.request_path, destination)
        return changed

    def write_apply_evidence(
        self,
        claim: ClaimedGeometryRequest,
        value: dict[str, object],
    ) -> Path:
        """Atomically persist crash-recovery evidence inside the claimed request."""
        if not isinstance(value, dict):
            raise TypeError("Apply evidence must be a dictionary")
        path = claim.request_path / GEOMETRY_APPLY_EVIDENCE_FILENAME
        _replace_fsynced(path, _canonical_json(value))
        return path

    def read_apply_evidence(
        self,
        request_path: Path,
    ) -> dict[str, object] | None:
        path = request_path / GEOMETRY_APPLY_EVIDENCE_FILENAME
        if not path.is_file() or path.is_symlink():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GeometryTransferIntegrityError(
                "Evidence phục hồi cập nhật hình học không hợp lệ."
            ) from error
        if not isinstance(value, dict):
            raise GeometryTransferIntegrityError(
                "Evidence phục hồi phải là JSON object."
            )
        return value

    def validate_request_directory(
        self,
        request_directory: Path,
        *,
        expected_project_id: UUID | None = None,
        expected_request_id: UUID | None = None,
    ) -> GeometryTransferRequest:
        """Verify a complete request without following links or trusting filenames."""
        if (
            request_directory.is_symlink()
            or not request_directory.is_dir()
        ):
            raise GeometryTransferIntegrityError(
                "Thư mục yêu cầu nạp 3D không hợp lệ."
            )
        metadata_path = request_directory / REQUEST_METADATA_FILENAME
        checksum_path = request_directory / REQUEST_CHECKSUM_FILENAME
        geometry_directory = request_directory / REQUEST_GEOMETRY_DIRECTORY
        preview_directory = request_directory / REQUEST_PREVIEW_DIRECTORY
        if any(
            path.is_symlink()
            for path in (
                metadata_path,
                checksum_path,
                geometry_directory,
                preview_directory,
            )
        ):
            raise GeometryTransferIntegrityError(
                "Yêu cầu nạp 3D chứa liên kết không an toàn."
            )
        if (
            not metadata_path.is_file()
            or not checksum_path.is_file()
            or not geometry_directory.is_dir()
            or not preview_directory.is_dir()
        ):
            raise GeometryTransferIntegrityError(
                "Yêu cầu nạp 3D chưa hoàn chỉnh."
            )
        try:
            request = GeometryTransferRequest.from_dict(
                json.loads(metadata_path.read_text(encoding="utf-8"))
            )
            checksums = json.loads(checksum_path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise GeometryTransferIntegrityError(
                "Không đọc được metadata/checksum của yêu cầu."
            ) from error
        if expected_project_id is not None and (
            request.target_project_id != expected_project_id
        ):
            raise GeometryTransferIntegrityError(
                "Project ID của yêu cầu không khớp dự án."
            )
        if expected_request_id is not None and (
            request.request_id != expected_request_id
        ):
            raise GeometryTransferIntegrityError(
                "ID yêu cầu không khớp thư mục vùng chờ."
            )
        payload = geometry_directory / request.payload_filename
        payloads = tuple(geometry_directory.iterdir())
        if (
            len(payloads) != 1
            or payloads[0] != payload
            or payload.is_symlink()
            or not payload.is_file()
            or payload.stat().st_size <= 0
            or payload.stat().st_size > MAX_TRANSFER_PAYLOAD_BYTES
        ):
            raise GeometryTransferIntegrityError(
                "Payload hình học của yêu cầu không hợp lệ."
            )
        expected_metadata = hashlib.sha256(
            _canonical_json(request.to_dict(include_metadata_checksum=False))
        ).hexdigest()
        if request.metadata_checksum != expected_metadata:
            raise GeometryTransferIntegrityError(
                "Checksum metadata của yêu cầu không khớp."
            )
        if sha256_file(payload) != request.payload_checksum:
            raise GeometryTransferIntegrityError(
                "Checksum payload hình học không khớp."
            )
        if checksums != _checksums_payload(request):
            raise GeometryTransferIntegrityError(
                "checksums.json của yêu cầu không khớp."
            )
        return request

    @staticmethod
    def deterministic_match(
        manifest: ProjectManifest,
        request: GeometryTransferRequest,
    ) -> SourceFileRecord | None:
        """Match only explicit lineage/object identity; filenames are ignored."""
        matches = tuple(
            record
            for record in manifest.source_files
            if (
                record.source_document_id == request.source_document_id
                and record.source_container_id == request.source_container_id
                and record.units == request.geometry_units
                and record.geometry_representation
                == request.geometry_representation.value
            )
        )
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def preview(
        session: ProjectSession,
        request: GeometryTransferRequest,
    ) -> IncomingGeometryPreview:
        assets = tuple(
            GeometryAssetSummary(
                source_id=record.source_id,
                display_name=record.original_name,
                geometry_version=record.geometry_version,
                units=record.units,
                representation=record.geometry_representation
                or record.geometry_type,
                fingerprint=record.source_geometry_fingerprint or record.sha256,
            )
            for record in session.manifest.source_files
        )
        match = GeometryTransferInbox.deterministic_match(
            session.manifest,
            request,
        )
        affected: list[str] = []
        if match is not None:
            for job in session.cam_snapshot.jobs:
                for setup in job.setups:
                    for operation in setup.operation_tree.operations:
                        if any(
                            item.reference.source_id == match.source_id
                            for item in operation.geometry_inputs
                        ):
                            affected.append(str(operation.operation_id))
        if not request.geometry_representation.exact_for_cam:
            reason = "Không đủ dữ liệu chính xác để dùng cho CAM."
            allowed = False
        elif match is None:
            reason = (
                "Không có nguồn gốc hoặc định danh đối tượng duy nhất; "
                "HMS không đoán theo tên tệp."
            )
            allowed = False
        else:
            reason = (
                "Nguồn gốc, định danh đối tượng, đơn vị và kiểu biểu diễn "
                "đều khớp."
            )
            allowed = True
        return IncomingGeometryPreview(
            request=request,
            current_assets=assets,
            deterministic_match_id=None if match is None else match.source_id,
            update_matching_allowed=allowed,
            update_matching_reason=reason,
            affected_operation_ids=tuple(sorted(set(affected))),
            simulation_post_warning=(
                "Nguyên công liên quan, mô phỏng và xử lý hậu kỳ sẽ bị "
                "đánh dấu cần cập nhật; HMS không tự tính toán."
            ),
        )

    @staticmethod
    def _probe_write_access(project_root: Path) -> None:
        descriptor = -1
        probe_path: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".hms-transfer-probe-",
                dir=project_root,
            )
            probe_path = Path(name)
        except (OSError, PermissionError) as error:
            raise ProjectPermissionError(
                "Project root chỉ đọc hoặc không thể tạo vùng chờ."
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)

    @staticmethod
    def _remove_owned_staging(staging: Path, staging_root: Path) -> None:
        try:
            resolved = staging.resolve()
            root = staging_root.resolve()
            if (
                resolved.parent == root
                and resolved.name.startswith(REQUEST_DIRECTORY_PREFIX)
                and resolved.name.endswith(".tmp")
            ):
                shutil.rmtree(resolved, ignore_errors=False)
        except FileNotFoundError:
            return

    @staticmethod
    def _pending_request_directory(
        project_root: Path,
        request_id: UUID,
    ) -> Path:
        return (
            project_root
            / INCOMING_GEOMETRY_DIRECTORY
            / INCOMING_GEOMETRY_PENDING_DIRECTORY
            / f"{REQUEST_DIRECTORY_PREFIX}{request_id}"
        )

    def _update_request_status(
        self,
        request_directory: Path,
        request: GeometryTransferRequest,
        status: GeometryTransferStatus,
        *,
        error_message: str | None = None,
    ) -> GeometryTransferRequest:
        changed = request.with_status(
            status,
            error_message=error_message,
        )
        _replace_fsynced(
            request_directory / REQUEST_METADATA_FILENAME,
            _canonical_json(changed.to_dict()),
        )
        _replace_fsynced(
            request_directory / REQUEST_CHECKSUM_FILENAME,
            _canonical_json(_checksums_payload(changed)),
        )
        return self.validate_request_directory(
            request_directory,
            expected_project_id=changed.target_project_id,
            expected_request_id=changed.request_id,
        )


def _checksums_payload(
    request: GeometryTransferRequest,
) -> dict[str, object]:
    return {
        "algorithm": GEOMETRY_TRANSFER_CHECKSUM_ALGORITHM,
        "entries": {
            f"{REQUEST_GEOMETRY_DIRECTORY}/{request.payload_filename}": (
                request.payload_checksum
            ),
            REQUEST_METADATA_FILENAME: request.metadata_checksum,
        },
    }


def _document_topology_counts(
    document: CadDocumentSession,
) -> tuple[int, int, int]:
    raw = document.cad_metadata.get("topology_counts", {})
    if not isinstance(raw, dict):
        return 0, 0, 0
    values = tuple(raw.get(name, 0) for name in ("solids", "faces", "edges"))
    if any(type(value) is not int or value < 0 for value in values):
        return 0, 0, 0
    return values  # type: ignore[return-value]
