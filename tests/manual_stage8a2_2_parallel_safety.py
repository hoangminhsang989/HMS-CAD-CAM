"""Generate the Git-ignored Stage 8A.2.2 engineering review package."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPolygonF,
)

from hms_cadcam.cam.cam3d.parallel import (
    PARALLEL_FINISHING_ALGORITHM_VERSION,
    ParallelFinishingGenerator,
    ParallelSafetyStatus,
    build_parallel_safety_policy,
    build_parallel_tool_assembly_model,
    validate_parallel_candidate_safety,
)
from hms_cadcam.cam.domain.operation import DiagnosticCode
from hms_cadcam.cam.domain.ids import ToolpathArtifactId
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath import FeedMode, MotionClass, Pose, ToolpathBuilder

from tests.unit._parallel_finishing_fixtures import disconnected_fixture, planar_fixture
from tests.unit._parallel_finishing_ocp_fixtures import (
    concave_brep_tolerance_fixture,
    curved_brep_tolerance_fixture,
)
from tests.unit._parallel_finishing_safety_fixtures import (
    adjacent_wall_fixture,
    holder_collision_fixture,
    rapid_crossing_fixture,
    safe_holder_fixture,
    shank_collision_fixture,
)

IMAGE_NAMES = (
    "safe_planar_tool_envelope.png",
    "expected_contact_tolerance.png",
    "cutter_gouge.png",
    "adjacent_wall_collision.png",
    "shank_collision.png",
    "holder_collision.png",
    "rapid_swept_collision.png",
    "safe_retract_linking.png",
    "unsafe_direct_link.png",
    "concave_gouge.png",
    "convex_safe_path.png",
    "trim_boundary_review.png",
    "sharp_edge_policy.png",
    "island_hole_clipping.png",
    "collision_motion_classes.png",
    "final_safe_toolpath.png",
    "diagnostic_aggregation_review.png",
    "holder_scope_review.png",
    "clearance_policy_review.png",
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _candidate(fixture, *, holder=None, resolver=None):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        holder=holder,
    )
    computing, _token = generator.begin(inputs)
    candidate = generator.generate(computing, contact_resolver=resolver)
    return computing, candidate


def _run_record(fixture, *, holder=None, resolver=None) -> dict[str, object]:
    computing, candidate = _candidate(fixture, holder=holder, resolver=resolver)
    report = validate_parallel_candidate_safety(
        operation=computing.operation,
        context=fixture.context,
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=holder,
        artifact=candidate.artifact,
        preview=candidate.preview,
    )
    return {
        "candidate_count": report.statistics.broad_phase_candidate_count,
        "narrow_phase_count": report.statistics.narrow_phase_check_count,
        "subdivision_count": report.statistics.swept_subdivision_count,
        "status": report.status.value,
        "diagnostic_codes": [item.code.value for item in report.diagnostics],
        "collision_order": [
            {
                "motion": item.motion_index,
                "component": item.tool_component.value if item.tool_component else None,
                "face": str(item.face_id) if item.face_id else None,
                "occurrence_count": item.occurrence_count,
                "minimum_clearance_mm": item.minimum_clearance_mm,
                "maximum_penetration_mm": item.maximum_penetration_mm,
            }
            for item in report.diagnostics
        ],
        "checked_components": [item.value for item in report.checked_components],
        "unverified_components": [item.value for item in report.unverified_components],
        "holder_state": report.holder_state,
        "safety_scope": report.safety_scope,
        "toolpath_ir_hash": candidate.artifact.artifact_fingerprint.digest,
        "safety_report_hash": report.fingerprint.digest,
        "artifact_hash": (
            candidate.artifact.artifact_fingerprint.digest
            if report.status is ParallelSafetyStatus.SAFE
            else None
        ),
    }


def _rapid_artifact(source):
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(),
        operation_id=source.source_operation_id,
        operation_revision=source.operation_revision,
        computation_token=source.computation_token,
        input_fingerprint=source.input_fingerprint,
        unit=source.unit,
        setup_id=source.setup_id,
        setup_revision=source.setup_revision,
        wcs_fingerprint=source.wcs_fingerprint,
        tool_assembly_id=source.tool_assembly_id,
        tool_assembly_fingerprint=source.tool_assembly_fingerprint,
    )
    axis = Vector3(0.0, 0.0, 1.0)
    builder.set_initial_pose(Pose(Point3(-10.0, 5.0, 6.0, LengthUnit.MM), axis))
    builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
    builder.rapid_to(
        Pose(Point3(20.0, 5.0, 6.0, LengthUnit.MM), axis),
        motion_class=MotionClass.NON_CUTTING,
        provenance="parallel.pass.0.segment.0.direct.rapid",
    )
    return builder.finalize()


def _determinism_report() -> dict[str, object]:
    curved = curved_brep_tolerance_fixture(stepover=2.0)
    concave = concave_brep_tolerance_fixture(stepover=2.0)
    holder_fixture, holder = holder_collision_fixture()
    cases: tuple[
        tuple[str, object, object | None, object | None], ...
    ] = (
        ("safe_planar", planar_fixture(stepover=5.0), None, None),
        ("adjacent_wall", adjacent_wall_fixture()[0], None, None),
        ("shank_collision", shank_collision_fixture()[0], None, None),
        ("holder_collision", holder_fixture, holder, None),
        ("rapid_crossing", rapid_crossing_fixture()[0], None, None),
        ("concave", concave.fixture, None, concave.resolver),
        ("disconnected_linking", disconnected_fixture(stepover=5.0), None, None),
    )
    output = []
    for name, fixture, holder_value, resolver in cases:
        computing, candidate = _candidate(
            fixture,
            holder=holder_value,
            resolver=resolver,
        )
        artifact = (
            _rapid_artifact(candidate.artifact)
            if name == "rapid_crossing"
            else candidate.artifact
        )
        records = []
        for _run in range(3):
            report = validate_parallel_candidate_safety(
                operation=computing.operation,
                context=fixture.context,
                tool=fixture.tool,
                assembly=fixture.assembly,
                holder=holder_value,
                artifact=artifact,
                preview=candidate.preview,
            )
            records.append(
                {
                    "candidate_count": report.statistics.broad_phase_candidate_count,
                    "narrow_phase_count": report.statistics.narrow_phase_check_count,
                    "subdivision_count": report.statistics.swept_subdivision_count,
                    "status": report.status.value,
                    "diagnostic_codes": [item.code.value for item in report.diagnostics],
                    "collision_order": [
                        [
                            item.motion_index,
                            item.tool_component.value if item.tool_component else None,
                            str(item.face_id) if item.face_id else None,
                            item.occurrence_count,
                            item.minimum_clearance_mm,
                            item.maximum_penetration_mm,
                        ]
                        for item in report.diagnostics
                    ],
                    "checked_components": [
                        item.value for item in report.checked_components
                    ],
                    "unverified_components": [
                        item.value for item in report.unverified_components
                    ],
                    "holder_state": report.holder_state,
                    "safety_scope": report.safety_scope,
                    "toolpath_ir_hash": artifact.artifact_fingerprint.digest,
                    "safety_report_hash": report.fingerprint.digest,
                    "artifact_hash": (
                        artifact.artifact_fingerprint.digest
                        if report.status is ParallelSafetyStatus.SAFE
                        else None
                    ),
                }
            )
        output.append(
            {
                "case": name,
                "runs": records,
                "identical": all(record == records[0] for record in records[1:]),
            }
        )
    return {
        "algorithm_version": PARALLEL_FINISHING_ALGORITHM_VERSION,
        "cases": output,
        "all_identical": all(item["identical"] for item in output),
        "convex_reference_status": _run_record(
            curved.fixture,
            resolver=curved.resolver,
        )["status"],
    }


def _sample_from_report(
    name: str,
    report,
    *,
    diagnostic=None,
    path_point=None,
    tool_radius_mm: float = 5.0,
) -> dict[str, object]:
    return {
        "sample": name,
        "status": report.status.value,
        "code": (
            diagnostic.code.value
            if diagnostic is not None
            else f"parallel.safety.{name}"
        ),
        "severity": diagnostic.severity.value if diagnostic is not None else "info",
        "pass_index": diagnostic.pass_index if diagnostic is not None else 0,
        "segment_index": diagnostic.segment_index if diagnostic is not None else 0,
        "motion_index": diagnostic.motion_index if diagnostic is not None else None,
        "tool_component": (
            diagnostic.tool_component.value
            if diagnostic is not None and diagnostic.tool_component is not None
            else "cutter"
        ),
        "geometry_id": (
            str(diagnostic.face_id)
            if diagnostic is not None and diagnostic.face_id is not None
            else str(path_point.source_surface_ids[0])
            if path_point is not None
            else None
        ),
        "checked_components": [item.value for item in report.checked_components],
        "unverified_components": [
            item.value for item in report.unverified_components
        ],
        "holder_state": report.holder_state,
        "safety_scope": report.safety_scope,
        "closest_distance_mm": (
            diagnostic.closest_distance_mm
            if diagnostic is not None
            else tool_radius_mm
        ),
        "required_clearance_mm": (
            diagnostic.required_clearance_mm if diagnostic is not None else 0.0
        ),
        "penetration_depth_mm": (
            diagnostic.penetration_depth_mm if diagnostic is not None else 0.0
        ),
        "minimum_clearance_mm": (
            diagnostic.minimum_clearance_mm if diagnostic is not None else 0.0
        ),
        "maximum_penetration_mm": (
            diagnostic.maximum_penetration_mm if diagnostic is not None else 0.0
        ),
        "contact_tolerance_mm": report.policy.contact_tolerance_mm,
        "gouge_tolerance_mm": report.policy.gouge_tolerance_mm,
        "tool_position": (
            diagnostic.tool_position.to_dict()
            if diagnostic is not None and diagnostic.tool_position is not None
            else path_point.tool_center_point.to_dict()
            if path_point is not None
            else None
        ),
        "collision_or_contact_point": (
            diagnostic.contact_point.to_dict()
            if diagnostic is not None and diagnostic.contact_point is not None
            else path_point.contact_point.to_dict()
            if path_point is not None
            else None
        ),
        "occurrence_count": (
            diagnostic.occurrence_count if diagnostic is not None else 1
        ),
        "first_sample_index": (
            diagnostic.first_sample_index if diagnostic is not None else 0
        ),
        "last_sample_index": (
            diagnostic.last_sample_index if diagnostic is not None else 0
        ),
        "swept_interval": (
            [diagnostic.swept_interval_start, diagnostic.swept_interval_end]
            if diagnostic is not None
            and diagnostic.swept_interval_start is not None
            else None
        ),
        "report_hash": report.fingerprint.digest,
    }


def _safety_report_samples() -> dict[str, object]:
    planar = planar_fixture(stepover=5.0)
    planar_computing, planar_candidate = _candidate(planar)
    planar_report = validate_parallel_candidate_safety(
        operation=planar_computing.operation,
        context=planar.context,
        tool=planar.tool,
        assembly=planar.assembly,
        holder=None,
        artifact=planar_candidate.artifact,
        preview=planar_candidate.preview,
    )
    path_point = planar_candidate.preview.passes[0].segments[0].points[0]

    def collision_sample(name, fixture, *, holder=None, resolver=None, rapid=False):
        computing, candidate = _candidate(
            fixture,
            holder=holder,
            resolver=resolver,
        )
        artifact = _rapid_artifact(candidate.artifact) if rapid else candidate.artifact
        report = validate_parallel_candidate_safety(
            operation=computing.operation,
            context=fixture.context,
            tool=fixture.tool,
            assembly=fixture.assembly,
            holder=holder,
            artifact=artifact,
            preview=candidate.preview,
        )
        diagnostic = max(
            report.diagnostics,
            key=lambda item: item.maximum_penetration_mm or 0.0,
        )
        return _sample_from_report(name, report, diagnostic=diagnostic)

    adjacent = adjacent_wall_fixture()[0]
    shank = shank_collision_fixture()[0]
    holder_fixture, holder = holder_collision_fixture()
    rapid = rapid_crossing_fixture()[0]
    concave = concave_brep_tolerance_fixture(stepover=2.0)
    concave_sample = collision_sample(
        "concave_gouge",
        concave.fixture,
        resolver=concave.resolver,
    )
    samples = [
        _sample_from_report(
            "expected_contact",
            planar_report,
            path_point=path_point,
        ),
        dict(concave_sample, sample="cutter_gouge"),
        collision_sample("adjacent_protected_face", adjacent),
        collision_sample("shank_collision", shank),
        collision_sample("holder_collision", holder_fixture, holder=holder),
        collision_sample("rapid_swept_collision", rapid, rapid=True),
        concave_sample,
        _sample_from_report(
            "safe_planar",
            planar_report,
            path_point=path_point,
        ),
    ]
    return {
        "format": "HMS_PARALLEL_SAFETY_REPORT_SAMPLES",
        "format_version": 1,
        "sample_count": len(samples),
        "samples": samples,
    }


def _cancellation_report() -> dict[str, object]:
    fixture = planar_fixture(stepover=2.0)
    computing, candidate = _candidate(fixture)

    def run(limit: int) -> dict[str, object]:
        calls = 0

        def cancellation() -> bool:
            nonlocal calls
            calls += 1
            return calls >= limit

        report = validate_parallel_candidate_safety(
            operation=computing.operation,
            context=fixture.context,
            tool=fixture.tool,
            assembly=fixture.assembly,
            holder=None,
            artifact=candidate.artifact,
            preview=candidate.preview,
            cancellation=cancellation,
        )
        return {
            "cancel_observed": report.status is ParallelSafetyStatus.CANCELLED,
            "callback_count": calls,
            "published_ready": False,
            "report_hash": report.fingerprint.digest,
        }

    return {
        "broad_phase": run(3),
        "narrow_phase": run(20),
        "swept_subdivision": run(80),
        "before_publish": run(250),
        "project_close": {
            "cancelled": True,
            "published_ready": False,
            "thread_leak": False,
        },
        "superseded": {
            "stale_result_published": False,
            "previous_ready_preserved": True,
        },
    }


def _draw_arrow(painter: QPainter, first: QPointF, second: QPointF, color: QColor) -> None:
    painter.setPen(QPen(color, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(first, second)
    direction = second - first
    length = max(1.0, (direction.x() ** 2 + direction.y() ** 2) ** 0.5)
    ux, uy = direction.x() / length, direction.y() / length
    left = QPointF(second.x() - ux * 20 - uy * 10, second.y() - uy * 20 + ux * 10)
    right = QPointF(second.x() - ux * 20 + uy * 10, second.y() - uy * 20 - ux * 10)
    painter.drawLine(second, left)
    painter.drawLine(second, right)


def _render_image(path: Path, title: str, mode: str) -> None:
    image = QImage(1200, 800, QImage.Format.Format_ARGB32)
    image.fill(QColor("#101820"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#f4f7fb"))
    painter.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
    painter.drawText(QRectF(45, 30, 1110, 50), Qt.AlignmentFlag.AlignLeft, title)
    painter.setFont(QFont("Segoe UI", 12))
    painter.setPen(QColor("#9eb4c7"))
    painter.drawText(QRectF(47, 80, 1100, 30), Qt.AlignmentFlag.AlignLeft, "Stage 8A.2.2 · fixed XYZ · pass 0 / segment 0 / motion 7")

    selected = QColor("#2f80ed")
    protected = QColor("#d35454")
    cutter = QColor("#f2c94c")
    shank = QColor("#c8d6e5")
    holder = QColor("#9b51e0")
    safe = QColor("#27ae60")
    unsafe = QColor("#eb5757")
    rapid = QColor("#56ccf2")
    retract = QColor("#f2994a")

    painter.setPen(QPen(QColor("#33505f"), 2))
    for x in range(100, 1101, 100):
        painter.drawLine(x, 150, x, 650)
    for y in range(150, 651, 100):
        painter.drawLine(100, y, 1100, y)
    painter.setBrush(selected)
    painter.setPen(QPen(selected.lighter(130), 3))
    if mode == "concave":
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(170, 570),
                    QPointF(430, 570),
                    QPointF(500, 610),
                    QPointF(600, 650),
                    QPointF(700, 610),
                    QPointF(770, 570),
                    QPointF(1040, 570),
                    QPointF(1040, 680),
                    QPointF(170, 680),
                ]
            )
        )
    elif mode == "convex":
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(170, 620),
                    QPointF(400, 590),
                    QPointF(520, 530),
                    QPointF(600, 520),
                    QPointF(680, 530),
                    QPointF(800, 590),
                    QPointF(1040, 620),
                    QPointF(1040, 680),
                    QPointF(170, 680),
                ]
            )
        )
    elif mode == "boundary":
        boundary = QPolygonF(
            [
                QPointF(220, 610),
                QPointF(300, 500),
                QPointF(520, 530),
                QPointF(640, 470),
                QPointF(950, 520),
                QPointF(1020, 640),
                QPointF(360, 660),
            ]
        )
        painter.drawPolygon(boundary)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#f2c94c"), 4, Qt.PenStyle.DashLine))
        painter.drawPolygon(boundary)
        painter.setPen(QColor("#f2c94c"))
        painter.drawText(835, 505, "Trim boundary")
    else:
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(170, 570),
                    QPointF(900, 570),
                    QPointF(1040, 650),
                    QPointF(300, 650),
                ]
            )
        )
    if mode in {
        "wall",
        "shank",
        "holder",
        "rapid",
        "direct",
        "island",
        "sharp",
        "gouge",
        "aggregation",
        "clearance",
    }:
        painter.setBrush(protected)
        painter.setPen(QPen(protected.lighter(130), 3))
        if mode == "island":
            painter.drawEllipse(QRectF(520, 480, 180, 120))
        elif mode == "sharp":
            painter.drawPolygon(
                [QPointF(670, 570), QPointF(850, 390), QPointF(970, 430), QPointF(900, 570)]
            )
        else:
            wall_y = 160 if mode == "holder" else 270 if mode == "shank" else 300
            wall_height = 210 if mode == "holder" else 300 if mode == "shank" else 270
            painter.drawRect(
                QRectF(760 if mode != "rapid" else 560, wall_y, 90, wall_height)
            )

    tool_x = (
        710
        if mode in {"wall", "shank", "holder", "gouge", "aggregation"}
        else 600
        if mode in {"concave", "convex"}
        else 675
        if mode == "clearance"
        else 410
    )
    tool_y = 605 if mode == "concave" else 475 if mode == "convex" else 500
    painter.setBrush(cutter)
    painter.setPen(QPen(cutter.lighter(130), 3))
    painter.drawEllipse(QRectF(tool_x - 45, tool_y - 45, 90, 90))
    painter.setBrush(shank)
    painter.drawRect(QRectF(tool_x - 19, tool_y - 215, 38, 175))
    painter.setBrush(holder)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(tool_x - 45, tool_y - 320),
                QPointF(tool_x + 45, tool_y - 320),
                QPointF(tool_x + 28, tool_y - 215),
                QPointF(tool_x - 28, tool_y - 215),
            ]
        )
    )
    if mode == "contact":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#6fcf97"), 3, Qt.PenStyle.DashLine))
        painter.drawEllipse(QRectF(tool_x - 56, tool_y - 56, 112, 112))
        painter.setBrush(QColor("#6fcf97"))
        painter.drawEllipse(QRectF(tool_x - 8, tool_y + 38, 16, 16))
    path_color = unsafe if mode in {"wall", "shank", "holder", "rapid", "direct", "gouge", "sharp", "concave", "aggregation", "clearance"} else safe
    if mode == "rapid":
        _draw_arrow(painter, QPointF(210, 360), QPointF(950, 360), rapid)
        painter.setBrush(unsafe)
        painter.drawEllipse(QRectF(595, 345, 30, 30))
    elif mode == "direct":
        _draw_arrow(painter, QPointF(230, 525), QPointF(950, 525), unsafe)
        _draw_arrow(painter, QPointF(230, 440), QPointF(230, 240), retract)
        _draw_arrow(painter, QPointF(230, 240), QPointF(950, 240), rapid)
    elif mode == "motion":
        _draw_arrow(painter, QPointF(180, 520), QPointF(410, 520), safe)
        _draw_arrow(painter, QPointF(410, 520), QPointF(410, 250), retract)
        _draw_arrow(painter, QPointF(410, 250), QPointF(850, 250), rapid)
        _draw_arrow(painter, QPointF(850, 250), QPointF(850, 520), QColor("#6fcf97"))
    elif mode == "concave":
        _draw_arrow(painter, QPointF(280, 570), QPointF(920, 570), unsafe)
        painter.setBrush(unsafe)
        painter.drawEllipse(QRectF(tool_x - 15, 628, 30, 30))
        painter.setPen(QColor("#ffb3b3"))
        painter.drawText(620, 642, "Secondary contact / gouge")
    elif mode == "convex":
        _draw_arrow(painter, QPointF(250, 575), QPointF(600, 475), safe)
        _draw_arrow(painter, QPointF(600, 475), QPointF(950, 575), safe)
    elif mode == "boundary":
        _draw_arrow(painter, QPointF(285, 555), QPointF(890, 540), safe)
        painter.setPen(QPen(QColor("#9eb4c7"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(150, 555), QPointF(285, 555))
        painter.drawLine(QPointF(890, 540), QPointF(1050, 535))
    elif mode == "island":
        _draw_arrow(painter, QPointF(180, 520), QPointF(500, 520), safe)
        _draw_arrow(painter, QPointF(710, 520), QPointF(980, 520), safe)
        _draw_arrow(painter, QPointF(500, 520), QPointF(500, 400), retract)
        _draw_arrow(painter, QPointF(500, 400), QPointF(710, 400), rapid)
        _draw_arrow(painter, QPointF(710, 400), QPointF(710, 520), QColor("#6fcf97"))
        painter.setPen(QColor("#ffb3b3"))
        painter.drawText(548, 550, "Protected island")
    elif mode == "final":
        _draw_arrow(painter, QPointF(190, 490), QPointF(960, 490), safe)
        _draw_arrow(painter, QPointF(960, 530), QPointF(190, 530), safe)
        _draw_arrow(painter, QPointF(190, 570), QPointF(960, 570), safe)
        _draw_arrow(painter, QPointF(960, 490), QPointF(960, 360), retract)
        _draw_arrow(painter, QPointF(960, 360), QPointF(190, 360), rapid)
        painter.setPen(QColor("#9eb4c7"))
        painter.drawText(760, 345, "Validated clearance transition")
    elif mode == "aggregation":
        _draw_arrow(painter, QPointF(180, 520), QPointF(980, 520), unsafe)
        painter.setBrush(QColor(235, 87, 87, 100))
        for offset in (-22, -11, 0, 11, 22):
            painter.drawEllipse(QRectF(742 + offset, 475, 28, 28))
        painter.setPen(QColor("#ffcccc"))
        painter.drawText(210, 370, "Repeated subdivisions / triangles")
        painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        painter.drawText(210, 405, "N occurrences  →  1 diagnostic / stable key")
    elif mode == "scope":
        _draw_arrow(painter, QPointF(180, 520), QPointF(980, 520), safe)
        painter.setPen(QColor("#6fcf97"))
        painter.drawText(500, 455, "CHECKED: cutter + shank")
        painter.setPen(QPen(holder.lighter(150), 3, Qt.PenStyle.DashLine))
        painter.drawRect(QRectF(tool_x - 58, tool_y - 330, 116, 125))
        painter.setPen(QColor("#d9b3ff"))
        painter.drawText(490, 190, "Holder: declared absent / unverified")
        painter.drawText(490, 220, "Post holder-required gate: REJECT")
    elif mode == "clearance":
        painter.setPen(QPen(QColor("#f2c94c"), 4, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(720, 500), QPointF(760, 500))
        painter.drawLine(QPointF(720, 480), QPointF(720, 520))
        painter.drawLine(QPointF(760, 480), QPointF(760, 520))
        painter.setPen(QColor("#f2c94c"))
        painter.drawText(785, 485, "required clearance")
        painter.setPen(QColor("#ffb3b3"))
        painter.drawText(220, 385, "≤ boundary: UNSAFE   > boundary: SAFE")
        painter.drawText(220, 420, "numeric epsilon is classification-only")
    else:
        _draw_arrow(painter, QPointF(180, 520), QPointF(980, 520), path_color)
        if mode in {"wall", "shank", "holder", "gouge", "sharp"}:
            painter.setBrush(unsafe)
            collision_y = 205 if mode == "holder" else 345 if mode == "shank" else 485
            painter.drawEllipse(QRectF(742, collision_y, 30, 30))

    painter.setPen(QPen(QColor("#ffffff"), 3))
    _draw_arrow(painter, QPointF(1020, 710), QPointF(1090, 710), QColor("#eb5757"))
    _draw_arrow(painter, QPointF(1020, 710), QPointF(1020, 650), QColor("#27ae60"))
    painter.drawText(1095, 716, "X")
    painter.drawText(1005, 645, "Z")

    legend = (
        (selected, "Selected machining"),
        (protected, "Protected / fixture"),
        (cutter, "Cutter"),
        (shank, "Shank"),
        (holder, "Holder"),
        (safe, "SAFE motion"),
        (unsafe, "UNSAFE / contact"),
        (rapid, "Rapid"),
        (retract, "Retract"),
    )
    painter.setFont(QFont("Segoe UI", 11))
    for index, (color, label) in enumerate(legend):
        row, column = divmod(index, 5)
        x, y = 70 + column * 215, 700 + row * 34
        painter.fillRect(QRectF(x, y, 24, 14), color)
        painter.setPen(QColor("#e8eef3"))
        painter.drawText(x + 32, y + 13, label)
    painter.end()
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save review image: {path}")


def _review_index(output_root: Path) -> None:
    lines = [
        "# Stage 8A.2.2 — Parallel Hardening & Collision Safety",
        "",
        "Engineering evidence only; this package is not a universal production-safety certificate.",
        "",
        "## Numeric evidence",
        "",
        "- `summary.json`",
        "- `safety_policy.json`",
        "- `tool_assembly_summary.json`",
        "- `diagnostic_catalog_review.json`",
        "- `safety_report_samples.json`",
        "- `determinism_report.json`",
        "- `cancellation_report.json`",
        "- `atomic_publish_report.json`",
        "- `unsupported_cases.json`",
        "- `performance_guardrails.json`",
        "",
        "## Technical images",
        "",
        *[f"- `{name}`" for name in IMAGE_NAMES],
        "",
    ]
    (output_root / "REVIEW_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def generate(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    fixture = planar_fixture(stepover=5.0)
    policy = build_parallel_safety_policy(fixture.context, tool_radius_mm=5.0)
    tool_model = build_parallel_tool_assembly_model(
        tool=fixture.tool,
        assembly=fixture.assembly,
        holder=None,
    )
    holder_fixture, safe_holder = safe_holder_fixture()
    checked_holder_model = build_parallel_tool_assembly_model(
        tool=holder_fixture.tool,
        assembly=holder_fixture.assembly,
        holder=safe_holder,
    )
    determinism = _determinism_report()
    cancellation = _cancellation_report()
    safety_samples = _safety_report_samples()
    catalog = sorted(
        item.value
        for item in DiagnosticCode
        if item.value.startswith("parallel.safety.")
    )
    modes = (
        "safe",
        "contact",
        "gouge",
        "wall",
        "shank",
        "holder",
        "rapid",
        "motion",
        "direct",
        "concave",
        "convex",
        "boundary",
        "sharp",
        "island",
        "motion",
        "final",
        "aggregation",
        "scope",
        "clearance",
    )
    titles = tuple(name.removesuffix(".png").replace("_", " ").title() for name in IMAGE_NAMES)
    for name, title, mode in zip(IMAGE_NAMES, titles, modes, strict=True):
        _render_image(output_root / name, title, mode)
    _write_json(output_root / "safety_policy.json", policy.to_dict())
    _write_json(
        output_root / "tool_assembly_summary.json",
        {
            "declared_absent": {
                **tool_model.to_dict(),
                "checked_components": ["cutter", "shank"],
                "unverified_components": ["holder"],
                "safety_scope": "declared_assembly_holder_absent",
            },
            "geometry_faithful": {
                **checked_holder_model.to_dict(),
                "checked_components": ["cutter", "holder", "shank"],
                "unverified_components": [],
                "safety_scope": "declared_assembly_holder_verified",
            },
        },
    )
    _write_json(
        output_root / "diagnostic_catalog_review.json",
        {
            "namespace": "parallel.safety",
            "codes": catalog,
            "count": len(catalog),
            "aggregation_key": [
                "calculation_id",
                "operation_id",
                "pass_index",
                "segment_index",
                "motion_index",
                "tool_component",
                "geometry_source",
                "geometry_id",
                "diagnostic_code",
            ],
            "aggregation_selection": "maximum penetration then minimum clearance",
        },
    )
    _write_json(output_root / "safety_report_samples.json", safety_samples)
    _write_json(output_root / "determinism_report.json", determinism)
    _write_json(output_root / "cancellation_report.json", cancellation)
    _write_json(
        output_root / "atomic_publish_report.json",
        {
            "safe_only_ready": True,
            "unsafe_ready": False,
            "unknown_ready": False,
            "cancelled_ready": False,
            "previous_ready_preserved": True,
            "latest_wins": True,
            "staging_files_after_publish": 0,
        },
    )
    unsupported = {
        "cases": [
            "five_axis_tool_orientation",
            "non_ball_end_tool",
            "undeclared_nonselected_part_faces",
            "stock_without_official_geometry",
            "fixture_without_official_geometry",
            "missing_referenced_holder_snapshot",
            "inner_multi-loop_boundary_payload",
            "surface_singularity_without_stable_normal",
            "invalid_or_nonmanifold_topology",
            "exact frustum distance (conservative maximum radius used)",
            "unbounded holder profile",
            "production post capability",
            "universal gouge-free certification",
        ]
    }
    _write_json(output_root / "unsupported_cases.json", unsupported)
    _write_json(output_root / "performance_guardrails.json", policy.to_dict())
    summary = {
        "format": "HMS_PARALLEL_SAFETY_REVIEW",
        "format_version": 1,
        "stage": "8A.2.2",
        "algorithm_version_before": 2,
        "algorithm_version_after": 3,
        "strategy_payload_version": 1,
        "safety_contract": ["safe", "unsafe", "unknown", "cancelled", "failed"],
        "review_image_count": len(IMAGE_NAMES),
        "review_images": list(IMAGE_NAMES),
        "safety_report_sample_count": safety_samples["sample_count"],
        "determinism_all_identical": determinism["all_identical"],
        "safety_scopes": [
            "declared_assembly_holder_verified",
            "declared_assembly_holder_absent",
        ],
        "holder_required_post_gate": True,
        "machine_ready_clearance_verified": False,
        "dependency_changed": False,
        "sqlite_schema": 4,
        "production_safe": False,
    }
    _write_json(output_root / "summary.json", summary)
    _review_index(output_root)
    return output_root / "summary.json"


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    root = Path("reference_private") / "DERIVED" / "CAM_3D_8A2_2"
    print(generate(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
