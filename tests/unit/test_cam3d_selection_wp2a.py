from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from hms_cadcam.cad.models import BoundingBox, CadDocumentId, CadObjectId
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectedSurface,
    Cam3DSelectionApplicationService,
    Cam3DSelectionIssue,
    Cam3DSelectionProvenance,
    Cam3DSelectionRole,
    Cam3DSelectionSource,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
    Cam3DSelectionValidity,
)
from hms_cadcam.cam.cam3d import CamSurfaceRole
from hms_cadcam.cam.domain import GeometryReferenceKind, Revision
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from tests.unit._cam3d_fixtures import surface


def _metadata(
    document_id: CadDocumentId,
    name: str,
    *,
    topology: SelectionMode = SelectionMode.FACE,
) -> SelectionMetadata:
    return SelectionMetadata(
        document_id,
        f"{document_id}:{topology.value}:{name}",
        topology,
        BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
        CadObjectId(f"object-{name}"),
    )


def _item(
    role: Cam3DSelectionRole,
    selector: str,
    *,
    project_id=None,
    source_id=None,
    generation: int = 4,
    document_id: CadDocumentId | None = None,
) -> Cam3DSelectedSurface:
    project_id = project_id or uuid4()
    source_id = source_id or uuid4()
    document_id = document_id or CadDocumentId("document-1")
    return Cam3DSelectedSurface(
        role,
        surface(
            project_id,
            source_id,
            selector,
            role.cam_role,
            revision=Revision(0),
        ),
        Cam3DSelectionProvenance(
            project_id,
            generation,
            document_id,
            source_id,
        ),
        f"CAD surface {selector}",
    )


def test_selection_state_default_construction_and_role_labels_are_typed() -> None:
    state = Cam3DSelectionState.closed()
    assert state.status is Cam3DSelectionStatus.PROJECT_CLOSED
    assert not state.resolved
    assert tuple(role.label_key for role in Cam3DSelectionRole) == (
        "Part",
        "Check",
        "Fixtures",
    )
    assert Cam3DSelectionIssue.INVALID_GEOMETRY_KIND.label_key != (
        Cam3DSelectionIssue.INVALID_GEOMETRY_KIND.value
    )
    with pytest.raises(TypeError, match="role"):
        state.assign("part", ())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="role"):
        state.clear_role("part")  # type: ignore[arg-type]


def test_role_assignment_replace_remove_and_clear_are_immutable() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    empty = Cam3DSelectionState.for_project(project_id, 4)
    first = _item(
        Cam3DSelectionRole.PART,
        "part-a",
        project_id=project_id,
        source_id=source_id,
        document_id=document_id,
    )
    second = _item(
        Cam3DSelectionRole.PART,
        "part-b",
        project_id=project_id,
        source_id=source_id,
        document_id=document_id,
    )

    assigned = empty.assign(Cam3DSelectionRole.PART, (first,))
    replaced = assigned.assign(Cam3DSelectionRole.PART, (second,))
    removed = replaced.clear_role(Cam3DSelectionRole.PART)

    assert empty.part == ()
    assert assigned.part == (first,)
    assert replaced.part == (second,)
    assert removed.status is Cam3DSelectionStatus.EMPTY
    assert replaced.clear_all().part == ()


def test_part_check_fixture_resolve_only_when_all_roles_are_present() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    state = Cam3DSelectionState.for_project(project_id, 4)
    for role in Cam3DSelectionRole:
        state = state.assign(
            role,
            (
                _item(
                    role,
                    role.value,
                    project_id=project_id,
                    source_id=source_id,
                    document_id=document_id,
                ),
            ),
        )
        if role is not Cam3DSelectionRole.FIXTURE:
            assert state.status is Cam3DSelectionStatus.PARTIAL
    assert state.status is Cam3DSelectionStatus.RESOLVED
    assert state.resolved


def test_duplicate_surface_cannot_silently_own_multiple_roles() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    part = _item(
        Cam3DSelectionRole.PART,
        "shared",
        project_id=project_id,
        source_id=source_id,
        document_id=document_id,
    )
    check = replace(
        part,
        role=Cam3DSelectionRole.CHECK,
        reference=replace(part.reference, role=CamSurfaceRole.CHECK),
    )
    state = Cam3DSelectionState.for_project(project_id, 4).assign(
        Cam3DSelectionRole.PART,
        (part,),
    )
    rejected = state.assign(Cam3DSelectionRole.CHECK, (check,))

    assert rejected.part == state.part
    assert rejected.check == ()
    assert rejected.issue is Cam3DSelectionIssue.DUPLICATE_SURFACE
    assert rejected.status is Cam3DSelectionStatus.INVALID


def test_non_face_item_and_stale_item_fail_closed() -> None:
    item = _item(Cam3DSelectionRole.PART, "part")
    with pytest.raises(ValueError, match="FACE"):
        replace(item, geometry_kind=GeometryReferenceKind.EDGE)
    stale = replace(
        item,
        validity=Cam3DSelectionValidity.STALE,
        stale_reason=Cam3DSelectionIssue.STALE_IDENTITY,
    )
    state = Cam3DSelectionState.for_project(
        item.provenance.project_id,
        item.provenance.project_generation,
    ).assign(Cam3DSelectionRole.PART, (stale,))
    assert state.status is Cam3DSelectionStatus.STALE
    assert not state.resolved


def test_read_only_and_project_reset_reject_mutation_without_losing_identity() -> None:
    project_id = uuid4()
    state = Cam3DSelectionState.for_project(project_id, 7, read_only=True)
    rejected = state.assign(Cam3DSelectionRole.PART, ())
    assert rejected.project_id == project_id
    assert rejected.issue is Cam3DSelectionIssue.READ_ONLY
    assert not rejected.can_mutate
    assert Cam3DSelectionState.closed().part == ()


def test_selection_state_equality_is_deterministic() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    item = _item(
        Cam3DSelectionRole.PART,
        "stable",
        project_id=project_id,
        source_id=source_id,
        document_id=document_id,
    )
    left = Cam3DSelectionState.for_project(project_id, 4).assign(
        Cam3DSelectionRole.PART,
        (item,),
    )
    right = Cam3DSelectionState.for_project(project_id, 4).assign(
        Cam3DSelectionRole.PART,
        (item,),
    )
    assert left == right
    assert hash(item) == hash(item)


def test_application_service_assigns_three_roles_and_replaces_one_role() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    current = [_metadata(document_id, "part")]

    def source_provider() -> Cam3DSelectionSource:
        return Cam3DSelectionSource(
            project_id,
            5,
            document_id,
            source_id,
            False,
            tuple(current),
        )

    def binder(metadata: SelectionMetadata, role: CamSurfaceRole):
        return surface(
            project_id,
            source_id,
            metadata.selection_id,
            role,
            revision=Revision(0),
        )

    service = Cam3DSelectionApplicationService(source_provider, binder)
    service.bind_project(project_id, 5)
    for role in Cam3DSelectionRole:
        current[:] = [_metadata(document_id, role.value)]
        service.assign_current(role)
    assert service.state.resolved

    cleared_check = service.clear_role(Cam3DSelectionRole.CHECK)
    assert cleared_check.check == ()
    assert len(cleared_check.part) == 1
    assert len(cleared_check.fixture) == 1
    current[:] = [_metadata(document_id, Cam3DSelectionRole.CHECK.value)]
    assert service.assign_current(Cam3DSelectionRole.CHECK).resolved

    current[:] = [_metadata(document_id, "part-replacement")]
    replaced = service.assign_current(Cam3DSelectionRole.PART)
    assert len(replaced.part) == 1
    assert "part-replacement" in replaced.part[0].reference.geometry.subshape_selector
    assert service.clear_all().status is Cam3DSelectionStatus.EMPTY


def test_application_service_rejects_invalid_kind_and_empty_selection() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    current: list[SelectionMetadata] = []

    def source_provider() -> Cam3DSelectionSource:
        return Cam3DSelectionSource(
            project_id,
            2,
            document_id,
            source_id,
            False,
            tuple(current),
        )

    service = Cam3DSelectionApplicationService(
        source_provider,
        lambda metadata, role: surface(
            project_id,
            source_id,
            metadata.selection_id,
            role,
            revision=Revision(0),
        ),
    )
    service.bind_project(project_id, 2)
    service.assign_current(Cam3DSelectionRole.PART)
    assert service.state.issue is Cam3DSelectionIssue.NO_SELECTION
    current[:] = [_metadata(document_id, "edge", topology=SelectionMode.EDGE)]
    service.assign_current(Cam3DSelectionRole.PART)
    assert service.state.issue is Cam3DSelectionIssue.INVALID_GEOMETRY_KIND
    assert service.state.part == ()


def test_application_service_read_only_and_generation_guards_are_fail_closed() -> None:
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("document-1")
    generation = [3]
    source_read_only = [False]

    def source_provider() -> Cam3DSelectionSource:
        return Cam3DSelectionSource(
            project_id,
            generation[0],
            document_id,
            source_id,
            source_read_only[0],
            (_metadata(document_id, "part"),),
        )

    service = Cam3DSelectionApplicationService(
        source_provider,
        lambda metadata, role: surface(
            project_id,
            source_id,
            metadata.selection_id,
            role,
            revision=Revision(0),
        ),
    )
    service.bind_project(project_id, 3, read_only=True)
    service.assign_current(Cam3DSelectionRole.PART)
    assert service.state.issue is Cam3DSelectionIssue.READ_ONLY
    assert service.state.part == ()

    service.bind_project(project_id, 3, read_only=False)
    assigned = service.assign_current(Cam3DSelectionRole.PART)
    assert len(assigned.part) == 1

    source_read_only[0] = True
    rejected_clear = service.clear_role(Cam3DSelectionRole.PART)
    assert rejected_clear.issue is Cam3DSelectionIssue.READ_ONLY
    assert rejected_clear.part == assigned.part

    source_read_only[0] = False
    service.bind_project(project_id, 3, read_only=False)
    generation[0] = 4
    rejected_clear_all = service.clear_all()
    assert rejected_clear_all.status is Cam3DSelectionStatus.STALE
    assert rejected_clear_all.part == assigned.part


def test_project_switch_and_close_drop_previous_role_binding() -> None:
    first = uuid4()
    second = uuid4()
    service = Cam3DSelectionApplicationService(lambda: None, lambda _item, _role: None)  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError, match="role"):
        service.assign_current("part")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="role"):
        service.clear_role("part")  # type: ignore[arg-type]
    service.bind_project(first, 1)
    assert service.state.project_id == first
    service.bind_project(second, 2)
    assert service.state.project_id == second
    assert service.state.part == ()
    service.reset()
    assert service.state.status is Cam3DSelectionStatus.PROJECT_CLOSED
