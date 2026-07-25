"""Merge the reviewed Stage 8A.4.4 storage strings into UTF-8 catalogs."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from hms_cadcam.ui.storage_translations import STORAGE_TRANSLATIONS


CATALOG_ROOT = Path("src/hms_cadcam/ui/catalogs")


def update_catalogs() -> tuple[Path, ...]:
    counts = Counter(source for source, _vi, _ko in STORAGE_TRANSLATIONS)
    duplicates = tuple(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate storage translation keys: {duplicates!r}")
    updates = {
        "vi_VN.json": {source: vietnamese for source, vietnamese, _ko in STORAGE_TRANSLATIONS},
        "en_US.json": {source: source for source, _vi, _ko in STORAGE_TRANSLATIONS},
        "ko_KR.json": {source: korean for source, _vi, korean in STORAGE_TRANSLATIONS},
    }
    written: list[Path] = []
    for filename, additions in updates.items():
        path = CATALOG_ROOT / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in document.items()
        ):
            raise ValueError(f"Invalid catalog: {path}")
        document.update(additions)
        path.write_text(
            json.dumps(dict(sorted(document.items())), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return tuple(written)


def main() -> int:
    for path in update_catalogs():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
