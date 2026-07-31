"""Typed, in-memory feature flags for UI review work.

Stages 9A.7/9A.8 intentionally do not persist review flags in SQLite, ``.HMS``
projects, profiles, or backup categories.  Production remains disabled until
the later GUI approval gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class UiFeatureFlag(StrEnum):
    POST_ASSEMBLY_9A7 = "post_assembly_9a7"
    CAM_3D_9A8 = "cam_3d_9a8"
    LATHE_9A9 = "lathe_9a9"
    LATHE_TOOLPATH_12_1 = "lathe_toolpath_12_1"


@dataclass(frozen=True, slots=True)
class UiFeatureFlags:
    """Immutable in-memory feature-flag container."""

    _values: Mapping[UiFeatureFlag, bool]

    def __post_init__(self) -> None:
        if not isinstance(self._values, Mapping):
            raise TypeError("feature flags must be provided as a mapping")
        values: dict[UiFeatureFlag, bool] = {}
        for flag, enabled in self._values.items():
            if type(flag) is not UiFeatureFlag:
                raise TypeError("feature flag keys must be UiFeatureFlag")
            if type(enabled) is not bool:
                raise TypeError("feature flag values must be bool")
            values[flag] = enabled
        object.__setattr__(self, "_values", MappingProxyType(values))

    def is_enabled(self, flag: UiFeatureFlag) -> bool:
        """Return the explicit value for ``flag``; unknown flags fail closed."""

        if type(flag) is not UiFeatureFlag:
            return False
        return self._values.get(flag, False)

    @classmethod
    def for_development_and_tests(cls) -> "UiFeatureFlags":
        return cls(
            {
                UiFeatureFlag.POST_ASSEMBLY_9A7: False,
                UiFeatureFlag.CAM_3D_9A8: False,
                UiFeatureFlag.LATHE_9A9: False,
                UiFeatureFlag.LATHE_TOOLPATH_12_1: False,
            }
        )

    @classmethod
    def for_review_harness(cls) -> "UiFeatureFlags":
        return cls(
            {
                UiFeatureFlag.POST_ASSEMBLY_9A7: True,
                UiFeatureFlag.CAM_3D_9A8: True,
                UiFeatureFlag.LATHE_9A9: False,
                UiFeatureFlag.LATHE_TOOLPATH_12_1: False,
            }
        )

    @classmethod
    def for_production(cls) -> "UiFeatureFlags":
        # WP1 stays fail-closed until the GUI approval gate.
        return cls(
            {
                UiFeatureFlag.POST_ASSEMBLY_9A7: False,
                UiFeatureFlag.CAM_3D_9A8: False,
                UiFeatureFlag.LATHE_9A9: False,
                UiFeatureFlag.LATHE_TOOLPATH_12_1: False,
            }
        )


__all__ = ["UiFeatureFlag", "UiFeatureFlags"]
