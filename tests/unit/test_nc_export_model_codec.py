from dataclasses import replace
from pathlib import Path

import pytest

from hms_cadcam.cam.domain import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.post import ExportTarget, NCExportRequest
from hms_cadcam.cam.post.export_codec import dumps, loads
from tests.unit._export_fixtures import production_export_fixture


def test_export_request_round_trip_and_runtime_path_is_not_serialized(tmp_path) -> None:
    request, _ = production_export_fixture(
        tmp_path / "Model.HMS",
        target=ExportTarget.FILESYSTEM_DIRECTORY,
        target_directory=tmp_path / "server-a",
    )
    other = replace(request, target_directory=tmp_path / "server-b")
    assert request.fingerprint == other.fingerprint
    text = dumps(request)
    assert str(tmp_path) not in text
    restored = loads(text)
    assert isinstance(restored, NCExportRequest)
    assert restored.target_directory is None
    assert restored.fingerprint == request.fingerprint


def test_export_codec_rejects_future_version_and_invalid_enum(tmp_path) -> None:
    request, _ = production_export_fixture(tmp_path / "Future.HMS")
    payload = request.to_dict()
    payload["format_version"] = 2
    import json

    with pytest.raises(UnsupportedCamSchemaError):
        loads(json.dumps(payload))
    payload["format_version"] = 1
    payload["target"] = "ftp"
    with pytest.raises(CamValidationError):
        loads(json.dumps(payload))


def test_export_request_rejects_invalid_field_types(tmp_path) -> None:
    request, _ = production_export_fixture(tmp_path / "Invalid.HMS")
    with pytest.raises(CamValidationError):
        replace(request, create_target_directory=1)
    with pytest.raises(CamValidationError):
        replace(request, target_directory="C:/NC")
