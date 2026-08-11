"""Stage18A exact-machine static NC qualification package."""

from hms_cadcam.cam.qualification.codec import dumps, loads
from hms_cadcam.cam.qualification.model import (
    AuthorityClass,
    EvidenceReference,
    EvidenceResult,
    FindingCode,
    FindingSeverity,
    MachineQualificationContract,
    PhysicalEvidence,
    QualificationFinding,
    QualificationLevel,
    QualificationReport,
    QualificationState,
    QualifiedLeaf,
    QualifiedNCArtifact,
    SampleAuthority,
    StockEnvelope,
    ToolQualificationInput,
)
from hms_cadcam.cam.qualification.profile import (
    ROBODRILL_ALPHA_D21MIB_DISPLAY_NAME,
    ROBODRILL_ALPHA_D21MIB_PROFILE_ID,
    robodrill_alpha_d21mib_contract,
)
from hms_cadcam.cam.qualification.samples import (
    GoldenSampleFixture,
    GoldenSampleResult,
    SampleStatus,
    engineering_sample_fixtures,
    run_deterministic_sample,
)
from hms_cadcam.cam.qualification.service import (
    MachineQualificationService,
    qualification_status_vi,
    render_qualification_report_vi,
)
from hms_cadcam.cam.qualification.store import (
    QualificationArtifactStore,
    QualificationStoreError,
)
from hms_cadcam.cam.qualification.validation import (
    StaticQualificationInput,
    qualify_static_nc,
    validate_fanuc_modal_sequence,
)
from hms_cadcam.cam.qualification.physical_model import (
    AxisTravelLimit,
    ClearanceState,
    Coordinate3D,
    EnvelopeDimensions,
    FixtureEvidence,
    FixtureVerificationState,
    HolderFixtureClearanceEvidence,
    MachineSetupQualification,
    MachineTravelContract,
    Orientation3D,
    PartialCoordinate3D,
    PhysicalReadinessResult,
    PhysicalTravelState,
    PlacementState,
    SetupQualificationState,
    StockPlacementEvidence,
    ToolHolderQualification,
    ToolReachState,
    WorkOffsetTransform,
    calculate_physical_readiness,
    validate_physical_travel,
    validate_stock_and_fixture_placement,
)
from hms_cadcam.cam.qualification.evidence_model import (
    DryRunMode,
    DryRunQualificationEvidence,
    EvidenceAttachment,
    EvidenceAttachmentRole,
    EvidenceState,
    Level2QualificationRecord,
    Level2Readiness,
    Level2WorkflowState,
    OwnerAcceptanceRecord,
    PhysicalAcceptancePolicy,
    assess_level2_readiness,
    level2_status_vi,
)
from hms_cadcam.cam.qualification.tranche2_service import Tranche2QualificationService
from hms_cadcam.cam.qualification.tranche2_store import (
    Tranche2QualificationStore,
    Tranche2StoreError,
    dumps_level2_record,
    loads_level2_record,
)
from hms_cadcam.cam.qualification.checklist import (
    GoldenSampleApproval,
    PhysicalChecklistItem,
    ROBODRILL_CHECKLIST_KEYS,
    RobodrillPhysicalChecklist,
)


__all__ = [
    "AuthorityClass", "AxisTravelLimit", "ClearanceState", "Coordinate3D",
    "DryRunMode", "DryRunQualificationEvidence", "EnvelopeDimensions",
    "EvidenceAttachment", "EvidenceAttachmentRole", "EvidenceReference",
    "EvidenceResult", "EvidenceState", "FindingCode",
    "FindingSeverity", "GoldenSampleFixture", "GoldenSampleResult",
    "FixtureEvidence", "FixtureVerificationState", "GoldenSampleApproval",
    "HolderFixtureClearanceEvidence",
    "Level2QualificationRecord", "Level2Readiness", "Level2WorkflowState",
    "MachineQualificationContract", "MachineQualificationService",
    "MachineSetupQualification", "MachineTravelContract", "Orientation3D",
    "OwnerAcceptanceRecord", "PartialCoordinate3D", "PhysicalAcceptancePolicy",
    "PhysicalChecklistItem", "PhysicalEvidence", "PhysicalReadinessResult", "PhysicalTravelState",
    "PlacementState",
    "QualificationArtifactStore", "QualificationFinding", "QualificationLevel",
    "QualificationReport", "QualificationState", "QualificationStoreError",
    "QualifiedLeaf", "QualifiedNCArtifact", "ROBODRILL_ALPHA_D21MIB_DISPLAY_NAME",
    "ROBODRILL_ALPHA_D21MIB_PROFILE_ID", "SampleAuthority", "SampleStatus",
    "SetupQualificationState", "StaticQualificationInput", "StockEnvelope",
    "StockPlacementEvidence", "ToolHolderQualification", "ToolQualificationInput",
    "ToolReachState", "Tranche2QualificationService", "Tranche2QualificationStore",
    "ROBODRILL_CHECKLIST_KEYS", "RobodrillPhysicalChecklist", "Tranche2StoreError",
    "WorkOffsetTransform", "assess_level2_readiness",
    "calculate_physical_readiness", "dumps", "dumps_level2_record", "loads",
    "loads_level2_record", "level2_status_vi", "qualification_status_vi", "qualify_static_nc",
    "engineering_sample_fixtures",
    "render_qualification_report_vi", "robodrill_alpha_d21mib_contract",
    "run_deterministic_sample", "validate_fanuc_modal_sequence",
    "validate_physical_travel", "validate_stock_and_fixture_placement",
]
