"""Fail-closed validation of rendered basic Lathe NC text."""

from __future__ import annotations

import re

from hms_cadcam.cam.lathe.lathe_post.basic_profile import BasicLathePostProfile
from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicPostDiagnostic, BasicPostDiagnosticCode


def _diag(code: BasicPostDiagnosticCode, subject: str | None = None) -> BasicPostDiagnostic:
    return BasicPostDiagnostic(code.value, f"lathe.basic_post.diagnostic.{code.name.casefold()}", subject)


def validate_basic_nc_text(text: str, profile: BasicLathePostProfile) -> tuple[BasicPostDiagnostic, ...]:
    diagnostics: list[BasicPostDiagnostic] = []
    if not isinstance(text, str) or not text:
        return (_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "empty"),)
    if not text.startswith("%") or not text.endswith("%\r\n"):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "envelope"))
    if any((ord(char) < 32 and char not in "\r\n\t") or ord(char) == 127 for char in text):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "control_character"))
    if text.count("(") != text.count(")") or any(text.count("(") < text.count(")") for _ in (0,)):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "comments"))
    program_words = re.findall(r"(?m)^O\d{4}\r?$", text)
    if len(program_words) != 1:
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "program_number"))
    if "G21" not in text:
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "G21"))
    if "G99" not in text:
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "G99"))
    if len(re.findall(r"(?m)^M30\r?$", text)) != 1:
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "M30"))
    if re.search(r"(?i)(?:\d+\.\d*e[+-]?\d+|[-+]?(?:0|[1-9]\d*)\.0+\b)", text):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "numeric_style"))
    if re.search(r"(?m)^\s*\w*\s*(?:nan|inf)\b", text, re.IGNORECASE):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "non_finite"))
    if re.search(r"(?m)^-0(?:\.0+)?(?:\s|$)", text):
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "negative_zero"))
    if profile.machine_verified or profile.production_approved:
        # The basic profile may later be cloned, but V1 still never elevates readiness.
        diagnostics.append(_diag(BasicPostDiagnosticCode.OUTPUT_INVALID, "unverified_state_changed"))
    return tuple(diagnostics)


__all__ = ["validate_basic_nc_text"]
