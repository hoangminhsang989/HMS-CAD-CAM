"""Runtime Qt geometry evidence for the Stage 9A.7 WP2 unified panel."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QCoreApplication, QRect
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget

if TYPE_CHECKING:
    from hms_cadcam.ui.post_assembly_panel import UnifiedPostAssemblyPanel

GEOMETRY_SCHEMA_VERSION = "stage9a7.wp2.geometry.v2"


def rect_values(rect: QRect) -> list[int]:
    """Return a QRect as JSON-safe x/y/width/height values."""
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def size_values(widget: QWidget, source: str = "size") -> list[int]:
    """Return one runtime QWidget size method as JSON-safe values."""
    value = getattr(widget, source)()
    return [value.width(), value.height()]


def mapped_rect(widget: QWidget, ancestor: QWidget) -> QRect:
    """Map widget bounds into an ancestor's client coordinate system."""
    top_left = widget.mapTo(ancestor, widget.rect().topLeft())
    bottom_right = widget.mapTo(ancestor, widget.rect().bottomRight())
    return QRect(top_left, bottom_right).normalized()


def rect_inside(container: QRect, child: QRect, tolerance: int = 0) -> bool:
    """Return whether a positive-area child is wholly inside container."""
    if child.width() <= 0 or child.height() <= 0:
        return False
    return (
        child.left() >= container.left() - tolerance
        and child.top() >= container.top() - tolerance
        and child.right() <= container.right() + tolerance
        and child.bottom() <= container.bottom() + tolerance
    )


def minimum_exceeds_available(
    effective_minimum: Sequence[int], available_geometry: QRect
) -> tuple[bool, bool]:
    """Return width/height blocker flags for one screen-minimum fixture."""
    values = tuple(int(value) for value in effective_minimum)
    if len(values) != 2:
        raise ValueError("effective_minimum must contain width and height")
    return (
        values[0] > available_geometry.width(),
        values[1] > available_geometry.height(),
    )


def visible_sibling_overlaps(
    widgets: Sequence[QWidget],
    ancestor: QWidget,
    *,
    allowed_pairs: Iterable[frozenset[str]] = (),
) -> list[dict[str, Any]]:
    """Check only a semantically selected set of visible widgets."""
    allowed = set(allowed_pairs)
    overlaps: list[dict[str, Any]] = []
    visible = [widget for widget in widgets if widget.isVisible()]
    for index, first in enumerate(visible):
        first_name = first.objectName()
        first_rect = mapped_rect(first, ancestor)
        for second in visible[index + 1 :]:
            second_name = second.objectName()
            if frozenset((first_name, second_name)) in allowed:
                continue
            intersection = first_rect.intersected(mapped_rect(second, ancestor))
            if intersection.width() <= 0 or intersection.height() <= 0:
                continue
            overlaps.append(
                {
                    "first": first_name,
                    "second": second_name,
                    "intersection": rect_values(intersection),
                    "allowed": False,
                }
            )
    return overlaps


def activate_widget_layouts(widget: QWidget) -> None:
    """Polish and activate the existing production widget tree in place."""
    for item in (widget, *widget.findChildren(QWidget)):
        item.ensurePolished()
        layout = item.layout()
        if layout is not None:
            layout.activate()


def _visible_geometry_signature(window: QMainWindow) -> tuple[tuple[int, ...], ...]:
    """Return deterministic visible-widget geometry for stability detection."""

    widgets = (window, *window.findChildren(QWidget))
    return tuple(
        (
            id(widget),
            widget.geometry().x(),
            widget.geometry().y(),
            widget.geometry().width(),
            widget.geometry().height(),
        )
        for widget in widgets
        if widget.isVisible()
    )


def settle_geometry(
    app: QApplication,
    window: QMainWindow,
    *,
    maximum_iterations: int = 12,
    stable_iterations: int = 3,
) -> bool:
    """Settle native Qt geometry and report bounded event-loop stability."""

    if maximum_iterations <= 0 or stable_iterations <= 0:
        raise ValueError("geometry settle iteration counts must be positive")
    window.show()
    window.raise_()
    window.activateWindow()
    previous: tuple[tuple[int, ...], ...] | None = None
    stable_count = 0
    for _iteration in range(maximum_iterations):
        QCoreApplication.sendPostedEvents(None, 0)
        activate_widget_layouts(window)
        app.processEvents()
        current = _visible_geometry_signature(window)
        if current == previous:
            stable_count += 1
            if stable_count >= stable_iterations:
                return True
        else:
            stable_count = 0
            previous = current
    return False


def _visible_axis_range(
    count: int,
    extent: int,
    resolver: Callable[[int], int],
) -> list[int] | None:
    if count <= 0 or extent <= 0:
        return None
    visible = {
        int(resolver(position))
        for position in range(extent)
        if int(resolver(position)) >= 0
    }
    return [min(visible), max(visible)] if visible else None


def capture_post_assembly_geometry(
    window: QMainWindow,
    panel: UnifiedPostAssemblyPanel,
    *,
    capture_id: str = "stage9a7_wp2_layout",
    requested_window_size: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Measure production MainWindow/panel geometry and derive acceptance."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication is required for geometry evidence")
    layout_stable = settle_geometry(app, window)

    dock = window.post_assembly_dock
    dock_content = dock.widget()
    table = panel.operation_table
    viewport = table.viewport()
    header = table.horizontalHeader()
    action_widgets = (
        panel.add_button,
        panel.remove_button,
        panel.move_up_button,
        panel.move_down_button,
        panel.clear_button,
        panel.generate_button,
        panel.save_managed_button,
        panel.export_external_button,
    )
    footer_widgets = (
        panel.generate_button,
        panel.save_managed_button,
        panel.export_external_button,
    )
    semantic_controls = (
        panel.source_operation_label,
        panel.source_operation_picker,
        panel.operation_table_group,
        panel.add_button,
        panel.remove_button,
        panel.move_up_button,
        panel.move_down_button,
        panel.clear_button,
        panel.artifact_summary,
        panel.preview_placeholder,
        panel.diagnostics_placeholder,
        *footer_widgets,
    )

    window_client = window.rect()
    dock_window_rect = mapped_rect(dock, window)
    dock_content_window_rect = (
        mapped_rect(dock_content, window) if dock_content is not None else QRect()
    )
    panel_window_rect = mapped_rect(panel, window)
    panel_client = panel.contentsRect()
    table_panel_rect = mapped_rect(table, panel)
    viewport_panel_rect = mapped_rect(viewport, panel)
    header_panel_rect = mapped_rect(header, panel)
    screen = window.screen() or QApplication.primaryScreen()
    screen_rect = screen.availableGeometry() if screen is not None else QRect()
    actual_window_size = size_values(window)
    requested_size = (
        [int(value) for value in requested_window_size]
        if requested_window_size is not None
        else list(actual_window_size)
    )
    if len(requested_size) != 2:
        raise ValueError("requested_window_size must contain width and height")
    effective_minimum_size = size_values(window, "minimumSize")
    exceeds_available_width, exceeds_available_height = minimum_exceeds_available(
        effective_minimum_size, screen_rect
    )

    bounds_checks: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    def add_check(
        rule_id: str,
        widget_name: str,
        passed: bool,
        actual: Any,
        expected: Any,
        message: str,
        *,
        severity: str = "BLOCKER",
    ) -> None:
        check = {
            "rule_id": rule_id,
            "widget": widget_name,
            "pass": bool(passed),
            "actual": actual,
            "expected": expected,
            "message": message,
        }
        bounds_checks.append(check)
        if not passed:
            violations.append(
                {
                    "rule_id": rule_id,
                    "widget": widget_name,
                    "actual_geometry": actual,
                    "expected_bounds": expected,
                    "severity": severity,
                    "message": message,
                }
            )

    add_check(
        "LAYOUT_STABLE",
        window.objectName(),
        layout_stable,
        layout_stable,
        True,
        "Geometry must remain unchanged across bounded event-loop iterations.",
    )
    add_check(
        "WINDOW_NONZERO",
        window.objectName(),
        window.width() > 0 and window.height() > 0,
        size_values(window),
        [">0", ">0"],
        "Main window must have positive runtime dimensions.",
    )
    add_check(
        "WINDOW_CLIENT_NONZERO",
        window.objectName(),
        window_client.width() > 0 and window_client.height() > 0,
        rect_values(window_client),
        [0, 0, ">0", ">0"],
        "Main window client rectangle must be measurable.",
    )
    window_minimum = window.minimumSize()
    add_check(
        "WINDOW_MINIMUM_MEANINGFUL",
        window.objectName(),
        window_minimum.width() > 0 and window_minimum.height() > 0,
        [window_minimum.width(), window_minimum.height()],
        [">0", ">0"],
        "Production MainWindow must expose a meaningful minimum size.",
    )
    add_check(
        "WINDOW_MINIMUM_EXCEEDS_AVAILABLE_SCREEN",
        window.objectName(),
        screen is not None
        and not exceeds_available_width
        and not exceeds_available_height,
        {
            "effective_minimum_size": effective_minimum_size,
            "exceeds_available_width": exceeds_available_width,
            "exceeds_available_height": exceeds_available_height,
        },
        {"available_screen_geometry": rect_values(screen_rect)},
        "The effective client minimum must not exceed the available screen.",
    )
    add_check(
        "WINDOW_ON_AVAILABLE_SCREEN",
        window.objectName(),
        screen is not None
        and rect_inside(screen_rect, window.frameGeometry()),
        rect_values(window.frameGeometry()),
        rect_values(screen_rect),
        "The production window frame must remain inside the available screen.",
    )
    add_check(
        "DOCK_VISIBLE_NONZERO",
        dock.objectName(),
        dock.isVisible() and dock.width() > 0 and dock.height() > 0,
        {"visible": dock.isVisible(), "geometry": rect_values(dock_window_rect)},
        rect_values(window_client),
        "Unified-state dock must be visible and measurable.",
    )
    add_check(
        "DOCK_IN_WINDOW_CLIENT",
        dock.objectName(),
        rect_inside(window_client, dock_window_rect),
        rect_values(dock_window_rect),
        rect_values(window_client),
        "Dock geometry must remain inside MainWindow client bounds.",
    )
    add_check(
        "PANEL_VISIBLE_NONZERO",
        panel.objectName(),
        panel.isVisible() and panel.width() > 0 and panel.height() > 0,
        {"visible": panel.isVisible(), "geometry": rect_values(panel_window_rect)},
        rect_values(dock_content_window_rect),
        "Unified panel must be visible and measurable.",
    )
    add_check(
        "PANEL_IN_DOCK_CONTENT",
        panel.objectName(),
        dock_content is panel
        and rect_inside(dock_content_window_rect, panel_window_rect),
        rect_values(panel_window_rect),
        rect_values(dock_content_window_rect),
        "Panel must stay inside its dock content bounds.",
    )
    add_check(
        "PANEL_IN_WINDOW_CLIENT",
        panel.objectName(),
        rect_inside(window_client, panel_window_rect),
        rect_values(panel_window_rect),
        rect_values(window_client),
        "Panel must remain inside MainWindow client bounds.",
    )
    panel_minimum_hint = panel.minimumSizeHint()
    add_check(
        "PANEL_LAYOUT_MINIMUM_MEANINGFUL",
        panel.objectName(),
        panel_minimum_hint.width() > 0 and panel_minimum_hint.height() > 0,
        [panel_minimum_hint.width(), panel_minimum_hint.height()],
        [">0", ">0"],
        "A zero explicit minimum is acceptable only with meaningful Qt layout hints.",
    )
    add_check(
        "TABLE_VIEWPORT_NONZERO",
        viewport.objectName() or "qt_scrollarea_viewport",
        viewport.isVisible() and viewport.width() > 0 and viewport.height() > 0,
        {"visible": viewport.isVisible(), "geometry": rect_values(viewport_panel_rect)},
        rect_values(table_panel_rect),
        "Table viewport must be visible and measurable.",
    )
    add_check(
        "TABLE_HEADER_VISIBLE",
        header.objectName() or "qt_horizontal_header",
        header.isVisible() and header.height() > 0,
        {"visible": header.isVisible(), "geometry": rect_values(header_panel_rect)},
        rect_values(table_panel_rect),
        "Horizontal header must be visible with positive height.",
    )
    model = table.model()
    row_count = model.rowCount() if model is not None else 0
    column_count = model.columnCount() if model is not None else 0
    visible_row_range = _visible_axis_range(row_count, viewport.height(), table.rowAt)
    visible_column_range = _visible_axis_range(
        column_count, viewport.width(), table.columnAt
    )
    add_check(
        "TABLE_SIX_COLUMNS",
        table.objectName(),
        column_count == 6,
        column_count,
        6,
        "Unified operation table must expose exactly six columns.",
    )
    add_check(
        "TABLE_VIEWPORT_IN_TABLE",
        viewport.objectName() or "qt_scrollarea_viewport",
        rect_inside(table_panel_rect, viewport_panel_rect),
        rect_values(viewport_panel_rect),
        rect_values(table_panel_rect),
        "Viewport must remain inside table bounds.",
    )
    add_check(
        "TABLE_HEADER_IN_TABLE",
        header.objectName() or "qt_horizontal_header",
        rect_inside(table_panel_rect, header_panel_rect),
        rect_values(header_panel_rect),
        rect_values(table_panel_rect),
        "Header must remain inside table bounds.",
    )
    fully_visible_rows = [
        row
        for row in range(row_count)
        if table.rowViewportPosition(row) >= 0
        and table.rowViewportPosition(row) + table.rowHeight(row)
        <= viewport.height()
    ]
    fully_visible_row_range = (
        [min(fully_visible_rows), max(fully_visible_rows)]
        if fully_visible_rows
        else None
    )
    if row_count:
        add_check(
            "TABLE_DATA_ROW_VISIBLE",
            table.objectName(),
            fully_visible_row_range is not None,
            {
                "visible_row_range": visible_row_range,
                "fully_visible_row_range": fully_visible_row_range,
                "viewport_height": viewport.height(),
            },
            [0, row_count - 1],
            "A populated production state must expose at least one complete row.",
        )

    actions: list[dict[str, Any]] = []
    clipped: list[str] = []
    for button in action_widgets:
        rect = mapped_rect(button, panel)
        size_hint = button.sizeHint()
        minimum_hint = button.minimumSizeHint()
        metrics = button.fontMetrics()
        text_extent = [metrics.horizontalAdvance(button.text()), metrics.height()]
        bounds_ok = rect_inside(panel_client, rect)
        visible_nonzero = (
            button.isVisible() and button.width() > 0 and button.height() > 0
        )
        visible_region = button.visibleRegion().boundingRect()
        fully_visible = (
            visible_region.width() >= button.width() - 1
            and visible_region.height() >= button.height() - 1
        )
        readable = (
            button.width() >= minimum_hint.width()
            and button.height() >= minimum_hint.height()
            and button.contentsRect().width() >= text_extent[0]
            and button.contentsRect().height() >= text_extent[1]
        )
        clipped_flag = not (bounds_ok and visible_nonzero and fully_visible and readable)
        if clipped_flag:
            clipped.append(button.objectName())
        actions.append(
            {
                "object_name": button.objectName(),
                "text": button.text(),
                "visible": button.isVisible(),
                "enabled": button.isEnabled(),
                "geometry_in_panel": rect_values(rect),
                "visible_region": rect_values(visible_region),
                "size_hint": [size_hint.width(), size_hint.height()],
                "minimum_size_hint": [minimum_hint.width(), minimum_hint.height()],
                "font_metrics_text_extent": text_extent,
                "clipped": clipped_flag,
            }
        )
        add_check(
            "ACTION_VISIBLE_NONZERO",
            button.objectName(),
            visible_nonzero,
            {"visible": button.isVisible(), "geometry": rect_values(rect)},
            rect_values(panel_client),
            "Required production action must be visible and measurable.",
        )
        add_check(
            "ACTION_IN_PANEL_BOUNDS",
            button.objectName(),
            bounds_ok and fully_visible,
            {"geometry": rect_values(rect), "visible_region": rect_values(visible_region)},
            rect_values(panel_client),
            "Required action must remain fully visible inside panel client bounds.",
        )
        add_check(
            "ACTION_TEXT_READABLE",
            button.objectName(),
            readable,
            {
                "actual_size": [button.width(), button.height()],
                "minimum_size_hint": [minimum_hint.width(), minimum_hint.height()],
                "font_metrics_text_extent": text_extent,
            },
            [size_hint.width(), size_hint.height()],
            "Button text must fit its runtime minimum hint and font metrics.",
        )

    footer_names = {widget.objectName() for widget in footer_widgets}
    footer_accessible = all(
        item["visible"] and not item["clipped"]
        for item in actions
        if item["object_name"] in footer_names
    )
    scroll_area_present = any(
        child.metaObject().className() == "QScrollArea"
        for child in panel.findChildren(QWidget)
    )

    overlaps = visible_sibling_overlaps(semantic_controls, panel)
    table_footer_overlaps = visible_sibling_overlaps(
        (panel.operation_table_group, *footer_widgets), panel
    )
    for overlap in overlaps:
        violations.append(
            {
                "rule_id": "SEMANTIC_SIBLING_OVERLAP",
                "widget": f"{overlap['first']}|{overlap['second']}",
                "actual_geometry": overlap["intersection"],
                "expected_bounds": "no positive-area intersection",
                "severity": "BLOCKER",
                "message": "Semantically separate visible controls must not overlap.",
            }
        )
    for overlap in table_footer_overlaps:
        violations.append(
            {
                "rule_id": "TABLE_FOOTER_OVERLAP",
                "widget": f"{overlap['first']}|{overlap['second']}",
                "actual_geometry": overlap["intersection"],
                "expected_bounds": "no positive-area intersection",
                "severity": "BLOCKER",
                "message": "Operation table must not cover the footer action area.",
            }
        )

    evidence: dict[str, Any] = {
        "schema_version": GEOMETRY_SCHEMA_VERSION,
        "capture_id": str(capture_id),
        "coordinate_systems": {
            "geometry_in_window": "MainWindow client coordinates",
            "geometry_in_panel": "UnifiedPostAssemblyPanel client coordinates",
            "window_frame_geometry": "virtual desktop coordinates",
        },
        "available_screen_geometry": rect_values(screen_rect),
        "requested_window_size": requested_size,
        "actual_window_size": actual_window_size,
        "effective_minimum_size": effective_minimum_size,
        "exceeds_available_width": exceeds_available_width,
        "exceeds_available_height": exceeds_available_height,
        "scroll_area_present": scroll_area_present,
        "footer_accessible": footer_accessible,
        "layout_stable": layout_stable,
        "window": {
            "object_name": window.objectName(),
            "geometry": rect_values(window.geometry()),
            "frame_geometry": rect_values(window.frameGeometry()),
            "client_rect": rect_values(window_client),
            "size": actual_window_size,
            "requested_size": requested_size,
            "effective_minimum_size": effective_minimum_size,
            "minimum_size": effective_minimum_size,
            "minimum_size_hint": size_values(window, "minimumSizeHint"),
            "device_pixel_ratio": window.devicePixelRatioF(),
            "available_screen_geometry": rect_values(screen_rect),
        },
        "dock": {
            "object_name": dock.objectName(),
            "visible": dock.isVisible(),
            "geometry_in_window": rect_values(dock_window_rect),
            "content_geometry": rect_values(dock_content_window_rect),
            "minimum_size": size_values(dock, "minimumSize"),
            "minimum_size_hint": size_values(dock, "minimumSizeHint"),
        },
        "panel": {
            "object_name": panel.objectName(),
            "visible": panel.isVisible(),
            "geometry_in_window": rect_values(panel_window_rect),
            "rect": rect_values(panel.rect()),
            "minimum_size": size_values(panel, "minimumSize"),
            "minimum_size_hint": size_values(panel, "minimumSizeHint"),
            "size_hint": size_values(panel, "sizeHint"),
        },
        "table": {
            "object_name": table.objectName(),
            "geometry_in_panel": rect_values(table_panel_rect),
            "viewport_geometry": rect_values(viewport_panel_rect),
            "header_geometry": rect_values(header_panel_rect),
            "header_visible": header.isVisible(),
            "row_count": row_count,
            "column_count": column_count,
            "visible_row_range": visible_row_range,
            "fully_visible_row_range": fully_visible_row_range,
            "visible_column_range": visible_column_range,
            "scrollbar_state": {
                "horizontal_visible": table.horizontalScrollBar().isVisible(),
                "vertical_visible": table.verticalScrollBar().isVisible(),
            },
        },
        "footer_actions": actions,
        "bounds_checks": bounds_checks,
        "overlap_checks": {
            "semantic_pairs_checked": len(semantic_controls)
            * (len(semantic_controls) - 1)
            // 2,
            "allowed_pairs": [],
            "intersections": overlaps,
            "table_footer_intersections": table_footer_overlaps,
        },
        "violations": violations,
        "result": (
            "PASS"
            if not any(item["severity"] == "BLOCKER" for item in violations)
            else "FAIL"
        ),
        "qt_platform": QApplication.platformName(),
    }

    evidence.update(
        {
            "main_window_bounds": rect_values(window.rect()),
            "parent_client_bounds": rect_values(window_client),
            "dock_content_bounds": rect_values(dock_content_window_rect),
            "panel_bounds": rect_values(panel_window_rect),
            "widget_geometry": {
                widget.objectName(): rect_values(mapped_rect(widget, window))
                for widget in semantic_controls
            },
            "visible_regions": {
                widget.objectName(): rect_values(widget.visibleRegion().boundingRect())
                for widget in semantic_controls
            },
            "footer_button_bounds": {
                widget.objectName(): rect_values(mapped_rect(widget, window))
                for widget in footer_widgets
            },
            "table_viewport_bounds": rect_values(mapped_rect(viewport, window)),
            "clipped_widgets": clipped,
            "overlap_intersections": overlaps,
            "clipped_widget_count": len(clipped),
            "overlap_count": len(overlaps),
            "minimum_readable_dimensions": {
                "panel_minimum": size_values(panel, "minimumSize"),
                "panel_minimum_size_hint": size_values(panel, "minimumSizeHint"),
                "panel_actual": size_values(panel),
                "table_actual": size_values(table),
                "source_picker_actual": size_values(panel.source_operation_picker),
                "pass": not any(
                    item["severity"] == "BLOCKER" for item in violations
                ),
            },
            "scrollbar_state": evidence["table"]["scrollbar_state"],
            "native_windows_qpa_evidence": {
                "platform_name": QApplication.platformName(),
                "is_native_windows": QApplication.platformName().lower() == "windows",
            },
        }
    )
    return evidence
