from dataclasses import replace

from hms_cadcam.cam.post import *
from hms_cadcam.cam.post.validation import validate_output, validate_program_ir
from tests.unit._post_fixtures import source_snapshot


def test_ir_validation_detects_unbalanced_process_state():
    source = source_snapshot()
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))
    program = lower_toolpath(request, source)
    assert validate_program_ir(program) == ()


def test_output_validation_rejects_controller_syntax_and_nonfinite_text():
    source = source_snapshot(with_motion=False)
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))
    program = lower_toolpath(request, source)
    diagnostics = validate_output("PROGRAM_BEGIN\nG1 Xnan\nPROGRAM_END\n", program, request.post_definition)
    codes = {item.code for item in diagnostics}
    assert PostDiagnosticCode.FORMAT_FAILED in codes
