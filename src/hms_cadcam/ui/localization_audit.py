"""Three-locale catalog, runtime, accessibility, layout and glyph auditing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import QAction, QFont, QFontDatabase, QRawFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDockWidget,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabBar,
    QTabWidget,
    QToolButton,
    QWidget,
)


# Item models can mark user/domain values that must stay byte-for-byte intact.
# Headers, tooltips and the remaining presentation cells are still audited.
LOCALIZATION_AUDIT_EXCLUDE_ROLE = int(Qt.ItemDataRole.UserRole) + 97
from shiboken6 import getCppPointer

from hms_cadcam.ui.i18n import (
    TranslationService,
    UiLanguage,
    format_geometry_update_message,
    translation_service,
    validate_glossary,
)

VIETNAMESE_MARKS = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"àáảãạằắẳẵặầấẩẫậ"
    r"èéẻẽẹềếểễệ"
    r"ìíỉĩị"
    r"òóỏõọồốổỗộờớởỡợ"
    r"ùúủũụừứửữự"
    r"ỳýỷỹỵ]",
)
HANGUL = re.compile(r"[\uac00-\ud7a3]")
RAW_KEY = re.compile(r"^(?:ui|menu|dialog|field|status|error)\.[a-z0-9_.-]+$")
RAW_NAMESPACE = re.compile(r"\b(?:parallel|z_level|simulation|post)\.[a-z0-9_.-]+\b")
RAW_MODEL_TOKEN = re.compile(
    r"\b(?:RESOLVED|UNRESOLVED|MISSING|STALE|INVALID|PRIMARY|SECONDARY)\b"
)
RAW_ENUM = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
REPLACEMENT = "\ufffd"
TOFU = "\u25a1"
NATIVE_LANGUAGE_LABELS = {
    "English",
    "Korean",
    "Vietnamese",
    "한국어",
    "Tiếng Việt",
}
APPROVED_TECHNICAL_LABELS = {
    "CAD/Viewer",
}
APPROVED_LATIN_TOKENS = frozenset(
    {
        "CAD",
        "CAM",
        "CNC",
        "TOOL",
        "HOLDER",
        "POST",
        "G-CODE",
        "TOOLPATH",
        "IR",
        "SQLITE",
        "OCP",
        "BREP",
        "UUID",
        "ID",
        "STEP",
        "IGES",
        "STL",
        "HMS",
        "STP",
        "IGS",
        "RPM",
        "MM",
        "APPDATA",
        "PROGRAMDATA",
        "WINDOWS",
        "ROAMING",
        "KB",
        "MB",
        "GB",
        "CACHE",
        "LOGS",
        "TEMP",
        "CRASH",
        "JSON",
        "EXE",
        "UI",
        "DPI",
        "RUNTIME",
        "PLUGIN",
        "SANDBOX",
        "FAIL-CLOSED",
        "MACHINE-READY",
        "PID",
    }
)
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z-]+")
HMS_USER_VALUE = re.compile(r"[\w.-]+\.HMS", re.IGNORECASE)
PHYSICAL_WINDOWS_PATH = re.compile(r"(?:[A-Za-z]:[\\/][^\n]+)")
PHYSICAL_FILENAME = re.compile(
    r"\b[\w.-]+\.(?:json|ini|lock|backup|exe|hms)\b",
    re.IGNORECASE,
)
HASH_VALUE = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
FORMAT_FIELD = re.compile(r"\{[^{}]+\}")
VIETNAMESE_FORBIDDEN_ENGLISH = re.compile(
    r"\b(?:"
    r"setup|mesh|strategy|viewer|metric|review|unknown|unavailable|"
    r"ribbon|float|closes|undocks|top|rendering|backend|"
    r"run\s+simulation|new\s+3d\s+data"
    r")\b",
    re.IGNORECASE,
)
VIETNAMESE_UNAPPROVED_ENGLISH = re.compile(
    r"\b(?:root\s+production|executable|runtime|plugin|installer|"
    r"contract\s+runtime|preference|program\s+templates|machine-ready|"
    r"autosave|snapshot|command\s+id|profile)\b",
    re.IGNORECASE,
)
ENGLISH_SENTENCE_WORDS = frozenset(
    {
        "the",
        "is",
        "are",
        "from",
        "to",
        "and",
        "or",
        "with",
        "without",
        "current",
        "new",
        "open",
        "close",
        "save",
        "project",
        "operation",
        "available",
        "unavailable",
        "ready",
        "review",
        "stage",
        "rendering",
        "backend",
    }
)


@dataclass(frozen=True, slots=True)
class LocaleAuditReport:
    locale: str
    catalog_key_count: int
    production_visible_key_count: int
    translated_count: int
    missing_key_count: int
    fallback_hit_count: int
    empty_translation_count: int
    duplicate_key_count: int
    raw_key_count: int
    mixed_language_count: int
    unapproved_term_count: int
    raw_enum_count: int
    raw_model_token_count: int
    raw_namespace_count: int
    missing_accessible_name_count: int
    missing_accessible_description_count: int
    clipping_count: int
    clipped_text_count: int
    unintended_elision_count: int
    sidebar_elision_count: int
    ribbon_elision_count: int
    dock_tab_elision_count: int
    duplicate_dock_tab_bar_count: int
    duplicate_dock_tab_set_count: int
    duplicate_visible_tab_label_count: int
    dock_tab_partial_visibility_count: int
    dock_tab_out_of_bounds_count: int
    dock_tab_missing_leading_character_count: int
    missing_full_text_tooltip_count: int
    missing_full_accessible_name_count: int
    locale_message_format_error_count: int
    overlap_count: int
    horizontal_scroll_count: int
    missing_glyph_count: int
    replacement_glyph_count: int
    tofu_count: int
    unapproved_english_token_count: int
    vietnamese_semantic_translation_error_count: int
    physical_path_false_positive_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeAuditMetrics:
    texts: tuple[str, ...] = ()
    missing_accessible_name_count: int = 0
    missing_accessible_description_count: int = 0
    clipping_count: int = 0
    clipped_text_count: int = 0
    unintended_elision_count: int = 0
    sidebar_elision_count: int = 0
    ribbon_elision_count: int = 0
    dock_tab_elision_count: int = 0
    duplicate_dock_tab_bar_count: int = 0
    duplicate_dock_tab_set_count: int = 0
    duplicate_visible_tab_label_count: int = 0
    dock_tab_partial_visibility_count: int = 0
    dock_tab_out_of_bounds_count: int = 0
    dock_tab_missing_leading_character_count: int = 0
    missing_full_text_tooltip_count: int = 0
    missing_full_accessible_name_count: int = 0
    locale_message_format_error_count: int = 0
    overlap_count: int = 0
    horizontal_scroll_count: int = 0
    missing_glyph_count: int = 0
    replacement_glyph_count: int = 0
    tofu_count: int = 0
    unapproved_english_token_count: int = 0
    vietnamese_semantic_translation_error_count: int = 0
    physical_path_false_positive_count: int = 0


@dataclass(frozen=True, slots=True)
class _RenderedTextMetrics:
    unintended_elision_count: int = 0
    sidebar_elision_count: int = 0
    ribbon_elision_count: int = 0
    dock_tab_elision_count: int = 0
    duplicate_dock_tab_bar_count: int = 0
    duplicate_dock_tab_set_count: int = 0
    duplicate_visible_tab_label_count: int = 0
    dock_tab_partial_visibility_count: int = 0
    dock_tab_out_of_bounds_count: int = 0
    dock_tab_missing_leading_character_count: int = 0
    missing_full_text_tooltip_count: int = 0
    missing_full_accessible_name_count: int = 0
    locale_message_format_error_count: int = 0


def audit_locale(
    service: TranslationService,
    language: UiLanguage,
    *,
    visible_keys: tuple[str, ...],
    runtime: RuntimeAuditMetrics = RuntimeAuditMetrics(),
) -> LocaleAuditReport:
    """Audit one locale with a fresh resolver so fallback hits are exact."""
    vietnamese = service.catalogs.get(UiLanguage.VI_VN)
    catalog = service.catalogs.get(language)
    required = () if vietnamese is None else tuple(vietnamese.entries)
    source_entries = {} if vietnamese is None else vietnamese.entries
    if catalog is None:
        missing = required
        empty: tuple[str, ...] = ()
        duplicates: tuple[str, ...] = ()
        entries: dict[str, str] = {}
    else:
        validation = catalog.validate(required, source_entries=source_entries)
        missing = validation.missing_keys
        empty = validation.empty_keys
        duplicates = validation.duplicate_keys
        entries = dict(catalog.entries)
    probe = TranslationService(service.catalogs, language=language)
    catalog_rendered = tuple(probe.translate_key(key) for key in visible_keys)
    rendered = (
        tuple(dict.fromkeys(runtime.texts))
        if runtime.texts
        else catalog_rendered
    )
    fallback_hits = sum(
        diagnostic.requested_locale is language
        for diagnostic in probe.diagnostics
    )
    glossary_violations = tuple(
        item for item in validate_glossary(service.catalogs)
        if item.startswith(f"{language.value}:")
    )
    return LocaleAuditReport(
        locale=language.value,
        catalog_key_count=len(entries),
        production_visible_key_count=len(visible_keys),
        translated_count=sum(bool(value.strip()) for value in entries.values()),
        missing_key_count=len(missing),
        fallback_hit_count=fallback_hits,
        empty_translation_count=len(empty),
        duplicate_key_count=len(duplicates),
        raw_key_count=sum(bool(RAW_KEY.fullmatch(text.strip())) for text in rendered),
        mixed_language_count=sum(_is_mixed(text, language) for text in rendered),
        unapproved_term_count=len(glossary_violations),
        raw_enum_count=sum(
            bool(RAW_ENUM.fullmatch(text.strip()))
            and text.strip() not in {"CAD", "CAM", "CNC", "OCP", "UUID", "ID", "STEP", "IGES", "STL"}
            for text in rendered
        ),
        raw_model_token_count=sum(bool(RAW_MODEL_TOKEN.search(text)) for text in rendered),
        raw_namespace_count=sum(bool(RAW_NAMESPACE.search(text)) for text in rendered),
        missing_accessible_name_count=runtime.missing_accessible_name_count,
        missing_accessible_description_count=runtime.missing_accessible_description_count,
        clipping_count=runtime.clipping_count,
        clipped_text_count=runtime.clipped_text_count,
        unintended_elision_count=runtime.unintended_elision_count,
        sidebar_elision_count=runtime.sidebar_elision_count,
        ribbon_elision_count=runtime.ribbon_elision_count,
        dock_tab_elision_count=runtime.dock_tab_elision_count,
        duplicate_dock_tab_bar_count=runtime.duplicate_dock_tab_bar_count,
        duplicate_dock_tab_set_count=runtime.duplicate_dock_tab_set_count,
        duplicate_visible_tab_label_count=(
            runtime.duplicate_visible_tab_label_count
        ),
        dock_tab_partial_visibility_count=(
            runtime.dock_tab_partial_visibility_count
        ),
        dock_tab_out_of_bounds_count=runtime.dock_tab_out_of_bounds_count,
        dock_tab_missing_leading_character_count=(
            runtime.dock_tab_missing_leading_character_count
        ),
        missing_full_text_tooltip_count=runtime.missing_full_text_tooltip_count,
        missing_full_accessible_name_count=runtime.missing_full_accessible_name_count,
        locale_message_format_error_count=runtime.locale_message_format_error_count,
        overlap_count=runtime.overlap_count,
        horizontal_scroll_count=runtime.horizontal_scroll_count,
        missing_glyph_count=runtime.missing_glyph_count,
        replacement_glyph_count=runtime.replacement_glyph_count,
        tofu_count=runtime.tofu_count,
        unapproved_english_token_count=(
            _unapproved_vietnamese_count(rendered)
            if language is UiLanguage.VI_VN
            else 0
        ),
        vietnamese_semantic_translation_error_count=(
            _vietnamese_semantic_errors(entries)
            if language is UiLanguage.VI_VN
            else 0
        ),
        physical_path_false_positive_count=_physical_path_false_positive_count(
            rendered,
            language,
        ),
    )


def audit_widget(root: QWidget) -> RuntimeAuditMetrics:
    """Inspect one rendered production widget without changing its state."""
    texts = tuple(dict.fromkeys(_collect_texts(root)))
    interactive = [
        widget
        for widget in root.findChildren(QWidget)
        if isinstance(
            widget,
            (
                QAbstractButton,
                QComboBox,
                QLineEdit,
                QAbstractItemView,
            ),
        )
        and widget.isVisibleTo(root)
        and not widget.objectName().startswith("qt_")
    ]
    missing_names = sum(
        not _effective_accessible_name(widget)
        for widget in interactive
    )
    missing_descriptions = sum(
        not _effective_accessible_description(widget)
        for widget in interactive
    )
    generic_clipping = sum(
        _is_clipped(widget) for widget in root.findChildren(QWidget)
    )
    text_layout = _rendered_text_metrics(root)
    clipping = generic_clipping + text_layout.unintended_elision_count
    horizontal_scroll = sum(
        view.horizontalScrollBar().isVisible()
        and view.horizontalScrollBar().maximum() > 0
        and _has_visible_region_to_root(view.horizontalScrollBar(), root)
        for view in root.findChildren(QAbstractItemView)
        if view.isVisibleTo(root)
    )
    missing_glyphs, replacements, tofu = _glyph_counts(root, texts)
    return RuntimeAuditMetrics(
        texts=texts,
        missing_accessible_name_count=missing_names,
        missing_accessible_description_count=missing_descriptions,
        clipping_count=clipping,
        clipped_text_count=clipping,
        unintended_elision_count=text_layout.unintended_elision_count,
        sidebar_elision_count=text_layout.sidebar_elision_count,
        ribbon_elision_count=text_layout.ribbon_elision_count,
        dock_tab_elision_count=text_layout.dock_tab_elision_count,
        duplicate_dock_tab_bar_count=(
            text_layout.duplicate_dock_tab_bar_count
        ),
        duplicate_dock_tab_set_count=text_layout.duplicate_dock_tab_set_count,
        duplicate_visible_tab_label_count=(
            text_layout.duplicate_visible_tab_label_count
        ),
        dock_tab_partial_visibility_count=(
            text_layout.dock_tab_partial_visibility_count
        ),
        dock_tab_out_of_bounds_count=(
            text_layout.dock_tab_out_of_bounds_count
        ),
        dock_tab_missing_leading_character_count=(
            text_layout.dock_tab_missing_leading_character_count
        ),
        missing_full_text_tooltip_count=(
            text_layout.missing_full_text_tooltip_count
        ),
        missing_full_accessible_name_count=(
            text_layout.missing_full_accessible_name_count
        ),
        locale_message_format_error_count=(
            text_layout.locale_message_format_error_count
        ),
        overlap_count=0,
        horizontal_scroll_count=horizontal_scroll,
        missing_glyph_count=missing_glyphs,
        replacement_glyph_count=replacements,
        tofu_count=tofu,
        unapproved_english_token_count=(
            _unapproved_vietnamese_count(texts)
            if translation_service().language is UiLanguage.VI_VN
            else 0
        ),
        physical_path_false_positive_count=_physical_path_false_positive_count(
            texts,
            translation_service().language,
        ),
    )


def _collect_texts(root: QWidget):
    objects: list[object] = [root]
    objects.extend(root.findChildren(QWidget))
    objects.extend(root.findChildren(QAction))
    for item in objects:
        if isinstance(item, QAction) and _inside_file_dialog_object(item):
            # QFileDialog place/history actions are filesystem data, while
            # the visible dialog labels and buttons remain audited.
            continue
        if isinstance(item, QAction):
            owner = item.parent()
            while owner is not None and not isinstance(owner, QWidget):
                owner = owner.parent() if hasattr(owner, "parent") else None
            if isinstance(owner, QWidget) and not owner.isVisibleTo(root):
                continue
            if isinstance(owner, QWidget):
                domain_owner = owner
                while domain_owner is not None:
                    if bool(
                        domain_owner.property(
                            "localizationAuditDomainText"
                        )
                    ):
                        # Context/menu actions attached to a marked
                        # production view carry dynamic project/model text.
                        # The surrounding controls remain audited normally.
                        break
                    parent = domain_owner.parent()
                    domain_owner = (
                        parent if isinstance(parent, QWidget) else None
                    )
                else:
                    domain_owner = None
                if domain_owner is not None:
                    continue
        if (
            isinstance(item, QWidget)
            and item is not root
            and (
                not item.isVisible()
                or not item.isVisibleTo(root)
                or not _has_visible_region_to_root(item, root)
            )
        ):
            continue
        domain_text = (
            isinstance(item, QWidget)
            and bool(item.property("localizationAuditDomainText"))
        )
        for getter_name in (
            "text",
            "title",
            "windowTitle",
            "toolTip",
            "statusTip",
            "accessibleName",
            "accessibleDescription",
            "placeholderText",
            "toPlainText",
        ):
            if domain_text and getter_name in {
                "text",
                "toPlainText",
                "accessibleName",
                "accessibleDescription",
            }:
                continue
            if getter_name == "text" and isinstance(item, QLineEdit):
                # Editable content is user/domain data. Placeholder,
                # tooltip and accessibility text remain audited below.
                continue
            getter = getattr(item, getter_name, None)
            if callable(getter):
                value = str(getter() or "").strip()
                if value:
                    yield value
        if isinstance(item, QTabWidget):
            for index in range(item.count()):
                value = item.tabText(index).strip()
                if value:
                    yield value
        if isinstance(item, QComboBox) and not _inside_file_dialog(item):
            for index in range(item.count()):
                value = item.itemText(index).strip()
                if value:
                    yield value
        if isinstance(item, QAbstractItemView):
            if bool(item.property("localizationAuditDomainText")):
                # Projection rows may contain user/domain names, stable IDs and
                # dynamic lifecycle summaries.  A marked production view keeps
                # those values visible while excluding them from the language
                # token audit; headers, toolbars and surrounding UI remain
                # audited normally.
                continue
            if not _inside_file_dialog(item) or item.objectName() == "sidebar":
                yield from _collect_model_texts(item)


def _inside_file_dialog(widget: QWidget) -> bool:
    return _inside_file_dialog_object(widget)


def _has_visible_region_to_root(widget: QWidget, root: QWidget) -> bool:
    """Reject descendants hidden behind an inactive native dock/tab page."""
    current: QWidget | None = widget
    while current is not None and current is not root:
        if current.isVisible() and current.visibleRegion().isEmpty():
            return False
        current = current.parentWidget()
    return current is root


def _inside_file_dialog_object(item: object) -> bool:
    current: object | None = item
    while current is not None:
        if any(
            base.__name__ == "QFileDialog"
            for base in type(current).__mro__
        ):
            return True
        parent = getattr(current, "parent", None)
        current = parent() if callable(parent) else None
    return False


def _collect_model_texts(view: QAbstractItemView):
    """Read rendered model/header/delegate roles without mutating the model."""
    model = view.model()
    if model is None or type(model).__name__ == "QFileSystemModel":
        return
    remaining = 2_000
    for section in range(model.columnCount()):
        for role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.AccessibleTextRole,
            Qt.ItemDataRole.AccessibleDescriptionRole,
        ):
            value = model.headerData(section, Qt.Orientation.Horizontal, role)
            if isinstance(value, str) and value.strip():
                yield value.strip()

    def visit(parent: QModelIndex = QModelIndex()):
        nonlocal remaining
        rows = model.rowCount(parent)
        columns = model.columnCount(parent)
        for row in range(rows):
            if remaining <= 0:
                return
            remaining -= 1
            for column in range(columns):
                index = model.index(row, column, parent)
                if not index.isValid():
                    continue
                if model.data(index, LOCALIZATION_AUDIT_EXCLUDE_ROLE):
                    continue
                for role in (
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                ):
                    audit_value = getattr(model, "localization_audit_value", None)
                    value = (
                        audit_value(index, role)
                        if callable(audit_value)
                        else model.data(index, role)
                    )
                    if isinstance(value, str) and value.strip():
                        yield value.strip()
                delegate = view.itemDelegateForIndex(index)
                audit_texts = getattr(delegate, "audit_texts", None)
                if callable(audit_texts):
                    for value in audit_texts(index):
                        text = str(value).strip()
                        if text:
                            yield text
            first = model.index(row, 0, parent)
            if first.isValid() and model.rowCount(first):
                yield from visit(first)

    yield from visit()


def _effective_accessible_name(widget: QWidget) -> str:
    explicit = widget.accessibleName().strip()
    if explicit:
        return explicit
    text = getattr(widget, "text", None)
    return str(text() if callable(text) else "").strip()


def _effective_accessible_description(widget: QWidget) -> str:
    return (
        widget.accessibleDescription().strip()
        or widget.toolTip().strip()
        or _effective_accessible_name(widget)
    )


def _is_clipped(widget: QWidget) -> bool:
    if (
        not widget.isVisible()
        or isinstance(widget, QToolButton)
        or isinstance(widget, QLabel) and widget.wordWrap()
    ):
        return False
    getter = getattr(widget, "text", None)
    if not callable(getter):
        return False
    text = str(getter() or "")
    if not text or "\n" in text:
        return False
    margins = widget.contentsMargins()
    available = widget.width() - margins.left() - margins.right() - 4
    # Native Qt text rendering has a small antialiasing/rounding allowance at
    # the right edge; treat only a material overrun as clipping.
    return (
        available > 0
        and widget.fontMetrics().horizontalAdvance(text) > available + 8
    )


def _normalized_label(text: object) -> str:
    return " ".join(str(text).replace("&", "").split())


def _contains_full_text(container: object, full_text: object) -> bool:
    full = _normalized_label(full_text)
    return bool(full) and full in _normalized_label(container)


def _would_elide(widget: QWidget, text: str, available_width: int) -> bool:
    if available_width <= 0:
        return True
    rendered = widget.fontMetrics().elidedText(
        text,
        Qt.TextElideMode.ElideRight,
        available_width,
    )
    return _normalized_label(rendered) != _normalized_label(text)


def _rendered_text_metrics(root: QWidget) -> _RenderedTextMetrics:
    """Audit the exact surfaces where native Qt can silently elide labels."""
    ribbon_elision = 0
    sidebar_elision = 0
    dock_tab_elision = 0
    missing_tooltips = 0
    missing_accessible_names = 0
    message_errors = 0

    for button in root.findChildren(QToolButton):
        if (
            button.objectName() != "RibbonButton"
            or not button.isVisibleTo(root)
        ):
            continue
        full = str(button.property("fullText") or "").strip()
        displayed_lines = button.text().splitlines()
        if displayed_lines and displayed_lines[0] in {"●", "○", "■", "•"}:
            displayed_lines = displayed_lines[1:]
        displayed = "\n".join(displayed_lines)
        expected_display = str(
            button.property("compactText") or full
        ).strip()
        content_width = max(0, button.contentsRect().width() - 8)
        visibly_elided = (
            not full
            or _normalized_label(displayed)
            != _normalized_label(expected_display)
            or "..." in displayed
            or "…" in displayed
            or any(
                _would_elide(button, line, content_width)
                for line in displayed_lines
                if line.strip()
            )
        )
        ribbon_elision += visibly_elided
        missing_tooltips += not _contains_full_text(button.toolTip(), full)
        missing_accessible_names += not _contains_full_text(
            button.accessibleName(),
            full,
        )

    for view in root.findChildren(QAbstractItemView):
        if (
            view.objectName() != "sidebar"
            or not view.isVisibleTo(root)
            or view.model() is None
        ):
            continue
        model = view.model()
        icon_width = max(24, view.iconSize().width())
        content_width = max(0, view.viewport().contentsRect().width())
        text_width = max(0, content_width - icon_width - 24)
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            displayed = str(
                model.data(index, Qt.ItemDataRole.DisplayRole) or ""
            ).strip()
            tooltip = str(
                model.data(index, Qt.ItemDataRole.ToolTipRole) or ""
            ).strip()
            accessible = str(
                model.data(index, Qt.ItemDataRole.AccessibleTextRole) or ""
            ).strip()
            visibly_elided = (
                not displayed
                or "..." in displayed
                or "…" in displayed
                or _would_elide(view, displayed, text_width)
            )
            sidebar_elision += visibly_elided
            missing_tooltips += not _contains_full_text(tooltip, displayed)
            missing_accessible_names += not _contains_full_text(
                accessible,
                displayed,
            )

    service = translation_service()
    dock_sources = {
        "Geometry / Project": (
            "Geometry / Project",
            "Geometry structure / Project Manager",
        ),
        "Geometry structure / Project Manager": (
            "Geometry / Project",
            "Geometry structure / Project Manager",
        ),
        "Operations": ("Operations", "Operation Manager"),
        "Operation Manager": ("Operations", "Operation Manager"),
        "Post": ("Post", "Simulation / Post"),
        "Simulation / Post": ("Post", "Simulation / Post"),
    }
    dock_bars: list[tuple[QTabBar, tuple[str, ...]]] = []
    visible_labels: list[str] = []
    root_rect = QRect(root.mapToGlobal(QPoint(0, 0)), root.size())
    native_docks_by_payload: dict[int, QDockWidget] = {}
    for dock in root.findChildren(QDockWidget):
        native_docks_by_payload[id(dock)] = dock
        native_docks_by_payload[int(getCppPointer(dock)[0])] = dock
    partial_visibility = 0
    out_of_bounds = 0
    missing_leading_character = 0
    seen_rendered_bars: set[
        tuple[tuple[int, int, int, int], tuple[str, ...]]
    ] = set()
    for tab_bar in root.findChildren(QTabBar):
        if (
            not tab_bar.isVisible()
            or not bool(tab_bar.property("hmsDockTabBar"))
            or not any(
                isinstance(tab_bar.tabData(index), int)
                for index in range(tab_bar.count())
            )
        ):
            continue
        bar_global_rect = QRect(
            tab_bar.mapToGlobal(QPoint(0, 0)),
            tab_bar.size(),
        )
        if not root_rect.intersects(bar_global_rect):
            # QMainWindow parks hidden native dock groups off-screen while
            # leaving their private QTabBar visibility flag set.
            continue
        semantic_ids: list[str] = []
        audited_tabs: list[tuple[int, str, str]] = []
        declared_sources = tuple(
            str(value)
            for value in (
                tab_bar.property("dockTabCompactSources") or ()
            )
        )
        for index in range(tab_bar.count()):
            declared_source = (
                declared_sources[index]
                if index < len(declared_sources)
                else ""
            )
            canonical = declared_source or str(
                service.canonical_key(tab_bar.tabText(index))
            )
            source_pair = dock_sources.get(canonical)
            if source_pair is None:
                continue
            compact_source, full_source = source_pair
            semantic_ids.append(str(tab_bar.tabData(index)))
            audited_tabs.append(
                (
                    index,
                    service.translate_key(compact_source),
                    service.translate_key(full_source),
                )
            )
        if not audited_tabs:
            continue
        semantic_key = tuple(semantic_ids)
        rendered_key = (
            bar_global_rect.getRect(),
            semantic_key,
        )
        if rendered_key in seen_rendered_bars:
            # Some Qt platform plugins expose multiple private wrappers for
            # the same native row. Equal geometry and semantic IDs render as
            # one row; only distinct rows constitute a duplicate.
            continue
        seen_rendered_bars.add(rendered_key)
        dock_bars.append((tab_bar, semantic_key))
        mapped_docks = tuple(
            native_docks_by_payload[payload]
            for index in range(tab_bar.count())
            if isinstance((payload := tab_bar.tabData(index)), int)
            and payload in native_docks_by_payload
        )
        visible_dock_rects = tuple(
            QRect(dock.mapToGlobal(QPoint(0, 0)), dock.size())
            for dock in mapped_docks
            if root_rect.intersects(
                QRect(dock.mapToGlobal(QPoint(0, 0)), dock.size())
            )
        )
        if visible_dock_rects and not any(
            dock_rect.left() == bar_global_rect.left()
            and dock_rect.right() == bar_global_rect.right()
            and abs(bar_global_rect.top() - (dock_rect.bottom() + 1)) <= 2
            for dock_rect in visible_dock_rects
        ):
            # A native tab row belongs directly below the visible dock in its
            # tabified group. A row parked beside another dock can otherwise
            # have locally valid tabRect values while being globally wrong.
            out_of_bounds += len(audited_tabs)
        for index, compact, full in audited_tabs:
            displayed = tab_bar.tabText(index)
            available = max(0, tab_bar.tabRect(index).width())
            visibly_elided = (
                "..." in displayed
                or "…" in displayed
                or _would_elide(tab_bar, displayed, available)
            )
            dock_tab_elision += visibly_elided
            missing_tooltips += not _contains_full_text(
                tab_bar.tabToolTip(index),
                full,
            )
            missing_accessible_names += not _contains_full_text(
                tab_bar.accessibleName(),
                full,
            )
            visible_labels.append(_normalized_label(displayed))
            if compact == "Post" and _normalized_label(displayed) != "Post":
                missing_leading_character += not _normalized_label(
                    displayed
                ).startswith("P")
            tab_rect = tab_bar.tabRect(index)
            global_tab_rect = QRect(
                tab_bar.mapToGlobal(tab_rect.topLeft()),
                tab_rect.size(),
            )
            root_rect = QRect(root.mapToGlobal(QPoint(0, 0)), root.size())
            fully_inside = (
                root_rect.contains(global_tab_rect.topLeft())
                and root_rect.contains(global_tab_rect.bottomRight())
            )
            if not fully_inside:
                if root_rect.intersects(global_tab_rect):
                    partial_visibility += 1
                else:
                    out_of_bounds += 1

    semantic_sets: dict[tuple[str, ...], int] = {}
    for _tab_bar, semantic_ids in dock_bars:
        semantic_sets[semantic_ids] = semantic_sets.get(semantic_ids, 0) + 1
    duplicate_dock_tab_bar = sum(
        max(0, count - 1) for count in semantic_sets.values()
    )
    duplicate_dock_tab_set = sum(
        count > 1 for count in semantic_sets.values()
    )
    label_counts: dict[str, int] = {}
    for label in visible_labels:
        label_counts[label] = label_counts.get(label, 0) + 1
    duplicate_visible_labels = sum(
        max(0, count - 1) for count in label_counts.values()
    )

    for label in root.findChildren(QLabel):
        if label.objectName() != "IncomingGeometryMessage":
            continue
        count = label.property("localeMessageCount")
        source = label.property("localeMessageSource")
        try:
            expected = format_geometry_update_message(
                int(count),
                "" if source is None else source,
            )
        except (TypeError, ValueError):
            message_errors += 1
            continue
        message_errors += label.text() != expected

    unintended = ribbon_elision + sidebar_elision + dock_tab_elision
    return _RenderedTextMetrics(
        unintended_elision_count=unintended,
        sidebar_elision_count=sidebar_elision,
        ribbon_elision_count=ribbon_elision,
        dock_tab_elision_count=dock_tab_elision,
        duplicate_dock_tab_bar_count=duplicate_dock_tab_bar,
        duplicate_dock_tab_set_count=duplicate_dock_tab_set,
        duplicate_visible_tab_label_count=duplicate_visible_labels,
        dock_tab_partial_visibility_count=partial_visibility,
        dock_tab_out_of_bounds_count=out_of_bounds,
        dock_tab_missing_leading_character_count=missing_leading_character,
        missing_full_text_tooltip_count=missing_tooltips,
        missing_full_accessible_name_count=missing_accessible_names,
        locale_message_format_error_count=message_errors,
    )


def _glyph_counts(root: QWidget, texts: tuple[str, ...]) -> tuple[int, int, int]:
    base_font = root.font()
    families = [
        base_font.family(),
        "Segoe UI",
        "Malgun Gothic",
        "Arial",
        "Segoe UI Symbol",
    ]
    raw_fonts: list[QRawFont] = []
    installed = set(QFontDatabase.families())
    for family in dict.fromkeys(families):
        if family not in installed:
            continue
        candidate = QRawFont.fromFont(QFont(family, base_font.pointSize()))
        if candidate.isValid():
            raw_fonts.append(candidate)
    missing = 0
    if raw_fonts:
        for text in texts:
            # Line breaks and other controls are layout instructions, not
            # renderable glyphs, and Qt quite correctly reports glyph index 0
            # for them.
            renderable = "".join(
                character
                for character in text
                if character not in "\r\n\t"
            )
            for character in renderable:
                if not any(
                    font.glyphIndexesForString(character)[0] != 0
                    for font in raw_fonts
                ):
                    missing += 1
    replacements = sum(text.count(REPLACEMENT) for text in texts)
    tofu = sum(text.count(TOFU) for text in texts)
    return missing, replacements, tofu


def _is_mixed(text: str, language: UiLanguage) -> bool:
    if text.strip() in NATIVE_LANGUAGE_LABELS | APPROVED_TECHNICAL_LABELS:
        return False
    remainder = _strip_physical_data(text)
    if language is UiLanguage.VI_VN:
        if HANGUL.search(remainder):
            return True
        if VIETNAMESE_FORBIDDEN_ENGLISH.search(remainder):
            return True
        if VIETNAMESE_UNAPPROVED_ENGLISH.search(remainder):
            return True
        words = {
            word.casefold()
            for word in LATIN_WORD.findall(remainder)
            if word.upper() not in APPROVED_LATIN_TOKENS
        }
        return len(words & ENGLISH_SENTENCE_WORDS) >= 2
    if language is UiLanguage.EN_US:
        return bool(HANGUL.search(remainder) or VIETNAMESE_MARKS.search(remainder))
    if VIETNAMESE_MARKS.search(remainder):
        return True
    return any(
        word.upper() not in APPROVED_LATIN_TOKENS
        for word in LATIN_WORD.findall(remainder)
    )


def _strip_physical_data(text: str) -> str:
    return FORMAT_FIELD.sub(
        "",
        HASH_VALUE.sub(
            "",
            PHYSICAL_FILENAME.sub(
                "",
                PHYSICAL_WINDOWS_PATH.sub("", HMS_USER_VALUE.sub("", text)),
            ),
        ),
    )


def _unapproved_vietnamese_count(texts: tuple[str, ...]) -> int:
    return sum(
        len(VIETNAMESE_UNAPPROVED_ENGLISH.findall(_strip_physical_data(text)))
        for text in texts
    )


def _vietnamese_semantic_errors(entries: dict[str, str]) -> int:
    expected = {
        "Back": "Quay lại",
        "Next": "Tiếp tục",
        "Close": "Đóng",
    }
    return sum(entries.get(key) != value for key, value in expected.items())


def _physical_path_false_positive_count(
    texts: tuple[str, ...],
    language: UiLanguage,
) -> int:
    if language is not UiLanguage.VI_VN:
        return 0
    return sum(
        bool(PHYSICAL_WINDOWS_PATH.search(text))
        and bool(
            VIETNAMESE_UNAPPROVED_ENGLISH.search(
                _strip_physical_data(text)
            )
        )
        for text in texts
    )


__all__ = [
    "LocaleAuditReport",
    "RuntimeAuditMetrics",
    "audit_locale",
    "audit_widget",
]
