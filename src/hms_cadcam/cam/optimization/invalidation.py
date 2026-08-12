"""Phase-aware invalidation rules; unknown changes fail closed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InvalidationScope(StrEnum):
    NONE = "none"
    LINKING = "linking"
    REST = "rest"
    GEOMETRY = "geometry"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class InvalidationDecision:
    parameter: str
    scope: InvalidationScope
    reusable_phases: tuple[str, ...]


class InvalidationMatrix:
    """Conservative matrix for parameters whose dependency semantics are proven."""

    _RULES = {
        "camera": (InvalidationScope.NONE, ("geometry", "regions", "offsets", "toolpath", "validation")),
        "ui_color": (InvalidationScope.NONE, ("geometry", "regions", "offsets", "toolpath", "validation")),
        "feed_rate": (InvalidationScope.LINKING, ("geometry", "regions", "offsets", "roughing")),
        "lead": (InvalidationScope.LINKING, ("geometry", "regions", "offsets", "roughing")),
        "tool_diameter": (InvalidationScope.GEOMETRY, ()),
        "tool_type": (InvalidationScope.GEOMETRY, ()),
        "holder": (InvalidationScope.ALL, ()),
        "stock": (InvalidationScope.REST, ("geometry", "regions", "offsets")),
        "wcs": (InvalidationScope.ALL, ()),
        "tolerance": (InvalidationScope.GEOMETRY, ()),
        "stepover": (InvalidationScope.GEOMETRY, ()),
        "stepdown": (InvalidationScope.GEOMETRY, ()),
        "boundary": (InvalidationScope.GEOMETRY, ()),
        "previous_operation": (InvalidationScope.REST, ()),
        "engine_version": (InvalidationScope.ALL, ()),
    }

    @classmethod
    def decide(cls, parameter: str) -> InvalidationDecision:
        if not isinstance(parameter, str) or not parameter.strip():
            raise ValueError("Invalidation parameter is invalid")
        key = parameter.strip().lower()
        scope, reusable = cls._RULES.get(key, (InvalidationScope.ALL, ()))
        return InvalidationDecision(key, scope, reusable)
