"""R223 deterministic NC block analyzer and fail-closed syntax tests."""

from hms_cadcam.cam.qualification import (
    AnalysisPolicy,
    MotionClass,
    OfflineFindingSeverity,
    analyze_nc_bytes,
    diff_nc_text,
)
from tests.unit._stage18a_tranche3_fixtures import release_context


def test_clean_program_has_deterministic_trace_and_typed_physical_unknowns():
    _service, payload, _setup, _ready, session, *_rest = release_context()

    assert session.blocker_count == 0
    assert tuple(block.sequence for block in session.blocks) == tuple(range(len(session.blocks)))
    assert any(block.motion_class is MotionClass.RAPID for block in session.blocks)
    assert any(block.motion_class is MotionClass.CUTTING_LINEAR for block in session.blocks)
    assert any(block.motion_class is MotionClass.TOOL_CHANGE for block in session.blocks)
    assert session.nc_sha256 == analyze_nc_bytes(payload).nc_sha256
    codes = {item.code for item in session.findings}
    assert "RAPID_PHYSICAL_CLEARANCE_UNVERIFIED" in codes
    assert "PHYSICAL_TOOL_CHANGE_POSITION_UNVERIFIED" in codes
    assert all(item.source_validator and item.authority and item.remediation for item in session.findings)


def test_canned_cycles_are_adjacency_and_lowercase_safe():
    variants = ("G90G81", "g90g81", "N120G90G81", "G90 G81", "N120 g90 G81 (cycle)")
    for variant in variants:
        result = analyze_nc_bytes(f"%\n{variant}\nM30\n%".encode(), AnalysisPolicy())
        blockers = {item.code for item in result.findings if item.severity is OfflineFindingSeverity.BLOCKER}
        assert "UNSUPPORTED_CANNED_CYCLE_TOKEN" in blockers


def test_tapping_and_unresolved_syntax_are_permanent_blockers():
    tapping = analyze_nc_bytes(b"%\nG90G84Z-5.R2.F100\nM30\n%")
    unresolved = analyze_nc_bytes(b"%\nG54\nG01X1Y1QBAD\nM30\n%")

    assert "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED" in {item.code for item in tapping.findings}
    assert "UNRESOLVED_BLOCK_TOKEN" in {item.code for item in unresolved.findings}
    assert any(block.motion_class is MotionClass.UNRESOLVED for block in unresolved.blocks)


def test_comment_only_byte_change_changes_sha_and_is_classified():
    _service, payload, *_rest = release_context()
    changed = payload + b"(R223 COMMENT ONLY)\n"

    original = analyze_nc_bytes(payload)
    modified = analyze_nc_bytes(changed)
    changes = diff_nc_text(payload, changed)

    assert original.nc_sha256 != modified.nc_sha256
    assert changes[-1].category == "COMMENT_ONLY"
