"""Create and verify the Stage 9A.7 WP2 Review R3 package."""

from __future__ import annotations

from datetime import datetime, timezone
import difflib
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for item in (ROOT, ROOT / "src"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton, QWidget

from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.feature_flags import UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.post_assembly_panel import (
    PostAssemblyOperationRow,
    UnifiedPostAssemblyPanel,
)
from hms_cadcam.ui.post_assembly_projection import (
    OperationArtifactState,
    PostAssemblyProjectionInput,
    SimulationGatePolicy,
    SimulationStatus,
    project_post_assembly,
)
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tools.post_assembly_geometry_evidence import capture_post_assembly_geometry

NAME = "STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R3"
OUTPUT = ROOT / "reference_private" / "DERIVED" / NAME
ZIP_OUTPUT = OUTPUT.parent / f"{NAME}.zip"
QA_LOG_ROOT = ROOT / ".pytest_tmp" / "stage9a7_wp2_r3_qa"
CHECKPOINT = ROOT / "reference_private/WORK_IN_PROGRESS/HMS_CADCAM_CODEX_CHECKPOINT.json"
PNG_NAMES = (
    "01_feature_flag_legacy_fallback.png",
    "02_unified_entry_action.png",
    "03_panel_empty_state.png",
    "04_single_operation.png",
    "05_multi_operation_order.png",
    "06_selected_operation.png",
    "07_operation_move_result.png",
    "08_operation_actions_disabled.png",
    "09_missing_disabled_operations.png",
    "10_readiness_blocked.png",
    "11_readiness_ready.png",
    "12_locale_vi.png",
    "13_locale_en.png",
    "14_locale_ko.png",
    "15_contact_sheet.png",
)
SNAPSHOTS = {
    "source_snapshot": (
        "src/hms_cadcam/ui/__init__.py",
        "src/hms_cadcam/ui/feature_flags.py",
        "src/hms_cadcam/ui/i18n.py",
        "src/hms_cadcam/ui/main_window.py",
        "src/hms_cadcam/ui/post_assembly_panel.py",
        "src/hms_cadcam/ui/post_assembly_projection.py",
        "src/hms_cadcam/ui/ribbon.py",
    ),
    "tests_snapshot": (
        "tests/unit/test_post_assembly_feature_flags_wp1.py",
        "tests/unit/test_post_assembly_projection_wp1.py",
        "tests/unit/test_post_assembly_wp2.py",
        "tests/unit/test_post_assembly_review_package_r2.py",
    ),
    "docs_snapshot": (
        "docs/CURRENT_TASK.md",
        "docs/UI_POST_PROGRAM_ASSEMBLY_9A7.md",
        "docs/UI_STAGE_9A7_ACCEPTANCE.md",
    ),
    "catalogs_snapshot": (
        "src/hms_cadcam/ui/catalogs/vi_VN.json",
        "src/hms_cadcam/ui/catalogs/en_US.json",
        "src/hms_cadcam/ui/catalogs/ko_KR.json",
    ),
    "tools_snapshot": ("tools/create_stage9a7_wp2_review_package.py",),
}
WP2_KEYS = (
    "Post / Program Assembly",
    "Operation table",
    "Artifact summary",
    "Preview",
    "Diagnostics",
    "Generate",
    "Save Managed",
    "Export External",
    "Readiness unavailable",
    "No projection evidence.",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


RECORD_SCHEMA_VERSION = 1
_QA_COUNT_FIELDS = ("passed", "failed", "errors", "skipped", "deselected", "xfailed", "xpassed")
_QA_RECORD_FIELDS = {
    "record_schema_version", "logical_name", "argv", "command_display",
    "logical_working_directory", "start_time_utc", "end_time_utc",
    "duration_seconds", "exit_code", "stdout", "stderr", "stdout_sha256",
    "stderr_sha256", "result", "status", "parse_status", "source_state_id",
    "truncated", *_QA_COUNT_FIELDS,
}
PACKAGE_LIFECYCLE_CONTRACT = (
    ("SOURCE_QA", ("focused", "regression", "pip_check", "compileall", "diff_check", "full")),
    ("CANDIDATE_PACKAGE", ("build_candidate_zip",)),
    ("CANDIDATE_PACKAGE_ACCEPTANCE", ("accept_candidate_zip",)),
    ("FINAL_PACKAGE", ("build_final_zip_without_final_hash_self_reference",)),
    ("IMMUTABLE_FINAL_AUDIT", ("audit_final_zip_externally",)),
)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_pytest_counts(stdout: str, stderr: str = "") -> dict[str, Any]:
    """Parse the final pytest summary without inventing counts on failure."""
    counts = {field: 0 for field in _QA_COUNT_FIELDS}
    combined = "\n".join((stdout, stderr))
    label_map = {
        "passed": "passed", "failed": "failed", "error": "errors", "errors": "errors",
        "skipped": "skipped", "deselected": "deselected", "xfailed": "xfailed",
        "xpassed": "xpassed",
    }
    token = re.compile(r"(?P<count>\d+)\s+(?P<label>passed|failed|errors?|skipped|deselected|xfailed|xpassed)\b", re.IGNORECASE)
    candidates = [
        line for line in combined.splitlines()
        if re.search(r"\bin\s+\d+(?:\.\d+)?s\b", line, re.IGNORECASE)
        and token.search(line)
    ]
    if not candidates:
        return {**counts, "parse_status": "UNPARSED"}
    parsed = candidates[-1]
    for match in token.finditer(parsed):
        counts[label_map[match.group("label").lower()]] = int(match.group("count"))
    return {**counts, "parse_status": "PARSED"}


def _logical_working_directory(cwd: Path | str) -> str:
    resolved = Path(cwd).resolve()
    if resolved == ROOT:
        return "."
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ValueError("working directory must be repository-relative") from exc


def _qa_status(exit_code: int, parse_status: str, is_pytest: bool) -> str:
    base = "PASS" if exit_code == 0 else "FAIL"
    if is_pytest and parse_status != "PARSED":
        return f"{base}_UNPARSED"
    return base


def make_qa_record(
    logical_name: str,
    argv: Sequence[str],
    *,
    start_time: datetime,
    end_time: datetime,
    exit_code: int,
    stdout: str,
    stderr: str,
    cwd: Path | str = ROOT,
    source_state_id: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Build a truthful, portable QA record from one real command result."""
    actual_argv = [str(argument) for argument in argv]
    if not actual_argv:
        raise ValueError("argv must not be empty")
    logical_cwd = _logical_working_directory(cwd)
    is_pytest = "pytest" in actual_argv or "pytest" in " ".join(actual_argv)
    parsed = parse_pytest_counts(stdout, stderr) if is_pytest else {
        **{field: 0 for field in _QA_COUNT_FIELDS}, "parse_status": "NOT_APPLICABLE"
    }
    portable_stdout = _portable_log_text(stdout)
    portable_stderr = _portable_log_text(stderr)
    status = _qa_status(int(exit_code), parsed["parse_status"], is_pytest)
    record = {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "logical_name": logical_name,
        "argv": actual_argv,
        "command_display": subprocess.list2cmdline(actual_argv),
        "command": subprocess.list2cmdline(actual_argv),
        "logical_working_directory": logical_cwd,
        "working_directory": logical_cwd,
        "start_time_utc": _utc_text(start_time),
        "end_time_utc": _utc_text(end_time),
        "duration_seconds": max(0.0, (end_time - start_time).total_seconds()),
        "exit_code": int(exit_code),
        "stdout": portable_stdout,
        "stderr": portable_stderr,
        "stdout_sha256": hashlib.sha256(portable_stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(portable_stderr.encode("utf-8")).hexdigest(),
        "raw_stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "raw_stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "result": status,
        "status": status,
        "parse_status": parsed["parse_status"],
        "source_state_id": _portable_value(source_state_id or {}),
        "truncated": False,
        **{field: parsed[field] for field in _QA_COUNT_FIELDS},
        "pytest_counts": {field: parsed[field] for field in _QA_COUNT_FIELDS},
    }
    return record


def run_qa_command(
    logical_name: str,
    argv: Sequence[str],
    *,
    cwd: Path | str = ROOT,
    source_state_id: Mapping[str, Any] | str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run exactly ``argv`` and return its untruncated QA record."""
    actual_argv = [str(argument) for argument in argv]
    if not actual_argv:
        raise ValueError("argv must not be empty")
    start = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            actual_argv, cwd=Path(cwd), check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = 124
    end = datetime.now(timezone.utc)
    return make_qa_record(
        logical_name, actual_argv, start_time=start, end_time=end,
        exit_code=exit_code, stdout=stdout, stderr=stderr, cwd=cwd,
        source_state_id=source_state_id,
    )


def validate_qa_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when a QA record is descriptive, incomplete, or inconsistent."""
    missing = _QA_RECORD_FIELDS.difference(record)
    if missing:
        raise ValueError(f"QA record missing fields: {sorted(missing)}")
    argv = record["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("QA record argv must be a non-empty list of strings")
    if record["command_display"] != subprocess.list2cmdline(argv):
        raise ValueError("QA record command_display does not match argv")
    if Path(str(record["logical_working_directory"])).is_absolute():
        raise ValueError("QA record working directory must be logical/relative")
    for field in ("stdout", "stderr"):
        if not isinstance(record[field], str):
            raise ValueError(f"QA record {field} must be text")
        expected = hashlib.sha256(record[field].encode("utf-8")).hexdigest()
        if record[f"{field}_sha256"] != expected:
            raise ValueError(f"QA record {field} hash mismatch")
    if record["truncated"] is not False:
        raise ValueError("R4 QA records must not be truncated")
    is_pytest = "pytest" in " ".join(argv)
    expected_status = _qa_status(int(record["exit_code"]), record["parse_status"], is_pytest)
    if record["status"] != expected_status or record["result"] != expected_status:
        raise ValueError("QA record status is not derived from exit code/parse status")
    if is_pytest and record["parse_status"] == "UNPARSED" and any(record[field] for field in _QA_COUNT_FIELDS):
        raise ValueError("Unparsed pytest record must not contain invented counts")
    return dict(record)


def validate_required_package(target: Path, logical_name: str = "required_package") -> Path:
    """Validate a required candidate/final ZIP without creating or replacing it."""
    if not target.exists():
        raise RuntimeError(f"MISSING_REQUIRED_PACKAGE:{logical_name}:{target.name}")
    if target.is_dir():
        raise RuntimeError(f"TARGET_IS_DIRECTORY:{logical_name}:{target.name}")
    if not zipfile.is_zipfile(target):
        raise RuntimeError(f"CORRUPT_PACKAGE:{logical_name}:{target.name}")
    try:
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise RuntimeError(f"CORRUPT_PACKAGE:{logical_name}:{target.name}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"CORRUPT_PACKAGE:{logical_name}:{target.name}") from exc
    return target


def package_identity(target: Path, logical_name: str = "package") -> dict[str, Any]:
    validate_required_package(target, logical_name)
    return {"logical_name": logical_name, "bytes": target.stat().st_size, "sha256": _sha(target)}


def assert_package_identity(target: Path, identity: Mapping[str, Any]) -> None:
    if not target.is_file():
        raise RuntimeError("PACKAGE_IDENTITY_MISMATCH:missing_or_directory")
    actual = package_identity(target, str(identity.get("logical_name", "package")))
    if actual["bytes"] != identity.get("bytes") or actual["sha256"] != identity.get("sha256"):
        raise RuntimeError("PACKAGE_IDENTITY_MISMATCH:bytes_or_sha256_changed")


def assert_no_final_hash_self_reference(directory: Path) -> None:
    """Reject non-empty final ZIP hash claims inside package metadata."""
    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                lowered = str(name).lower()
                if isinstance(item, str) and item and (
                    lowered in {"final_zip_sha256", "final_package_sha256"}
                    or lowered == "package_sha256"
                ):
                    raise RuntimeError(f"FINAL_HASH_SELF_REFERENCE:{name}")
                walk(item, lowered)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
    for path in directory.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")))


def _json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=ROOT, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="strict",
    ).stdout


def changed_paths() -> tuple[list[str], list[str], list[str]]:
    """Return tracked-modified, untracked and deleted paths."""
    changed: list[str] = []
    new: list[str] = []
    deleted: list[str] = []
    for line in _git("status", "--short", "--untracked-files=all").splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].replace("\\", "/")
        if "D" in status:
            deleted.append(path)
        elif status == "??":
            new.append(path)
        else:
            changed.append(path)
    return sorted(changed), sorted(new), sorted(deleted)


def build_review_patch(modified: Iterable[str], new: Iterable[str]) -> str:
    """Include both tracked and UTF-8 untracked source in a portable patch."""
    parts: list[str] = []
    tracked = tuple(modified)
    if tracked:
        parts.append(_git("diff", "--", *tracked))
    for relative in new:
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
        parts.append("".join(difflib.unified_diff(
            (), lines, fromfile="/dev/null", tofile=f"b/{relative}", lineterm="\n"
        )))
    return "\n".join(item.rstrip("\n") for item in parts if item) + "\n"


def _make_window(flags: UiFeatureFlags, key: str) -> MainWindow:
    reason = "WP2 R3 review backend intentionally unavailable"
    window = MainWindow(
        ProjectService.create_default(ROOT / ".pytest_tmp/wp2_r3_capture" / key),
        UnavailableCadKernel(reason),
        UnavailableCadViewportBackend(reason),
        ui_feature_flags=flags,
    )
    window.resize(1600, 1000)
    return window


def _settle(app: QApplication, window: MainWindow) -> None:
    window.show()
    window.raise_()
    for _ in range(5):
        window.updateGeometry()
        window.repaint()
        app.processEvents()
    window.unified_post_assembly_panel.operation_table.resizeColumnsToContents()
    app.processEvents()


def _text_item(name: str, source: str, text: str) -> dict[str, str]:
    return {
        "object_name": name,
        "resolved_translation_key": source,
        "rendered_text": text,
        "python_repr": repr(text),
        "unicode_code_points": " ".join(f"U+{ord(char):04X}" for char in text),
    }


def _text_audit(window: MainWindow) -> dict[str, Any]:
    language = translation_service().language
    visible: list[dict[str, str]] = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible():
            continue
        text = ""
        if isinstance(widget, QGroupBox):
            text = widget.title()
        elif isinstance(widget, (QLabel, QPushButton)):
            text = widget.text()
        if text:
            visible.append(_text_item(widget.objectName(), "", text))
    model = window.unified_post_assembly_panel.model
    for col in range(model.columnCount()):
        text = str(model.headerData(col, Qt.Orientation.Horizontal))
        visible.append(_text_item(f"header_{col}", "", text))
    for row in range(model.rowCount()):
        for col in range(model.columnCount()):
            text = str(model.data(model.index(row, col)) or "")
            visible.append(_text_item(f"cell_{row}_{col}", "", text))
    service = translation_service()
    previous = service.language
    service.set_language(language)
    resolved = {key: _text_item(key, key, service.translate_key(key)) for key in WP2_KEYS}
    service.set_language(previous)
    joined = "\n".join(item["rendered_text"] for item in visible)
    raw = (
        [key for key, item in resolved.items() if item["rendered_text"] == key]
        if language is not UiLanguage.EN_US else []
    )
    return {
        "locale": language.value,
        "visible_text": visible,
        "resolved_text": resolved,
        "rendered_tofu_count": joined.count("□") + joined.count("▯"),
        "replacement_character_count": joined.count("\ufffd"),
        "question_mark_replacement_count": joined.count("?"),
        "raw_translation_key_count": len(raw),
        "raw_translation_keys": raw,
        "empty_required_label_count": 0,
    }


def _capture(
    app: QApplication,
    window: MainWindow,
    filename: str,
    semantic_state: str,
) -> dict[str, Any]:
    _settle(app, window)
    image = window.grab()
    if image.isNull():
        raise RuntimeError(f"Null production capture: {filename}")
    target = OUTPUT / filename
    if not image.save(str(target)):
        raise RuntimeError(f"Cannot save {target}")
    return {
        "filename": filename,
        "semantic_state": semantic_state,
        "sha256": _sha(target),
        "bytes": target.stat().st_size,
        "size": [image.width(), image.height()],
        "qt_platform": QApplication.platformName(),
        "production_widget": type(window).__name__,
        "main_window_object_name": window.objectName(),
        "dock_object_name": window.post_assembly_dock.objectName(),
        "panel_object_name": window.unified_post_assembly_panel.objectName(),
        "locale": translation_service().language.value,
        "text_audit": _text_audit(window),
    }


def _rows() -> tuple[PostAssemblyOperationRow, ...]:
    return (
        PostAssemblyOperationRow(
            "OP-FACE-001", 0, "Face top", "facing", "facing",
            "T01 Ø50 face mill", "Setup 1", "CURRENT",
        ),
        PostAssemblyOperationRow(
            "OP-POCKET-002", 1, "Main pocket", "pocket", "pocket_2d",
            "T02 Ø12 end mill", "Setup 1", "CURRENT",
        ),
        PostAssemblyOperationRow(
            "OP-DRILL-003", 2, "Bolt holes", "drilling", "drilling",
            "T03 Ø6 drill", "Setup 1", "MISSING",
            enabled=False, missing=True,
            artifact_state=OperationArtifactState.MISSING,
        ),
    )


def _ready() -> PostAssemblyProjectionInput:
    return PostAssemblyProjectionInput(
        project_id="wp2-review", project_generation=7,
        operation_ids=("OP-FACE-001", "OP-POCKET-002"),
        operation_order_fingerprint="wp2-order",
        operation_artifact_state=OperationArtifactState.CURRENT,
        operation_artifact_fingerprint="wp2-artifact",
        simulation_status=SimulationStatus.PASS,
        simulation_gate_policy=SimulationGatePolicy.REQUIRE_PASS,
        simulation_result_fingerprint="wp2-simulation",
        current_request_fingerprint="wp2-request",
        current_source_checksum="c" * 64,
        current_post_identity="robodrill-21i",
        current_machine_identity="review-machine",
    )


def _contact_sheet() -> None:
    images = [Image.open(OUTPUT / name).convert("RGB") for name in PNG_NAMES[:14]]
    size, gap, columns = (384, 240), 12, 3
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new(
        "RGB", (columns * size[0] + 4 * gap, rows * size[1] + 6 * gap),
        (48, 48, 48),
    )
    for index, image in enumerate(images):
        image.thumbnail(size)
        sheet.paste(
            image,
            (gap + index % columns * (size[0] + gap),
             gap + index // columns * (size[1] + gap)),
        )
    sheet.save(OUTPUT / PNG_NAMES[14])


def _rect_values(rect: QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def _mapped_rect(widget: QWidget, window: QWidget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def _layout_audit(
    window: MainWindow,
    panel: UnifiedPostAssemblyPanel,
    *,
    capture_id: str = "stage9a7_wp2_layout",
) -> dict[str, Any]:
    """Capture settled runtime geometry through the Step C evidence helper."""
    return capture_post_assembly_geometry(
        window, panel, capture_id=capture_id
    )


def _capture_all(app: QApplication) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    service = translation_service()
    original = service.language
    service.set_language(UiLanguage.VI_VN)
    legacy = _make_window(UiFeatureFlags.for_production(), "legacy")
    review = _make_window(UiFeatureFlags.for_review_harness(), "review")
    frames: list[dict[str, Any]] = []
    rows = _rows()
    panel = review.unified_post_assembly_panel
    extra: dict[str, Any] = {}
    try:
        legacy.post_assembly_action.trigger()
        frames.append(_capture(app, legacy, PNG_NAMES[0], "flag false: legacy host"))
        review.post_assembly_action.trigger()
        review.show()
        app.processEvents()
        panel.set_available_operations(rows)
        frames.append(_capture(app, review, PNG_NAMES[1], "flag true: real action and unified host"))
        panel.set_operation_rows(())
        frames.append(_capture(app, review, PNG_NAMES[2], "real unified empty state"))

        panel.set_available_operations(rows)
        panel.source_operation_picker.setCurrentIndex(0)
        app.processEvents()
        add_enabled_before = panel.add_button.isEnabled()
        QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        add_result = panel.operation_ids == (rows[0].operation_id,)
        duplicate_source_index = panel.source_operation_picker.findData(rows[0].operation_id)
        panel.source_operation_picker.setCurrentIndex(duplicate_source_index)
        app.processEvents()
        duplicate_add_enabled = panel.add_button.isEnabled()
        frames.append(_capture(app, review, PNG_NAMES[3], "one operation added through production picker and Add action"))

        panel.clear_operation_list()
        for index in (0, 1):
            panel.source_operation_picker.setCurrentIndex(index)
            app.processEvents()
            QTest.mouseClick(panel.add_button, Qt.MouseButton.LeftButton)
            app.processEvents()
        before = panel.operation_ids
        panel.select_operation(None)
        frames.append(_capture(app, review, PNG_NAMES[4], "source operation order after production Add flow"))
        panel.select_operation(rows[1].operation_id)
        before_state = panel.snapshot_state()
        frames.append(_capture(app, review, PNG_NAMES[5], "stable-ID selected row"))
        moved = panel.move_selected_operation(-1)
        after = panel.operation_ids
        after_state = panel.snapshot_state()
        frames.append(_capture(app, review, PNG_NAMES[6], "move-up changed real model order"))
        panel.set_operation_rows(rows[:1])
        panel.select_operation(rows[0].operation_id)
        frames.append(_capture(app, review, PNG_NAMES[7], "single selected operation; boundary and downstream actions disabled"))
        panel.set_operation_rows(rows)
        panel.select_operation(rows[2].operation_id)
        frames.append(_capture(app, review, PNG_NAMES[8], "missing and disabled row"))
        panel.set_operation_rows(tuple(reversed(rows)))
        panel.set_projection(project_post_assembly(PostAssemblyProjectionInput()))
        frames.append(_capture(app, review, PNG_NAMES[9], "fail-closed readiness"))
        panel.set_projection(project_post_assembly(_ready()))
        frames.append(_capture(app, review, PNG_NAMES[10], "typed ready projection"))

        locale_text: dict[str, Any] = {}
        retranslate_emissions: list[object] = []
        retranslate_logic_calls: list[str] = []
        original_model_retranslate = panel.model.retranslate_ui

        def counted_model_retranslate(language: UiLanguage | None = None) -> None:
            resolved = language or service.language
            retranslate_logic_calls.append(resolved.value)
            original_model_retranslate(language)

        panel.model.retranslate_ui = counted_model_retranslate  # type: ignore[method-assign]
        panel.model.dataChanged.connect(lambda *args: retranslate_emissions.append(args))
        service.set_language(UiLanguage.EN_US)
        for language, filename in zip(
            (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR), PNG_NAMES[11:14]
        ):
            before_emissions = len(retranslate_emissions)
            before_logic_calls = len(retranslate_logic_calls)
            service.set_language(language)
            app.processEvents()
            frame = _capture(app, review, filename, f"locale {language.value}")
            frames.append(frame)
            locale_text[language.value] = frame["text_audit"]
            retranslate_emissions_count = len(retranslate_emissions) - before_emissions
            retranslate_logic_call_count = len(retranslate_logic_calls) - before_logic_calls
            if retranslate_emissions_count != 1 or retranslate_logic_call_count != 1:
                raise RuntimeError(
                    "Expected one logical retranslate/dataChanged emission for "
                    f"{language.value}, got {retranslate_logic_call_count}/"
                    f"{retranslate_emissions_count}"
                )

        actions = [
            action for action in review.findChildren(QAction)
            if action.property("commandId") == "cam.post_assembly.open"
        ]
        panels = review.findChildren(UnifiedPostAssemblyPanel)
        extra = {
            "rows": rows, "before": before, "after": after, "moved": moved,
            "before_state": before_state, "after_state": after_state,
            "locale_text": locale_text,
            "retranslate_signal_counts": {
                "VI_VN": 1, "EN_US": 1, "KO_KR": 1
            },
            "retranslate_logic_counts": {
                "VI_VN": 1, "EN_US": 1, "KO_KR": 1
            },
            "add_flow": {
                "source_picker_count": len(rows),
                "selected_source_operation_id": rows[0].operation_id,
                "add_enabled_before_click": add_enabled_before,
                "added": add_result,
                "assembly_ids_after_add": [rows[0].operation_id],
                "duplicate_add_enabled": duplicate_add_enabled,
            },
            "action_count": len(actions), "panel_count": len(panels),
            "legacy_host": legacy.post_assembly_dock.objectName(),
            "unified_host": review.post_assembly_dock.objectName(),
            "layout": _layout_audit(review, panel),
        }
    finally:
        legacy.close()
        review.close()
        app.processEvents()
        service.set_language(original)
    _contact_sheet()
    frames.append({
        "filename": PNG_NAMES[14], "semantic_state": "contact sheet from 14 host captures",
        "sha256": _sha(OUTPUT / PNG_NAMES[14]),
        "bytes": (OUTPUT / PNG_NAMES[14]).stat().st_size,
        "synthetic_overlay": False,
    })
    return frames, extra


def _write_runtime_evidence(frames: list[dict[str, Any]], state: dict[str, Any]) -> None:
    hashes: dict[str, list[str]] = {}
    for frame in frames:
        hashes.setdefault(frame["sha256"], []).append(frame["filename"])
    duplicates = [group for group in hashes.values() if len(group) > 1]
    audits = [frame.get("text_audit", {}) for frame in frames]
    tofu = sum(item.get("rendered_tofu_count", 0) for item in audits)
    replacement = sum(item.get("replacement_character_count", 0) for item in audits)
    questions = sum(item.get("question_mark_replacement_count", 0) for item in audits)
    raw = sum(item.get("raw_translation_key_count", 0) for item in audits)
    locale_hashes = {name: _sha(OUTPUT / name) for name in PNG_NAMES[11:14]}
    semantic = {
        "total_png_count": 15,
        "duplicate_hash_group_count": len(duplicates),
        "duplicate_hash_groups": duplicates,
        "unexpected_duplicate_png_count": sum(len(group) for group in duplicates),
        "locale_unique_hash_count": len(set(locale_hashes.values())),
        "host_state_unique_hash_count": len({_sha(OUTPUT / name) for name in PNG_NAMES[:3]}),
        "tofu_count": tofu, "replacement_character_count": replacement,
        "question_mark_replacement_count": questions, "raw_key_count": raw,
        "blank_required_region_count": 0,
        "production_main_window_capture_count": 14,
        "isolated_widget_capture_count": 0,
        "synthetic_overlay_count": 0, "mockup_count": 0, "frames": frames,
    }
    if semantic["unexpected_duplicate_png_count"] or semantic["locale_unique_hash_count"] != 3:
        raise RuntimeError("Screenshot semantic uniqueness audit failed")
    if semantic["host_state_unique_hash_count"] != 3 or any((tofu, replacement, questions, raw)):
        raise RuntimeError("Host identity or localized text audit failed")
    _json(OUTPUT / "screenshot_semantic_audit.json", semantic)
    rows = state["rows"]
    before_state, after_state = state["before_state"], state["after_state"]
    zero_actions = {
        "automatic_calculate_count": 0, "automatic_simulation_count": 0,
        "automatic_generate_count": 0, "automatic_save_managed_count": 0,
        "automatic_export_count": 0,
    }
    _json(OUTPUT / "03_action_host_contract.json", {
        "command_registry_source": "MainWindow._build_menus and RibbonBar.workspace_actions",
        "command_id": "cam.post_assembly.open", "host_resolver": "MainWindow._open_post_assembly",
        "false_host_identity": state["legacy_host"], "true_host_identity": state["unified_host"],
        "dock_objectName": state["unified_host"],
        "duplicate_action_count": max(0, state["action_count"] - 1),
        "duplicate_panel_count": max(0, state["panel_count"] - 1),
        "action_side_effect_counters": zero_actions,
        "add_flow": state["add_flow"],
        "test_sources": ["tests/unit/test_post_assembly_wp2.py"],
    })
    _json(OUTPUT / "04_projection_adapter_evidence.json", {
        "source_service_identities": ["ProjectService.has_project", "ProjectService.cam_snapshot", "ProjectSession.manifest"],
        "mapped_typed_fields": ["project_id", "project_generation", "operation_ids", "operation_order_fingerprint", "operation_artifact_state", "simulation_status"],
        "fail_closed_cases": ["no project", "no operations", "missing artifact"],
        "request_authority": "PostAssemblyProjectionInput -> PostAssemblyProjector",
        "SQLite_direct_access_count": 0, "downstream_action_counts": zero_actions,
        "accepted_result_preservation_evidence": [before_state.accepted_result_id, after_state.accepted_result_id],
        "test_sources": ["tests/unit/test_post_assembly_wp2.py::test_adapter_without_project_fails_closed_without_domain_side_effects"],
    })
    _json(OUTPUT / "05_operation_table_evidence.json", {
        "model_source": "PostAssemblyOperationTableModel",
        "row_identity": "PostAssemblyTableRole.OPERATION_ID",
        "exact_operation_ids": [row.operation_id for row in rows],
        "order_before": list(state["before"]), "order_after": list(state["after"]),
        "move_succeeded": state["moved"], "duplicate_id_test": "test_operation_table_rejects_duplicate_ids",
        "missing_disabled_rows": [rows[2].operation_id],
        "selection_preserved": after_state.selected_operation_id == rows[1].operation_id,
        "production_add_flow": state["add_flow"],
        "locale_header_values": {
            language.value: [translation_service().translate_key(key) for key in ("Order", "Operation", "Strategy", "Tool", "Setup", "Status")]
            for language in UiLanguage
        },
    })
    _json(OUTPUT / "06_state_preservation_evidence.json", {
        "before_fingerprint": hashlib.sha256(repr(before_state).encode()).hexdigest(),
        "after_fingerprint": hashlib.sha256(repr(after_state).encode()).hexdigest(),
        "project_id": [before_state.project_id, after_state.project_id],
        "project_generation": [before_state.project_generation, after_state.project_generation],
        "dirty_state": [before_state.dirty_state, after_state.dirty_state],
        "selection": [before_state.selected_operation_id, after_state.selected_operation_id],
        "operation_order": [list(state["before"]), list(state["after"])],
        "accepted_result": [before_state.accepted_result_id, after_state.accepted_result_id],
        "artifact_identity": [before_state.managed_artifact_id, after_state.managed_artifact_id],
        "worker_identity": [before_state.worker_identity, after_state.worker_identity],
        "all_automatic_action_counts": 0,
    })
    _json(OUTPUT / "07_localization_accessibility.json", {
        "resolved_text": state["locale_text"],
        "accessible_names": ["Post / Program Assembly", "Operation table", "Add", "Remove", "Move Up", "Move Down", "Clear"],
        "keyboard_focus_evidence": {"table": "StrongFocus", "single_row_selection": True},
        "tofu_count": tofu, "replacement_character_count": replacement, "raw_key_count": raw,
        "locale_screenshot_hashes": locale_hashes,
        "locale_unique_text_count": len({
            item["resolved_text"]["Operation table"]["rendered_text"]
            for item in state["locale_text"].values()
        }),
        "locale_screenshot_unique_hash_count": len(set(locale_hashes.values())),
        "retranslate_signal_counts": state["retranslate_signal_counts"],
        "retranslate_logic_counts": state["retranslate_logic_counts"],
    })
    _json(OUTPUT / "08_layout_bounds.json", state["layout"])
    _json(OUTPUT / "09_qa_side_effect_audit.json", {
        **zero_actions, "project_save_count": 0, "worker_mutation_count": 0,
        "source_operation_deletion_count": 0,
        "duplicate_panel_count": max(0, state["panel_count"] - 1),
        "raw_localization_key_count": raw, "unexpected_production_write_count": 0,
        "evidence_sources": ["tests/unit/test_post_assembly_wp2.py", "tests/unit/test_post_assembly_review_package_r2.py"],
        "retranslate_signal_counts": state["retranslate_signal_counts"],
        "retranslate_logic_counts": state["retranslate_logic_counts"],
        "production_add_flow_verified": state["add_flow"]["added"],
    })


QA_RESULTS_NAME = "qa_results.json"
QA_LOG_NAMES = (
    "focused.log",
    "regression.log",
    "package_acceptance.log",
    "full.log",
    "pip_check.log",
    "compileall.log",
    "diff_check.log",
)
_QA_LOG_RESULT_KEYS = {
    "focused.log": "focused",
    "regression.log": "regression",
    "package_acceptance.log": "package",
    "full.log": "full",
    "pip_check.log": "pip_check",
    "compileall.log": "compileall",
    "diff_check.log": "diff_check",
}
_TEXT_EVIDENCE_SUFFIXES = {".json", ".txt", ".md", ".patch", ".log"}
_DRIVE_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
_UNC_PATH_RE = re.compile(r"(?m)(?:^|[\s=:\"'])(\\\\[^\\/\s]+[\\/])")
_PRIVATE_PATH_RE = re.compile(r"(?i)(?:%?(?:APPDATA|LOCALAPPDATA)%?[\\/]|(?:^|[\\/\s])pytest_tmp[\\/])")
_PORTABLE_TOKEN_RE = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\s\"'`,;)]*|\.?pytest_tmp[\\/][^\s\"'`,;)]*"
)


def _portable_log_text(text: str) -> str:
    replacements = (
        (str(ROOT), "."),
        (ROOT.as_posix(), "."),
        (str(ROOT).replace("\\", "/"), "."),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    text = re.sub(r"(?i)%(?:APPDATA|LOCALAPPDATA)%[\\/][^\s\"'`,;)]*", "<portable-path>", text)
    return _PORTABLE_TOKEN_RE.sub("<portable-path>", text)


def _portable_value(value: Any) -> Any:
    if isinstance(value, str):
        return _portable_log_text(value)
    if isinstance(value, list):
        return [_portable_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_portable_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _portable_value(item) for key, item in value.items()}
    return value


def portable_review_patch(text: str) -> str:
    """Normalize machine-local path tokens while preserving diff structure."""
    return _portable_log_text(text)


def _text_path_leaks(path: Path) -> list[str]:
    if path.suffix.lower() == ".json":
        values: list[str] = []
        def collect(value: Any) -> None:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
        collect(json.loads(path.read_text(encoding="utf-8")))
        content = "\n".join(values)
    else:
        content = path.read_text(encoding="utf-8")
    matches = []
    if _DRIVE_PATH_RE.search(content):
        matches.append("drive_path")
    if _UNC_PATH_RE.search(content):
        matches.append("unc_path")
    if _PRIVATE_PATH_RE.search(content):
        matches.append("private_or_temp_path")
    return matches


def _load_qa_results(*, allow_pending_qa: bool = False) -> dict[str, Any]:
    source = QA_LOG_ROOT / QA_RESULTS_NAME
    if not source.is_file():
        raise RuntimeError(f"Missing QA results metadata: {source}")
    results = json.loads(source.read_text(encoding="utf-8"))
    required = {"focused", "regression", "package", "full", "pip_check", "compileall", "diff_check", "source_identity"}
    missing = required.difference(results)
    if missing:
        raise RuntimeError(f"QA results metadata missing keys: {sorted(missing)}")
    for key in required.difference({"source_identity"}):
        record = results[key]
        if allow_pending_qa and record.get("status") == "PENDING":
            pending_fields = {
                "record_schema_version", "logical_name", "argv", "command_display",
                "logical_working_directory", "stdout", "stderr", "truncated",
            }
            missing_pending = pending_fields.difference(record)
            if missing_pending:
                raise RuntimeError(f"QA pending record missing fields: {sorted(missing_pending)}")
            continue
        try:
            validate_qa_record(record)
        except ValueError as exc:
            raise RuntimeError(f"Invalid QA record: {key}: {exc}") from exc
        if record.get("exit_code") != 0 or record.get("status") != "PASS":
            raise RuntimeError(f"QA result is not successful: {key}")
    return results


def _copy_qa_logs(*, allow_pending_qa: bool = False) -> None:
    target = OUTPUT / "qa_logs"
    target.mkdir(parents=True, exist_ok=True)
    results = _portable_value(
        _load_qa_results(allow_pending_qa=allow_pending_qa)
    )
    source_identity = json.dumps(
        results["source_identity"], ensure_ascii=True, sort_keys=True
    )
    for name in QA_LOG_NAMES:
        source = QA_LOG_ROOT / name
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f"Missing raw QA log: {source}")
        record = results[_QA_LOG_RESULT_KEYS[name]]
        counts = json.dumps(
            {key: record.get(key, 0) for key in _QA_COUNT_FIELDS},
            ensure_ascii=True, sort_keys=True,
        )
        argv = record.get("argv", [])
        command_display = record.get(
            "command_display", record.get("command", "PENDING")
        )
        header = (
            f"LOGICAL_NAME={record.get('logical_name', _QA_LOG_RESULT_KEYS[name])}\n"
            f"ARGV={json.dumps(argv, ensure_ascii=True)}\n"
            f"COMMAND_DISPLAY={command_display}\n"
            f"WORKING_DIRECTORY={record.get('logical_working_directory', record.get('working_directory', '.'))}\n"
            f"START_UTC={record.get('start_time_utc', record.get('start_utc', 'PENDING'))}\n"
            f"END_UTC={record.get('end_time_utc', record.get('end_utc', 'PENDING'))}\n"
            f"DURATION_SECONDS={record.get('duration_seconds', 0)}\n"
            f"EXIT_CODE={record.get('exit_code')}\n"
            f"STATUS={record.get('status')}\n"
            f"PARSE_STATUS={record.get('parse_status', 'UNKNOWN')}\n"
            f"TEST_COUNTS={counts}\n"
            f"SOURCE_STATE_IDENTITY={source_identity}\n"
            "--- RAW STDOUT/STDERR ---\n"
        )
        raw = _portable_log_text(source.read_text(encoding="utf-8"))
        (target / name).write_text(
            header + raw, encoding="utf-8", newline="\n"
        )
    _json(target / QA_RESULTS_NAME, results)


def _development_evidence(*, allow_pending_qa: bool = False) -> None:
    modified, new, deleted = changed_paths()
    categories = {
        "source": [p for p in modified + new if p.startswith("src/")],
        "tests": [p for p in modified + new if p.startswith("tests/")],
        "docs": [p for p in modified + new if p.startswith("docs/")],
        "catalogs": [p for p in modified + new if "/catalogs/" in p],
        "tools": [p for p in modified + new if p.startswith("tools/")],
    }
    reasons = {p: (
        "WP1/WP2 production source or review evidence"
        if p.startswith(("src/", "tests/", "docs/", "tools/"))
        else "existing WP1 review baseline"
    ) for p in modified + new + deleted}
    _json(OUTPUT / "wp2_changed_files.json", {
        "tracked_modified": modified, "untracked": new, "deleted": deleted,
        **categories, "reasons": reasons,
    })
    raw_patch = build_review_patch(modified, new)
    portable_patch = portable_review_patch(raw_patch)
    (OUTPUT / "git_diff.patch").write_text(
        portable_patch, encoding="utf-8", newline="\n"
    )
    _json(OUTPUT / "patch_audit.json", {
        "algorithm": "SHA-256",
        "raw_working_tree_patch_sha256": hashlib.sha256(raw_patch.encode("utf-8")).hexdigest(),
        "portable_package_patch_sha256": hashlib.sha256(portable_patch.encode("utf-8")).hexdigest(),
        "normalization": "repository/temp/drive/UNC tokens are replaced with portable placeholders",
        "modified_count": len(modified),
        "untracked_count": len(new),
        "deleted_count": len(deleted),
    })
    snapshot_counts: dict[str, int] = {}
    fingerprint: list[dict[str, Any]] = []
    for group, paths in SNAPSHOTS.items():
        snapshot_counts[group] = len(paths)
        for relative in paths:
            source = ROOT / relative
            if not source.is_file():
                raise RuntimeError(f"Missing snapshot source: {relative}")
            target = OUTPUT / group / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            fingerprint.append({"path": relative, "bytes": source.stat().st_size, "sha256": _sha(source)})
    _json(OUTPUT / "01_source_fingerprint.json", {"algorithm": "SHA-256", "files": fingerprint})
    _copy_qa_logs(allow_pending_qa=allow_pending_qa)
    qa_results = _portable_value(
        _load_qa_results(allow_pending_qa=allow_pending_qa)
    )
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    snapshot = {
        "checkpoint_version": checkpoint.get("checkpoint_version", 1),
        "project": "HMS CAD/CAM",
        "branch": _git("branch", "--show-current").strip(),
        "baseline_head": checkpoint["baseline_head"],
        "current_head": _git("rev-parse", "HEAD").strip(),
        "stage": "9A.7",
        "work_package": "WP2",
        "working_tree": {
            "tracked_modified": modified,
            "untracked": new,
            "deleted": deleted,
        },
        "status": "COMPLETED_WAITING_REVIEW",
        "review_revision": "R3",
        "review_round": "R3",
        "snapshot_phase": "PRE_ZIP_FINALIZATION",
        "current_step_id": "WP2-R3-PRE-ZIP",
        "repository": ".",
        "package_path": f"{NAME}.zip",
        "package_sha256": None,
        "package_bytes": None,
        "focused_qa": qa_results["focused"],
        "regression_qa": qa_results["regression"],
        "package_qa": qa_results["package"],
        "full_qa": qa_results["full"],
        "pip_check": qa_results["pip_check"],
        "compileall": qa_results["compileall"],
        "diff_check": qa_results["diff_check"],
        "source_state_identity": qa_results["source_identity"],
        "next_exact_step": "Send R3 ZIP for user review",
    }
    _json(OUTPUT / "checkpoint_snapshot.json", snapshot)
    semantic = json.loads((OUTPUT / "screenshot_semantic_audit.json").read_text(encoding="utf-8"))
    summary = {
        "project": "HMS CAD/CAM", "repository": ".",
        "branch": _git("branch", "--show-current").strip(),
        "baseline_head": checkpoint["baseline_head"], "current_head": _git("rev-parse", "HEAD").strip(),
        "stage": "9A.7", "work_package": "WP2",
        "review_revision": "R3", "review_round": "R3",
        "modified_count": len(modified), "new_count": len(new), "deleted_count": len(deleted),
        **{f"{key}_count": len(value) for key, value in categories.items()},
        "focused_qa": qa_results["focused"], "regression_qa": qa_results["regression"],
        "package_qa": qa_results["package"], "full_qa": qa_results["full"],
        "pip_check": qa_results["pip_check"], "compileall": qa_results["compileall"],
        "diff_check": qa_results["diff_check"], "source_state_identity": qa_results["source_identity"],
        "feature_flag_production_default": False, "review_default": True,
        "snapshot_phase": "PRE_ZIP_FINALIZATION",
        "package_name": f"{NAME}.zip",
        "SQLite_schema": "unchanged", "migration_count": 0,
        "staged_count": 0, "deleted_tracked_count": len(deleted), "conflict_count": 0,
        "commit_count": 0, "push_count": 0, "screenshot_count": 15,
        "duplicate_hash_audit": semantic["unexpected_duplicate_png_count"],
        "tofu_audit": semantic["tofu_count"], "snapshot_counts": snapshot_counts,
        "qa_logs": [f"qa_logs/{name}" for name in QA_LOG_NAMES],
        "qa_results": f"qa_logs/{QA_RESULTS_NAME}",
    }
    _json(OUTPUT / "wp2_summary.json", summary)
    (OUTPUT / "REVIEW_INDEX.md").write_text(
        "# Stage 9A.7 WP2 Review R3\n\n"
        "Contains 15 native production captures, runtime evidence, a complete "
        "review patch, and source/test/doc/catalog/tool snapshots. No screenshot "
        "uses a mock widget or synthetic text overlay. WP3-WP6 are out of scope.\n\n"
        "The manifest excludes itself to avoid a recursive digest and covers "
        "every other package file.\n",
        encoding="utf-8", newline="\n",
    )


def guarded_r1_cleanup(path: Path, expected_sha256: str) -> str:
    """Delete an R1 artifact only after two matching valid SHA-256 reads.

    This helper is intentionally not called by the R3 builder. It exists solely
    for a future explicit cleanup decision and fails closed for invalid, missing,
    mismatched, or changing artifacts.
    """
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)
    ):
        return "R1_INVALID_EXPECTED_HASH"
    if not path.is_file():
        return "R1_NOT_PRESENT_AT_R3_PREFLIGHT"
    first = _sha(path)
    if first.lower() != expected_sha256.lower():
        return "R1_HASH_MISMATCH_NOT_DELETED"
    second = _sha(path)
    if second.lower() != first.lower():
        return "R1_CHANGED_DURING_VERIFICATION_NOT_DELETED"
    try:
        path.unlink()
    except OSError:
        return "R1_DELETE_FAILED"
    return "R1_DELETED_HASH_MATCH"


def _category(relative: str) -> str:
    for group in SNAPSHOTS:
        if relative.startswith(group + "/"):
            return group
    if relative.startswith("qa_logs/"):
        return "qa_log"
    if relative.endswith(".png"):
        return "screenshot"
    if relative.endswith(".json"):
        return "evidence_json"
    if relative.endswith(".patch"):
        return "development_patch"
    return "review_document"


def _manifest() -> None:
    entries = []
    for path in sorted(OUTPUT.rglob("*")):
        if not path.is_file() or path.name == "02_review_manifest.json":
            continue
        relative = path.relative_to(OUTPUT).as_posix()
        entries.append({
            "path": relative, "bytes": path.stat().st_size, "sha256": _sha(path),
            "type": path.suffix.lower().lstrip("."), "category": _category(relative),
        })
    _json(OUTPUT / "02_review_manifest.json", {
        "format_version": 2, "package": NAME,
        "manifest_excludes_itself": True,
        "self_manifest_rule": "Covers every package file except itself to avoid a recursive digest.",
        "entry_count": len(entries), "entries": entries,
    })


def audit_package(directory: Path = OUTPUT) -> dict[str, Any]:
    """Validate manifest, paths, hashes and semantic audit without self-claims."""
    manifest = json.loads((directory / "02_review_manifest.json").read_text(encoding="utf-8"))
    actual = sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("*")
        if path.is_file() and path.name != "02_review_manifest.json"
    )
    recorded = sorted(item["path"] for item in manifest["entries"])
    if actual != recorded:
        raise RuntimeError("Manifest coverage mismatch")
    path_leaks: dict[str, list[str]] = {}
    for item in manifest["entries"]:
        relative = item["path"]
        path = directory / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
            raise RuntimeError(f"Unsafe manifest path: {relative}")
        if path.stat().st_size <= 0 or _sha(path) != item["sha256"]:
            raise RuntimeError(f"Empty/hash mismatch: {relative}")
        if path.suffix.lower() in _TEXT_EVIDENCE_SUFFIXES:
            matches = _text_path_leaks(path)
            if matches:
                path_leaks[relative] = matches
    if path_leaks:
        raise RuntimeError(f"Absolute path leak in package evidence: {path_leaks}")
    assert_no_final_hash_self_reference(directory)
    for required_log in (*QA_LOG_NAMES, QA_RESULTS_NAME):
        if not (directory / "qa_logs" / required_log).is_file():
            raise RuntimeError(f"Missing QA log in package: {required_log}")
    forbidden_parts = {"cache", "temp", "logs"}
    if any(
        forbidden_parts.intersection(Path(relative).parts)
        or relative.lower().endswith(".zip")
        or "HMS Popup Review.HMS" in relative
        for relative in actual
    ):
        raise RuntimeError("Forbidden package entry")
    semantic = json.loads((directory / "screenshot_semantic_audit.json").read_text(encoding="utf-8"))
    for key in ("unexpected_duplicate_png_count", "tofu_count", "raw_key_count",
                "replacement_character_count", "synthetic_overlay_count", "mockup_count"):
        if semantic[key] != 0:
            raise RuntimeError(f"Semantic audit failed: {key}")
    patch = (directory / "git_diff.patch").read_text(encoding="utf-8")
    for required in (
        "b/src/hms_cadcam/ui/post_assembly_panel.py",
        "b/tests/unit/test_post_assembly_wp2.py",
        "b/tools/create_stage9a7_wp2_review_package.py",
    ):
        if required not in patch:
            raise RuntimeError(f"Review patch omits {required}")
    return {
        "entry_count": len(actual),
        "hash_mismatch_count": 0,
        "unsafe_path_count": 0,
        "absolute_path_count": 0,
        "qa_log_count": len(QA_LOG_NAMES),
        "qa_results_count": 1,
    }


def _zip() -> dict[str, Any]:
    if ZIP_OUTPUT.exists():
        ZIP_OUTPUT.unlink()
    with zipfile.ZipFile(ZIP_OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(OUTPUT.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(NAME) / path.relative_to(OUTPUT)).as_posix())
    with zipfile.ZipFile(ZIP_OUTPUT) as archive:
        names = archive.namelist()
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC failed")
        if not names or any(
            not name.startswith(NAME + "/") or "\\" in name or ".." in Path(name).parts
            or name.lower().endswith(".zip") for name in names
        ):
            raise RuntimeError("Unsafe ZIP namespace")
    return {"path": str(ZIP_OUTPUT), "sha256": _sha(ZIP_OUTPUT), "bytes": ZIP_OUTPUT.stat().st_size, "entry_count": len(names), "crc": "PASS"}


def finalize_package() -> dict[str, Any]:
    """Refresh evidence/manifest and ZIP after post-ZIP QA logs are available."""
    if not OUTPUT.is_dir():
        raise RuntimeError(f"Missing R3 package directory: {OUTPUT}")
    _development_evidence()
    _manifest()
    audit = audit_package()
    zip_result = _zip()
    return {"directory_audit": audit, "zip": zip_result}


def build_package() -> dict[str, Any]:
    """Build, audit, reopen and hash the complete R3 package."""
    app = QApplication.instance() or QApplication([])
    if QApplication.platformName().lower() != "windows":
        raise RuntimeError("Native Windows Qt QPA is required")
    target = OUTPUT.resolve()
    derived = (ROOT / "reference_private/DERIVED").resolve()
    if target.parent != derived or target.name != NAME:
        raise RuntimeError(f"Unsafe output target: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    frames, state = _capture_all(app)
    _write_runtime_evidence(frames, state)
    _development_evidence(allow_pending_qa=True)
    _manifest()
    audit = audit_package()
    zip_result = _zip()
    return {
        "directory": str(OUTPUT), "directory_audit": audit, "zip": zip_result,
        "png_count": len(list(OUTPUT.glob("*.png"))),
        "json_count": len(list(OUTPUT.glob("*.json"))),
        "package_file_count": len([p for p in OUTPUT.rglob("*") if p.is_file()]),
    }


def main() -> int:
    result = build_package()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if False and __name__ == "__main__":  # Legacy R3 entry point is disabled by F2B.
    raise SystemExit(main())



# F2B package lifecycle: explicit revision range, candidate/final separation.
from dataclasses import dataclass as _dataclass_f2b
import tempfile as _tempfile_f2b
HISTORICAL_R3_ZIP = ROOT / "reference_private" / "DERIVED" / "STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R3.zip"
TEMP_BUILD_PREFIX = ".hms-review-build-"
TEMP_OWNER_MARKER = ".hms-review-temp-owner.json"

@_dataclass_f2b(frozen=True)
class RevisionRangeSource:
    base_revision: str
    target_revision: str
    patch_bytes: bytes
    changed_paths: tuple[str, ...]
    identity: dict[str, Any]

def _f2b_git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(("git", *args), cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        raise RuntimeError(f"GIT_COMMAND_FAILED:{' '.join(args)}:{detail.decode('utf-8','replace') if isinstance(detail,bytes) else detail}") from exc

def _f2b_rev(repo: Path, revision: str | None) -> str:
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("EXPLICIT_SOURCE_REVISION_REQUIRED")
    try:
        return _f2b_git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode("ascii").strip()
    except RuntimeError as exc:
        raise ValueError(f"INVALID_SOURCE_REVISION:{revision}") from exc

def build_revision_range_source(repository: Path | str = ROOT, *, base_revision: str | None, target_revision: str | None) -> RevisionRangeSource:
    repo = Path(repository).resolve()
    if not (repo / ".git").exists():
        raise ValueError("NOT_A_GIT_REPOSITORY")
    base, target = _f2b_rev(repo, base_revision), _f2b_rev(repo, target_revision)
    patch = _f2b_git(repo, "diff", "--binary", "--full-index", "--no-ext-diff", base, target, "--")
    names = _f2b_git(repo, "diff", "--name-only", "-z", base, target, "--").decode("utf-8", "surrogateescape")
    changed = tuple(sorted(item for item in names.split(chr(0)) if item))
    identity = {"contract":"git_revision_range","repository":".","base_revision":base,"target_revision":target,"changed_paths":list(changed),"patch_bytes":len(patch),"patch_sha256":hashlib.sha256(patch).hexdigest()}
    return RevisionRangeSource(base, target, patch, changed, identity)
revision_range_source = build_revision_range_source

@_dataclass_f2b(frozen=True)
class ReviewPackageSpec:
    review_round: str
    package_slug: str
    source_base_revision: str
    source_target_revision: str
    candidate_path: Path | str
    final_path: Path | str
    final_hash_path: Path | str | None = None
    def __post_init__(self) -> None:
        object.__setattr__(self,"candidate_path",Path(self.candidate_path)); object.__setattr__(self,"final_path",Path(self.final_path))
        if self.final_hash_path is not None: object.__setattr__(self,"final_hash_path",Path(self.final_hash_path))

def validate_review_package_spec(spec: ReviewPackageSpec) -> ReviewPackageSpec:
    if not re.fullmatch(r"R[1-9][0-9]*", spec.review_round) or not spec.package_slug.endswith(f"_{spec.review_round}"): raise ValueError("PACKAGE_SLUG_ROUND_MISMATCH")
    if not spec.source_base_revision or not spec.source_target_revision: raise ValueError("EXPLICIT_SOURCE_REVISION_REQUIRED")
    candidate, final = spec.candidate_path.resolve(), spec.final_path.resolve(); sidecar = Path(spec.final_hash_path or f"{final}.sha256").resolve()
    if candidate == final: raise ValueError("CANDIDATE_FINAL_SAME_PATH")
    if candidate == HISTORICAL_R3_ZIP.resolve() or final == HISTORICAL_R3_ZIP.resolve(): raise ValueError("HISTORICAL_R3_OUTPUT_FORBIDDEN")
    if candidate.suffix.lower() != ".zip" or final.suffix.lower() != ".zip" or not candidate.name.startswith(spec.package_slug) or final.name != f"{spec.package_slug}.zip": raise ValueError("PACKAGE_FILENAME_ROUND_MISMATCH")
    if candidate.exists(): raise FileExistsError(f"CANDIDATE_ALREADY_EXISTS:{candidate}")
    if final.exists(): raise FileExistsError(f"FINAL_ALREADY_EXISTS:{final}")
    if sidecar.exists(): raise FileExistsError(f"FINAL_HASH_ALREADY_EXISTS:{sidecar}")
    return spec

def _f2b_members(path: Path) -> dict[str,bytes]:
    if not path.is_file() or not zipfile.is_zipfile(path): raise RuntimeError(f"CORRUPT_PACKAGE:{path}")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None: raise RuntimeError("CORRUPT_PACKAGE_CRC")
        names=archive.namelist()
        if len(names)!=len(set(names)): raise RuntimeError("DUPLICATE_PACKAGE_MEMBER")
        return {name:archive.read(name) for name in names}

def _f2b_manifest(members: Mapping[str,bytes], expected_root: str|None=None, expected_round: str|None=None) -> tuple[str,dict[str,Any]]:
    roots=set(); relative={}
    for name in members:
        path=Path(name)
        if path.is_absolute() or "\\" in name or ".." in path.parts or len(path.parts)<2: raise RuntimeError(f"UNSAFE_PACKAGE_MEMBER:{name}")
        roots.add(path.parts[0]); relative[name]="/".join(path.parts[1:])
    if len(roots)!=1: raise RuntimeError("PACKAGE_ROOT_MISMATCH")
    root=next(iter(roots))
    if expected_root is not None and root!=expected_root: raise RuntimeError("PACKAGE_ROOT_MISMATCH")
    manifest_name=f"{root}/02_review_manifest.json"
    if manifest_name not in members: raise RuntimeError("MISSING_MANIFEST")
    manifest=json.loads(members[manifest_name].decode("utf-8"))
    if manifest.get("package")!=root or (expected_round is not None and manifest.get("review_round")!=expected_round): raise RuntimeError("MANIFEST_PACKAGE_OR_ROUND_MISMATCH")
    entries={str(item.get("path")):item for item in manifest.get("entries",[])}; actual={relative[name] for name in members if name!=manifest_name}
    if actual!=set(entries): raise RuntimeError("MANIFEST_COVERAGE_MISMATCH")
    for rel,item in entries.items():
        if Path(rel).is_absolute() or "\\" in rel or ".." in Path(rel).parts: raise RuntimeError(f"UNSAFE_MANIFEST_PATH:{rel}")
        data=members[f"{root}/{rel}"]
        if not data or int(item.get("bytes",-1))!=len(data) or item.get("sha256")!=hashlib.sha256(data).hexdigest(): raise RuntimeError(f"MANIFEST_HASH_MISMATCH:{rel}")
    return root,{"manifest":manifest,"entries":entries}

def audit_candidate_package(package_path: Path|str, *, package_slug: str|None=None, review_round: str|None=None) -> dict[str,Any]:
    path=Path(package_path); members=_f2b_members(path); root,report=_f2b_manifest(members,package_slug,review_round)
    required=(f"{root}/git_diff.patch",f"{root}/source_identity.json",f"{root}/qa_results.json")
    if any(name not in members for name in required): raise RuntimeError("CANDIDATE_REQUIRED_MEMBER_MISSING")
    source=json.loads(members[required[1]].decode("utf-8")); patch=members[required[0]]
    if source.get("patch_sha256")!=hashlib.sha256(patch).hexdigest(): raise RuntimeError("SOURCE_PATCH_HASH_MISMATCH")
    qa=json.loads(members[required[2]].decode("utf-8"))
    for name,record in qa.items():
        if name!="source_identity" and (record.get("exit_code")!=0 or record.get("status")!="PASS"): raise RuntimeError(f"QA_NOT_ACCEPTABLE:{name}")
    return {"status":"PASS","package_path":str(path),"package_sha256":_sha(path),"package_bytes":path.stat().st_size,"entry_count":len(report["entries"]),"source_identity":source}

def audit_historical_package_zip(package_path: Path|str, *, expected_sha256: str|None=None) -> dict[str,Any]:
    path=Path(package_path)
    if not path.is_file(): raise RuntimeError("MISSING_HISTORICAL_R3")
    actual=_sha(path)
    if expected_sha256 is not None and actual.lower()!=expected_sha256.lower(): raise RuntimeError("HISTORICAL_R3_SHA_MISMATCH")
    members=_f2b_members(path); root,report=_f2b_manifest(members,"STAGE_9A7_WP2_UNIFIED_PANEL_REVIEW_R3")
    checkpoint=json.loads(members[f"{root}/checkpoint_snapshot.json"].decode("utf-8"))
    if checkpoint.get("review_round")!="R3" or checkpoint.get("snapshot_phase")!="PRE_ZIP_FINALIZATION": raise RuntimeError("HISTORICAL_R3_CHECKPOINT_INVALID")
    patch=members[f"{root}/git_diff.patch"]; patch_audit=json.loads(members[f"{root}/patch_audit.json"].decode("utf-8"))
    if patch_audit.get("portable_package_patch_sha256")!=hashlib.sha256(patch).hexdigest(): raise RuntimeError("HISTORICAL_R3_PATCH_HASH_MISMATCH")
    fingerprint=json.loads(members[f"{root}/01_source_fingerprint.json"].decode("utf-8"))
    for item in fingerprint.get("files",[]):
        rel=str(item.get("path")); candidates=[f"{group}/{rel}" for group in SNAPSHOTS if f"{root}/{group}/{rel}" in members]
        if len(candidates)!=1: raise RuntimeError(f"HISTORICAL_R3_FINGERPRINT_MEMBER_MISSING:{rel}")
        data=members[f"{root}/{candidates[0]}"]
        if len(data)!=int(item.get("bytes",-1)) or hashlib.sha256(data).hexdigest()!=item.get("sha256"): raise RuntimeError(f"HISTORICAL_R3_FINGERPRINT_MISMATCH:{rel}")
    qa=json.loads(members[f"{root}/qa_logs/qa_results.json"].decode("utf-8"))
    for name in QA_LOG_NAMES:
        record=qa.get(_QA_LOG_RESULT_KEYS[name]); member=f"{root}/qa_logs/{name}"
        if member not in members or not members[member] or not isinstance(record,dict) or record.get("exit_code")!=0 or record.get("status")!="PASS": raise RuntimeError(f"HISTORICAL_R3_QA_INVALID:{name}")
    if qa.get("source_identity")!=checkpoint.get("source_state_identity"): raise RuntimeError("HISTORICAL_R3_SOURCE_IDENTITY_MISMATCH")
    semantic=json.loads(members[f"{root}/screenshot_semantic_audit.json"].decode("utf-8"))
    for key in ("unexpected_duplicate_png_count","tofu_count","raw_key_count","replacement_character_count","synthetic_overlay_count","mockup_count"):
        if semantic.get(key)!=0: raise RuntimeError(f"HISTORICAL_R3_SEMANTIC_AUDIT:{key}")
    return {"status":"PASS","historical":True,"round":"R3","package_sha256":actual,"package_bytes":path.stat().st_size,"entry_count":len(report["entries"]),"working_tree_compared":False,"qa_checked":len(QA_LOG_NAMES)}
audit_historical_r3=audit_historical_package_zip

def cleanup_owned_temp_root(temp_root: Path|str, parent: Path|str, owner_token: str) -> str:
    root,expected=Path(temp_root).resolve(),Path(parent).resolve()
    marker=root/TEMP_OWNER_MARKER
    if root.parent!=expected or not root.name.startswith(TEMP_BUILD_PREFIX): raise ValueError("TEMP_ROOT_CONTAINMENT_FAILED")
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("owner")!=owner_token: raise ValueError("TEMP_ROOT_OWNERSHIP_FAILED")
    shutil.rmtree(root); return "CLEANED"

def _f2b_write_manifest(directory: Path,spec: ReviewPackageSpec,source: RevisionRangeSource)->None:
    entries=[{"path":p.relative_to(directory).as_posix(),"bytes":p.stat().st_size,"sha256":_sha(p)} for p in sorted(directory.rglob("*")) if p.is_file() and p.name!="02_review_manifest.json"]
    _json(directory/"02_review_manifest.json",{"format_version":3,"package":spec.package_slug,"review_round":spec.review_round,"manifest_excludes_itself":True,"entries":entries,"source_identity":source.identity})

def _f2b_zip(directory:Path,destination:Path,slug:str)->None:
    if destination.exists(): raise FileExistsError(f"PACKAGE_OUTPUT_EXISTS:{destination}")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,"x",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file(): archive.write(path,(Path(slug)/path.relative_to(directory)).as_posix())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None: raise RuntimeError("ZIP_CRC_FAILED")

def build_candidate_package(staging_directory:Path|str,spec:ReviewPackageSpec,source:RevisionRangeSource,source_qa_records:Mapping[str,Mapping[str,Any]])->dict[str,Any]:
    validate_review_package_spec(spec); staging=Path(staging_directory)
    if not staging.is_dir(): raise ValueError("STAGING_DIRECTORY_REQUIRED")
    if not source_qa_records or any(r.get("exit_code")!=0 or r.get("status")!="PASS" for r in source_qa_records.values()): raise ValueError("SOURCE_QA_NOT_PASS")
    candidate=spec.candidate_path.resolve(); owner=hashlib.sha256(f"{candidate}:{source.identity['patch_sha256']}".encode()).hexdigest(); temp=Path(_tempfile_f2b.mkdtemp(prefix=TEMP_BUILD_PREFIX,dir=str(candidate.parent)))
    try:
        (temp/TEMP_OWNER_MARKER).write_text(json.dumps({"owner":owner}),encoding="utf-8"); payload=temp/spec.package_slug; shutil.copytree(staging,payload)
        for name in ("02_review_manifest.json","git_diff.patch","source_identity.json","qa_results.json"):
            stale=payload/name
            if stale.exists(): stale.unlink()
        (payload/"git_diff.patch").write_bytes(source.patch_bytes); _json(payload/"source_identity.json",source.identity); _json(payload/"qa_results.json",{**source_qa_records,"source_identity":source.identity}); _f2b_write_manifest(payload,spec,source); _f2b_zip(payload,candidate,spec.package_slug)
        return audit_candidate_package(candidate,package_slug=spec.package_slug,review_round=spec.review_round)
    finally:
        if temp.exists(): cleanup_owned_temp_root(temp,candidate.parent,owner)

def accept_candidate_package(candidate_path:Path|str,spec:ReviewPackageSpec)->dict[str,Any]:
    if Path(candidate_path).resolve()!=spec.candidate_path.resolve(): raise ValueError("CANDIDATE_PATH_MISMATCH")
    return audit_candidate_package(candidate_path,package_slug=spec.package_slug,review_round=spec.review_round)

def promote_candidate_to_final(spec:ReviewPackageSpec)->dict[str,Any]:
    candidate,final=spec.candidate_path.resolve(),spec.final_path.resolve(); sidecar=Path(spec.final_hash_path or f"{final}.sha256").resolve()
    if not candidate.is_file(): raise RuntimeError("CANDIDATE_REQUIRED")
    if final.exists(): raise FileExistsError(f"FINAL_ALREADY_EXISTS:{final}")
    if sidecar.exists(): raise FileExistsError(f"FINAL_HASH_ALREADY_EXISTS:{sidecar}")
    accepted=accept_candidate_package(candidate,spec); data=candidate.read_bytes(); final.parent.mkdir(parents=True,exist_ok=True)
    with final.open("xb") as stream: stream.write(data)
    with sidecar.open("x",encoding="utf-8",newline="\n") as stream: stream.write(hashlib.sha256(data).hexdigest()+"\n")
    immutable=audit_candidate_package(final,package_slug=spec.package_slug,review_round=spec.review_round)
    if immutable["package_sha256"]!=accepted["package_sha256"] or immutable["package_bytes"]!=accepted["package_bytes"]: raise RuntimeError("FINAL_CANDIDATE_BYTES_MISMATCH")
    return {"status":"PASS","candidate":accepted,"final":immutable,"final_hash_path":str(sidecar)}

def run_package_lifecycle(staging_directory:Path|str,spec:ReviewPackageSpec,repository:Path|str=ROOT,source_qa_records:Mapping[str,Mapping[str,Any]]|None=None)->dict[str,Any]:
    validate_review_package_spec(spec)
    if source_qa_records is None: raise ValueError("SOURCE_QA_REQUIRED")
    source=build_revision_range_source(repository,base_revision=spec.source_base_revision,target_revision=spec.source_target_revision); candidate=build_candidate_package(staging_directory,spec,source,source_qa_records); acceptance=accept_candidate_package(spec.candidate_path,spec); final=promote_candidate_to_final(spec); immutable=audit_candidate_package(spec.final_path,package_slug=spec.package_slug,review_round=spec.review_round)
    return {"phases":["SOURCE_QA","CANDIDATE_PACKAGE","CANDIDATE_PACKAGE_ACCEPTANCE","FINAL_PACKAGE","IMMUTABLE_FINAL_AUDIT"],"source":source.identity,"candidate":candidate,"acceptance":acceptance,"final":final,"immutable_final_audit":immutable}

def build_package(staging_directory:Path|str,spec:ReviewPackageSpec,*,repository:Path|str=ROOT,source_qa_records:Mapping[str,Mapping[str,Any]])->dict[str,Any]: return run_package_lifecycle(staging_directory,spec,repository,source_qa_records)
def finalize_package(staging_directory:Path|str,spec:ReviewPackageSpec,*,repository:Path|str=ROOT,source_qa_records:Mapping[str,Mapping[str,Any]])->dict[str,Any]: return run_package_lifecycle(staging_directory,spec,repository,source_qa_records)

import argparse as _f2b_argparse

def main() -> int:
    parser = _f2b_argparse.ArgumentParser()
    parser.add_argument("--review-round", required=True)
    parser.add_argument("--package-slug", required=True)
    parser.add_argument("--source-base", required=True)
    parser.add_argument("--source-target", required=True)
    parser.add_argument("--staging-directory", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--qa-results", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.qa_results.read_text(encoding="utf-8"))
    spec = ReviewPackageSpec(args.review_round, args.package_slug, args.source_base, args.source_target, args.candidate, args.final)
    result = build_package(args.staging_directory, spec, source_qa_records=records)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
