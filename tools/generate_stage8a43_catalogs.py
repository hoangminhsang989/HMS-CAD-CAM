"""Generate deterministic Stage 8A.4.3 JSON catalogs.

The production application never calls a network translation service.  This
developer tool is used only to seed the Korean catalog; reviewed HMS overrides
and technical glossary terms are applied before the UTF-8 JSON is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hms_cadcam.ui.i18n import (
    CORE_TRANSLATIONS,
    DISPLAY_SOURCE_TRANSLATIONS,
    KOREAN_OVERRIDES,
    LEGACY_TRANSLATIONS,
    RIBBON_TRANSLATIONS,
    TECHNICAL_GLOSSARY,
    VIETNAMESE_SOURCE_TRANSLATIONS,
)
from hms_cadcam.ui.localization import UI_TRANSLATIONS

CATALOG_DIRECTORY = Path("src/hms_cadcam/ui/catalogs")
SPLITTER = "HMS_I18N_SPLIT_7F3A"
TRANSLATION_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
VIETNAMESE_PATTERN = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ]",
    re.IGNORECASE,
)


def _combined_source() -> tuple[dict[str, str], dict[str, str]]:
    vietnamese = dict(UI_TRANSLATIONS)
    english = {key: key for key in UI_TRANSLATIONS}
    for source, vi_text, _ko_text in CORE_TRANSLATIONS:
        vietnamese[source] = vi_text
        english[source] = source
    for source, vi_text, _ko_text in RIBBON_TRANSLATIONS:
        vietnamese[source] = vi_text
        english[source] = source
        unavailable_key = f"{source} — unavailable"
        vietnamese[unavailable_key] = f"{vi_text} — chưa khả dụng"
        english[unavailable_key] = unavailable_key
    for source, vi_text, _ko_text in LEGACY_TRANSLATIONS:
        vietnamese[source] = vi_text
        english[source] = source
    for vi_source, en_text, _ko_text in VIETNAMESE_SOURCE_TRANSLATIONS:
        vietnamese.setdefault(vi_source, vi_source)
        english[vi_source] = en_text
    for source, vi_text, en_text, _ko_text in DISPLAY_SOURCE_TRANSLATIONS:
        vietnamese[source] = vi_text
        english[source] = en_text
    for term in TECHNICAL_GLOSSARY:
        vietnamese[term.source] = term.vietnamese
        english[term.source] = term.english
    return vietnamese, english


def _translate_batch(values: tuple[str, ...]) -> tuple[str, ...]:
    payload = f"\n{SPLITTER}\n".join(values)
    query = urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": "ko",
            "dt": "t",
            "q": payload,
        }
    )
    request = Request(
        f"{TRANSLATION_ENDPOINT}?{query}",
        headers={"User-Agent": "HMS-CADCAM-catalog-builder/1.0"},
    )
    last_error: OSError | ValueError | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                document = json.loads(response.read().decode("utf-8"))
            translated = "".join(segment[0] for segment in document[0])
            parts = tuple(part.strip() for part in translated.split(SPLITTER))
            if len(parts) != len(values):
                raise ValueError(
                    f"Expected {len(values)} translations, received {len(parts)}"
                )
            return parts
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"Korean catalog translation failed: {last_error}")


def _batches(
    items: tuple[tuple[str, str], ...],
    *,
    max_items: int = 18,
    max_characters: int = 3_000,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    batches: list[tuple[tuple[str, str], ...]] = []
    current: list[tuple[str, str]] = []
    current_size = 0
    for item in items:
        item_size = len(item[1]) + len(SPLITTER) + 2
        if current and (
            len(current) >= max_items
            or current_size + item_size > max_characters
        ):
            batches.append(tuple(current))
            current = []
            current_size = 0
        current.append(item)
        current_size += item_size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _korean_sources(
    vietnamese: dict[str, str],
    english: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    for key in sorted(english):
        source = english[key]
        if VIETNAMESE_PATTERN.search(source):
            source = vietnamese[key]
        sources.append((key, source))
    return tuple(sources)


def _reviewed_korean_overrides(keys: set[str]) -> dict[str, str]:
    overrides = {
        key: value for key, value in KOREAN_OVERRIDES.items()
        if key in keys
    }
    for source, _vi_text, ko_text in CORE_TRANSLATIONS:
        if source in keys:
            overrides[source] = ko_text
    for source, _vi_text, ko_text in RIBBON_TRANSLATIONS:
        if source in keys:
            overrides[source] = ko_text
        unavailable_key = f"{source} — unavailable"
        if unavailable_key in keys:
            overrides[unavailable_key] = f"{ko_text} — 사용할 수 없음"
    for source, _vi_text, ko_text in LEGACY_TRANSLATIONS:
        if source in keys:
            overrides[source] = ko_text
    for vi_source, _en_text, ko_text in VIETNAMESE_SOURCE_TRANSLATIONS:
        if vi_source in keys:
            overrides[vi_source] = ko_text
    for source, _vi_text, _en_text, ko_text in DISPLAY_SOURCE_TRANSLATIONS:
        if source in keys:
            overrides[source] = ko_text
    for term in TECHNICAL_GLOSSARY:
        if term.source in keys:
            overrides.setdefault(term.source, term.korean)
    return overrides


def generate_catalogs(*, use_network: bool) -> dict[str, dict[str, str]]:
    vietnamese, english = _combined_source()
    korean = {key: KOREAN_OVERRIDES.get(key, key) for key in english}
    if use_network:
        translated: dict[str, str] = {}
        sources = _korean_sources(vietnamese, english)
        for batch in _batches(sources):
            values = tuple(value for _key, value in batch)
            results = _translate_batch(values)
            translated.update(
                (key, result)
                for (key, _source), result in zip(batch, results, strict=True)
            )
        korean.update(translated)
    korean.update(_reviewed_korean_overrides(set(english)))
    return {
        "vi_VN": vietnamese,
        "en_US": english,
        "ko_KR": korean,
    }


def write_catalogs(catalogs: dict[str, dict[str, str]]) -> tuple[Path, ...]:
    CATALOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem, entries in catalogs.items():
        path = CATALOG_DIRECTORY / f"{stem}.json"
        path.write_text(
            json.dumps(dict(sorted(entries.items())), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return tuple(written)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--translate-korean",
        action="store_true",
        help="Seed Korean entries through the developer-only translation endpoint.",
    )
    args = parser.parse_args()
    for path in write_catalogs(
        generate_catalogs(use_network=args.translate_korean)
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
