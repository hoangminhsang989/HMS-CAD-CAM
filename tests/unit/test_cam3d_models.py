"""Stage 8A.1 domain, codec, selection, boundary and policy tests."""

from __future__ import annotations

import dataclasses
import math
from uuid import uuid4

import pytest

from hms_cadcam.cam.cam3d import (
    BoundaryInclusionPolicy3D,
    BoundaryOrientation3D,
    Cam3DCalculationRequest,
    Cam3DSafeMotionPolicy,
    Cam3DStockAllowance,
    Cam3DTolerancePolicy,
    CamSurfaceOrientation,
    CamSurfaceReference,
    CamSurfaceRole,
    CamSurfaceSelection,
    CheckSurfaceSet,
    FixtureSurfaceSet,
    MachiningBoundary3D,
    MachiningBoundary3DKind,
    MachiningZone3D,
    PartSurfaceSet,
)
from hms_cadcam.cam.domain import (
    CamSurfaceSelectionId,
    CamValidationError,
    GeometryFingerprint,
    LengthUnit,
    Point3,
    Revision,
    SetupId,
    UnsupportedCamSchemaError,
    Vector3,
    WcsFrame,
)
from tests.unit._cam3d_fixtures import (
    geometry_reference,
    request,
    selection,
    surface,
    tolerance,
    zone,
)


def test_surface_reference_round_trip_and_identity() -> None:
    project_id, source_id = uuid4(), uuid4()
    value = surface(project_id, source_id, "face-a")
    restored = CamSurfaceReference.from_dict(value.to_dict())
    assert restored == value
    assert restored.face_identity == "face-a"
    assert restored.fingerprint == value.fingerprint


def test_surface_reference_rejects_invalid_enum_and_future_version() -> None:
    value = surface(uuid4(), uuid4(), "face-a")
    invalid = value.to_dict()
    invalid["role"] = "guessed_from_color"
    with pytest.raises(CamValidationError):
        CamSurfaceReference.from_dict(invalid)
    future = value.to_dict()
    future["format_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        CamSurfaceReference.from_dict(future)


def test_selection_canonical_order_and_fingerprint_ignore_selection_id() -> None:
    project_id, source_id = uuid4(), uuid4()
    first = surface(project_id, source_id, "face-b")
    second = surface(project_id, source_id, "face-a")
    left = selection(project_id, (first, second))
    right = CamSurfaceSelection(
        CamSurfaceSelectionId.new(),
        project_id,
        Revision(1),
        (second, first),
    )
    assert left.surfaces == right.surfaces
    assert left.fingerprint == right.fingerprint
    assert left.selection_id != right.selection_id
    assert CamSurfaceSelection.from_dict(left.to_dict()) == left


def test_selection_rejects_duplicate_target_even_with_new_reference_id() -> None:
    project_id, source_id = uuid4(), uuid4()
    first = surface(project_id, source_id, "same")
    duplicate = dataclasses.replace(
        first,
        geometry=dataclasses.replace(
            first.geometry, reference_id=first.geometry.reference_id.new()
        ),
    )
    with pytest.raises(CamValidationError, match="Duplicate"):
        selection(project_id, (first, duplicate))


def test_selection_rejects_stale_revision_and_project_mismatch() -> None:
    project_id, source_id = uuid4(), uuid4()
    stale = surface(project_id, source_id, "face", revision=Revision(2))
    with pytest.raises(CamValidationError, match="revision"):
        selection(project_id, (stale,), revision=Revision(1))
    current = surface(project_id, source_id, "face")
    with pytest.raises(CamValidationError, match="project"):
        selection(uuid4(), (current,))


def test_surface_sets_enforce_role_and_empty_policy() -> None:
    project_id, source_id = uuid4(), uuid4()
    part = surface(project_id, source_id, "part")
    check = surface(project_id, source_id, "check", CamSurfaceRole.CHECK)
    fixture = surface(project_id, source_id, "fixture", CamSurfaceRole.FIXTURE)
    assert PartSurfaceSet(selection(project_id, (part,))).selection.surfaces
    assert CheckSurfaceSet(
        selection(project_id, (), allow_empty=True)
    ).selection.surfaces == ()
    assert FixtureSurfaceSet(
        selection(project_id, (fixture,), allow_empty=True)
    ).selection.surfaces == (fixture,)
    with pytest.raises(CamValidationError):
        PartSurfaceSet(selection(project_id, (), allow_empty=True))
    with pytest.raises(CamValidationError):
        CheckSurfaceSet(selection(project_id, (part,), allow_empty=True))
    with pytest.raises(CamValidationError):
        FixtureSurfaceSet(selection(project_id, (check,), allow_empty=True))


def test_closed_boundary_round_trip_and_deterministic_fingerprint() -> None:
    value = zone(with_boundary=True).boundary
    assert value is not None
    restored = MachiningBoundary3D.from_dict(value.to_dict())
    assert restored == value
    assert restored.fingerprint == value.fingerprint


def test_closed_boundary_rejects_open_and_self_intersecting_contours() -> None:
    current = zone(with_boundary=True).boundary
    assert current is not None
    open_points = (*current.points[:-1], Point3(1, 0, 0, LengthUnit.MM))
    with pytest.raises(CamValidationError, match="open"):
        dataclasses.replace(current, points=open_points)
    bow_tie = (
        Point3(0, 0, 0, LengthUnit.MM),
        Point3(10, 10, 0, LengthUnit.MM),
        Point3(0, 10, 0, LengthUnit.MM),
        Point3(10, 0, 0, LengthUnit.MM),
        Point3(0, 0, 0, LengthUnit.MM),
    )
    with pytest.raises(CamValidationError, match="self-intersecting"):
        dataclasses.replace(current, points=bow_tie)


def test_boundary_none_and_silhouette_contracts_fail_closed() -> None:
    current = zone().part_surfaces.selection.surfaces[0]
    frame = WcsFrame.identity(LengthUnit.MM)
    none = MachiningBoundary3D(
        MachiningBoundary3DKind.NONE,
        SetupId.new(),
        frame,
        0.001,
        BoundaryOrientation3D.COUNTERCLOCKWISE,
        BoundaryInclusionPolicy3D.INSIDE,
        Revision(1),
    )
    assert not none.points
    silhouette = dataclasses.replace(
        none,
        kind=MachiningBoundary3DKind.SURFACE_SILHOUETTE_REFERENCE,
        source_references=(current.geometry,),
    )
    assert silhouette.source_references
    with pytest.raises(CamValidationError):
        dataclasses.replace(silhouette, source_references=())


def test_zone_rejects_wrong_setup_plane_and_nonfixed_tool_axis() -> None:
    current = zone(with_boundary=True)
    assert current.boundary is not None
    with pytest.raises(CamValidationError, match="another Setup"):
        dataclasses.replace(
            current,
            boundary=dataclasses.replace(current.boundary, setup_id=SetupId.new()),
        )
    rotated = WcsFrame(
        Point3(0, 0, 0, LengthUnit.MM),
        Vector3(0, 0, 1),
        Vector3(0, 1, 0),
        Vector3(-1, 0, 0),
    )
    with pytest.raises(CamValidationError, match="wrong Setup plane"):
        dataclasses.replace(
            current,
            boundary=dataclasses.replace(
                current.boundary,
                plane=rotated,
                points=(
                    Point3(0, 0, 0, LengthUnit.MM),
                    Point3(0, 0, 10, LengthUnit.MM),
                    Point3(0, 10, 10, LengthUnit.MM),
                    Point3(0, 10, 0, LengthUnit.MM),
                    Point3(0, 0, 0, LengthUnit.MM),
                ),
            ),
        )
    with pytest.raises(CamValidationError, match="Setup Z"):
        dataclasses.replace(current, tool_axis=Vector3(1, 0, 0))


def test_zone_round_trip_covers_part_check_fixture_and_boundary() -> None:
    value = zone(with_check=True, with_fixture=True, with_boundary=True)
    restored = MachiningZone3D.from_dict(value.to_dict())
    assert restored == value
    assert {item.role for item in restored.all_surfaces()} == {
        CamSurfaceRole.PART,
        CamSurfaceRole.CHECK,
        CamSurfaceRole.FIXTURE,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("chordal_tolerance", -1.0),
        ("chordal_tolerance", 0.0),
        ("chordal_tolerance", 100.0),
        ("angular_tolerance", 0.0),
        ("calculation_epsilon", math.nan),
        ("boundary_tolerance", math.inf),
        ("contact_tolerance", -1.0),
    ],
)
def test_tolerance_policy_rejects_nonfinite_or_unsafe_values(
    field: str, value: float
) -> None:
    with pytest.raises(CamValidationError):
        dataclasses.replace(tolerance(), **{field: value})


def test_tolerance_round_trip_and_change_affects_fingerprint() -> None:
    first = tolerance(0.01)
    second = tolerance(0.02)
    assert Cam3DTolerancePolicy.from_dict(first.to_dict()) == first
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, 1001.0])
def test_stock_allowance_rejects_invalid_values(value: float) -> None:
    with pytest.raises(CamValidationError):
        Cam3DStockAllowance(part_normal=value)


def test_allowance_zero_is_valid_and_separate_from_tolerance() -> None:
    allowance = Cam3DStockAllowance()
    assert Cam3DStockAllowance.from_dict(allowance.to_dict()) == allowance
    assert allowance.part_normal == 0.0
    assert allowance.fingerprint != tolerance().fingerprint


def test_nonfinite_safe_motion_field_is_rejected() -> None:
    value = zone()
    policy = request(value).safe_motion_policy
    with pytest.raises(CamValidationError):
        dataclasses.replace(policy, approach_distance=math.nan)


def test_request_runtime_token_does_not_affect_identity() -> None:
    value = request(zone())
    changed = dataclasses.replace(value, request_token=uuid4())
    assert value.request_token != changed.request_token
    assert value.fingerprint == changed.fingerprint


def test_geometry_reference_must_be_face() -> None:
    project_id, source_id = uuid4(), uuid4()
    reference = geometry_reference(source_id, "face")
    reference = dataclasses.replace(reference, kind=reference.kind.BODY)
    with pytest.raises(CamValidationError, match="FACE"):
        CamSurfaceReference(
            project_id,
            reference,
            CamSurfaceOrientation.FORWARD,
            CamSurfaceRole.PART,
        )
