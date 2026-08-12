"""Lossless visual-rule projection for legacy Post source.

Unknown directives deliberately remain raw; this layer never serializes back
over a WorkNC source file in Tranche1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.revision import ContentFingerprint


class RuleMappingState(StrEnum):
    MAPPED = "MAPPED"
    RAW_SOURCE_REQUIRED = "RAW_SOURCE_REQUIRED"


@dataclass(frozen=True, slots=True)
class VisualPostRule:
    key: str
    state: RuleMappingState
    source_lines: tuple[int, ...]
    value: str | None


@dataclass(frozen=True, slots=True)
class VisualRuleProjection:
    rules: tuple[VisualPostRule, ...]
    raw_source_required: bool
    fingerprint: ContentFingerprint


def project_visual_rules(source: bytes) -> VisualRuleProjection:
    text = source.decode("latin-1")
    rules: list[VisualPostRule] = []
    known = {"G40": "cutter_compensation_cancel", "G41": "cutter_compensation_left", "G42": "cutter_compensation_right", "G28": "safe_return_g28", "G53": "safe_return_g53", "G54": "work_offset_g54"}
    mapped_lines: set[int] = set()
    for number, line in enumerate(text.splitlines(), 1):
        upper = line.upper().replace(" ", "")
        for token, key in known.items():
            if token in upper:
                rules.append(VisualPostRule(key, RuleMappingState.MAPPED, (number,), token)); mapped_lines.add(number)
    unknown = any(line.strip() and number not in mapped_lines for number, line in enumerate(text.splitlines(), 1))
    if unknown:
        rules.append(VisualPostRule("raw_legacy_directives", RuleMappingState.RAW_SOURCE_REQUIRED, (), None))
    payload = {"rules": [{"key": r.key, "state": r.state.value, "source_lines": list(r.source_lines), "value": r.value} for r in rules], "raw_source_required": unknown}
    return VisualRuleProjection(tuple(rules), unknown, ContentFingerprint.from_payload(payload))


__all__ = ["RuleMappingState", "VisualPostRule", "VisualRuleProjection", "project_visual_rules"]
