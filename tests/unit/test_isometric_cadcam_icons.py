"""Contract tests for the Stage 9A.I1 HMS isometric icon pack."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402

from tools import build_isometric_cadcam_icons as icons  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
SVG_ROOT = icons.svg_directory(ROOT)
PNG_ROOT = icons.png_directory(ROOT)
SCRIPT = ROOT / "tools" / "build_isometric_cadcam_icons.py"


def _image_luminances(image: QImage) -> list[int]:
    values: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 0:
                values.append(round(0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()))
    return values


def test_pack_has_exactly_six_independent_svg_sources() -> None:
    paths = sorted(SVG_ROOT.glob("*.svg"))
    assert [path.stem for path in paths] == sorted(icons.ICON_NAMES)
    assert all(path.read_bytes().startswith(b"<?xml") for path in paths)
    assert all(not path.read_bytes().startswith(b"\xef\xbb\xbf") for path in paths)


def test_svg_structure_palette_groups_and_no_external_resources() -> None:
    required_groups = {
        "threaded-shaft": {"main-solid", "construction-detail", "feature-highlight"},
        "slot-cut": {"main-solid", "feature-highlight", "tool", "action-arrow"},
        "mirror-body": {"main-solid", "mirror-plane", "action-arrow"},
        "offset-surface": {"main-solid", "construction-detail", "action-arrow"},
        "scale-up": {"main-solid", "construction-detail", "action-arrow"},
        "join-bodies": {"main-solid", "feature-highlight", "action-arrow"},
    }
    color_re = re.compile(r"#[0-9A-Fa-f]{6}")
    for icon_name in icons.ICON_NAMES:
        path = SVG_ROOT / f"{icon_name}.svg"
        assert icons.validate_svg(path) == ()
        raw = path.read_text(encoding="utf-8")
        assert "<text" not in raw
        assert "<image" not in raw
        assert "base64" not in raw.lower()
        assert "http://" not in raw.lower().replace('xmlns="http://www.w3.org/2000/svg"', "")
        ids = re.findall(r'\bid="([^"]+)"', raw)
        assert len(ids) == len(set(ids))
        assert required_groups[icon_name] <= set(re.findall(r'<g id="([^"]+)"', raw))
        assert set(color_re.findall(raw)) <= icons.SVG_PALETTE
        assert re.search(r"\b(?:NaN|Infinity)\b", raw, re.IGNORECASE) is None


def test_each_size_is_rgba_transparent_uncropped_and_contrasty() -> None:
    for size in icons.DEFAULT_SIZES:
        for icon_name in icons.ICON_NAMES:
            path = PNG_ROOT / str(size) / f"{icon_name}.png"
            assert path.is_file(), path
            data = path.read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            assert data[25] == 6  # PNG color type RGBA.
            image = QImage(str(path))
            assert not image.isNull()
            assert image.width() == size and image.height() == size
            assert image.hasAlphaChannel()
            assert image.pixelColor(0, 0).alpha() == 0
            bounds = icons._visible_bounds(image)
            assert bounds is not None
            assert bounds[0] > 0 and bounds[1] > 0
            assert bounds[2] < size - 1 and bounds[3] < size - 1
            luminances = _image_luminances(image)
            assert max(luminances) - min(luminances) >= 24


def test_pngs_are_byte_deterministic_renders() -> None:
    for size in icons.DEFAULT_SIZES:
        for icon_name in icons.ICON_NAMES:
            svg_path = SVG_ROOT / f"{icon_name}.svg"
            first = icons.render_svg(svg_path, size)
            second = icons.render_svg(svg_path, size)
            first_bytes = icons._png_bytes(first)
            second_bytes = icons._png_bytes(second)
            assert first_bytes == second_bytes
            assert first_bytes == (PNG_ROOT / str(size) / f"{icon_name}.png").read_bytes()


def test_pack_check_has_no_errors() -> None:
    assert icons.check_pack(ROOT) == ()


def test_command_check_is_independent_of_working_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_svg_is_reported_without_silencing_errors(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.svg"
    invalid.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        '<rect width="64" height="64" fill="#ffffff"/><text>bad</text></svg>',
        encoding="utf-8",
    )
    errors = icons.validate_svg(invalid)
    assert any("banned element <text>" in error for error in errors)
    assert any("opaque full-viewBox" in error for error in errors)
