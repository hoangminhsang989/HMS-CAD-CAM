"""Tests for CAM job and setup aggregate invariants."""

import dataclasses
import json
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    AffineTransform,
    BoxStock,
    CamChildNotFoundError,
    CamInvariantError,
    CamJob,
    CamJobId,
    CamSourceScopeError,
    CamValidationError,
    DuplicateCamIdError,
    FixtureInstance,
    FixtureInstanceId,
    FixtureRole,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    Length,
    LengthUnit,
    ModelStock,
    Revision,
    Setup,
    SetupId,
    SetupKind,
    SourceScope,
    UnsupportedCamSchemaError,
    WcsFrame,
    WorkOffset,
)


def _reference(source_id, selector: str) -> GeometryReference:
    return GeometryReference(
        GeometryReferenceId.new(),
        "hms_persistent_geometry",
        1,
        source_id,
        GeometryReferenceKind.BODY,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}),
        Revision(1),
        subshape_selector=selector,
    )


def _translated(x: float) -> AffineTransform:
    return AffineTransform(
        (
            1.0,
            0.0,
            0.0,
            x,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ),
        LengthUnit.MM,
    )


def _fixture(source_id, *, fixture_id=None, x: float = 0.0) -> FixtureInstance:
    return FixtureInstance(
        fixture_id or FixtureInstanceId.new(),
        "Clamp",
        _reference(source_id, "fixture:clamp"),
        _translated(x),
        FixtureRole.CLAMP,
    )


def _setup(
    *,
    setup_id=None,
    name: str = "Setup 1",
    source_id=None,
    auxiliary_source_ids=(),
    fixtures=(),
    stock=None,
) -> Setup:
    primary = source_id or uuid4()
    frame = WcsFrame.identity(LengthUnit.MM)
    return Setup(
        setup_id or SetupId.new(),
        name,
        SetupKind.MILL,
        frame,
        WorkOffset("PRIMARY", 1),
        stock
        or BoxStock(
            Length(100.0, LengthUnit.MM),
            Length(80.0, LengthUnit.MM),
            Length(20.0, LengthUnit.MM),
            frame,
        ),
        _reference(primary, "model:main"),
        SourceScope(primary, tuple(auxiliary_source_ids)),
        tuple(fixtures),
    )


def test_create_valid_cam_job_and_first_setup_becomes_active() -> None:
    job = CamJob(CamJobId.new(), "Main CAM")
    setup = _setup()

    job.add_setup(setup)

    assert job.name == "Main CAM"
    assert job.setups == (setup,)
    assert job.active_setup == setup
    assert job.revision == Revision(1)


@pytest.mark.parametrize("factory", (lambda: CamJob(CamJobId.new(), " "), lambda: _setup(name="")))
def test_empty_job_or_setup_name_is_rejected(factory) -> None:
    with pytest.raises(CamValidationError):
        factory()


def test_duplicate_setup_id_is_rejected_without_revision_change() -> None:
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    before = job.to_dict()

    with pytest.raises(DuplicateCamIdError):
        job.add_setup(dataclasses.replace(setup, name="Duplicate"))

    assert job.to_dict() == before


def test_active_setup_must_exist() -> None:
    with pytest.raises(CamInvariantError):
        CamJob(
            CamJobId.new(),
            "Job",
            setups=(_setup(),),
            active_setup_id=SetupId.new(),
        )


def test_removing_active_setup_falls_back_to_first_remaining() -> None:
    first = _setup(name="First")
    second = _setup(name="Second")
    job = CamJob(
        CamJobId.new(),
        "Job",
        setups=(first, second),
        active_setup_id=second.setup_id,
    )

    job.remove_setup(second.setup_id)

    assert job.setups == (first,)
    assert job.active_setup_id == first.setup_id


def test_failed_mutation_does_not_increment_revision_or_change_state() -> None:
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    before = job.to_dict()

    with pytest.raises(CamValidationError):
        job.rename_setup(setup.setup_id, " ")

    assert job.revision == Revision(0)
    assert job.to_dict() == before


def test_successful_mutations_increment_revision_once_and_noop_does_not() -> None:
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)

    job.rename_setup(setup.setup_id, "Renamed")
    assert job.revision == Revision(1)
    job.rename_setup(setup.setup_id, "Renamed")
    assert job.revision == Revision(1)
    job.update_wcs(setup.setup_id, WcsFrame.identity(LengthUnit.MM))
    assert job.revision == Revision(1)


def test_repeated_fixture_geometry_has_independent_ids_and_transforms() -> None:
    source_id = uuid4()
    shared_reference = _reference(source_id, "fixture:shared")
    first = FixtureInstance(
        FixtureInstanceId.new(),
        "First",
        shared_reference,
        _translated(0.0),
    )
    second = FixtureInstance(
        FixtureInstanceId.new(),
        "Second",
        shared_reference,
        _translated(50.0),
    )
    setup = _setup(source_id=source_id, fixtures=(first, second))

    assert setup.fixtures[0].fixture_id != setup.fixtures[1].fixture_id
    assert setup.fixtures[0].transform != setup.fixtures[1].transform
    assert setup.fixtures[0].geometry_reference == setup.fixtures[1].geometry_reference


@pytest.mark.parametrize("perspective", (0.5, 1.0e-13))
def test_non_affine_fixture_transform_is_rejected(perspective) -> None:
    values = list(AffineTransform.identity(LengthUnit.MM).values)
    values[12] = perspective

    with pytest.raises(CamValidationError):
        AffineTransform(tuple(values), LengthUnit.MM)


def test_fixture_transform_unit_must_match_setup_wcs() -> None:
    source_id = uuid4()
    fixture = dataclasses.replace(
        _fixture(source_id),
        transform=AffineTransform.identity(LengthUnit.INCH),
    )

    with pytest.raises(CamValidationError):
        _setup(source_id=source_id, fixtures=(fixture,))


def test_duplicate_fixture_id_is_rejected() -> None:
    source_id = uuid4()
    first = _fixture(source_id)
    duplicate = dataclasses.replace(first, name="Duplicate", transform=_translated(10.0))

    with pytest.raises(DuplicateCamIdError):
        _setup(source_id=source_id, fixtures=(first, duplicate))


def test_foreign_fixture_requires_explicit_auxiliary_source() -> None:
    primary = uuid4()
    fixture_source = uuid4()
    fixture = _fixture(fixture_source)

    with pytest.raises(CamSourceScopeError):
        _setup(source_id=primary, fixtures=(fixture,))

    setup = _setup(
        source_id=primary,
        auxiliary_source_ids=(fixture_source,),
        fixtures=(fixture,),
    )
    assert fixture_source in setup.source_scope.allowed_source_ids


def test_foreign_model_stock_requires_explicit_auxiliary_source() -> None:
    primary = uuid4()
    stock_source = uuid4()
    stock = ModelStock(_reference(stock_source, "stock:model"))

    with pytest.raises(CamSourceScopeError):
        _setup(source_id=primary, stock=stock)

    assert _setup(
        source_id=primary,
        auxiliary_source_ids=(stock_source,),
        stock=stock,
    ).stock == stock


def test_job_fixture_mutations_are_atomic_and_ordered() -> None:
    source_id = uuid4()
    setup = _setup(source_id=source_id)
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    first = _fixture(source_id, x=1.0)
    second = _fixture(source_id, x=2.0)

    job.add_fixture(setup.setup_id, first)
    job.add_fixture(setup.setup_id, second)
    updated = dataclasses.replace(first, name="Updated")
    job.update_fixture(setup.setup_id, updated)
    job.remove_fixture(setup.setup_id, second.fixture_id)

    assert job.get_setup(setup.setup_id).fixtures == (updated,)
    assert job.revision == Revision(4)


def test_reorder_setup_preserves_active_identity_and_increments_revision() -> None:
    first = _setup(name="First")
    second = _setup(name="Second")
    job = CamJob(
        CamJobId.new(),
        "Job",
        setups=(first, second),
        active_setup_id=first.setup_id,
    )

    job.reorder_setup(second.setup_id, 0)

    assert job.setups == (second, first)
    assert job.active_setup_id == first.setup_id
    assert job.revision == Revision(1)


def test_missing_child_error_does_not_change_job() -> None:
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    before = job.to_dict()

    with pytest.raises(CamChildNotFoundError):
        job.remove_fixture(setup.setup_id, FixtureInstanceId.new())

    assert job.to_dict() == before


def test_public_collections_and_setup_snapshots_cannot_be_modified_directly() -> None:
    setup = _setup()
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)

    assert isinstance(job.setups, tuple)
    assert isinstance(job.setups[0].fixtures, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setup.name = "Bypass aggregate"


def test_cam_job_round_trip_is_deterministic_and_preserves_order() -> None:
    first = _setup(name="First")
    second = _setup(name="Second")
    job = CamJob(
        CamJobId.new(),
        "Job",
        revision=Revision(9),
        setups=(first, second),
        active_setup_id=second.setup_id,
    )

    restored = CamJob.from_dict(job.to_dict())

    assert restored == job
    assert tuple(item.name for item in restored.setups) == ("First", "Second")
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        job.to_dict(), sort_keys=True
    )


def test_setup_and_fixture_round_trip() -> None:
    source_id = uuid4()
    setup = _setup(source_id=source_id, fixtures=(_fixture(source_id),))

    restored = Setup.from_dict(setup.to_dict())

    assert restored == setup
    assert FixtureInstance.from_dict(setup.fixtures[0].to_dict()) == setup.fixtures[0]


def test_future_job_schema_is_rejected() -> None:
    payload = CamJob(CamJobId.new(), "Job").to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        CamJob.from_dict(payload)


def test_malformed_child_payload_does_not_change_existing_aggregate() -> None:
    source_id = uuid4()
    setup = _setup(source_id=source_id, fixtures=(_fixture(source_id),))
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)
    payload = job.to_dict()
    payload["setups"][0]["fixtures"][0]["transform"]["values"][15] = 0.0
    before = job.to_dict()

    with pytest.raises(CamValidationError):
        CamJob.from_dict(payload)

    assert job.to_dict() == before


def test_public_aggregate_graph_contains_no_ocp_or_pyside_types() -> None:
    source_id = uuid4()
    setup = _setup(source_id=source_id, fixtures=(_fixture(source_id),))
    job = CamJob(CamJobId.new(), "Job", setups=(setup,), active_setup_id=setup.setup_id)

    def walk(value):
        yield value
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                yield from walk(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                yield from walk(item)

    public_values = (job, job.setups, job.active_setup, job.revision)
    assert all(
        not type(value).__module__.startswith(("OCP", "PySide6"))
        for root in public_values
        for value in walk(root)
    )
