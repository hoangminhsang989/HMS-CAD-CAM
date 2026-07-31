"""Focused Stage 12.4B tests for the basic sample-derived Lathe Post."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from hms_cadcam.cam.lathe.lathe_post import (
    BasicFinalSafeTool,
    BasicNcExportService,
    BasicPostDiagnosticCode,
    BasicPostMetadata,
    BasicPostReadiness,
    BasicToolMapping,
    DwellPayload,
    LatheBasicFanucPostRendererV1,
    LatheProgramBlock,
    LatheProgramBlockKind,
    LatheProgramIRV1,
    basic_lathe_post_profile,
    basic_lathe_post_profile_registry,
)
from hms_cadcam.cam.lathe.lathe_post.formatting import format_number, round_rpm, sanitize_comment
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, stock_snapshot
from tests.unit.test_lathe_post_foundation_12_4a import _assembled


def _render(strategy=None):
    if strategy is None:
        _, _, _, program = _assembled()
    elif "thread" in strategy.value:
        _, _, _, program = _assembled(strategy)
    else:
        _, _, _, base = _assembled()
        blocks = []
        for block in base.blocks:
            payload = block.payload
            if hasattr(payload, "strategy_id"):
                payload = replace(payload, strategy_id=strategy.value)
            blocks.append(replace(block, payload=payload))
        program = LatheProgramIRV1(base.identity, tuple(blocks), base.profile_id)
    tool = next(block.payload for block in program.blocks if block.kind is LatheProgramBlockKind.TOOL_INTENT)
    result = LatheBasicFanucPostRendererV1().render(program, [BasicToolMapping(tool.tool_id, 1, 1)], BasicPostMetadata("sample"))
    assert result.accepted, result.diagnostics
    assert result.snapshot is not None
    return program, result.snapshot


def test_exact_profile_and_immutable_configuration() -> None:
    profile = basic_lathe_post_profile()
    assert profile.profile_id == "hms.lathe.fanuc_basic_sample_v1"
    assert profile.controller_family == "FANUC_STYLE_UNVERIFIED"
    assert profile.machine_model == "UNSPECIFIED"
    assert profile.controller_model == "UNSPECIFIED"
    assert profile.output_extension == ".NC"
    assert profile.profile_state == "BASIC_POST_UNVERIFIED"
    assert profile.output_readiness == "BASIC_NC_OUTPUT_READY_UNVERIFIED"
    assert profile.final_safe_tool == BasicFinalSafeTool(3, 3)
    assert profile.emit_line_numbers is False
    with pytest.raises(AttributeError):
        profile.program_number = 1  # type: ignore[misc]
    with pytest.raises(ValueError):
        replace(profile, work_offset_code="G54 M8")
    with pytest.raises(ValueError):
        replace(profile, optional_raw_setup_sequence=("M73",))


def test_feature_gate_requires_foundation_and_registry_is_singleton() -> None:
    flags = UiFeatureFlags({UiFeatureFlag.LATHE_POST_FOUNDATION_12_4A: False, UiFeatureFlag.LATHE_BASIC_POST_12_4B: True})
    assert flags.is_enabled(UiFeatureFlag.LATHE_BASIC_POST_12_4B) is False
    flags = UiFeatureFlags({UiFeatureFlag.LATHE_POST_FOUNDATION_12_4A: True, UiFeatureFlag.LATHE_BASIC_POST_12_4B: True})
    assert flags.is_enabled(UiFeatureFlag.LATHE_BASIC_POST_12_4B) is True
    assert basic_lathe_post_profile_registry(enabled=False, foundation_enabled=True).profiles == ()
    assert len(basic_lathe_post_profile_registry(enabled=True, foundation_enabled=True).profiles) == 1


def test_numeric_and_comment_contract() -> None:
    assert format_number(0.315, 3) == ".315"
    assert format_number(-0.0, 3) == "0"
    assert format_number(1.23456, 3) == "1.235"
    assert format_number(0.25, 4) == ".25"
    assert round_rpm(1000.5) == 1001
    assert sanitize_comment("ren (mũi)\n") == "REN [MUI]"
    with pytest.raises(ValueError):
        format_number(True, 3)
    with pytest.raises(ValueError):
        format_number(float("nan"), 3)


def test_sample_program_envelope_and_operation_order() -> None:
    _, snapshot = _render()
    text = snapshot.text
    assert text.startswith("%\r\nO0000\r\n")
    assert text.endswith("M30\r\n%\r\n")
    assert text.count("\r\nM30\r\n") == 1
    assert "G21\r\n" in text and "G99\r\n" in text
    assert "G0 T0101\r\n" in text
    assert "G0 G54 X" in text
    assert "G1 X" in text
    assert "\r\nN10 " not in text
    assert text.count("\r\nM05\r\n") == 1
    assert text.count("\r\nM9\r\n") == 1
    assert text.count("\r\nG28 U0 W0\r\n") == 1
    assert snapshot.readiness is BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED
    assert len(snapshot.sha256) == 64


@pytest.mark.parametrize("strategy", [
    "lathe.face.v1", "lathe.od_rough.v1", "lathe.od_finish.v1", "lathe.id_rough.v1",
    "lathe.id_finish.v1", "lathe.od_groove.v1", "lathe.id_groove.v1", "lathe.part_off.v1",
    "lathe.od_thread.v1", "lathe.id_thread.v1", "lathe.axial_drill.v1",
])
def test_all_eleven_strategies_render(strategy: str) -> None:
    from hms_cadcam.cam.lathe.types import LatheStrategyId
    enum_strategy = LatheStrategyId(strategy)
    _, snapshot = _render(enum_strategy)
    assert snapshot.text.startswith("%")
    assert "M30" in snapshot.text
    if "thread" in strategy:
        assert "G32" in snapshot.text
        assert "THREAD OUTPUT USES BASIC G32 - SPINDLE PHASE NOT VERIFIED" in snapshot.text
        assert " F." in snapshot.text or " F0." not in snapshot.text
        assert "G76" not in snapshot.text


def test_missing_mapping_duplicate_mapping_and_unsupported_dwell_fail_closed() -> None:
    _, _, _, program = _assembled()
    renderer = LatheBasicFanucPostRendererV1()
    tool = next(block.payload for block in program.blocks if block.kind is LatheProgramBlockKind.TOOL_INTENT)
    missing = renderer.render(program, ())
    assert not missing.accepted
    assert missing.diagnostics[0].code == BasicPostDiagnosticCode.MISSING_TOOL_MAPPING.value
    duplicate = renderer.render(program, [BasicToolMapping(tool.tool_id, 1, 1), BasicToolMapping("other", 1, 1)])
    assert not duplicate.accepted
    assert duplicate.diagnostics[0].code == BasicPostDiagnosticCode.DUPLICATE_TOOL_MAPPING.value
    motion = next(block.payload for block in program.blocks if block.kind is LatheProgramBlockKind.RAPID_MOTION)
    blocks = list(program.blocks)
    end_index = next(i for i, block in enumerate(blocks) if block.kind is LatheProgramBlockKind.OPERATION_END)
    blocks.insert(end_index, LatheProgramBlock(len(blocks), LatheProgramBlockKind.DWELL_INTENT, DwellPayload(motion.start, 1.0, motion.strategy_id, motion.toolpath_fingerprint), motion.strategy_id, "test"))
    blocks = [replace(block, sequence_index=i) for i, block in enumerate(blocks)]
    dwell_program = LatheProgramIRV1(program.identity, tuple(blocks), program.profile_id)
    dwell_result = renderer.render(dwell_program, [BasicToolMapping(tool.tool_id, 1, 1)])
    assert not dwell_result.accepted
    assert dwell_result.diagnostics[0].code == BasicPostDiagnosticCode.BASIC_POST_DWELL_SYNTAX_UNDEFINED.value


def test_export_acknowledgement_overwrite_atomic_and_sha(tmp_path: Path) -> None:
    _, snapshot = _render()
    exporter = BasicNcExportService()
    destination = tmp_path / "sample.NC"
    denied = exporter.export(snapshot, destination, acknowledged_unverified=False)
    assert not denied.success
    assert denied.diagnostics[0].code == BasicPostDiagnosticCode.EXPORT_ACK_REQUIRED.value
    first = exporter.export(snapshot, destination, acknowledged_unverified=True)
    assert first.success and destination.read_bytes().decode("ascii") == snapshot.text
    blocked = exporter.export(snapshot, destination, acknowledged_unverified=True)
    assert not blocked.success
    assert blocked.diagnostics[0].code == BasicPostDiagnosticCode.OVERWRITE_CONFIRMATION_REQUIRED.value
    replaced = exporter.export(snapshot, destination, acknowledged_unverified=True, overwrite_confirmed=True)
    assert replaced.success and replaced.sha256 == snapshot.sha256
    assert replaced.readiness is BasicPostReadiness.BASIC_NC_EXPORT_READY_UNVERIFIED


def test_catalog_parity_for_stage12_4b_keys() -> None:
    root = Path(__file__).parents[2] / "src/hms_cadcam/ui/catalogs"
    catalogs = [json.loads((root / name).read_text(encoding="utf-8")) for name in ("vi_VN.json", "en_US.json", "ko_KR.json")]
    keys = {key for key in catalogs[0] if key.startswith("lathe.basic_post.")}
    assert keys
    assert all(keys <= set(catalog) for catalog in catalogs)
    assert all(catalog[key] for catalog in catalogs for key in keys)