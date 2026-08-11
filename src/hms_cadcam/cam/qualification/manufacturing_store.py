"""Additive JSON persistence for Stage18A Tranche4 job/release history."""

from __future__ import annotations

import json
from pathlib import Path

from hms_cadcam.cam.qualification.manufacturing_job import ManufacturingJob, ManufacturingJobRelease

STORE_FORMAT_VERSION = 1
SQLITE_SCHEMA_VERSION = 5


class ManufacturingStoreError(RuntimeError):
    """Raised when the additive Tranche4 store is corrupt or unavailable."""


class ManufacturingJobStore:
    """Persist jobs and immutable release records under project qualification data."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / "post" / "qualification" / "tranche4"
        self.path = self.root / "manufacturing_jobs.json"

    def save(self, jobs: tuple[ManufacturingJob, ...], releases: tuple[ManufacturingJobRelease, ...]) -> None:
        if len({job.job_id for job in jobs}) != len(jobs):
            raise ManufacturingStoreError("Duplicate manufacturing job ID")
        if len({release.release_id for release in releases}) != len(releases):
            raise ManufacturingStoreError("Duplicate manufacturing release ID")
        release_ids = {release.release_id for release in releases}
        for release in releases:
            if release.supersedes_release_id is not None and release.supersedes_release_id not in release_ids:
                raise ManufacturingStoreError("Superseded release reference is detached")
        payload = {"format": "HMS_STAGE18A_MANUFACTURING_JOB_STORE", "format_version": STORE_FORMAT_VERSION,
                   "sqlite_schema": SQLITE_SCHEMA_VERSION, "jobs": [job.to_dict() for job in jobs],
                   "releases": [release.to_dict() for release in releases]}
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as error:
            raise ManufacturingStoreError("Unable to persist Tranche4 job store") from error

    def load(self) -> tuple[tuple[ManufacturingJob, ...], tuple[ManufacturingJobRelease, ...]]:
        if not self.path.exists():
            return (), ()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManufacturingStoreError("Tranche4 job store is unreadable") from error
        if (data.get("format") != "HMS_STAGE18A_MANUFACTURING_JOB_STORE"
                or data.get("format_version") != STORE_FORMAT_VERSION
                or data.get("sqlite_schema") != SQLITE_SCHEMA_VERSION
                or not isinstance(data.get("jobs"), list) or not isinstance(data.get("releases"), list)):
            raise ManufacturingStoreError("Tranche4 job store schema is unsupported")
        try:
            jobs = tuple(ManufacturingJob.from_dict(item) for item in data["jobs"])
            releases = tuple(ManufacturingJobRelease.from_dict(item) for item in data["releases"])
        except (KeyError, TypeError, ValueError) as error:
            raise ManufacturingStoreError("Tranche4 job store contains invalid records") from error
        return jobs, releases


__all__ = ["ManufacturingJobStore", "ManufacturingStoreError", "SQLITE_SCHEMA_VERSION", "STORE_FORMAT_VERSION"]
