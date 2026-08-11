"""Deterministic engineering-only fixtures for Stage18A Tranche2 tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.qualification import (
    AuthorityClass,
    AxisTravelLimit,
    ClearanceState,
    Coordinate3D,
    DryRunMode,
    DryRunQualificationEvidence,
    EnvelopeDimensions,
    EvidenceAttachment,
    EvidenceAttachmentRole,
    EvidenceState,
    FixtureEvidence,
    FixtureVerificationState,
    HolderFixtureClearanceEvidence,
    Level2QualificationRecord,
    MachineSetupQualification,
    MachineTravelContract,
    Orientation3D,
    OwnerAcceptanceRecord,
    PartialCoordinate3D,
    PhysicalAcceptancePolicy,
    SetupQualificationState,
    StockPlacementEvidence,
    ToolHolderQualification,
    WorkOffsetTransform,
    calculate_physical_readiness,
    qualify_static_nc,
)
from tests.unit._stage18a_qualification_fixtures import qualification_input


NOW = "2026-08-11T10:00:00+07:00"
BASE_INPUT = qualification_input()
BASE_REPORT = qualify_static_nc(BASE_INPUT)
TEST_EVIDENCE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "stage18a" / "r222_external_evidence.txt"
)


def fingerprint(name: str) -> ContentFingerprint:
    return ContentFingerprint.from_payload({"r221": name})


def setup_qualification(
    *,
    authoritative_transform: bool = True,
    fixture_verified: bool = True,
    sufficient_reach: bool = True,
    with_clearance: bool = True,
) -> MachineSetupQualification:
    level1_input = BASE_INPUT
    static_report = BASE_REPORT
    tool = ToolHolderQualification(
        1,
        level1_input.tools[0].tool_assembly_fingerprint,
        fingerprint("cutter"),
        fingerprint("holder"),
        80.0,
        35.0,
        110.0,
        25.0 if sufficient_reach else 5.0,
        10.0,
        10.0,
        40.0,
    )
    authority = (
        AuthorityClass.OWNER_CONFIRMED
        if authoritative_transform
        else AuthorityClass.UNVERIFIED
    )
    fixture_state = (
        FixtureVerificationState.OWNER_CONFIRMED
        if fixture_verified
        else FixtureVerificationState.UNVERIFIED
    )
    fixture = FixtureEvidence(
        "fixture-r221", "vise", EnvelopeDimensions(160.0, 100.0, 80.0),
        PartialCoordinate3D(20.0, 20.0, 0.0), Orientation3D(0.0, 0.0, 0.0),
        "owner setup sheet", authority, fixture_state,
    )
    setup = MachineSetupQualification(
        level1_input.machine_contract.profile_id,
        level1_input.machine_contract.fingerprint,
        "nc-r221",
        static_report.nc_sha256,
        static_report.post_profile_fingerprint,
        WorkOffsetTransform(
            "G54",
            PartialCoordinate3D(100.0, 100.0, 50.0) if authoritative_transform else PartialCoordinate3D(),
            Orientation3D(0.0, 0.0, 0.0) if authoritative_transform else Orientation3D(),
            "owner measured setup", authority, NOW if authoritative_transform else None,
        ),
        PartialCoordinate3D(0.0, 0.0, 0.0),
        "machine reference return",
        StockPlacementEvidence(
            EnvelopeDimensions(100.0, 80.0, 40.0),
            PartialCoordinate3D(50.0, 50.0, 0.0), Orientation3D(0.0, 0.0, 0.0),
            "owner setup sheet", authority,
        ),
        fixture,
        (tool,),
        NOW,
        authority,
        "R221 engineering fixture; no physical qualification claim",
        SetupQualificationState.OWNER_CONFIRMED if authoritative_transform else SetupQualificationState.UNVERIFIED,
    )
    if not with_clearance:
        return setup
    clearance = HolderFixtureClearanceEvidence(
        setup.binding_fingerprint,
        setup.tool_set_fingerprint,
        fixture.fingerprint,
        ClearanceState.HOLDER_FIXTURE_CLEARANCE_STATICALLY_VALIDATED,
        "existing simulation report:r221-engineering",
        AuthorityClass.REPOSITORY_CONFIRMED,
    )
    return replace(setup, clearance_evidence=clearance)


def travel_contract(*, complete: bool = True) -> MachineTravelContract:
    if not complete:
        unknown = AxisTravelLimit()
        return MachineTravelContract(
            unknown, unknown, unknown, "absolute endpoints not supplied", AuthorityClass.UNVERIFIED
        )
    return MachineTravelContract(
        AxisTravelLimit(-50.0, 550.0),
        AxisTravelLimit(-50.0, 450.0),
        AxisTravelLimit(-50.0, 380.0),
        "owner measured engineering fixture",
        AuthorityClass.OWNER_CONFIRMED,
    )


def physical_readiness(setup: MachineSetupQualification | None = None):
    value = setup or setup_qualification()
    return calculate_physical_readiness(
        value,
        (Coordinate3D(0.0, 0.0, 0.0), Coordinate3D(100.0, 80.0, 40.0)),
        travel_contract(),
        table_width_mm=650.0,
        table_depth_mm=400.0,
    )


def acceptance_policy() -> PhysicalAcceptancePolicy:
    return PhysicalAcceptancePolicy(
        "robodrill-r221-owner-policy",
        1,
        controller_graphics_required=False,
        dry_run_required=True,
        single_block_required=False,
        air_cut_required=False,
        operator_signoff_required=True,
        verifier_signoff_required=True,
        owner_signoff_required=True,
        owner_authority="HMS product owner",
        confirmed_at=NOW,
    )


def level2_record(
    *,
    setup: MachineSetupQualification | None = None,
    policy: PhysicalAcceptancePolicy | None = None,
    attempts: tuple[DryRunQualificationEvidence, ...] = (),
) -> Level2QualificationRecord:
    return Level2QualificationRecord(
        "r221-record", setup or setup_qualification(), policy or acceptance_policy(), attempts, NOW
    )


def dry_run_attempt(
    setup: MachineSetupQualification,
    *,
    result: EvidenceState = EvidenceState.PASS,
    evidence_id: str = "attempt-1",
    performed_at: str = "2026-08-11T10:10:00+07:00",
    remediation: str | None = None,
    attachments=(),
    policy: PhysicalAcceptancePolicy | None = None,
) -> DryRunQualificationEvidence:
    value = BASE_INPUT
    selected_policy = policy or acceptance_policy()
    selected_attachments = tuple(attachments)
    if result is EvidenceState.PASS and not selected_attachments:
        selected_attachments = (
            EvidenceAttachment.from_local_file(
                TEST_EVIDENCE_PATH,
                role=EvidenceAttachmentRole.NOTES,
                captured_at=NOW,
                provenance="engineering-only R222 evidence-shape fixture",
            ),
        )
    return DryRunQualificationEvidence(
        evidence_id,
        setup.machine_profile_id,
        "FANUC 31i-B",
        setup.nc_sha256,
        setup.machine_profile_fingerprint,
        setup.fingerprint,
        setup.tool_set_fingerprint,
        setup.post_fingerprint,
        value.machine_contract.fingerprint,
        selected_policy.fingerprint,
        "G54",
        performed_at,
        "operator-r221",
        "external operator record",
        DryRunMode.DRY_RUN,
        result,
        "External dry-run record; engineering test fixture only",
        () if result is EvidenceState.PASS else ("Observed motion blocker",),
        selected_attachments,
        OwnerAcceptanceRecord(
            "operator-r221", "verifier-r221", "owner-r221", result,
            performed_at, "Attributable approval record; not a digital signature",
        ),
        remediation,
    )


def level1_report():
    return BASE_REPORT


__all__ = [
    "NOW", "acceptance_policy", "dry_run_attempt", "fingerprint", "level1_report",
    "level2_record", "physical_readiness", "setup_qualification", "travel_contract",
]
