"""Unit tests for manifest models and UTF-8 persistence."""

from datetime import timezone
from uuid import uuid4

from hms_cadcam.project.constants import (
    APPLICATION_NAME,
    APPLICATION_VERSION,
    DATABASE_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
)
from hms_cadcam.project.manifest import ProjectManifestStore
from hms_cadcam.project.models import (
    ProjectManifest,
    SourceFileRecord,
    UnitSystem,
    utc_now,
)


def test_manifest_round_trip_preserves_unicode(tmp_path) -> None:
    now = utc_now()
    manifest = ProjectManifest(
        format=PROJECT_FORMAT,
        format_version=PROJECT_FORMAT_VERSION,
        application=APPLICATION_NAME,
        application_version=APPLICATION_VERSION,
        project_id=uuid4(),
        project_name="Chi tiết có dấu",
        created_at=now,
        modified_at=now,
        units=UnitSystem.MILLIMETER,
        source_files=(
            SourceFileRecord(
                source_id=uuid4(),
                original_name="mẫu thử.step",
                stored_path="source/mẫu thử.step",
                size_bytes=3,
                sha256="a" * 64,
                imported_at=now,
            ),
        ),
        active_document=None,
        database=DATABASE_FILENAME,
    )
    store = ProjectManifestStore()
    path = store.save(tmp_path, manifest)
    loaded = store.load(tmp_path)

    assert loaded == manifest
    assert loaded.created_at.tzinfo == timezone.utc
    assert "Chi tiết có dấu" in path.read_text(encoding="utf-8")
    assert "\\u" not in path.read_text(encoding="utf-8")
