"""Stable, Qt-free Lathe Foundation V1 enums and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.operation import DiagnosticSeverity


class LatheStrategyId(StrEnum):
    """Exact owner-approved Lathe V1 strategy identifiers."""

    FACE = "lathe.face.v1"
    OD_ROUGH = "lathe.od_rough.v1"
    OD_FINISH = "lathe.od_finish.v1"
    ID_ROUGH = "lathe.id_rough.v1"
    ID_FINISH = "lathe.id_finish.v1"
    OD_GROOVE = "lathe.od_groove.v1"
    ID_GROOVE = "lathe.id_groove.v1"
    PART_OFF = "lathe.part_off.v1"
    OD_THREAD = "lathe.od_thread.v1"
    ID_THREAD = "lathe.id_thread.v1"
    AXIAL_DRILL = "lathe.axial_drill.v1"


class LatheStrategyFamily(StrEnum):
    """Exact Lathe V1 strategy-family identifiers."""

    TURNING = "lathe.family.turning.v1"
    GROOVING = "lathe.family.grooving.v1"
    THREADING = "lathe.family.threading.v1"
    HOLE_MAKING = "lathe.family.hole_making.v1"


class LatheToolCapability(StrEnum):
    """Capabilities explicitly supplied by an injected Tool resolver."""

    FACE_TURNING = "FACE_TURNING"
    OD_TURNING = "OD_TURNING"
    ID_TURNING = "ID_TURNING"
    OD_GROOVING = "OD_GROOVING"
    ID_GROOVING = "ID_GROOVING"
    PARTING = "PARTING"
    OD_THREADING = "OD_THREADING"
    ID_THREADING = "ID_THREADING"
    AXIAL_DRILLING = "AXIAL_DRILLING"


class LatheGeometryKind(StrEnum):
    """Kernel-neutral geometry kinds accepted by Lathe V1."""

    AXIS = "lathe.geometry.axis.v1"
    PROFILE = "lathe.geometry.profile.v1"
    FACE = "lathe.geometry.face.v1"
    EDGE = "lathe.geometry.edge.v1"
    CYLINDER = "lathe.geometry.cylinder.v1"
    POINT = "lathe.geometry.point.v1"


class LatheParameterGroup(StrEnum):
    BASIC = "BASIC"
    ADVANCED = "ADVANCED"


class LatheParameterValueKind(StrEnum):
    FLOAT = "FLOAT"
    INTEGER = "INTEGER"
    ENUM = "ENUM"


class LatheParameterUnitKind(StrEnum):
    NONE = "none"
    MILLIMETRE = "mm"
    RPM = "rpm"
    MM_PER_REVOLUTION = "mm/rev"
    DEGREE = "degree"
    SECOND = "second"


class LatheSpindleDirection(StrEnum):
    CW = "CW"
    CCW = "CCW"


class LatheThreadHand(StrEnum):
    RIGHT = "RIGHT"
    LEFT = "LEFT"


class LatheOperationReadiness(StrEnum):
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"
    READY = "READY"


class LatheWorkspaceReadinessState(StrEnum):
    FOUNDATION_UNAVAILABLE = "FOUNDATION_UNAVAILABLE"
    FOUNDATION_READY = "FOUNDATION_READY"
    PRESENTER_IMPLEMENTATION_ALLOWED = "PRESENTER_IMPLEMENTATION_ALLOWED"
    PRESENTER_ACTIVE = "PRESENTER_ACTIVE"


class LatheWorkspaceReadinessReason(StrEnum):
    FOUNDATION_NOT_READY = "foundation_not_ready"
    PRESENTER_NOT_IMPLEMENTED = "presenter_not_implemented"
    NONE = "none"


class LatheStage9A9State(StrEnum):
    BLOCKED = "BLOCKED"
    UNBLOCKED_FOR_IMPLEMENTATION = "UNBLOCKED_FOR_IMPLEMENTATION"
    COMPLETE = "COMPLETE"


class LatheDiagnosticCode(StrEnum):
    """Stable, localization-neutral Lathe diagnostic codes."""

    MISSING_SETUP = "missing_setup"
    MISSING_GEOMETRY = "missing_geometry"
    MISSING_TOOL = "missing_tool"
    INCOMPATIBLE_TOOL = "incompatible_tool"
    INCOMPATIBLE_GEOMETRY = "incompatible_geometry"
    INVALID_PARAMETER = "invalid_parameter"
    STALE_OWNERSHIP = "stale_ownership"
    READ_ONLY = "read_only"
    CLOSED = "closed"
    UNKNOWN_STRATEGY = "unknown_strategy"
    DISABLED_OPERATION = "disabled_operation"
    REVISION_MISMATCH = "revision_mismatch"
    DUPLICATE_OPERATION = "duplicate_operation"
    OPERATION_NOT_FOUND = "operation_not_found"


@dataclass(frozen=True, slots=True)
class LatheDiagnostic:
    """One immutable semantic diagnostic without visible text."""

    code: LatheDiagnosticCode
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    field_id: str | None = None
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, LatheDiagnosticCode):
            raise TypeError("Lathe diagnostic code must be LatheDiagnosticCode")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise TypeError("Lathe diagnostic severity must be DiagnosticSeverity")
        if self.field_id is not None and (
            not isinstance(self.field_id, str) or not self.field_id.strip()
        ):
            raise ValueError("Lathe diagnostic field_id must be non-blank")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], str)
            for item in self.parameters
        ):
            raise TypeError("Lathe diagnostic parameters must be string pairs")
        ordered = tuple(sorted(self.parameters))
        if len({key for key, _value in ordered}) != len(ordered):
            raise ValueError("Lathe diagnostic parameter keys must be unique")
        object.__setattr__(self, "parameters", ordered)


def ordered_lathe_diagnostics(
    diagnostics: tuple[LatheDiagnostic, ...] | list[LatheDiagnostic],
) -> tuple[LatheDiagnostic, ...]:
    """Return deterministic, duplicate-free diagnostics."""

    if any(not isinstance(item, LatheDiagnostic) for item in diagnostics):
        raise TypeError("Lathe diagnostics must contain LatheDiagnostic values")
    return tuple(
        sorted(
            set(diagnostics),
            key=lambda item: (
                item.code.value,
                item.field_id or "",
                item.severity.value,
                item.parameters,
            ),
        )
    )


__all__ = [
    "LatheDiagnostic",
    "LatheDiagnosticCode",
    "LatheGeometryKind",
    "LatheOperationReadiness",
    "LatheParameterGroup",
    "LatheParameterUnitKind",
    "LatheParameterValueKind",
    "LatheSpindleDirection",
    "LatheStage9A9State",
    "LatheStrategyFamily",
    "LatheStrategyId",
    "LatheThreadHand",
    "LatheToolCapability",
    "LatheWorkspaceReadinessReason",
    "LatheWorkspaceReadinessState",
    "ordered_lathe_diagnostics",
]
