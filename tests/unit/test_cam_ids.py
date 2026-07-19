"""Tests for strongly typed CAM identities."""

from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import CAM_ID_TYPES, CamJobId, SetupId
from hms_cadcam.cam.domain.errors import CamValidationError


@pytest.mark.parametrize("id_type", CAM_ID_TYPES)
def test_each_cam_id_has_deterministic_round_trip(id_type) -> None:
    identity = id_type.new()

    assert id_type.parse(str(identity)) == identity
    assert str(id_type.parse(str(identity))) == str(identity)


def test_different_cam_id_types_are_never_equal() -> None:
    value = uuid4()

    assert CamJobId(value) != SetupId(value)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "cam_job:",
        "cam_job:not-a-uuid",
        "setup:00000000-0000-4000-8000-000000000000",
        "cam_job:00000000-0000-0000-0000-000000000000",
    ),
)
def test_invalid_cam_id_text_is_rejected(value: str) -> None:
    with pytest.raises(CamValidationError):
        CamJobId.parse(value)


def test_cam_id_constructor_rejects_non_uuid() -> None:
    with pytest.raises(CamValidationError):
        CamJobId("not-a-uuid")  # type: ignore[arg-type]
