"""Tests for CAM revisions and deterministic fingerprints."""

import builtins

import pytest

from hms_cadcam.cam.domain import (
    ContentFingerprint,
    DependencyFingerprint,
    GeometryFingerprint,
    Revision,
)
from hms_cadcam.cam.domain.errors import CamValidationError


def test_revision_round_trip_and_progression() -> None:
    revision = Revision(4)

    assert Revision.from_dict(revision.to_dict()) == revision
    assert revision.next() == Revision(5)


def test_fingerprint_is_deterministic_for_canonical_input(monkeypatch) -> None:
    def forbidden_hash(_value):
        raise AssertionError("Python hash() must not be used")

    monkeypatch.setattr(builtins, "hash", forbidden_hash)
    first = ContentFingerprint.from_payload({"b": [2, 3], "a": 1})
    second = ContentFingerprint.from_payload({"a": 1, "b": [2, 3]})

    assert first == second
    assert first.algorithm == "sha256"
    assert len(first.digest) == 64


@pytest.mark.parametrize(
    "fingerprint_type",
    (ContentFingerprint, GeometryFingerprint, DependencyFingerprint),
)
def test_fingerprint_round_trip_preserves_semantic_type(fingerprint_type) -> None:
    fingerprint = fingerprint_type.from_payload({"shape": "fixture"})

    assert fingerprint_type.from_dict(fingerprint.to_dict()) == fingerprint


def test_future_or_mismatched_fingerprint_algorithm_is_not_equivalent() -> None:
    current = GeometryFingerprint.from_payload({"geometry": 1})
    future = GeometryFingerprint("sha256", 2, current.digest)

    assert future != current


@pytest.mark.parametrize("payload", ({"value": -1}, {"value": 1, "extra": 2}, {}))
def test_malformed_revision_is_rejected(payload) -> None:
    with pytest.raises(CamValidationError):
        Revision.from_dict(payload)
