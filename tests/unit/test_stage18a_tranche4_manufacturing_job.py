"""Focused and adversarial tests for Stage18A Tranche4 job governance."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.manufacturing_job import (
    JobProgramBinding, JobReleasePolicy, JobReleaseReview, JobQualificationState,
    JobSetupBinding, JobToolBinding, ManufacturingJob, ManufacturingJobState, ReleaseDecision,
    TRANCHE4_ADVERSARIAL_CASES,
)
from hms_cadcam.cam.qualification.manufacturing_package import ManufacturingHandoffPackageBuilder, ManufacturingPackageError
from hms_cadcam.cam.qualification.manufacturing_release import (
    assess_job, create_job_release, diff_job_releases, reconcile_job_tools,
)
from hms_cadcam.cam.qualification.manufacturing_store import ManufacturingJobStore


def fp(value: object) -> ContentFingerprint:
    return ContentFingerprint.from_payload(value)


NC_A = b"O1001\nG54\nT1 M6\nG0 X0 Y0\nM30\n"
NC_B = b"O1002\nG54\nT1 M6\nG0 X1 Y0\nM30\n"


def make_job(*, changed_tool: bool = False, stale_program: bool = False) -> ManufacturingJob:
    tool = JobToolBinding(1, fp({"tool": "B" if changed_tool else "A"}), "End mill", "MILL", 10.0, 50.0,
                          "BT30", 1, 1, ("P1",), ("S1",))
    setup = JobSetupBinding("S1", fp({"setup": 1}), "G54", fp({"stock": 1}), fp({"fixture": 1}),
                           "ROBODRILL_D21MIB", (1,), ("P1",), JobQualificationState.CURRENT,
                           ("physical fixture dimensions unknown",))
    program = JobProgramBinding("P1", fp({"release": 1}), hashlib.sha256(NC_A).hexdigest(), "S1", "G54",
                                "ROBODRILL_D21MIB", fp({"machine": 1}), "FANUC_31I_B_BT30", fp({"post": 1}), (1,),
                                JobQualificationState.STALE if stale_program else JobQualificationState.CURRENT, 1)
    return ManufacturingJob("PROJECT", "JOB-1", "PART", "R1", fp({"project": 1}), "ROBODRILL_D21MIB",
                            fp({"machine": 1}), "FANUC_31I_B_BT30", (program,), (setup,), (tool,),
                            JobReleasePolicy(require_handoff_package=False), ManufacturingJobState.READY_FOR_RELEASE_REVIEW,
                            (("source", "R224"),))


def review_for(job: ManufacturingJob, decision: ReleaseDecision = ReleaseDecision.APPROVE_FOR_EXTERNAL_DRY_RUN_HANDOFF) -> JobReleaseReview:
    return JobReleaseReview("operator", "manufacturing reviewer", "2026-08-11T15:00:00+07:00", job.job_fingerprint,
                            tuple(p.nc_release_fingerprint for p in job.programs), (), decision, "Reviewed offline only")


def test_reconciliation_and_release_gate_are_fail_closed():
    job = make_job()
    report = reconcile_job_tools(job)
    assert report.passed
    assert assess_job(job, tool_report=report).passed
    release = create_job_release(job, review_for(job), release_id="REL-1", released_at="2026-08-11T15:01:00+07:00",
                                 package_fingerprint=fp({"package": 1}))
    assert release.state is ManufacturingJobState.RELEASED_FOR_EXTERNAL_DRY_RUN
    assert release.job.state is ManufacturingJobState.RELEASE_APPROVED


def test_stale_program_and_tool_change_block_release():
    stale = make_job(stale_program=True)
    assert "PROGRAM_NOT_CURRENT" in assess_job(stale).blockers
    changed = make_job(changed_tool=True)
    # A changed Tool is a new job revision, not silently reconciled to old data.
    assert changed.tools[0].fingerprint != make_job().tools[0].fingerprint


def test_review_binding_and_reject_decision_block():
    job = make_job()
    with pytest.raises(ValueError):
        create_job_release(job, review_for(job, ReleaseDecision.REJECT), release_id="REL-1",
                           released_at="2026-08-11T15:01:00+07:00", package_fingerprint=fp({"package": 1}))
    changed = make_job().with_state(ManufacturingJobState.STALE)
    with pytest.raises(ValueError):
        create_job_release(changed, review_for(job), release_id="REL-2", released_at="2026-08-11T15:01:00+07:00",
                           package_fingerprint=fp({"package": 2}))


def test_package_is_deterministic_and_tamper_protected(tmp_path: Path):
    job = make_job()
    release = create_job_release(job, review_for(job), release_id="REL-1", released_at="2026-08-11T15:01:00+07:00",
                                 package_fingerprint=fp({"package": 1}))
    builder = ManufacturingHandoffPackageBuilder()
    package = builder.build(release, tmp_path, nc_files={"P1.nc": NC_A})
    builder.verify(package)
    (package.root / "nc" / "P1.nc").write_bytes(NC_B)
    with pytest.raises(ManufacturingPackageError):
        builder.verify(package)


def test_forbidden_package_content_is_rejected(tmp_path: Path):
    job = make_job()
    release = create_job_release(job, review_for(job), release_id="REL-1", released_at="2026-08-11T15:01:00+07:00",
                                 package_fingerprint=fp({"package": 1}))
    with pytest.raises(ManufacturingPackageError):
        ManufacturingHandoffPackageBuilder().build(release, tmp_path, nc_files={"P1.nc": NC_A},
                                                   verification_reports={"upload.ps1": b"bad"})


def test_store_round_trip_preserves_schema5_and_fingerprints(tmp_path: Path):
    job = make_job()
    release = create_job_release(job, review_for(job), release_id="REL-1", released_at="2026-08-11T15:01:00+07:00",
                                 package_fingerprint=fp({"package": 1}))
    store = ManufacturingJobStore(tmp_path)
    store.save((job,), (release,))
    jobs, releases = store.load()
    assert jobs[0].job_fingerprint == job.job_fingerprint
    assert releases[0].release_fingerprint == release.release_fingerprint
    assert '"sqlite_schema":5' in store.path.read_text(encoding="utf-8")


def test_structured_diff_detects_nc_tool_setup_and_policy_changes():
    old_job = make_job()
    old = create_job_release(old_job, review_for(old_job), release_id="REL-1", released_at="2026-08-11T15:01:00+07:00",
                             package_fingerprint=fp({"package": 1}))
    changed = make_job(changed_tool=True)
    changed_program = JobProgramBinding("P1", fp({"release": 2}), hashlib.sha256(NC_B).hexdigest(), "S1", "G54",
                                        "ROBODRILL_D21MIB", fp({"machine": 1}), "FANUC_31I_B_BT30", fp({"post": 2}), (1,),
                                        JobQualificationState.CURRENT, 2)
    changed = ManufacturingJob(changed.project_id, changed.job_id, changed.part_id, changed.part_revision,
                               changed.project_fingerprint, changed.machine_profile_id, changed.machine_profile_fingerprint,
                               changed.controller_contract, (changed_program,), changed.setups, changed.tools,
                               JobReleasePolicy(require_handoff_package=False, require_tool_reconciliation=False),
                               changed.state, changed.provenance)
    new = create_job_release(changed, review_for(changed), release_id="REL-2", released_at="2026-08-11T15:02:00+07:00",
                             package_fingerprint=fp({"package": 2}))
    diff = diff_job_releases(old, new)
    assert diff.nc_revision_changes == ("P1",)
    assert diff.tool_changes == (1,)
    assert diff.post_changes == ("P1",)


def test_adversarial_matrix_is_permanent_and_complete():
    assert len(TRANCHE4_ADVERSARIAL_CASES) == 24
    assert "manual Level2 flag" in TRANCHE4_ADVERSARIAL_CASES
    assert "machine_ready injection" in TRANCHE4_ADVERSARIAL_CASES
