from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import *
from hms_cadcam.cam.post.model import *


def test_definition_fingerprint_excludes_display_name_and_is_typed():
    first = PostProcessorDefinition(PostProcessorDefinitionId.new(), 1, "canonical_dummy", 1,
                                    PostProcessorCapabilities(), display_name="A")
    second = PostProcessorDefinition(PostProcessorDefinitionId.new(), 1, "canonical_dummy", 1,
                                     PostProcessorCapabilities(), display_name="B")
    assert first.fingerprint == second.fingerprint
    assert first.capabilities.supported_operation_strategies == (
        "boring_v1", "contour_2d", "drilling_v1", "facing_2_5d", "pocket_2_5d", "reaming_v1", "tapping_v1"
    )


def test_policy_rejects_motion_approximation_and_gate_is_versioned():
    with pytest.raises(CamInvariantError):
        LoweringPolicy(allow_arc_to_line=True)
    assert SimulationGatePolicy(SimulationGateMode.REQUIRE_PASS).fingerprint != SimulationGatePolicy(SimulationGateMode.OPTIONAL).fingerprint


def test_program_records_are_immutable_and_require_explicit_boundaries():
    with pytest.raises(CamValidationError):
        UnitsRecord(0, LengthUnit.UNKNOWN)
    with pytest.raises(CamInvariantError):
        NCProgramIR.create(program_id=NCProgramId.new(), project_id=uuid4(), operation_id=OperationId.new(),
                           artifact_id=ToolpathArtifactId.new(), artifact_fingerprint=ContentFingerprint.from_payload({"a": 1}),
                           strategy_key="facing_2_5d", strategy_version=1, unit=LengthUnit.MM,
                           coordinate_mode=CoordinateMode.ABSOLUTE, plane=Plane.XY, setup_id=SetupId.new(),
                           setup_revision=Revision(0), wcs=WcsFrame.identity(LengthUnit.MM), work_offset=WorkOffset("PRIMARY", 1),
                           tool_assembly_id=ToolAssemblyId.new(), tool_assembly_fingerprint=ContentFingerprint.from_payload({"t": 1}),
                           records=(ProgramEndRecord(0),))


def test_diagnostic_catalog_has_post_namespace():
    assert all(code.value.startswith("post.") for code in PostDiagnosticCode)
