"""Evidence-contract checks for the Stage 8A.3.2 review package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from tools.create_zlevel_hardening_review_package import (
    IMAGE_NAMES,
    MASTER_RECORD_IDS,
    REPORT_NAMES,
    create,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostic_ids(value: object) -> set[str]:
    if isinstance(value, dict):
        values = {
            item
            for key, item in value.items()
            if key == "diagnostic_id" and isinstance(item, str)
        }
        for item in value.values():
            values.update(_diagnostic_ids(item))
        return values
    if isinstance(value, list):
        values: set[str] = set()
        for item in value:
            values.update(_diagnostic_ids(item))
        return values
    return set()


def test_stage_8a32_review_package_has_specialized_fixture_evidence(
    tmp_path: Path,
) -> None:
    root = create(tmp_path / "CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY")
    summary = _read(root / "summary.json")
    manifest = _read(root / "evidence_manifest.json")
    master = _read(root / "calculation_records.json")
    records = {item["record_id"]: item for item in master["records"]}

    assert summary["technical_image_count"] == 18
    assert summary["montage_count"] == 1
    assert summary["report_count"] == 17
    assert summary["specialized_report_count"] == 16
    assert summary["master_record_count"] == len(MASTER_RECORD_IDS) == 20
    assert summary["evidence_entry_count"] == 18
    assert summary["total_file_count"] == 39
    assert summary["ready_count"] == 3
    assert summary["artifact_published_count"] == 3
    assert len(list(root.iterdir())) == 39
    assert set(records) == set(MASTER_RECORD_IDS)
    assert master["summary"]["ready_count"] == sum(
        bool(item["ready"]) for item in records.values()
    ) == 3
    assert master["summary"]["artifact_published_count"] == sum(
        bool(item["artifact_published"]) for item in records.values()
    ) == 3
    expected_status_counts = {
        status: sum(
            item["safety_status"] == status for item in records.values()
        )
        for status in ("safe", "unsafe", "unknown", "failed")
    }
    assert summary["status_counts"] == expected_status_counts
    assert master["summary"]["status_counts"] == expected_status_counts
    consistency = summary["report_consistency"]
    assert consistency["duplicate_report_content_hashes"] == []
    assert consistency["orphan_manifest_record_ids"] == []
    assert consistency["missing_master_record_ids"] == []

    report_bytes = [(root / name).read_bytes() for name in REPORT_NAMES]
    assert len(set(report_bytes)) == len(REPORT_NAMES)
    for name in REPORT_NAMES:
        report = _read(root / name)
        assert report["report_type"] == name.removesuffix(".json")
        assert report["report_filename"] == name
        assert report["record_count"] == len(report["records"])
        assert report["record_ids"] == [
            item["record_id"] for item in report["records"]
        ]
        assert set(report["record_ids"]) <= set(records)
        source_records = [records[item] for item in report["record_ids"]]
        assert report["summary"]["ready_count"] == sum(
            bool(item["ready"]) for item in source_records
        )
        assert report["summary"]["artifact_published_count"] == sum(
            bool(item["artifact_published"]) for item in source_records
        )
        assert report["summary"]["status_counts"] == {
            status: sum(
                item["safety_status"] == status for item in source_records
            )
            for status in ("safe", "unsafe", "unknown", "failed")
        }
        for item in report["records"]:
            source = records[item["record_id"]]
            assert item.get("status", source["safety_status"]) == source[
                "safety_status"
            ]
            if "ready" in item:
                assert item["ready"] == source["ready"]
            if "artifact_published" in item:
                assert item["artifact_published"] == source[
                    "artifact_published"
                ]
            assert _diagnostic_ids(item) <= _diagnostic_ids(source)
        content_hash = report.pop("content_hash")
        canonical = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert content_hash == hashlib.sha256(canonical).hexdigest()

    direct = records["direct_link_safe"]
    assert direct["safety_status"] == "safe"
    assert direct["linking_decision"] == "direct_safe"
    assert direct["attempted_direct_link"] and direct["attempt_safe"]
    assert any("link.direct" in item for item in direct["motion_segment_provenance"])
    direct_scope = {item["name"]: item["status"] for item in direct["safety_scope"]}
    assert direct_scope["direct_links"] == "CHECKED"

    fallback = records["direct_link_fallback"]
    assert fallback["attempted_direct_link"]
    assert fallback["attempt_status"] == "unsafe"
    assert fallback["attempt_rejection_diagnostics"]
    assert fallback["fallback_selected"]
    assert fallback["linking_decision"] == "direct_rejected_fallback"
    assert {"APPROACH", "CLEARANCE_RAPID", "RETRACT"} <= set(
        fallback["fallback_motion_actions"]
    )
    assert fallback["final_safety_decision"] == "safe"

    expected_motion = {
        "rapid_collision": (
            "rapid",
            "rapid",
            "non_cutting",
            "rapid",
            "z_level.linking.rapid_collision",
        ),
        "approach_collision": (
            "approach",
            "linear",
            "link",
            "approach",
            "z_level.linking.approach_collision",
        ),
        "retract_collision": (
            "retract",
            "linear",
            "retract",
            "retract",
            "z_level.linking.retract_collision",
        ),
    }
    for fixture_id, (
        motion_kind,
        event_kind,
        motion_class,
        token,
        diagnostic_code,
    ) in expected_motion.items():
        record = records[fixture_id]
        assert record["actual_motion_kind"] == motion_kind
        assert record["actual_motion_class"] == motion_class
        assert motion_kind in record["motion_kinds"]
        assert event_kind in record["event_kinds"]
        assert token in record["actual_motion_provenance"]
        assert record["diagnostics"][0]["code"] == diagnostic_code
        assert token in record["diagnostics"][0]["code"]

    absent = records["holder_absent_scope"]
    missing = records["holder_not_provided_unknown"]
    invalid = records["holder_invalid_unknown"]
    assert absent["holder_state"] == "declared_absent"
    assert missing["holder_state"] == "missing"
    assert invalid["holder_state"] == "reference_invalid"
    assert absent["safety_status"] == "safe"
    assert missing["diagnostics"][0]["code"] == "z_level.safety.holder_not_provided"
    assert invalid["diagnostics"][0]["code"] == "z_level.safety.invalid_holder"
    holder_scope = {
        item["name"]: item["status"] for item in invalid["safety_scope"]
    }
    assert holder_scope["holder"] == "INVALID"

    hole = records["inner_hole_link_rejected"]
    assert hole["diagnostics"][0]["code"] == "z_level.safety.hole_crossing"
    assert any("link.direct" in item for item in hole["motion_segment_provenance"])
    assert hole["candidate_linking_decision"] == "direct_candidate"
    assert hole["attempted_direct_link"]
    assert hole["boundary_hole_result"] == "unsafe"
    assert hole["final_linking_decision"] == "rejected_fail_closed"
    assert hole["linking_decision"] == "rejected_fail_closed"
    assert not hole["fallback_selected"]
    assert hole["final_safety_decision"] == "unsafe"
    assert hole["publish_decision"] == "rejected"
    assert not hole["ready"]
    assert not hole["artifact_published"]
    boundary_hole = _read(root / "boundary_hole_report.json")
    hole_projection = next(
        item
        for item in boundary_hole["records"]
        if item["record_id"] == "inner_hole_link_rejected"
    )
    assert hole_projection["candidate_linking_decision"] == "direct_candidate"
    assert hole_projection["final_linking_decision"] == "rejected_fail_closed"
    assert hole_projection["final_safety_decision"] == "unsafe"
    assert hole_projection["publish_decision"] == "rejected"
    assert records["boundary_escape"]["artifact_published"] is False
    assert records["pathological_topology"]["diagnostics"][0]["code"] == (
        "z_level.geometry.unsupported_topology"
    )

    cutter_report = _read(root / "cutter_gouge_report.json")
    for record_id in ("concave_cutter_gouge", "neighbor_face_gouge"):
        projected = next(
            item
            for item in cutter_report["records"]
            if item["record_id"] == record_id
        )
        assert projected["diagnostics"]
        source_ids = set(records[record_id]["diagnostic_ids"])
        for diagnostic in projected["diagnostics"]:
            assert diagnostic["diagnostic_id"] in source_ids
            assert diagnostic["component"] == "cutter"
            assert diagnostic["candidate_geometry"]
            assert diagnostic["motion_id"]
            assert diagnostic["motion_provenance"]
            assert diagnostic["broad_phase_result"]
            assert diagnostic["narrow_phase_result"]
            assert diagnostic["occurrence_count"] > 0
            assert diagnostic["minimum_clearance_mm"] is not None
            assert diagnostic["maximum_penetration_mm"] is not None
            assert diagnostic["exact_conservative_status"] in {
                "exact",
                "conservative",
            }

    artifact_report = _read(root / "artifact_hash_report.json")
    assert artifact_report["record_count"] == len(MASTER_RECORD_IDS)
    for item in artifact_report["records"]:
        assert item["artifact_hash_verified"]
        assert item["expected_artifact_hash"] == item["actual_artifact_hash"]
        if item["toolpath_ir_hash"] is not None:
            assert item["artifact_hash_matches_toolpath_ir"] is False
            assert item["actual_artifact_hash"] != item["toolpath_ir_hash"]
        assert set(item["canonical_fields"]) >= {
            "strategy",
            "algorithm_version",
            "payload_version",
            "operation_revision",
            "selected_face_fingerprints",
            "machining_frame",
            "effective_parameters",
            "effective_parameter_hash",
            "tool_fingerprint",
            "shank_fingerprint",
            "holder_fingerprint",
            "holder_state",
            "assembly_fingerprint",
            "protected_geometry_fingerprints",
            "stock_fingerprint",
            "fixture_fingerprints",
            "safety_scope",
            "safety_scope_hash",
            "toolpath_ir_hash",
            "safety_report_hash",
            "machine_ready_clearance_verified",
        }

    invalidation = _read(root / "invalidation_report.json")["records"][0]
    assert len(invalidation["mutation_matrix"]) >= 13
    changed = [
        item
        for item in invalidation["mutation_matrix"]
        if item["mutation"] != "identical_input"
    ]
    identical = next(
        item
        for item in invalidation["mutation_matrix"]
        if item["mutation"] == "identical_input"
    )
    assert all(item["hash_changed"] for item in changed)
    assert all(item["artifact_stale"] for item in changed)
    assert all(not item["ready_after_mutation"] for item in changed)
    assert not identical["hash_changed"]
    assert not identical["artifact_stale"]
    assert identical["ready_after_mutation"]

    ready = next(
        item
        for item in _read(root / "ready_gate_report.json")["records"]
        if item["record_id"] == "ready_gate_matrix"
    )
    assert len(ready["ready_matrix"]) == 17
    assert all(item["pass"] for item in ready["ready_matrix"])
    assert sum(item["actual_ready"] for item in ready["ready_matrix"]) == 1
    assert ready["ready_matrix_rendered_cases"] == [
        item["case"] for item in ready["ready_matrix"]
    ]

    determinism = _read(root / "determinism_report.json")
    assert determinism["record_count"] == 18
    assert all(item["identical"] for item in determinism["records"])
    assert all(
        len(item["runs"]) == 3
        and all(run["actual_execution"] for run in item["runs"])
        for item in determinism["records"]
    )

    cancellation = _read(root / "cancellation_latest_wins_report.json")["records"][0]
    checkpoints = {
        item["checkpoint"] for item in cancellation["checkpoint_cases"]
    }
    assert {
        "safety_scope_preparation",
        "cutter_narrow_phase",
        "holder_narrow_phase",
        "swept_subdivision",
        "artifact_hash",
        "before_publish",
        "project_close",
    } <= checkpoints
    assert all(
        not item["partial_toolpath_published"]
        and not item["partial_safety_report_published"]
        and not item["db_committed"]
        for item in cancellation["checkpoint_cases"]
    )

    aggregation = _read(root / "collision_aggregation_report.json")
    assert any(
        item["raw_collision_occurrences"] > item["final_group_count"]
        for item in aggregation["records"]
    )
    assert all(item["aggregation_hash"] for item in aggregation["records"])
    multi_group = next(
        item
        for item in aggregation["records"]
        if item["record_id"] == "collision_aggregation"
    )
    assert multi_group["final_group_count"] >= 2
    assert len(multi_group["final_groups"]) == multi_group["final_group_count"]
    assert all(
        item["first_occurrence"]
        and item["last_occurrence"]
        and item["representative_point"] is not None
        and item["severity"] == "unsafe"
        for item in multi_group["final_groups"]
    )
    topology = _read(root / "pathological_topology_report.json")
    seam = next(
        item for item in topology["records"]
        if item["record_id"] == "seam_shared_edge_safety"
    )
    assert not seam["double_count_detected"]
    assert seam["raw_seam_candidates"] > seam["unique_shared_edges_after_dedup"]
    assert seam["duplicate_collision_candidates_before_dedup"] > 0
    assert seam["shared_edge_candidate_count"] >= 2
    assert len(seam["face_provenance"]) == 2
    assert all(
        len(item["face_provenance"]) == 2
        for item in seam["shared_edge_candidates"]
    )

    guardrails = _read(root / "performance_guardrails.json")
    assert all(item["declared_limits"] for item in guardrails["records"])
    assert all(not item["exceeded_fixture"]["ready"] for item in guardrails["records"])
    exceeded = guardrails["records"][0]["exceeded_fixture"]
    assert exceeded["source"].startswith("actual_production_safety_run")
    assert exceeded["actual_value"] > exceeded["limit_value"]
    assert exceeded["diagnostic"]["code"] == "z_level.safety.excessive_checks"
    assert exceeded["status"] in {"unknown", "failed"}
    unsupported = _read(root / "unsupported_cases.json")
    unsupported_rows = [
        row
        for item in unsupported["records"]
        for row in item["unsupported"]
    ]
    assert unsupported_rows
    assert all(row["status"] == "unsupported" and not row["ready"] for row in unsupported_rows)
    assert {
        "flat_end",
        "bull_nose",
        "tapered_tool",
        "five_axis",
        "3_plus_2",
        "undercut",
        "invalid_holder_reference",
        "missing_required_protected_geometry",
        "excessive_safety_checks",
        "unsupported_topology",
        "production_post",
        "machine_ready_clearance_certification",
    } <= {row["case"] for row in unsupported_rows}

    assert len(manifest["entries"]) == 18
    png_hashes = set()
    for entry in manifest["entries"]:
        png = root / entry["artifact"]
        assert entry["png_sha256"] == hashlib.sha256(png.read_bytes()).hexdigest()
        png_hashes.add(entry["png_sha256"])
        report = _read(root / entry["source_report"])
        assert entry["report_record_id"] in report["record_ids"]
        assert entry["source_report_record"].endswith(
            f"#records/{entry['report_record_id']}"
        )
        assert entry["render_data_hash"]
        assert entry["source_calculation_id"] == entry["calculation_id"]
        assert entry["source_safety_report_hash"] == entry["safety_report_hash"]
    montage_path = (
        root / "CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY_MONTAGE.png"
    )
    png_hashes.add(hashlib.sha256(montage_path.read_bytes()).hexdigest())
    assert len(png_hashes) == 19

    review_index = (root / "REVIEW_INDEX.md").read_text(encoding="utf-8")
    indexed_files = (
        *REPORT_NAMES,
        "summary.json",
        "evidence_manifest.json",
    )
    assert all(f"`{name}`" in review_index for name in indexed_files)
    assert review_index.count("- Mục đích:") == 19
    assert review_index.count("- Record scope:") == 19
    assert review_index.count("- Invariant chính:") == 19
    assert review_index.count("- Quan hệ master:") == 19

    for stem in IMAGE_NAMES:
        with Image.open(root / f"{stem}.png") as image:
            assert image.size == (1200, 800)
    with Image.open(montage_path) as image:
        assert image.size == (1200, 800)
