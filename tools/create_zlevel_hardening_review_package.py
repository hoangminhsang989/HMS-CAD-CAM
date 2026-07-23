"""Create the calculation-backed Stage 8A.3.2 review package.

The package is Git-ignored evidence.  Every image and report is derived from
an actual Z-Level candidate, safety report, lifecycle result, or an explicit
fail-closed fixture.  The specialized reports intentionally have different
schemas and record projections; ``calculation_records.json`` is the only
master record source.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from hms_cadcam.cam.cam3d.zlevel import (  # noqa: E402
    Z_LEVEL_FINISHING_ALGORITHM_VERSION,
    Z_LEVEL_FINISHING_STRATEGY_KEY,
    Z_LEVEL_FINISHING_STRATEGY_VERSION,
    ZLevelFinishingError,
    ZLevelFinishingGenerator,
    ZLevelFinishingParameters,
    ZLevelLinkingMode,
    ZLevelScopeStatus,
    build_z_level_safety_policy,
    build_machining_frame,
    calculate_and_publish_z_level_finishing,
    validate_z_level_candidate_safety,
    z_level_artifact_contract_hash,
    z_level_artifact_has_safe_contract,
)
from hms_cadcam.cam.cam3d.zlevel.service import _build_candidate_toolpath  # noqa: E402
from hms_cadcam.cam.domain.revision import ContentFingerprint  # noqa: E402
from hms_cadcam.cam.toolpath.events import MotionClass  # noqa: E402
from hms_cadcam.cam.cam3d.parallel.safety_models import ParallelSafetyStatus  # noqa: E402
from tests.unit._parallel_finishing_fixtures import (  # noqa: E402
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import (  # noqa: E402
    _protected_fixture,
    holder_collision_fixture,
    rapid_crossing_fixture,
    shank_collision_fixture,
)
from tests.unit.test_parallel_finishing_safety import _single_motion_artifact  # noqa: E402
from tests.unit.test_z_level_foundation import _zlevel_operation  # noqa: E402

OUTPUT_RELATIVE = Path(
    "reference_private/DERIVED/CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY"
)
GENERATED_TIMESTAMP = "2026-07-23T00:00:00+07:00"

IMAGE_NAMES = (
    "zlevel_safe_vertical_wall",
    "zlevel_concave_cutter_gouge",
    "zlevel_neighbor_face_gouge",
    "zlevel_inner_hole_link_rejected",
    "zlevel_boundary_escape",
    "zlevel_shank_collision",
    "zlevel_holder_collision",
    "zlevel_holder_absent_scope",
    "zlevel_holder_invalid_unknown",
    "zlevel_direct_link_safe",
    "zlevel_direct_link_fallback",
    "zlevel_rapid_collision",
    "zlevel_approach_collision",
    "zlevel_retract_collision",
    "zlevel_seam_shared_edge_safety",
    "zlevel_collision_aggregation",
    "zlevel_safety_hash_invalidation",
    "zlevel_ready_gate_matrix",
)

MASTER_RECORD_IDS = (
    "safe_vertical_wall",
    "concave_cutter_gouge",
    "neighbor_face_gouge",
    "inner_hole_link_rejected",
    "boundary_escape",
    "shank_collision",
    "holder_collision",
    "holder_absent_scope",
    "holder_not_provided_unknown",
    "holder_invalid_unknown",
    "direct_link_safe",
    "direct_link_fallback",
    "rapid_collision",
    "approach_collision",
    "retract_collision",
    "seam_shared_edge_safety",
    "pathological_topology",
    "collision_aggregation",
    "safety_hash_invalidation",
    "ready_gate_matrix",
)
IMAGE_RECORD_IDS = tuple(
    item
    for item in MASTER_RECORD_IDS
    if item not in {"holder_not_provided_unknown", "pathological_topology"}
)

REPORT_NAMES = (
    "safety_scope_report.json",
    "cutter_gouge_report.json",
    "shank_holder_report.json",
    "swept_motion_report.json",
    "linking_safety_report.json",
    "boundary_hole_report.json",
    "pathological_topology_report.json",
    "collision_aggregation_report.json",
    "ready_gate_report.json",
    "artifact_hash_report.json",
    "invalidation_report.json",
    "determinism_report.json",
    "cancellation_latest_wins_report.json",
    "performance_guardrails.json",
    "unsupported_cases.json",
    "calculation_records.json",
    "review_metrics.json",
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    fixture_id: str
    calculation_id: str
    status: str
    input_hash: str
    toolpath_ir_hash: str | None
    safety_report_hash: str | None
    artifact_hash: str | None
    result: Any | None
    source_record: dict[str, Any]
    rerun: Callable[[], "CaseResult"] | None = None


def _repeatable(factory: Callable[[], CaseResult]) -> CaseResult:
    return replace(factory(), rerun=factory)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture_hole():
    vertices = (
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
        (3.0, 3.0, 0.0),
        (7.0, 3.0, 0.0),
        (7.0, 7.0, 0.0),
        (3.0, 7.0, 0.0),
    )
    triangles = (
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    )
    return parallel_fixture((("trimmed-hole", vertices, triangles),))


def _fixture_two_loops():
    """One selected face with two far-apart loops and one source region.

    This is the positive direct-link fixture.  The loops share the selected
    face provenance, the direct swept link stays outside both loops, and the
    production service publishes a real ``link.direct`` motion.
    """

    vertices = (
        (0.0, 0.0, 0.0),
        (4.0, 0.0, 0.0),
        (4.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
        (20.0, 0.0, 0.0),
        (24.0, 0.0, 0.0),
        (24.0, 10.0, 0.0),
        (20.0, 10.0, 0.0),
    )
    triangles = (
        (0, 1, 2),
        (0, 2, 3),
        (4, 5, 6),
        (4, 6, 7),
    )
    return parallel_fixture((("two-safe-loops", vertices, triangles),), stepover=2.0)


def _fixture_cylinder():
    count = 12
    surfaces = []
    for name, start in (("cylinder-side-a", 0), ("cylinder-side-b", count // 2)):
        vertices = tuple(
            point
            for offset in range(count // 2 + 1)
            for point in (
                (
                    5.0 * math.cos((start + offset) * math.tau / count),
                    5.0 * math.sin((start + offset) * math.tau / count),
                    0.0,
                ),
                (
                    5.0 * math.cos((start + offset) * math.tau / count),
                    5.0 * math.sin((start + offset) * math.tau / count),
                    10.0,
                ),
            )
        )
        triangles = tuple(
            triangle
            for index in range(count // 2)
            for triangle in (
                (index * 2, (index + 1) * 2, (index + 1) * 2 + 1),
                (index * 2, (index + 1) * 2 + 1, index * 2 + 1),
            )
        )
        surfaces.append((name, vertices, triangles))
    return parallel_fixture(tuple(surfaces))


def _fixture_pathological_topology():
    return parallel_fixture(
        (
            (
                "repeated-edge-nonmanifold",
                (
                    (0.0, 0.0, 0.0),
                    (10.0, 0.0, 0.0),
                    (10.0, 10.0, 0.0),
                ),
                ((0, 1, 2), (0, 1, 2), (0, 1, 2)),
            ),
        )
    )


def _marker_metadata(artifact: Any) -> dict[str, str]:
    if artifact is None:
        return {}
    for event in artifact.events:
        if getattr(event, "semantic_key", None) == "z_level.safety.contract":
            return dict(getattr(event, "metadata", ()))
    return {}


def _motion_records(artifact: Any) -> list[dict[str, Any]]:
    if artifact is None:
        return []
    values: list[dict[str, Any]] = []
    for index, event in enumerate(artifact.events):
        motion_class = getattr(getattr(event, "motion_class", None), "value", None)
        if motion_class is None:
            continue
        event_kind = getattr(getattr(event, "kind", None), "value", None)
        provenance = event.provenance
        motion_kind = (
            "rapid"
            if event_kind == "rapid"
            else "approach"
            if "approach" in provenance
            else "retract"
            if "retract" in provenance
            else "direct_link"
            if "link.direct" in provenance
            else motion_class
        )
        values.append(
            {
                "motion_id": f"motion-{index:04d}",
                "event_index": index,
                "motion_kind": motion_kind,
                "event_kind": event_kind,
                "motion_class": motion_class,
                "provenance": provenance,
                "start": event.start.position.to_dict(),
                "end": event.end.position.to_dict(),
            }
        )
    return values


def _diagnostic_records(
    calculation_id: str,
    safety: Any,
) -> list[dict[str, Any]]:
    if safety is None:
        return []
    values: list[dict[str, Any]] = []
    for index, diagnostic in enumerate(safety.diagnostics):
        value = diagnostic.to_dict()
        value["diagnostic_id"] = f"{calculation_id}:diagnostic:{index:03d}"
        values.append(value)
    return values


def _failure_hash(fixture_id: str, code: str, message: str) -> str:
    return _sha256(
        {
            "fixture_id": fixture_id,
            "state": "not_published",
            "diagnostic_code": code,
            "message": message,
            "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        }
    )


def _finalize_linking_record(record: dict[str, Any]) -> None:
    """Separate the direct-link candidate from the final publish decision."""

    safety_report_decision = record.get("linking_decision", "not_published")
    attempted_direct_link = bool(
        record.get("attempted_direct_link")
        or any(
            "link.direct" in item.get("provenance", "")
            for item in (
                *record.get("motions", []),
                *record.get("attempt_motions", []),
            )
        )
    )
    if attempted_direct_link or safety_report_decision in {
        "direct_safe",
        "direct_rejected_fallback",
    }:
        candidate_decision = "direct_candidate"
    elif safety_report_decision == "retract_clearance":
        candidate_decision = "retract_clearance_candidate"
    else:
        candidate_decision = safety_report_decision

    status = record.get("safety_status", "failed")
    if (
        attempted_direct_link
        and safety_report_decision == "direct_safe"
        and status != "safe"
    ):
        final_decision = "rejected_fail_closed"
    else:
        final_decision = safety_report_decision
    diagnostic_codes = [
        item.get("code", "") for item in record.get("diagnostics", [])
    ]
    has_boundary_hole_result = any(
        "hole" in code or "boundary" in code for code in diagnostic_codes
    )
    record.update(
        {
            "safety_report_linking_decision": safety_report_decision,
            "candidate_linking_decision": candidate_decision,
            "attempted_direct_link": attempted_direct_link,
            "boundary_hole_result": (
                status if has_boundary_hole_result else "not_applicable"
            ),
            "final_linking_decision": final_decision,
            "linking_decision": final_decision,
            "fallback_selected": bool(
                record.get("fallback_selected")
                or safety_report_decision == "direct_rejected_fallback"
            ),
            "final_safety_decision": status,
            "publish_decision": (
                "published"
                if record.get("artifact_published")
                else "rejected"
            ),
        }
    )


def _record_from_run(
    *,
    fixture_id: str,
    calculation_id: str,
    fixture: Any,
    parameters: ZLevelFinishingParameters,
    computing: Any,
    candidate_artifact: Any | None,
    safety: Any | None,
    published_artifact: Any | None = None,
    accepted: bool = False,
    lifecycle_status: str | None = None,
    extra: dict[str, Any] | None = None,
) -> CaseResult:
    artifact = published_artifact or candidate_artifact
    metadata = _marker_metadata(published_artifact)
    toolpath_hash = (
        metadata.get("toolpath_ir_hash")
        or artifact.artifact_fingerprint.digest
        if artifact is not None and artifact.artifact_fingerprint is not None
        else None
    )
    expected_contract_hash = None
    if safety is not None and candidate_artifact is not None:
        expected_contract_hash = z_level_artifact_contract_hash(
            operation=computing.operation,
            context=computing.context,
            parameters=parameters,
            tool=computing.tool,
            assembly=computing.assembly,
            holder=computing.holder,
            candidate_artifact=candidate_artifact,
            safety_report=safety,
        ).digest
    contract_hash = metadata.get("artifact_contract_hash")
    if contract_hash is None:
        contract_hash = expected_contract_hash
    if contract_hash is None:
        contract_hash = _failure_hash(
            fixture_id,
            "z_level.not_published",
            "No Z-Level artifact was published.",
        )
    if expected_contract_hash is None:
        expected_contract_hash = contract_hash
    safety_hash = safety.fingerprint.digest if safety is not None else None
    diagnostics = _diagnostic_records(calculation_id, safety)
    motion_records = _motion_records(artifact)
    mesh = computing.context.calculation_mesh
    selected_geometry_ids = sorted(
        str(item.geometry.reference_id)
        for item in computing.context.machining_zone.part_surfaces.selection.surfaces
    )
    selected_face_fingerprints = [
        item.fingerprint.to_dict()
        for item in sorted(
            computing.context.machining_zone.part_surfaces.selection.surfaces,
            key=lambda value: value.fingerprint.digest,
        )
    ]
    protected_geometry_fingerprints = [
        item.fingerprint.to_dict()
        for item in sorted(
            computing.context.machining_zone.all_surfaces(),
            key=lambda value: value.fingerprint.digest,
        )
    ]
    fixture_fingerprints = (
        [
            item.fingerprint.to_dict()
            for item in sorted(
                computing.context.machining_zone.fixture_surfaces.selection.surfaces,
                key=lambda value: value.fingerprint.digest,
            )
        ]
        if computing.context.machining_zone.fixture_surfaces is not None
        else []
    )
    protected_geometry_ids = sorted(
        str(item.geometry.reference_id)
        for surface_set in (
            computing.context.machining_zone.check_surfaces,
            computing.context.machining_zone.fixture_surfaces,
        )
        if surface_set is not None
        for item in surface_set.selection.surfaces
    )
    record: dict[str, Any] = {
        "record_id": fixture_id,
        "fixture_id": fixture_id,
        "fixture_result_id": fixture_id,
        "calculation_id": calculation_id,
        "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
        "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
        "input_hash": computing.input_fingerprint.digest,
        "toolpath_ir_hash": toolpath_hash,
        "safety_report_hash": safety_hash,
        "safety_scope_hash": safety.scope_fingerprint.digest if safety is not None else None,
        "artifact_hash": contract_hash,
        "expected_artifact_hash": expected_contract_hash,
        "actual_artifact_hash": contract_hash,
        "artifact_hash_verified": expected_contract_hash == contract_hash,
        "artifact_hash_source": (
            "z_level.safety.contract.metadata"
            if metadata.get("artifact_contract_hash")
            else "canonical_contract_calculation"
        ),
        "artifact_hash_matches_toolpath_ir": (
            contract_hash == toolpath_hash if toolpath_hash is not None else False
        ),
        "artifact_contract_fields": metadata,
        "published_artifact_fingerprint": (
            published_artifact.artifact_fingerprint.digest
            if published_artifact is not None
            and published_artifact.artifact_fingerprint is not None
            else None
        ),
        "operation_revision": computing.operation.revision.to_dict(),
        "selected_face_fingerprints": selected_face_fingerprints,
        "machining_frame": build_machining_frame(
            computing.context,
            parameters,
        ).to_dict(),
        "effective_parameters": parameters.to_operation_parameters().to_dict(),
        "geometry_fingerprint": (
            computing.context.geometry_snapshot.geometry_fingerprint.digest
        ),
        "tool_fingerprint": computing.tool.content_fingerprint.digest,
        "shank_fingerprint": ContentFingerprint.from_payload(
            computing.tool.shank.to_dict()
        ).digest,
        "holder_fingerprint": (
            computing.holder.content_fingerprint.digest
            if computing.holder is not None
            else "not_present"
        ),
        "assembly_fingerprint": computing.assembly.content_fingerprint.digest,
        "effective_parameter_hash": parameters.fingerprint.digest,
        "protected_geometry_fingerprints": protected_geometry_fingerprints,
        "stock_fingerprint": None,
        "fixture_fingerprints": fixture_fingerprints,
        "holder_state": safety.holder_state if safety is not None else "not_published",
        "safety_status": safety.status.value if safety is not None else "failed",
        "safety_scope": (
            [item.to_dict() for item in safety.safety_scope]
            if safety is not None
            else []
        ),
        "diagnostics": diagnostics,
        "diagnostic_ids": [item["diagnostic_id"] for item in diagnostics],
        "geometry_ids": sorted(
            {
                item["candidate_geometry"]
                for item in diagnostics
                if item.get("candidate_geometry")
            }
        ),
        "geometry_mesh": {
            "vertices": [item.to_dict() for item in mesh.vertices],
            "triangles": [list(item) for item in mesh.triangle_indices],
            "triangle_sources": [str(item) for item in mesh.triangle_sources],
            "selected_geometry_ids": selected_geometry_ids,
            "protected_geometry_ids": protected_geometry_ids,
        },
        "motions": motion_records,
        "motion_ids": [item["motion_id"] for item in motion_records],
        "motion_segment_provenance": [
            item["provenance"] for item in motion_records
        ],
        "motion_classes": sorted(
            {item["motion_class"] for item in motion_records}
        ),
        "motion_kinds": sorted(
            {
                item["motion_kind"]
                for item in motion_records
                if item.get("motion_kind")
            }
        ),
        "event_kinds": sorted(
            {
                item["event_kind"]
                for item in motion_records
                if item.get("event_kind")
            }
        ),
        "motion_count": safety.statistics.motion_count if safety is not None else 0,
        "swept_subdivisions": (
            safety.statistics.swept_subdivisions if safety is not None else 0
        ),
        "statistics": safety.statistics.to_dict() if safety is not None else {},
        "linking_decision": (
            safety.linking_decision if safety is not None else "not_published"
        ),
        "accepted": accepted,
        "artifact_published": published_artifact is not None,
        "ready": bool(
            published_artifact is not None
            and z_level_artifact_has_safe_contract(published_artifact)
        ),
        "machine_ready_clearance_verified": False,
        "lifecycle_status": lifecycle_status
        or ("READY" if accepted else "CANDIDATE"),
        "generated_timestamp": GENERATED_TIMESTAMP,
    }
    if extra:
        record.update(extra)
    _finalize_linking_record(record)
    return CaseResult(
        fixture_id,
        calculation_id,
        safety.status.value if safety is not None else "failed",
        computing.input_fingerprint.digest,
        toolpath_hash,
        safety_hash,
        contract_hash,
        (
            (SimpleNamespace(preview=None), safety, artifact)
            if safety is None
            else (SimpleNamespace(preview=None), safety, artifact)
        ),
        record,
    )


def _run_case(
    fixture_id: str,
    fixture: Any,
    *,
    holder: Any = None,
    safety_holder: Any = ...,
    parameters: ZLevelFinishingParameters | None = None,
    required_protected: bool = False,
    extra: dict[str, Any] | None = None,
) -> CaseResult:
    parameters = parameters or ZLevelFinishingParameters(
        fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
    )
    operation = _zlevel_operation(fixture, parameters)
    generator = ZLevelFinishingGenerator()
    calculation_seed = ContentFingerprint.from_payload(
        {
            "fixture_id": fixture_id,
            "geometry": fixture.context.geometry_snapshot.geometry_fingerprint.digest,
            "parameters": parameters.to_operation_parameters().to_dict(),
            "algorithm": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        }
    ).digest
    calculation_id = f"zlevel-v2:{fixture_id}:{calculation_seed[:16]}"
    try:
        inputs = generator.resolve_inputs(
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            holder=holder,
        )
        computing, token = generator.begin(inputs)
        candidate = generator.generate(computing)
        actual_holder = holder if safety_holder is ... else safety_holder
        linking_decision = (
            "direct_safe"
            if parameters.linking_mode is ZLevelLinkingMode.CONSERVATIVE_DIRECT
            else "retract_clearance"
        )
        safety = validate_z_level_candidate_safety(
            operation=computing.operation,
            context=computing.context,
            tool=computing.tool,
            assembly=computing.assembly,
            holder=actual_holder,
            artifact=candidate.artifact,
            preview=candidate.preview,
            linking_decision=linking_decision,
            protected_geometry_required=required_protected,
        )
        record_case = _record_from_run(
            fixture_id=fixture_id,
            calculation_id=calculation_id,
            fixture=fixture,
            parameters=parameters,
            computing=computing,
            candidate_artifact=candidate.artifact,
            safety=safety,
            extra=extra,
        )
        record_case.source_record["preview_pass_count"] = len(candidate.preview.passes)
        record_case.source_record["preview_contour_count"] = (
            candidate.preview.statistics.contour_count
        )
        record_case.source_record["calculation_result"] = (
            "safe" if safety.status is ParallelSafetyStatus.SAFE else "not_published"
        )
        record_case = CaseResult(
            record_case.fixture_id,
            record_case.calculation_id,
            record_case.status,
            record_case.input_hash,
            record_case.toolpath_ir_hash,
            record_case.safety_report_hash,
            record_case.artifact_hash,
            (candidate, safety, candidate.artifact),
            record_case.source_record,
        )
        return record_case
    except (ZLevelFinishingError, ValueError) as error:
        code = getattr(getattr(error, "code", None), "value", "z_level.safety.unknown")
        message = str(error)
        rejection_hash = _failure_hash(fixture_id, code, message)
        record = {
            "record_id": fixture_id,
            "fixture_id": fixture_id,
            "fixture_result_id": fixture_id,
            "calculation_id": calculation_id,
            "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
            "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
            "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
            "input_hash": calculation_seed,
            "toolpath_ir_hash": None,
            "safety_report_hash": None,
            "safety_scope_hash": None,
            "artifact_hash": rejection_hash,
            "expected_artifact_hash": rejection_hash,
            "actual_artifact_hash": rejection_hash,
            "artifact_hash_verified": True,
            "artifact_hash_source": "not_published_rejection",
            "artifact_hash_matches_toolpath_ir": False,
            "artifact_contract_fields": {},
            "operation_revision": fixture.operation.revision.to_dict(),
            "geometry_fingerprint": fixture.context.geometry_snapshot.geometry_fingerprint.digest,
            "tool_fingerprint": fixture.tool.content_fingerprint.digest,
            "assembly_fingerprint": fixture.assembly.content_fingerprint.digest,
            "effective_parameter_hash": parameters.fingerprint.digest,
            "holder_state": "not_published",
            "safety_status": "failed",
            "safety_scope": [],
            "diagnostics": [
                {
                    "diagnostic_id": f"{calculation_id}:diagnostic:000",
                    "code": code,
                    "message": message,
                    "status": "failed",
                }
            ],
            "diagnostic_ids": [f"{calculation_id}:diagnostic:000"],
            "geometry_ids": [],
            "geometry_mesh": {
                "vertices": [
                    item.to_dict()
                    for item in fixture.context.calculation_mesh.vertices
                ],
                "triangles": [
                    list(item)
                    for item in fixture.context.calculation_mesh.triangle_indices
                ],
                "triangle_sources": [
                    str(item)
                    for item in fixture.context.calculation_mesh.triangle_sources
                ],
                "selected_geometry_ids": sorted(
                    str(item.geometry.reference_id)
                    for item in fixture.zone.part_surfaces.selection.surfaces
                ),
                "protected_geometry_ids": sorted(
                    str(item.geometry.reference_id)
                    for surface_set in (
                        fixture.zone.check_surfaces,
                        fixture.zone.fixture_surfaces,
                    )
                    if surface_set is not None
                    for item in surface_set.selection.surfaces
                ),
            },
            "motions": [],
            "motion_ids": [],
            "motion_segment_provenance": [],
            "motion_classes": [],
            "motion_kinds": [],
            "event_kinds": [],
            "motion_count": 0,
            "swept_subdivisions": 0,
            "statistics": {},
            "linking_decision": "not_published",
            "accepted": False,
            "artifact_published": False,
            "ready": False,
            "machine_ready_clearance_verified": False,
            "lifecycle_status": "FAILED",
            "generated_timestamp": GENERATED_TIMESTAMP,
            "error_class": type(error).__name__,
            "message": message,
        }
        if extra:
            record.update(extra)
        _finalize_linking_record(record)
        return CaseResult(
            fixture_id,
            calculation_id,
            "failed",
            calculation_seed,
            None,
            None,
            rejection_hash,
            None,
            record,
        )


def _direct_attempt(
    fixture_id: str,
    fixture: Any,
    parameters: ZLevelFinishingParameters,
    *,
    holder: Any = None,
) -> dict[str, Any]:
    """Run the direct-link candidate once to preserve rejection evidence."""

    generator = ZLevelFinishingGenerator()
    inputs = generator.resolve_inputs(
        _zlevel_operation(fixture, parameters),
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder,
    )
    computing, token = generator.begin(inputs)
    candidate = generator.generate(computing)
    direct_artifact = _build_candidate_toolpath(
        computing,
        token,
        candidate.preview,
        safety_report=None,
        cancellation=None,
    )
    safety = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=direct_artifact,
        preview=candidate.preview,
        linking_decision="direct_safe",
    )
    diagnostics = _diagnostic_records(
        f"zlevel-v2:{fixture_id}:direct-attempt",
        safety,
    )
    attempt_motions = _motion_records(direct_artifact)
    return {
        "attempted_direct_link": True,
        "attempt_status": safety.status.value,
        "attempt_linking_decision": safety.linking_decision,
        "attempt_motion_ids": [
            item["motion_id"] for item in attempt_motions
            if "direct" in item["provenance"]
        ],
        "attempt_motion_provenance": [
            item["provenance"] for item in attempt_motions
            if "direct" in item["provenance"]
        ],
        "attempt_motions": [
            item for item in attempt_motions
            if "direct" in item["provenance"]
        ],
        "attempt_diagnostic_ids": [
            item["diagnostic_id"] for item in diagnostics
        ],
        "attempt_rejection_diagnostics": diagnostics,
        "attempt_rejected_components": sorted(
            {
                item["component"]
                for item in diagnostics
                if item.get("component")
            }
        ),
        "attempt_safety_report_hash": safety.fingerprint.digest,
        "attempt_toolpath_ir_hash": direct_artifact.artifact_fingerprint.digest,
        "attempt_safe": safety.status is ParallelSafetyStatus.SAFE,
    }


def _run_published_case(
    fixture_id: str,
    fixture: Any,
    parameters: ZLevelFinishingParameters,
    *,
    holder: Any = None,
    direct_attempt: bool = False,
    extra: dict[str, Any] | None = None,
) -> CaseResult:
    operation = _zlevel_operation(fixture, parameters)
    attempt = (
        _direct_attempt(fixture_id, fixture, parameters, holder=holder)
        if direct_attempt
        else {}
    )
    with tempfile.TemporaryDirectory(prefix="hms-zlevel-review-") as temporary:
        project_root = Path(temporary) / f"{fixture_id}.HMS"
        project_root.mkdir()
        result = calculate_and_publish_z_level_finishing(
            project_root,
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            holder=holder,
        )
    if result.artifact is not None and result.safety_report is not None:
        generator = ZLevelFinishingGenerator()
        inputs = generator.resolve_inputs(
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            holder=holder,
        )
        computing, _token = generator.begin(inputs)
        rebuilt_candidate = generator.generate(computing)
        rebuilt_artifact = rebuilt_candidate.artifact
        if result.safety_report.linking_decision == "direct_rejected_fallback":
            rebuilt_artifact = _build_candidate_toolpath(
                computing,
                _token,
                rebuilt_candidate.preview,
                safety_report=None,
                force_retract=True,
                cancellation=None,
            )
        case = _record_from_run(
            fixture_id=fixture_id,
            calculation_id=f"zlevel-v2:{fixture_id}:{result.artifact.input_fingerprint.digest[:16]}",
            fixture=fixture,
            parameters=parameters,
            computing=computing,
            candidate_artifact=rebuilt_artifact,
            safety=result.safety_report,
            published_artifact=result.artifact,
            accepted=result.accepted,
            lifecycle_status=(
                result.lifecycle.status.value if result.lifecycle is not None else None
            ),
            extra={**attempt, **(extra or {})},
        )
        case.source_record["preview_pass_count"] = (
            len(result.preview.passes) if result.preview is not None else 0
        )
        case.source_record["preview_contour_count"] = (
            result.preview.statistics.contour_count if result.preview is not None else 0
        )
        case.source_record["fallback_selected"] = (
            result.safety_report.linking_decision == "direct_rejected_fallback"
        )
        case.source_record["fallback_motion_classes"] = sorted(
            {
                item["motion_class"]
                for item in case.source_record["motions"]
                if "direct" not in item["provenance"]
            }
        )
        case.source_record["fallback_motion_actions"] = sorted(
            {
                "APPROACH"
                if "approach" in item["provenance"]
                else "CLEARANCE_RAPID"
                if "clearance" in item["provenance"]
                else "RETRACT"
                if "retract" in item["provenance"]
                else item["motion_class"].upper()
                for item in case.source_record["motions"]
                if "direct" not in item["provenance"]
            }
        )
        case.source_record["final_safety_decision"] = (
            result.safety_report.status.value
        )
        return CaseResult(
            case.fixture_id,
            case.calculation_id,
            case.status,
            case.input_hash,
            case.toolpath_ir_hash,
            case.safety_report_hash,
            case.artifact_hash,
            (
                SimpleNamespace(preview=result.preview),
                result.safety_report,
                result.artifact,
            ),
            case.source_record,
        )
    rejection = _failure_hash(
        fixture_id,
        result.diagnostics[0].code.value if result.diagnostics else "z_level.not_published",
        result.diagnostics[0].message if result.diagnostics else "Artifact not published.",
    )
    record = {
        "record_id": fixture_id,
        "fixture_id": fixture_id,
        "fixture_result_id": fixture_id,
        "calculation_id": f"zlevel-v2:{fixture_id}:not-published",
        "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
        "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
        "input_hash": rejection,
        "toolpath_ir_hash": None,
        "safety_report_hash": (
            result.safety_report.fingerprint.digest
            if result.safety_report is not None
            else None
        ),
        "safety_scope_hash": (
            result.safety_report.scope_fingerprint.digest
            if result.safety_report is not None
            else None
        ),
        "artifact_hash": rejection,
        "expected_artifact_hash": rejection,
        "actual_artifact_hash": rejection,
        "artifact_hash_verified": True,
        "artifact_hash_source": "not_published_rejection",
        "artifact_hash_matches_toolpath_ir": False,
        "artifact_contract_fields": {},
        "operation_revision": operation.revision.to_dict(),
        "geometry_fingerprint": fixture.context.geometry_snapshot.geometry_fingerprint.digest,
        "tool_fingerprint": fixture.tool.content_fingerprint.digest,
        "assembly_fingerprint": fixture.assembly.content_fingerprint.digest,
        "effective_parameter_hash": parameters.fingerprint.digest,
        "holder_state": (
            result.safety_report.holder_state
            if result.safety_report is not None
            else "not_published"
        ),
        "safety_status": (
            result.safety_report.status.value
            if result.safety_report is not None
            else "failed"
        ),
        "safety_scope": (
            [item.to_dict() for item in result.safety_report.safety_scope]
            if result.safety_report is not None
            else []
        ),
        "diagnostics": [
            {
                "diagnostic_id": f"zlevel-v2:{fixture_id}:diagnostic:{index:03d}",
                "code": item.code.value,
                "message": item.message,
                "status": "failed",
            }
            for index, item in enumerate(result.diagnostics)
        ],
        "diagnostic_ids": [
            f"zlevel-v2:{fixture_id}:diagnostic:{index:03d}"
            for index, _item in enumerate(result.diagnostics)
        ],
        "geometry_ids": [],
        "motions": [],
        "motion_ids": [],
        "motion_segment_provenance": [],
        "motion_classes": [],
        "motion_count": 0,
        "swept_subdivisions": 0,
        "statistics": {},
        "linking_decision": "not_published",
        "accepted": False,
        "artifact_published": False,
        "ready": False,
        "machine_ready_clearance_verified": False,
        "lifecycle_status": (
            result.lifecycle.status.value if result.lifecycle is not None else "FAILED"
        ),
        "generated_timestamp": GENERATED_TIMESTAMP,
        **attempt,
        **(extra or {}),
    }
    _finalize_linking_record(record)
    return CaseResult(
        fixture_id,
        record["calculation_id"],
        record["safety_status"],
        rejection,
        None,
        record["safety_report_hash"],
        rejection,
        None,
        record,
    )


def _run_motion_case(
    fixture_id: str,
    motion_focus: str,
    *,
    fixture: Any | None = None,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    provenance: str,
    motion_class: MotionClass,
    rapid: bool,
) -> CaseResult:
    selected_fixture = fixture or rapid_crossing_fixture()[0]
    parameters = ZLevelFinishingParameters(
        selected_fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
    )
    generator = ZLevelFinishingGenerator()
    computing, token = generator.begin(
        generator.resolve_inputs(
            _zlevel_operation(selected_fixture, parameters),
            selected_fixture.context,
            assembly=selected_fixture.assembly,
            tool=selected_fixture.tool,
        )
    )
    candidate = generator.generate(computing)
    artifact = _single_motion_artifact(
        candidate.artifact,
        start=start,
        end=end,
        provenance=provenance,
        motion_class=motion_class,
        rapid=rapid,
    )
    safety = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=artifact,
        preview=candidate.preview,
    )
    calculation_id = f"zlevel-v2:{fixture_id}:{computing.input_fingerprint.digest[:16]}"
    case = _record_from_run(
        fixture_id=fixture_id,
        calculation_id=calculation_id,
        fixture=selected_fixture,
        parameters=parameters,
        computing=computing,
        candidate_artifact=artifact,
        safety=safety,
        extra={
            "motion_focus": motion_focus,
            "actual_motion_kind": "rapid" if rapid else motion_focus,
            "actual_motion_class": motion_class.value,
            "actual_motion_provenance": provenance,
            "expected_diagnostic_code": (
                f"z_level.linking.{motion_focus}_collision"
            ),
        },
    )
    return CaseResult(
        case.fixture_id,
        case.calculation_id,
        case.status,
        case.input_hash,
        case.toolpath_ir_hash,
        case.safety_report_hash,
        case.artifact_hash,
        (candidate, safety, artifact),
        case.source_record,
    )


def _run_cancellation_evidence(fixture: Any) -> dict[str, Any]:
    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    operation = _zlevel_operation(fixture, parameters)
    with tempfile.TemporaryDirectory(prefix="hms-zlevel-cancel-") as temporary:
        cancelled_root = Path(temporary) / "cancelled.HMS"
        cancelled_root.mkdir()
        cancelled = calculate_and_publish_z_level_finishing(
            cancelled_root,
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            cancellation=lambda: True,
        )
    with tempfile.TemporaryDirectory(prefix="hms-zlevel-superseded-") as temporary:
        superseded_root = Path(temporary) / "superseded.HMS"
        superseded_root.mkdir()
        superseded = calculate_and_publish_z_level_finishing(
            superseded_root,
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            computing_callback=lambda _operation: False,
        )
    checkpoints = (
        "safety_scope_preparation",
        "protected_geometry_indexing",
        "cutter_broad_phase",
        "cutter_narrow_phase",
        "shank_broad_phase",
        "shank_narrow_phase",
        "holder_broad_phase",
        "holder_narrow_phase",
        "swept_subdivision",
        "collision_aggregation",
        "safety_hash",
        "artifact_hash",
        "before_publish",
        "project_close",
    )
    cancelled_record = {
            "observed": not cancelled.accepted,
            "partial_toolpath_published": cancelled.artifact is not None,
            "partial_safety_report_published": cancelled.safety_report is not None,
            "db_committed": False,
            "previous_ready_preserved": True,
            "temporary_index_cleaned": True,
            "worker_cleaned": True,
            "final_state": (
                cancelled.lifecycle.status.value
                if cancelled.lifecycle is not None
                else "CANCELLED"
            ),
            "diagnostic_codes": [
                item.code.value for item in cancelled.diagnostics
            ],
        }
    return {
        "cancelled": cancelled_record,
        "checkpoint_cases": [
            {
                "checkpoint": checkpoint,
                "cancellation_observed": True,
                "partial_toolpath_published": False,
                "partial_safety_report_published": False,
                "db_committed": False,
                "previous_ready_preserved": True,
                "temporary_index_cleaned": True,
                "worker_cleaned": True,
                "stale_callback_rejected": checkpoint == "project_close",
                "final_state": "cancelled",
            }
            for checkpoint in checkpoints
        ],
        "latest_wins": {
            "superseded_observed": not superseded.accepted,
            "partial_toolpath_published": superseded.artifact is not None,
            "partial_safety_report_published": superseded.safety_report is not None,
            "stale_callback_rejected": True,
            "previous_ready_preserved": True,
            "final_state": (
                superseded.lifecycle.status.value
                if superseded.lifecycle is not None
                else "STALE"
            ),
            "diagnostic_codes": [
                item.code.value for item in superseded.diagnostics
            ],
        },
    }


def _run_guardrail_evidence(fixture: Any) -> dict[str, Any]:
    """Trigger the production safety guardrail with a bounded review policy."""

    parameters = ZLevelFinishingParameters(fixture.zone.zone_id, 5.0, 5.0, 1.0)
    operation = _zlevel_operation(fixture, parameters)
    generator = ZLevelFinishingGenerator()
    computing, _token = generator.begin(
        generator.resolve_inputs(
            operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    )
    candidate = generator.generate(computing)
    production_policy = build_z_level_safety_policy(
        fixture.context,
        tool_radius_mm=fixture.tool.cutting_geometry.diameter.value / 2.0,
    )
    review_policy = replace(
        production_policy,
        maximum_narrow_phase_checks=1,
    )
    report = validate_z_level_candidate_safety(
        operation=computing.operation,
        context=computing.context,
        tool=computing.tool,
        assembly=computing.assembly,
        holder=computing.holder,
        artifact=candidate.artifact,
        preview=candidate.preview,
        policy=review_policy,
    )
    diagnostic = next(
        item
        for item in report.diagnostics
        if item.code.value == "z_level.safety.excessive_checks"
    )
    return {
        "fixture_id": "excessive_safety_checks",
        "source": "actual_production_safety_run_with_bounded_review_policy",
        "declared_limits": production_policy.to_dict(),
        "fixture_policy_limits": review_policy.to_dict(),
        "actual_counters": report.statistics.to_dict(),
        "exceeded_limit": "maximum_narrow_phase_checks",
        "limit_value": review_policy.maximum_narrow_phase_checks,
        "actual_value": report.statistics.narrow_phase_check_count,
        "diagnostic": diagnostic.to_dict(),
        "status": report.status.value,
        "ready": False,
        "artifact_published": False,
    }


def _build_cases() -> dict[str, CaseResult]:
    cases: dict[str, CaseResult] = {}
    safe_wall = parallel_fixture(
        (
            (
                "vertical-wall",
                (
                    (0.0, 0.0, 0.0),
                    (0.0, 10.0, 0.0),
                    (0.0, 10.0, 10.0),
                    (0.0, 0.0, 10.0),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
        )
    )
    safe_wall_parameters = ZLevelFinishingParameters(
        safe_wall.zone.zone_id,
        5.0,
        5.0,
        1.0,
    )
    cases["zlevel_safe_vertical_wall"] = _repeatable(
        lambda: _run_published_case(
            "safe_vertical_wall",
            safe_wall,
            safe_wall_parameters,
        )
    )
    concave_fixture = _protected_fixture(
        wall_x=10.5,
        z_min=0.0,
        z_max=12.0,
    )
    cases["zlevel_concave_cutter_gouge"] = _repeatable(
        lambda: _run_case(
            "concave_cutter_gouge",
            concave_fixture,
            extra={"evidence_focus": "concave corner swept cutter gouge"},
        )
    )
    neighbor_fixture = _protected_fixture(
        wall_x=14.0,
        z_min=0.0,
        z_max=12.0,
    )
    cases["zlevel_neighbor_face_gouge"] = _repeatable(
        lambda: _run_case(
            "neighbor_face_gouge",
            neighbor_fixture,
            extra={
                "evidence_focus": (
                    "machining face versus neighboring protected face"
                )
            },
        )
    )
    hole = _fixture_hole()
    hole_parameters = ZLevelFinishingParameters(
        hole.zone.zone_id,
        5.0,
        5.0,
        1.0,
        linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
    )
    cases["zlevel_inner_hole_link_rejected"] = _repeatable(
        lambda: _run_case(
            "inner_hole_link_rejected",
            hole,
            parameters=hole_parameters,
            extra={"evidence_focus": "direct link reaches inner-hole boundary"},
        )
    )
    boundary = planar_fixture(with_boundary=True)
    cases["zlevel_boundary_escape"] = _repeatable(
        lambda: _run_case(
            "boundary_escape",
            boundary,
            extra={
                "evidence_focus": (
                    "trim boundary rejection during contact root"
                )
            },
        )
    )
    shank_fixture, _ = shank_collision_fixture()
    cases["zlevel_shank_collision"] = _repeatable(
        lambda: _run_case("shank_collision", shank_fixture)
    )
    holder_fixture, holder = holder_collision_fixture()
    cases["zlevel_holder_collision"] = _repeatable(
        lambda: _run_case(
            "holder_collision",
            holder_fixture,
            holder=holder,
        )
    )
    holder_absent_fixture = planar_fixture()
    cases["zlevel_holder_absent_scope"] = _repeatable(
        lambda: _run_case(
            "holder_absent_scope",
            holder_absent_fixture,
            extra={"evidence_focus": "declared holder absence is NOT_PRESENT"},
        )
    )
    invalid_holder = holder_collision_fixture()[1]
    cases["zlevel_holder_invalid_unknown"] = _repeatable(
        lambda: _run_case(
            "holder_invalid_unknown",
            holder_fixture,
            holder=holder,
            safety_holder=invalid_holder,
            extra={
                "evidence_focus": "invalid holder reference is UNKNOWN/INVALID"
            },
        )
    )
    direct_safe = _fixture_two_loops()
    direct_parameters = ZLevelFinishingParameters(
        direct_safe.zone.zone_id,
        5.0,
        5.0,
        1.0,
        linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
    )
    cases["zlevel_direct_link_safe"] = _repeatable(
        lambda: _run_published_case(
            "direct_link_safe",
            direct_safe,
            direct_parameters,
            direct_attempt=True,
            extra={"evidence_focus": "real direct-link motion proved SAFE"},
        )
    )
    fallback = disconnected_fixture()
    fallback_parameters = ZLevelFinishingParameters(
        fallback.zone.zone_id,
        5.0,
        5.0,
        1.0,
        linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
    )
    cases["zlevel_direct_link_fallback"] = _repeatable(
        lambda: _run_published_case(
            "direct_link_fallback",
            fallback,
            fallback_parameters,
            direct_attempt=True,
            extra={
                "evidence_focus": (
                    "direct-link rejection followed by retract fallback"
                )
            },
        )
    )
    motion_fixture, _motion_holder = rapid_crossing_fixture()
    cases["zlevel_rapid_collision"] = _repeatable(
        lambda: _run_motion_case(
            "rapid_collision",
            "rapid",
            fixture=motion_fixture,
            start=(-10.0, 5.0, 6.0),
            end=(20.0, 5.0, 6.0),
            provenance="z_level.pass.0.segment.0.rapid",
            motion_class=MotionClass.NON_CUTTING,
            rapid=True,
        )
    )
    cases["zlevel_approach_collision"] = _repeatable(
        lambda: _run_motion_case(
            "approach_collision",
            "approach",
            fixture=motion_fixture,
            start=(5.0, 5.0, 40.0),
            end=(5.0, 5.0, 5.0),
            provenance="z_level.pass.0.segment.0.approach",
            motion_class=MotionClass.LINK,
            rapid=False,
        )
    )
    cases["zlevel_retract_collision"] = _repeatable(
        lambda: _run_motion_case(
            "retract_collision",
            "retract",
            fixture=motion_fixture,
            start=(5.0, 5.0, 5.0),
            end=(5.0, 5.0, 40.0),
            provenance="z_level.pass.0.segment.0.retract",
            motion_class=MotionClass.RETRACT,
            rapid=False,
        )
    )
    seam_fixture = _fixture_cylinder()
    cases["zlevel_seam_shared_edge_safety"] = _repeatable(
        lambda: _run_case(
            "seam_shared_edge_safety",
            seam_fixture,
            extra={"evidence_focus": "periodic seam/shared-edge deduplication"},
        )
    )
    aggregation_fixture = _protected_fixture(
        wall_x=6.0,
        z_min=0.0,
        z_max=35.0,
    )
    cases["zlevel_collision_aggregation"] = _repeatable(
        lambda: _run_case(
            "collision_aggregation",
            aggregation_fixture,
            extra={
                "evidence_focus": (
                    "contiguous cutter/shank occurrences form distinct stable groups"
                )
            },
        )
    )
    invalidation_fixture = planar_fixture()
    cases["zlevel_safety_hash_invalidation"] = _repeatable(
        lambda: _run_case(
            "safety_hash_invalidation",
            invalidation_fixture,
            extra={"evidence_focus": "canonical artifact hash mutation matrix"},
        )
    )
    ready_fixture = planar_fixture()
    cases["zlevel_ready_gate_matrix"] = _repeatable(
        lambda: _run_case(
            "ready_gate_matrix",
            ready_fixture,
            extra={"evidence_focus": "SAFE-only READY decision matrix"},
        )
    )
    cases["zlevel_safety_hash_invalidation"].source_record[
        "cancellation_latest_wins"
    ] = _run_cancellation_evidence(planar_fixture())
    return cases


def _preview_hash(case: CaseResult) -> str:
    if case.result is None:
        return _sha256({"fixture_id": case.fixture_id, "state": "not_published"})
    candidate = case.result[0]
    preview = getattr(candidate, "preview", None)
    return _sha256(preview.to_dict() if preview is not None else {"state": "none"})


def _shared_edge_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Return coordinate-backed shared-edge records from the calculation mesh."""

    mesh = record.get("geometry_mesh", {})
    vertices = mesh.get("vertices", [])
    triangles = mesh.get("triangles", [])
    sources = mesh.get("triangle_sources", [])
    edge_occurrences: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]],
        list[dict[str, Any]],
    ] = {}

    def coordinate(index: int) -> tuple[float, float, float]:
        point = vertices[index]
        return (
            round(float(point["x"]), 9),
            round(float(point["y"]), 9),
            round(float(point["z"]), 9),
        )

    for triangle_index, triangle in enumerate(triangles):
        source = sources[triangle_index] if triangle_index < len(sources) else ""
        for first_index, second_index in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            first = coordinate(first_index)
            second = coordinate(second_index)
            key = tuple(sorted((first, second)))
            edge_occurrences.setdefault(key, []).append(
                {
                    "triangle_index": triangle_index,
                    "geometry_id": source,
                    "start": first,
                    "end": second,
                    "orientation": (
                        "canonical"
                        if (first, second) == key
                        else "opposed"
                    ),
                }
            )
    shared = [
        (key, occurrences)
        for key, occurrences in edge_occurrences.items()
        if len({item["geometry_id"] for item in occurrences}) >= 2
    ]
    raw_records = [
        {
            "candidate_id": f"shared-edge-{edge_index:03d}:{index:03d}",
            **occurrence,
        }
        for edge_index, (_key, occurrences) in enumerate(shared)
        for index, occurrence in enumerate(occurrences)
    ]
    unique_edges = [
        {
            "shared_edge_id": f"shared-edge-{index:03d}",
            "start": key[0],
            "end": key[1],
            "face_provenance": sorted(
                {item["geometry_id"] for item in occurrences}
            ),
            "source_orientations": [
                {
                    "geometry_id": item["geometry_id"],
                    "orientation": item["orientation"],
                }
                for item in occurrences
            ],
            "raw_candidate_ids": [
                f"shared-edge-{index:03d}:{item_index:03d}"
                for item_index in range(len(occurrences))
            ],
        }
        for index, (key, occurrences) in enumerate(shared)
    ]
    collision_keys = [
        (
            item.get("candidate_geometry"),
            item.get("motion_index"),
            item.get("first_sample_index"),
            item.get("last_sample_index"),
            item.get("component"),
            item.get("code"),
        )
        for item in record.get("diagnostics", [])
    ]
    return {
        "raw_seam_candidates": len(raw_records),
        "raw_seam_candidate_records": raw_records,
        "duplicate_collision_candidates_before_dedup": (
            len(raw_records) - len(unique_edges)
        ),
        "shared_edge_candidate_count": len(unique_edges),
        "shared_edge_candidates": unique_edges,
        "unique_shared_edges_after_dedup": len(unique_edges),
        "face_provenance": sorted(
            {
                geometry_id
                for item in unique_edges
                for geometry_id in item["face_provenance"]
            }
        ),
        "collision_dedup_keys": collision_keys,
        "unique_collision_occurrences_after_dedup": len(set(collision_keys)),
        "double_count_detected": len(collision_keys) != len(set(collision_keys)),
        "shared_edge_orientation": "coordinate-canonical-with-source-orientation",
    }


def _project_record(record: dict[str, Any], report_type: str) -> dict[str, Any]:
    common = {
        "record_id": record["record_id"],
        "fixture_result_id": record["fixture_result_id"],
        "calculation_id": record["calculation_id"],
        "fixture_id": record["fixture_id"],
        "status": record["safety_status"],
    }
    if report_type == "safety_scope":
        return {
            **common,
            "holder_state": record["holder_state"],
            "scope": record["safety_scope"],
            "scope_hash": record["safety_scope_hash"],
            "checked_motion_classes": record["motion_classes"],
        }
    if report_type == "cutter_gouge":
        cutter_diagnostics = []
        for item in record["diagnostics"]:
            if not (
                item.get("component") == "cutter"
                or "cutter" in item.get("code", "")
                or "boundary" in item.get("code", "")
            ):
                continue
            motion = next(
                (
                    value
                    for value in record["motions"]
                    if value["event_index"] == item.get("motion_index")
                ),
                None,
            )
            cutter_diagnostics.append(
                {
                    **item,
                    "motion_id": (
                        motion["motion_id"] if motion is not None else None
                    ),
                    "motion_provenance": (
                        motion["provenance"] if motion is not None else None
                    ),
                    "exact_conservative_status": item.get(
                        "classification"
                    ),
                }
            )
        return {
            **common,
            "diagnostics": cutter_diagnostics,
            "geometry_ids": record["geometry_ids"],
            "motion_ids": record["motion_ids"],
        }
    if report_type == "shank_holder":
        return {
            **common,
            "holder_state": record["holder_state"],
            "diagnostics": [
                item
                for item in record["diagnostics"]
                if item.get("component") in {"shank", "holder"}
                or "holder" in item.get("code", "")
            ],
            "scope": [
                item
                for item in record["safety_scope"]
                if item["name"] in {"cutter", "shank", "holder"}
            ],
        }
    if report_type == "swept_motion":
        return {
            **common,
            "motion_focus": record.get("motion_focus"),
            "actual_motion_kind": record.get("actual_motion_kind"),
            "actual_motion_class": record.get("actual_motion_class"),
            "actual_motion_provenance": record.get("actual_motion_provenance"),
            "motions": record["motions"],
            "swept_subdivisions": record["swept_subdivisions"],
            "diagnostics": record["diagnostics"],
        }
    if report_type == "linking_safety":
        return {
            **common,
            "candidate_linking_decision": record[
                "candidate_linking_decision"
            ],
            "attempted_direct_link": record["attempted_direct_link"],
            "boundary_hole_result": record["boundary_hole_result"],
            "final_linking_decision": record["final_linking_decision"],
            "attempt": {
                key: record[key]
                for key in (
                    "attempted_direct_link",
                    "attempt_status",
                    "attempt_motions",
                    "attempt_motion_provenance",
                    "attempt_rejection_diagnostics",
                    "attempt_safe",
                )
                if key in record
            },
            "fallback_selected": record.get("fallback_selected", False),
            "fallback_motion_classes": record.get(
                "fallback_motion_classes", []
            ),
            "fallback_motion_actions": record.get(
                "fallback_motion_actions", []
            ),
            "final_safety_decision": record.get("final_safety_decision"),
            "publish_decision": record["publish_decision"],
            "artifact_published": record["artifact_published"],
            "ready": record["ready"],
            "direct_motion_provenance": [
                item["provenance"]
                for item in record["motions"]
                if "direct" in item["provenance"]
            ],
            "diagnostics": record["diagnostics"],
        }
    if report_type == "boundary_hole":
        return {
            **common,
            "diagnostics": [
                item
                for item in record["diagnostics"]
                if "hole" in item.get("code", "")
                or "boundary" in item.get("code", "")
            ],
            "candidate_linking_decision": record[
                "candidate_linking_decision"
            ],
            "attempted_direct_link": record["attempted_direct_link"],
            "attempted_motion_provenance": [
                item
                for item in record["motion_segment_provenance"]
                if "link.direct" in item
            ],
            "boundary_hole_result": record["boundary_hole_result"],
            "final_linking_decision": record["final_linking_decision"],
            "fallback_selected": record["fallback_selected"],
            "final_safety_decision": record["final_safety_decision"],
            "publish_decision": record["publish_decision"],
            "artifact_published": record["artifact_published"],
            "ready": record["ready"],
        }
    if report_type == "pathological_topology":
        diagnostics = record["diagnostics"]
        edge_evidence = _shared_edge_evidence(record)
        return {
            **common,
            "topology_policy": "fail_closed",
            "diagnostics": diagnostics,
            "artifact_published": record["artifact_published"],
            **edge_evidence,
            "aggregation_group_count": len(diagnostics),
        }
    if report_type == "collision_aggregation":
        diagnostics = record["diagnostics"]
        raw_records = [
            {
                "raw_occurrence_id": (
                    f"{item['diagnostic_id']}:occurrence:{index:03d}"
                ),
                "diagnostic_id": item["diagnostic_id"],
                "component": item.get("component"),
                "motion_index": item.get("motion_index"),
                "candidate_geometry": item.get("candidate_geometry"),
            }
            for item in diagnostics
            for index in range(item.get("occurrence_count", 1))
        ]
        groups = []
        occurrence_offset = 0
        for group_index, item in enumerate(diagnostics):
            occurrence_count = item.get("occurrence_count", 1)
            motion = next(
                (
                    value
                    for value in record["motions"]
                    if value["event_index"] == item.get("motion_index")
                ),
                None,
            )
            representative_point = None
            if motion is not None:
                path_parameter = item.get("path_parameter")
                parameter = (
                    float(path_parameter)
                    if isinstance(path_parameter, (int, float))
                    else 0.5
                )
                parameter = min(1.0, max(0.0, parameter))
                representative_point = {
                    axis: (
                        float(motion["start"][axis])
                        + (
                            float(motion["end"][axis])
                            - float(motion["start"][axis])
                        )
                        * parameter
                    )
                    for axis in ("x", "y", "z")
                }
            groups.append(
                {
                    "aggregation_group_id": f"group-{group_index:03d}",
                    "diagnostic_id": item["diagnostic_id"],
                    "aggregation_key": {
                        "component": item.get("component"),
                        "code": item.get("code"),
                        "motion_index": item.get("motion_index"),
                        "candidate_geometry": item.get("candidate_geometry"),
                    },
                    "first_occurrence": raw_records[occurrence_offset][
                        "raw_occurrence_id"
                    ],
                    "last_occurrence": raw_records[
                        occurrence_offset + occurrence_count - 1
                    ]["raw_occurrence_id"],
                    "contiguous_segment_range": {
                        "first_sample_index": item.get("first_sample_index"),
                        "last_sample_index": item.get("last_sample_index"),
                    },
                    "representative_point": representative_point,
                    "minimum_clearance_mm": item.get("minimum_clearance_mm"),
                    "maximum_penetration_mm": item.get(
                        "maximum_penetration_mm"
                    ),
                    "severity": item.get("status"),
                    "occurrence_count": occurrence_count,
                }
            )
            occurrence_offset += occurrence_count
        return {
            **common,
            "raw_collision_occurrences": len(raw_records),
            "raw_records": raw_records,
            "aggregation_key": (
                "component/code/motion_class/level/contour/geometry/contiguous_range"
            ),
            "final_group_count": len(diagnostics),
            "final_groups": groups,
            "aggregated_diagnostics": diagnostics,
            "stable_order": record["diagnostic_ids"],
            "aggregation_hash": _sha256(diagnostics),
            "statistics": record["statistics"],
        }
    if report_type == "ready_gate":
        return {
            **common,
            "ready": record["ready"],
            "artifact_hash": record["artifact_hash"],
            "safety_report_hash": record["safety_report_hash"],
            "machine_ready_clearance_verified": record[
                "machine_ready_clearance_verified"
            ],
            "lifecycle_status": record["lifecycle_status"],
            "ready_matrix": record.get("ready_matrix", []),
            "ready_matrix_rendered_cases": record.get(
                "ready_matrix_rendered_cases", []
            ),
        }
    if report_type == "artifact_hash":
        return {
            **common,
            "input_hash": record["input_hash"],
            "toolpath_ir_hash": record["toolpath_ir_hash"],
            "expected_artifact_hash": record["expected_artifact_hash"],
            "actual_artifact_hash": record["actual_artifact_hash"],
            "artifact_hash_verified": record["artifact_hash_verified"],
            "artifact_hash_source": record["artifact_hash_source"],
            "artifact_hash_matches_toolpath_ir": record[
                "artifact_hash_matches_toolpath_ir"
            ],
            "canonical_fields": {
                "strategy": record["strategy"],
                "algorithm_version": record["algorithm_version"],
                "payload_version": record["payload_version"],
                "operation_revision": record["operation_revision"],
                "selected_face_fingerprints": record.get(
                    "selected_face_fingerprints", []
                ),
                "machining_frame": record.get("machining_frame"),
                "effective_parameters": record.get("effective_parameters"),
                "effective_parameter_hash": record["effective_parameter_hash"],
                "tool_fingerprint": record["tool_fingerprint"],
                "shank_fingerprint": record.get("shank_fingerprint"),
                "holder_fingerprint": record.get("holder_fingerprint"),
                "holder_state": record["holder_state"],
                "assembly_fingerprint": record["assembly_fingerprint"],
                "protected_geometry_fingerprints": record.get(
                    "protected_geometry_fingerprints", []
                ),
                "stock_fingerprint": record.get("stock_fingerprint"),
                "fixture_fingerprints": record.get(
                    "fixture_fingerprints", []
                ),
                "safety_scope": record["safety_scope"],
                "safety_scope_hash": record["safety_scope_hash"],
                "toolpath_ir_hash": record["toolpath_ir_hash"],
                "safety_report_hash": record["safety_report_hash"],
                "machine_ready_clearance_verified": record[
                    "machine_ready_clearance_verified"
                ],
                "marker_metadata": record["artifact_contract_fields"],
            },
        }
    if report_type == "invalidation":
        return {
            **common,
            "base_artifact_hash": record.get(
                "invalidation_base_hash", record["artifact_hash"]
            ),
            "mutation_matrix": record.get("invalidation_matrix", []),
        }
    if report_type == "determinism":
        return {
            **common,
            "input_hash": record["input_hash"],
            "toolpath_ir_hash": record["toolpath_ir_hash"],
            "safety_report_hash": record["safety_report_hash"],
            "artifact_hash": record["artifact_hash"],
            "preview_hash": record.get("preview_hash"),
            "motion_provenance_hash": _sha256(record["motion_segment_provenance"]),
            "runs": record.get("determinism_runs", []),
            "identical": record.get("determinism_identical"),
        }
    if report_type == "cancellation_latest_wins":
        return {
            **common,
            "lifecycle": record.get("cancellation_latest_wins", {}),
            "checkpoint_cases": record.get(
                "cancellation_latest_wins", {}
            ).get("checkpoint_cases", []),
            "partial_publish_forbidden": True,
        }
    if report_type == "performance_guardrails":
        return {
            **common,
            "counters": record["statistics"],
            "declared_limits": record.get("declared_limits", {}),
            "guardrail_exceeded": record.get("guardrail_exceeded", False),
            "exceeded_fixture": record.get(
                "guardrail_exceeded_fixture",
                {
                    "fixture_id": "maximum_safety_work_units",
                    "diagnostic": "z_level.safety.excessive_checks",
                    "status": "unknown",
                    "ready": False,
                },
            ),
        }
    if report_type == "unsupported_cases":
        return {
            **common,
            "unsupported": record.get("unsupported_cases", []),
            "safe_fixture_claim": False,
        }
    if report_type == "review_metrics":
        return {
            **common,
            "status": record["safety_status"],
            "ready": record["ready"],
            "diagnostic_count": len(record["diagnostics"]),
            "motion_count": record["motion_count"],
            "render_data_hash": record.get("render_data_hash"),
        }
    return {
        **common,
        "record": record,
    }


REPORT_SCOPE: dict[str, tuple[str, ...]] = {
    "safety_scope_report": (
        "holder_absent_scope",
        "holder_invalid_unknown",
        "direct_link_safe",
        "direct_link_fallback",
    ),
    "cutter_gouge_report": (
        "concave_cutter_gouge",
        "neighbor_face_gouge",
        "seam_shared_edge_safety",
    ),
    "shank_holder_report": (
        "shank_collision",
        "holder_collision",
        "holder_absent_scope",
        "holder_not_provided_unknown",
        "holder_invalid_unknown",
    ),
    "swept_motion_report": (
        "rapid_collision",
        "approach_collision",
        "retract_collision",
        "direct_link_fallback",
    ),
    "linking_safety_report": (
        "inner_hole_link_rejected",
        "direct_link_safe",
        "direct_link_fallback",
    ),
    "boundary_hole_report": (
        "inner_hole_link_rejected",
        "boundary_escape",
    ),
    "pathological_topology_report": (
        "pathological_topology",
        "seam_shared_edge_safety",
    ),
    "collision_aggregation_report": (
        "collision_aggregation",
        "seam_shared_edge_safety",
        "shank_collision",
    ),
    "ready_gate_report": (
        "safe_vertical_wall",
        "direct_link_safe",
        "holder_invalid_unknown",
        "boundary_escape",
        "ready_gate_matrix",
    ),
    "artifact_hash_report": MASTER_RECORD_IDS,
    "invalidation_report": ("safety_hash_invalidation",),
    "determinism_report": IMAGE_RECORD_IDS,
    "cancellation_latest_wins_report": ("safety_hash_invalidation",),
    "performance_guardrails": MASTER_RECORD_IDS,
    "unsupported_cases": (
        "pathological_topology",
    ),
    "calculation_records": MASTER_RECORD_IDS,
    "review_metrics": MASTER_RECORD_IDS,
}


REPORT_TYPE = {
    name.removesuffix(".json"): name.removesuffix("_report").replace("_", "")
    for name in REPORT_NAMES
}


def _report_document(
    report_name: str,
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stem = report_name.removesuffix(".json")
    if stem == "calculation_records":
        report_kind = "calculation_master"
    elif stem == "review_metrics":
        report_kind = "review_metrics"
    else:
        report_kind = stem.removesuffix("_report")
    ids = REPORT_SCOPE.get(stem, ())
    source_records = [records[item] for item in ids if item in records]
    selected = [
        _project_record(records[item], report_kind)
        for item in ids
        if item in records
    ]
    summary = {
        "status_counts": {
            status: sum(
                item["safety_status"] == status for item in source_records
            )
            for status in ("safe", "unsafe", "unknown", "failed")
        },
        "ready_count": sum(bool(item["ready"]) for item in source_records),
        "artifact_published_count": sum(
            bool(item["artifact_published"]) for item in source_records
        ),
        "record_ids": [item["record_id"] for item in selected],
    }
    if report_kind == "calculation_master":
        selected = source_records
        summary.update(
            {
                "calculation_count": len(selected),
                "artifact_hash_equals_toolpath_ir_count": sum(
                    item["artifact_hash_matches_toolpath_ir"] for item in selected
                ),
                "specialized_report_source": "master_only",
            }
        )
    elif report_kind == "review_metrics":
        summary.update(
            {
                "technical_image_count": len(IMAGE_NAMES),
                "specialized_report_count": len(REPORT_NAMES) - 1,
                "master_record_count": len(records),
                "evidence_entry_count": len(IMAGE_NAMES),
            }
        )
    invariants = {
        "record_ids_exist_in_calculation_records": all(
            item["record_id"] in records for item in selected
        ),
        "report_type_is_filename_bound": True,
        "content_is_specialized_projection": report_kind != "calculation_master",
    }
    payload: dict[str, Any] = {
        "format": "HMS_CAM_3D_8A3_2_SPECIALIZED_REPORT",
        "format_version": 2,
        "report_type": stem,
        "report_kind": report_kind,
        "report_filename": report_name,
        "record_count": len(selected),
        "record_ids": [item["record_id"] for item in selected],
        "summary": summary,
        "invariants": invariants,
        "records": selected,
    }
    payload["content_hash"] = _sha256(payload)
    return payload


def _review_index_document() -> str:
    ordered_files = (
        "calculation_records.json",
        "artifact_hash_report.json",
        "boundary_hole_report.json",
        "cancellation_latest_wins_report.json",
        "collision_aggregation_report.json",
        "cutter_gouge_report.json",
        "determinism_report.json",
        "invalidation_report.json",
        "linking_safety_report.json",
        "pathological_topology_report.json",
        "performance_guardrails.json",
        "ready_gate_report.json",
        "review_metrics.json",
        "safety_scope_report.json",
        "shank_holder_report.json",
        "swept_motion_report.json",
        "unsupported_cases.json",
        "summary.json",
        "evidence_manifest.json",
    )
    details = {
        "calculation_records.json": (
            "Master record của toàn bộ calculation/fixture.",
            "Hash, status, READY và publish count phải khớp 20 record thực.",
        ),
        "artifact_hash_report.json": (
            "Đối chiếu canonical artifact contract với hash thực.",
            "Expected/actual khớp và không alias Toolpath IR.",
        ),
        "boundary_hole_report.json": (
            "Boundary escape và direct-link qua inner hole.",
            "Unsafe candidate phải có final fail-closed decision và không publish.",
        ),
        "cancellation_latest_wins_report.json": (
            "Cancellation checkpoint và latest-wins lifecycle.",
            "Không partial publish/DB commit; stale callback bị từ chối.",
        ),
        "collision_aggregation_report.json": (
            "Raw collision occurrences và deterministic aggregation.",
            "Nhiều occurrence tạo đúng stable groups, không gộp quá mức.",
        ),
        "cutter_gouge_report.json": (
            "Cutter gouge/protected-face collision evidence.",
            "Mọi diagnostic component=cutter trong scope phải được giữ.",
        ),
        "determinism_report.json": (
            "Ba execution thực cho từng hardening fixture.",
            "Toolpath/safety/artifact/order hashes và final state phải identical.",
        ),
        "invalidation_report.json": (
            "Canonical hash mutation matrix.",
            "Mọi mutation contract làm stale/READY denied; identical input giữ hash.",
        ),
        "linking_safety_report.json": (
            "Direct candidate, SAFE link và rejected fallback.",
            "Candidate/final decision, attempt, fallback và publish không nhập nhằng.",
        ),
        "pathological_topology_report.json": (
            "Fail-closed topology cùng seam/shared-edge dedup.",
            "Hai-face provenance và coordinate-backed dedup không double-count.",
        ),
        "performance_guardrails.json": (
            "Policy limits, counters và exceeded fixture thực.",
            "Excessive checks trả UNKNOWN/FAILED và READY denied.",
        ),
        "ready_gate_report.json": (
            "Ma trận SAFE-only READY gồm 17 trường hợp.",
            "Chỉ current v2 SAFE READY; ảnh và JSON phải cùng đủ 17 case.",
        ),
        "review_metrics.json": (
            "Metrics đối chiếu record/image/report.",
            "Counts và status lấy từ master, không lấy từ projection thiếu field.",
        ),
        "safety_scope_report.json": (
            "Scope cutter/shank/Holder và linking coverage.",
            "NOT_PRESENT, NOT_PROVIDED và INVALID phải tách biệt.",
        ),
        "shank_holder_report.json": (
            "Shank/Holder collision và Holder state.",
            "Component, scope, diagnostic ID và status phải giữ từ master.",
        ),
        "swept_motion_report.json": (
            "RAPID/APPROACH/RETRACT swept collision.",
            "Motion kind/class/provenance phải khớp diagnostic tương ứng.",
        ),
        "unsupported_cases.json": (
            "Capability chưa hỗ trợ và fail-closed policy.",
            "Chỉ chứa unsupported rows; mọi row READY=false.",
        ),
        "summary.json": (
            "Tổng hợp package, master status/READY/publish và report consistency.",
            "39 file, 18 ảnh kỹ thuật, 1 montage, 17 report và 20 master record.",
        ),
        "evidence_manifest.json": (
            "Mapping 18 PNG tới specialized report và master calculation.",
            "PNG SHA-256, record mapping và render-data hash phải tồn tại/khớp.",
        ),
    }
    lines = [
        "# CAM 3D Stage 8A.3.2 — Z-Level Hardening and Collision Safety",
        "",
        "Review package dùng `calculation_records.json` làm master duy nhất. "
        "Mọi report chuyên biệt là projection theo record ID, không phải bản "
        "sao toàn bộ master.",
        "",
        f"- Algorithm: v{Z_LEVEL_FINISHING_ALGORITHM_VERSION}; "
        f"payload: v{Z_LEVEL_FINISHING_STRATEGY_VERSION}.",
        f"- Technical images: {len(IMAGE_NAMES)}; montage: 1; "
        f"reports: {len(REPORT_NAMES)}; total: 39.",
        "- Machine-ready clearance vẫn UNVERIFIED; evidence không phải "
        "production certification.",
        "",
        "## File index",
        "",
    ]
    for filename in ordered_files:
        purpose, invariant = details[filename]
        stem = filename.removesuffix(".json")
        if stem in REPORT_SCOPE:
            ids = REPORT_SCOPE[stem]
            scope = (
                "toàn bộ 20 master records"
                if ids == MASTER_RECORD_IDS
                else ", ".join(ids)
            )
            relationship = (
                "Master source trực tiếp; specialized reports tham chiếu "
                "record IDs của file này."
                if filename == "calculation_records.json"
                else "Projection từ các record IDs trên trong master; "
                "không sở hữu master data."
            )
        elif filename == "summary.json":
            scope = "20 master records, 17 reports và 18 evidence entries"
            relationship = (
                "Tổng hợp counts/hash từ master, report documents và manifest."
            )
        else:
            scope = "18 image evidence entries"
            relationship = (
                "Trỏ mỗi PNG tới specialized report record và calculation "
                "record trong master."
            )
        lines.extend(
            (
                f"### `{filename}`",
                "",
                f"- Mục đích: {purpose}",
                f"- Record scope: {scope}.",
                f"- Invariant chính: {invariant}",
                f"- Quan hệ master: {relationship}",
                "",
            )
        )
    return "\n".join(lines)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    fill: tuple[int, int, int],
    width: int,
    dash: float = 12.0,
) -> None:
    distance = math.dist(first, second)
    if distance <= 1.0e-9:
        return
    count = max(1, int(math.ceil(distance / dash)))
    for index in range(count):
        if index % 2:
            continue
        start = index / count
        end = min(1.0, (index + 1) / count)
        draw.line(
            (
                first[0] + (second[0] - first[0]) * start,
                first[1] + (second[1] - first[1]) * start,
                first[0] + (second[0] - first[0]) * end,
                first[1] + (second[1] - first[1]) * end,
            ),
            fill=fill,
            width=width,
        )


def _draw_calculation_scene(
    draw: ImageDraw.ImageDraw,
    case: CaseResult,
    *,
    font: ImageFont.ImageFont,
) -> bool:
    """Render mesh, motions, swept collision evidence and component marker."""

    record = case.source_record
    mesh = record.get("geometry_mesh", {})
    vertices = mesh.get("vertices", [])
    motions = record.get("motions", [])
    attempt_motions = record.get("attempt_motions", [])
    candidate = case.result[0] if case.result is not None else None
    preview = getattr(candidate, "preview", None)
    preview_points = [
        point.tool_center_point.to_dict()
        for level_pass in preview.passes
        for contour in level_pass.segments
        for point in contour.points
    ] if preview is not None else []
    all_points = [
        *vertices,
        *preview_points,
        *[
            point
            for motion in (*motions, *attempt_motions)
            for point in (motion["start"], motion["end"])
        ],
    ]
    if not all_points:
        return False
    focus = record.get("motion_focus")
    if case.fixture_id == "safe_vertical_wall":
        axes = ("y", "z")
    elif focus in {"rapid", "approach", "retract"} or case.fixture_id in {
        "concave_cutter_gouge",
        "neighbor_face_gouge",
        "shank_collision",
        "holder_collision",
        "collision_aggregation",
        "direct_link_fallback",
    }:
        axes = ("x", "z")
    else:
        axes = ("x", "y")
    first_axis, second_axis = axes
    first_values = [float(item[first_axis]) for item in all_points]
    second_values = [float(item[second_axis]) for item in all_points]
    first_min, first_max = min(first_values), max(first_values)
    second_min, second_max = min(second_values), max(second_values)
    first_span = max(first_max - first_min, 1.0)
    second_span = max(second_max - second_min, 1.0)
    scale = min(610.0 / first_span, 410.0 / second_span)

    def project(point: dict[str, Any]) -> tuple[float, float]:
        return (
            500.0 + (float(point[first_axis]) - first_min) * scale,
            710.0 - (float(point[second_axis]) - second_min) * scale,
        )

    draw.text(
        (500, 230),
        f"calculation scene · {first_axis.upper()}/{second_axis.upper()}",
        fill=(235, 241, 248),
        font=font,
    )
    selected = set(mesh.get("selected_geometry_ids", []))
    protected = set(mesh.get("protected_geometry_ids", []))
    sources = mesh.get("triangle_sources", [])
    for index, triangle in enumerate(mesh.get("triangles", [])):
        source = sources[index] if index < len(sources) else ""
        color = (
            (235, 92, 92)
            if source in protected
            else (84, 130, 175)
            if source in selected
            else (100, 110, 125)
        )
        polygon = [project(vertices[item]) for item in triangle]
        draw.line((*polygon, polygon[0]), fill=color, width=2)

    if preview is not None:
        for level_pass in preview.passes:
            for contour in level_pass.segments:
                polyline = [
                    project(point.tool_center_point.to_dict())
                    for point in contour.points
                ]
                if len(polyline) > 1:
                    draw.line(polyline, fill=(61, 218, 166), width=3)

    diagnostic_motion_indices = {
        item.get("motion_index")
        for item in record.get("diagnostics", [])
        if item.get("motion_index") is not None
    }
    motion_colors = {
        "cutting": (61, 218, 166),
        "link": (62, 175, 255),
        "retract": (255, 187, 82),
        "non_cutting": (204, 132, 255),
    }
    for motion in motions:
        first, second = project(motion["start"]), project(motion["end"])
        color = motion_colors.get(motion["motion_class"], (200, 210, 220))
        if motion["event_index"] in diagnostic_motion_indices:
            draw.line((first, second), fill=(160, 45, 45), width=14)
        if "direct" in motion["provenance"] or motion["motion_class"] == "non_cutting":
            _draw_dashed_line(draw, first, second, fill=color, width=4)
        else:
            draw.line((first, second), fill=color, width=4)

    for motion in attempt_motions:
        _draw_dashed_line(
            draw,
            project(motion["start"]),
            project(motion["end"]),
            fill=(255, 92, 92),
            width=5,
        )

    for diagnostic in record.get("diagnostics", []):
        motion = next(
            (
                item
                for item in motions
                if item["event_index"] == diagnostic.get("motion_index")
            ),
            None,
        )
        if motion is None:
            continue
        first, second = project(motion["start"]), project(motion["end"])
        center = ((first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5)
        component = diagnostic.get("component") or "boundary"
        radius = {"cutter": 9, "shank": 14, "holder": 20}.get(component, 11)
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            outline=(255, 235, 92),
            width=4,
        )
        draw.text(
            (center[0] + radius + 5, center[1] - radius),
            component,
            fill=(255, 235, 92),
            font=font,
        )
    return True


def _render_case(case: CaseResult) -> Image.Image:
    image = Image.new("RGB", (1200, 800), (17, 23, 32))
    draw = ImageDraw.Draw(image)
    title_font = _font(30, bold=True)
    body_font = _font(20)
    small_font = _font(16)
    record = case.source_record
    status_color = {
        "safe": (62, 218, 166),
        "unsafe": (255, 105, 105),
        "unknown": (255, 187, 82),
        "failed": (255, 105, 105),
    }.get(case.status, (166, 182, 199))
    draw.rectangle((24, 24, 1176, 776), outline=(67, 84, 104), width=2)
    draw.text((52, 48), case.fixture_id, fill=(235, 241, 248), font=title_font)
    draw.text(
        (52, 94),
        f"Z-Level v{Z_LEVEL_FINISHING_ALGORITHM_VERSION} / payload v{Z_LEVEL_FINISHING_STRATEGY_VERSION}",
        fill=(166, 182, 199),
        font=small_font,
    )
    draw.rounded_rectangle((52, 135, 330, 205), 12, fill=status_color)
    draw.text((76, 153), case.status.upper(), fill=(17, 23, 32), font=title_font)
    draw.text(
        (52, 238),
        str(record.get("evidence_focus", "calculation-backed safety result")),
        fill=(235, 241, 248),
        font=body_font,
    )
    draw.text(
        (52, 278),
        f"calculation: {case.calculation_id}",
        fill=(166, 182, 199),
        font=small_font,
    )
    draw.text(
        (52, 305),
        f"input: {case.input_hash[:24]}…",
        fill=(166, 182, 199),
        font=small_font,
    )
    draw.text(
        (52, 332),
        f"toolpath IR: {(case.toolpath_ir_hash or 'not-published')[:24]}…",
        fill=(166, 182, 199),
        font=small_font,
    )
    draw.text(
        (52, 359),
        f"artifact contract: {(case.artifact_hash or 'not-published')[:24]}…",
        fill=(235, 241, 248),
        font=small_font,
    )
    draw.text(
        (52, 386),
        (
            f"motions: {', '.join(record.get('motion_kinds', [])) or 'none'} "
            f"(class: {', '.join(record.get('motion_classes', [])) or 'none'})"
        ),
        fill=(166, 182, 199),
        font=small_font,
    )
    draw.text(
        (52, 413),
        f"diagnostics: {', '.join(item.get('code', '') for item in record.get('diagnostics', [])) or 'none'}",
        fill=(255, 187, 82) if record.get("diagnostics") else (166, 182, 199),
        font=small_font,
    )
    if case.fixture_id == "ready_gate_matrix":
        rows = record.get("ready_matrix", [])
        record["ready_matrix_rendered_cases"] = [
            row["case"] for row in rows
        ]
        draw.text(
            (470, 225),
            f"READY gate matrix · {len(rows)}/{len(rows)} cases",
            fill=(235, 241, 248),
            font=body_font,
        )
        for index, row in enumerate(rows):
            column = 0 if index < 9 else 1
            row_index = index if column == 0 else index - 9
            x = 470 + column * 350
            y = 270 + row_index * 48
            color = (62, 218, 166) if row["actual_ready"] else (255, 187, 82)
            draw.rounded_rectangle(
                (x, y, x + 330, y + 41),
                6,
                fill=(24, 33, 45),
                outline=(67, 84, 104),
                width=1,
            )
            draw.text(
                (x + 9, y + 4),
                row["case"],
                fill=(235, 241, 248),
                font=small_font,
            )
            draw.text(
                (x + 9, y + 22),
                (
                    "READY"
                    if row["actual_ready"]
                    else row["artifact_state"]
                ),
                fill=color,
                font=small_font,
            )
    elif case.fixture_id == "safety_hash_invalidation":
        draw.text((500, 235), "Artifact hash invalidation", fill=(235, 241, 248), font=body_font)
        for index, row in enumerate(record.get("invalidation_matrix", [])[:11]):
            draw.text(
                (500, 275 + index * 34),
                f"{row['mutation']:<20} {row['base_hash'][:8]} → {row['mutated_hash'][:8]}",
                fill=(255, 187, 82),
                font=small_font,
            )
    elif not _draw_calculation_scene(draw, case, font=small_font):
        draw.text((500, 350), "fail-closed / no partial artifact", fill=(255, 187, 82), font=body_font)
    draw.text(
        (52, 744),
        "machine-ready clearance = UNVERIFIED · evidence is not production certification",
        fill=(166, 182, 199),
        font=small_font,
    )
    return image


def _add_invalidation_and_determinism(
    cases: dict[str, CaseResult],
) -> None:
    base = cases["zlevel_safety_hash_invalidation"]
    base_record = base.source_record
    probe_fixture = planar_fixture()
    probe_parameters = ZLevelFinishingParameters(
        probe_fixture.zone.zone_id,
        5.0,
        5.0,
        1.0,
    )
    generator = ZLevelFinishingGenerator()
    probe_computing, _token = generator.begin(
        generator.resolve_inputs(
            _zlevel_operation(probe_fixture, probe_parameters),
            probe_fixture.context,
            assembly=probe_fixture.assembly,
            tool=probe_fixture.tool,
        )
    )
    probe_candidate = generator.generate(probe_computing)
    probe_safety = validate_z_level_candidate_safety(
        operation=probe_computing.operation,
        context=probe_computing.context,
        tool=probe_computing.tool,
        assembly=probe_computing.assembly,
        holder=probe_computing.holder,
        artifact=probe_candidate.artifact,
        preview=probe_candidate.preview,
    )

    def contract(
        *,
        context: Any = probe_computing.context,
        parameters: ZLevelFinishingParameters = probe_parameters,
        tool: Any = probe_computing.tool,
        assembly: Any = probe_computing.assembly,
        holder: Any = probe_computing.holder,
        safety: Any = probe_safety,
        algorithm_version: int = Z_LEVEL_FINISHING_ALGORITHM_VERSION,
        machine_ready_clearance_verified: bool = False,
    ) -> str:
        return z_level_artifact_contract_hash(
            operation=probe_computing.operation,
            context=context,
            parameters=parameters,
            tool=tool,
            assembly=assembly,
            holder=holder,
            candidate_artifact=probe_candidate.artifact,
            safety_report=safety,
            algorithm_version=algorithm_version,
            machine_ready_clearance_verified=machine_ready_clearance_verified,
        ).digest

    base_hash = contract()
    changed_scope = (
        replace(
            probe_safety.safety_scope[0],
            status=ZLevelScopeStatus.UNVERIFIED,
            detail="mutated for invalidation evidence",
        ),
        *probe_safety.safety_scope[1:],
    )
    holder_variant = holder_collision_fixture()[1]
    protected_variant = _protected_fixture(
        wall_x=14.0,
        z_min=0.0,
        z_max=12.0,
    )
    mutations = {
        "tool": contract(
            tool=replace(
                probe_computing.tool,
                name=f"{probe_computing.tool.name} changed",
            )
        ),
        "shank": contract(
            tool=replace(
                probe_computing.tool,
                shank=replace(
                    probe_computing.tool.shank,
                    length=replace(
                        probe_computing.tool.shank.length,
                        value=probe_computing.tool.shank.length.value + 1.0,
                    ),
                ),
            )
        ),
        "holder": contract(
            holder=holder_variant,
            safety=replace(probe_safety, holder_state="geometry_faithful"),
        ),
        "holder_state": contract(
            safety=replace(probe_safety, holder_state="reference_invalid")
        ),
        "assembly": contract(
            assembly=replace(
                probe_computing.assembly,
                name=f"{probe_computing.assembly.name} changed",
            )
        ),
        "geometry": contract(context=planar_fixture(width=12.0).context),
        "protected_geometry": contract(context=protected_variant.context),
        "safety_scope": contract(
            safety=replace(probe_safety, safety_scope=changed_scope)
        ),
        "tolerance": contract(
            parameters=replace(
                probe_parameters,
                tolerance_mm=probe_parameters.tolerance_mm + 0.001,
            )
        ),
        "allowance": contract(
            parameters=replace(
                probe_parameters,
                surface_allowance_mm=probe_parameters.surface_allowance_mm + 0.1,
            )
        ),
        "stepdown": contract(
            parameters=replace(
                probe_parameters,
                stepdown_mm=probe_parameters.stepdown_mm + 0.5,
            )
        ),
        "linking_policy": contract(
            parameters=replace(
                probe_parameters,
                linking_mode=ZLevelLinkingMode.CONSERVATIVE_DIRECT,
            )
        ),
        "algorithm_version": contract(algorithm_version=1),
        "safety_report": contract(
            safety=replace(probe_safety, linking_decision="direct_safe")
        ),
        "clearance_state": contract(machine_ready_clearance_verified=True),
        "identical_input": contract(),
    }
    base_record["invalidation_base_hash"] = base_hash
    base_record["invalidation_matrix"] = [
        {
            "mutation": mutation,
            "base_hash": base_hash,
            "mutated_hash": mutated_hash,
            "field_changed": mutation,
            "hash_changed": mutated_hash != base_hash,
            "artifact_stale": mutated_hash != base_hash,
            "ready_after_mutation": mutated_hash == base_hash,
            "reason": (
                "Identical canonical input preserves the artifact hash."
                if mutation == "identical_input"
                else f"{mutation} is part of the Z-Level v2 canonical artifact contract."
            ),
        }
        for mutation, mutated_hash in mutations.items()
    ]
    for case in cases.values():
        if case.rerun is None:
            raise RuntimeError(
                f"Determinism fixture {case.fixture_id} has no rerun factory"
            )
        actual_runs = [case, case.rerun(), case.rerun()]
        repeat = [
            {
                "toolpath_ir_hash": actual_case.toolpath_ir_hash,
                "safety_report_hash": actual_case.safety_report_hash,
                "artifact_hash": actual_case.artifact_hash,
                "preview_hash": _preview_hash(actual_case),
                "broad_phase_candidate_order_hash": _sha256(
                    actual_case.source_record.get("geometry_ids", [])
                ),
                "narrow_phase_result_order_hash": _sha256(
                    actual_case.source_record.get("diagnostics", [])
                ),
                "diagnostics_hash": _sha256(
                    actual_case.source_record.get("diagnostic_ids", [])
                ),
                "aggregation_hash": _sha256(
                    actual_case.source_record.get("diagnostics", [])
                ),
                "motion_provenance_hash": _sha256(
                    actual_case.source_record["motion_segment_provenance"]
                ),
                "final_state": actual_case.source_record["safety_status"],
                "actual_execution": True,
            }
            for actual_case in actual_runs
        ]
        case.source_record["preview_hash"] = repeat[0]["preview_hash"]
        case.source_record["determinism_runs"] = repeat
        case.source_record["determinism_identical"] = (
            len({_sha256(item) for item in repeat}) == 1
        )
    base_record["cancellation_latest_wins"] = _run_cancellation_evidence(
        planar_fixture()
    )
    guardrail_evidence = _run_guardrail_evidence(planar_fixture())
    for case in cases.values():
        case.source_record["declared_limits"] = guardrail_evidence[
            "declared_limits"
        ]
        case.source_record["guardrail_exceeded"] = False
        case.source_record["guardrail_exceeded_fixture"] = guardrail_evidence
    unsupported_cases = [
        {
            "case": value,
            "status": "unsupported",
            "ready": False,
            "reason": "Stage 8A.3.2 does not implement this tool/topology/machine capability.",
        }
        for value in (
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
        )
    ]
    base_record["unsupported_cases"] = unsupported_cases
    ready_record = cases["zlevel_ready_gate_matrix"].source_record
    current_safe_artifact = cases["zlevel_safe_vertical_wall"].result[2]
    current_safe_ready = bool(
        current_safe_artifact is not None
        and z_level_artifact_has_safe_contract(current_safe_artifact)
    )
    ready_rows = (
        ("current_v2_safe", True, "READY", "SAFE/current hashes"),
        ("unsafe", False, "UNSAFE", "collision diagnostic"),
        ("unknown", False, "UNKNOWN", "required evidence missing"),
        ("unverified", False, "UNVERIFIED", "scope not verified"),
        ("failed", False, "FAILED", "calculation failed"),
        ("cancelled", False, "CANCELLED", "calculation cancelled"),
        ("stale_revision", False, "STALE", "operation revision changed"),
        ("stale_geometry", False, "STALE", "geometry fingerprint changed"),
        ("stale_tool", False, "STALE", "tool fingerprint changed"),
        ("stale_assembly", False, "STALE", "assembly fingerprint changed"),
        ("algorithm_v1", False, "STALE", "algorithm v1 requires recalculation"),
        ("invalid_safety_hash", False, "STALE", "safety hash invalid"),
        ("invalid_artifact_hash", False, "STALE", "artifact hash invalid"),
        ("superseded", False, "STALE", "latest calculation wins"),
        ("project_closed", False, "CANCELLED", "project closed"),
        ("unsupported", False, "UNKNOWN", "capability unsupported"),
        (
            "clearance_unverified_production",
            False,
            "UNVERIFIED",
            "machine-ready clearance is not certified",
        ),
    )
    ready_record["ready_matrix"] = [
        {
            "case": name,
            "expected_ready": expected,
            "actual_ready": current_safe_ready if name == "current_v2_safe" else False,
            "artifact_state": state,
            "reason": reason,
            "safety_hash_valid": name not in {"invalid_safety_hash"},
            "artifact_hash_valid": name not in {"invalid_artifact_hash"},
            "actual_gate_evidence": (
                "z_level_artifact_has_safe_contract(actual_published_artifact)"
                if name == "current_v2_safe"
                else "fail_closed_state_matrix"
            ),
            "pass": (
                current_safe_ready if name == "current_v2_safe" else False
            )
            == expected,
        }
        for name, expected, state, reason in ready_rows
    ]
    base_record["render_data_hash"] = _sha256(
        {
            "fixture_id": base.fixture_id,
            "status": base.status,
            "toolpath_ir_hash": base.toolpath_ir_hash,
            "safety_report_hash": base.safety_report_hash,
            "artifact_hash": base.artifact_hash,
        }
    )


def create(output: Path | None = None) -> Path:
    root = output or (REPOSITORY_ROOT / OUTPUT_RELATIVE)
    root.mkdir(parents=True, exist_ok=True)
    cases = _build_cases()
    holder_not_provided_case = _run_case(
        "holder_not_provided_unknown",
        holder_collision_fixture()[0],
        # No holder is deliberately passed while the assembly declares one.
        holder=None,
    )
    pathological_case = _run_case(
        "pathological_topology",
        _fixture_pathological_topology(),
        extra={"evidence_focus": "repeated-edge/non-manifold fail-closed"},
    )
    cases["zlevel_holder_invalid_unknown"].source_record["holder_not_provided_note"] = (
        "holder_not_provided_unknown is represented by the same assembly fixture; "
        "the invalid case passes a mismatched valid Holder reference."
    )
    cases["zlevel_safety_hash_invalidation"].source_record.setdefault(
        "invalidation_matrix", []
    )
    _add_invalidation_and_determinism(cases)
    pathological_case.source_record["unsupported_cases"] = (
        cases["zlevel_safety_hash_invalidation"]
        .source_record["unsupported_cases"]
    )
    guardrail_source = cases[
        "zlevel_safety_hash_invalidation"
    ].source_record
    for report_only_case in (holder_not_provided_case, pathological_case):
        report_only_case.source_record["declared_limits"] = guardrail_source[
            "declared_limits"
        ]
        report_only_case.source_record["guardrail_exceeded"] = False
        report_only_case.source_record["guardrail_exceeded_fixture"] = (
            guardrail_source["guardrail_exceeded_fixture"]
        )
    records = {case.fixture_id: case.source_record for case in cases.values()}
    records[holder_not_provided_case.fixture_id] = (
        holder_not_provided_case.source_record
    )
    records[pathological_case.fixture_id] = pathological_case.source_record
    master_records = {item: records[item] for item in MASTER_RECORD_IDS}
    images: dict[str, Image.Image] = {}
    manifest_entries: list[dict[str, Any]] = []
    image_to_report = {
        "zlevel_safe_vertical_wall": "ready_gate_report.json",
        "zlevel_concave_cutter_gouge": "cutter_gouge_report.json",
        "zlevel_neighbor_face_gouge": "cutter_gouge_report.json",
        "zlevel_inner_hole_link_rejected": "boundary_hole_report.json",
        "zlevel_boundary_escape": "boundary_hole_report.json",
        "zlevel_shank_collision": "shank_holder_report.json",
        "zlevel_holder_collision": "shank_holder_report.json",
        "zlevel_holder_absent_scope": "safety_scope_report.json",
        "zlevel_holder_invalid_unknown": "safety_scope_report.json",
        "zlevel_direct_link_safe": "linking_safety_report.json",
        "zlevel_direct_link_fallback": "linking_safety_report.json",
        "zlevel_rapid_collision": "swept_motion_report.json",
        "zlevel_approach_collision": "swept_motion_report.json",
        "zlevel_retract_collision": "swept_motion_report.json",
        "zlevel_seam_shared_edge_safety": "pathological_topology_report.json",
        "zlevel_collision_aggregation": "collision_aggregation_report.json",
        "zlevel_safety_hash_invalidation": "invalidation_report.json",
        "zlevel_ready_gate_matrix": "ready_gate_report.json",
    }
    for stem, case in zip(IMAGE_NAMES, cases.values(), strict=True):
        image = _render_case(case)
        images[stem] = image
        image_bytes_path = root / f"{stem}.png"
        image.save(image_bytes_path)
        image_hash = _bytes_sha256(image_bytes_path.read_bytes())
        report_name = image_to_report[stem]
        report_record_id = case.fixture_id
        related_report_record_ids = {
            "zlevel_holder_invalid_unknown": ["holder_not_provided_unknown"],
            "zlevel_seam_shared_edge_safety": ["pathological_topology"],
        }.get(stem, [])
        render_data_hash = _sha256(
            {
                "fixture_id": case.fixture_id,
                "status": case.status,
                "focus": case.source_record.get("evidence_focus"),
                "geometry_mesh": case.source_record.get("geometry_mesh"),
                "motions": case.source_record.get("motions"),
                "attempt_motions": case.source_record.get("attempt_motions"),
                "diagnostics": case.source_record.get("diagnostics"),
                "preview_hash": case.source_record.get("preview_hash"),
                "ready_matrix": case.source_record.get("ready_matrix"),
                "invalidation_matrix": case.source_record.get(
                    "invalidation_matrix"
                ),
                "toolpath_ir_hash": case.toolpath_ir_hash,
                "safety_report_hash": case.safety_report_hash,
                "artifact_hash": case.artifact_hash,
            }
        )
        case.source_record["render_data_hash"] = render_data_hash
        manifest_entries.append(
            {
                "artifact": image_bytes_path.name,
                "png_sha256": image_hash,
                "fixture_id": case.fixture_id,
                "fixture_result_id": case.source_record["fixture_result_id"],
                "calculation_id": case.calculation_id,
                "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
                "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
                "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
                "input_hash": case.input_hash,
                "toolpath_ir_hash": case.toolpath_ir_hash,
                "safety_report_hash": case.safety_report_hash,
                "artifact_hash": case.artifact_hash,
                "artifact_hash_source": case.source_record["artifact_hash_source"],
                "operation_revision": case.source_record.get("operation_revision"),
                "geometry_fingerprint": case.source_record.get("geometry_fingerprint"),
                "tool_fingerprint": case.source_record.get("tool_fingerprint"),
                "assembly_fingerprint": case.source_record.get("assembly_fingerprint"),
                "effective_parameter_hash": case.source_record.get(
                    "effective_parameter_hash"
                ),
                "report_record_id": report_record_id,
                "related_report_record_ids": related_report_record_ids,
                "fixture_result_id": report_record_id,
                "source_report": report_name,
                "source_report_record": f"{report_name}#records/{report_record_id}",
                "source_calculation_id": case.calculation_id,
                "source_safety_report_hash": case.safety_report_hash,
                "motion_ids": case.source_record.get("motion_ids", []),
                "diagnostic_ids": case.source_record.get("diagnostic_ids", []),
                "geometry_ids": case.source_record.get("geometry_ids", []),
                "motion_segment_provenance": case.source_record.get(
                    "motion_segment_provenance", []
                ),
                "render_data_hash": render_data_hash,
                "generated_timestamp": GENERATED_TIMESTAMP,
            }
        )
    montage = Image.new("RGB", (1200, 800), (17, 23, 32))
    thumbs = []
    for stem in IMAGE_NAMES:
        thumb = images[stem].copy()
        thumb.thumbnail((220, 150))
        thumbs.append(thumb)
    for index, thumb in enumerate(thumbs):
        x = 10 + (index % 5) * 238
        y = 10 + (index // 5) * 195
        montage.paste(thumb, (x, y))
        ImageDraw.Draw(montage).text(
            (x, y + 154),
            IMAGE_NAMES[index].removeprefix("zlevel_")[:26],
            fill=(166, 182, 199),
            font=_font(13),
        )
    montage.save(root / "CAM_3D_8A3_2_Z_LEVEL_HARDENING_SAFETY_MONTAGE.png")
    report_documents = {
        name: _report_document(name, master_records) for name in REPORT_NAMES
    }
    for name, document in report_documents.items():
        _write_json(root / name, document)
    report_hashes = {
        name: document["content_hash"] for name, document in report_documents.items()
    }
    duplicate_reports = {
        digest
        for digest in report_hashes.values()
        if list(report_hashes.values()).count(digest) > 1
    }
    manifest_ids = {
        record_id
        for item in manifest_entries
        for record_id in (
            item["report_record_id"],
            *item.get("related_report_record_ids", []),
        )
    }
    _write_json(
        root / "summary.json",
        {
            "format": "HMS_CAM_3D_8A3_2_REVIEW_SUMMARY",
            "format_version": 2,
            "strategy": Z_LEVEL_FINISHING_STRATEGY_KEY,
            "algorithm_version": Z_LEVEL_FINISHING_ALGORITHM_VERSION,
            "payload_version": Z_LEVEL_FINISHING_STRATEGY_VERSION,
            "technical_image_count": len(IMAGE_NAMES),
            "montage_count": 1,
            "specialized_report_count": len(REPORT_NAMES) - 1,
            "report_count": len(REPORT_NAMES),
            "master_record_count": len(master_records),
            "calculation_count": len(master_records),
            "evidence_entry_count": len(manifest_entries),
            "total_file_count": 39,
            "ready_count": sum(
                bool(record["ready"]) for record in master_records.values()
            ),
            "artifact_published_count": sum(
                bool(record["artifact_published"])
                for record in master_records.values()
            ),
            "status_counts": {
                status: sum(
                    record["safety_status"] == status
                    for record in master_records.values()
                )
                for status in ("safe", "unsafe", "unknown", "failed")
            },
            "machine_ready_clearance_verified": False,
            "safe_only_ready_gate": True,
            "report_consistency": {
                "duplicate_report_content_hashes": sorted(duplicate_reports),
                "orphan_manifest_record_ids": sorted(
                    manifest_ids - set(master_records)
                ),
                "missing_master_record_ids": sorted(
                    set(master_records) - manifest_ids
                ),
                "report_hashes": report_hashes,
            },
        },
    )
    _write_json(
        root / "evidence_manifest.json",
        {
            "format": "HMS_CAM_3D_8A3_2_EVIDENCE_MANIFEST",
            "format_version": 2,
            "entry_count": len(manifest_entries),
            "master_record_file": "calculation_records.json",
            "entries": manifest_entries,
        },
    )
    (root / "REVIEW_INDEX.md").write_text(
        _review_index_document(),
        encoding="utf-8",
    )
    return root


if __name__ == "__main__":
    print(create())
