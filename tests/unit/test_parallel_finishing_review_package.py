"""Second-review package completeness checks for Stage 8A.2.1."""

from __future__ import annotations

import json

import pytest
from PySide6.QtGui import QImage

from tests.manual_stage8a2_1_parallel_finishing import generate

pytestmark = pytest.mark.ocp


def test_second_review_package_contains_numeric_and_visual_evidence(
    tmp_path,
    qapp,
) -> None:
    target = generate(tmp_path)
    summary = json.loads(target.read_text(encoding="utf-8"))
    assert summary["format_version"] == 2
    assert summary["review_image_count"] >= 10
    assert summary["determinism_all_identical"] is True
    assert len(tuple(tmp_path.glob("*.png"))) >= 10
    for name in summary["review_images"]:
        image = QImage(str(tmp_path / name))
        assert not image.isNull()
        assert image.width() == 1200 and image.height() == 800

    normal = json.loads(
        (tmp_path / "normal_comparison.json").read_text(encoding="utf-8")
    )
    assert normal["root_cause"]["cause_a_intentionally_coarse_mesh"] is True
    assert normal["root_cause"]["cause_c_triangle_normal_used"] is True
    assert normal["brep_surface_normal"][
        "maximum_surface_projection_deviation_mm"
    ] <= 0.01
    assert normal["brep_surface_normal"][
        "maximum_transverse_contact_normal_jump_degrees"
    ] < normal["coarse_triangle_normal"][
        "maximum_transverse_contact_normal_jump_degrees"
    ]

    cancellation = json.loads(
        (tmp_path / "cancellation_report.json").read_text(encoding="utf-8")
    )
    assert cancellation["intersection"]["cancel_observed"] is True
    assert cancellation["discretization"]["cancel_observed"] is True
    assert cancellation["partial_result_did_not_replace_latest"] is True
    assert cancellation["worker_project_close"]["thread_leak"] is False
    assert cancellation["worker_project_close"]["project_closed"] is True

    unsupported = json.loads(
        (tmp_path / "unsupported_cases.json").read_text(encoding="utf-8")
    )
    assert len(unsupported["cases"]) >= 13
    assert (tmp_path / "REVIEW_INDEX.md").is_file()
