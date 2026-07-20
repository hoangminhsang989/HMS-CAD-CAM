"""HMS CAD/CAM Post Processor Foundation (7D.1)."""

from hms_cadcam.cam.post.adapter import PostProcessorAdapter
from hms_cadcam.cam.post.dummy import CanonicalDummyAdapter, canonical_definition
from hms_cadcam.cam.post.lowering import PostSourceSnapshot, lower_toolpath, validate_post_source
from hms_cadcam.cam.post.model import *
from hms_cadcam.cam.post.service import PostComputationToken, PostExecution, PostRuntimeService, build_post_input_fingerprint
from hms_cadcam.cam.post.validation import validate_output, validate_program_ir, validate_request
from hms_cadcam.cam.post.profile import (
    ArcOutputMode, ArcPolicy, BlockNumberPolicy, ControllerToolBinding,
    CoolantCodeMapping, CutterCompensationPolicy, DwellPolicy,
    NumericFormatPolicy, ProductionControllerProfile, ProductionProgramContext,
    ProgramNumberPolicy, SafeSequenceToken, SpindleCodeMapping,
    ToolActivationPolicy, WorkOffsetMapping, profile_from_dict, profile_to_dict,
    sanitize_comment_fragment,
)
from hms_cadcam.cam.post.fanuc_robodrill_21i import (
    ADAPTER_KEY as FANUC_ROBODRILL_21I_ADAPTER_KEY,
    PROFILE_KEY as FANUC_ROBODRILL_21I_PROFILE_KEY,
    FanucRobodrill21iAdapter, robodrill_21i_definition, robodrill_21i_profile,
)
from hms_cadcam.cam.post.export_model import (
    ExportOverwritePolicy,
    ExportTarget,
    NCAssemblyExportRequest,
    NCArtifactManifest,
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCExportDiagnostic,
    NCExportDiagnosticCode,
    NCExportRequest,
    NCExportResult,
    NCExportStatistics,
    NCExportStatus,
)
from hms_cadcam.cam.post.export_service import (
    NCExportExecution,
    NCAssemblyExportSourceSnapshot,
    NCExportService,
    NCExportSourceSnapshot,
    NCExportToken,
)
from hms_cadcam.cam.post.export_store import (
    NCArtifactStore,
    NCArtifactStoreError,
)
from hms_cadcam.cam.post.assembly_model import (
    ProgramAssemblyContext,
    ProgramAssemblyDiagnostic,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyOperationInput,
    ProgramAssemblyOrderingPolicy,
    ProgramAssemblyPlan,
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyStatistics,
    ProgramAssemblyStatus,
    ProgramOperationSection,
)
from hms_cadcam.cam.post.assembly_service import (
    ProgramAssemblyComputationToken,
    ProgramAssemblyExecution,
    ProgramAssemblyService,
    build_assembly_input_fingerprint,
)
from hms_cadcam.cam.post.assembly_validation import (
    validate_assembly_output,
    validate_assembly_plan,
    validate_assembly_request,
)
