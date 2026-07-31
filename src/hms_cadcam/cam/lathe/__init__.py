"""Public Qt-free HMS Lathe Foundation V1 contract."""

from hms_cadcam.cam.lathe.application import LatheCommandOutcome, LatheOperationService, LatheServiceSession
from hms_cadcam.cam.lathe.capabilities import FailClosedLatheToolCapabilityResolver, LatheToolCapabilityResolution, LatheToolCapabilityResolver, LatheToolReference, StaticLatheToolCapabilityResolver
from hms_cadcam.cam.lathe.commands import BindLatheGeometry, BindLatheTool, ChangeLatheStrategy, ClearLatheGeometry, ClearLatheTool, CreateLatheOperation, DeleteLatheOperation, SetLatheOperationEnabled, UpdateLatheParameters, ValidateLatheOperation
from hms_cadcam.cam.lathe.domain import LATHE_SNAPSHOT_SCHEMA_VERSION, LatheGeometryBinding, LatheOperationEvaluation, LatheOperationState, LatheOwnershipKey, LatheToolBinding, evaluate_lathe_operation, lathe_operation_from_canonical_mapping, lathe_operation_to_canonical_mapping
from hms_cadcam.cam.lathe.parameters import COMMON_PARAMETER_DESCRIPTORS, COMMON_PARAMETER_IDS, LATHE_PARAMETER_SCHEMAS, LatheParameterDescriptor, LatheParameterSchema, LatheParameterState, LatheParameterUpdate, LatheParameterValidationError, build_lathe_v1_defaults, lathe_parameter_schema
from hms_cadcam.cam.lathe.presenter import LatheOperationSnapshot, LathePresenterFacade, LathePresenterSnapshot, LatheStrategyDescriptor
from hms_cadcam.cam.lathe.readiness import LatheFoundationProvider, LatheWorkspaceReadiness, STAGE12_LATHE_WORKSPACE_READINESS, create_lathe_foundation_provider
from hms_cadcam.cam.lathe.strategies import LATHE_STRATEGY_REGISTRY, LatheStrategyDefinition, lathe_strategy_definition
from hms_cadcam.cam.lathe.types import LatheDiagnostic, LatheDiagnosticCode, LatheGeometryKind, LatheOperationReadiness, LatheParameterGroup, LatheParameterUnitKind, LatheParameterValueKind, LatheSpindleDirection, LatheStage9A9State, LatheStrategyFamily, LatheStrategyId, LatheThreadHand, LatheToolCapability, LatheWorkspaceReadinessReason, LatheWorkspaceReadinessState

__all__ = [
    "BindLatheGeometry", "BindLatheTool", "COMMON_PARAMETER_DESCRIPTORS",
    "COMMON_PARAMETER_IDS", "ChangeLatheStrategy", "ClearLatheGeometry",
    "ClearLatheTool", "CreateLatheOperation", "DeleteLatheOperation",
    "FailClosedLatheToolCapabilityResolver", "LATHE_PARAMETER_SCHEMAS",
    "LATHE_SNAPSHOT_SCHEMA_VERSION", "LATHE_STRATEGY_REGISTRY",
    "LatheCommandOutcome", "LatheDiagnostic", "LatheDiagnosticCode",
    "LatheFoundationProvider", "LatheGeometryBinding", "LatheGeometryKind",
    "LatheOperationEvaluation", "LatheOperationReadiness", "LatheOperationService",
    "LatheOperationSnapshot", "LatheOperationState", "LatheOwnershipKey",
    "LatheParameterDescriptor", "LatheParameterGroup", "LatheParameterSchema",
    "LatheParameterState", "LatheParameterUnitKind", "LatheParameterUpdate",
    "LatheParameterValidationError", "LatheParameterValueKind",
    "LathePresenterFacade", "LathePresenterSnapshot", "LatheServiceSession",
    "LatheSpindleDirection", "LatheStage9A9State", "LatheStrategyDefinition",
    "LatheStrategyDescriptor", "LatheStrategyFamily", "LatheStrategyId",
    "LatheThreadHand", "LatheToolBinding", "LatheToolCapability",
    "LatheToolCapabilityResolution", "LatheToolCapabilityResolver",
    "LatheToolReference", "LatheWorkspaceReadiness",
    "LatheWorkspaceReadinessReason", "LatheWorkspaceReadinessState",
    "STAGE12_LATHE_WORKSPACE_READINESS", "SetLatheOperationEnabled",
    "StaticLatheToolCapabilityResolver", "UpdateLatheParameters",
    "ValidateLatheOperation", "build_lathe_v1_defaults",
    "create_lathe_foundation_provider", "evaluate_lathe_operation",
    "lathe_operation_from_canonical_mapping", "lathe_operation_to_canonical_mapping",
    "lathe_parameter_schema", "lathe_strategy_definition",
]
# Stage 12.4A additive controller-neutral Program IR public surface.
from hms_cadcam.cam.lathe.lathe_post import *
from hms_cadcam.cam.lathe.lathe_post import __all__ as _STAGE12_4A_PUBLIC

__all__ += _STAGE12_4A_PUBLIC

# Preserve the approved semantic import name without creating a reserved
# pre-12.4A post.py module or generic cam/post dependency.
import sys as _sys
from hms_cadcam.cam.lathe import lathe_post as _lathe_post
_sys.modules[f"{__name__}.post"] = _lathe_post
for _name in ("assembler", "identity", "ir", "listing", "profile", "service"):
    _canonical = f"{__name__}.lathe_post.{_name}"
    if _canonical in _sys.modules:
        _sys.modules[f"{__name__}.post.{_name}"] = _sys.modules[_canonical]
