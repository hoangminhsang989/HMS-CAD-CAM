"""Architecture, feature, I18N and scope-exclusion gates for Stage 12.1."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


ROOT = Path(__file__).resolve().parents[2]
TOOLPATH_ROOT = ROOT / "src" / "hms_cadcam" / "cam" / "lathe" / "toolpath"
UI_ROOT = ROOT / "src" / "hms_cadcam" / "ui"
VIEWER_ROOT = ROOT / "src" / "hms_cadcam" / "viewer"
SPEC = ROOT / "docs" / "architecture" / "STAGE_12_1_LATHE_TOOLPATH_PREVIEW_V1.md"

PURE_PATHS = tuple(sorted(TOOLPATH_ROOT.glob("*.py")))
STAGE12_1_UI_PATHS = (
    UI_ROOT / "lathe_toolpath.py",
    UI_ROOT / "lathe_session.py",
    UI_ROOT / "lathe_workspace.py",
)
STAGE12_1_VIEWER_PATHS = (
    VIEWER_ROOT / "lathe.py",
    VIEWER_ROOT / "backend.py",
    VIEWER_ROOT / "unavailable_backend.py",
    VIEWER_ROOT / "widget.py",
    VIEWER_ROOT / "ocp" / "backend.py",
)

TOOLPATH_KEYS = {
    "lathe.toolpath.preview.action",
    "lathe.toolpath.preview.help",
    "lathe.toolpath.cancel.action",
    "lathe.toolpath.cancel.help",
    *(f"lathe.toolpath.status.{value}" for value in (
        "ready",
        "calculating",
        "cancelling",
        "preview_ready",
        "cache_hit",
        "cancelled",
        "unsupported_strategy",
        "invalid_request",
        "generation_failed",
        "publication_failed",
        "stale_result_dropped",
    )),
}

CANDIDATE_PATHS = {
    "docs/architecture/STAGE_12_1_LATHE_TOOLPATH_PREVIEW_V1.md",
    "src/hms_cadcam/cam/lathe/toolpath/__init__.py",
    "src/hms_cadcam/cam/lathe/toolpath/model.py",
    "src/hms_cadcam/cam/lathe/toolpath/stock.py",
    "src/hms_cadcam/cam/lathe/toolpath/request.py",
    "src/hms_cadcam/cam/lathe/toolpath/generators.py",
    "src/hms_cadcam/cam/lathe/toolpath/runtime.py",
    "src/hms_cadcam/viewer/lathe.py",
    "src/hms_cadcam/ui/lathe_toolpath.py",
    "src/hms_cadcam/ui/feature_flags.py",
    "src/hms_cadcam/ui/lathe_session.py",
    "src/hms_cadcam/ui/lathe_workspace.py",
    "src/hms_cadcam/ui/main_window.py",
    "src/hms_cadcam/viewer/backend.py",
    "src/hms_cadcam/viewer/unavailable_backend.py",
    "src/hms_cadcam/viewer/widget.py",
    "src/hms_cadcam/viewer/ocp/backend.py",
    "src/hms_cadcam/viewer/__init__.py",
    "src/hms_cadcam/ui/catalogs/vi_VN.json",
    "src/hms_cadcam/ui/catalogs/en_US.json",
    "src/hms_cadcam/ui/catalogs/ko_KR.json",
    "tests/unit/_lathe_toolpath_fixtures.py",
    "tests/unit/test_lathe_toolpath_contracts_12_1.py",
    "tests/unit/test_lathe_toolpath_request_cache_12_1.py",
    "tests/unit/test_lathe_toolpath_generators_12_1.py",
    "tests/unit/test_lathe_toolpath_worker_12_1.py",
    "tests/unit/test_lathe_toolpath_viewport_12_1.py",
    "tests/unit/test_lathe_toolpath_ui_12_1.py",
    "tests/unit/test_lathe_toolpath_architecture_12_1.py",
    "tests/unit/test_lathe_architecture.py",
    "tests/unit/test_lathe_i18n_architecture_9a9.py",
}


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_pure_toolpath_layer_has_no_qt_ocp_viewer_or_ui_import() -> None:
    forbidden = ("PySide6", "shiboken6", "OCP", "hms_cadcam.viewer", "hms_cadcam.ui")
    assert len(PURE_PATHS) == 6
    for path in PURE_PATHS:
        imports = _imports(path)
        assert not any(
            imported == token or imported.startswith(f"{token}.")
            for imported in imports
            for token in forbidden
        )


def test_pure_public_import_does_not_load_pyside_or_ocp_in_fresh_process() -> None:
    code = (
        "import sys; "
        "import hms_cadcam.cam.lathe.toolpath; "
        "bad=sorted(n for n in sys.modules if n.startswith(('PySide6','shiboken6','OCP'))); "
        "print('\\n'.join(bad)); raise SystemExit(bool(bad))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_all_touched_python_files_parse_and_direct_import_without_cycle() -> None:
    paths = (*PURE_PATHS, *STAGE12_1_UI_PATHS, *STAGE12_1_VIEWER_PATHS)
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    code = (
        "import hms_cadcam.cam.lathe.toolpath; "
        "import hms_cadcam.viewer.lathe; "
        "import hms_cadcam.ui.lathe_toolpath; "
        "import hms_cadcam.ui.lathe_session; "
        "import hms_cadcam.ui.lathe_workspace; "
        "import hms_cadcam.ui.main_window; "
        "import hms_cadcam.viewer.ocp.backend"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={
            **dict(__import__("os").environ),
            "PYTHONPATH": str(ROOT / "src"),
            "QT_QPA_PLATFORM": "offscreen",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_exact_nine_executable_and_two_unsupported_strategy_partition() -> None:
    assert EXECUTABLE_LATHE_TOOLPATH_STRATEGIES == (
        LatheStrategyId.FACE,
        LatheStrategyId.OD_ROUGH,
        LatheStrategyId.OD_FINISH,
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.OD_GROOVE,
        LatheStrategyId.ID_GROOVE,
        LatheStrategyId.PART_OFF,
        LatheStrategyId.AXIAL_DRILL,
    )
    assert UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES == (
        LatheStrategyId.OD_THREAD,
        LatheStrategyId.ID_THREAD,
    )
    generator_source = (TOOLPATH_ROOT / "generators.py").read_text(encoding="utf-8")
    assert "ThreadToolpathGenerator" not in generator_source


def test_toolpath_layers_have_no_persistence_post_gcode_simulation_or_process_worker() -> None:
    forbidden_imports = (
        "sqlite",
        "pickle",
        "hms_cadcam.project",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "multiprocessing",
        "subprocess",
    )
    forbidden_source = (
        "project.db",
        "project.hms.json",
        "generate_gcode",
        "post_processor",
        "stock_removal",
        "collision_check",
    )
    for path in PURE_PATHS:
        imports = _imports(path)
        source = path.read_text(encoding="utf-8").casefold()
        assert not any(
            imported == token or imported.startswith(f"{token}.")
            for imported in imports
            for token in forbidden_imports
        )
        assert not any(token in source for token in forbidden_source)


def test_fingerprint_source_uses_no_time_random_locale_theme_or_rendering_data() -> None:
    source = (TOOLPATH_ROOT / "request.py").read_text(encoding="utf-8")
    imports = _imports(TOOLPATH_ROOT / "request.py")
    assert "time" not in imports and "random" not in imports and "uuid" not in imports
    semantic_function = source[source.index("def _semantic_payload"):source.index("def _sha256_payload")]
    assert not any(
        token in semantic_function
        for token in ("job_id", "request_sequence", "language", "locale", "theme", "ui_scale", "actor")
    )


def test_feature_flag_is_independent_typed_and_off_in_every_default_profile() -> None:
    flag = UiFeatureFlag.LATHE_TOOLPATH_12_1
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(flag)
    assert not UiFeatureFlags.for_review_harness().is_enabled(flag)
    assert not UiFeatureFlags.for_production().is_enabled(flag)
    only_lathe = UiFeatureFlags({UiFeatureFlag.LATHE_9A9: True})
    assert only_lathe.is_enabled(UiFeatureFlag.LATHE_9A9)
    assert not only_lathe.is_enabled(flag)
    only_toolpath = UiFeatureFlags({flag: True})
    assert only_toolpath.is_enabled(flag)
    assert not only_toolpath.is_enabled(UiFeatureFlag.LATHE_9A9)


def test_main_window_hosts_toolpath_only_when_both_additive_flags_are_enabled() -> None:
    source = (UI_ROOT / "main_window.py").read_text(encoding="utf-8")
    assert "self._lathe_review_host\n            and self._ui_feature_flags.is_enabled(UiFeatureFlag.LATHE_TOOLPATH_12_1)" in source
    assert source.count("LatheViewportPreviewSink(self.viewport)") == 1
    assert source.count("toolpath_sink=(") == 1


def test_workspace_has_explicit_preview_only_and_edits_only_invalidate() -> None:
    tree = ast.parse(
        (UI_ROOT / "lathe_workspace.py").read_text(encoding="utf-8"),
        filename="lathe_workspace.py",
    )
    preview_callers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "preview"
            ):
                preview_callers.append(node.name)
    assert preview_callers == ["_preview_toolpath"]
    source = (UI_ROOT / "lathe_workspace.py").read_text(encoding="utf-8")
    command_block = source[source.index("def _command_completed"):source.index("def _revision_conflict")]
    assert "invalidate_after_edit" in command_block
    assert ".preview(" not in command_block


def test_vi_en_ko_have_exact_new_key_parity_and_honest_offline_wording() -> None:
    catalogs = {
        name: json.loads(
            (UI_ROOT / "catalogs" / name).read_text(encoding="utf-8")
        )
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    }
    for entries in catalogs.values():
        assert TOOLPATH_KEYS.issubset(entries)
        assert all(entries[key].strip() for key in TOOLPATH_KEYS)
        assert all(key not in entries[key] for key in TOOLPATH_KEYS)
    en_success = catalogs["en_US.json"]["lathe.toolpath.status.preview_ready"].casefold()
    vi_success = catalogs["vi_VN.json"]["lathe.toolpath.status.preview_ready"].casefold()
    ko_success = catalogs["ko_KR.json"]["lathe.toolpath.status.preview_ready"].casefold()
    assert "offline nominal preview" in en_success and "not a machine-ready" in en_success
    assert "offline" in vi_success and "chạy máy" in vi_success
    assert "오프라인" in ko_success and "기계" in ko_success


def test_authoritative_spec_covers_contract_algorithms_lifecycle_and_exclusions() -> None:
    source = SPEC.read_text(encoding="utf-8")
    required = (
        "lathe.od_rough.v1",
        "lathe.od_finish.v1",
        "lathe.axial_drill.v1",
        "toolpath_not_implemented_v1",
        "x_diameter_mm",
        "(X / 2, 0, Z)",
        "lathe.toolpath.preview.v1",
        "latest-wins",
        "bounded deterministic FIFO cache",
        "atomic",
        "LATHE_TOOLPATH_12_1",
        "offline nominal",
        "not machine-ready",
        "Post",
        "G-code",
        "simulation",
        "persistence",
    )
    assert all(token in source for token in required)
    assert "Status: owner-approved authoritative additive specification" in source


def test_candidate_allowlist_is_exact_individually_bounded_and_has_no_scope_path() -> None:
    assert len(CANDIDATE_PATHS) == 31
    assert len(CANDIDATE_PATHS) <= 57
    assert sum(path.startswith("src/") for path in CANDIDATE_PATHS) == 20
    assert sum(path.startswith("tests/") for path in CANDIDATE_PATHS) == 10
    assert sum(path.startswith("docs/") for path in CANDIDATE_PATHS) == 1
    forbidden = ("database", "schema", "post", "gcode", "simulation", "persistence")
    assert not any(
        token in path.casefold()
        for path in CANDIDATE_PATHS
        for token in forbidden
    )


def test_locked_stage12_and_stage9a9_specs_remain_unmodified() -> None:
    locked = (
        "docs/architecture/STAGE_12_LATHE_FOUNDATION_V1.md",
        "docs/architecture/STAGE_9A9_LATHE_UI_CONTRACT_V1.md",
    )
    completed = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *locked],
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0
