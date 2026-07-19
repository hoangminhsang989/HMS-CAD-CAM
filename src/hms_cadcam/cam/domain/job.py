"""CAM job aggregate owning ordered immutable setup snapshots."""

from __future__ import annotations

from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import (
    CamChildNotFoundError,
    CamInvariantError,
    CamValidationError,
    DuplicateCamIdError,
)
from hms_cadcam.cam.domain.ids import CamJobId, FixtureInstanceId, SetupId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.domain.operation_tree import OperationTree
from hms_cadcam.cam.domain.setup import (
    FixtureInstance,
    Setup,
    StockDefinition,
    WorkOffset,
    _name,
)
from hms_cadcam.cam.domain.spatial import WcsFrame, _strict_payload

_CAM_JOB_FORMAT = "HMS_CAM_JOB"
_CAM_JOB_VERSION = 1


class CamJob:
    """Aggregate root controlling every mutation of its ordered setups."""

    SERIALIZATION_VERSION: ClassVar[int] = _CAM_JOB_VERSION
    __slots__ = ("_active_setup_id", "_job_id", "_name", "_revision", "_setups")

    def __init__(
        self,
        job_id: CamJobId,
        name: str,
        *,
        revision: Revision | None = None,
        setups: tuple[Setup, ...] = (),
        active_setup_id: SetupId | None = None,
    ) -> None:
        if not isinstance(job_id, CamJobId):
            raise CamValidationError("CAM job ID is invalid")
        normalized_name = _name(name, "CAM job")
        selected_revision = revision if revision is not None else Revision(0)
        if not isinstance(selected_revision, Revision):
            raise CamValidationError("CAM job revision is invalid")
        if not isinstance(setups, tuple) or any(
            not isinstance(setup, Setup) for setup in setups
        ):
            raise CamValidationError("CAM job setups must be an immutable tuple")
        setup_ids = tuple(setup.setup_id for setup in setups)
        if len(set(setup_ids)) != len(setup_ids):
            raise DuplicateCamIdError("Setup IDs must be unique in one CAM job")
        if active_setup_id is not None and active_setup_id not in setup_ids:
            raise CamInvariantError("Active setup must belong to the CAM job")
        self._job_id = job_id
        self._name = normalized_name
        self._revision = selected_revision
        self._setups = setups
        self._active_setup_id = active_setup_id

    @property
    def job_id(self) -> CamJobId:
        """Return this aggregate identity."""
        return self._job_id

    @property
    def name(self) -> str:
        """Return the job display name."""
        return self._name

    @property
    def revision(self) -> Revision:
        """Return the current aggregate revision."""
        return self._revision

    @property
    def setups(self) -> tuple[Setup, ...]:
        """Return immutable setup snapshots in deterministic order."""
        return self._setups

    @property
    def active_setup_id(self) -> SetupId | None:
        """Return the active setup identity, if any."""
        return self._active_setup_id

    @property
    def active_setup(self) -> Setup | None:
        """Return the active immutable setup snapshot."""
        if self._active_setup_id is None:
            return None
        return self.get_setup(self._active_setup_id)

    def get_setup(self, setup_id: SetupId) -> Setup:
        """Return one setup or raise a typed missing-child error."""
        return self._setups[self._setup_index(setup_id)]

    def rename(self, name: str) -> None:
        """Rename the job and increment revision only when state changes."""
        normalized = _name(name, "CAM job")
        if normalized == self._name:
            return
        self._name = normalized
        self._touch()

    def add_setup(self, setup: Setup) -> None:
        """Append a setup; the first setup becomes active."""
        if not isinstance(setup, Setup):
            raise CamValidationError("Setup is invalid")
        if any(item.setup_id == setup.setup_id for item in self._setups):
            raise DuplicateCamIdError(f"Duplicate setup ID: {setup.setup_id}")
        active = self._active_setup_id or setup.setup_id
        self._commit_setups((*self._setups, setup), active)

    def remove_setup(self, setup_id: SetupId) -> None:
        """Remove a setup; active falls back to the first remaining setup."""
        index = self._setup_index(setup_id)
        setups = self._setups[:index] + self._setups[index + 1 :]
        active = self._active_setup_id
        if active == setup_id:
            active = setups[0].setup_id if setups else None
        self._commit_setups(setups, active)

    def rename_setup(self, setup_id: SetupId, name: str) -> None:
        """Replace a setup with a validated renamed snapshot."""
        current = self.get_setup(setup_id)
        self._replace_setup(current.with_name(name))

    def replace_setup(self, setup: Setup) -> None:
        """Replace one complete setup through the aggregate boundary."""
        if not isinstance(setup, Setup):
            raise CamValidationError("Setup is invalid")
        self._replace_setup(setup)

    def set_active_setup(self, setup_id: SetupId | None) -> None:
        """Select an existing setup or explicitly clear selection."""
        if setup_id is not None:
            self._setup_index(setup_id)
        if setup_id == self._active_setup_id:
            return
        self._active_setup_id = setup_id
        self._touch()

    def update_wcs(self, setup_id: SetupId, wcs: WcsFrame) -> None:
        """Replace one setup WCS after full setup validation."""
        self._replace_setup(self.get_setup(setup_id).with_wcs(wcs))

    def update_work_offset(self, setup_id: SetupId, offset: WorkOffset) -> None:
        """Replace one controller-neutral work offset."""
        if not isinstance(offset, WorkOffset):
            raise CamValidationError("Work offset is invalid")
        self._replace_setup_with(setup_id, work_offset=offset)

    def set_stock(self, setup_id: SetupId, stock: StockDefinition) -> None:
        """Replace one setup stock after source-scope validation."""
        self._replace_setup(self.get_setup(setup_id).with_stock(stock))

    def add_fixture(self, setup_id: SetupId, fixture: FixtureInstance) -> None:
        """Append a fixture instance to one setup."""
        self._replace_setup(self.get_setup(setup_id).with_fixture_added(fixture))

    def update_fixture(self, setup_id: SetupId, fixture: FixtureInstance) -> None:
        """Replace an existing fixture while preserving its position."""
        self._replace_setup(self.get_setup(setup_id).with_fixture_updated(fixture))

    def remove_fixture(
        self,
        setup_id: SetupId,
        fixture_id: FixtureInstanceId,
    ) -> None:
        """Remove one fixture instance from one setup."""
        self._replace_setup(
            self.get_setup(setup_id).with_fixture_removed(fixture_id)
        )

    def update_operation_tree(
        self,
        setup_id: SetupId,
        operation_tree: OperationTree,
    ) -> None:
        """Atomically replace one setup tree and touch setup/job revisions once."""
        self._replace_setup(
            self.get_setup(setup_id).with_operation_tree(operation_tree)
        )

    def reorder_setup(self, setup_id: SetupId, new_index: int) -> None:
        """Move one setup to a deterministic zero-based position."""
        old_index = self._setup_index(setup_id)
        if type(new_index) is not int or not 0 <= new_index < len(self._setups):
            raise CamValidationError("Setup position is out of range")
        if old_index == new_index:
            return
        setups = list(self._setups)
        setup = setups.pop(old_index)
        setups.insert(new_index, setup)
        self._commit_setups(tuple(setups), self._active_setup_id)

    def _replace_setup_with(self, setup_id: SetupId, **changes: object) -> None:
        from dataclasses import replace

        self._replace_setup(replace(self.get_setup(setup_id), **changes))

    def _replace_setup(self, setup: Setup) -> None:
        index = self._setup_index(setup.setup_id)
        if setup == self._setups[index]:
            return
        setups = list(self._setups)
        setups[index] = setup
        self._commit_setups(tuple(setups), self._active_setup_id)

    def _setup_index(self, setup_id: SetupId) -> int:
        if not isinstance(setup_id, SetupId):
            raise CamValidationError("Setup ID is invalid")
        for index, setup in enumerate(self._setups):
            if setup.setup_id == setup_id:
                return index
        raise CamChildNotFoundError(f"Setup does not exist: {setup_id}")

    def _commit_setups(
        self,
        setups: tuple[Setup, ...],
        active_setup_id: SetupId | None,
    ) -> None:
        validated = CamJob(
            self._job_id,
            self._name,
            revision=self._revision,
            setups=setups,
            active_setup_id=active_setup_id,
        )
        self._setups = validated._setups
        self._active_setup_id = validated._active_setup_id
        self._touch()

    def _touch(self) -> None:
        self._revision = self._revision.next()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full aggregate while preserving setup order."""
        return {
            "format": _CAM_JOB_FORMAT,
            "format_version": _CAM_JOB_VERSION,
            "job_id": str(self._job_id),
            "name": self._name,
            "revision": self._revision.to_dict(),
            "setups": [setup.to_dict() for setup in self._setups],
            "active_setup_id": (
                str(self._active_setup_id)
                if self._active_setup_id is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CamJob":
        """Deserialize atomically into a complete validated aggregate."""
        _strict_payload(
            data,
            format_name=_CAM_JOB_FORMAT,
            version=_CAM_JOB_VERSION,
            fields={"job_id", "name", "revision", "setups", "active_setup_id"},
        )
        setups = data["setups"]
        if not isinstance(setups, list):
            raise CamValidationError("CAM job setups payload must be a list")
        active = data["active_setup_id"]
        return cls(
            CamJobId.parse(data["job_id"]),
            data["name"],
            revision=Revision.from_dict(data["revision"]),
            setups=tuple(Setup.from_dict(item) for item in setups),
            active_setup_id=SetupId.parse(active) if active is not None else None,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CamJob):
            return NotImplemented
        return self.to_dict() == other.to_dict()
