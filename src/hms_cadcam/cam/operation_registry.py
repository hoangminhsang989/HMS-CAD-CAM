"""Official, versioned construction boundary for registered CAM operations.

R272 begins with Rest Contour only.  Generic :class:`Operation` persistence is
deliberately broader: a project containing an unknown future strategy must
still reopen as a generic record, while the application may not create that
unknown strategy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hms_cadcam.cam.domain import (
    CamNodeId, GeometryInputId, GeometryReferenceKind, MachineRequirement, Operation, OperationFamily,
    OperationCapability, OperationGeometryInput, OperationId, SetupId, ToolAssembly,
    ToolAssemblyReference,
)
from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.operation import GeometryInputRole
from hms_cadcam.cam.domain.contour import ContourProfileSource
from hms_cadcam.cam.domain.rest_contour import (
    REST_CONTOUR_PARAMETER_SCHEMA_VERSION, REST_CONTOUR_STRATEGY_KEY,
    REST_CONTOUR_STRATEGY_VERSION, RestContourParameters, RestContourProfileSelection,
)


@dataclass(frozen=True, slots=True)
class RegisteredOperationType:
    """The immutable public discovery contract for one known strategy."""

    strategy_key: str
    strategy_version: int
    parameter_schema_version: int

    def __post_init__(self) -> None:
        if (
            self.strategy_key != REST_CONTOUR_STRATEGY_KEY
            or self.strategy_version != REST_CONTOUR_STRATEGY_VERSION
            or self.parameter_schema_version != REST_CONTOUR_PARAMETER_SCHEMA_VERSION
        ):
            raise CamValidationError("Unsupported registered operation type")


class RestContourOperationRegistry:
    """Discover, create and validate the v1 Rest Contour operation type."""

    def __init__(self) -> None:
        self._registered: dict[tuple[str, int, int], RegisteredOperationType] = {}
        self.register_default()

    def register_default(self) -> "RestContourOperationRegistry":
        """Register the sole R272 creation type; repeated registration is safe."""
        definition = RegisteredOperationType(
            REST_CONTOUR_STRATEGY_KEY, REST_CONTOUR_STRATEGY_VERSION,
            REST_CONTOUR_PARAMETER_SCHEMA_VERSION,
        )
        self._registered[(definition.strategy_key, definition.strategy_version,
                          definition.parameter_schema_version)] = definition
        return self

    @property
    def registered_types(self) -> tuple[RegisteredOperationType, ...]:
        return tuple(sorted(self._registered.values(), key=lambda item: (
            item.strategy_key, item.strategy_version, item.parameter_schema_version,
        )))

    def is_registered(
        self, strategy_key: str, strategy_version: int = REST_CONTOUR_STRATEGY_VERSION,
        parameter_schema_version: int = REST_CONTOUR_PARAMETER_SCHEMA_VERSION,
    ) -> bool:
        return (strategy_key, strategy_version, parameter_schema_version) in self._registered

    def create(
        self, *, operation_id: OperationId, node_id: CamNodeId, setup_id: SetupId,
        parameters: RestContourParameters, profile: RestContourProfileSelection,
        dependency_operation_id: OperationId, tool_assembly: ToolAssembly,
        machine_requirement: MachineRequirement,
    ) -> Operation:
        """Create one generic aggregate record through the typed Rest codec.

        The producer identity is accepted here to force explicit source
        selection.  Its durable graph edge belongs to the owning OperationTree;
        callers must add ``DependencyEdge.material_state(producer, consumer)``
        atomically with this returned operation.
        """
        if not self.is_registered(REST_CONTOUR_STRATEGY_KEY):
            raise CamValidationError("Rest Contour operation type is not registered")
        if not isinstance(operation_id, OperationId) or not isinstance(node_id, CamNodeId) or not isinstance(setup_id, SetupId):
            raise CamValidationError("Rest Contour operation identity is invalid")
        if not isinstance(parameters, RestContourParameters) or not isinstance(profile, RestContourProfileSelection):
            raise CamValidationError("Rest Contour parameters or profile are invalid")
        expected_profile_kind = _rest_contour_profile_kind(parameters.profile_source)
        if profile.descriptor.reference.kind is not expected_profile_kind:
            raise CamValidationError("Rest Contour profile source and reference kind disagree")
        if not isinstance(dependency_operation_id, OperationId) or dependency_operation_id == operation_id:
            raise CamValidationError("Rest Contour material-state dependency is invalid")
        if not isinstance(tool_assembly, ToolAssembly):
            raise CamValidationError("Rest Contour tool assembly is invalid")
        if not isinstance(machine_requirement, MachineRequirement):
            raise CamValidationError("Rest Contour machine requirement is invalid")
        if parameters.unit is not profile.descriptor.unit or parameters.unit is not tool_assembly.unit:
            raise CamValidationError("Rest Contour profile and tool must use the parameter unit")
        if machine_requirement.unit is not parameters.unit:
            raise CamValidationError("Rest Contour machine must use the parameter unit")
        if OperationCapability.MILLING not in machine_requirement.required_capabilities:
            raise CamValidationError("Rest Contour machine must require milling capability")
        return Operation(
            operation_id, node_id, OperationFamily.MILLING, setup_id,
            ToolAssemblyReference.from_assembly(tool_assembly),
            (OperationGeometryInput(
                GeometryInputId.new(), GeometryInputRole.PROFILE,
                profile.descriptor.reference, True,
                expected_kind=profile.descriptor.reference.kind, selection_order=0,
            ),),
            parameters.to_operation_parameters(), machine_requirement,
        )

    def create_for_strategy(self, strategy_key: str, **kwargs: Any) -> Operation:
        """Create a registered strategy and reject all unknown creation types."""
        if strategy_key != REST_CONTOUR_STRATEGY_KEY:
            raise UnsupportedCamSchemaError(f"Unknown CAM operation type: {strategy_key!r}")
        return self.create(**kwargs)

    def decode(self, payload: Mapping[str, Any]) -> Operation:
        """Decode a known Rest operation, preserving generic unknown records.

        Project persistence therefore remains forward-compatible while every
        Rest Contour record receives typed parameter/profile validation on
        reopen before an application lifecycle can use it.
        """
        if not isinstance(payload, Mapping):
            raise CamValidationError("Operation payload is invalid")
        operation = Operation.from_dict(dict(payload))
        if operation.strategy_key != REST_CONTOUR_STRATEGY_KEY:
            return operation
        if not self.is_registered(
            operation.strategy_key, operation.strategy_version,
            operation.parameters.schema_version,
        ):
            raise UnsupportedCamSchemaError("Unsupported Rest Contour operation version")
        parameters = RestContourParameters.from_operation_parameters(operation.parameters)
        profiles = tuple(item for item in operation.geometry_inputs if item.role is GeometryInputRole.PROFILE)
        expected_profile_kind = _rest_contour_profile_kind(parameters.profile_source)
        if (
            operation.family is not OperationFamily.MILLING
            or len(operation.geometry_inputs) != 1
            or len(profiles) != 1
            or not profiles[0].required
            or profiles[0].selection_order != 0
            or profiles[0].reference.kind is not expected_profile_kind
            or profiles[0].expected_kind is not expected_profile_kind
            or operation.tool_assembly.unit is not parameters.unit
            or operation.machine_requirement is None
            or operation.machine_requirement.unit is not parameters.unit
            or OperationCapability.MILLING not in operation.machine_requirement.required_capabilities
        ):
            raise CamValidationError("Persisted Rest Contour operation contract is invalid")
        return operation


def _rest_contour_profile_kind(source: ContourProfileSource) -> GeometryReferenceKind:
    """Map the typed profile-source contract to its sole accepted reference kind."""
    if source is ContourProfileSource.PLANAR_FACE_OUTER:
        return GeometryReferenceKind.FACE
    if source is ContourProfileSource.CLOSED_WIRE:
        return GeometryReferenceKind.SKETCH_OR_PROFILE
    raise CamValidationError("Rest Contour profile source is unsupported")


def default_rest_contour_operation_registry() -> RestContourOperationRegistry:
    """Return a fully registered, process-local registry without global state."""
    return RestContourOperationRegistry()
