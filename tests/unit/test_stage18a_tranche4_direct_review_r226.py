"""Independent R226 direct-review probes for Tranche4 release governance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.manufacturing_job import (
    JobProgramBinding, JobReleasePolicy, JobReleaseReview, JobQualificationState,
    JobSetupBinding, JobToolBinding, ManufacturingJob, ManufacturingJobState, ReleaseDecision,
)
from hms_cadcam.cam.qualification.manufacturing_package import ManufacturingHandoffPackageBuilder, ManufacturingPackageError
from hms_cadcam.cam.qualification.manufacturing_release import assess_job, create_job_release, supersede_release
from hms_cadcam.cam.qualification.manufacturing_store import ManufacturingJobStore, ManufacturingStoreError


NC = b"O1001\nG54\nT1 M6\nG0 X0 Y0\nM30\n"


def _fp(value: object) -> ContentFingerprint:
    return ContentFingerprint.from_payload(value)


def _job(*, state: ManufacturingJobState = ManufacturingJobState.READY_FOR_RELEASE_REVIEW,
         post: int = 1, controller: str = "FANUC_31I_B_BT30", blockers: tuple[str, ...] = ()) -> ManufacturingJob:
    tool = JobToolBinding(1, _fp({"tool": 1}), "End mill", "MILL", 10.0, 50.0, "BT30", 1, 1, ("P1",), ("S1",))
    setup = JobSetupBinding("S1", _fp({"setup": 1}), "G54", _fp({"stock": 1}), _fp({"fixture": 1}),
                            "ROBODRILL_D21MIB", (1,), ("P1",), JobQualificationState.CURRENT)
    program = JobProgramBinding("P1", _fp({"release": 1}), hashlib.sha256(NC).hexdigest(), "S1", "G54",
                                "ROBODRILL_D21MIB", _fp({"machine": 1}), controller, _fp({"post": post}), (1,),
                                JobQualificationState.CURRENT, 1, blockers)
    return ManufacturingJob("PROJECT", "JOB", "PART", "R1", _fp({"project": 1}), "ROBODRILL_D21MIB",
                            _fp({"machine": 1}), controller, (program,), (setup,), (tool,),
                            JobReleasePolicy(require_handoff_package=False), state)


def _review(job: ManufacturingJob) -> JobReleaseReview:
    return JobReleaseReview("reviewer", "release authority", "2026-08-11T17:00:00+07:00", job.job_fingerprint,
                            tuple(item.nc_release_fingerprint for item in job.programs), (),
                            ReleaseDecision.APPROVE_FOR_EXTERNAL_DRY_RUN_HANDOFF, "Offline review only")


def _release(job: ManufacturingJob, release_id: str = "REL-1"):
    return create_job_release(job, _review(job), release_id=release_id,
                              released_at="2026-08-11T17:01:00+07:00", package_fingerprint=_fp({"package": release_id}))


def test_draft_and_direct_state_bypass_cannot_release():
    draft = _job(state=ManufacturingJobState.DRAFT)
    assert "JOB_NOT_READY_FOR_RELEASE_REVIEW" in assess_job(draft).blockers
    with pytest.raises(ValueError, match="not releasable"):
        _release(draft)
    injected = replace(draft, state=ManufacturingJobState.RELEASED_FOR_EXTERNAL_DRY_RUN, job_fingerprint=None)
    with pytest.raises(ValueError, match="not releasable"):
        _release(injected)


def test_program_controller_post_duplicate_and_canned_cycle_boundaries_are_fail_closed():
    job = _job(blockers=("G84",))
    assert "UNQUALIFIED_PROGRAM_SEMANTICS" in assess_job(job).blockers
    with pytest.raises(CamInvariantError, match="controller"):
        ManufacturingJob(job.project_id, job.job_id, job.part_id, job.part_revision, job.project_fingerprint,
                         job.machine_profile_id, job.machine_profile_fingerprint, "OTHER", job.programs,
                         job.setups, job.tools, job.release_policy, job.state)
    duplicate_program = replace(job.programs[0], program_id="P2")
    with pytest.raises(CamInvariantError, match="NC release"):
        ManufacturingJob(job.project_id, job.job_id, job.part_id, job.part_revision, job.project_fingerprint,
                         job.machine_profile_id, job.machine_profile_fingerprint, job.controller_contract,
                         (job.programs[0], duplicate_program), job.setups, job.tools, job.release_policy, job.state)


def test_malformed_deserialization_and_old_review_fail_closed():
    job = _job()
    payload = job.to_dict()
    payload.pop("part_id")
    with pytest.raises(KeyError):
        ManufacturingJob.from_dict(payload)
    stale = _review(_job())
    changed = ManufacturingJob(job.project_id, job.job_id, job.part_id, "R2", job.project_fingerprint,
                               job.machine_profile_id, job.machine_profile_fingerprint, job.controller_contract,
                               job.programs, job.setups, job.tools, job.release_policy, job.state)
    with pytest.raises(ValueError, match="stale"):
        create_job_release(changed, stale, release_id="REL-X", released_at="2026-08-11T17:01:00+07:00",
                           package_fingerprint=_fp({"package": "X"}))


def test_supersede_history_is_immutable_and_store_rejects_duplicate_release_ids(tmp_path: Path):
    previous = _release(_job(), "REL-1")
    replacement_job = ManufacturingJob(_job().project_id, "JOB", "PART", "R2", _fp({"project": 2}),
                                        "ROBODRILL_D21MIB", _fp({"machine": 1}), "FANUC_31I_B_BT30",
                                        _job().programs, _job().setups, _job().tools,
                                        JobReleasePolicy(require_handoff_package=False), ManufacturingJobState.READY_FOR_RELEASE_REVIEW)
    replacement = _release(replacement_job, "REL-2")
    replacement = replace(replacement, supersedes_release_id="REL-1", release_fingerprint=None)
    old, current = supersede_release(previous, replacement, reason="part revision changed", superseded_at="2026-08-11T17:02:00+07:00")
    assert old.state is ManufacturingJobState.SUPERSEDED
    assert old.superseded_by_release_id == current.release_id
    assert old.superseded_reason == "part revision changed"
    store = ManufacturingJobStore(tmp_path)
    with pytest.raises(ManufacturingStoreError, match="Duplicate"):
        store.save((previous.job,), (previous, previous))


def test_handoff_package_checks_nc_sha_manifest_and_expected_inventory(tmp_path: Path):
    release = _release(_job())
    builder = ManufacturingHandoffPackageBuilder()
    package = builder.build(release, tmp_path, nc_files={"P1.nc": NC})
    builder.verify(package)
    expected = {
        "machine/machine_summary.json", "setup/setup_pack.json", "tools/master_tool_list.json",
        "release/review_record.json", "history/revision_history.json", "nc/P1.nc.sha256",
    }
    assert expected.issubset({item["path"] for item in package.manifest["files"]})
    manifest_path = package.root / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["credential_findings"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManufacturingPackageError, match="boundary/security"):
        builder.verify(package)
    with pytest.raises(ManufacturingPackageError, match="released SHA"):
        builder.build(release, tmp_path / "wrong-bytes", nc_files={"P1.nc": b"same name, changed bytes"})


def test_tranche4_source_has_no_cnc_or_network_control_imports():
    root = Path(__file__).parents[2] / "src" / "hms_cadcam"
    inspected = tuple((root / "cam" / "qualification").glob("manufacturing*.py")) + (root / "ui" / "manufacturing_release_center.py",)
    forbidden_imports = ("import socket", "from socket", "import serial", "import ftplib", "import subprocess", "import ctypes")
    text = "\n".join(path.read_text(encoding="utf-8") for path in inspected)
    assert not any(token in text for token in forbidden_imports)
    assert "FOCAS" not in text
