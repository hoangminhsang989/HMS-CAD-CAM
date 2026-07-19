"""Pocket geometry resolver composed over the existing Contour profile resolver."""

from __future__ import annotations

import logging
from typing import Protocol

from hms_cadcam.cam.domain import (
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryReference,
    GeometryResolutionStatus,
    PocketBoundary,
    PocketGeometryInput,
    PocketRegion,
    PocketValidationError,
    ResolvedContourProfile,
    ResolvedPocketGeometry,
    ValidationDiagnostic,
)

logger = logging.getLogger(__name__)


class ContourProfileResolverPort(Protocol):
    """Narrow port already implemented by the persistent Contour resolver."""

    def resolve(self, reference: GeometryReference) -> ResolvedContourProfile:
        """Resolve one persistent FACE/WIRE reference."""
        ...


class PocketGeometryResolver:
    """Map a verified Contour descriptor into the Pocket geometry contract."""

    def __init__(self, contour_resolver: ContourProfileResolverPort) -> None:
        if not callable(getattr(contour_resolver, "resolve", None)):
            raise TypeError("Pocket geometry requires a Contour profile resolver")
        self._contour_resolver = contour_resolver

    def resolve(self, geometry_input: PocketGeometryInput) -> ResolvedPocketGeometry:
        """Resolve fail-closed without exposing native geometry or runtime IDs."""
        if not isinstance(geometry_input, PocketGeometryInput):
            return _failure(GeometryResolutionStatus.MISSING,
                            DiagnosticCode.POCKET_PROFILE_MISSING,
                            "Pocket geometry input is missing")
        try:
            result = self._contour_resolver.resolve(geometry_input.reference)
        except Exception:
            logger.exception("Contour profile resolver failed while resolving Pocket geometry")
            return _failure(GeometryResolutionStatus.INVALID,
                            DiagnosticCode.POCKET_PROFILE_INVALID,
                            "Pocket profile could not be resolved safely")
        if not isinstance(result, ResolvedContourProfile):
            return _failure(GeometryResolutionStatus.INVALID,
                            DiagnosticCode.POCKET_PROFILE_INVALID,
                            "Contour resolver returned an invalid Pocket profile result")
        if result.status is not GeometryResolutionStatus.RESOLVED:
            return _failure(result.status, _pocket_code(result),
                            result.message or "Pocket profile resolution failed")
        descriptor = result.profile
        assert descriptor is not None
        if descriptor.reference != geometry_input.reference:
            return _failure(GeometryResolutionStatus.SOURCE_MISMATCH,
                            DiagnosticCode.POCKET_PROFILE_INVALID,
                            "Resolved Pocket profile does not match its persistent reference")
        if descriptor.inner_loops:
            return _failure(GeometryResolutionStatus.INVALID,
                            DiagnosticCode.POCKET_PROFILE_INVALID,
                            "Pocket v1 does not support islands or inner loops")
        if descriptor.unit is not geometry_input.unit:
            return _failure(GeometryResolutionStatus.INVALID,
                            DiagnosticCode.POCKET_UNIT_MISSING,
                            "Pocket profile and geometry input units do not match")
        try:
            boundary = PocketBoundary(descriptor.outer_loop, descriptor.unit)
            region = PocketRegion(
                descriptor.reference,
                boundary,
                descriptor.plane_origin,
                descriptor.x_axis,
                descriptor.y_axis,
                descriptor.normal,
                descriptor.bounds,
                descriptor.unit,
                descriptor.geometry_fingerprint,
                descriptor.provenance,
            )
        except PocketValidationError as error:
            return _failure(GeometryResolutionStatus.INVALID, error.code, str(error))
        return ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED, region)


def _pocket_code(result: ResolvedContourProfile) -> DiagnosticCode:
    direct = {
        DiagnosticCode.CONTOUR_UNSUPPORTED_CURVE: DiagnosticCode.POCKET_UNSUPPORTED_CURVE,
        DiagnosticCode.CONTOUR_SELF_INTERSECTION: DiagnosticCode.POCKET_SELF_INTERSECTION,
    }
    if result.diagnostic_code in direct:
        return direct[result.diagnostic_code]
    if result.status is GeometryResolutionStatus.MISSING:
        return DiagnosticCode.POCKET_PROFILE_MISSING
    if result.status in {GeometryResolutionStatus.STALE, GeometryResolutionStatus.TOPOLOGY_CHANGED}:
        return DiagnosticCode.POCKET_PROFILE_STALE
    return DiagnosticCode.POCKET_PROFILE_INVALID


def _failure(
    status: GeometryResolutionStatus,
    code: DiagnosticCode,
    message: str,
) -> ResolvedPocketGeometry:
    diagnostic = ValidationDiagnostic(DiagnosticSeverity.ERROR, code, message)
    return ResolvedPocketGeometry(status, diagnostics=(diagnostic,))
