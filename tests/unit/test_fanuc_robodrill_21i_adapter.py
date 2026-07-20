import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from hms_cadcam.cam.domain import CamInvariantError
from hms_cadcam.cam.post import (
    FanucRobodrill21iAdapter, PostRequest, SimulationGateMode,
    PostStatistics, SemanticMarkerRecord, SimulationGatePolicy,
    robodrill_21i_definition,
)
from hms_cadcam.cam.post.fanuc_validation import validate_fanuc_output
from hms_cadcam.cam.toolpath import FeedMode
from tests.unit._fanuc_fixtures import basic_program, fixture_context
from tests.unit._post_fixtures import source_snapshot


GOLDEN_DIR = Path(__file__).parents[1] / "golden" / "post" / "robodrill_fanuc_21i_worknc_expanded_v1"


def test_fanuc_adapter_formats_expanded_motion_with_crlf_and_footer():
    program = basic_program()
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    assert adapter.validate_program_ir(program) == ()
    text = adapter.format_program(program, definition)
    assert text.endswith("\r\n")
    assert "M06T1" in text and "G54" in text and "G01X10.Y0.Z0.F100" in text
    assert text.splitlines()[-6:] == ["M09", "M05", "G91G28G0Z0", "G28Y0.", "M30", "%"]
    assert adapter.validate_output(text, program, definition) == ()


def test_fanuc_arc_output_preserves_large_signed_arc_and_ijk():
    program = basic_program(strategy="contour_2d", arc=True)
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    assert adapter.validate_program_ir(program) == ()
    text = adapter.format_program(program, definition)
    assert "G03" in text and "I-5." in text and "J0." in text
    assert adapter.validate_output(text, program, definition) == ()


@pytest.mark.parametrize("hand", ("right_hand_tap", "left_hand_tap"))
def test_fanuc_tapping_is_rejected_without_canned_cycle_or_g95(hand):
    program = basic_program(strategy="tapping_v1")
    records = list(program.records)
    records.insert(-1, SemanticMarkerRecord(0, "tap.hand", metadata=(("hand", hand),)))
    records = tuple(replace(record, sequence_index=index) for index, record in enumerate(records))
    program = replace(
        program,
        records=records,
        statistics=PostStatistics.calculate(records),
        program_fingerprint=None,
    )
    definition = robodrill_21i_definition()
    diagnostics = FanucRobodrill21iAdapter(definition).validate_program_ir(program)
    assert any(item.message_key == "post.fanuc.tapping_unsupported" for item in diagnostics)
    with pytest.raises(ValueError):
        FanucRobodrill21iAdapter(definition).format_program(program, definition)


def test_production_request_without_context_fails_closed():
    program = basic_program()
    with pytest.raises(CamInvariantError):
        PostRequest(program.project_id, program.operation_id, program.artifact_id, robodrill_21i_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))


def test_validator_rejects_tampering_and_wrong_footer():
    program = basic_program()
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    text = adapter.format_program(program, definition).replace("M30\r\n%\r\n", "M30\r\nO1234\r\n%\r\n")
    diagnostics = validate_fanuc_output(text, program, definition)
    assert any(item.code.value == "post.format_failed" for item in diagnostics)


def test_validator_rejects_arc_endpoint_tampering_even_when_radius_still_matches():
    program = basic_program(strategy="contour_2d", arc=True, sweep=math.pi / 2.0)
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    text = adapter.format_program(program, definition).replace(
        "G03X5.Y5.Z0.I-5.J0.F100",
        "G03X5.Y-5.Z0.I-5.J0.F100",
    )

    diagnostics = validate_fanuc_output(text, program, definition)

    assert any(item.message_key == "post.fanuc.arc_output_geometry_invalid" for item in diagnostics)


@pytest.mark.parametrize(
    ("original", "tampered"),
    (
        ("G01X10.Y0.Z0.F100", "G01X9.Y0.Z0.F100"),
        ("G01X10.Y0.Z0.F100", "G01X10.Y-0.Z0.F100"),
        ("M03S4000", "M03S9000"),
    ),
)
def test_adapter_validator_rejects_semantically_tampered_known_commands(original, tampered):
    program = basic_program()
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    text = adapter.format_program(program, definition).replace(original, tampered)

    diagnostics = adapter.validate_output(text, program, definition)

    assert any(item.message_key == "post.fanuc.output_semantic_mismatch" for item in diagnostics)


def test_fanuc_golden_programs_match_bytes_and_sha256_manifest():
    source = source_snapshot(with_motion=False)
    cases = {
        "facing.fn": dict(strategy="facing_2_5d"),
        "planar_face_facing.fn": dict(strategy="facing_2_5d"),
        "contour_line.fn": dict(strategy="contour_2d", context=fixture_context(source, file_name="contour_line.fn", cutter=True)),
        "contour_g02.fn": dict(strategy="contour_2d", arc=True, sweep=-math.pi / 2.0, context=fixture_context(source, file_name="contour_g02.fn", cutter=True)),
        "contour_g03.fn": dict(strategy="contour_2d", arc=True, sweep=math.pi / 2.0, context=fixture_context(source, file_name="contour_g03.fn", cutter=True)),
        "arc_over_180.fn": dict(strategy="contour_2d", arc=True, sweep=3.0 * math.pi / 2.0, context=fixture_context(source, file_name="arc_over_180.fn", cutter=True)),
        "pocket.fn": dict(strategy="pocket_2_5d"),
        "drilling_expanded.fn": dict(strategy="drilling_v1"),
        "drilling_peck_expanded.fn": dict(strategy="drilling_v1", peck=True),
        "reaming_controlled_retract.fn": dict(strategy="reaming_v1", feed_mode=FeedMode.UNITS_PER_REVOLUTION, coolant=True),
        "boring_controlled_retract.fn": dict(strategy="boring_v1", feed_mode=FeedMode.UNITS_PER_REVOLUTION, coolant=True),
    }
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    assert set(manifest) == set(cases)
    for name, options in cases.items():
        context = options.pop("context", fixture_context(source, file_name=name))
        program = basic_program(source=source, context=context, **options)
        text = adapter.format_program(program, definition)
        golden_path = GOLDEN_DIR / name
        assert text == golden_path.read_bytes().decode("utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == manifest[name]
        assert adapter.validate_output(text, program, definition) == ()
