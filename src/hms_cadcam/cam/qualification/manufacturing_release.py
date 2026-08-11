"""Application services for offline Stage18A Tranche4 job release governance."""

from __future__ import annotations

from dataclasses import dataclass
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.manufacturing_job import (
    JobReleaseDiff,
    JobReleaseReview,
    JobQualificationState,
    JobToolReconciliationReport,
    ManufacturingJob,
    ManufacturingJobRelease,
    ManufacturingJobState,
    ToolReconciliationIssue,
)


@dataclass(frozen=True, slots=True)
class JobReleaseAssessment:
    state: ManufacturingJobState
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    tool_report: JobToolReconciliationReport

    @property
    def passed(self) -> bool:
        return self.state is ManufacturingJobState.READY_FOR_RELEASE_REVIEW and not self.blockers


def reconcile_job_tools(job: ManufacturingJob) -> JobToolReconciliationReport:
    """Reconcile all typed Tool bindings and fail closed on ambiguity."""

    issues: list[ToolReconciliationIssue] = []
    for tool in job.tools:
        if tool.state is not JobQualificationState.CURRENT:
            issues.append(ToolReconciliationIssue("STALE_TOOL", tool.tool_number,
                                                  f"Tool T{tool.tool_number} is {tool.state.value}", "BLOCKER"))
        if not tool.program_ids or not tool.setup_ids:
            issues.append(ToolReconciliationIssue("TOOL_USAGE_MISSING", tool.tool_number,
                                                  "Tool usage is not bound to a program and setup", "BLOCKER"))
    by_number: dict[int, list] = {}
    for tool in job.tools:
        by_number.setdefault(tool.tool_number, []).append(tool)
    # Canonical job state normally forbids duplicates.  This path still catches
    # a decoded/adversarial object before any release decision is made.
    for number, entries in sorted(by_number.items()):
        fingerprints = {entry.fingerprint for entry in entries}
        if len(fingerprints) > 1:
            issues.append(ToolReconciliationIssue("TOOL_NUMBER_CONFLICT", number,
                                                  "Same Tool number has different fingerprints", "BLOCKER"))
        hd = {(entry.h_offset, entry.d_offset) for entry in entries}
        if len(hd) > 1:
            issues.append(ToolReconciliationIssue("HD_CONFLICT", number,
                                                  "H/D namespace differs across bindings", "BLOCKER"))
        holders = {entry.holder for entry in entries}
        if len(holders) > 1:
            issues.append(ToolReconciliationIssue("HOLDER_CONFLICT", number,
                                                  "Holder differs across bindings", "BLOCKER"))
    tool_numbers = {tool.tool_number for tool in job.tools}
    for setup in job.setups:
        for number in setup.tool_numbers:
            if number not in tool_numbers:
                issues.append(ToolReconciliationIssue("TOOL_MISSING", number,
                                                      f"Tool T{number} is missing from the job master list", "BLOCKER"))
    return JobToolReconciliationReport(job.job_fingerprint, tuple(issues), len(job.tools))


def assess_job(job: ManufacturingJob, *, tool_report: JobToolReconciliationReport | None = None,
               handoff_package_ready: bool = False) -> JobReleaseAssessment:
    """Evaluate policy with explicit blockers and no implicit readiness flag."""

    report = tool_report or reconcile_job_tools(job)
    blockers: list[str] = []
    warnings: list[str] = []
    if job.state in {ManufacturingJobState.STALE, ManufacturingJobState.SUPERSEDED, ManufacturingJobState.INVALID}:
        blockers.append(f"JOB_{job.state.value}")
    if job.state in {ManufacturingJobState.DRAFT, ManufacturingJobState.IN_VERIFICATION}:
        blockers.append("JOB_NOT_READY_FOR_RELEASE_REVIEW")
    if job.state is ManufacturingJobState.RELEASED_FOR_EXTERNAL_DRY_RUN:
        blockers.append("JOB_ALREADY_RELEASED")
    if not job.programs:
        blockers.append("NO_PROGRAMS")
    if not job.setups:
        blockers.append("NO_SETUPS")
    if any(program.machine_profile_id != job.machine_profile_id for program in job.programs):
        blockers.append("MIXED_MACHINE_PROFILE")
    if any(program.controller_contract != job.controller_contract for program in job.programs):
        blockers.append("MIXED_CONTROLLER_CONTRACT")
    if len({program.post_fingerprint for program in job.programs}) != 1:
        blockers.append("MISMATCHED_POST")
    if len({program.nc_release_fingerprint for program in job.programs}) != len(job.programs):
        blockers.append("DUPLICATE_NC_RELEASE")
    if any(program.qualification_state is not JobQualificationState.CURRENT for program in job.programs):
        if job.release_policy.require_current_programs:
            blockers.append("PROGRAM_NOT_CURRENT")
        else:
            warnings.append("PROGRAM_NOT_CURRENT")
    if any(setup.qualification_state is not JobQualificationState.CURRENT for setup in job.setups):
        if job.release_policy.require_setup_readiness:
            blockers.append("SETUP_NOT_CURRENT")
        else:
            warnings.append("SETUP_NOT_CURRENT")
    if job.release_policy.require_tool_reconciliation and not report.passed:
        blockers.append("TOOL_RECONCILIATION_FAILED")
    if job.release_policy.require_handoff_package and not handoff_package_ready:
        blockers.append("HANDOFF_PACKAGE_NOT_READY")
    # These boundaries are permanent and cannot be overridden through policy.
    if any(program.g54_identity != "G54" for program in job.programs):
        blockers.append("UNSUPPORTED_WORK_OFFSET")
    forbidden = {"TAPPING", "G84", "UNQUALIFIED_CANNED_CYCLE"}
    if any(forbidden & set(program.qualification_blockers) for program in job.programs):
        blockers.append("UNQUALIFIED_PROGRAM_SEMANTICS")
    state = ManufacturingJobState.BLOCKED if blockers else ManufacturingJobState.READY_FOR_RELEASE_REVIEW
    return JobReleaseAssessment(state, tuple(sorted(set(blockers))), tuple(sorted(set(warnings))), report)


def create_job_release(job: ManufacturingJob, review: JobReleaseReview, *, release_id: str,
                       released_at: str, package_fingerprint: ContentFingerprint,
                       handoff_package_ready: bool = True,
                       supersedes_release_id: str | None = None) -> ManufacturingJobRelease:
    """Create an immutable release only after policy and review binding pass."""

    report = reconcile_job_tools(job)
    assessment = assess_job(job, tool_report=report, handoff_package_ready=handoff_package_ready)
    if not assessment.passed:
        raise ValueError(f"Manufacturing job is not releasable: {', '.join(assessment.blockers)}")
    if job.state not in {ManufacturingJobState.READY_FOR_RELEASE_REVIEW, ManufacturingJobState.RELEASE_APPROVED}:
        raise ValueError("Job state does not permit release")
    if review.job_fingerprint != job.job_fingerprint:
        raise ValueError("Release review is stale for the current job")
    if not review.program_release_fingerprints or any(
        program.nc_release_fingerprint not in review.program_release_fingerprints for program in job.programs
    ):
        raise ValueError("Release review does not cover every program release")
    if review.decision.value != "APPROVE_FOR_EXTERNAL_DRY_RUN_HANDOFF":
        raise ValueError("Release review did not approve external dry-run handoff")
    return ManufacturingJobRelease(release_id, job.with_state(ManufacturingJobState.RELEASE_APPROVED),
                                   report, review, released_at, package_fingerprint,
                                   supersedes_release_id=supersedes_release_id)


def supersede_release(previous: ManufacturingJobRelease, replacement: ManufacturingJobRelease,
                      *, reason: str, superseded_at: str) -> tuple[ManufacturingJobRelease, ManufacturingJobRelease]:
    if replacement.supersedes_release_id != previous.release_id:
        raise ValueError("Replacement release does not identify the previous release")
    if not reason.strip():
        raise ValueError("Supersede reason is required")
    stale_previous = ManufacturingJobRelease(
        previous.release_id,
        previous.job.with_state(ManufacturingJobState.SUPERSEDED),
        previous.tool_report,
        previous.review,
        previous.released_at,
        previous.package_fingerprint,
        ManufacturingJobState.SUPERSEDED,
        supersedes_release_id=previous.supersedes_release_id,
        superseded_by_release_id=replacement.release_id,
        superseded_reason=reason,
        superseded_at=superseded_at,
    )
    return stale_previous, replacement


def diff_job_releases(old: ManufacturingJobRelease, new: ManufacturingJobRelease) -> JobReleaseDiff:
    old_programs = {p.program_id: p for p in old.job.programs}
    new_programs = {p.program_id: p for p in new.job.programs}
    common = old_programs.keys() & new_programs.keys()
    nc_revision_changes = tuple(sorted(pid for pid in common if (
        old_programs[pid].nc_sha256 != new_programs[pid].nc_sha256
        or old_programs[pid].release_revision != new_programs[pid].release_revision)))
    post_changes = tuple(sorted(pid for pid in common if old_programs[pid].post_fingerprint != new_programs[pid].post_fingerprint))
    old_setups = {s.setup_id: s for s in old.job.setups}
    new_setups = {s.setup_id: s for s in new.job.setups}
    unchanged_setups = {
        sid for sid in old_setups.keys() & new_setups.keys()
        if old_setups[sid].fingerprint == new_setups[sid].fingerprint
    }
    setup_changes = tuple(sorted((set(old_setups) | set(new_setups)) - unchanged_setups))
    old_tools = {t.tool_number: t for t in old.job.tools}
    new_tools = {t.tool_number: t for t in new.job.tools}
    tool_changes = tuple(sorted(number for number in set(old_tools) | set(new_tools) if (
        number not in old_tools or number not in new_tools or old_tools[number].fingerprint != new_tools[number].fingerprint
        or old_tools[number].holder != new_tools[number].holder
        or old_tools[number].h_offset != new_tools[number].h_offset
        or old_tools[number].d_offset != new_tools[number].d_offset
    )))
    return JobReleaseDiff(
        tuple(sorted(new_programs.keys() - old_programs.keys())),
        tuple(sorted(old_programs.keys() - new_programs.keys())),
        nc_revision_changes,
        setup_changes,
        tool_changes,
        old.job.machine_profile_fingerprint != new.job.machine_profile_fingerprint,
        post_changes,
        tuple(sorted(set(old.review.acknowledged_findings) ^ set(new.review.acknowledged_findings))),
        old.job.release_policy != new.job.release_policy,
    )


__all__ = ["JobReleaseAssessment", "assess_job", "create_job_release", "diff_job_releases",
           "reconcile_job_tools", "supersede_release"]
