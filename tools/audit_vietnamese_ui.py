"""Deterministic production-UI Vietnamese localization audit for Stage 8A.2.3."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable

from hms_cadcam.ui.localization import (
    DISPLAY_VALUE_MAPPINGS,
    OPERATION_DISPLAY_NAMES,
    PROGRESS_PHASE_TRANSLATIONS,
    TECHNICAL_TERMS,
    UI_TRANSLATIONS,
    ui_text,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "reference_private" / "DERIVED" / "UI_VIETNAMESE_AUDIT"
)
PRODUCTION_UI_ROOT = REPOSITORY_ROOT / "src" / "hms_cadcam" / "ui"

_UI_CONSTRUCTORS = {
    "FunctionEditorDiagnostic",
    "FunctionEditorField",
    "FunctionEditorSection",
    "FunctionEditorSectionSummary",
    "FunctionEditorSummary",
    "FunctionEditorValidationRule",
    "OperationManagerHeader",
    "OperationManagerStatus",
    "QAction",
    "QCheckBox",
    "QDockWidget",
    "QGroupBox",
    "QLabel",
    "QMenu",
    "QMessageBox",
    "QPushButton",
    "QRadioButton",
    "QTableWidgetItem",
    "QToolButton",
    "QTreeWidgetItem",
    "ValidationRule",
}
_UI_METHODS = {
    "_action",
    "_disable",
    "_error",
    "_set_capability",
    "_set_enabled",
    "addAction",
    "addActions",
    "addItem",
    "addItems",
    "addMenu",
    "addRow",
    "addTab",
    "critical",
    "emit",
    "information",
    "question",
    "setAccessibleDescription",
    "setAccessibleName",
    "set_cache_diagnostic",
    "set_error",
    "setHeaderLabels",
    "setHorizontalHeaderLabels",
    "setInformativeText",
    "setItemText",
    "setPlaceholderText",
    "setStatusTip",
    "setTabText",
    "setText",
    "setTitle",
    "setToolTip",
    "setVerticalHeaderLabels",
    "setWindowTitle",
    "status",
    "warning",
}
_UI_KEYWORDS = {
    "accessible_description",
    "accessible_name",
    "button_label",
    "disabled_reason",
    "help_text",
    "label",
    "message",
    "placeholder",
    "secondary_summary",
    "summary",
    "title",
    "tooltip",
}

# Stable IDs and persisted enum values are intentionally excluded by position.
# Only positional arguments that can be presented to a user are audited here;
# keyword presentation fields continue to be covered by ``_UI_KEYWORDS``.
_PRESENTATION_ARGUMENTS = {
    "FunctionEditorDiagnostic": (1,),
    "FunctionEditorField": (1,),
    "FunctionEditorSection": (1, 3),
    "FunctionEditorSectionSummary": (0,),
    "FunctionEditorSummary": (0, 1, 2, 3, 4),
    "FunctionEditorValidationRule": (2,),
    "OperationManagerHeader": (0, 1, 2, 3),
    "OperationManagerStatus": (2, 3),
    "ValidationRule": (2,),
}

# This is deliberately a vocabulary, not a phrase or file allowlist.  A mixed
# Vietnamese/English label is still reported when it contains any word here.
_ENGLISH_UI_WORDS = {
    "absent", "action", "active", "add", "advanced", "all", "allowance", "apply", "approach",
    "artifact", "assembly", "basic", "blocked", "boring", "bounds", "broad",
    "browse", "calculate", "calculated", "calculating", "calculation", "cancel",
    "cancelled", "capability", "category", "checked", "checksum", "clear", "clearance",
    "close", "code", "collision", "component", "components", "contour", "copy", "cutter",
    "binding", "comment", "controller", "count", "current", "delete", "details", "diagnostic", "diagnostics", "directory", "display", "document",
    "declared", "direction", "disable", "disabled", "distance", "domain", "draft", "drilling",
    "duplicate", "elapsed", "empty", "enable", "enabled", "error", "expert",
    "export", "external", "face", "faces", "facing", "failed", "feed", "file", "filesystem", "filter",
    "finalization", "finishing", "frame", "generate", "generation", "geometry",
    "geometryreferenceid", "gouge", "group", "hook", "input", "intersection", "invalid", "island", "job", "key", "library", "linear", "linking", "local", "machine", "mapped",
    "machining", "manage", "managed", "manager", "marker", "message", "metadata", "missing", "mode", "motion", "name", "narrow",
    "new", "next", "no", "none", "normal", "not", "occurrence", "offset", "outer",
    "object", "one", "open", "operation", "operations", "optional", "ordering", "overall", "pass", "persistent",
    "phase", "placeholder", "planning", "pocket", "precision", "preview", "profile", "project",
    "present", "processor", "production", "program", "progress", "projection", "protected", "radius", "rapid", "raw", "ready", "reaming", "reference", "rename", "render", "revision",
    "reference-only", "report", "required", "reset", "result", "retract", "review", "safe", "selection", "shank", "snapshot",
    "safety", "save", "scope", "section", "segment", "select", "selected",
    "setup", "severity", "simulation", "source", "stage", "stale", "status", "stepover", "stock", "summary", "topology",
    "surface", "swept", "tapping", "time", "toolpath", "tolerance", "total",
    "typed", "unavailable", "unknown", "unresolved", "unsafe", "unverified", "validate", "validation", "verified",
    "viewport", "warning", "workflow", "workspace", "way", "zigzag",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_CODE_OR_ID = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9]*[._-][A-Za-z0-9_.-]*|G\d+|M\d+|T\d+|v?\d+(?:\.\d+)*)$"
)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    file: str
    line: int
    text: str
    context: str
    classification: str
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditResult:
    total: int
    translated: int
    allowlisted: int
    untranslated: int
    entries: tuple[AuditEntry, ...]


@dataclass(frozen=True, slots=True)
class RuntimeAuditEntry:
    state: str
    object_type: str
    object_name: str
    source: str
    text: str
    classification: str
    matched_terms: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeAuditResult:
    state_count: int
    total: int
    translated: int
    allowlisted: int
    untranslated: int
    entries: tuple[RuntimeAuditEntry, ...]


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _template(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{…}")
        return "".join(parts)
    return None


def _string_nodes(node: ast.AST) -> Iterable[ast.AST]:
    value = _template(node)
    if value is not None:
        yield node
        return
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for child in node.elts:
            yield from _string_nodes(child)


class _UiLiteralVisitor(ast.NodeVisitor):
    def __init__(self, relative_file: str) -> None:
        self.relative_file = relative_file
        self.candidates: dict[tuple[int, int, str], tuple[str, int, str]] = {}

    def _add(self, node: ast.AST, context: str) -> None:
        for string_node in _string_nodes(node):
            value = _template(string_node)
            if value is None or not value.strip():
                continue
            key = (
                int(getattr(string_node, "lineno", 0)),
                int(getattr(string_node, "col_offset", 0)),
                value,
            )
            self.candidates[key] = (value, key[0], context)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"log", "logger", "_logger"}
        ):
            self.generic_visit(node)
            return
        is_ui_call = name in _UI_CONSTRUCTORS or name in _UI_METHODS
        if is_ui_call:
            indexes = _PRESENTATION_ARGUMENTS.get(name, range(len(node.args)))
            for index in indexes:
                if index < len(node.args):
                    self._add(node.args[index], name)
        if name == "ui_text":
            for argument in node.args:
                self._add(argument, name)
        for keyword in node.keywords:
            if keyword.arg in _UI_KEYWORDS and name != "require_stable_id":
                self._add(keyword.value, f"{name}.{keyword.arg}")
        self.generic_visit(node)


def _technical_matches(text: str) -> tuple[str, ...]:
    return tuple(term for term in TECHNICAL_TERMS if re.search(rf"\b{re.escape(term)}\b", text))


def _english_matches(text: str) -> tuple[str, ...]:
    scrubbed = re.sub(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b", " ", text)
    for term in sorted(TECHNICAL_TERMS, key=len, reverse=True):
        scrubbed = re.sub(rf"\b{re.escape(term)}\b", " ", scrubbed)
    return tuple(
        sorted(
            {
                token.casefold()
                for token in _WORD.findall(scrubbed)
                if token.casefold() in _ENGLISH_UI_WORDS
            }
        )
    )


def _classify(text: str, context: str) -> tuple[str, tuple[str, ...]]:
    stripped = text.strip()
    technical = _technical_matches(stripped)
    code_prefix = re.match(r"^([a-z][a-z0-9_.-]+)\s*[·:]\s*(.*)$", stripped)
    code_message = (
        re.sub(r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]+\b", " ", code_prefix.group(2))
        if code_prefix is not None
        else ""
    )
    if code_prefix is not None and not _english_matches(code_message):
        return "allowlisted", (code_prefix.group(1), *technical)
    if _CODE_OR_ID.fullmatch(stripped) or (
        technical and not _english_matches(stripped)
    ):
        return "allowlisted", technical or (stripped,)
    if context == "ui_text" or stripped in UI_TRANSLATIONS or ui_text(stripped) != stripped:
        return "translated", ()
    english = _english_matches(stripped)
    if english:
        return "untranslated", english
    return "translated", ()


def audit_production_ui() -> AuditResult:
    """Return a deterministic audit of production user-facing literals."""
    records: list[AuditEntry] = []
    for path in sorted(PRODUCTION_UI_ROOT.rglob("*.py")):
        if path.name == "localization.py":
            continue
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        visitor = _UiLiteralVisitor(relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=relative))
        for value, line, context in visitor.candidates.values():
            classification, matches = _classify(value, context)
            records.append(
                AuditEntry(relative, line, value, context, classification, matches)
            )
    catalog_path = PRODUCTION_UI_ROOT / "localization.py"
    catalog_lines = catalog_path.read_text(encoding="utf-8").splitlines()
    catalogs = (
        ("catalog_target", UI_TRANSLATIONS),
        ("progress_catalog_target", PROGRESS_PHASE_TRANSLATIONS),
    )
    for context, catalog in catalogs:
        for source, target in sorted(catalog.items()):
            english = _english_matches(target)
            if not english:
                continue
            line = next(
                (
                    index
                    for index, value in enumerate(catalog_lines, start=1)
                    if f'"{source}"' in value
                ),
                0,
            )
            records.append(
                AuditEntry(
                    catalog_path.relative_to(REPOSITORY_ROOT).as_posix(),
                    line,
                    target,
                    context,
                    "untranslated",
                    english,
                )
            )
    entries = tuple(sorted(records, key=lambda item: (item.file, item.line, item.text)))
    counts = {
        name: sum(item.classification == name for item in entries)
        for name in ("translated", "allowlisted", "untranslated")
    }
    return AuditResult(
        len(entries),
        counts["translated"],
        counts["allowlisted"],
        counts["untranslated"],
        entries,
    )


_RUNTIME_INTERNAL_CODE = re.compile(
    r"\b(?:parallel|field|simulation|post)\.[a-z0-9_.-]+\b"
)
_RUNTIME_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_RUNTIME_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_RUNTIME_UPPER_ENUM = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def _runtime_categories(text: str) -> tuple[str, ...]:
    categories: set[str] = set()
    for category, mapping in DISPLAY_VALUE_MAPPINGS.items():
        if any(
            re.search(rf"(?<![\w.]){re.escape(source)}(?![\w.])", text)
            or target in text
            for source, target in mapping.items()
        ):
            categories.add(category)
    if categories:
        categories.add("dynamic_enum")
    return tuple(sorted(categories))


def _classify_runtime(
    text: str,
    source: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    stripped = text.strip()
    categories = _runtime_categories(stripped)
    if not stripped:
        return "translated", (), categories
    if stripped.startswith("Mã phạm vi nội bộ:"):
        raw_scope = stripped.split(":", 1)[1].strip()
        if raw_scope in DISPLAY_VALUE_MAPPINGS["safety_scope"]:
            return "allowlisted", (raw_scope,), categories

    scrubbed = _RUNTIME_INTERNAL_CODE.sub(" ", stripped)
    scrubbed = _RUNTIME_UUID.sub(" ", scrubbed)
    scrubbed = re.sub(r"\b[0-9a-fA-F]{32,64}\b", " ", scrubbed)
    for term in sorted(TECHNICAL_TERMS, key=len, reverse=True):
        scrubbed = re.sub(
            rf"\b{re.escape(term)}\b", " ", scrubbed, flags=re.IGNORECASE
        )

    raw_matches: set[str] = set()
    for mapping in DISPLAY_VALUE_MAPPINGS.values():
        for raw in mapping:
            if re.search(rf"(?<![\w.]){re.escape(raw)}(?![\w.])", scrubbed):
                raw_matches.add(raw)
    raw_matches.update(_RUNTIME_IDENTIFIER.findall(scrubbed))
    upper = {
        token
        for token in _RUNTIME_UPPER_ENUM.findall(scrubbed)
        if token.casefold() in _ENGLISH_UI_WORDS
        or any(
            token in mapping
            for mapping in DISPLAY_VALUE_MAPPINGS.values()
        )
    }
    raw_matches.update(upper)
    if raw_matches:
        return "untranslated", tuple(sorted(raw_matches)), categories

    remainder = re.sub(r"\b(?:G|M|T)\d+\b|\bv?\d+(?:\.\d+)*\b", " ", scrubbed)
    remainder = re.sub(r"\b[0-9a-fA-F]{8,16}\b", " ", remainder)
    if re.match(r"^[A-Za-z]:[\\/]", stripped) or not remainder.strip():
        return "allowlisted", (source,), categories
    english = _english_matches(remainder)
    if english:
        return "untranslated", english, categories
    return "translated", (), categories


def collect_runtime_strings(root: object, state: str) -> tuple[RuntimeAuditEntry, ...]:
    """Collect rendered Qt strings, including item-view DisplayRole values."""
    from PySide6.QtCore import QModelIndex, Qt
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractItemView,
        QComboBox,
        QGroupBox,
        QLabel,
        QLineEdit,
        QMenu,
        QProgressBar,
        QStatusBar,
        QTabWidget,
        QWidget,
    )

    candidates: dict[tuple[str, str, str, str], tuple[object, str, str]] = {}

    def add(item: object, source: str, value: object) -> None:
        text = str(value).strip()
        if not text:
            return
        object_type = type(item).__name__
        object_name_method = getattr(item, "objectName", None)
        object_name = object_name_method() if callable(object_name_method) else ""
        key = (object_type, str(object_name), source, text)
        candidates[key] = (item, source, text)

    widgets: list[QWidget] = []
    if isinstance(root, QWidget):
        widgets = [root, *root.findChildren(QWidget)]
    for item in widgets:
        if item is not root and not item.isVisibleTo(root):
            continue
        if isinstance(item, QLabel):
            add(item, "text", item.text())
        if isinstance(item, QAbstractButton):
            add(item, "text", item.text())
        if isinstance(item, QGroupBox):
            add(item, "title", item.title())
        if isinstance(item, QMenu):
            add(item, "title", item.title())
        if isinstance(item, QLineEdit):
            add(item, "placeholder", item.placeholderText())
        if isinstance(item, QProgressBar):
            add(item, "progress_text", item.text())
        if isinstance(item, QStatusBar):
            add(item, "status_message", item.currentMessage())
        if isinstance(item, QTabWidget):
            for index in range(item.count()):
                add(item, f"tab_text[{index}]", item.tabText(index))
                add(item, f"tab_tooltip[{index}]", item.tabToolTip(index))
        if isinstance(item, QComboBox):
            add(item, "placeholder", item.placeholderText())
            for index in range(item.count()):
                add(item, f"combo_item[{index}]", item.itemText(index))
                add(
                    item,
                    f"combo_tooltip[{index}]",
                    item.itemData(index, Qt.ItemDataRole.ToolTipRole) or "",
                )
        if isinstance(item, QAbstractItemView):
            model = item.model()
            if model is not None:
                try:
                    root_column_count = model.columnCount(QModelIndex())
                except (RuntimeError, TypeError):
                    # Qt's private combo popup model is covered through
                    # QComboBox.itemText/itemData above.
                    root_column_count = 0
                for column in range(root_column_count):
                    add(
                        item,
                        f"model_header[{column}]",
                        model.headerData(
                            column,
                            Qt.Orientation.Horizontal,
                            Qt.ItemDataRole.DisplayRole,
                        )
                        or "",
                    )

                def visit(parent: QModelIndex = QModelIndex()) -> None:
                    for row in range(model.rowCount(parent)):
                        first = model.index(row, 0, parent)
                        for column in range(model.columnCount(parent)):
                            index = model.index(row, column, parent)
                            add(
                                item,
                                f"model_display[{row},{column}]",
                                model.data(index, Qt.ItemDataRole.DisplayRole) or "",
                            )
                            add(
                                item,
                                f"model_tooltip[{row},{column}]",
                                model.data(index, Qt.ItemDataRole.ToolTipRole) or "",
                            )
                        if first.isValid():
                            try:
                                child_count = model.rowCount(first)
                            except (RuntimeError, TypeError):
                                child_count = 0
                            if child_count:
                                visit(first)

                if root_column_count:
                    visit()
        for getter_name, source in (
            ("windowTitle", "window_title"),
            ("toolTip", "tooltip"),
            ("statusTip", "status_tip"),
            ("accessibleName", "accessible_name"),
            ("accessibleDescription", "accessible_description"),
        ):
            getter = getattr(item, getter_name, None)
            if callable(getter):
                add(item, source, getter())

    if isinstance(root, QWidget):
        for action in root.findChildren(QAction):
            add(action, "action_text", action.text())
            add(action, "tooltip", action.toolTip())
            add(action, "status_tip", action.statusTip())

    records: list[RuntimeAuditEntry] = []
    for (object_type, object_name, source, text), _value in sorted(
        candidates.items()
    ):
        classification, matches, categories = _classify_runtime(text, source)
        records.append(
            RuntimeAuditEntry(
                state,
                object_type,
                object_name,
                source,
                text,
                classification,
                matches,
                categories,
            )
        )
    return tuple(records)


def audit_runtime_review_states() -> RuntimeAuditResult:
    """Render and audit the 20 deterministic Parallel editor review states."""
    repository_text = str(REPOSITORY_ROOT)
    if repository_text not in sys.path:
        sys.path.insert(0, repository_text)
    from tests.manual_stage8a2_3_parallel_editor import generate

    states: set[str] = set()
    records: list[RuntimeAuditEntry] = []

    def observe(state: str, root: object) -> None:
        states.add(state)
        records.extend(collect_runtime_strings(root, state))

    operation_state = "operation_display_names"
    states.add(operation_state)
    for source_name, display_name in OPERATION_DISPLAY_NAMES.items():
        classification, matches, categories = _classify_runtime(
            display_name, "operation_display_name"
        )
        records.append(
            RuntimeAuditEntry(
                operation_state,
                "OperationDisplayMapper",
                source_name,
                "operation_display_name",
                display_name,
                classification,
                matches,
                tuple(sorted({*categories, "dynamic_operation_name"})),
            )
        )

    with tempfile.TemporaryDirectory(prefix="hms_vi_runtime_audit_") as temporary:
        generate(Path(temporary) / "unused", observer=observe, write_images=False)
    entries = tuple(
        sorted(
            records,
            key=lambda item: (
                item.state,
                item.object_type,
                item.object_name,
                item.source,
                item.text,
            ),
        )
    )
    counts = {
        name: sum(item.classification == name for item in entries)
        for name in ("translated", "allowlisted", "untranslated")
    }
    return RuntimeAuditResult(
        len(states),
        len(entries),
        counts["translated"],
        counts["allowlisted"],
        counts["untranslated"],
        entries,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_reports(
    result: AuditResult,
    output: Path = DEFAULT_OUTPUT,
    runtime: RuntimeAuditResult | None = None,
) -> None:
    """Write deterministic static and rendered-runtime review artifacts."""
    output.mkdir(parents=True, exist_ok=True)
    runtime = runtime or RuntimeAuditResult(0, 0, 0, 0, 0, ())
    untranslated = [
        asdict(item) for item in result.entries if item.classification == "untranslated"
    ]
    runtime_untranslated = [
        asdict(item)
        for item in runtime.entries
        if item.classification == "untranslated"
    ]
    allowed = [
        asdict(item) for item in result.entries if item.classification == "allowlisted"
    ]
    _write_json(
        output / "untranslated_strings.json",
        {"count": result.untranslated, "strings": untranslated},
    )
    _write_json(
        output / "allowed_technical_terms.json",
        {
            "allowlist": list(TECHNICAL_TERMS),
            "count": result.allowlisted,
            "strings": allowed,
        },
    )
    _write_json(
        output / "runtime_rendered_strings.json",
        {
            "state_count": runtime.state_count,
            "count": runtime.total,
            "strings": [asdict(item) for item in runtime.entries],
        },
    )
    _write_json(
        output / "runtime_untranslated_strings.json",
        {"count": runtime.untranslated, "strings": runtime_untranslated},
    )
    _write_json(
        output / "display_value_mappings.json",
        {
            category: dict(mapping)
            for category, mapping in sorted(DISPLAY_VALUE_MAPPINGS.items())
        },
    )
    category_counts = {
        category: sum(category in item.categories for item in runtime.entries)
        for category in (
            "dynamic_enum",
            "safety_scope",
            "safety_component",
            "geometry_source",
        )
    }
    category_untranslated = {
        category: sum(
            category in item.categories and item.classification == "untranslated"
            for item in runtime.entries
        )
        for category in category_counts
    }
    total = result.total + runtime.total
    translated = result.translated + runtime.translated
    allowlisted = result.allowlisted + runtime.allowlisted
    untranslated_total = result.untranslated + runtime.untranslated
    _write_json(
        output / "translated_strings_summary.json",
        {
            "catalog_entries": len(UI_TRANSLATIONS) + len(PROGRESS_PHASE_TRANSLATIONS),
            "static_strings_audited": result.total,
            "runtime_states_audited": runtime.state_count,
            "runtime_strings_audited": runtime.total,
            "dynamic_enum_values_audited": category_counts["dynamic_enum"],
            "scope_values_audited": category_counts["safety_scope"],
            "component_values_audited": category_counts["safety_component"],
            "geometry_source_values_audited": category_counts["geometry_source"],
            "total_audited": total,
            "total_user_facing_strings": total,
            "translated": translated,
            "translated_strings": translated,
            "allowlisted": allowlisted,
            "allowlisted_technical_strings": allowlisted,
            "untranslated": untranslated_total,
            "untranslated_strings": untranslated_total,
            "static_untranslated": result.untranslated,
            "runtime_untranslated": runtime.untranslated,
            "dynamic_enum_untranslated": category_untranslated["dynamic_enum"],
            "gates": {
                "static_untranslated_zero": result.untranslated == 0,
                "runtime_untranslated_zero": runtime.untranslated == 0,
                "dynamic_enum_untranslated_zero": (
                    category_untranslated["dynamic_enum"] == 0
                ),
            },
        },
    )
    rows = [
        "# Kiểm tra Việt hóa giao diện Stage 8A.2.3",
        "",
        f"- Chuỗi tĩnh đã kiểm tra: {result.total}",
        f"- Trạng thái runtime đã kiểm tra: {runtime.state_count}",
        f"- Chuỗi runtime đã kiểm tra: {runtime.total}",
        f"- Giá trị enum động đã kiểm tra: {category_counts['dynamic_enum']}",
        f"- Giá trị scope đã kiểm tra: {category_counts['safety_scope']}",
        f"- Giá trị component đã kiểm tra: {category_counts['safety_component']}",
        f"- Nguồn hình học đã kiểm tra: {category_counts['geometry_source']}",
        f"- Tổng chuỗi đã kiểm tra: {total}",
        f"- Chuỗi đã dịch: {translated}",
        f"- Chuỗi thuộc danh sách kỹ thuật: {allowlisted}",
        f"- Chuỗi chưa dịch: {untranslated_total}",
        f"- Cổng tĩnh: {'ĐẠT' if result.untranslated == 0 else 'KHÔNG ĐẠT'}",
        f"- Cổng runtime: {'ĐẠT' if runtime.untranslated == 0 else 'KHÔNG ĐẠT'}",
        f"- Cổng enum động: {'ĐẠT' if category_untranslated['dynamic_enum'] == 0 else 'KHÔNG ĐẠT'}",
        "",
        "## Chuỗi chưa dịch",
        "",
    ]
    all_untranslated = [*untranslated, *runtime_untranslated]
    if all_untranslated:
        rows.extend(
            f"- {item.get('file', item.get('state', 'runtime'))} — {item['text']}"
            for item in all_untranslated
        )
    else:
        rows.append("Không có. Cổng kiểm tra đạt: untranslated production user-facing strings = 0.")
    (output / "UI_VIETNAMESE_AUDIT.md").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="render and audit the deterministic 20-state GUI package",
    )
    arguments = parser.parse_args()
    result = audit_production_ui()
    runtime = audit_runtime_review_states() if arguments.runtime else None
    write_reports(result, arguments.output, runtime)
    print(
        f"audited={result.total} translated={result.translated} "
        f"allowlisted={result.allowlisted} untranslated={result.untranslated}"
    )
    if runtime is not None:
        print(
            f"runtime_states={runtime.state_count} runtime_audited={runtime.total} "
            f"runtime_translated={runtime.translated} "
            f"runtime_allowlisted={runtime.allowlisted} "
            f"runtime_untranslated={runtime.untranslated}"
        )
    return 1 if result.untranslated or (runtime and runtime.untranslated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
