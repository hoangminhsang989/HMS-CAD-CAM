"""Deterministic Lathe Toolpath Preview V1 request and fail-closed builder."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Mapping

from hms_cadcam.cam.domain.ids import OperationId
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.lathe.application import LatheOperationService
from hms_cadcam.cam.lathe.domain import (
    LatheGeometryBinding,
    LatheOperationState,
    LatheOwnershipKey,
    LatheToolBinding,
)
from hms_cadcam.cam.lathe.types import (
    LatheDiagnosticCode,
    LatheOperationReadiness,
    LatheStrategyId,
)
from hms_cadcam.cam.lathe.toolpath.model import (
    LATHE_AXIAL_DRILL_ALGORITHM_VERSION,
    LATHE_FACE_ALGORITHM_VERSION,
    LATHE_ID_FINISH_ALGORITHM_VERSION,
    LATHE_ID_GROOVE_ALGORITHM_VERSION,
    LATHE_ID_ROUGH_ALGORITHM_VERSION,
    LATHE_ID_THREAD_ALGORITHM_VERSION,
    LATHE_OD_FINISH_ALGORITHM_VERSION,
    LATHE_OD_GROOVE_ALGORITHM_VERSION,
    LATHE_OD_ROUGH_ALGORITHM_VERSION,
    LATHE_OD_THREAD_ALGORITHM_VERSION,
    LATHE_PART_OFF_ALGORITHM_VERSION,
    LATHE_TOOLPATH_ALGORITHM_VERSION,
    LatheToolpathCacheKey,
    LatheToolpathDiagnostic,
    LatheToolpathDiagnosticCode,
    LatheToolpathFingerprint,
    LatheToolpathJobId,
)
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1

LATHE_TOOLPATH_REQUEST_CONTRACT_VERSION = 1
LATHE_TOOLPATH_CACHE_KEY_CONTRACT_VERSION = 1

EXECUTABLE_LATHE_TOOLPATH_STRATEGIES: tuple[LatheStrategyId, ...] = (
    LatheStrategyId.FACE,
    LatheStrategyId.OD_ROUGH,
    LatheStrategyId.OD_FINISH,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
    LatheStrategyId.OD_THREAD,
    LatheStrategyId.ID_THREAD,
    LatheStrategyId.AXIAL_DRILL,
)
UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES: tuple[LatheStrategyId, ...] = ()

_ALGORITHM_BY_STRATEGY: Mapping[LatheStrategyId, str] = MappingProxyType(
    {
        LatheStrategyId.FACE: LATHE_FACE_ALGORITHM_VERSION,
        LatheStrategyId.OD_ROUGH: LATHE_OD_ROUGH_ALGORITHM_VERSION,
        LatheStrategyId.OD_FINISH: LATHE_OD_FINISH_ALGORITHM_VERSION,
        LatheStrategyId.ID_ROUGH: LATHE_ID_ROUGH_ALGORITHM_VERSION,
        LatheStrategyId.ID_FINISH: LATHE_ID_FINISH_ALGORITHM_VERSION,
        LatheStrategyId.OD_GROOVE: LATHE_OD_GROOVE_ALGORITHM_VERSION,
        LatheStrategyId.ID_GROOVE: LATHE_ID_GROOVE_ALGORITHM_VERSION,
        LatheStrategyId.PART_OFF: LATHE_PART_OFF_ALGORITHM_VERSION,
        LatheStrategyId.OD_THREAD: LATHE_OD_THREAD_ALGORITHM_VERSION,
        LatheStrategyId.ID_THREAD: LATHE_ID_THREAD_ALGORITHM_VERSION,
        LatheStrategyId.AXIAL_DRILL: LATHE_AXIAL_DRILL_ALGORITHM_VERSION,
    }
)


def strategy_algorithm_version(strategy_id: LatheStrategyId) -> str:
    if not isinstance(strategy_id, LatheStrategyId):
        raise TypeError("Lathe toolpath strategy ID is invalid")
    try:
        return _ALGORITHM_BY_STRATEGY[strategy_id]
    except KeyError as error:
        raise ValueError("Lathe toolpath strategy is unsupported") from error


def _geometry_payload(binding: LatheGeometryBinding) -> dict[str, object]:
    return {
        "kind": binding.kind.value,
        "entity_ids": list(binding.entity_ids),
        "source_id": str(binding.source_id),
        "generation": binding.generation,
    }


def _tool_payload(binding: LatheToolBinding) -> dict[str, object]:
    return {
        "tool_id": str(binding.tool_id),
        "profile_id": str(binding.profile_id) if binding.profile_id is not None else None,
        "assembly_id": str(binding.assembly_id),
        "resolved_capabilities": sorted(
            item.value for item in binding.resolved_capabilities
        ),
        "tool_revision": binding.tool_revision.value,
        "profile_revision": (
            binding.profile_revision.value
            if binding.profile_revision is not None
            else None
        ),
        "assembly_revision": binding.assembly_revision.value,
    }


@dataclass(frozen=True, slots=True)
class LatheToolpathOperationSnapshotV1:
    """Minimum immutable operation semantics copied at explicit submission."""

    ownership: LatheOwnershipKey
    strategy_id: LatheStrategyId
    parameter_values: tuple[tuple[str, object], ...]
    geometry_binding: LatheGeometryBinding
    tool_binding: LatheToolBinding
    enabled: bool
    revision: Revision

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, LatheOwnershipKey):
            raise TypeError("Lathe toolpath operation ownership is invalid")
        if not isinstance(self.strategy_id, LatheStrategyId):
            raise TypeError("Lathe toolpath operation strategy is invalid")
        if not isinstance(self.parameter_values, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            for item in self.parameter_values
        ):
            raise TypeError("Lathe toolpath parameter snapshot is invalid")
        if len({key for key, _value in self.parameter_values}) != len(
            self.parameter_values
        ):
            raise ValueError("Lathe toolpath parameter IDs are duplicated")
        for _key, value in self.parameter_values:
            if value is not None and type(value) not in {str, int, float}:
                raise TypeError("Lathe toolpath parameters must be JSON primitives")
        if not isinstance(self.geometry_binding, LatheGeometryBinding):
            raise TypeError("Lathe toolpath geometry binding is invalid")
        if not isinstance(self.tool_binding, LatheToolBinding):
            raise TypeError("Lathe toolpath Tool binding is invalid")
        if type(self.enabled) is not bool:
            raise TypeError("Lathe toolpath enabled state is invalid")
        if not isinstance(self.revision, Revision):
            raise TypeError("Lathe toolpath operation revision is invalid")

    @classmethod
    def from_state(
        cls, operation: LatheOperationState
    ) -> "LatheToolpathOperationSnapshotV1":
        if not isinstance(operation, LatheOperationState):
            raise TypeError("Lathe toolpath operation state is invalid")
        if operation.geometry_binding is None or operation.tool_binding is None:
            raise ValueError("Lathe toolpath operation bindings are incomplete")
        return cls(
            operation.ownership,
            operation.strategy_id,
            operation.parameter_state.canonical_values(),
            operation.geometry_binding,
            operation.tool_binding,
            operation.enabled,
            operation.revision,
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return MappingProxyType(dict(self.parameter_values))

    def canonical_payload(self) -> dict[str, object]:
        ownership = self.ownership
        return {
            "ownership": {
                "project_id": str(ownership.project_id),
                "document_id": str(ownership.document_id),
                "source_id": str(ownership.source_id),
                "generation": ownership.generation,
                "setup_id": str(ownership.setup_id),
                "operation_id": str(ownership.operation_id),
            },
            "strategy_id": self.strategy_id.value,
            "parameter_values": [
                {"parameter_id": key, "value": value}
                for key, value in self.parameter_values
            ],
            "geometry_binding": _geometry_payload(self.geometry_binding),
            "tool_binding": _tool_payload(self.tool_binding),
            "enabled": self.enabled,
            "revision": self.revision.value,
        }


def _semantic_payload(
    operation: LatheToolpathOperationSnapshotV1,
    stock: LatheStockSnapshotV1,
    algorithm_version: str,
) -> dict[str, object]:
    return {
        "format": "HMS_LATHE_TOOLPATH_REQUEST",
        "format_version": LATHE_TOOLPATH_REQUEST_CONTRACT_VERSION,
        "global_algorithm_version": LATHE_TOOLPATH_ALGORITHM_VERSION,
        "strategy_algorithm_version": algorithm_version,
        "operation": operation.canonical_payload(),
        "stock": stock.canonical_payload(),
    }


def _sha256_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_for(
    operation: LatheToolpathOperationSnapshotV1,
    stock: LatheStockSnapshotV1,
    algorithm_version: str,
) -> LatheToolpathFingerprint:
    return LatheToolpathFingerprint(
        LATHE_TOOLPATH_REQUEST_CONTRACT_VERSION,
        _sha256_payload(_semantic_payload(operation, stock, algorithm_version)),
    )


def _cache_key_for(
    fingerprint: LatheToolpathFingerprint,
    algorithm_version: str,
) -> LatheToolpathCacheKey:
    return LatheToolpathCacheKey(
        LATHE_TOOLPATH_CACHE_KEY_CONTRACT_VERSION,
        _sha256_payload(
            {
                "format": "HMS_LATHE_TOOLPATH_CACHE_KEY",
                "format_version": LATHE_TOOLPATH_CACHE_KEY_CONTRACT_VERSION,
                "global_algorithm_version": LATHE_TOOLPATH_ALGORITHM_VERSION,
                "strategy_algorithm_version": algorithm_version,
                "request_fingerprint": fingerprint.digest,
            }
        ),
    )


@dataclass(frozen=True, slots=True)
class LatheToolpathRequestV1:
    job_id: LatheToolpathJobId
    request_sequence: int
    operation: LatheToolpathOperationSnapshotV1
    stock: LatheStockSnapshotV1
    algorithm_version: str
    fingerprint: LatheToolpathFingerprint
    cache_key: LatheToolpathCacheKey

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, LatheToolpathJobId):
            raise TypeError("Lathe request job identity is invalid")
        if type(self.request_sequence) is not int or self.request_sequence < 0:
            raise ValueError("Lathe request sequence must be non-negative")
        if not isinstance(self.operation, LatheToolpathOperationSnapshotV1):
            raise TypeError("Lathe request operation snapshot is invalid")
        if not isinstance(self.stock, LatheStockSnapshotV1):
            raise TypeError("Lathe request stock snapshot is invalid")
        if self.algorithm_version != strategy_algorithm_version(
            self.operation.strategy_id
        ):
            raise ValueError("Lathe request algorithm version is invalid")
        expected_fingerprint = _fingerprint_for(
            self.operation, self.stock, self.algorithm_version
        )
        if self.fingerprint != expected_fingerprint:
            raise ValueError("Lathe request fingerprint does not match semantics")
        if self.cache_key != _cache_key_for(
            self.fingerprint, self.algorithm_version
        ):
            raise ValueError("Lathe request cache key does not match fingerprint")

    @property
    def ownership(self) -> LatheOwnershipKey:
        return self.operation.ownership

    @property
    def strategy_id(self) -> LatheStrategyId:
        return self.operation.strategy_id


@dataclass(frozen=True, slots=True)
class LatheToolpathRequestBuildResult:
    request: LatheToolpathRequestV1 | None
    diagnostics: tuple[LatheToolpathDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.request is not None and not isinstance(
            self.request, LatheToolpathRequestV1
        ):
            raise TypeError("Lathe request build result request is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, LatheToolpathDiagnostic)
            for item in self.diagnostics
        ):
            raise TypeError("Lathe request build diagnostics are invalid")
        if (self.request is None) == (not self.diagnostics):
            raise ValueError("Lathe request build result requires request xor diagnostics")

    @property
    def accepted(self) -> bool:
        return self.request is not None


def _failure(
    code: LatheToolpathDiagnosticCode,
    *,
    field_id: str | None = None,
    **details: object,
) -> LatheToolpathRequestBuildResult:
    return LatheToolpathRequestBuildResult(
        None,
        (
            LatheToolpathDiagnostic(
                code,
                field_id,
                tuple((key, str(value)) for key, value in details.items()),
            ),
        ),
    )


_DOMAIN_DIAGNOSTIC_MAP: Mapping[LatheDiagnosticCode, LatheToolpathDiagnosticCode] = (
    MappingProxyType(
        {
            LatheDiagnosticCode.MISSING_GEOMETRY: LatheToolpathDiagnosticCode.MISSING_GEOMETRY,
            LatheDiagnosticCode.MISSING_TOOL: LatheToolpathDiagnosticCode.MISSING_TOOL,
            LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY: LatheToolpathDiagnosticCode.INCOMPATIBLE_GEOMETRY,
            LatheDiagnosticCode.INCOMPATIBLE_TOOL: LatheToolpathDiagnosticCode.INCOMPATIBLE_TOOL,
            LatheDiagnosticCode.STALE_OWNERSHIP: LatheToolpathDiagnosticCode.STALE_OWNERSHIP,
            LatheDiagnosticCode.READ_ONLY: LatheToolpathDiagnosticCode.READ_ONLY,
            LatheDiagnosticCode.CLOSED: LatheToolpathDiagnosticCode.CLOSED,
            LatheDiagnosticCode.DISABLED_OPERATION: LatheToolpathDiagnosticCode.DISABLED_OPERATION,
            LatheDiagnosticCode.INVALID_PARAMETER: LatheToolpathDiagnosticCode.INVALID_PARAMETER,
        }
    )
)


_THREAD_PARAMETER_DIAGNOSTICS: Mapping[str, LatheToolpathDiagnosticCode] = (
    MappingProxyType(
        {
            "pitch_mm": LatheToolpathDiagnosticCode.INVALID_PITCH,
            "pass_count": LatheToolpathDiagnosticCode.INVALID_PASS_COUNT,
            "spring_passes": LatheToolpathDiagnosticCode.INVALID_SPRING_PASSES,
            "infeed_angle_deg": LatheToolpathDiagnosticCode.INVALID_INFEED_ANGLE,
            "major_diameter_mm": (
                LatheToolpathDiagnosticCode.INVALID_THREAD_DIAMETER_ORDER
            ),
            "minor_diameter_mm": (
                LatheToolpathDiagnosticCode.INVALID_THREAD_DIAMETER_ORDER
            ),
            "start_z_mm": LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
            "end_z_mm": LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
        }
    )
)


def _thread_readiness_diagnostic(
    diagnostic_code: LatheDiagnosticCode,
    field_id: str | None,
) -> LatheToolpathDiagnosticCode | None:
    if diagnostic_code is LatheDiagnosticCode.INCOMPATIBLE_TOOL:
        return LatheToolpathDiagnosticCode.INCOMPATIBLE_THREAD_TOOL
    if diagnostic_code is LatheDiagnosticCode.INCOMPATIBLE_GEOMETRY:
        return LatheToolpathDiagnosticCode.INCOMPATIBLE_THREAD_GEOMETRY
    if diagnostic_code is LatheDiagnosticCode.INVALID_PARAMETER and field_id:
        return _THREAD_PARAMETER_DIAGNOSTICS.get(field_id)
    return None


def _stock_parameter_diagnostic(
    operation: LatheOperationState,
    stock: LatheStockSnapshotV1,
) -> LatheToolpathDiagnostic | None:
    parameters = operation.parameter_state.mapping
    minimum_z = min(stock.front_z_mm, stock.back_z_mm)
    maximum_z = max(stock.front_z_mm, stock.back_z_mm)
    strategy_id = operation.strategy_id

    if strategy_id is LatheStrategyId.FACE:
        outer = float(parameters["outer_diameter_mm"])
        inner = float(parameters["inner_diameter_mm"])
        if outer > stock.outer_diameter_mm:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "outer_diameter_mm",
                (("rule", "outer_diameter_within_stock"),),
            )
        if stock.inner_diameter_mm > 0.0 and inner < stock.inner_diameter_mm:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "inner_diameter_mm",
                (("rule", "inner_diameter_at_or_above_bore"),),
            )
        face = float(parameters["face_z_mm"])
        effective = face - stock.axial_direction * float(
            parameters["finish_allowance_mm"]
        )
        if not minimum_z <= face <= maximum_z:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "face_z_mm",
                (("rule", "face_inside_stock_axial_range"),),
            )
        if not minimum_z <= effective <= maximum_z:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "finish_allowance_mm",
                (("rule", "effective_face_inside_stock"),),
            )
        return None

    if strategy_id in {
        LatheStrategyId.OD_ROUGH,
        LatheStrategyId.OD_FINISH,
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
    }:
        internal = strategy_id in {
            LatheStrategyId.ID_ROUGH,
            LatheStrategyId.ID_FINISH,
        }
        if internal and stock.inner_diameter_mm <= 0.0:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE,
                details=(("strategy_id", strategy_id.value),),
            )
        target = float(parameters["target_diameter_mm"])
        invalid_target = (
            target >= stock.outer_diameter_mm
            if internal
            else target > stock.outer_diameter_mm
        ) or target <= stock.inner_diameter_mm
        if invalid_target:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "target_diameter_mm",
                (("rule", "target_inside_stock_envelope"),),
            )
        start = float(parameters["start_z_mm"])
        end = float(parameters["end_z_mm"])
        if not (
            minimum_z <= start <= maximum_z
            and minimum_z <= end <= maximum_z
        ):
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "start_z_mm",
                (("rule", "axial_range_inside_stock"),),
            )
        if strategy_id in {
            LatheStrategyId.OD_ROUGH,
            LatheStrategyId.ID_ROUGH,
        }:
            radial_allowance = 2.0 * float(
                parameters["radial_stock_to_leave_mm"]
            )
            rough_target = (
                target - radial_allowance
                if internal
                else target + radial_allowance
            )
            if (
                internal and rough_target < stock.inner_diameter_mm
            ) or (not internal and rough_target > stock.outer_diameter_mm):
                return LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                    "radial_stock_to_leave_mm",
                    (
                        (
                            "rule",
                            (
                                "rough_target_at_or_above_stock_bore"
                                if internal
                                else "rough_target_below_stock_od"
                            ),
                        ),
                    ),
                )
            direction = 1.0 if end > start else -1.0
            effective_end = end - direction * float(
                parameters["axial_stock_to_leave_mm"]
            )
            if direction * (effective_end - start) <= 0.0:
                return LatheToolpathDiagnostic(
                    LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                    "axial_stock_to_leave_mm",
                    (("rule", "effective_end_beyond_start"),),
                )
        return None

    if strategy_id in {
        LatheStrategyId.OD_GROOVE,
        LatheStrategyId.ID_GROOVE,
    }:
        internal = strategy_id is LatheStrategyId.ID_GROOVE
        if internal and stock.inner_diameter_mm <= 0.0:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE,
                details=(("strategy_id", strategy_id.value),),
            )
        target = float(parameters["target_diameter_mm"])
        if (
            target <= stock.inner_diameter_mm
            or target >= stock.outer_diameter_mm
        ):
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "target_diameter_mm",
                (("rule", "target_inside_stock_envelope"),),
            )
        effective_width = float(parameters["groove_width_mm"]) - 2.0 * float(
            parameters["side_allowance_mm"]
        )
        if effective_width <= 0.0:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "side_allowance_mm",
                (("rule", "positive_effective_groove_width"),),
            )
        center = float(parameters["center_z_mm"])
        left = center - effective_width / 2.0
        right = center + effective_width / 2.0
        if left < minimum_z or right > maximum_z:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "center_z_mm",
                (("rule", "groove_inside_stock_axial_range"),),
            )
        return None

    if strategy_id is LatheStrategyId.PART_OFF:
        target = float(parameters["target_diameter_mm"])
        if target >= stock.outer_diameter_mm or (
            stock.inner_diameter_mm > 0.0
            and target < stock.inner_diameter_mm
        ):
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "target_diameter_mm",
                (("rule", "part_off_target_inside_stock_envelope"),),
            )
        cutoff = float(parameters["cutoff_z_mm"])
        approach = cutoff - stock.axial_direction * float(
            parameters["side_clearance_mm"]
        )
        if not minimum_z <= cutoff <= maximum_z:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "cutoff_z_mm",
                (("rule", "cutoff_inside_stock_axial_range"),),
            )
        if not minimum_z <= approach <= maximum_z:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "side_clearance_mm",
                (("rule", "approach_inside_stock_axial_range"),),
            )
        return None

    if strategy_id in {
        LatheStrategyId.OD_THREAD,
        LatheStrategyId.ID_THREAD,
    }:
        internal = strategy_id is LatheStrategyId.ID_THREAD
        if internal and stock.inner_diameter_mm <= 0.0:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE,
                details=(("strategy_id", strategy_id.value),),
            )
        major = float(parameters["major_diameter_mm"])
        minor = float(parameters["minor_diameter_mm"])
        if major <= minor:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_THREAD_DIAMETER_ORDER,
                "minor_diameter_mm",
            )
        if (internal and major >= stock.outer_diameter_mm) or (
            not internal and major > stock.outer_diameter_mm
        ):
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.THREAD_MAJOR_EXCEEDS_STOCK,
                "major_diameter_mm",
            )
        if minor < stock.inner_diameter_mm:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.THREAD_MINOR_BELOW_BORE,
                "minor_diameter_mm",
            )
        start = float(parameters["start_z_mm"])
        end = float(parameters["end_z_mm"])
        if not (
            minimum_z <= start <= maximum_z
            and minimum_z <= end <= maximum_z
        ):
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.THREAD_RANGE_OUTSIDE_STOCK,
                "start_z_mm",
            )
        return None

    if strategy_id is LatheStrategyId.AXIAL_DRILL:
        depth = float(parameters["depth_mm"])
        if depth > stock.axial_length_mm:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "depth_mm",
                (("rule", "depth_inside_stock"),),
            )
        retract_plane = float(parameters["retract_plane_z_mm"])
        if stock.axial_direction * (retract_plane - stock.front_z_mm) > 0.0:
            return LatheToolpathDiagnostic(
                LatheToolpathDiagnosticCode.INVALID_PARAMETER,
                "retract_plane_z_mm",
                (("rule", "retract_at_or_outside_front"),),
            )
    return None


class LatheToolpathRequestBuilder:
    """Build one immutable request from an authoritative Stage 12 service."""

    def build(
        self,
        *,
        service: LatheOperationService,
        operation_id: OperationId,
        expected_revision: Revision,
        stock: LatheStockSnapshotV1 | None,
        job_id: LatheToolpathJobId | None,
        request_sequence: int,
    ) -> LatheToolpathRequestBuildResult:
        if not isinstance(service, LatheOperationService):
            raise TypeError("Lathe request builder service is invalid")
        if not isinstance(operation_id, OperationId):
            raise TypeError("Lathe request builder operation identity is invalid")
        if not isinstance(expected_revision, Revision):
            raise TypeError("Lathe request builder revision is invalid")
        if stock is not None and not isinstance(stock, LatheStockSnapshotV1):
            raise TypeError("Lathe request builder stock is invalid")
        if job_id is not None and not isinstance(job_id, LatheToolpathJobId):
            raise TypeError("Lathe request builder job identity is invalid")
        if type(request_sequence) is not int or request_sequence < 0:
            raise ValueError("Lathe request sequence must be non-negative")
        if job_id is None:
            return _failure(LatheToolpathDiagnosticCode.INVALID_REQUEST, reason="job_id_missing")
        try:
            operation = service.query(operation_id)
        except KeyError:
            return _failure(
                LatheToolpathDiagnosticCode.INVALID_REQUEST,
                reason="operation_not_found",
            )
        if operation.revision != expected_revision:
            return _failure(
                LatheToolpathDiagnosticCode.REVISION_MISMATCH,
                expected=expected_revision.value,
                actual=operation.revision.value,
            )
        session = service.session
        if session.closed:
            return _failure(LatheToolpathDiagnosticCode.CLOSED)
        if session.read_only:
            return _failure(LatheToolpathDiagnosticCode.READ_ONLY)
        ownership = operation.ownership
        if (
            ownership.project_id != session.project_id
            or ownership.document_id != session.document_id
            or ownership.source_id != session.source_id
            or ownership.generation != session.generation
            or session.setup_id is None
            or ownership.setup_id != session.setup_id
        ):
            return _failure(LatheToolpathDiagnosticCode.STALE_OWNERSHIP)
        if operation.strategy_id in UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES:
            return _failure(
                LatheToolpathDiagnosticCode.THREAD_TOOLPATH_NOT_IMPLEMENTED_V2,
                strategy_id=operation.strategy_id.value,
            )
        if not operation.enabled:
            return _failure(LatheToolpathDiagnosticCode.DISABLED_OPERATION)
        if stock is None:
            return _failure(LatheToolpathDiagnosticCode.INVALID_STOCK, reason="missing")
        if stock.source_id != session.source_id or stock.generation != session.generation:
            return _failure(LatheToolpathDiagnosticCode.STALE_OWNERSHIP, subject="stock")
        evaluation = service.evaluate(operation_id)
        if evaluation.readiness is not LatheOperationReadiness.READY:
            first = evaluation.diagnostics[0] if evaluation.diagnostics else None
            thread_mapped = (
                _thread_readiness_diagnostic(first.code, first.field_id)
                if first is not None
                and operation.strategy_id
                in {LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD}
                else None
            )
            mapped = thread_mapped or (
                _DOMAIN_DIAGNOSTIC_MAP.get(
                    first.code, LatheToolpathDiagnosticCode.OPERATION_NOT_READY
                )
                if first is not None
                else LatheToolpathDiagnosticCode.OPERATION_NOT_READY
            )
            return _failure(
                mapped,
                field_id=None if first is None else first.field_id,
                readiness=evaluation.readiness.value,
            )
        parameter_issue = _stock_parameter_diagnostic(operation, stock)
        if parameter_issue is not None:
            return LatheToolpathRequestBuildResult(None, (parameter_issue,))
        try:
            snapshot = LatheToolpathOperationSnapshotV1.from_state(operation)
            algorithm_version = strategy_algorithm_version(operation.strategy_id)
            fingerprint = _fingerprint_for(snapshot, stock, algorithm_version)
            cache_key = _cache_key_for(fingerprint, algorithm_version)
            request = LatheToolpathRequestV1(
                job_id,
                request_sequence,
                snapshot,
                stock,
                algorithm_version,
                fingerprint,
                cache_key,
            )
        except (TypeError, ValueError):
            return _failure(LatheToolpathDiagnosticCode.INVALID_REQUEST)
        return LatheToolpathRequestBuildResult(request)


__all__ = [
    "EXECUTABLE_LATHE_TOOLPATH_STRATEGIES",
    "LATHE_TOOLPATH_CACHE_KEY_CONTRACT_VERSION",
    "LATHE_TOOLPATH_REQUEST_CONTRACT_VERSION",
    "UNSUPPORTED_LATHE_TOOLPATH_STRATEGIES",
    "LatheToolpathOperationSnapshotV1",
    "LatheToolpathRequestBuildResult",
    "LatheToolpathRequestBuilder",
    "LatheToolpathRequestV1",
    "strategy_algorithm_version",
]
