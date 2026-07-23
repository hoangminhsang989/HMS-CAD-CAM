"""Create the calculation-backed, Git-ignored Stage 8A.3.1 review package."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont
from hms_cadcam.cam.toolpath import MotionClass

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

try:
    from tools.zlevel_review_evidence import (
        ALGORITHM_VERSION,
        PAYLOAD_VERSION,
        STRATEGY,
        CalculationEvidence,
        EvidenceBundle,
        build_evidence_bundle,
        canonical_hash,
    )
except ModuleNotFoundError:  # Direct execution: python tools/create_....py
    from zlevel_review_evidence import (  # type: ignore[no-redef]
        ALGORITHM_VERSION,
        PAYLOAD_VERSION,
        STRATEGY,
        CalculationEvidence,
        EvidenceBundle,
        build_evidence_bundle,
        canonical_hash,
    )


IMAGE_SOURCES = {
    "zlevel_vertical_wall": "vertical_wall",
    "zlevel_cylinder_closed_loops": "cylinder",
    "zlevel_conical_wall": "cone",
    "zlevel_freeform_steep": "freeform_steep",
    "zlevel_trimmed_boundary": "trimmed_boundary",
    "zlevel_inner_hole": "inner_hole",
    "zlevel_disconnected_regions": "disconnected_regions",
    "zlevel_shared_edge": "shared_edge",
    "zlevel_near_tangent_policy": "near_tangent",
    "zlevel_allowance": "allowance",
    "zlevel_level_schedule": "partial_final_step",
    "zlevel_contour_ordering": "contour_ordering",
    "zlevel_conservative_linking": "conservative_linking",
    "zlevel_tool_center_contact": "allowance",
    "zlevel_toolpath_ir": "conservative_linking",
}
NAMES = tuple(IMAGE_SOURCES)
MONTAGE_NAME = "CAM_3D_8A3_1_Z_LEVEL_MONTAGE.png"
SIZE = (1200, 800)
BG = (17, 23, 32)
PANEL = (28, 37, 50)
GRID = (67, 84, 104)
TEXT = (235, 241, 248)
MUTED = (166, 182, 199)
CONTACT = (255, 187, 82)
PATH = (61, 218, 166)
LINK = (105, 168, 255)
RAPID = (213, 128, 255)
WARNING = (255, 105, 105)
SURFACE = (93, 113, 138)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _polyline(
    draw: ImageDraw.ImageDraw,
    points: Iterable[tuple[float, float]],
    color: tuple[int, int, int],
    width: int = 4,
    *,
    close: bool = False,
) -> None:
    values = list(points)
    if close and values:
        values.append(values[0])
    if len(values) >= 2:
        draw.line(values, fill=color, width=width, joint="curve")


def _arrow(
    draw: ImageDraw.ImageDraw,
    first: tuple[float, float],
    second: tuple[float, float],
    color: tuple[int, int, int],
    *,
    width: int = 4,
) -> None:
    draw.line((first, second), fill=color, width=width)
    dx, dy = second[0] - first[0], second[1] - first[1]
    length = max(1.0, math.hypot(dx, dy))
    ux, uy = dx / length, dy / length
    left = (
        second[0] - ux * 14 - uy * 7,
        second[1] - uy * 14 + ux * 7,
    )
    right = (
        second[0] - ux * 14 + uy * 7,
        second[1] - uy * 14 - ux * 7,
    )
    draw.polygon((second, left, right), fill=color)


def _base(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (22, 22, 1178, 778),
        radius=18,
        fill=PANEL,
        outline=GRID,
        width=2,
    )
    draw.text((48, 38), title, fill=TEXT, font=_font(30, bold=True))
    draw.text((48, 82), subtitle, fill=MUTED, font=_font(18))
    draw.rounded_rectangle(
        (48, 126, 1152, 650),
        radius=12,
        fill=BG,
        outline=GRID,
        width=1,
    )
    return image, draw


def _footer(
    draw: ImageDraw.ImageDraw,
    calculation: CalculationEvidence,
    detail: str,
) -> None:
    record = calculation.source_record
    draw.text(
        (50, 672),
        (
            f"fixture={calculation.spec.fixture_id}  "
            f"calc={record['calculation_id']}  "
            f"IR={record['toolpath_ir_hash'][:16]}…"
        ),
        fill=MUTED,
        font=_font(15),
    )
    draw.text((50, 705), detail, fill=TEXT, font=_font(17))
    draw.text(
        (50, 738),
        (
            f"preview={calculation.candidate.preview.fingerprint.digest[:16]}…  "
            f"safety={record['safety_status']} / "
            f"{record['safety_report_hash'][:16]}…"
        ),
        fill=MUTED,
        font=_font(15),
    )


def _preview_points(
    calculation: CalculationEvidence,
    *,
    projection: str,
) -> list[tuple[Any, list[tuple[float, float]]]]:
    frame = calculation.candidate.preview.frame
    output = []
    for level_pass in calculation.candidate.preview.passes:
        for contour in level_pass.segments:
            points = []
            for point in contour.points:
                u, v, w = frame.coordinates(point.tool_center_point)
                points.append(
                    (u, v)
                    if projection == "uv"
                    else (u, w)
                    if projection == "uw"
                    else (v, w)
                )
            output.append((contour, points))
    return output


def _transform(
    series: Iterable[Iterable[tuple[float, float]]],
    box: tuple[int, int, int, int] = (100, 165, 1100, 610),
) -> Any:
    values = [point for points in series for point in points]
    if not values:
        return lambda point: (box[0], box[1])
    min_x = min(point[0] for point in values)
    max_x = max(point[0] for point in values)
    min_y = min(point[1] for point in values)
    max_y = max(point[1] for point in values)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min(
        (box[2] - box[0]) / span_x,
        (box[3] - box[1]) / span_y,
    ) * 0.88
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    screen_x = (box[0] + box[2]) / 2.0
    screen_y = (box[1] + box[3]) / 2.0

    def apply(point: tuple[float, float]) -> tuple[float, float]:
        return (
            screen_x + (point[0] - center_x) * scale,
            screen_y - (point[1] - center_y) * scale,
        )

    return apply


def _render_preview(
    name: str,
    calculation: CalculationEvidence,
    *,
    title: str,
    subtitle: str,
    projection: str = "uv",
    show_points: bool = True,
) -> Image.Image:
    image, draw = _base(title, subtitle)
    contours = _preview_points(calculation, projection=projection)
    transform = _transform(points for _contour, points in contours)
    for contour, points in contours:
        screen = [transform(point) for point in points]
        _polyline(
            draw,
            screen,
            WARNING if contour.loop_type.value == "inner" else PATH,
            5,
            close=contour.closed,
        )
        if show_points:
            for x, y in screen:
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=CONTACT)
        if screen:
            draw.text(
                (screen[0][0] + 8, screen[0][1] - 22),
                (
                    f"L{contour.pass_index}/C{contour.segment_index} "
                    f"{contour.region_id}"
                ),
                fill=MUTED,
                font=_font(14),
            )
    topology = calculation.topology
    detail = (
        f"levels={len(calculation.candidate.preview.schedule.levels)}  "
        f"raw={topology['raw_segment_count']} → "
        f"dedup={topology['deduplicated_segment_count']}  "
        f"contours={topology['closed_contour_count']} closed/"
        f"{topology['open_contour_count']} open  "
        f"points={calculation.candidate.preview.statistics.point_count}"
    )
    _footer(draw, calculation, detail)
    return image


def _render_cylinder(calculation: CalculationEvidence) -> Image.Image:
    image = _render_preview(
        "zlevel_cylinder_closed_loops",
        calculation,
        title="Periodic cylinder · actual closed contours",
        subtitle="Tool-center U/V loops rendered from ZLevelPreview",
        projection="uv",
    )
    draw = ImageDraw.Draw(image)
    contours = _preview_points(calculation, projection="uv")
    transform = _transform(points for _contour, points in contours)
    seam_start = transform((5.0, -6.0))
    seam_end = transform((5.0, 6.0))
    draw.line((seam_start, seam_end), fill=WARNING, width=2)
    draw.text(
        (seam_start[0] + 8, 144),
        (
            f"periodic seam · endpoints={calculation.topology['seam_candidate_count']} "
            f"· dedup={calculation.topology['seam_dedup_count']}"
        ),
        fill=WARNING,
        font=_font(16),
    )
    return image


def _render_freeform(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Freeform steep surface · actual traced contours",
        "Non-uniform verified source mesh; points are ZLevelPreview samples",
    )
    contours = _preview_points(calculation, projection="uv")
    frame = calculation.candidate.preview.frame
    mesh_uv = [
        frame.coordinates(point)[:2]
        for point in calculation.spec.fixture.context.calculation_mesh.vertices
    ]
    transform = _transform([mesh_uv, *(points for _c, points in contours)])
    for triangle in calculation.spec.fixture.context.calculation_mesh.triangle_indices:
        screen = [transform(mesh_uv[index]) for index in triangle]
        _polyline(draw, screen, SURFACE, 1, close=True)
    for contour, points in contours:
        screen = [transform(point) for point in points]
        _polyline(draw, screen, PATH, 4, close=contour.closed)
        for x, y in screen:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=CONTACT)
    deviations = max(
        (
            point.level_deviation_mm
            for level_pass in calculation.candidate.preview.passes
            for contour in level_pass.segments
            for point in contour.points
        ),
        default=0.0,
    )
    draw.text(
        (70, 145),
        (
            "High-curvature band x≈0 uses denser source cells; "
            f"max level deviation={deviations:.3e} mm"
        ),
        fill=TEXT,
        font=_font(16),
    )
    _footer(
        draw,
        calculation,
        (
            f"candidate cells="
            f"{len(calculation.spec.fixture.context.calculation_mesh.triangle_indices) * len(calculation.candidate.preview.schedule.levels)}  "
            f"points={calculation.candidate.preview.statistics.point_count}  "
            "review does not claim cusp-height adaptive stepdown"
        ),
    )
    return image


def _render_shared_edge(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Shared-edge multi-face · raw provenance → final contour",
        "Two selected faces, duplicate internal boundary removed by actual graph input",
    )
    draw.rectangle((165, 210, 590, 570), outline=LINK, width=3)
    draw.rectangle((590, 210, 1015, 570), outline=RAPID, width=3)
    draw.line((590, 190, 590, 590), fill=WARNING, width=6)
    draw.text((300, 230), "face: shared-left", fill=LINK, font=_font(19))
    draw.text((720, 230), "face: shared-right", fill=RAPID, font=_font(19))
    draw.text((608, 585), "shared edge", fill=WARNING, font=_font(16))
    contours = _preview_points(calculation, projection="uv")
    transform = _transform(
        (points for _contour, points in contours),
        (190, 270, 990, 540),
    )
    for _contour, points in contours:
        _polyline(
            draw,
            [transform(point) for point in points],
            PATH,
            7,
            close=True,
        )
    raw = calculation.topology["raw_segment_count"]
    final = calculation.topology["deduplicated_segment_count"]
    draw.text(
        (75, 145),
        (
            f"raw segments={raw} · shared candidates="
            f"{calculation.topology['shared_edge_candidate_count']} · "
            f"final segments={final}"
        ),
        fill=TEXT,
        font=_font(18),
    )
    _footer(
        draw,
        calculation,
        (
            f"provenance sources="
            f"{len(calculation.topology['provenance_source_counts'])}  "
            f"final contour hash="
            f"{calculation.topology['final_contour_hashes'][0][:20]}…"
        ),
    )
    return image


def _render_near_tangent(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Near-tangent policy · actual implicit-field samples",
        "Signs and roots use the same tool-center field as Z-Level geometry",
    )
    preview = calculation.candidate.preview
    frame = preview.frame
    mesh = calculation.spec.fixture.context.calculation_mesh
    level = preview.schedule.levels[0]
    sampled: dict[tuple[float, float, float], list[float]] = {}
    for triangle_index, triangle in enumerate(mesh.triangle_indices):
        normal = mesh.triangle_normals[triangle_index]
        for vertex_index in triangle:
            point = mesh.vertices[vertex_index]
            field = (
                frame.coordinates(point)[2]
                + calculation.inputs.tool_radius * normal.dot(frame.w_axis)
                - level
            )
            sampled.setdefault(
                (point.x, point.y, point.z), []
            ).append(field)
    values = [
        (
            frame.coordinates(mesh.vertices[index])[:2],
            sum(sampled[(point.x, point.y, point.z)])
            / len(sampled[(point.x, point.y, point.z)]),
        )
        for index, point in enumerate(mesh.vertices)
    ]
    transform = _transform([[point for point, _value in values]])
    for point, value in values:
        x, y = transform(point)
        color = WARNING if value > 0.01 else LINK if value < -0.01 else CONTACT
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=color)
        draw.text(
            (x + 12, y - 10),
            f"g={value:+.4f}",
            fill=color,
            font=_font(14),
        )
    for _contour, points in _preview_points(calculation, projection="uv"):
        _polyline(
            draw,
            [transform(point) for point in points],
            PATH,
            5,
        )
    draw.text(
        (70, 145),
        "red=positive · blue=negative · yellow=within tolerance · green=accepted root",
        fill=TEXT,
        font=_font(16),
    )
    _footer(
        draw,
        calculation,
        (
            f"rejected={preview.statistics.rejected_sample_count}  "
            f"ambiguous={preview.statistics.ambiguous_sample_count}  "
            f"contours={preview.statistics.contour_count}; no path is invented "
            "through an unresolved region"
        ),
    )
    return image


def _render_allowance(
    positive: CalculationEvidence,
    zero: CalculationEvidence,
) -> Image.Image:
    image, draw = _base(
        "Allowance semantics · nominal contact, offset tool center",
        "Actual allowance=0 and allowance=+0.5 mm calculation samples",
    )
    zero_point = zero.candidate.preview.passes[0].segments[0].points[0]
    positive_point = positive.candidate.preview.passes[0].segments[0].points[0]
    surface_y = 535
    draw.line((130, surface_y, 1070, surface_y), fill=SURFACE, width=6)
    scale = 42.0
    for x, point, allowance, color in (
        (400, zero_point, 0.0, LINK),
        (800, positive_point, 0.5, PATH),
    ):
        radius = positive.inputs.tool_radius * scale
        center_y = surface_y - (positive.inputs.tool_radius + allowance) * scale
        draw.ellipse(
            (x - radius, center_y - radius, x + radius, center_y + radius),
            outline=color,
            width=5,
        )
        draw.ellipse((x - 7, surface_y - 7, x + 7, surface_y + 7), fill=CONTACT)
        draw.ellipse((x - 7, center_y - 7, x + 7, center_y + 7), fill=color)
        _arrow(draw, (x, surface_y), (x, center_y), color, width=3)
        draw.text(
            (x - 130, 165),
            f"allowance={allowance:+.3f} mm",
            fill=color,
            font=_font(20, bold=True),
        )
        draw.text(
            (x - 130, 200),
            f"|center-contact|={positive.inputs.tool_radius + allowance:.3f} mm",
            fill=TEXT,
            font=_font(16),
        )
    measured = (
        positive_point.tool_center_point.z
        - zero_point.tool_center_point.z
    )
    _footer(
        draw,
        positive,
        (
            f"measured center delta={measured:+.6f} mm  "
            f"allowance deviation={positive_point.allowance_deviation_mm:.3e} mm  "
            "formula: c = p + (r + a)n, applied once"
        ),
    )
    return image


def _render_schedule(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Inclusive index-based level schedule",
        "Actual schedule from partial-final-step fixture",
    )
    schedule = calculation.candidate.preview.schedule
    top, bottom = schedule.top_level, schedule.bottom_level
    span = max(top - bottom, 1.0)
    x0, x1 = 220, 1030
    y = 365
    draw.line((x0, y, x1, y), fill=GRID, width=5)
    for index, level in enumerate(schedule.levels):
        x = x0 + (top - level) / span * (x1 - x0)
        draw.line((x, y - 55, x, y + 55), fill=PATH, width=4)
        draw.text(
            (x - 28, y + 72),
            f"{level:g}",
            fill=TEXT,
            font=_font(18, bold=True),
        )
        draw.text(
            (x - 20, y - 88),
            f"i={index}",
            fill=MUTED,
            font=_font(15),
        )
    draw.text(
        (140, 180),
        (
            f"top={top:g}  bottom={bottom:g}  "
            f"stepdown={schedule.stepdown_mm:g}  levels={list(schedule.levels)}"
        ),
        fill=TEXT,
        font=_font(22, bold=True),
    )
    _footer(
        draw,
        calculation,
        (
            f"schedule hash={canonical_hash(schedule.to_dict())[:24]}…  "
            "last residual=0 · duplicate count=0 · no cumulative z -= step"
        ),
    )
    return image


def _render_ordering(calculation: CalculationEvidence) -> Image.Image:
    image = _render_preview(
        "zlevel_contour_ordering",
        calculation,
        title="Deterministic contour ordering",
        subtitle="Actual level index / contour index / predecessor order",
        projection="uv",
        show_points=False,
    )
    draw = ImageDraw.Draw(image)
    contours = _preview_points(calculation, projection="uv")
    transform = _transform(points for _contour, points in contours)
    anchors = []
    for contour, points in contours:
        if not points:
            continue
        anchor = transform(points[0])
        anchors.append(anchor)
        draw.ellipse(
            (anchor[0] - 10, anchor[1] - 10, anchor[0] + 10, anchor[1] + 10),
            fill=CONTACT,
        )
        draw.text(
            (anchor[0] + 14, anchor[1] - 13),
            f"{contour.pass_index}.{contour.segment_index}",
            fill=TEXT,
            font=_font(18, bold=True),
        )
    for first, second in zip(anchors, anchors[1:]):
        _arrow(draw, first, second, LINK, width=3)
    return image


def _render_linking(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Conservative linking · actual Toolpath IR motions",
        "Retract → clearance rapid → approach; no unverified direct cut link",
    )
    artifact = calculation.candidate.artifact
    movements = [
        event
        for event in artifact.events
        if hasattr(event, "start") and hasattr(event, "end")
    ]
    series = [
        [
            (event.start.position.x, event.start.position.z),
            (event.end.position.x, event.end.position.z),
        ]
        for event in movements
    ]
    transform = _transform(series, (90, 165, 1110, 610))
    colors = {
        MotionClass.CUTTING: PATH,
        MotionClass.LINK: LINK,
        MotionClass.RETRACT: RAPID,
        MotionClass.NON_CUTTING: WARNING,
    }
    for event, points in zip(movements, series, strict=True):
        _arrow(
            draw,
            transform(points[0]),
            transform(points[1]),
            colors[event.motion_class],
            width=4,
        )
    counts = Counter(
        event.motion_class.value for event in movements
    )
    draw.text(
        (70, 145),
        "green=CUT · blue=APPROACH/LINK · purple=RETRACT · red=RAPID",
        fill=TEXT,
        font=_font(16),
    )
    _footer(
        draw,
        calculation,
        (
            f"motion classes={dict(sorted(counts.items()))}  "
            f"components={calculation.topology['connected_component_count']}  "
            "machine_ready_clearance_verified=false"
        ),
    )
    return image


def _render_contact(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Ball tool-center/contact evidence",
        "Actual nominal-surface point, differential normal and requested W level",
    )
    point = calculation.candidate.preview.passes[0].segments[0].points[0]
    radius = calculation.inputs.tool_radius
    allowance = calculation.spec.allowance_mm
    contact_xy = (580.0, 525.0)
    normal = point.surface_normal
    screen_normal = (-normal.x, -normal.z)
    magnitude = max(math.hypot(*screen_normal), 1.0e-9)
    screen_normal = (
        screen_normal[0] / magnitude,
        screen_normal[1] / magnitude,
    )
    scale = 44.0
    center_xy = (
        contact_xy[0] + screen_normal[0] * (radius + allowance) * scale,
        contact_xy[1] + screen_normal[1] * (radius + allowance) * scale,
    )
    draw.line((180, 525, 1020, 525), fill=SURFACE, width=7)
    draw.ellipse(
        (
            center_xy[0] - radius * scale,
            center_xy[1] - radius * scale,
            center_xy[0] + radius * scale,
            center_xy[1] + radius * scale,
        ),
        outline=PATH,
        width=6,
    )
    draw.ellipse(
        (
            contact_xy[0] - 8,
            contact_xy[1] - 8,
            contact_xy[0] + 8,
            contact_xy[1] + 8,
        ),
        fill=CONTACT,
    )
    draw.ellipse(
        (
            center_xy[0] - 8,
            center_xy[1] - 8,
            center_xy[0] + 8,
            center_xy[1] + 8,
        ),
        fill=PATH,
    )
    _arrow(draw, contact_xy, center_xy, LINK, width=4)
    draw.line(
        (160, center_xy[1], 1040, center_xy[1]),
        fill=RAPID,
        width=2,
    )
    draw.text((610, 535), "nominal contact p", fill=CONTACT, font=_font(18))
    draw.text(
        (center_xy[0] + 16, center_xy[1] - 20),
        "tool center c",
        fill=PATH,
        font=_font(18),
    )
    draw.text(
        (750, center_xy[1] + 8),
        f"requested W={point.requested_level:.6f} mm",
        fill=RAPID,
        font=_font(17),
    )
    draw.text(
        (70, 145),
        (
            f"radius={radius:.3f} mm · allowance={allowance:.3f} mm · "
            f"n=({normal.x:+.5f}, {normal.y:+.5f}, {normal.z:+.5f})"
        ),
        fill=TEXT,
        font=_font(18),
    )
    _footer(
        draw,
        calculation,
        (
            f"level deviation={point.level_deviation_mm:.3e} mm  "
            f"contact deviation={point.contact_deviation_mm:.3e} mm  "
            f"allowance deviation={point.allowance_deviation_mm:.3e} mm"
        ),
    )
    return image


def _render_ir(calculation: CalculationEvidence) -> Image.Image:
    image, draw = _base(
        "Toolpath IR · actual event stream",
        "Motion classes and counts from controller-neutral candidate artifact",
    )
    counts = calculation.source_record["motion_counts"]
    rows = (
        ("CUT", counts["cut"], PATH),
        ("DIRECT LINK", counts["direct_link"], LINK),
        ("RETRACT", counts["retract"], RAPID),
        ("RAPID", counts["rapid"], WARNING),
        ("APPROACH", counts["approach"], LINK),
    )
    y = 175
    maximum = max((count for _label, count, _color in rows), default=1)
    for label, count, color in rows:
        draw.text((110, y + 12), label, fill=color, font=_font(20, bold=True))
        width = 720 * count / max(maximum, 1)
        draw.rounded_rectangle(
            (310, y, 310 + max(width, 4), y + 50),
            radius=8,
            fill=color,
        )
        draw.text((1050, y + 12), str(count), fill=TEXT, font=_font(20))
        y += 82
    _footer(
        draw,
        calculation,
        (
            f"events={len(calculation.candidate.artifact.events)}  "
            f"artifact hash="
            f"{calculation.candidate.artifact.artifact_fingerprint.digest[:24]}…  "
            "production Post=false"
        ),
    )
    return image


def _render_images(bundle: EvidenceBundle) -> dict[str, Image.Image]:
    calculations = bundle.calculations
    return {
        "zlevel_vertical_wall": _render_preview(
            "zlevel_vertical_wall",
            calculations["vertical_wall"],
            title="Vertical wall · actual tool-center levels",
            subtitle="Open horizontal contours in V/W, W equals requested level",
            projection="vw",
        ),
        "zlevel_cylinder_closed_loops": _render_cylinder(
            calculations["cylinder"]
        ),
        "zlevel_conical_wall": _render_preview(
            "zlevel_conical_wall",
            calculations["cone"],
            title="Conical wall · actual changing loop radius",
            subtitle="Ball-center implicit field, U/V projection",
            projection="uv",
        ),
        "zlevel_freeform_steep": _render_freeform(
            calculations["freeform_steep"]
        ),
        "zlevel_trimmed_boundary": _render_preview(
            "zlevel_trimmed_boundary",
            calculations["trimmed_boundary"],
            title="Irregular trimmed outer boundary",
            subtitle="Actual non-rectangular selected-face contour after graph assembly",
            projection="uv",
        ),
        "zlevel_inner_hole": _render_preview(
            "zlevel_inner_hole",
            calculations["inner_hole"],
            title="Trimmed face with inner hole",
            subtitle="Actual outer/inner loop classification and opposite orientation",
            projection="uv",
        ),
        "zlevel_disconnected_regions": _render_preview(
            "zlevel_disconnected_regions",
            calculations["disconnected_regions"],
            title="Disconnected regions · separate contour IDs",
            subtitle="No horizontal cut path is drawn between the two components",
            projection="uv",
        ),
        "zlevel_shared_edge": _render_shared_edge(
            calculations["shared_edge"]
        ),
        "zlevel_near_tangent_policy": _render_near_tangent(
            calculations["near_tangent"]
        ),
        "zlevel_allowance": _render_allowance(
            calculations["allowance"], calculations["allowance_zero"]
        ),
        "zlevel_level_schedule": _render_schedule(
            calculations["partial_final_step"]
        ),
        "zlevel_contour_ordering": _render_ordering(
            calculations["contour_ordering"]
        ),
        "zlevel_conservative_linking": _render_linking(
            calculations["conservative_linking"]
        ),
        "zlevel_tool_center_contact": _render_contact(
            calculations["allowance"]
        ),
        "zlevel_toolpath_ir": _render_ir(
            calculations["conservative_linking"]
        ),
    }


def _image_manifest_entries(
    bundle: EvidenceBundle,
) -> list[dict[str, Any]]:
    entries = []
    for name, fixture_id in IMAGE_SOURCES.items():
        calculation = bundle.calculations[fixture_id]
        value = {
            "artifact": f"{name}.png",
            "sample_id": name,
            **calculation.manifest_fields,
            "generated_timestamp": bundle.generated_at,
            "render_data_selector": (
                f"calculation_records.json#records/{fixture_id}/preview"
            ),
        }
        value["entry_hash"] = canonical_hash(
            {
                key: item
                for key, item in value.items()
                if key != "generated_timestamp"
            }
        )
        entries.append(value)
    montage_base = bundle.calculations["vertical_wall"]
    montage = {
        "artifact": MONTAGE_NAME,
        "sample_id": "technical_montage",
        **montage_base.manifest_fields,
        "generated_timestamp": bundle.generated_at,
        "render_data_selector": [
            f"calculation_records.json#records/{fixture_id}/preview"
            for fixture_id in IMAGE_SOURCES.values()
        ],
    }
    montage["entry_hash"] = canonical_hash(
        {
            key: item
            for key, item in montage.items()
            if key != "generated_timestamp"
        }
    )
    entries.append(montage)
    return entries


def _review_index(
    image_files: list[str],
    report_files: list[str],
    *,
    total_file_count: int,
) -> str:
    return (
        "# CAM 3D Stage 8A.3.1 — Calculation-backed Review\n\n"
        "Gói review cục bộ, Git-ignored; không phải asset production.\n\n"
        f"- Strategy: `{STRATEGY}`, algorithm v{ALGORITHM_VERSION}, "
        f"payload v{PAYLOAD_VERSION}.\n"
        f"- Ảnh kỹ thuật: {len(image_files) - 1}; montage: 1.\n"
        f"- JSON reports: {len(report_files)}; tổng file: {total_file_count}.\n"
        "- Mọi ảnh dùng `calculation_records.json`; mapping và provenance nằm "
        "trong `evidence_manifest.json`.\n"
        "- Contact nằm trên nominal surface; tool center dùng "
        "`p + (radius + allowance) * normal` đúng một lần.\n"
        "- Safety dùng lại contract Stage 8A.2.2; machine-ready clearance và "
        "Production Post vẫn fail closed.\n\n"
        "## Images\n\n"
        + "\n".join(f"- `{name}`" for name in image_files)
        + "\n\n## Reports\n\n"
        + "\n".join(f"- `{name}`" for name in report_files)
        + "\n"
    )


def create(output: Path) -> Path:
    """Generate all evidence, reports and renderings; return summary path."""
    output.mkdir(parents=True, exist_ok=True)
    bundle = build_evidence_bundle()
    images = _render_images(bundle)
    for name in NAMES:
        images[name].save(
            output / f"{name}.png",
            format="PNG",
            optimize=False,
        )
    montage = Image.new("RGB", (1200, 800), BG)
    for index, name in enumerate(NAMES):
        thumbnail = images[name].resize((300, 200))
        montage.paste(thumbnail, ((index % 4) * 300, (index // 4) * 200))
    montage.save(output / MONTAGE_NAME, format="PNG", optimize=False)

    for filename, report in bundle.reports.items():
        _write_json(output / filename, report)

    manifest_entries = [
        *bundle.sample_manifest_entries,
        *_image_manifest_entries(bundle),
    ]
    evidence_manifest = {
        "format": "HMS_Z_LEVEL_EVIDENCE_MANIFEST",
        "format_version": 1,
        "stage": "8A.3.1",
        "strategy": STRATEGY,
        "algorithm_version": ALGORITHM_VERSION,
        "payload_version": PAYLOAD_VERSION,
        "generated_timestamp": bundle.generated_at,
        "entry_count": len(manifest_entries),
        "entries": sorted(
            manifest_entries,
            key=lambda item: (item["artifact"], item["sample_id"]),
        ),
    }
    evidence_manifest["manifest_hash"] = canonical_hash(
        {
            key: value
            for key, value in evidence_manifest.items()
            if key != "generated_timestamp"
        }
    )
    _write_json(output / "evidence_manifest.json", evidence_manifest)

    technical_images = [f"{name}.png" for name in NAMES]
    image_files = [*technical_images, MONTAGE_NAME]
    report_files = [
        "summary.json",
        "evidence_manifest.json",
        *sorted(bundle.reports),
    ]
    report_count = len(report_files)
    total_file_count = len(image_files) + report_count + 1
    summary = {
        "format": "HMS_CAM_3D_Z_LEVEL_REVIEW",
        "format_version": 1,
        "stage": "8A.3.1",
        "strategy_key": STRATEGY,
        "algorithm_version": ALGORITHM_VERSION,
        "strategy_payload_version": PAYLOAD_VERSION,
        "generated_timestamp": bundle.generated_at,
        "technical_image_count": len(technical_images),
        "montage_count": 1,
        "report_count": report_count,
        "index_count": 1,
        "total_file_count": total_file_count,
        "technical_images": technical_images,
        "montage": MONTAGE_NAME,
        "reports": report_files,
        "evidence_manifest_hash": evidence_manifest["manifest_hash"],
        "calculation_record_count": len(bundle.calculations),
        "determinism_case_count": (
            bundle.reports["determinism_report.json"]["case_count"]
        ),
        "determinism_run_count": (
            bundle.reports["determinism_report.json"]["run_count"]
        ),
        "machine_ready_clearance_verified": False,
        "production_post": False,
        "unsupported_cases_fail_closed": True,
    }
    _write_json(output / "summary.json", summary)
    (output / "REVIEW_INDEX.md").write_text(
        _review_index(
            image_files,
            report_files,
            total_file_count=total_file_count,
        ),
        encoding="utf-8",
    )
    return output / "summary.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reference_private/DERIVED/"
            "CAM_3D_8A3_1_Z_LEVEL_FOUNDATION"
        ),
    )
    args = parser.parse_args()
    summary_path = create(args.output)
    print(summary_path)


if __name__ == "__main__":
    main()
