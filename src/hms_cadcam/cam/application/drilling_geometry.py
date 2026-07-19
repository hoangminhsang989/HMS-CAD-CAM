"""Drilling geometry resolver composed over persistent native adapters."""

from __future__ import annotations

import logging
from typing import Protocol

from hms_cadcam.cam.domain import (
    DiagnosticCode,
    DiagnosticSeverity,
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillingRegion,
    DrillValidationError,
    GeometryFingerprint,
    GeometryResolutionStatus,
    HolePattern,
    HoleReference,
    ResolvedDrillingGeometry,
    ResolvedHoleLocation,
    ValidationDiagnostic,
)

logger = logging.getLogger(__name__)


class HoleReferenceResolverPort(Protocol):
    """Narrow native adapter port for one persistent VERTEX or circular EDGE."""

    def resolve(self, reference: HoleReference) -> ResolvedHoleLocation:
        """Resolve one native-free normalized location."""
        ...


class DrillingGeometryResolver:
    """Resolve explicit patterns or persistent hole references fail-closed."""

    def __init__(self, reference_resolver: HoleReferenceResolverPort | None = None) -> None:
        if reference_resolver is not None and not callable(
            getattr(reference_resolver, "resolve", None)
        ):
            raise TypeError("Drilling reference resolver is invalid")
        self._reference_resolver = reference_resolver

    def resolve(
        self,
        geometry_input: DrillGeometryInput,
        depth: DrillDepthDefinition,
    ) -> ResolvedDrillingGeometry:
        """Return one immutable region without leaking native or runtime objects."""
        if not isinstance(geometry_input, DrillGeometryInput):
            return _failure(
                GeometryResolutionStatus.MISSING,
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Drilling geometry input is missing",
            )
        if not isinstance(depth, DrillDepthDefinition):
            return _failure(
                GeometryResolutionStatus.INVALID,
                DiagnosticCode.DRILL_INVALID_DEPTH,
                "Drilling depth is invalid",
            )
        if geometry_input.unit is not depth.unit:
            return _failure(
                GeometryResolutionStatus.INVALID,
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drilling geometry and depth units do not match",
            )
        source = geometry_input.source
        if isinstance(source, HolePattern):
            pattern = source
            source_fingerprint = GeometryFingerprint.from_payload({
                "explicit_pattern": source.fingerprint.to_dict(),
            })
        else:
            if not isinstance(source, HoleReference) or self._reference_resolver is None:
                return _failure(
                    GeometryResolutionStatus.MISSING,
                    DiagnosticCode.DRILL_GEOMETRY_MISSING,
                    "Persistent drilling reference cannot be resolved",
                )
            try:
                resolved = self._reference_resolver.resolve(source)
            except Exception:
                logger.exception("Persistent drilling reference resolver failed")
                return _failure(
                    GeometryResolutionStatus.INVALID,
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Persistent drilling geometry could not be resolved safely",
                )
            if not isinstance(resolved, ResolvedHoleLocation):
                return _failure(
                    GeometryResolutionStatus.INVALID,
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Drilling resolver returned an invalid result",
                )
            if resolved.status is not GeometryResolutionStatus.RESOLVED:
                return ResolvedDrillingGeometry(
                    resolved.status,
                    diagnostics=resolved.diagnostics,
                )
            location = resolved.location
            assert location is not None
            if location.reference != source:
                return _failure(
                    GeometryResolutionStatus.SOURCE_MISMATCH,
                    DiagnosticCode.DRILL_SOURCE_MISMATCH,
                    "Resolved hole does not match its persistent reference",
                )
            try:
                pattern = HolePattern((location,), geometry_input.unit)
            except DrillValidationError as error:
                return _failure(GeometryResolutionStatus.INVALID, error.code, str(error))
            source_fingerprint = location.fingerprint
        try:
            region = DrillingRegion(
                geometry_input,
                pattern,
                depth,
                geometry_input.unit,
                source_fingerprint,
            )
        except DrillValidationError as error:
            return _failure(GeometryResolutionStatus.INVALID, error.code, str(error))
        return ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)


def _failure(
    status: GeometryResolutionStatus,
    code: DiagnosticCode,
    message: str,
) -> ResolvedDrillingGeometry:
    return ResolvedDrillingGeometry(
        status,
        diagnostics=(ValidationDiagnostic(DiagnosticSeverity.ERROR, code, message),),
    )
