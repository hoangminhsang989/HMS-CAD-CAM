"""Build and validate the HMS isometric CAD/CAM icon foundation pack.

SVG remains the source of truth.  PySide6's SVG renderer creates deterministic
transparent PNGs for the requested target sizes; review contact sheets are
written below the Git-ignored ``reference_private`` directory only.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final, Iterable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402


logger = logging.getLogger("hms.isometric_cadcam_icons")

ICON_NAMES: Final[tuple[str, ...]] = (
    "threaded-shaft",
    "slot-cut",
    "mirror-body",
    "offset-surface",
    "scale-up",
    "join-bodies",
)
DEFAULT_SIZES: Final[tuple[int, ...]] = (24, 32, 48, 64)
REVIEW_SIZES: Final[tuple[int, ...]] = (24, 32, 64)
SVG_VIEWBOX: Final[tuple[float, float, float, float]] = (0.0, 0.0, 64.0, 64.0)
SVG_GEOMETRY_TAGS: Final[frozenset[str]] = frozenset(
    {
        "circle",
        "ellipse",
        "line",
        "path",
        "polygon",
        "polyline",
        "rect",
    }
)
SVG_ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {"svg", "defs", "g", "linearGradient", "stop", *SVG_GEOMETRY_TAGS}
)
SVG_BANNED_TAGS: Final[frozenset[str]] = frozenset(
    {"image", "text", "script", "style", "foreignObject", "use", "filter"}
)
SVG_PALETTE: Final[frozenset[str]] = frozenset(
    {
        "#1E4A5D",
        "#2E6277",
        "#356F85",
        "#438CA4",
        "#4F7588",
        "#5199B0",
        "#596771",
        "#6FB2C4",
        "#82C0D0",
        "#8D9AA3",
        "#9B5B16",
        "#AEB8BF",
        "#B8DCE5",
        "#D3DADE",
        "#D9F1F5",
        "#DDEBF0",
        "#E89924",
        "#F2F5F7",
    }
)
_NUMERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(?:NaN|Infinity|-Infinity)(?![A-Za-z])|"
    r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![A-Za-z])"
)
_EXTERNAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:https?://|data:|base64|javascript:|url\s*\(\s*['\"]?(?!#))"
)


def project_root() -> Path:
    """Return the repository root independently of the current directory."""

    return Path(__file__).resolve().parents[1]


def svg_directory(root: Path | None = None) -> Path:
    """Return the source SVG directory for the icon pack."""

    repository = (root or project_root()).resolve()
    return repository / "src" / "hms_cadcam" / "ui" / "assets" / "icons" / "isometric_cadcam" / "svg"


def png_directory(root: Path | None = None) -> Path:
    """Return the generated PNG root directory for the icon pack."""

    repository = (root or project_root()).resolve()
    return repository / "src" / "hms_cadcam" / "ui" / "assets" / "icons" / "isometric_cadcam" / "png"


def review_directory(root: Path | None = None) -> Path:
    """Return the private visual-review output directory."""

    repository = (root or project_root()).resolve()
    return repository / "reference_private" / "DERIVED" / "UI_ICON_PACK_9AI1"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_svg(svg_path: Path) -> tuple[str, ...]:
    """Return structural validation errors for one production SVG."""

    try:
        root = ET.parse(svg_path).getroot()
    except (ET.ParseError, OSError) as error:
        return (f"cannot parse {svg_path.name}: {error}",)

    errors: list[str] = []
    if _local_name(root.tag) != "svg":
        errors.append("root element is not svg")
    if root.get("viewBox") != "0 0 64 64":
        errors.append('viewBox must be "0 0 64 64"')
    if root.get("width") != "64" or root.get("height") != "64":
        errors.append("width and height must both be 64")

    ids: set[str] = set()
    geometry_count = 0
    for element in root.iter():
        tag = _local_name(element.tag)
        if tag in SVG_BANNED_TAGS:
            errors.append(f"banned element <{tag}>")
        if tag not in SVG_ALLOWED_TAGS:
            errors.append(f"unsupported element <{tag}>")
        if tag in SVG_GEOMETRY_TAGS:
            geometry_count += 1
        element_id = element.get("id")
        if element_id:
            if element_id in ids:
                errors.append(f"duplicate id {element_id!r}")
            ids.add(element_id)
        for attribute, value in element.attrib.items():
            if _EXTERNAL_RE.search(value):
                errors.append(f"external resource in {attribute}")
            if _NUMERIC_RE.search(value) and any(
                token in value.lower() for token in ("nan", "infinity")
            ):
                errors.append(f"non-finite coordinate in {attribute}")
        fill = element.get("fill", "")
        if tag == "rect" and fill not in ("", "none"):
            if (
                element.get("x", "0") == "0"
                and element.get("y", "0") == "0"
                and element.get("width") == "64"
                and element.get("height") == "64"
            ):
                errors.append("opaque full-viewBox background rectangle")

    if geometry_count == 0:
        errors.append("SVG has no visible geometry")
    raw = svg_path.read_text(encoding="utf-8")
    if "<!--" in raw or "<?xml-stylesheet" in raw:
        errors.append("SVG contains an external/style declaration")
    return tuple(dict.fromkeys(errors))


def _ensure_application() -> QGuiApplication:
    application = QGuiApplication.instance()
    if application is None:
        application = QGuiApplication([sys.argv[0]])
    return application


def _validated_renderer(svg_path: Path) -> QSvgRenderer:
    errors = validate_svg(svg_path)
    if errors:
        raise ValueError(f"{svg_path.name}: " + "; ".join(errors))
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"{svg_path.name}: QSvgRenderer rejected SVG")
    return renderer


def render_svg(svg_path: Path, size: int) -> QImage:
    """Render one valid SVG into a transparent ARGB32 image."""

    if size <= 0:
        raise ValueError("render size must be positive")
    _ensure_application()
    renderer = _validated_renderer(svg_path)
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
    )
    try:
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer, "PNG"):
            raise OSError("QImage could not encode PNG")
        return bytes(buffer.data())
    finally:
        buffer.close()


def _write_if_changed(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
    except OSError as error:
        raise OSError(f"cannot read existing {path}: {error}") from error
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError as error:
        raise OSError(f"cannot write {path}: {error}") from error
    return True


def _normalise_sizes(sizes: Iterable[int]) -> tuple[int, ...]:
    normalised = tuple(sorted(set(int(size) for size in sizes)))
    if not normalised or any(size <= 0 for size in normalised):
        raise ValueError("sizes must contain positive integers")
    return normalised


def _icon_svg_paths(root: Path) -> tuple[Path, ...]:
    directory = svg_directory(root)
    return tuple(directory / f"{name}.svg" for name in ICON_NAMES)


def _render_pack(root: Path, sizes: tuple[int, ...]) -> dict[int, dict[str, QImage]]:
    rendered: dict[int, dict[str, QImage]] = {}
    for svg_path in _icon_svg_paths(root):
        if not svg_path.is_file():
            raise FileNotFoundError(f"missing SVG: {svg_path}")
        for size in sizes:
            rendered.setdefault(size, {})[svg_path.stem] = render_svg(svg_path, size)
    return rendered


def _png_path(root: Path, icon_name: str, size: int) -> Path:
    return png_directory(root) / str(size) / f"{icon_name}.png"


def build_pngs(root: Path | None = None, sizes: Iterable[int] = DEFAULT_SIZES) -> int:
    """Render the SVG pack and return the number of changed PNG files."""

    repository = (root or project_root()).resolve()
    requested = _normalise_sizes(sizes)
    rendered = _render_pack(repository, requested)
    changed = 0
    for size in requested:
        for icon_name in ICON_NAMES:
            if _write_if_changed(
                _png_path(repository, icon_name, size),
                _png_bytes(rendered[size][icon_name]),
            ):
                changed += 1
    logger.info("PNG build: %d file(s) changed, %d size(s)", changed, len(requested))
    return changed


def _visible_bounds(image: QImage) -> tuple[int, int, int, int] | None:
    bounds: tuple[int, int, int, int] | None = None
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() == 0:
                continue
            if bounds is None:
                bounds = (x, y, x, y)
            else:
                bounds = (min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y))
    return bounds


def _check_png(path: Path, expected: bytes, size: int) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        actual = path.read_bytes()
    except OSError as error:
        return (f"missing/unreadable PNG {path}: {error}",)
    if actual != expected:
        errors.append("PNG bytes differ from deterministic SVG render")
    image = QImage(str(path))
    if image.isNull():
        return tuple(errors + ["PNG cannot be decoded by QImage"])
    if image.width() != size or image.height() != size:
        errors.append(f"dimensions are {image.width()}x{image.height()}, expected {size}x{size}")
    if not image.hasAlphaChannel():
        errors.append("PNG has no alpha channel")
    if image.pixelColor(0, 0).alpha() != 0:
        errors.append("top-left pixel is not transparent")
    bounds = _visible_bounds(image)
    if bounds is None:
        errors.append("PNG is completely transparent")
    elif bounds[0] <= 0 or bounds[1] <= 0 or bounds[2] >= size - 1 or bounds[3] >= size - 1:
        errors.append(f"visible geometry is cropped at {bounds}")
    png_color_type = actual[25] if len(actual) > 25 and actual[:8] == b"\x89PNG\r\n\x1a\n" else None
    if png_color_type != 6:
        errors.append(f"PNG color type is {png_color_type!r}, expected RGBA type 6")
    return tuple(errors)


def check_pack(root: Path | None = None, sizes: Iterable[int] = DEFAULT_SIZES) -> tuple[str, ...]:
    """Validate SVGs and compare every requested PNG with a fresh render."""

    repository = (root or project_root()).resolve()
    requested = _normalise_sizes(sizes)
    errors: list[str] = []
    for svg_path in _icon_svg_paths(repository):
        errors.extend(f"{svg_path.name}: {error}" for error in validate_svg(svg_path))
        if not svg_path.is_file():
            errors.append(f"missing SVG: {svg_path}")
    if errors:
        return tuple(errors)
    rendered = _render_pack(repository, requested)
    for size in requested:
        for icon_name in ICON_NAMES:
            errors.extend(
                f"{icon_name}@{size}: {error}"
                for error in _check_png(
                    _png_path(repository, icon_name, size),
                    _png_bytes(rendered[size][icon_name]),
                    size,
                )
            )
    return tuple(errors)


def _font_family() -> str:
    """Return a stable, available review-sheet font family."""

    families = set(QFontDatabase.families())
    for family in ("Segoe UI", "Arial", "Noto Sans", "DejaVu Sans"):
        if family in families:
            return family
    windows_directory = os.environ.get("WINDIR")
    if windows_directory:
        for filename in ("segoeui.ttf", "arial.ttf"):
            font_path = Path(windows_directory) / "Fonts" / filename
            if not font_path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id >= 0:
                loaded = QFontDatabase.applicationFontFamilies(font_id)
                if loaded:
                    return loaded[0]
    return "Sans Serif"


def _checkerboard(painter: QPainter, rect: QRect, square: int = 8) -> None:
    light = QColor("#F7F9FA")
    dark = QColor("#E2E7EB")
    for row, y in enumerate(range(rect.top(), rect.bottom(), square)):
        for column, x in enumerate(range(rect.left(), rect.right(), square)):
            painter.fillRect(
                QRect(x, y, min(square, rect.right() - x), min(square, rect.bottom() - y)),
                light if (row + column) % 2 == 0 else dark,
            )


def _save_review_sheet(image: QImage, destination: Path) -> None:
    _write_if_changed(destination, _png_bytes(image))


def _contact_sheet_for_size(
    rendered: dict[str, QImage], size: int, destination: Path
) -> None:
    cell_width, cell_height = 190, 150
    columns = 3
    rows = (len(ICON_NAMES) + columns - 1) // columns
    sheet = QImage(columns * cell_width, rows * cell_height + 42, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#DCE3E8"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(QFont(_font_family(), 10, QFont.Weight.DemiBold))
    painter.setPen(QColor("#203243"))
    painter.drawText(QRect(18, 8, sheet.width() - 36, 26), Qt.AlignmentFlag.AlignLeft, f"HMS Isometric CAD/CAM — {size}px")
    label_font = QFont(_font_family(), 8)
    painter.setFont(label_font)
    for index, icon_name in enumerate(ICON_NAMES):
        column, row = index % columns, index // columns
        x, y = column * cell_width + 10, row * cell_height + 40
        tile = QRect(x, y, cell_width - 20, 104)
        painter.setPen(QPen(QColor("#B7C2CB"), 1))
        painter.fillRect(tile, QColor("#F7F9FA"))
        painter.drawRect(tile)
        checker = QRect(tile.left() + (tile.width() - 88) // 2, tile.top() + 8, 88, 78)
        _checkerboard(painter, checker, 8)
        icon = rendered[icon_name]
        painter.drawImage(
            checker.left() + (checker.width() - icon.width()) // 2,
            checker.top() + (checker.height() - icon.height()) // 2,
            icon,
        )
        painter.setPen(QColor("#203243"))
        painter.drawText(QRect(x, tile.bottom() + 6, tile.width(), 28), Qt.AlignmentFlag.AlignCenter, icon_name)
    painter.end()
    _save_review_sheet(sheet, destination)


def _comparison_sheet(
    rendered: dict[int, dict[str, QImage]], destination: Path
) -> None:
    columns = len(REVIEW_SIZES)
    cell_width, cell_height = 150, 92
    label_width = 156
    sheet = QImage(label_width + columns * cell_width, 40 + len(ICON_NAMES) * cell_height, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#DCE3E8"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(QColor("#203243"))
    painter.setFont(QFont(_font_family(), 10, QFont.Weight.DemiBold))
    painter.drawText(QRect(12, 8, sheet.width() - 24, 24), Qt.AlignmentFlag.AlignLeft, "HMS Isometric CAD/CAM — native-size comparison")
    painter.setFont(QFont(_font_family(), 8, QFont.Weight.DemiBold))
    for column, size in enumerate(REVIEW_SIZES):
        painter.drawText(QRect(label_width + column * cell_width, 10, cell_width, 20), Qt.AlignmentFlag.AlignCenter, f"{size}px")
    painter.setFont(QFont(_font_family(), 8))
    for row, icon_name in enumerate(ICON_NAMES):
        y = 40 + row * cell_height
        painter.setPen(QColor("#203243"))
        painter.drawText(QRect(8, y, label_width - 16, cell_height), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, icon_name)
        for column, size in enumerate(REVIEW_SIZES):
            x = label_width + column * cell_width
            tile = QRect(x + 16, y + 8, cell_width - 32, cell_height - 16)
            _checkerboard(painter, tile, 8)
            icon = rendered[size][icon_name]
            painter.drawImage(x + (cell_width - icon.width()) // 2, y + (cell_height - icon.height()) // 2, icon)
    painter.end()
    _save_review_sheet(sheet, destination)


def build_review_sheets(root: Path | None = None) -> tuple[Path, ...]:
    """Build the three native-size sheets and one all-size comparison sheet."""

    repository = (root or project_root()).resolve()
    rendered = _render_pack(repository, REVIEW_SIZES)
    output = review_directory(repository)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for size in REVIEW_SIZES:
        destination = output / f"contact_sheet_{size}.png"
        _contact_sheet_for_size(rendered[size], size, destination)
        paths.append(destination)
    comparison = output / "comparison_all_sizes.png"
    _comparison_sheet(rendered, comparison)
    paths.append(comparison)
    logger.info("Review sheets: %s", output)
    return tuple(paths)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate SVG and existing PNG output")
    parser.add_argument("--sizes", nargs="+", type=int, default=DEFAULT_SIZES, help="PNG sizes to build/check")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the icon builder and return a process exit status."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    try:
        requested = _normalise_sizes(args.sizes)
        if args.check:
            errors = check_pack(sizes=requested)
            if errors:
                for error in errors:
                    logger.error(error)
                return 1
            logger.info("Icon pack check passed: %d SVG × %d size(s)", len(ICON_NAMES), len(requested))
            return 0
        build_pngs(sizes=requested)
        build_review_sheets()
    except (FileNotFoundError, OSError, RuntimeError, ValueError, ET.ParseError) as error:
        logger.error("Icon pack build failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
