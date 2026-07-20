import re

from hms_cadcam.cam.post import *
from tests.unit._post_fixtures import source_snapshot


def test_dummy_adapter_is_deterministic_neutral_utf8_without_g_or_m_words():
    source = source_snapshot()
    definition = canonical_definition()
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          definition, simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))
    program = lower_toolpath(request, source)
    adapter = CanonicalDummyAdapter()
    first = adapter.format_program(program, definition)
    second = adapter.format_program(program, definition)
    assert first == second
    assert first.endswith("\n") and first.encode("utf-8")
    assert adapter.validate_output(first, program, definition) == ()
    assert "PROGRAM_BEGIN" in first and "PROGRAM_END" in first
    assert re.search(r"\b[GM]\d+\b", first) is None
