"""Unit tests for project naming and manifest validation."""

import pytest

from hms_cadcam.project.exceptions import (
    InvalidProjectNameError,
    UnsupportedFormatVersionError,
    UnsupportedProjectFormatError,
)
from hms_cadcam.project.filesystem import project_target_path
from hms_cadcam.project.models import ProjectManifest, UnitSystem, utc_now
from hms_cadcam.project.validator import ProjectValidator
from hms_cadcam.project.constants import APPLICATION_NAME, APPLICATION_VERSION, DATABASE_FILENAME
from uuid import uuid4


def make_manifest(**changes) -> ProjectManifest:
    now = utc_now()
    values = {
        "format": "HMS_PROJECT",
        "format_version": 1,
        "application": APPLICATION_NAME,
        "application_version": APPLICATION_VERSION,
        "project_id": uuid4(),
        "project_name": "Chi tiết A",
        "created_at": now,
        "modified_at": now,
        "units": UnitSystem.MILLIMETER,
        "source_files": (),
        "active_document": None,
        "database": DATABASE_FILENAME,
    }
    values.update(changes)
    return ProjectManifest(**values)


@pytest.mark.parametrize("name", ["Chi tiết Việt", "Project with spaces", "A.HMS", "A.HMS.HMS"])
def test_valid_names_normalize_to_one_suffix(tmp_path, name: str) -> None:
    validator = ProjectValidator()
    stem = validator.validate_project_name(name)
    target = project_target_path(tmp_path, stem)
    assert target.name.casefold().count(".hms") == 1


@pytest.mark.parametrize("name", ["", "CON", "LPT1.txt", "bad:name", "trailing.", "trailing "])
def test_invalid_windows_names_are_rejected(name: str) -> None:
    with pytest.raises(InvalidProjectNameError):
        ProjectValidator().validate_project_name(name)


def test_wrong_format_and_version_are_rejected() -> None:
    validator = ProjectValidator()
    with pytest.raises(UnsupportedProjectFormatError):
        validator.validate_manifest(make_manifest(format="OTHER"))
    with pytest.raises(UnsupportedFormatVersionError):
        validator.validate_manifest(make_manifest(format_version=99))


def test_manifest_rejects_database_and_active_document() -> None:
    validator = ProjectValidator()
    with pytest.raises(ValueError):
        validator.validate_manifest(make_manifest(database="other.db"))
    with pytest.raises(ValueError):
        validator.validate_manifest(make_manifest(active_document="doc"))
