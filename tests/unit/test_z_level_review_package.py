"""Completeness checks for calculation-backed Stage 8A.3.1 evidence."""

from __future__ import annotations

import json

from PIL import Image
import pytest

from tools.create_zlevel_review_package import (
    IMAGE_SOURCES,
    MONTAGE_NAME,
    create,
)


def _read_json(root, name: str):
    return json.loads((root / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def review_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("zlevel-review")
    create(root)
    return root


def test_zlevel_review_package_maps_every_image_and_report_sample(
    review_package,
) -> None:
    summary = _read_json(review_package, "summary.json")
    manifest = _read_json(review_package, "evidence_manifest.json")

    assert summary["technical_image_count"] == 15
    assert summary["montage_count"] == 1
    assert summary["report_count"] == 12
    assert summary["total_file_count"] == 29
    assert len(tuple(review_package.iterdir())) == summary["total_file_count"]
    assert len(manifest["entries"]) == manifest["entry_count"]

    required = {
        "fixture_id",
        "calculation_id",
        "strategy",
        "algorithm_version",
        "payload_version",
        "operation_revision",
        "geometry_fingerprint",
        "tool_fingerprint",
        "assembly_fingerprint",
        "effective_parameter_hash",
        "input_hash",
        "toolpath_ir_hash",
        "safety_report_hash",
        "source_calculation_artifact",
        "generated_timestamp",
        "deterministic_source_record_id",
    }
    entries = manifest["entries"]
    assert all(required <= set(item) for item in entries)
    artifacts = {item["artifact"] for item in entries}
    assert {f"{name}.png" for name in IMAGE_SOURCES} <= artifacts
    assert MONTAGE_NAME in artifacts
    for name in (*summary["technical_images"], summary["montage"]):
        with Image.open(review_package / name) as image:
            assert image.size == ((1200, 800) if name != MONTAGE_NAME else (1200, 800))

    sample_artifacts = set()
    reports = {
        "level_schedule_report.json": ("cases", "case_id"),
        "contact_validation_report.json": ("samples", "sample_id"),
        "contour_topology_report.json": ("fixtures", "fixture_id"),
        "cancellation_report.json": ("checkpoints", "checkpoint"),
        "artifact_lifecycle_report.json": ("cases", "case_id"),
        "safety_integration_report.json": ("fixtures", "fixture_id"),
        "unsupported_cases.json": ("cases", "case_id"),
    }
    for filename, (collection, identity) in reports.items():
        for item in _read_json(review_package, filename)[collection]:
            sample_artifacts.add(f"{filename}#{collection}/{item[identity]}")
    determinism = _read_json(review_package, "determinism_report.json")
    for case in determinism["cases"]:
        for run in case["runs"]:
            sample_artifacts.add(
                "determinism_report.json#cases/"
                f"{case['fixture_id']}/runs/{run['run_index']}"
            )
    performance = _read_json(review_package, "performance_guardrails.json")
    for item in performance["fixtures"]:
        sample_artifacts.add(
            "performance_guardrails.json#fixtures/"
            f"{item['fixture_id']}"
        )
    for item in performance["exceeded_cases"]:
        sample_artifacts.add(
            "performance_guardrails.json#exceeded_cases/"
            f"{item['case_id']}"
        )
    calculations = _read_json(review_package, "calculation_records.json")
    for fixture_id in calculations["records"]:
        sample_artifacts.add(
            f"calculation_records.json#records/{fixture_id}"
        )
    assert sample_artifacts <= artifacts


def test_zlevel_review_reports_contain_numeric_calculation_evidence(
    review_package,
) -> None:
    schedule = _read_json(review_package, "level_schedule_report.json")
    partial = next(
        item for item in schedule["cases"]
        if item["case_id"] == "partial_final_step"
    )
    assert partial["actual_levels"] == [10.0, 7.0, 4.0, 1.0, 0.0]
    assert partial["duplicate_count"] == 0
    assert partial["last_level_residual"] == 0.0
    rejected = {
        item["case_id"]: item["diagnostic_code"]
        for item in schedule["cases"]
        if item["result_status"] == "rejected"
    }
    assert rejected["maximum_level_guardrail"] == "z_level.excessive_level_count"
    assert rejected["zero_stepdown"] == "z_level.invalid_stepdown"

    contact = _read_json(review_package, "contact_validation_report.json")
    assert contact["double_allowance_guard"]["passed"] is True
    positive = next(
        item for item in contact["samples"]
        if item["sample_id"] == "allowance_positive"
    )
    assert positive["contact_semantics"] == "nominal_surface"
    assert positive["allowance_mm"] == 0.5
    assert positive["allowance_deviation_mm"] <= 1.0e-8
    singular = next(
        item for item in contact["samples"]
        if item["sample_id"] == "singular_invalid_normal"
    )
    assert singular["accepted"] is False
    assert singular["diagnostic_code"] == "z_level.singular_normal"

    topology = _read_json(review_package, "contour_topology_report.json")
    cylinder = next(
        item for item in topology["fixtures"]
        if item["fixture_id"] == "cylinder"
    )
    shared = next(
        item for item in topology["fixtures"]
        if item["fixture_id"] == "shared_edge"
    )
    assert cylinder["closed_contour_count"] == len(cylinder["generated_levels"])
    assert cylinder["seam_candidate_count"] == cylinder["seam_dedup_count"] > 0
    assert shared["shared_edge_candidate_count"] == shared["shared_edge_dedup_count"] > 0
    failures = {
        item["fixture_id"]: item["diagnostic_code"]
        for item in topology["fixtures"]
        if item["result_status"] == "rejected"
    }
    assert failures == {
        "branch_open_fail_closed": "z_level.branch_point",
        "self_intersection_fail_closed": "z_level.self_intersection",
    }

    determinism = _read_json(review_package, "determinism_report.json")
    assert determinism["case_count"] == 13
    assert determinism["run_count"] == 39
    assert determinism["all_identical"] is True
    assert all(
        item["run_count"] == 3
        and item["identical_runs"]
        and not item["mismatch_location"]
        for item in determinism["cases"]
    )

    cancellation = _read_json(review_package, "cancellation_report.json")
    assert cancellation["checkpoint_count"] == 16
    assert cancellation["all_cancelled_or_superseded"] is True
    assert all(
        not item["partial_artifact_published"]
        and item["previous_ready_preserved"]
        and item["temporary_state_cleaned"]
        for item in cancellation["checkpoints"]
    )

    lifecycle = _read_json(review_package, "artifact_lifecycle_report.json")
    assert lifecycle["case_count"] == 14
    assert lifecycle["all_passed"] is True
    safety = _read_json(review_package, "safety_integration_report.json")
    statuses = {
        item["fixture_id"]: item["safety_status"]
        for item in safety["fixtures"]
    }
    assert statuses["safe_zlevel"] == "safe"
    assert statuses["cutter_gouge"] == "unsafe"
    assert statuses["shank_collision"] == "unsafe"
    assert statuses["holder_collision"] == "unsafe"
    assert statuses["rapid_link_collision"] == "unsafe"
    assert statuses["holder_missing"] == "unknown"
    assert statuses["holder_invalid"] == "unknown"
    assert statuses["unknown_guardrail"] == "unknown"
    assert all(
        item["ready_gate_decision"]
        == ("allow" if item["safety_status"] == "safe" else "deny")
        for item in safety["fixtures"]
    )

    performance = _read_json(review_package, "performance_guardrails.json")
    assert performance["fixtures"]
    assert all(
        all(
            isinstance(item[name], int)
            for name in (
                "processed_faces",
                "levels",
                "candidate_cells",
                "refined_roots",
                "graph_nodes",
                "graph_edges",
                "contours",
                "segments",
                "points",
                "linking_motions",
                "safety_checks",
                "rejected_samples",
            )
        )
        for item in performance["fixtures"]
    )
    assert {
        item["diagnostic_code"]
        for item in performance["exceeded_cases"]
    } == {"z_level.excessive_level_count", "z_level.excessive_point_count"}

    unsupported = _read_json(review_package, "unsupported_cases.json")
    assert unsupported["case_count"] >= 24
    assert unsupported["all_ready_decisions_denied"] is True
