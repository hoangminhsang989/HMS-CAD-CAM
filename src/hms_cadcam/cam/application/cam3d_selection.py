"""Project-scoped Part/Check/Fixture selection application contract.

The module is deliberately native-free and Qt-free.  It accepts immutable
viewport metadata through injected ports, validates project/document/source
provenance, and publishes only typed CAM 3D references to presentation code.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.cam3d import CamSurfaceReference, CamSurfaceRole
from hms_cadcam.cam.domain import GeometryReferenceKind, Revision
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

logger = logging.getLogger(__name__)


class Cam3DSelectionRole(StrEnum):
    """Roles editable in Stage 9A.8 WP2A."""

    PART = "part"
    CHECK = "check"
    FIXTURE = "fixture"

    @property
    def cam_role(self) -> CamSurfaceRole:
        return {
            Cam3DSelectionRole.PART: CamSurfaceRole.PART,
            Cam3DSelectionRole.CHECK: CamSurfaceRole.CHECK,
            Cam3DSelectionRole.FIXTURE: CamSurfaceRole.FIXTURE,
        }[self]

    @property
    def label_key(self) -> str:
        return {
            Cam3DSelectionRole.PART: "Part",
            Cam3DSelectionRole.CHECK: "Check",
            Cam3DSelectionRole.FIXTURE: "Fixtures",
        }[self]


class Cam3DSelectionValidity(StrEnum):
    """Validity of one immutable role item."""

    VALID = "valid"
    STALE = "stale"
    INVALID = "invalid"


class Cam3DSelectionIssue(StrEnum):
    """Stable internal outcomes mapped to localized presentation keys."""

    NONE = "none"
    NO_PROJECT = "no_project"
    NO_SELECTION = "no_selection"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INVALID_GEOMETRY_KIND = "invalid_geometry_kind"
    FOREIGN_DOCUMENT = "foreign_document"
    FOREIGN_PROJECT = "foreign_project"
    STALE_PROJECT = "stale_project"
    STALE_IDENTITY = "stale_identity"
    DUPLICATE_SURFACE = "duplicate_surface"
    READ_ONLY = "read_only"

    @property
    def label_key(self) -> str:
        return _ISSUE_LABEL_KEYS[self]


class Cam3DSelectionStatus(StrEnum):
    """Aggregate selection state independent from the WP1 shell state."""

    PROJECT_CLOSED = "project_closed"
    EMPTY = "empty"
    PARTIAL = "partial"
    RESOLVED = "resolved"
    STALE = "stale"
    INVALID = "invalid"

    @property
    def label_key(self) -> str:
        return _STATUS_LABEL_KEYS[self]


_ISSUE_LABEL_KEYS: Final[dict[Cam3DSelectionIssue, str]] = {
    Cam3DSelectionIssue.NONE: "Selection is valid",
    Cam3DSelectionIssue.NO_PROJECT: "Open a CAM project before assigning surfaces",
    Cam3DSelectionIssue.NO_SELECTION: "Select one or more BRep faces",
    Cam3DSelectionIssue.SOURCE_UNAVAILABLE: "Active CAD source is unavailable",
    Cam3DSelectionIssue.INVALID_GEOMETRY_KIND: "Current selection must contain BRep faces only",
    Cam3DSelectionIssue.FOREIGN_DOCUMENT: "Selection belongs to another document",
    Cam3DSelectionIssue.FOREIGN_PROJECT: "Selection belongs to another project",
    Cam3DSelectionIssue.STALE_PROJECT: "Selection belongs to an inactive project generation",
    Cam3DSelectionIssue.STALE_IDENTITY: "Selection identity is stale or unresolved",
    Cam3DSelectionIssue.DUPLICATE_SURFACE: "The same surface cannot be assigned to multiple roles",
    Cam3DSelectionIssue.READ_ONLY: "The project is read-only",
}

_STATUS_LABEL_KEYS: Final[dict[Cam3DSelectionStatus, str]] = {
    Cam3DSelectionStatus.PROJECT_CLOSED: "Project is closed",
    Cam3DSelectionStatus.EMPTY: "No surfaces selected",
    Cam3DSelectionStatus.PARTIAL: "Surface selection is partial",
    Cam3DSelectionStatus.RESOLVED: "Surface selection is resolved",
    Cam3DSelectionStatus.STALE: "Surface selection is stale",
    Cam3DSelectionStatus.INVALID: "Surface selection is invalid",
}


@dataclass(frozen=True, slots=True)
class Cam3DSelectionProvenance:
    """Project/document/source facts retained without native CAD handles."""

    project_id: UUID
    project_generation: int
    document_id: CadDocumentId
    source_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("Selection project identity is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("Selection project generation must be positive")
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("Selection document identity is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("Selection source identity is invalid")


@dataclass(frozen=True, slots=True)
class Cam3DSelectedSurface:
    """One role-owned stable face reference rendered by the WP2A panel."""

    role: Cam3DSelectionRole
    reference: CamSurfaceReference
    provenance: Cam3DSelectionProvenance
    display_label: str
    geometry_kind: GeometryReferenceKind = GeometryReferenceKind.FACE
    validity: Cam3DSelectionValidity = Cam3DSelectionValidity.VALID
    stale_reason: Cam3DSelectionIssue = Cam3DSelectionIssue.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        if not isinstance(self.reference, CamSurfaceReference):
            raise TypeError("CAM 3D surface reference is invalid")
        if not isinstance(self.provenance, Cam3DSelectionProvenance):
            raise TypeError("CAM 3D selection provenance is invalid")
        if not isinstance(self.display_label, str) or not self.display_label.strip():
            raise ValueError("CAM 3D selection display label is empty")
        if self.geometry_kind is not GeometryReferenceKind.FACE:
            raise ValueError("CAM 3D role selection requires FACE geometry")
        if not isinstance(self.validity, Cam3DSelectionValidity):
            raise TypeError("CAM 3D selection validity is invalid")
        if not isinstance(self.stale_reason, Cam3DSelectionIssue):
            raise TypeError("CAM 3D stale reason is invalid")
        if self.reference.role is not self.role.cam_role:
            raise ValueError("CAM 3D role conflicts with the surface reference")
        if self.reference.project_id != self.provenance.project_id:
            raise ValueError("CAM 3D surface belongs to another project")
        if self.reference.geometry.source_id != self.provenance.source_id:
            raise ValueError("CAM 3D surface belongs to another source")
        if self.reference.geometry.kind is not self.geometry_kind:
            raise ValueError("CAM 3D geometry kind is inconsistent")
        if self.validity is Cam3DSelectionValidity.VALID:
            if self.stale_reason is not Cam3DSelectionIssue.NONE:
                raise ValueError("Valid CAM 3D selection cannot have a stale reason")
        elif self.stale_reason is Cam3DSelectionIssue.NONE:
            raise ValueError("Non-valid CAM 3D selection requires a reason")

    @property
    def stable_identity(self) -> tuple[object, ...]:
        """Return persistent identity without UUID/enum presentation leakage."""

        return self.reference.target_key


@dataclass(frozen=True, slots=True)
class Cam3DSelectionState:
    """Immutable Part/Check/Fixture aggregate for one project generation."""

    project_id: UUID | None = None
    project_generation: int | None = None
    read_only: bool = False
    part: tuple[Cam3DSelectedSurface, ...] = ()
    check: tuple[Cam3DSelectedSurface, ...] = ()
    fixture: tuple[Cam3DSelectedSurface, ...] = ()
    issue: Cam3DSelectionIssue = Cam3DSelectionIssue.NONE

    def __post_init__(self) -> None:
        if (self.project_id is None) != (self.project_generation is None):
            raise ValueError("CAM 3D selection project identity must be paired")
        if self.project_id is not None and (
            not isinstance(self.project_id, UUID) or self.project_id.int == 0
        ):
            raise ValueError("CAM 3D selection project ID is invalid")
        if self.project_generation is not None and (
            type(self.project_generation) is not int or self.project_generation <= 0
        ):
            raise ValueError("CAM 3D selection generation must be positive")
        if type(self.read_only) is not bool:
            raise TypeError("CAM 3D selection read_only must be bool")
        if not isinstance(self.issue, Cam3DSelectionIssue):
            raise TypeError("CAM 3D selection issue is invalid")
        for role, items in self.role_items:
            if not isinstance(items, tuple) or any(
                not isinstance(item, Cam3DSelectedSurface) or item.role is not role
                for item in items
            ):
                raise TypeError("CAM 3D role items are invalid")
            if self.project_id is None and items:
                raise ValueError("Closed project cannot retain CAM 3D selections")
            if any(
                item.provenance.project_id != self.project_id
                or item.provenance.project_generation != self.project_generation
                for item in items
            ):
                raise ValueError("CAM 3D role item provenance is stale")
            keys = tuple(item.stable_identity for item in items)
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate CAM 3D role surface")
        all_keys = [
            item.stable_identity
            for _role, items in self.role_items
            for item in items
        ]
        if len(all_keys) != len(set(all_keys)):
            raise ValueError("One CAM 3D surface cannot own multiple roles")

    @classmethod
    def closed(cls) -> "Cam3DSelectionState":
        return cls()

    @classmethod
    def for_project(
        cls,
        project_id: UUID,
        project_generation: int,
        *,
        read_only: bool = False,
    ) -> "Cam3DSelectionState":
        return cls(project_id, project_generation, read_only)

    @property
    def role_items(
        self,
    ) -> tuple[tuple[Cam3DSelectionRole, tuple[Cam3DSelectedSurface, ...]], ...]:
        return (
            (Cam3DSelectionRole.PART, self.part),
            (Cam3DSelectionRole.CHECK, self.check),
            (Cam3DSelectionRole.FIXTURE, self.fixture),
        )

    @property
    def status(self) -> Cam3DSelectionStatus:
        if self.project_id is None:
            return Cam3DSelectionStatus.PROJECT_CLOSED
        all_items = tuple(item for _role, items in self.role_items for item in items)
        if self.issue in {
            Cam3DSelectionIssue.STALE_PROJECT,
            Cam3DSelectionIssue.STALE_IDENTITY,
        } or any(item.validity is Cam3DSelectionValidity.STALE for item in all_items):
            return Cam3DSelectionStatus.STALE
        if self.issue is not Cam3DSelectionIssue.NONE or any(
            item.validity is Cam3DSelectionValidity.INVALID for item in all_items
        ):
            return Cam3DSelectionStatus.INVALID
        populated = sum(bool(items) for _role, items in self.role_items)
        if populated == 3:
            return Cam3DSelectionStatus.RESOLVED
        if populated:
            return Cam3DSelectionStatus.PARTIAL
        return Cam3DSelectionStatus.EMPTY

    @property
    def resolved(self) -> bool:
        return self.status is Cam3DSelectionStatus.RESOLVED

    @property
    def can_mutate(self) -> bool:
        return self.project_id is not None and not self.read_only

    def items_for(self, role: Cam3DSelectionRole) -> tuple[Cam3DSelectedSurface, ...]:
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        return {
            Cam3DSelectionRole.PART: self.part,
            Cam3DSelectionRole.CHECK: self.check,
            Cam3DSelectionRole.FIXTURE: self.fixture,
        }[role]

    def assign(
        self,
        role: Cam3DSelectionRole,
        items: tuple[Cam3DSelectedSurface, ...],
    ) -> "Cam3DSelectionState":
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        if self.project_id is None:
            return replace(self, issue=Cam3DSelectionIssue.NO_PROJECT)
        if self.read_only:
            return replace(self, issue=Cam3DSelectionIssue.READ_ONLY)
        if not isinstance(items, tuple) or not items:
            return replace(self, issue=Cam3DSelectionIssue.NO_SELECTION)
        if any(item.role is not role for item in items):
            raise ValueError("CAM 3D assignment role is inconsistent")
        keys = tuple(item.stable_identity for item in items)
        if len(keys) != len(set(keys)):
            return replace(self, issue=Cam3DSelectionIssue.DUPLICATE_SURFACE)
        other_keys = {
            item.stable_identity
            for other_role, other_items in self.role_items
            if other_role is not role
            for item in other_items
        }
        if any(key in other_keys for key in keys):
            return replace(self, issue=Cam3DSelectionIssue.DUPLICATE_SURFACE)
        field_name = role.value
        return replace(self, **{field_name: items, "issue": Cam3DSelectionIssue.NONE})

    def clear_role(self, role: Cam3DSelectionRole) -> "Cam3DSelectionState":
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        if self.project_id is None:
            return replace(self, issue=Cam3DSelectionIssue.NO_PROJECT)
        if self.read_only:
            return replace(self, issue=Cam3DSelectionIssue.READ_ONLY)
        return replace(self, **{role.value: (), "issue": Cam3DSelectionIssue.NONE})

    def clear_all(self) -> "Cam3DSelectionState":
        if self.project_id is None:
            return replace(self, issue=Cam3DSelectionIssue.NO_PROJECT)
        if self.read_only:
            return replace(self, issue=Cam3DSelectionIssue.READ_ONLY)
        return replace(
            self,
            part=(),
            check=(),
            fixture=(),
            issue=Cam3DSelectionIssue.NONE,
        )

    def with_issue(self, issue: Cam3DSelectionIssue) -> "Cam3DSelectionState":
        if not isinstance(issue, Cam3DSelectionIssue):
            raise TypeError("CAM 3D selection issue is invalid")
        return replace(self, issue=issue)

    def mark_stale(self) -> "Cam3DSelectionState":
        if self.project_id is None:
            return self

        def stale_items(
            items: tuple[Cam3DSelectedSurface, ...],
        ) -> tuple[Cam3DSelectedSurface, ...]:
            return tuple(
                replace(
                    item,
                    validity=Cam3DSelectionValidity.STALE,
                    stale_reason=Cam3DSelectionIssue.STALE_IDENTITY,
                )
                for item in items
            )

        return replace(
            self,
            part=stale_items(self.part),
            check=stale_items(self.check),
            fixture=stale_items(self.fixture),
            issue=Cam3DSelectionIssue.STALE_IDENTITY,
        )


@dataclass(frozen=True, slots=True)
class Cam3DSelectionSource:
    """Current eligible viewport selection and its application provenance."""

    project_id: UUID
    project_generation: int
    document_id: CadDocumentId | None
    source_id: UUID | None
    read_only: bool
    selections: tuple[SelectionMetadata, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("CAM 3D source project identity is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D source generation must be positive")
        if self.document_id is not None and not isinstance(
            self.document_id, CadDocumentId
        ):
            raise TypeError("CAM 3D source document identity is invalid")
        if self.source_id is not None and not isinstance(self.source_id, UUID):
            raise TypeError("CAM 3D source identity is invalid")
        if type(self.read_only) is not bool:
            raise TypeError("CAM 3D source read_only must be bool")
        if not isinstance(self.selections, tuple) or any(
            not isinstance(item, SelectionMetadata) for item in self.selections
        ):
            raise TypeError("CAM 3D viewport selection is invalid")


SelectionSourceProvider = Callable[[], Cam3DSelectionSource | None]
SurfaceBinder = Callable[[SelectionMetadata, CamSurfaceRole], CamSurfaceReference]


class Cam3DSelectionApplicationService:
    """Own one runtime-only WP2A selection aggregate for the active project."""

    def __init__(
        self,
        source_provider: SelectionSourceProvider,
        surface_binder: SurfaceBinder,
    ) -> None:
        if not callable(source_provider) or not callable(surface_binder):
            raise TypeError("CAM 3D selection ports must be callable")
        self._source_provider = source_provider
        self._surface_binder = surface_binder
        self._state = Cam3DSelectionState.closed()

    @property
    def state(self) -> Cam3DSelectionState:
        return self._state

    def bind_project(
        self,
        project_id: UUID | None,
        project_generation: int | None,
        *,
        read_only: bool = False,
    ) -> Cam3DSelectionState:
        if project_id is None:
            if project_generation is not None:
                raise ValueError("Closed project cannot retain a generation")
            self._state = Cam3DSelectionState.closed()
            return self._state
        candidate = Cam3DSelectionState.for_project(
            project_id,
            project_generation,  # type: ignore[arg-type]
            read_only=read_only,
        )
        if (
            self._state.project_id == candidate.project_id
            and self._state.project_generation == candidate.project_generation
        ):
            self._state = replace(
                self._state,
                read_only=read_only,
                issue=Cam3DSelectionIssue.NONE,
            )
        else:
            self._state = candidate
        return self._state

    def reset(self) -> Cam3DSelectionState:
        self._state = Cam3DSelectionState.closed()
        return self._state

    def read_current_eligible_selection(
        self,
        role: Cam3DSelectionRole,
    ) -> tuple[Cam3DSelectedSurface, ...]:
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        source = self._source_provider()
        issue = self._validate_source(source)
        if issue is not Cam3DSelectionIssue.NONE:
            self._state = self._state.with_issue(issue)
            return ()
        assert source is not None
        resolved: list[Cam3DSelectedSurface] = []
        try:
            for index, selection in enumerate(source.selections, start=1):
                reference = self._surface_binder(selection, role.cam_role)
                issue = self.validate_identity(reference, source, role)
                if issue is not Cam3DSelectionIssue.NONE:
                    self._state = self._state.with_issue(issue)
                    return ()
                assert source.document_id is not None and source.source_id is not None
                resolved.append(
                    Cam3DSelectedSurface(
                        role=role,
                        reference=reference,
                        provenance=Cam3DSelectionProvenance(
                            source.project_id,
                            source.project_generation,
                            source.document_id,
                            source.source_id,
                        ),
                        display_label=f"CAD surface {index}",
                    )
                )
        except (RuntimeError, TypeError, ValueError):
            logger.warning("CAM 3D selection identity could not be resolved", exc_info=True)
            self._state = self._state.with_issue(Cam3DSelectionIssue.STALE_IDENTITY)
            return ()
        keys = tuple(item.stable_identity for item in resolved)
        if len(keys) != len(set(keys)):
            self._state = self._state.with_issue(
                Cam3DSelectionIssue.DUPLICATE_SURFACE
            )
            return ()
        return tuple(resolved)

    def assign_current(self, role: Cam3DSelectionRole) -> Cam3DSelectionState:
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        if self._state.read_only:
            self._state = self._state.with_issue(Cam3DSelectionIssue.READ_ONLY)
            return self._state
        items = self.read_current_eligible_selection(role)
        if not items:
            return self._state
        self._state = self._state.assign(role, items)
        return self._state

    def clear_role(self, role: Cam3DSelectionRole) -> Cam3DSelectionState:
        if not isinstance(role, Cam3DSelectionRole):
            raise TypeError("CAM 3D selection role is invalid")
        issue = self._validate_mutation_context(self._source_provider())
        if issue is not Cam3DSelectionIssue.NONE:
            self._state = self._state.with_issue(issue)
            return self._state
        self._state = self._state.clear_role(role)
        return self._state

    def clear_all(self) -> Cam3DSelectionState:
        issue = self._validate_mutation_context(self._source_provider())
        if issue is not Cam3DSelectionIssue.NONE:
            self._state = self._state.with_issue(issue)
            return self._state
        self._state = self._state.clear_all()
        return self._state

    def mark_stale(self) -> Cam3DSelectionState:
        self._state = self._state.mark_stale()
        return self._state

    @staticmethod
    def validate_identity(
        reference: CamSurfaceReference,
        source: Cam3DSelectionSource,
        role: Cam3DSelectionRole,
    ) -> Cam3DSelectionIssue:
        if not isinstance(reference, CamSurfaceReference):
            return Cam3DSelectionIssue.STALE_IDENTITY
        if reference.role is not role.cam_role:
            return Cam3DSelectionIssue.STALE_IDENTITY
        if reference.project_id != source.project_id:
            return Cam3DSelectionIssue.FOREIGN_PROJECT
        if reference.geometry.source_id != source.source_id:
            return Cam3DSelectionIssue.STALE_IDENTITY
        if reference.geometry.kind is not GeometryReferenceKind.FACE:
            return Cam3DSelectionIssue.INVALID_GEOMETRY_KIND
        if reference.geometry.expected_source_revision != Revision(0):
            return Cam3DSelectionIssue.STALE_IDENTITY
        return Cam3DSelectionIssue.NONE

    def resolve_display_summary(self) -> tuple[tuple[Cam3DSelectionRole, int], ...]:
        return tuple(
            (role, len(items)) for role, items in self._state.role_items
        )

    def _validate_source(
        self,
        source: Cam3DSelectionSource | None,
    ) -> Cam3DSelectionIssue:
        issue = self._validate_mutation_context(source)
        if issue is not Cam3DSelectionIssue.NONE:
            return issue
        assert source is not None
        if source.document_id is None or source.source_id is None:
            return Cam3DSelectionIssue.SOURCE_UNAVAILABLE
        if not source.selections:
            return Cam3DSelectionIssue.NO_SELECTION
        if any(
            item.document_id != source.document_id
            for item in source.selections
        ):
            return Cam3DSelectionIssue.FOREIGN_DOCUMENT
        if any(
            item.topology is not SelectionMode.FACE or item.object_id is None
            for item in source.selections
        ):
            return Cam3DSelectionIssue.INVALID_GEOMETRY_KIND
        return Cam3DSelectionIssue.NONE

    def _validate_mutation_context(
        self,
        source: Cam3DSelectionSource | None,
    ) -> Cam3DSelectionIssue:
        if self._state.project_id is None or source is None:
            return Cam3DSelectionIssue.NO_PROJECT
        if source.project_id != self._state.project_id:
            return Cam3DSelectionIssue.FOREIGN_PROJECT
        if source.project_generation != self._state.project_generation:
            return Cam3DSelectionIssue.STALE_PROJECT
        if self._state.read_only or source.read_only:
            return Cam3DSelectionIssue.READ_ONLY
        return Cam3DSelectionIssue.NONE


__all__ = [
    "Cam3DSelectedSurface",
    "Cam3DSelectionApplicationService",
    "Cam3DSelectionIssue",
    "Cam3DSelectionProvenance",
    "Cam3DSelectionRole",
    "Cam3DSelectionSource",
    "Cam3DSelectionState",
    "Cam3DSelectionStatus",
    "Cam3DSelectionValidity",
]
