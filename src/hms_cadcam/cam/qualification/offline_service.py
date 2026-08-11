"""Application services for offline NC verification and controlled handoff."""

from __future__ import annotations

from dataclasses import dataclass
import difflib

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.model import (
    AuthorityClass,
    MachineQualificationContract,
    QualificationLevel,
    QualificationReport,
    sha256_bytes,
)
from hms_cadcam.cam.qualification.offline_analyzer import (
    AnalysisPolicy,
    NCAnalysisResult,
    analyze_nc_bytes,
)
from hms_cadcam.cam.qualification.offline_model import (
    MotionClass,
    NCReleaseCandidate,
    OfflineFindingSeverity,
    OfflineNCVerificationSession,
    OperatorAcknowledgement,
    OperatorReview,
    OperatorReviewResult,
    ReleaseAssessment,
    ReleaseComparison,
    ReleaseState,
    StaticSafetyFinding,
    VerificationSessionState,
)
from hms_cadcam.cam.qualification.physical_model import (
    ClearanceState,
    MachineSetupQualification,
    PhysicalReadinessResult,
    PhysicalTravelState,
    PlacementState,
)


@dataclass(frozen=True, slots=True)
class CurrentReleaseSources:
    nc_sha256: str
    machine_profile_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    tool_set_fingerprint: ContentFingerprint
    post_fingerprint: ContentFingerprint
    qualification_contract_version: int


@dataclass(frozen=True, slots=True)
class NCLineDiff:
    old_line_number: int | None
    new_line_number: int | None
    change: str
    category: str
    old_text: str | None
    new_text: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "old_line_number": self.old_line_number, "new_line_number": self.new_line_number,
            "change": self.change, "category": self.category,
            "old_text": self.old_text, "new_text": self.new_text,
        }


def _contract_number(contract: MachineQualificationContract, key: str) -> float:
    value = contract.leaf(key).value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Machine contract {key} is not numeric")
    return float(value)


def _context_finding(code: str, message: str) -> StaticSafetyFinding:
    return StaticSafetyFinding(
        f"{code}:0:1", code, OfflineFindingSeverity.BLOCKER, message, None,
        "OfflineNCVerificationService", "current frozen source bindings",
        "Create a new verification session from the current exact sources.",
        "HANDOFF_BLOCKED",
    )


class OfflineNCVerificationService:
    """Build immutable verification sessions without any CNC I/O surface."""

    def analyze(
        self,
        nc_bytes: bytes,
        *,
        contract: MachineQualificationContract,
        setup: MachineSetupQualification,
        physical_readiness: PhysicalReadinessResult,
    ) -> NCAnalysisResult:
        tool_numbers = tuple(item.tool_number for item in setup.tools)
        policy = AnalysisPolicy(
            maximum_spindle_rpm=_contract_number(contract, "spindle.maximum_rpm"),
            maximum_feed_mm_min=_contract_number(contract, "spindle.feed_envelope"),
            expected_tool_numbers=tool_numbers,
            # Static coordinate checks remain distinct from controller/physical
            # endpoint evidence.  Owner-confirmed setup facts may prepare a
            # handoff but cannot suppress the physical rapid-clearance warning.
            physical_travel_verified=(
                physical_readiness.travel_state
                is PhysicalTravelState.PHYSICAL_TRAVEL_STATICALLY_VALIDATED
                and setup.authority is AuthorityClass.PHYSICAL_TEST_CONFIRMED
            ),
            fixture_placement_verified=(
                PlacementState.FIXTURE_PLACEMENT_UNVERIFIED
                not in physical_readiness.placement_states
                and setup.fixture is not None
                and setup.fixture.authority is AuthorityClass.PHYSICAL_TEST_CONFIRMED
            ),
            holder_clearance_verified=(
                physical_readiness.clearance_state
                is ClearanceState.HOLDER_FIXTURE_CLEARANCE_PHYSICALLY_CONFIRMED
            ),
            collision_evidence_current=setup.clearance_evidence is not None,
            level2_evidence_current=False,
            owner_sample_available=False,
        )
        return analyze_nc_bytes(nc_bytes, policy)

    def create_session(
        self,
        *,
        session_id: str,
        project_fingerprint: ContentFingerprint,
        program_fingerprint: ContentFingerprint,
        nc_artifact_id: str,
        nc_bytes: bytes,
        contract: MachineQualificationContract,
        setup: MachineSetupQualification,
        level1_report: QualificationReport,
        physical_readiness: PhysicalReadinessResult,
        finalized_at: str,
    ) -> OfflineNCVerificationSession:
        analysis = self.analyze(
            nc_bytes, contract=contract, setup=setup, physical_readiness=physical_readiness
        )
        findings = list(analysis.findings)
        digest = sha256_bytes(nc_bytes)
        if contract.profile_id != setup.machine_profile_id:
            findings.append(_context_finding("WRONG_MACHINE_PROFILE", "Setup targets another machine profile."))
        if contract.fingerprint != setup.machine_profile_fingerprint:
            findings.append(_context_finding("STALE_MACHINE_FINGERPRINT", "Machine profile fingerprint is stale."))
        if digest != setup.nc_sha256 or digest != level1_report.nc_sha256:
            findings.append(_context_finding("NC_HASH_MISMATCH", "NC bytes do not match setup and Level1 identity."))
        if setup.post_fingerprint != level1_report.post_profile_fingerprint:
            findings.append(_context_finding("STALE_POST_FINGERPRINT", "Post fingerprint is stale."))
        if setup.tool_set_fingerprint.digest == "":
            findings.append(_context_finding("STALE_TOOL_FINGERPRINT", "Tool set fingerprint is unavailable."))
        state = (
            VerificationSessionState.PRECHECK_FAILED
            if any(item.severity is OfflineFindingSeverity.BLOCKER for item in findings)
            else VerificationSessionState.READY_FOR_OPERATOR_REVIEW
        )
        return OfflineNCVerificationSession(
            session_id, project_fingerprint, program_fingerprint, nc_artifact_id, digest,
            contract.profile_id, contract.fingerprint, "FANUC 31i-B",
            setup.post_fingerprint, setup.fingerprint, setup.work_offset_transform.work_offset,
            setup.tool_set_fingerprint, contract.contract_revision, state, analysis.blocks,
            tuple(sorted(findings, key=lambda item: (item.block_line or 0, item.code, item.finding_id))),
            finalized_at,
        )

    def create_release_candidate(
        self,
        session: OfflineNCVerificationSession,
        level1_report: QualificationReport,
        *,
        release_revision: int,
        created_at: str,
    ) -> NCReleaseCandidate:
        if session.state not in {
            VerificationSessionState.READY_FOR_OPERATOR_REVIEW,
            VerificationSessionState.PRECHECK_FAILED,
        }:
            raise ValueError("Verification session is not finalized for release review")
        if level1_report.nc_sha256 != session.nc_sha256:
            raise ValueError("Level1 report is stale for the verification session")
        return NCReleaseCandidate(
            release_revision, session.nc_sha256, session.session_fingerprint,
            session.setup_fingerprint, session.tool_set_fingerprint,
            session.machine_profile_fingerprint, session.post_fingerprint,
            level1_report.report_fingerprint, level1_report.qualification_level.name,
            created_at,
        )

    def assess_release(
        self,
        *,
        session: OfflineNCVerificationSession,
        candidate: NCReleaseCandidate,
        level1_report: QualificationReport,
        physical_readiness: PhysicalReadinessResult,
        review: OperatorReview | None,
        acknowledgement: OperatorAcknowledgement | None,
        current: CurrentReleaseSources,
        operator_review_required: bool = True,
    ) -> ReleaseAssessment:
        blockers = {
            item.code for item in session.findings
            if item.severity is OfflineFindingSeverity.BLOCKER
        }
        warnings = {
            item.code for item in session.findings
            if item.severity is OfflineFindingSeverity.WARNING
        }
        if session.state in {VerificationSessionState.STALE, VerificationSessionState.INVALID}:
            blockers.add("VERIFICATION_SESSION_NOT_CURRENT")
        if level1_report.qualification_level is not QualificationLevel.STATICALLY_VALIDATED:
            blockers.add("LEVEL1_STATIC_QUALIFICATION_INVALID")
        if level1_report.has_errors:
            blockers.add("LEVEL1_STATIC_FINDINGS_BLOCKING")
        if not physical_readiness.ready_for_external_evidence:
            blockers.add("TRANCHE2_SETUP_READINESS_INCOMPLETE")
        comparisons = (
            (current.nc_sha256, candidate.nc_sha256, "NC_HASH_MISMATCH"),
            (current.machine_profile_fingerprint, candidate.machine_profile_fingerprint, "STALE_MACHINE_FINGERPRINT"),
            (current.setup_fingerprint, candidate.setup_fingerprint, "STALE_SETUP"),
            (current.tool_set_fingerprint, candidate.tool_set_fingerprint, "STALE_TOOL_FINGERPRINT"),
            (current.post_fingerprint, candidate.post_fingerprint, "STALE_POST_FINGERPRINT"),
        )
        for actual, frozen, code in comparisons:
            if actual != frozen:
                blockers.add(code)
        if current.qualification_contract_version != session.qualification_contract_version:
            blockers.add("STALE_QUALIFICATION_CONTRACT")
        if operator_review_required:
            if review is None:
                blockers.add("OPERATOR_REVIEW_MISSING")
            elif review.release_candidate_fingerprint != candidate.candidate_fingerprint:
                blockers.add("OPERATOR_REVIEW_STALE")
            elif review.result is not OperatorReviewResult.ACCEPT_FOR_EXTERNAL_DRY_RUN:
                blockers.add("OPERATOR_REVIEW_REJECTED")
        if acknowledgement is None:
            blockers.add("OPERATOR_ACKNOWLEDGEMENT_MISSING")
        elif acknowledgement.release_candidate_fingerprint != candidate.candidate_fingerprint:
            blockers.add("OPERATOR_ACKNOWLEDGEMENT_STALE")
        state = ReleaseState.BLOCKED if blockers else ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF
        return ReleaseAssessment(state, tuple(sorted(blockers)), tuple(sorted(warnings)))


def current_sources(
    nc_bytes: bytes,
    setup: MachineSetupQualification,
    contract: MachineQualificationContract,
) -> CurrentReleaseSources:
    return CurrentReleaseSources(
        sha256_bytes(nc_bytes), contract.fingerprint, setup.fingerprint,
        setup.tool_set_fingerprint, setup.post_fingerprint, contract.contract_revision,
    )


def compare_releases(
    old_candidate: NCReleaseCandidate,
    new_candidate: NCReleaseCandidate,
    old_session: OfflineNCVerificationSession,
    new_session: OfflineNCVerificationSession,
) -> ReleaseComparison:
    old_motion = tuple(
        (item.motion_class.value, item.normalized_tokens)
        for item in old_session.blocks
        if item.motion_class in {MotionClass.RAPID, MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}
    )
    new_motion = tuple(
        (item.motion_class.value, item.normalized_tokens)
        for item in new_session.blocks
        if item.motion_class in {MotionClass.RAPID, MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}
    )
    old_spindle_feed = tuple(
        (item.modal_after.spindle_rpm, item.modal_after.feed) for item in old_session.blocks
    )
    new_spindle_feed = tuple(
        (item.modal_after.spindle_rpm, item.modal_after.feed) for item in new_session.blocks
    )
    old_tools = tuple(
        item.modal_after.tool for item in old_session.blocks if item.motion_class is MotionClass.TOOL_CHANGE
    )
    new_tools = tuple(
        item.modal_after.tool for item in new_session.blocks if item.motion_class is MotionClass.TOOL_CHANGE
    )
    return ReleaseComparison(
        old_candidate.nc_sha256 != new_candidate.nc_sha256,
        old_candidate.machine_profile_fingerprint != new_candidate.machine_profile_fingerprint,
        old_candidate.setup_fingerprint != new_candidate.setup_fingerprint,
        old_candidate.tool_set_fingerprint != new_candidate.tool_set_fingerprint,
        old_candidate.post_fingerprint != new_candidate.post_fingerprint,
        len(old_session.blocks) != len(new_session.blocks), old_motion != new_motion,
        old_spindle_feed != new_spindle_feed, old_tools != new_tools,
        tuple((item.code, item.severity.value) for item in old_session.findings)
        != tuple((item.code, item.severity.value) for item in new_session.findings),
    )


def _diff_category(text: str) -> str:
    upper = text.upper()
    code = "".join(part for part in upper.split(";")[0].split())
    if not code or code.startswith("("):
        return "COMMENT_ONLY"
    if any(token in code for token in ("G00", "G01", "G02", "G03", "X", "Y", "Z")):
        return "MOTION"
    if "M06" in code or "T" in code:
        return "TOOL"
    if any(token in code for token in ("M03", "M04", "M05", "S")):
        return "SPINDLE"
    if any(token in code for token in ("M07", "M08", "M09")):
        return "COOLANT"
    if any(token in code for token in ("G54", "G55", "G56", "G57", "G58", "G59", "H", "D")):
        return "OFFSET"
    if any(token in code for token in ("G17", "G18", "G19", "G20", "G21", "G90", "G91")):
        return "MODAL"
    return "PROGRAM_STRUCTURE"


def diff_nc_text(old_bytes: bytes, new_bytes: bytes) -> tuple[NCLineDiff, ...]:
    old_lines = old_bytes.decode("utf-8").replace("\r\n", "\n").splitlines()
    new_lines = new_bytes.decode("utf-8").replace("\r\n", "\n").splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changes: list[NCLineDiff] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        width = max(i2 - i1, j2 - j1)
        for offset in range(width):
            old_text = old_lines[i1 + offset] if i1 + offset < i2 else None
            new_text = new_lines[j1 + offset] if j1 + offset < j2 else None
            category = _diff_category(new_text if new_text is not None else old_text or "")
            changes.append(
                NCLineDiff(
                    i1 + offset + 1 if old_text is not None else None,
                    j1 + offset + 1 if new_text is not None else None,
                    tag.upper(), category, old_text, new_text,
                )
            )
    return tuple(changes)


__all__ = [
    "CurrentReleaseSources", "NCLineDiff", "OfflineNCVerificationService",
    "compare_releases", "current_sources", "diff_nc_text",
]
