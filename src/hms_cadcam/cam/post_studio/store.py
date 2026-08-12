"""Atomic, project-contained Post Studio storage and portable packages."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.post_studio.model import PostDefinition, PostRevision


POST_STUDIO_DIRECTORY = "post/studio"
MANIFEST_NAME = "post-studio-manifest.json"
PACKAGE_FORMAT = "HMS_POST_STUDIO_PACKAGE"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise CamValidationError("Post Studio relative path is unsafe")
    return path


class PostStudioStore:
    """Append-only source/revision store below one HMS project ``post/`` root."""

    def root(self, project_root: Path) -> Path:
        if not isinstance(project_root, Path):
            raise TypeError("Project root must be a Path")
        return project_root / POST_STUDIO_DIRECTORY

    def _path(self, project_root: Path, relative: str) -> Path:
        rel = _safe_relative(relative)
        root = self.root(project_root)
        path = root.joinpath(*rel.parts)
        if path.parent.resolve(strict=False) != root.joinpath(*rel.parts[:-1]).resolve(strict=False):
            raise CamValidationError("Post Studio path escapes root")
        return path

    def publish_revision(self, project_root: Path, definition: PostDefinition, revision: PostRevision, source: bytes) -> None:
        if revision.post_id != definition.post_id:
            raise CamValidationError("Revision belongs to another Post definition")
        if hashlib.sha256(source).hexdigest() != revision.source_sha256 or len(source) != revision.source_size:
            raise CamValidationError("Revision source identity mismatch")
        root = self.root(project_root)
        source_path = self._path(project_root, f"sources/{revision.revision_id}.dat")
        revision_path = self._path(project_root, f"revisions/{revision.revision_id}.json")
        definition_path = self._path(project_root, f"definitions/{definition.post_id}.json")
        for path, data in ((source_path, source), (revision_path, _json_bytes(revision.to_dict())), (definition_path, _json_bytes(definition.to_dict()))):
            if path.exists():
                if path.read_bytes() != data:
                    raise CamValidationError("Immutable Post Studio object already exists with different bytes")
                continue
            self._atomic_write(path, data)
        self.write_manifest(project_root)

    def write_manifest(self, project_root: Path) -> dict[str, object]:
        root = self.root(project_root)
        files = []
        if root.exists():
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.name == MANIFEST_NAME:
                    continue
                data = path.read_bytes()
                files.append({"path": path.relative_to(root).as_posix(), "size": len(data), "sha256": _hash(data)})
        manifest: dict[str, object] = {"format": "HMS_POST_STUDIO_STORE_MANIFEST", "format_version": 1, "files": files, "self_excluded": True}
        self._atomic_write(root / MANIFEST_NAME, _json_bytes(manifest))
        return manifest

    def verify_manifest(self, project_root: Path) -> dict[str, object]:
        root = self.root(project_root)
        try:
            manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CamValidationError("Post Studio manifest is unavailable") from error
        if not isinstance(manifest, dict) or manifest.get("format") != "HMS_POST_STUDIO_STORE_MANIFEST" or not isinstance(manifest.get("files"), list):
            raise CamValidationError("Post Studio manifest is malformed")
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
                raise CamValidationError("Post Studio manifest entry is malformed")
            path = self._path(project_root, str(entry["path"]))
            data = path.read_bytes()
            if len(data) != entry["size"] or _hash(data) != entry["sha256"]:
                raise CamValidationError("Post Studio manifest verification failed")
        return manifest

    def export_package(self, project_root: Path, target: Path) -> dict[str, object]:
        manifest = self.verify_manifest(project_root)
        root = self.root(project_root)
        payloads = {entry["path"]: (root / str(entry["path"])).read_bytes() for entry in manifest["files"]}
        package_manifest = {"format": PACKAGE_FORMAT, "format_version": 1, "store_manifest": manifest, "deployment_state": "NOT_ACTIVE_GLOBALLY"}
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        target.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(temp, "w", compression=ZIP_DEFLATED) as archive:
            for name in sorted(payloads):
                info = ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = ZIP_DEFLATED
                archive.writestr(info, payloads[name])
            info = ZipInfo("package-manifest.json"); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = ZIP_DEFLATED
            archive.writestr(info, _json_bytes(package_manifest))
        os.replace(temp, target)
        return {"path": str(target), "sha256": _hash(target.read_bytes()), "manifest": package_manifest}

    def import_package(self, project_root: Path, package: Path) -> dict[str, object]:
        try:
            with ZipFile(package) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or "package-manifest.json" not in names or any(_safe_relative(name) != PurePosixPath(name) for name in names):
                    raise CamValidationError("Post Studio package paths are invalid")
                meta = json.loads(archive.read("package-manifest.json").decode("utf-8"))
                if not isinstance(meta, dict) or meta.get("format") != PACKAGE_FORMAT:
                    raise CamValidationError("Post Studio package manifest is invalid")
                store_manifest = meta.get("store_manifest")
                if not isinstance(store_manifest, dict) or not isinstance(store_manifest.get("files"), list):
                    raise CamValidationError("Post Studio package store manifest is invalid")
                for entry in store_manifest["files"]:
                    name = str(entry["path"]); data = archive.read(name)
                    if len(data) != entry["size"] or _hash(data) != entry["sha256"]:
                        raise CamValidationError("Post Studio package checksum mismatch")
                    target = self._path(project_root, name)
                    if target.exists() and target.read_bytes() != data:
                        raise CamValidationError("Post Studio import conflicts with immutable object")
                    if not target.exists():
                        self._atomic_write(target, data)
        except (OSError, KeyError, json.JSONDecodeError) as error:
            raise CamValidationError("Post Studio package cannot be imported") from error
        return self.write_manifest(project_root)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
            if path.read_bytes() != data:
                raise CamValidationError("Post Studio atomic write verification failed")
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["MANIFEST_NAME", "PACKAGE_FORMAT", "POST_STUDIO_DIRECTORY", "PostStudioStore"]
