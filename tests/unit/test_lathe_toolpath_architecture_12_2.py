"""Stage 12.2 architecture, I18N, feature and scope gates."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from hms_cadcam.cam.lathe.toolpath import (
    EXECUTABLE_LATHE_TOOLPATH_STRATEGIES,
    UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES,
)
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


ROOT = Path(__file__).resolve().parents[2]
TOOLPATH = ROOT / "src" / "hms_cadcam" / "cam" / "lathe" / "toolpath"
UI = ROOT / "src" / "hms_cadcam" / "ui"
SPEC = ROOT / "docs" / "architecture" / "STAGE_12_2_LATHE_TOOLPATH_PREVIEW_V2.md"

HISTORICAL_DIRTY = {
    "docs/CAM_3D_STAGE_8A3_1_Z_LEVEL_FINISHING_FOUNDATION.md",
    "docs/CURRENT_TASK.md",
    "docs/HMS_STORAGE_ARCHITECTURE_8A4_4.md",
    "docs/PROJECT_STATE.md",
    "docs/UI_POST_PROGRAM_ASSEMBLY_9A7.md",
    "docs/UI_STAGE_9A7_ACCEPTANCE.md",
}


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_v2_spec_is_authoritative_and_covers_six_algorithms_and_exclusions() -> None:
    text = SPEC.read_text(encoding="utf-8")
    required = (
        "owner-approved authoritative additive specification",
        "lathe.face.toolpath.v2",
        "lathe.id_rough.toolpath.v2",
        "lathe.id_finish.toolpath.v2",
        "lathe.od_groove.toolpath.v2",
        "lathe.id_groove.toolpath.v2",
        "lathe.part_off.toolpath.v2",
        "9/11",
        "thread_toolpath_not_implemented_v2",
        "missing_internal_bore",
        "1e-9 mm",
        "(X / 2, 0, Z)",
        "LATHE_TOOLPATH_12_1",
        "NOT_STARTED",
        "Post",
        "G-code",
        "simulation",
        "persistence",
    )
    assert all(token in text for token in required)


def test_pure_v2_toolpath_has_no_qt_ocp_viewer_post_simulation_or_persistence() -> None:
    forbidden_imports = (
        "PySide6",
        "shiboken6",
        "OCP",
        "hms_cadcam.viewer",
        "hms_cadcam.ui",
        "hms_cadcam.cam.post",
        "hms_cadcam.cam.simulation",
        "hms_cadcam.project",
        "sqlite",
        "pickle",
        "multiprocessing",
    )
    forbidden_source = (
        "generate_gcode",
        "post_processor",
        "stock_removal",
        "collision_check",
        "project.db",
        "project.hms.json",
        "ThreadToolpathGenerator",
    )
    for path in sorted(TOOLPATH.glob("*.py")):
        imports = _imports(path)
        source = path.read_text(encoding="utf-8")
        assert not any(
            item == token or item.startswith(token + ".")
            for item in imports
            for token in forbidden_imports
        )
        assert not any(token in source for token in forbidden_source)


def test_registry_partition_and_feature_flag_topology_are_reused() -> None:
    assert len(EXECUTABLE_LATHE_TOOLPATH_STRATEGIES) == 9
    assert len(UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES) == 2
    assert set(EXECUTABLE_LATHE_TOOLPATH_STRATEGIES).isdisjoint(
        UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES
    )
    assert UiFeatureFlag.LATHE_TOOLPATH_12_1.value == "lathe_toolpath_12_1"
    assert not UiFeatureFlags.for_development_and_tests().is_enabled(
        UiFeatureFlag.LATHE_TOOLPATH_12_1
    )
    source = (UI / "main_window.py").read_text(encoding="utf-8")
    assert source.count("LatheViewportPreviewSink(self.viewport)") == 1
    workspace = (UI / "lathe_workspace.py").read_text(encoding="utf-8")
    assert workspace.count("LathePreviewToolpathButton") == 1
    assert workspace.count("LatheCancelCalculationButton") == 1


def test_vi_en_ko_catalogs_remain_in_parity_with_v2_thread_status() -> None:
    catalogs = {
        name: json.loads((UI / "catalogs" / name).read_text(encoding="utf-8"))
        for name in ("vi_VN.json", "en_US.json", "ko_KR.json")
    }
    keys = tuple(catalogs["vi_VN.json"])
    assert all(tuple(entries) == keys for entries in catalogs.values())
    legacy_key = "lathe.toolpath.status.unsupported_strategy"
    v2_thread_key = "lathe.toolpath.status.stage12_2_thread_unsupported"
    expected = {
        "vi_VN.json": {
            legacy_key: (
                "Chiến lược này chưa hỗ trợ xem trước đường chạy dao trong V1."
            ),
            v2_thread_key: (
                "Xem trước đường chạy dao ren chưa được triển khai trong V2."
            ),
        },
        "en_US.json": {
            legacy_key: "This strategy has no V1 toolpath preview implementation.",
            v2_thread_key: "Thread toolpath preview is not implemented in V2.",
        },
        "ko_KR.json": {
            legacy_key: "이 전략은 V1 공구경로 미리보기를 지원하지 않습니다.",
            v2_thread_key: "V2에서는 나사 공구경로 미리보기가 구현되지 않았습니다.",
        },
    }
    assert all(
        catalogs[name][key] == value
        for name, entries in expected.items()
        for key, value in entries.items()
    )


def test_stage12_2_imports_do_not_mutate_shared_profile_or_ui_state(
    tmp_path: Path,
) -> None:
    probe = r'''from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import DEFAULT_TOOL_PROFILE_REGISTRY
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import translation_service


def digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(
    QSettings.Format.IniFormat,
    QSettings.Scope.UserScope,
    os.environ["HMS_TEST_QSETTINGS"],
)


def snapshot() -> dict[str, object]:
    service = translation_service()
    settings = QSettings("HMS", "stage12_2_import_isolation")
    registry_payload = [
        schema.report_dict() for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas
    ]
    catalogs = {
        language.value: dict(catalog.entries)
        for language, catalog in service.catalogs.items()
    }
    flag_sets = {}
    for name, flags in (
        ("development", UiFeatureFlags.for_development_and_tests()),
        ("review", UiFeatureFlags.for_review_harness()),
        ("production", UiFeatureFlags.for_production()),
    ):
        flag_sets[name] = {
            flag.value: flags.is_enabled(flag) for flag in UiFeatureFlag
        }
    app = QApplication.instance()
    return {
        "registry_count": len(registry_payload),
        "registry_sha256": digest(registry_payload),
        "language": service.language.value,
        "catalog_sha256": digest(catalogs),
        "feature_flags": flag_sets,
        "qsettings_file": settings.fileName(),
        "qsettings_keys": sorted(settings.allKeys()),
        "qapplication_exists": app is not None,
        "application_name": QCoreApplication.applicationName(),
        "organization_name": QCoreApplication.organizationName(),
        "library_paths": QCoreApplication.libraryPaths(),
        "top_level_widgets": [] if app is None else sorted(
            widget.objectName() or type(widget).__name__
            for widget in app.topLevelWidgets()
        ),
    }


before = snapshot()
modules = (
    "hms_cadcam.cam.lathe.toolpath.model",
    "hms_cadcam.cam.lathe.toolpath.request",
    "hms_cadcam.cam.lathe.toolpath.generators",
    "hms_cadcam.cam.lathe.toolpath",
    "hms_cadcam.ui.lathe_toolpath",
)
loaded = [importlib.import_module(name) for name in modules]
after = snapshot()
print(json.dumps({
    "before": before,
    "after": after,
    "module_files": [str(Path(module.__file__).resolve()) for module in loaded],
}, ensure_ascii=False, sort_keys=True))
'''
    env = os.environ.copy()
    env["HMS_TEST_QSETTINGS"] = str(tmp_path / "qsettings")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("QT_QPA_FONTDIR", None)
    source_root = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, env.get("PYTHONPATH", "")) if value
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["before"] == payload["after"]
    assert all(
        Path(module_file).is_relative_to((ROOT / "src").resolve())
        for module_file in payload["module_files"]
    )


def test_locked_stage12_stage9a9_and_stage12_1_specs_are_unchanged() -> None:
    locked = (
        "docs/architecture/STAGE_12_LATHE_FOUNDATION_V1.md",
        "docs/architecture/STAGE_9A9_LATHE_UI_CONTRACT_V1.md",
        "docs/architecture/STAGE_12_1_LATHE_TOOLPATH_PREVIEW_V1.md",
    )
    completed = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *locked],
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0


def test_candidate_paths_are_allowlisted_and_below_stage12_2_caps() -> None:
    completed = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=True,
    )
    dirty = {
        line[3:].replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    } - HISTORICAL_DIRTY
    assert len(dirty) <= 47
    assert sum(path.startswith("src/") and "/catalogs/" not in path for path in dirty) <= 24
    assert sum(path.startswith("tests/") for path in dirty) <= 18
    assert sum("/catalogs/" in path for path in dirty) <= 3
    assert sum(path.startswith("docs/") for path in dirty) <= 2
    forbidden = (
        "database",
        "schema",
        "/post/",
        "gcode",
        "simulation",
        "persistence",
    )
    assert not any(token in path.casefold() for path in dirty for token in forbidden)


def test_public_toolpath_exports_all_six_generators_and_versions() -> None:
    source = (TOOLPATH / "__init__.py").read_text(encoding="utf-8")
    required = (
        "FaceToolpathGenerator",
        "IdRoughToolpathGenerator",
        "IdFinishToolpathGenerator",
        "OdGrooveToolpathGenerator",
        "IdGrooveToolpathGenerator",
        "PartOffToolpathGenerator",
        "LATHE_FACE_ALGORITHM_VERSION",
        "LATHE_ID_ROUGH_ALGORITHM_VERSION",
        "LATHE_ID_FINISH_ALGORITHM_VERSION",
        "LATHE_OD_GROOVE_ALGORITHM_VERSION",
        "LATHE_ID_GROOVE_ALGORITHM_VERSION",
        "LATHE_PART_OFF_ALGORITHM_VERSION",
    )
    assert all(source.count(token) == 2 for token in required)
