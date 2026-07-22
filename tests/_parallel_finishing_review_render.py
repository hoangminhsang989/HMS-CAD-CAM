"""Headless Qt raster renderer for Parallel Finishing geometry review assets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from hms_cadcam.cam.toolpath import ArcMove, LinearMove, MotionClass, RapidMove

_WIDTH = 1200
_HEIGHT = 800
_COLORS = {
    "background": QColor("#0f172a"),
    "panel": QColor("#111827"),
    "geometry": QColor("#64748b"),
    "contact": QColor("#facc15"),
    "center": QColor("#22d3ee"),
    "normal": QColor("#f472b6"),
    "rapid": QColor("#ef4444"),
    "link": QColor("#fb923c"),
    "retract": QColor("#a78bfa"),
    "direction": QColor("#4ade80"),
    "text": QColor("#e5e7eb"),
    "axis_x": QColor("#f87171"),
    "axis_y": QColor("#4ade80"),
    "axis_z": QColor("#60a5fa"),
}


def render_geometry_review(
    path: Path,
    fixture,
    candidate,
    *,
    title: str,
    show_normals: bool = False,
    show_linking: bool = False,
    show_direction: bool = False,
) -> None:
    """Render mesh, contact, tool center, normals and optional linking evidence."""
    mesh_points = [_project(point) for point in fixture.mesh.vertices]
    path_points = [
        _project(point.contact_point)
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for point in segment.points
    ] + [
        _project(point.tool_center_point)
        for pass_value in candidate.preview.passes
        for segment in pass_value.segments
        for point in segment.points
    ]
    movement_points = []
    if show_linking:
        movement_points = [
            projected
            for event in candidate.artifact.events
            if isinstance(event, (RapidMove, LinearMove, ArcMove))
            for projected in (_project(event.start.position), _project(event.end.position))
        ]
    transform = _Transform(mesh_points + path_points + movement_points)
    image, painter = _canvas(title)
    try:
        _draw_mesh(painter, transform, fixture)
        _draw_paths(painter, transform, candidate, "contact_point", "contact", 2.5)
        _draw_paths(painter, transform, candidate, "tool_center_point", "center", 3.0)
        if show_normals:
            _draw_normals(painter, transform, candidate)
        if show_linking:
            _draw_motions(painter, transform, candidate)
        if show_direction:
            _draw_directions(painter, transform, candidate)
        _draw_axes(painter)
        _draw_legend(painter, show_normals=show_normals, show_linking=show_linking)
    finally:
        painter.end()
    _save(image, path)


def render_motion_review(path: Path, fixture, candidate, *, title: str) -> None:
    """Render final IR movements by motion class with geometry context."""
    render_geometry_review(
        path,
        fixture,
        candidate,
        title=title,
        show_linking=True,
        show_direction=False,
    )


class _Transform:
    def __init__(self, values: list[tuple[float, float]]) -> None:
        if not values:
            values = [(0.0, 0.0), (1.0, 1.0)]
        x_min = min(item[0] for item in values)
        x_max = max(item[0] for item in values)
        y_min = min(item[1] for item in values)
        y_max = max(item[1] for item in values)
        width = max(x_max - x_min, 1.0)
        height = max(y_max - y_min, 1.0)
        self._scale = min(980.0 / width, 610.0 / height)
        self._x_min = x_min
        self._y_min = y_min
        self._draw_height = height * self._scale

    def point(self, value: tuple[float, float]) -> QPointF:
        return QPointF(
            90.0 + (value[0] - self._x_min) * self._scale,
            710.0 - (value[1] - self._y_min) * self._scale,
        )


def _project(point) -> tuple[float, float]:
    return point.x - 0.38 * point.y, point.z + 0.22 * point.y


def _canvas(title: str) -> tuple[QImage, QPainter]:
    image = QImage(_WIDTH, _HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(_COLORS["background"])
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(QRectF(35, 60, 1130, 680), _COLORS["panel"])
    painter.setPen(_COLORS["text"])
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
    painter.drawText(QRectF(40, 12, 1120, 42), Qt.AlignmentFlag.AlignCenter, title)
    return image, painter


def _draw_mesh(painter: QPainter, transform: _Transform, fixture) -> None:
    painter.setPen(QPen(_COLORS["geometry"], 1.0))
    for triangle in fixture.mesh.triangle_indices:
        polygon = QPolygonF(
            [transform.point(_project(fixture.mesh.vertices[index])) for index in triangle]
        )
        polygon.append(polygon[0])
        painter.drawPolyline(polygon)


def _draw_paths(
    painter: QPainter,
    transform: _Transform,
    candidate,
    attribute: str,
    color: str,
    width: float,
) -> None:
    painter.setPen(QPen(_COLORS[color], width))
    for pass_value in candidate.preview.passes:
        for segment in pass_value.segments:
            polygon = QPolygonF(
                [
                    transform.point(_project(getattr(point, attribute)))
                    for point in segment.points
                ]
            )
            painter.drawPolyline(polygon)


def _draw_normals(painter: QPainter, transform: _Transform, candidate) -> None:
    painter.setPen(QPen(_COLORS["normal"], 1.8))
    for pass_value in candidate.preview.passes:
        for segment in pass_value.segments:
            stride = max(1, len(segment.points) // 5)
            for point in segment.points[::stride]:
                start = point.contact_point
                end = type(start)(
                    start.x + point.surface_normal.x * 1.5,
                    start.y + point.surface_normal.y * 1.5,
                    start.z + point.surface_normal.z * 1.5,
                    start.unit,
                )
                _arrow(
                    painter,
                    transform.point(_project(start)),
                    transform.point(_project(end)),
                    _COLORS["normal"],
                )


def _draw_directions(painter: QPainter, transform: _Transform, candidate) -> None:
    for pass_value in candidate.preview.passes:
        for segment in pass_value.segments:
            first = transform.point(_project(segment.points[0].tool_center_point))
            last = transform.point(_project(segment.points[-1].tool_center_point))
            _arrow(painter, first, last, _COLORS["direction"])


def _draw_motions(painter: QPainter, transform: _Transform, candidate) -> None:
    colors = {
        MotionClass.CUTTING: _COLORS["center"],
        MotionClass.NON_CUTTING: _COLORS["rapid"],
        MotionClass.LINK: _COLORS["link"],
        MotionClass.RETRACT: _COLORS["retract"],
    }
    for event in candidate.artifact.events:
        if not isinstance(event, (RapidMove, LinearMove, ArcMove)):
            continue
        pen = QPen(colors[event.motion_class], 2.2)
        if isinstance(event, RapidMove):
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(
            transform.point(_project(event.start.position)),
            transform.point(_project(event.end.position)),
        )


def _draw_axes(painter: QPainter) -> None:
    origin = QPointF(90.0, 700.0)
    for label, delta, color in (
        ("X / U", QPointF(55.0, 0.0), _COLORS["axis_x"]),
        ("Y / V", QPointF(-24.0, -22.0), _COLORS["axis_y"]),
        ("Z / W", QPointF(0.0, -55.0), _COLORS["axis_z"]),
    ):
        end = origin + delta
        _arrow(painter, origin, end, color)
        painter.setPen(color)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(end + QPointF(4.0, -3.0), label)


def _draw_legend(
    painter: QPainter,
    *,
    show_normals: bool,
    show_linking: bool,
) -> None:
    entries = [
        ("geometry", "Geometry mesh"),
        ("contact", "Contact path"),
        ("center", "Ball-center cutting path"),
        ("direction", "Pass direction"),
    ]
    if show_normals:
        entries.append(("normal", "BRep/contact normal"))
    if show_linking:
        entries.extend(
            (
                ("rapid", "Rapid"),
                ("link", "Approach / link"),
                ("retract", "Retract / clearance"),
            )
        )
    x_value = 725.0
    y_value = 82.0
    painter.setFont(QFont("Segoe UI", 9))
    for key, label in entries:
        painter.setPen(QPen(_COLORS[key], 3.0))
        painter.drawLine(QPointF(x_value, y_value), QPointF(x_value + 28, y_value))
        painter.setPen(_COLORS["text"])
        painter.drawText(QPointF(x_value + 36, y_value + 4), label)
        y_value += 19.0


def _arrow(
    painter: QPainter,
    start: QPointF,
    end: QPointF,
    color: QColor,
) -> None:
    painter.setPen(QPen(color, 2.0))
    painter.drawLine(start, end)
    delta = end - start
    length = max((delta.x() ** 2 + delta.y() ** 2) ** 0.5, 1.0)
    ux, uy = delta.x() / length, delta.y() / length
    left = QPointF(end.x() - ux * 10 - uy * 5, end.y() - uy * 10 + ux * 5)
    right = QPointF(end.x() - ux * 10 + uy * 5, end.y() - uy * 10 - ux * 5)
    painter.setBrush(color)
    painter.drawPolygon(QPolygonF([end, left, right]))


def _save(image: QImage, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "PNG"):
        raise OSError(f"Could not save Parallel review image: {path}")
