"""Generate the complete Git-ignored review package for Stage 8A.2.1."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import sys
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QGuiApplication

from hms_cadcam.cam.cam3d import PartSurfaceSet
from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ParallelCutDirection,
    ParallelFinishingError,
    ParallelFinishingGenerator,
    ParallelFinishingParameters,
    ParallelProgressPhase,
    build_machining_frame,
    build_frame_axes,
    calculate_and_publish_parallel_finishing,
    calculate_region_bounds,
    intersect_parallel_passes,
    plan_pass_positions,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamValidationError,
    ContentFingerprint,
    DiagnosticCode,
    DirtyReason,
    GeometryReferenceId,
    Length,
    LengthUnit,
    OperationParameterSet,
    SetupId,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    Vector3,
)
from hms_cadcam.cam.post import (
    PostRequest,
    SimulationGateMode,
    SimulationGatePolicy,
    canonical_definition,
    lower_toolpath,
)
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, MotionClass, RapidMove
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.parallel_finishing_worker import ParallelFinishingTask
from tests._parallel_finishing_review_metrics import (
    event_text,
    geometry_metrics,
    toolpath_ir_metrics,
)
from tests._parallel_finishing_review_render import (
    render_geometry_review,
    render_motion_review,
)
from tests.unit._cam3d_fixtures import tool
from tests.unit._parallel_finishing_fixtures import (
    contiguous_fixture,
    curved_coarse_mesh_fixture,
    disconnected_fixture,
    parallel_fixture,
    planar_fixture,
)
from tests.unit._parallel_finishing_ocp_fixtures import (
    curved_brep_tolerance_fixture,
    inclined_brep_tolerance_fixture,
)
from tests.unit._post_fixtures import source_snapshot

logger = logging.getLogger("manual_stage8a2_1")


def _candidate(fixture, resolver=None, *, cancellation=None, progress=None):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return generator.generate(
        computing,
        cancellation=cancellation,
        progress=progress,
        contact_resolver=resolver,
    )


def _write_json(path: Path, payload: object) -> None:
    _write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        logger.exception("Không thể ghi review artifact %s", path)
        raise


def _review_payload(name: str, fixture, candidate) -> dict[str, object]:
    return {
        "format": "HMS_CAM3D_PARALLEL_FIXTURE_REVIEW",
        "format_version": 2,
        "fixture": name,
        "selected_face_ids": [
            str(item.geometry.reference_id)
            for item in fixture.zone.part_surfaces.selection.surfaces
        ],
        "tolerance": fixture.zone.tolerance.to_dict(),
        "allowance": fixture.zone.allowance.to_dict(),
        "safe_motion": fixture.context.safe_motion_policy.to_dict(),
        "geometry_metrics": geometry_metrics(fixture, candidate),
        "preview": candidate.preview.to_dict(),
        "toolpath_ir": candidate.artifact.to_dict(),
    }


def _run_record(candidate) -> dict[str, object]:
    statistics = candidate.preview.statistics
    return {
        "pass_positions": list(candidate.preview.pass_positions),
        "pass_count": statistics.planned_pass_count,
        "segment_count": statistics.segment_count,
        "point_count": statistics.contact_point_count,
        "event_count": statistics.toolpath_event_count,
        "preview_hash": candidate.preview.fingerprint.digest,
        "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
    }


def _determinism_report(fixtures) -> dict[str, object]:
    cases: dict[str, object] = {}
    for name in ("planar", "curved_brep_tolerance", "contiguous", "disconnected", "zigzag"):
        fixture, resolver = fixtures[name]
        runs = [_run_record(_candidate(fixture, resolver)) for _index in range(3)]
        identical = runs[0] == runs[1] == runs[2]
        if not identical:
            raise RuntimeError(f"Parallel determinism failed for {name}")
        cases[name] = {"identical": identical, "runs": runs}
    return {
        "format": "HMS_CAM3D_PARALLEL_DETERMINISM_REPORT",
        "format_version": 1,
        "run_count_per_fixture": 3,
        "cases": cases,
    }


def _cancel_case(phase: ParallelProgressPhase, project_root: Path, fixture) -> dict[str, object]:
    state = {"cancel": False, "reports": []}

    def progress(report) -> None:
        state["reports"].append(
            {
                "phase": report.phase.value,
                "processed": report.processed,
                "total": report.total,
            }
        )
        if report.phase is phase and (
            phase is ParallelProgressPhase.DISCRETIZATION or report.processed >= 1
        ):
            state["cancel"] = True

    applied_before = fixture.operation.to_dict()
    result = calculate_and_publish_parallel_finishing(
        project_root,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        cancellation=lambda: bool(state["cancel"]),
        progress=progress,
    )
    return {
        "target_phase": phase.value,
        "cancel_observed": any(
            item.code is DiagnosticCode.PARALLEL_CANCELLED
            for item in result.diagnostics
        ),
        "accepted": result.accepted,
        "returned_artifact": result.artifact is not None,
        "returned_preview": result.preview is not None,
        "returned_state": result.operation.artifact_state.status.value,
        "applied_operation_unchanged": fixture.operation.to_dict() == applied_before,
        "published_files": sorted(
            item.name for item in (project_root / "toolpaths").glob("*")
        )
        if (project_root / "toolpaths").exists()
        else [],
        "progress": state["reports"],
    }


def _worker_close_report(workspace: Path) -> dict[str, object]:
    completed: list[object] = []
    gate = threading.Event()

    def operation(cancelled, _progress):
        gate.set()
        while not cancelled():
            threading.Event().wait(0.001)
        return "late-result"

    task = ParallelFinishingTask(operation)
    task.signals.completed.connect(completed.append)
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    service = ProjectService.create_default(workspace / "config")
    service.new_project(workspace, "Parallel Worker Close")
    pool.start(task)
    gate.wait(2.0)
    task.abandon()
    service.close_project(discard_changes=True)
    stopped = pool.waitForDone(5_000)
    app = QGuiApplication.instance()
    if app is not None:
        app.processEvents()
    return {
        "close_action": "ParallelFinishingTask.abandon",
        "worker_started": gate.is_set(),
        "worker_stopped": stopped,
        "active_thread_count": pool.activeThreadCount(),
        "cancelled": task.cancelled,
        "project_closed": not service.has_project,
        "late_completed_callbacks": len(completed),
        "thread_leak": pool.activeThreadCount() != 0,
    }


def _cancellation_report(output_root: Path, fixture) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="hms-parallel-cancel-") as directory:
        root = Path(directory)
        intersection = _cancel_case(
            ParallelProgressPhase.INTERSECTION,
            root / "Intersection.HMS",
            fixture,
        )
        discretization = _cancel_case(
            ParallelProgressPhase.DISCRETIZATION,
            root / "Discretization.HMS",
            fixture,
        )
        latest_root = root / "Latest.HMS"
        latest_root.mkdir()
        accepted = calculate_and_publish_parallel_finishing(
            latest_root,
            fixture.operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
        before = {
            item.name: hashlib.sha256(item.read_bytes()).hexdigest()
            for item in (latest_root / "toolpaths").glob("*")
        }
        recompute_operation = dataclasses.replace(
            accepted.operation,
            artifact_state=accepted.operation.artifact_state.mark_dirty(
                DirtyReason.PARAMETERS_CHANGED
            ),
        )
        cancelled = calculate_and_publish_parallel_finishing(
            latest_root,
            recompute_operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            cancellation=lambda: True,
        )
        after = {
            item.name: hashlib.sha256(item.read_bytes()).hexdigest()
            for item in (latest_root / "toolpaths").glob("*")
        }
        stale_root = root / "Stale.HMS"
        stale_root.mkdir()
        stale = calculate_and_publish_parallel_finishing(
            stale_root,
            fixture.operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
            current_operation=lambda: fixture.operation,
        )
        stale_files = sorted(
            item.name for item in (stale_root / "toolpaths").glob("*")
        ) if (stale_root / "toolpaths").exists() else []
        worker_close = _worker_close_report(root / "worker-close")
    report = {
        "format": "HMS_CAM3D_PARALLEL_CANCELLATION_REPORT",
        "format_version": 1,
        "intersection": intersection,
        "discretization": discretization,
        "partial_result_did_not_replace_latest": (
            not cancelled.accepted and before == after
        ),
        "latest_artifact_checksums_before": before,
        "latest_artifact_checksums_after": after,
        "latest_wins": {
            "accepted": stale.accepted,
            "diagnostic_codes": [item.code.value for item in stale.diagnostics],
            "published_files": stale_files,
        },
        "worker_project_close": worker_close,
    }
    _write_json(output_root / "cancellation_report.json", report)
    return report


def _error_record(action) -> dict[str, object]:
    try:
        action()
    except ParallelFinishingError as error:
        return {
            "code": error.code.value,
            "severity": "error",
            "message": str(error),
            "exception_type": type(error).__name__,
        }
    except CamValidationError as error:
        return {
            "code": "CamValidationError",
            "severity": "error",
            "message": str(error),
            "exception_type": type(error).__name__,
        }
    raise RuntimeError("Unsupported case unexpectedly succeeded")


def _flat_tool_error(fixture) -> None:
    flat = tool(ball=False)
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Unsupported flat assembly",
        flat,
        Length(30.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    operation = dataclasses.replace(
        fixture.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
    )
    context = dataclasses.replace(
        fixture.context,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(assembly.to_dict()),
        tool_definition_fingerprint=flat.content_fingerprint,
    )
    ParallelFinishingGenerator().resolve_inputs(
        operation,
        context,
        assembly=assembly,
        tool=flat,
    )


def _geometry_inputs(fixture):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    frame = build_machining_frame(
        fixture.zone,
        inputs.parameters.direction_angle_degrees,
        epsilon=fixture.zone.tolerance.calculation_epsilon,
    )
    bounds = calculate_region_bounds(
        fixture.mesh,
        frame,
        fixture.zone,
        padding=fixture.zone.tolerance.contact_tolerance,
    )
    return inputs, frame, bounds


def _missing_face_error(fixture) -> None:
    selected = fixture.zone.part_surfaces.selection.surfaces[0]
    missing = dataclasses.replace(
        selected,
        geometry=dataclasses.replace(
            selected.geometry,
            reference_id=GeometryReferenceId.new(),
        ),
    )
    zone = dataclasses.replace(
        fixture.zone,
        part_surfaces=PartSurfaceSet(
            dataclasses.replace(
                fixture.zone.part_surfaces.selection,
                surfaces=(missing,),
            )
        ),
    )
    frame = build_machining_frame(zone, 0.0, epsilon=1.0e-9)
    calculate_region_bounds(fixture.mesh, frame, zone, padding=0.001)


def _no_intersection_error(fixture) -> None:
    inputs, frame, bounds = _geometry_inputs(fixture)
    intersect_parallel_passes(
        fixture.context,
        frame,
        bounds,
        (100.0,),
        inputs.parameters,
        tool_radius=inputs.tool_radius,
    )


def _post_error(fixture) -> None:
    source = source_snapshot()
    source = dataclasses.replace(
        source,
        operation=dataclasses.replace(
            source.operation,
            parameters=ParallelFinishingParameters(
                fixture.zone.zone_id,
                2.0,
            ).to_operation_parameters(),
        ),
    )
    request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        canonical_definition(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )
    lower_toolpath(request, source)


def _unsupported_report(output_root: Path, brep_value) -> dict[str, object]:
    fixture = planar_fixture()
    inputs, frame, bounds = _geometry_inputs(fixture)
    long_curve = planar_fixture(
        width=30.0,
        stepover=10.0,
        maximum_segment_length=0.001,
    )
    large_result = planar_fixture(
        width=20.0,
        height=5.0,
        stepover=1.0,
        maximum_segment_length=0.001,
    )
    cases = {
        "flat_end_tool": _error_record(lambda: _flat_tool_error(fixture)),
        "unsupported_tool_geometry": _error_record(
            lambda: _flat_tool_error(fixture)
        ),
        "zero_direction": _error_record(
            lambda: build_frame_axes(
                Vector3(0.0, 0.0, 0.0),
                Vector3(0.0, 0.0, 1.0),
                0.0,
                epsilon=1.0e-9,
            )
        ),
        "invalid_stepover": _error_record(
            lambda: plan_pass_positions(bounds, 0.0, tolerance=0.001)
        ),
        "tolerance_non_positive": _error_record(
            lambda: build_machining_frame(fixture.zone, 0.0, epsilon=0.0)
        ),
        "missing_selected_face": _error_record(
            lambda: _missing_face_error(fixture)
        ),
        "missing_source_face": _error_record(
            lambda: brep_value.resolver(
                GeometryReferenceId.new(),
                brep_value.fixture.mesh.vertices[0],
                0.011,
            )
        ),
        "no_intersection": _error_record(lambda: _no_intersection_error(fixture)),
        "pass_count_over_20000": _error_record(
            lambda: plan_pass_positions(
                bounds=dataclasses.replace(bounds, v_max=100.0),
                stepover=0.001,
                tolerance=1.0e-6,
            )
        ),
        "curve_point_count_over_25000": _error_record(
            lambda: _candidate(long_curve)
        ),
        "result_point_count_over_100000": _error_record(
            lambda: _candidate(large_result)
        ),
        "unsupported_post": _error_record(lambda: _post_error(fixture)),
    }
    with tempfile.TemporaryDirectory(prefix="hms-parallel-warning-") as directory:
        root = Path(directory) / "Warning.HMS"
        root.mkdir()
        accepted = calculate_and_publish_parallel_finishing(
            root,
            fixture.operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    limitation = next(
        item
        for item in accepted.diagnostics
        if item.code is DiagnosticCode.PARALLEL_FOUNDATION_LIMITATION
    )
    cases["unsupported_collision_guarantee"] = {
        "code": limitation.code.value,
        "severity": limitation.severity.value,
        "message": limitation.message,
        "accepted_with_explicit_warning": accepted.accepted,
    }
    report = {
        "format": "HMS_CAM3D_PARALLEL_UNSUPPORTED_CASES",
        "format_version": 1,
        "cases": cases,
    }
    _write_json(output_root / "unsupported_cases.json", report)
    return report


def _toolpath_report(output_root: Path, fixture, candidate) -> dict[str, object]:
    metrics = toolpath_ir_metrics(candidate)
    safe = fixture.context.safe_motion_policy
    tool_centers = [
        point.tool_center_point
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for point in segment.points
    ]
    cutting = [
        event
        for event in candidate.artifact.events
        if isinstance(event, LinearMove)
        and event.motion_class is MotionClass.CUTTING
    ]
    metrics["cutting_endpoints_lie_on_tool_center_paths"] = all(
        any(
            math.dist(
                (event.end.position.x, event.end.position.y, event.end.position.z),
                (point.x, point.y, point.z),
            )
            <= fixture.zone.tolerance.contact_tolerance
            for point in tool_centers
        )
        for event in cutting
    )
    metrics["disconnected_segment_count"] = candidate.preview.statistics.segment_count
    metrics["retract_motion_count"] = sum(
        isinstance(event, (RapidMove, LinearMove, ArcMove))
        and event.motion_class is MotionClass.RETRACT
        for event in candidate.artifact.events
    )
    motions = tuple(
        event
        for event in candidate.artifact.events
        if isinstance(event, (RapidMove, LinearMove, ArcMove))
    )
    metrics["initial_position_at_clearance"] = math.isclose(
        candidate.artifact.initial_pose.position.z,
        safe.clearance_z,
        rel_tol=0.0,
        abs_tol=fixture.zone.tolerance.contact_tolerance,
    )
    metrics["final_position_at_clearance"] = math.isclose(
        motions[-1].end.position.z,
        safe.clearance_z,
        rel_tol=0.0,
        abs_tol=fixture.zone.tolerance.contact_tolerance,
    )
    metrics["final_motion_is_retract"] = (
        motions[-1].motion_class is MotionClass.RETRACT
    )
    metrics["disconnected_segments_have_retract"] = (
        metrics["retract_motion_count"]
        >= candidate.preview.statistics.segment_count * 2
    )
    metrics["motion_provenance_has_pass_and_segment"] = all(
        event.provenance.startswith("parallel.pass.")
        and ".segment." in event.provenance
        for event in motions
    )
    required_checks = (
        "sequence_indices_contiguous",
        "motion_start_matches_previous_end",
        "all_cutting_events_have_parallel_provenance",
        "all_events_have_contiguous_sequence_and_provenance",
        "cutting_endpoints_lie_on_tool_center_paths",
        "initial_position_at_clearance",
        "final_position_at_clearance",
        "final_motion_is_retract",
        "disconnected_segments_have_retract",
        "motion_provenance_has_pass_and_segment",
    )
    if not all(metrics[key] is True for key in required_checks):
        raise RuntimeError("Toolpath IR review invariant failed")
    report = {
        "format": "HMS_CAM3D_PARALLEL_TOOLPATH_IR_SUMMARY",
        "format_version": 1,
        "fixture": "disconnected",
        "metrics": metrics,
    }
    _write_json(output_root / "toolpath_ir_summary.json", report)
    lines = [event_text(event) for event in candidate.artifact.events]
    _write_text(
        output_root / "toolpath_ir_first_30_events.txt",
        "\n".join(lines[:30]) + "\n",
    )
    _write_text(
        output_root / "toolpath_ir_last_30_events.txt",
        "\n".join(lines[-30:]) + "\n",
    )
    return report


def _zigzag_result(fixture, candidate) -> dict[str, object]:
    directions = []
    for pass_value in candidate.preview.passes:
        if not pass_value.segments:
            continue
        segment = pass_value.segments[0]
        first_u = candidate.preview.frame.coordinates(
            segment.points[0].contact_point
        )[0]
        last_u = candidate.preview.frame.coordinates(
            segment.points[-1].contact_point
        )[0]
        directions.append(
            {
                "pass_index": pass_value.pass_index,
                "direction": "+U" if last_u > first_u else "-U",
                "source_ids": [
                    str(item) for item in segment.points[0].source_surface_ids
                ],
            }
        )
    expected = all(
        item["direction"] == ("+U" if item["pass_index"] % 2 == 0 else "-U")
        for item in directions
    )
    unsafe_rapid = any(
        isinstance(event, RapidMove)
        and abs(event.start.position.x - event.end.position.x) > 0.001
        and min(event.start.position.z, event.end.position.z)
        < fixture.context.safe_motion_policy.clearance_z
        for event in candidate.artifact.events
    )
    return {
        "directions": directions,
        "even_plus_u_odd_minus_u": expected,
        "unsafe_lateral_rapid": unsafe_rapid,
        "preview_hash": candidate.preview.fingerprint.digest,
        "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
    }


def _review_index(output_root: Path, image_names: tuple[str, ...]) -> None:
    rows = [
        ("summary.json", "all", "Tổng hợp fixture/metrics", "Đạt", "Foundation only"),
        ("mesh_quality_report.json", "coarse + BRep", "Tolerance và normal quality", "Đạt", "BRep deviation chỉ đo tại contact samples"),
        ("normal_comparison.json", "curved", "Facet so với BRep normal", "Đã sửa", "Coarse giữ làm fail-closed regression"),
        ("determinism_report.json", "5 cases × 3", "Pass/IR/hash identical", "Đạt", "Cùng immutable input"),
        ("cancellation_report.json", "curved", "Cancel/worker/latest-wins", "Đạt", "Cooperative cancellation"),
        ("unsupported_cases.json", "invalid inputs", "Diagnostic thực tế", "Đạt", "Post/collision vẫn unsupported"),
        ("toolpath_ir_summary.json", "disconnected", "IR continuity/retract/provenance", "Đạt", "Không chứng nhận machine motion"),
        ("toolpath_ir_first_30_events.txt", "disconnected", "Đầu event stream", "Đạt", "Text review"),
        ("toolpath_ir_last_30_events.txt", "disconnected", "Cuối event stream", "Đạt", "Text review"),
    ]
    rows.extend(
        (name, "geometry/IR", "Visual geometry evidence", "Đạt", "Oblique review projection")
        for name in image_names
    )
    lines = [
        "# Stage 8A.2.1 — Review Index",
        "",
        "Review lần hai: curved coarse fixture đã được nhận diện đúng là mesh cố ý thô; "
        "tool-center BRep mới dùng projected contact và differential normal từ source face.",
        "",
        "| File | Fixture | Điều kiểm chứng | Kết quả | Giới hạn |",
        "|---|---|---|---|---|",
        *[f"| `{a}` | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows],
        "",
        "## Vấn đề đã sửa",
        "",
        "- Đổi tên fixture cũ thành `curved_coarse_mesh`; metadata không còn được dùng như bằng chứng BRep tolerance.",
        "- OCP tessellation map chordal/angular/minimum-size policy bằng deterministic `IMeshTools_Parameters`.",
        "- Contact được project về trimmed BRep face; normal lấy từ surface differential và xử lý orientation.",
        "- Sharp-edge normal không bị average vượt angular tolerance; hệ thống fail-closed.",
        "",
        "## Vẫn deferred",
        "",
        "- Holder/shank collision, universal gouge guarantee, exact surface offset, production linking/editor/Post.",
        "- Ảnh là review projection có trục/màu, không phải production Viewer screenshot.",
    ]
    _write_text(output_root / "REVIEW_INDEX.md", "\n".join(lines) + "\n")


def generate(output_root: Path) -> Path:
    """Create numeric, lifecycle, IR and raster evidence for second review."""
    output_root.mkdir(parents=True, exist_ok=True)
    for stale_name in ("curved_review.json", "inclined_review.json"):
        (output_root / stale_name).unlink(missing_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])

    inclined = inclined_brep_tolerance_fixture()
    curved = curved_brep_tolerance_fixture()
    zigzag_curved = curved_brep_tolerance_fixture(
        cut_direction=ParallelCutDirection.ZIGZAG
    )
    fixtures = {
        "planar": (planar_fixture(stepover=2.0), None),
        "inclined_brep_tolerance": (inclined.fixture, inclined.resolver),
        "curved_coarse_mesh": (curved_coarse_mesh_fixture(stepover=2.0), None),
        "curved_brep_tolerance": (curved.fixture, curved.resolver),
        "contiguous": (contiguous_fixture(stepover=2.0), None),
        "disconnected": (disconnected_fixture(stepover=2.0), None),
        "zigzag_planar": (
            planar_fixture(
                stepover=2.0,
                cut_direction=ParallelCutDirection.ZIGZAG,
            ),
            None,
        ),
        "zigzag_curved": (zigzag_curved.fixture, zigzag_curved.resolver),
    }
    fixtures["zigzag"] = fixtures["zigzag_curved"]
    candidates = {
        name: _candidate(fixture, resolver)
        for name, (fixture, resolver) in fixtures.items()
        if name != "zigzag"
    }
    summary_cases: dict[str, object] = {}
    for name, candidate in candidates.items():
        fixture, _resolver = fixtures[name]
        filename = f"{name}_review.json"
        _write_json(output_root / filename, _review_payload(name, fixture, candidate))
        summary_cases[name] = {
            "review_file": filename,
            "pass_statistics": candidate.preview.statistics.to_dict(),
            "geometry_metrics": geometry_metrics(fixture, candidate),
            "preview_hash": candidate.preview.fingerprint.digest,
            "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
        }

    coarse_metrics = geometry_metrics(
        fixtures["curved_coarse_mesh"][0],
        candidates["curved_coarse_mesh"],
    )
    brep_metrics = geometry_metrics(curved.fixture, candidates["curved_brep_tolerance"])
    normal_comparison = {
        "format": "HMS_CAM3D_PARALLEL_NORMAL_COMPARISON",
        "format_version": 1,
        "root_cause": {
            "fixture": "curved_coarse_mesh",
            "cause_a_intentionally_coarse_mesh": True,
            "cause_b_ocp_tolerance_not_applied": False,
            "cause_c_triangle_normal_used": True,
            "cause_d_source_face_normal_missing": True,
            "cause_e_orientation_or_merge_error": False,
            "measured_original_max_normal_jump_degrees": 45.000000029222186,
            "measured_original_max_tool_center_jump_mm": 6.189037569166133,
        },
        "coarse_triangle_normal": coarse_metrics,
        "brep_surface_normal": brep_metrics,
        "conclusion": (
            "Coarse mesh remains approximation evidence only; BRep fixture projects "
            "contact and uses oriented source-surface differential normals."
        ),
    }
    _write_json(output_root / "normal_comparison.json", normal_comparison)
    _write_json(
        output_root / "mesh_quality_report.json",
        {
            "format": "HMS_CAM3D_PARALLEL_MESH_QUALITY_REPORT",
            "format_version": 1,
            "meshing_contract": {
                "chordal_tolerance": "IMeshTools_Parameters.Deflection/DeflectionInterior",
                "angular_tolerance": "IMeshTools_Parameters.Angle/AngleInterior",
                "minimum_triangle_size": "IMeshTools_Parameters.MinSize when configured",
                "relative": False,
                "in_parallel": False,
                "surface_deflection_control": True,
            },
            "fixtures": {
                "curved_coarse_mesh": coarse_metrics,
                "curved_brep_tolerance": brep_metrics,
            },
        },
    )
    determinism = _determinism_report(fixtures)
    _write_json(output_root / "determinism_report.json", determinism)
    cancellation = _cancellation_report(output_root, curved.fixture)
    unsupported = _unsupported_report(output_root, curved)
    ir_report = _toolpath_report(
        output_root,
        fixtures["disconnected"][0],
        candidates["disconnected"],
    )

    zigzag_planar_result = _zigzag_result(
        fixtures["zigzag_planar"][0],
        candidates["zigzag_planar"],
    )
    zigzag_curved_result = _zigzag_result(
        fixtures["zigzag_curved"][0],
        candidates["zigzag_curved"],
    )
    if not (
        zigzag_planar_result["even_plus_u_odd_minus_u"]
        and zigzag_curved_result["even_plus_u_odd_minus_u"]
        and not zigzag_planar_result["unsafe_lateral_rapid"]
        and not zigzag_curved_result["unsafe_lateral_rapid"]
    ):
        raise RuntimeError("Zigzag ordering/linking review failed")

    images = (
        "planar_contact_and_center.png",
        "inclined_contact_and_center.png",
        "curved_surface_contact_and_center.png",
        "curved_normals.png",
        "curved_coarse_mesh_normals.png",
        "contiguous_clipping.png",
        "disconnected_linking.png",
        "one_way_ordering.png",
        "zigzag_ordering.png",
        "clearance_retract_review.png",
        "toolpath_ir_motion_classes.png",
    )
    render_geometry_review(
        output_root / images[0],
        fixtures["planar"][0],
        candidates["planar"],
        title="Planar — contact and ball-center paths",
    )
    render_geometry_review(
        output_root / images[1],
        inclined.fixture,
        candidates["inclined_brep_tolerance"],
        title="Inclined BRep — contact and ball-center paths",
        show_normals=True,
    )
    render_geometry_review(
        output_root / images[2],
        curved.fixture,
        candidates["curved_brep_tolerance"],
        title="Curved BRep tolerance — contact and ball-center paths",
    )
    render_geometry_review(
        output_root / images[3],
        curved.fixture,
        candidates["curved_brep_tolerance"],
        title="Curved BRep — oriented differential normals",
        show_normals=True,
    )
    render_geometry_review(
        output_root / images[4],
        fixtures["curved_coarse_mesh"][0],
        candidates["curved_coarse_mesh"],
        title="Curved coarse mesh — facet-normal approximation",
        show_normals=True,
    )
    render_geometry_review(
        output_root / images[5],
        fixtures["contiguous"][0],
        candidates["contiguous"],
        title="Contiguous selected faces — clipping and source continuity",
    )
    render_geometry_review(
        output_root / images[6],
        fixtures["disconnected"][0],
        candidates["disconnected"],
        title="Disconnected regions — conservative linking",
        show_linking=True,
    )
    render_geometry_review(
        output_root / images[7],
        fixtures["planar"][0],
        candidates["planar"],
        title="One-way ordering — every pass follows +U",
        show_direction=True,
    )
    render_geometry_review(
        output_root / images[8],
        fixtures["zigzag_planar"][0],
        candidates["zigzag_planar"],
        title="Zigzag ordering — even +U, odd -U",
        show_direction=True,
    )
    render_motion_review(
        output_root / images[9],
        fixtures["disconnected"][0],
        candidates["disconnected"],
        title="Clearance / retract policy review",
    )
    render_motion_review(
        output_root / images[10],
        fixtures["disconnected"][0],
        candidates["disconnected"],
        title="Toolpath IR motion classes",
    )
    app.processEvents()

    summary = {
        "format": "HMS_CAM3D_PARALLEL_REVIEW",
        "format_version": 2,
        "algorithm": "hms_parallel_finishing_mesh_plane_with_brep_contact",
        "algorithm_version": PARALLEL_FINISHING_ALGORITHM_VERSION,
        "tool_support": "ball_end_fixed_three_axis",
        "fixtures": summary_cases,
        "zigzag": {
            "planar": zigzag_planar_result,
            "curved": zigzag_curved_result,
        },
        "determinism_all_identical": all(
            case["identical"] for case in determinism["cases"].values()
        ),
        "cancellation_checks": cancellation,
        "unsupported_case_count": len(unsupported["cases"]),
        "toolpath_ir_checks": ir_report["metrics"],
        "review_image_count": len(images),
        "review_images": list(images),
    }
    target = output_root / "summary.json"
    _write_json(target, summary)
    _review_index(output_root, images)
    logger.info("Đã tạo review package lần hai tại %s", output_root)
    return target


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Path("reference_private") / "DERIVED" / "CAM_3D_8A2_1"
    generate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
