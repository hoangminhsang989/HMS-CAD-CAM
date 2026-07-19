"""Tests for persistent geometry references and fail-closed resolution."""

import dataclasses
import json
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    HMS_GEOMETRY_REFERENCE_SCHEME,
    HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceError,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    GeometryResolutionEvidence,
    GeometryResolutionStatus,
    Revision,
    UnsupportedCamSchemaError,
    assess_geometry_resolution,
)


def _reference(
    *,
    source_id=None,
    occurrence_path: str = "assembly:aaa/part:one",
    selector: str = "face:sha256:111",
) -> GeometryReference:
    return GeometryReference(
        reference_id=GeometryReferenceId.new(),
        scheme=HMS_GEOMETRY_REFERENCE_SCHEME,
        scheme_version=HMS_GEOMETRY_REFERENCE_SCHEME_VERSION,
        source_id=source_id or uuid4(),
        kind=GeometryReferenceKind.FACE,
        geometry_kind=GeometryRepresentationKind.BREP,
        occurrence_path=occurrence_path,
        subshape_selector=selector,
        expected_geometry_fingerprint=GeometryFingerprint.from_payload(
            {"face": selector}
        ),
        expected_source_revision=Revision(3),
        hint="Mặt trên",
        diagnostic_fallback=(("label", "Top face"),),
    )


def _evidence(reference: GeometryReference, **changes) -> GeometryResolutionEvidence:
    values = {
        "source_id": reference.source_id,
        "source_revision": reference.expected_source_revision,
        "geometry_fingerprint": reference.expected_geometry_fingerprint,
        "match_count": 1,
    }
    values.update(changes)
    return GeometryResolutionEvidence(**values)


def test_geometry_reference_round_trip_is_deterministic() -> None:
    reference = _reference()

    payload = reference.to_dict()
    restored = GeometryReference.from_dict(payload)

    assert restored == reference
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        payload, sort_keys=True
    )


def test_repeated_occurrences_and_their_faces_remain_distinct() -> None:
    first = _reference(occurrence_path="assembly:aaa/part:first")
    second = dataclasses.replace(
        first,
        occurrence_path="assembly:aaa/part:second",
    )

    assert first.target_key != second.target_key
    assert first.subshape_selector == second.subshape_selector
    assert first.occurrence_path != second.occurrence_path


def test_resolution_rejects_foreign_source() -> None:
    reference = _reference()

    result = assess_geometry_resolution(
        reference, _evidence(reference, source_id=uuid4())
    )

    assert result.status is GeometryResolutionStatus.SOURCE_MISMATCH


def test_stale_revision_and_changed_topology_are_distinct() -> None:
    reference = _reference()
    stale = assess_geometry_resolution(
        reference, _evidence(reference, source_revision=Revision(4))
    )
    changed = assess_geometry_resolution(
        reference,
        _evidence(
            reference,
            geometry_fingerprint=GeometryFingerprint.from_payload({"face": "new"}),
        ),
    )

    assert stale.status is GeometryResolutionStatus.STALE
    assert changed.status is GeometryResolutionStatus.TOPOLOGY_CHANGED


def test_ambiguous_resolution_never_selects_a_candidate() -> None:
    reference = _reference()

    result = assess_geometry_resolution(
        reference, _evidence(reference, match_count=2)
    )

    assert result.status is GeometryResolutionStatus.AMBIGUOUS
    assert not hasattr(result, "native_object")


@pytest.mark.parametrize(
    ("change", "status"),
    (
        ({"scheme_supported": False}, GeometryResolutionStatus.UNSUPPORTED_SCHEME),
        ({"version_supported": False}, GeometryResolutionStatus.UNSUPPORTED_VERSION),
        ({"match_count": 0}, GeometryResolutionStatus.MISSING),
    ),
)
def test_resolution_reports_unsupported_or_missing(change, status) -> None:
    reference = _reference()

    assert assess_geometry_resolution(reference, _evidence(reference, **change)).status is status


@pytest.mark.parametrize("future_version", (2, True, "1"))
def test_future_or_malformed_serialized_version_is_rejected(future_version) -> None:
    payload = _reference().to_dict()
    payload["format_version"] = future_version

    with pytest.raises(UnsupportedCamSchemaError):
        GeometryReference.from_dict(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.pop("source_id"),
        lambda payload: payload.update(reference_kind="future_kind"),
        lambda payload: payload.update(source_id="not-a-uuid"),
        lambda payload: payload.update(diagnostic_fallback=[{"key": "missing-value"}]),
    ),
)
def test_malformed_payload_never_creates_partial_reference(mutation) -> None:
    payload = _reference().to_dict()
    mutation(payload)

    with pytest.raises(GeometryReferenceError):
        GeometryReference.from_dict(payload)


def test_public_reference_graph_contains_no_native_cad_objects() -> None:
    reference = _reference()

    def walk(value):
        yield value
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                yield from walk(getattr(value, field.name))
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from walk(item)

    forbidden = ("OCP", "PySide6")
    assert all(
        not type(value).__module__.startswith(forbidden) for value in walk(reference)
    )


def test_reference_invariants_reject_invalid_subshape_shape() -> None:
    reference = _reference()

    with pytest.raises(GeometryReferenceError):
        dataclasses.replace(reference, subshape_selector=None)
