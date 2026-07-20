import math
from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import Length, LengthUnit, Point3, Vector3, WorkOffset
from hms_cadcam.cam.post import (
    ArcMotionRecord,
    FanucRobodrill21iAdapter,
    LinearMotionRecord,
    PostRequest,
    PostStatistics,
    RapidMotionRecord,
    SimulationGateMode,
    SimulationGatePolicy,
    robodrill_21i_definition,
)
from hms_cadcam.cam.toolpath import Pose
from tests.unit._fanuc_fixtures import basic_program, pose


def test_g54_only_mapping_is_fail_closed():
    program = basic_program()
    changed = replace(program, work_offset=WorkOffset("OTHER", 2), program_fingerprint=None)
    definition = robodrill_21i_definition()
    diagnostics = FanucRobodrill21iAdapter(definition).validate_program_ir(changed)
    assert any(item.message_key == "post.fanuc.g54_mapping_required" for item in diagnostics)


def test_safe_z_below_program_motion_is_rejected():
    program = basic_program()
    context = replace(program.production_context, safe_z=Length(-1.0, LengthUnit.MM))
    changed = replace(program, production_context=context, program_fingerprint=None)
    diagnostics = FanucRobodrill21iAdapter(robodrill_21i_definition()).validate_program_ir(changed)
    assert any(item.message_key == "post.fanuc.safe_z_below_motion" for item in diagnostics)


def test_legacy_g41_requires_explicit_d_offset_and_contour_strategy():
    program = basic_program(strategy="facing_2_5d")
    context = replace(program.production_context, use_legacy_cutter_compensation=True)
    changed = replace(program, production_context=context, program_fingerprint=None)
    diagnostics = FanucRobodrill21iAdapter(robodrill_21i_definition()).validate_program_ir(changed)
    assert any(item.message_key == "post.fanuc.legacy_compensation_strategy_unsupported" for item in diagnostics)


def test_output_validator_rejects_tampered_length_compensation_safe_z():
    program = basic_program()
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    text = adapter.format_program(program, definition).replace("G43Z10.H1", "G43Z0.H1")

    diagnostics = adapter.validate_output(text, program, definition)

    assert any(item.message_key == "post.fanuc.length_offset_invalid" for item in diagnostics)


def test_output_validator_rejects_standalone_g40_without_explicit_policy():
    program = basic_program()
    definition = robodrill_21i_definition()
    adapter = FanucRobodrill21iAdapter(definition)
    text = adapter.format_program(program, definition).replace(
        "G43Z10.H1\r\n",
        "G43Z10.H1\r\nG40\r\n",
    )

    diagnostics = adapter.validate_output(text, program, definition)

    assert any(item.message_key == "post.fanuc.unexpected_cutter_compensation" for item in diagnostics)


def test_adapter_rejects_profile_fingerprint_drift_even_when_keys_match():
    program = basic_program()
    definition = robodrill_21i_definition()
    altered_profile = replace(
        definition.production_profile,
        safe_end_records=tuple(reversed(definition.production_profile.safe_end_records)),
    )
    altered_definition = replace(definition, production_profile=altered_profile)
    request = PostRequest(
        program.project_id,
        program.operation_id,
        program.artifact_id,
        altered_definition,
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
        program_context=program.production_context,
    )
    adapter = FanucRobodrill21iAdapter(altered_definition)

    diagnostics = adapter.validate_request(request)

    assert altered_profile.fingerprint != definition.production_profile.fingerprint
    assert any(item.message_key == "post.fanuc.definition_mismatch" for item in diagnostics)
    with pytest.raises(ValueError, match="post.fanuc.definition_mismatch"):
        adapter.format_program(program, altered_definition)


def test_non_xy_arc_is_rejected_even_when_xy_projected_radii_match():
    program = basic_program(strategy="contour_2d", arc=True, sweep=math.pi)
    records = tuple(
        replace(
            record,
            end=pose(10.0, 10.0, 0.0),
            center=Point3(10.0, 5.0, 0.0, LengthUnit.MM),
            plane_normal=Vector3(1.0, 0.0, 0.0),
            sweep_radians=math.pi,
        )
        if isinstance(record, ArcMotionRecord)
        else record
        for record in program.records
    )
    changed = replace(
        program,
        records=records,
        statistics=PostStatistics.calculate(records),
        program_fingerprint=None,
    )

    diagnostics = FanucRobodrill21iAdapter(robodrill_21i_definition()).validate_program_ir(changed)

    assert any(item.message_key == "post.fanuc.non_xy_arc_unsupported" for item in diagnostics)


def test_three_axis_profile_rejects_unrepresentable_tool_orientation():
    program = basic_program()
    records = tuple(
        replace(
            record,
            start=Pose(record.start.position, Vector3(1.0, 0.0, 0.0)),
            end=Pose(record.end.position, Vector3(1.0, 0.0, 0.0)),
        )
        if isinstance(record, (RapidMotionRecord, LinearMotionRecord))
        else record
        for record in program.records
    )
    changed = replace(
        program,
        records=records,
        statistics=PostStatistics.calculate(records),
        program_fingerprint=None,
    )

    diagnostics = FanucRobodrill21iAdapter(robodrill_21i_definition()).validate_program_ir(changed)

    assert any(item.message_key == "post.fanuc.tool_axis_unsupported" for item in diagnostics)
