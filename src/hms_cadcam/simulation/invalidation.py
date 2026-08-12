"""Dependency-aware incremental invalidation for machining simulation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint


class ArtifactKind(StrEnum):
    SAMPLED_TOOLPATH = "sampled_toolpath"
    SWEPT_VOLUME = "swept_volume"
    INTERMEDIATE_STOCK = "intermediate_stock"
    COLLISION = "collision"
    FINAL_STOCK = "final_stock"


@dataclass(frozen=True, slots=True)
class InvalidationPlan:
    invalidated_operations: tuple[str, ...]
    retained_operations: tuple[str, ...]
    invalidated_artifacts: tuple[ArtifactKind, ...]
    material_recomputation_required: bool
    reason: str


class SimulationDependencyGraph:
    """Operation-order graph with presentation inputs explicitly excluded."""

    def __init__(
        self,
        operation_fingerprints: tuple[tuple[str, ContentFingerprint], ...],
    ) -> None:
        if not operation_fingerprints:
            raise CamValidationError("Simulation dependency graph needs operations")
        identities = [identity for identity, _ in operation_fingerprints]
        if len(set(identities)) != len(identities):
            raise CamValidationError("Simulation operation identities are duplicated")
        if any(not isinstance(fp, ContentFingerprint) for _, fp in operation_fingerprints):
            raise CamValidationError("Simulation operation fingerprint is invalid")
        self._operations = operation_fingerprints

    def presentation_change(self, reason: str) -> InvalidationPlan:
        return InvalidationPlan((), tuple(key for key, _ in self._operations), (), False, reason)

    def operation_change(
        self,
        operation_id: str,
        fingerprint: ContentFingerprint,
    ) -> InvalidationPlan:
        try:
            index = next(i for i, (key, _) in enumerate(self._operations) if key == operation_id)
        except StopIteration as error:
            raise CamValidationError("Changed operation is outside the session") from error
        if self._operations[index][1] == fingerprint:
            return self.presentation_change("operation unchanged")
        invalidated = tuple(key for key, _ in self._operations[index:])
        retained = tuple(key for key, _ in self._operations[:index])
        return InvalidationPlan(
            invalidated,
            retained,
            (
                ArtifactKind.SAMPLED_TOOLPATH,
                ArtifactKind.SWEPT_VOLUME,
                ArtifactKind.INTERMEDIATE_STOCK,
                ArtifactKind.COLLISION,
                ArtifactKind.FINAL_STOCK,
            ),
            True,
            "operation or downstream material state changed",
        )

    def tool_or_holder_change(self, operation_id: str) -> InvalidationPlan:
        return self.operation_change(
            operation_id,
            ContentFingerprint.from_payload({"force": "tool_or_holder_changed"}),
        )
