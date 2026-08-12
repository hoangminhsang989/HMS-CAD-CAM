"""R241 evidence aggregation and target-surface comparison."""

from __future__ import annotations

from dataclasses import dataclass

from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.simulation.model import (
    SimulationIssueCode,
    SimulationResult as CollisionResult,
)
from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot

from .contracts import (
    SIMULATION_ENGINE_VERSION,
    CollisionKind,
    EngineKind,
    GougeStatus,
    OperationCoverage,
    QualityMode,
    ResultState,
    SimulationEvidence,
    SimulationSession,
    StageTiming,
    result_fingerprint,
)
from .heightfield import HeightFieldResult


@dataclass(frozen=True, slots=True)
class SurfaceComparison:
    status: GougeStatus
    maximum_gouge_depth: float
    maximum_remaining_height: float
    compared_cells: int


def compare_target_surface(
    result: HeightFieldResult,
    target_heights: tuple[float, ...] | None,
    *,
    tolerance: float,
) -> SurfaceComparison:
    """Compare a deterministic target height field without claiming B-Rep exactness."""

    if target_heights is None:
        return SurfaceComparison(GougeStatus.GEOMETRY_REFERENCE_UNAVAILABLE, 0.0, 0.0, 0)
    remaining = result.remaining_stock
    if len(target_heights) != len(remaining.top_heights):
        raise ValueError("Target surface grid differs from remaining-stock grid")
    gouge = max((target - actual for target, actual in zip(target_heights, remaining.top_heights, strict=True)), default=0.0)
    excess = max((actual - target for target, actual in zip(target_heights, remaining.top_heights, strict=True)), default=0.0)
    if gouge > tolerance:
        status = GougeStatus.GOUGE_DETECTED
    elif excess > tolerance:
        status = GougeStatus.REMAINING_MATERIAL
    else:
        status = GougeStatus.NO_GOUGE_FOUND
    return SurfaceComparison(status, max(0.0, gouge), max(0.0, excess), len(target_heights))


def session_from_input(
    inputs: SimulationInputSnapshot,
    quality: QualityMode,
    *,
    project_fingerprint: ContentFingerprint,
    coverage: OperationCoverage = OperationCoverage.SINGLE_OPERATION,
) -> SimulationSession:
    fixture_fingerprints = tuple(
        sorted(
            (
                str(fixture.fixture_id),
                fixture.geometry_reference.expected_geometry_fingerprint,
            )
            for fixture in inputs.setup.fixtures
            if fixture.enabled
        )
    )
    return SimulationSession(
        project_fingerprint=project_fingerprint,
        part_fingerprint=inputs.setup.model_reference.expected_geometry_fingerprint,
        stock_fingerprint=inputs.request.stock_fingerprint,
        wcs_fingerprint=inputs.request.wcs_fingerprint,
        operation_fingerprints=((str(inputs.operation.operation_id), inputs.request.artifact_fingerprint),),
        tool_fingerprints=((str(inputs.tool.tool_id), inputs.tool.content_fingerprint),),
        holder_fingerprints=((
            str(inputs.assembly.assembly_id),
            None if inputs.holder is None else inputs.holder.content_fingerprint,
        ),),
        fixture_fingerprints=fixture_fingerprints,
        settings_fingerprint=ContentFingerprint.from_payload({"quality": quality.value}),
        engine_fingerprint=ContentFingerprint.from_payload({
            "engine": EngineKind.HEIGHTFIELD_3AXIS.value,
            "version": SIMULATION_ENGINE_VERSION,
        }),
        coverage=coverage,
    )


def build_evidence(
    *,
    session: SimulationSession,
    material: HeightFieldResult,
    comparison: SurfaceComparison,
    timings: tuple[StageTiming, ...],
    collision: CollisionResult | None = None,
) -> SimulationEvidence:
    collisions = _collision_kinds(collision)
    warnings: list[str] = []
    if collision is None:
        collisions = (CollisionKind.UNVERIFIED_GEOMETRY,)
        warnings.append("Collision analysis was not supplied for this result.")
    if any(value is None for _key, value in session.holder_fingerprints):
        if CollisionKind.UNVERIFIED_GEOMETRY not in collisions:
            collisions = (*collisions, CollisionKind.UNVERIFIED_GEOMETRY)
        warnings.append("Holder geometry is unknown; Holder collision scope is unverified.")
    if comparison.status is GougeStatus.GEOMETRY_REFERENCE_UNAVAILABLE:
        warnings.append("Target geometry comparison is unavailable.")
    failed = (
        comparison.status is GougeStatus.GOUGE_DETECTED
        or any(
            value in {
                CollisionKind.TOOL_COLLISION,
                CollisionKind.HOLDER_COLLISION,
                CollisionKind.FIXTURE_COLLISION,
            }
            for value in collisions
        )
    )
    warning = bool(warnings) or comparison.status is GougeStatus.REMAINING_MATERIAL
    if failed:
        state = ResultState.FAIL
    elif session.coverage is not OperationCoverage.COMPLETE_JOB:
        state = ResultState.PARTIAL
    elif warning:
        state = ResultState.WARNING
    else:
        state = ResultState.PASS
    payload = {
        "session": session.fingerprint.to_dict(),
        "remaining_stock": {
            "width": material.remaining_stock.width,
            "height": material.remaining_stock.height,
            "removed_volume": material.remaining_stock.removed_volume,
            "remaining_volume": material.remaining_stock.remaining_volume,
            "heights": list(material.remaining_stock.top_heights),
        },
        "collisions": [value.value for value in collisions],
        "gouge": comparison.status.value,
        "coverage": session.coverage.value,
        "state": state.value,
    }
    inputs = tuple(sorted((
        ("part", session.part_fingerprint),
        ("stock", session.stock_fingerprint),
        ("wcs", session.wcs_fingerprint),
        *session.operation_fingerprints,
        *session.tool_fingerprints,
    ), key=lambda item: item[0]))
    return SimulationEvidence(
        session.fingerprint,
        EngineKind.HEIGHTFIELD_3AXIS,
        SIMULATION_ENGINE_VERSION,
        material.quality,
        inputs,
        tuple(key for key, _value in session.operation_fingerprints),
        session.coverage,
        timings,
        tuple(warnings),
        tuple(dict.fromkeys(collisions)),
        comparison.status,
        True,
        result_fingerprint(payload),
        state,
        "Bounded CPU height-field approximation for fixed-axis, top-down 3-axis milling; not exact B-Rep or physical-machine qualification.",
    )


def _collision_kinds(result: CollisionResult | None) -> tuple[CollisionKind, ...]:
    if result is None:
        return ()
    mapped: list[CollisionKind] = []
    for issue in result.issues:
        if issue.code in {SimulationIssueCode.HOLDER_FIXTURE_COLLISION, SimulationIssueCode.HOLDER_STOCK_COLLISION}:
            mapped.append(CollisionKind.HOLDER_COLLISION)
        elif issue.code in {SimulationIssueCode.TOOL_FIXTURE_COLLISION, SimulationIssueCode.SHANK_FIXTURE_COLLISION}:
            mapped.append(CollisionKind.FIXTURE_COLLISION)
        elif issue.code in {SimulationIssueCode.SHANK_STOCK_COLLISION, SimulationIssueCode.GOUGE_DETECTED}:
            mapped.append(CollisionKind.TOOL_COLLISION)
    return tuple(dict.fromkeys(mapped)) or (CollisionKind.NO_COLLISION_FOUND,)
