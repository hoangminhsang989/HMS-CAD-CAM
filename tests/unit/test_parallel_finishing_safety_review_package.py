"""Completeness checks for the Stage 8A.2.2 private review package."""

from __future__ import annotations

import json

import pytest
from PySide6.QtGui import QImage

from tests.manual_stage8a2_2_parallel_safety import IMAGE_NAMES, generate

pytestmark = pytest.mark.ocp


def test_parallel_safety_review_package_is_complete_and_deterministic(
    tmp_path,
    qapp,
) -> None:
    summary_path = generate(tmp_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["algorithm_version_before"] == 2
    assert summary["algorithm_version_after"] == 3
    assert summary["strategy_payload_version"] == 1
    assert summary["review_image_count"] == 19
    assert summary["safety_report_sample_count"] == 8
    assert summary["determinism_all_identical"] is True
    assert summary["production_safe"] is False
    for name in IMAGE_NAMES:
        image = QImage(str(tmp_path / name))
        assert not image.isNull()
        assert (image.width(), image.height()) == (1200, 800)
    determinism = json.loads(
        (tmp_path / "determinism_report.json").read_text(encoding="utf-8")
    )
    assert len(determinism["cases"]) == 7
    assert all(len(item["runs"]) == 3 and item["identical"] for item in determinism["cases"])
    for item in determinism["cases"]:
        assert {
            "candidate_count",
            "narrow_phase_count",
            "subdivision_count",
            "status",
            "diagnostic_codes",
            "collision_order",
            "toolpath_ir_hash",
            "safety_report_hash",
            "artifact_hash",
        } <= set(item["runs"][0])
    rapid = next(item for item in determinism["cases"] if item["case"] == "rapid_crossing")
    assert set(rapid["runs"][0]["diagnostic_codes"]) == {
        "parallel.safety.rapid_collision"
    }
    for case_name in ("shank_collision", "holder_collision", "rapid_crossing", "concave"):
        case = next(item for item in determinism["cases"] if item["case"] == case_name)
        assert len(case["runs"][0]["diagnostic_codes"]) == 1
        assert case["runs"][0]["collision_order"][0][3] > 1
    samples = json.loads(
        (tmp_path / "safety_report_samples.json").read_text(encoding="utf-8")
    )
    assert samples["sample_count"] == 8
    assert {item["sample"] for item in samples["samples"]} == {
        "expected_contact",
        "cutter_gouge",
        "adjacent_protected_face",
        "shank_collision",
        "holder_collision",
        "rapid_swept_collision",
        "concave_gouge",
        "safe_planar",
    }
    cancellation = json.loads(
        (tmp_path / "cancellation_report.json").read_text(encoding="utf-8")
    )
    assert all(
        cancellation[name]["cancel_observed"]
        for name in ("broad_phase", "narrow_phase", "swept_subdivision", "before_publish")
    )
    assert cancellation["project_close"]["thread_leak"] is False
    assert cancellation["superseded"]["stale_result_published"] is False
    assert (tmp_path / "REVIEW_INDEX.md").is_file()
