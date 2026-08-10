"""Thin Stage16A adapter into existing CAM strategy editors and services."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import cast
from uuid import UUID

from PySide6.QtWidgets import QWidget

from hms_cadcam.cam.application.drilling import (
    DrillingGenerationError,
    DrillingGenerator,
)
from hms_cadcam.cam.cam3d.models import CamSurfaceReference
from hms_cadcam.cam.cam3d.persistence import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import ParallelFinishingParameters
from hms_cadcam.cam.cam3d.zlevel import ZLevelFinishingParameters
from hms_cadcam.cam.domain import (
    CamNodeId,
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillingStrategy,
    GeometryResolutionStatus,
    HolePattern,
    HoleReference,
    Length,
    LengthUnit,
    MachineRequirement,
    MachiningZone3DId,
    Operation,
    OperationFamily,
    OperationId,
    Setup,
    ToolAssembly,
    ToolAssemblyReference,
    Vector3,
)
from hms_cadcam.cam.domain.machine import OperationCapability
from hms_cadcam.cam.operation_creation import (
    OperationCreationSession,
    OperationCreationState,
    OperationStrategyChoice,
    OperationToolChoice,
    Stage16AStrategyRegistry,
    Stage16AToolSelectionService,
    default_drilling_creation_strategy,
)
from hms_cadcam.cam.tool_profile_integration import (
    resolve_editor_tool_profile_application,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorDiagnostic,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.strategies.common_drilling import (
    DrillingFamilyEditorContext,
    DrillingFamilyEditorDraftContext,
    DrillingFamilyEditorKind,
    build_drilling_family_schema,
    drilling_family_applied_values,
    drilling_family_draft_transform,
    drilling_family_geometry_values,
    prepare_drilling_family_update,
)
from hms_cadcam.ui.function_editor.strategies.parallel import (
    ParallelEditorContext,
    ParallelEditorDraftContext,
    ParallelGeometryEvidence,
    build_parallel_schema,
    parallel_applied_values,
    parallel_draft_derived_values,
    parallel_validation_diagnostics,
    prepare_parallel_update,
)
from hms_cadcam.ui.function_editor.strategies.zlevel import (
    ZLevelEditorContext,
    ZLevelEditorDraftContext,
    ZLevelGeometryEvidence,
    build_z_level_schema,
    prepare_z_level_update,
    z_level_applied_values,
    z_level_draft_derived_values,
    z_level_validation_diagnostics,
)
from hms_cadcam.ui.operation_creation_wizard import (
    FinishBindingClaim,
    FinishBindingCompletion,
    OperationCreationEditorBinding,
    error_diagnostic,
)
from hms_cadcam.ui.tool_library import ToolLibraryDialog


HoleSource = HoleReference | HolePattern


@dataclass(frozen=True, slots=True)
class _OperationCreationBindingIdentity:
    """Stable binding metadata; the live session remains wizard-owned."""

    session_id: UUID
    project_id: UUID
    project_generation: int
    job_id: object
    setup_id: object
    parent_node_id: CamNodeId
    strategy_id: str | None
    tool_assembly_id: object | None
    tool_id: object | None
    profile_id: object | None
    tool_configuration_revision: object | None

    @classmethod
    def from_session(
        cls, session: OperationCreationSession
    ) -> "_OperationCreationBindingIdentity":
        return cls(
            session.session_id,
            session.project_id,
            session.project_generation,
            session.job_id,
            session.setup_id,
            session.parent_node_id,
            session.strategy_id,
            session.tool_assembly_id,
            session.tool_id,
            session.profile_id,
            session.tool_configuration_revision,
        )


class Stage16AOperationCreationAdapter:
    """Orchestrate working copies; algorithms and editor schemas remain authoritative."""

    def __init__(
        self,
        service: ProjectService,
        *,
        surface_provider: Callable[[], tuple[object, ...]] | None = None,
        geometry_bounds_provider: Callable[[], tuple[object, ...]] | None = None,
        drilling_pick_provider: Callable[[Vector3], HoleSource] | None = None,
        drilling_resolver: Callable[[DrillGeometryInput, DrillDepthDefinition], object]
        | None = None,
    ) -> None:
        self._service = service
        self._strategies = Stage16AStrategyRegistry()
        self._surface_provider = surface_provider
        self._geometry_bounds_provider = geometry_bounds_provider
        self._drilling_pick_provider = drilling_pick_provider
        self._drilling_resolver = drilling_resolver

    def strategy_choices(self) -> tuple[OperationStrategyChoice, ...]:
        return self._strategies.choices()

    def tool_choices(
        self, session: OperationCreationSession, query: str = ""
    ) -> tuple[OperationToolChoice, ...]:
        if session.strategy_id is None:
            return ()
        _job, setup, _parent = self._current_context(session)
        return Stage16AToolSelectionService(
            self._service.cam_snapshot, setup_unit=setup.wcs.origin.unit
        ).choices(session.strategy_id, query)

    def selected_tool_is_compatible(
        self, session: OperationCreationSession, strategy_id: str
    ) -> bool:
        if (
            session.tool_assembly_id is None
            or session.tool_id is None
            or session.tool_configuration_revision is None
        ):
            return False
        try:
            _job, setup, _parent = self._current_context(session)
            Stage16AToolSelectionService(
                self._service.cam_snapshot, setup_unit=setup.wcs.origin.unit
            ).require_current(
                strategy_id,
                session.tool_assembly_id,
                tool_id=session.tool_id,
                configuration_revision=session.tool_configuration_revision,
            )
        except (KeyError, RuntimeError, TypeError, ValueError):
            return False
        return True

    def context_is_current(
        self, session: OperationCreationSession
    ) -> tuple[bool, str]:
        try:
            if self._service.current_project is None:
                return False, "Dự án đã đóng; không thể tạo nguyên công."
            if (
                self._service.current_project.manifest.project_id
                != session.project_id
            ):
                return False, "Dự án hiện tại đã thay đổi."
            if self._service.cam_generation != session.project_generation:
                return False, "Phiên dự án đã thay đổi; hãy mở lại trình tạo nguyên công."
            _job, setup, _parent = self._current_context(session)
            if (
                session.strategy_id is not None
                and session.tool_assembly_id is not None
                and session.tool_id is not None
                and session.tool_configuration_revision is not None
            ):
                Stage16AToolSelectionService(
                    self._service.cam_snapshot,
                    setup_unit=setup.wcs.origin.unit,
                ).require_current(
                    session.strategy_id,
                    session.tool_assembly_id,
                    tool_id=session.tool_id,
                    configuration_revision=session.tool_configuration_revision,
                )
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            return False, str(error)
        return True, ""

    def build_editor(
        self,
        session: OperationCreationSession,
        *,
        claim_finish: FinishBindingClaim,
        complete_finish: FinishBindingCompletion,
    ) -> OperationCreationEditorBinding:
        current, reason = self.context_is_current(session)
        if not current:
            raise RuntimeError(reason)
        if session.strategy_id == "parallel_finishing_3d":
            return self._parallel_binding(session, claim_finish, complete_finish)
        if session.strategy_id == "z_level_finishing_3d":
            return self._z_level_binding(session, claim_finish, complete_finish)
        if session.strategy_id == "drilling_v1":
            return self._drilling_binding(session, claim_finish, complete_finish)
        raise ValueError("Strategy chưa được Stage16A hỗ trợ.")

    def open_tool_management(
        self, session: OperationCreationSession, parent: QWidget
    ) -> None:
        """Open the dedicated manager; Step2 refreshes only after it returns."""
        dialog = ToolLibraryDialog(
            self._service,
            initial_tool_id=session.tool_id,
            parent=parent,
        )
        dialog.exec()

    def _parallel_binding(
        self,
        session: OperationCreationSession,
        claim_finish: FinishBindingClaim,
        complete_finish: FinishBindingCompletion,
    ) -> OperationCreationEditorBinding:
        job, setup, _parent = self._current_context(session)
        if setup.wcs.origin.unit is not LengthUnit.MM:
            raise ValueError("Parallel Finishing v1 yêu cầu Setup dùng đơn vị mm.")
        snapshot = self._service.cam_snapshot
        assembly = self._assembly(snapshot.tool_assemblies, session)
        operation = self._parallel_operation(setup, assembly, snapshot.machine_definitions)
        context = ParallelEditorContext(
            "Parallel Finishing",
            operation,
            setup,
            job.job_id,
            session.project_id,
            None,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            geometry_resolved=False,
            geometry_diagnostic="Chưa chọn bề mặt gia công.",
        )
        draft = ParallelEditorDraftContext(())
        schema = build_parallel_schema(context)
        binding_identity = _OperationCreationBindingIdentity.from_session(session)

        def action(
            action_id: str, values: Mapping[str, PresentationValue]
        ) -> Mapping[str, PresentationValue] | None:
            return self._surface_action(context, draft, action_id, values)

        def finish(values: Mapping[str, PresentationValue]) -> Operation:
            success = False
            try:
                current_session = claim_finish()
                self._require_current_binding(binding_identity, current_session)
                update = prepare_parallel_update(context, draft, values)
                self._commit(
                    current_session,
                    update.operation_name,
                    update.operation,
                    zone=update.zone,
                )
                success = True
                return update.operation
            finally:
                complete_finish(success)

        binding = OperationCreationEditorBinding(
            schema,
            _schema_values(schema, parallel_applied_values(context)),
            lambda values: parallel_validation_diagnostics(
                schema, context, draft, values
            ),
            finish,
            action,
            lambda values: parallel_draft_derived_values(context, draft, values),
        )
        return self._resolved_editor_binding(session, binding, snapshot)

    def _z_level_binding(
        self,
        session: OperationCreationSession,
        claim_finish: FinishBindingClaim,
        complete_finish: FinishBindingCompletion,
    ) -> OperationCreationEditorBinding:
        job, setup, _parent = self._current_context(session)
        if setup.wcs.origin.unit is not LengthUnit.MM:
            raise ValueError("Z-Level Finishing v1 yêu cầu Setup dùng đơn vị mm.")
        snapshot = self._service.cam_snapshot
        assembly = self._assembly(snapshot.tool_assemblies, session)
        operation = self._z_level_operation(setup, assembly, snapshot.machine_definitions)
        context = ZLevelEditorContext(
            "Gia công tinh theo cao độ Z",
            operation,
            setup,
            job.job_id,
            session.project_id,
            None,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            geometry_resolved=False,
            geometry_diagnostic="Chưa chọn bề mặt gia công.",
        )
        draft = ZLevelEditorDraftContext(())
        schema = build_z_level_schema(context)
        binding_identity = _OperationCreationBindingIdentity.from_session(session)

        def action(
            action_id: str, values: Mapping[str, PresentationValue]
        ) -> Mapping[str, PresentationValue] | None:
            return self._surface_action(context, draft, action_id, values)

        def finish(values: Mapping[str, PresentationValue]) -> Operation:
            success = False
            try:
                current_session = claim_finish()
                self._require_current_binding(binding_identity, current_session)
                update = prepare_z_level_update(context, draft, values)
                self._commit(
                    current_session,
                    update.operation_name,
                    update.operation,
                    zone=update.zone,
                )
                success = True
                return update.operation
            finally:
                complete_finish(success)

        binding = OperationCreationEditorBinding(
            schema,
            _schema_values(schema, z_level_applied_values(context)),
            lambda values: z_level_validation_diagnostics(
                schema, context, draft, values
            ),
            finish,
            action,
            lambda values: z_level_draft_derived_values(context, draft, values),
        )
        return self._resolved_editor_binding(session, binding, snapshot)

    def _drilling_binding(
        self,
        session: OperationCreationSession,
        claim_finish: FinishBindingClaim,
        complete_finish: FinishBindingCompletion,
    ) -> OperationCreationEditorBinding:
        if self._drilling_pick_provider is None or self._drilling_resolver is None:
            raise RuntimeError("Bộ chọn và resolver hình học Khoan chưa sẵn sàng.")
        _job, setup, _parent = self._current_context(session)
        source = self._drilling_pick_provider(setup.wcs.z_axis)
        if not isinstance(source, (HoleReference, HolePattern)):
            raise TypeError("Bộ chọn lỗ trả về nguồn hình học không hợp lệ.")
        strategy = default_drilling_creation_strategy(setup, source)
        resolved = self._drilling_resolver(strategy.geometry, strategy.depth)
        if getattr(resolved, "status", None) is not GeometryResolutionStatus.RESOLVED:
            diagnostics = getattr(resolved, "diagnostics", ())
            raise ValueError(
                diagnostics[0].message if diagnostics else "Hình học Khoan không resolve được."
            )
        snapshot = self._service.cam_snapshot
        assembly = self._assembly(snapshot.tool_assemblies, session)
        operation = self._drilling_operation(
            setup, assembly, snapshot.machine_definitions, strategy
        )
        context = DrillingFamilyEditorContext(
            DrillingFamilyEditorKind.DRILLING,
            "Khoan",
            operation,
            setup,
            snapshot.tool_assemblies,
            snapshot.tool_definitions,
            snapshot.holder_definitions,
            snapshot.machine_definitions,
            source,
            True,
            resolved_pattern=resolved.region.pattern,
        )
        draft = DrillingFamilyEditorDraftContext(
            source, resolved_pattern=resolved.region.pattern
        )
        schema = build_drilling_family_schema(context)
        binding_identity = _OperationCreationBindingIdentity.from_session(session)

        def validate(
            values: Mapping[str, PresentationValue]
        ) -> tuple[FunctionEditorDiagnostic, ...]:
            try:
                update = prepare_drilling_family_update(context, draft, values)
                self._resolve_drilling(update, setup)
            except (
                DrillingGenerationError,
                KeyError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                code = getattr(getattr(error, "code", None), "value", "drilling.invalid")
                return error_diagnostic(str(error), code=code)
            return ()

        def action(
            action_id: str, values: Mapping[str, PresentationValue]
        ) -> Mapping[str, PresentationValue] | None:
            if action_id != "select_holes":
                raise ValueError(f"Thao tác Step 3 không được hỗ trợ: {action_id}")
            assert self._drilling_pick_provider is not None
            assert self._drilling_resolver is not None
            selected = self._drilling_pick_provider(setup.wcs.z_axis)
            if not isinstance(selected, (HoleReference, HolePattern)):
                raise TypeError("Bộ chọn lỗ trả về nguồn hình học không hợp lệ.")
            unit = setup.wcs.origin.unit
            depth = DrillDepthDefinition(
                unit,
                Length(float(values["top_z"]), unit),
                Length(float(values["final_depth"]), unit),
            )
            result = self._drilling_resolver(DrillGeometryInput(selected, unit), depth)
            if getattr(result, "status", None) is not GeometryResolutionStatus.RESOLVED:
                diagnostics = getattr(result, "diagnostics", ())
                raise ValueError(
                    diagnostics[0].message if diagnostics else "Hình học Khoan không hợp lệ."
                )
            draft.hole_source = selected
            draft.pending_input_ids = {}
            draft.resolved_pattern = result.region.pattern
            return drilling_family_geometry_values(selected, True)

        def finish(values: Mapping[str, PresentationValue]) -> Operation:
            success = False
            try:
                current_session = claim_finish()
                self._require_current_binding(binding_identity, current_session)
                update = prepare_drilling_family_update(context, draft, values)
                self._resolve_drilling(update, setup)
                self._commit(current_session, update.operation_name, update.operation)
                success = True
                return update.operation
            finally:
                complete_finish(success)

        binding = OperationCreationEditorBinding(
            schema,
            _schema_values(schema, drilling_family_applied_values(context)),
            validate,
            finish,
            action,
            lambda values: drilling_family_draft_transform(context, draft, values),
        )
        return self._resolved_editor_binding(session, binding, snapshot)

    def _resolved_editor_binding(
        self,
        session: OperationCreationSession,
        binding: OperationCreationEditorBinding,
        snapshot,
    ) -> OperationCreationEditorBinding:
        """Seed the existing editor from one authoritative profile resolution."""
        if session.strategy_id is None or session.tool_id is None:
            raise RuntimeError("Tool profile context is incomplete.")
        tool = next(
            (
                item
                for item in snapshot.tool_definitions
                if item.tool_id == session.tool_id
            ),
            None,
        )
        if tool is None:
            raise ValueError("Tool Definition is missing.")
        assembly = self._assembly(snapshot.tool_assemblies, session)
        holder = next(
            (
                item
                for item in snapshot.holder_definitions
                if assembly.holder_id is not None
                and item.holder_id == assembly.holder_id
            ),
            None,
        )
        applied = dict(binding.applied_values)
        for field_id, value in session.working_values:
            if field_id in applied:
                applied[field_id] = _presentation_like(applied[field_id], value)
        application = resolve_editor_tool_profile_application(
            tool,
            session.strategy_id,
            applied,
            profile_id=session.profile_id,
            operation_id=str(session.session_id),
            holder_fingerprint=(
                holder.content_fingerprint if holder is not None else None
            ),
        )
        for field_id, value in application.editor_values:
            if field_id in applied:
                applied[field_id] = _presentation_like(applied[field_id], value)
        if not set(session.resolved_provenance).issubset(
            application.winning_sources
        ):
            raise RuntimeError("Tool profile provenance changed; select the Tool again.")
        return replace(binding, applied_values=applied)

    def _resolve_drilling(self, update, setup: Setup) -> object:
        assert self._drilling_resolver is not None
        resolved = self._drilling_resolver(
            update.strategy.geometry, update.strategy.depth
        )
        return DrillingGenerator().resolve_inputs(
            replace(update.operation, enabled=True),
            setup,
            assembly=update.assembly,
            tool=update.tool,
            machine=update.machine,
            resolved_geometry=resolved,
        )

    def _surface_action(
        self,
        context: ParallelEditorContext | ZLevelEditorContext,
        draft: ParallelEditorDraftContext | ZLevelEditorDraftContext,
        action_id: str,
        _values: Mapping[str, PresentationValue],
    ) -> Mapping[str, PresentationValue] | None:
        if action_id in {"clear_parallel_faces", "clear_z_level_faces"}:
            draft.surfaces = ()
            draft.geometry_evidence = None
            return _surface_presentation(context, ())
        if self._surface_provider is None:
            raise RuntimeError("Bộ chọn bề mặt CAM 3D chưa sẵn sàng.")
        selected = tuple(self._surface_provider())
        if not selected:
            raise ValueError("Hãy chọn ít nhất một bề mặt BRep trong viewport.")
        surfaces = tuple(cast(CamSurfaceReference, item) for item in selected)
        for surface in surfaces:
            if not isinstance(surface, CamSurfaceReference):
                raise TypeError("Bộ chọn trả về tham chiếu bề mặt không hợp lệ.")
        if action_id in {
            "select_parallel_faces",
            "select_z_level_faces",
            "reselect_parallel_faces",
            "reselect_z_level_faces",
        }:
            replacing = action_id.startswith("reselect")
            if replacing:
                draft.geometry_evidence = None
            merged = {} if replacing else {
                _surface_key(item): item for item in draft.surfaces
            }
            merged.update({_surface_key(item): item for item in surfaces})
            draft.surfaces = tuple(
                sorted(merged.values(), key=lambda item: item.fingerprint.digest)
            )
        elif action_id in {"remove_parallel_faces", "remove_z_level_faces"}:
            removed = {_surface_key(item) for item in surfaces}
            draft.surfaces = tuple(
                item for item in draft.surfaces if _surface_key(item) not in removed
            )
            draft.geometry_evidence = None
            return _surface_presentation(context, draft.surfaces)
        else:
            raise ValueError(f"Thao tác bề mặt không được hỗ trợ: {action_id}")
        if self._geometry_bounds_provider is not None and draft.surfaces:
            bounds = tuple(self._geometry_bounds_provider())
            if len(bounds) != len(surfaces):
                raise ValueError("Hộp bao hình học đã stale; hãy chọn lại bề mặt.")
            added = _geometry_evidence(context, bounds)
            draft.geometry_evidence = _merge_geometry_evidence(
                draft.geometry_evidence, added
            )
        return _surface_presentation(context, draft.surfaces)

    @staticmethod
    def _require_current_binding(
        identity: _OperationCreationBindingIdentity,
        current_session: OperationCreationSession,
    ) -> None:
        if current_session.state not in {
            OperationCreationState.CONFIGURE_OPERATION,
            OperationCreationState.READY_TO_CREATE,
        }:
            raise RuntimeError("Phiên tạo nguyên công không còn hiện hành.")
        if (
            current_session.session_id != identity.session_id
            or current_session.project_id != identity.project_id
            or current_session.project_generation != identity.project_generation
            or current_session.job_id != identity.job_id
            or current_session.setup_id != identity.setup_id
            or current_session.parent_node_id != identity.parent_node_id
            or current_session.strategy_id != identity.strategy_id
            or current_session.tool_assembly_id != identity.tool_assembly_id
            or current_session.tool_id != identity.tool_id
            or current_session.profile_id != identity.profile_id
            or current_session.tool_configuration_revision
            != identity.tool_configuration_revision
        ):
            raise RuntimeError("Phiên tạo nguyên công không còn hiện hành.")

    def _commit(
        self,
        session: OperationCreationSession,
        operation_name: str,
        operation: Operation,
        *,
        zone=None,
    ) -> None:
        current, reason = self.context_is_current(session)
        if not current:
            raise RuntimeError(reason)
        if operation.setup_id != session.setup_id:
            raise ValueError("Candidate operation belongs to another Setup")
        if operation.strategy_key != session.strategy_id:
            raise ValueError("Candidate strategy changed during operation creation")
        snapshot = self._service.cam_snapshot
        if any(
            current.operation_id == operation.operation_id
            for job in snapshot.jobs
            for setup in job.setups
            for current in setup.operation_tree.operations
        ):
            raise ValueError("Operation đã được tạo; yêu cầu lặp bị chặn.")
        config = None
        if zone is not None:
            existing = self._service.cam3d_config
            config = Cam3DProjectConfig(
                existing.project_id,
                tuple(item for item in existing.zones if item.zone_id != zone.zone_id)
                + (zone,),
            )

        def command(app):
            live = app.snapshot
            job = next((item for item in live.jobs if item.job_id == session.job_id), None)
            if job is None:
                raise ValueError("CAM Job đã bị xóa.")
            setup = next((item for item in job.setups if item.setup_id == session.setup_id), None)
            if setup is None:
                raise ValueError("Setup đã bị xóa.")
            parent = setup.operation_tree.get_node(session.parent_node_id)
            if parent.operation_id is not None:
                raise ValueError("Vị trí chèn operation không còn là group.")
            assert session.tool_assembly_id is not None
            assert session.tool_id is not None
            assert session.tool_configuration_revision is not None
            Stage16AToolSelectionService(
                live, setup_unit=setup.wcs.origin.unit
            ).require_current(
                operation.strategy_key,
                session.tool_assembly_id,
                tool_id=session.tool_id,
                configuration_revision=session.tool_configuration_revision,
            )
            if operation.tool_assembly.assembly_id != session.tool_assembly_id:
                raise ValueError("Candidate Tool identity changed")
            return app.update_tree(
                session.job_id,
                session.setup_id,
                lambda tree: tree.add_operation(
                    session.parent_node_id, operation_name, operation
                ),
            )

        self._service.execute_cam_creation(
            command,
            expected_project_id=session.project_id,
            expected_generation=session.project_generation,
            cam3d_config=config,
        )

    def _current_context(self, session: OperationCreationSession):
        snapshot = self._service.cam_snapshot
        job = next((item for item in snapshot.jobs if item.job_id == session.job_id), None)
        if job is None:
            raise ValueError("CAM Job không còn tồn tại.")
        setup = next((item for item in job.setups if item.setup_id == session.setup_id), None)
        if setup is None:
            raise ValueError("Setup không còn tồn tại.")
        parent = setup.operation_tree.get_node(session.parent_node_id)
        if parent.operation_id is not None:
            raise ValueError("Context tạo operation không còn là group.")
        return job, setup, parent

    @staticmethod
    def _assembly(
        assemblies: tuple[ToolAssembly, ...], session: OperationCreationSession
    ) -> ToolAssembly:
        if session.tool_assembly_id is None:
            raise ValueError("Chưa chọn Tool Assembly.")
        assembly = next(
            (item for item in assemblies if item.assembly_id == session.tool_assembly_id),
            None,
        )
        if assembly is None:
            raise ValueError("Tool Assembly đã bị xóa.")
        return assembly

    @staticmethod
    def _machine(machines, setup: Setup, capability: OperationCapability):
        machine = next(
            (
                item
                for item in machines
                if item.unit is setup.wcs.origin.unit
                and capability in item.capabilities.operations
            ),
            None,
        )
        if machine is None:
            raise ValueError(f"Không có Machine tương thích capability {capability.value}.")
        return machine

    def _parallel_operation(self, setup: Setup, assembly: ToolAssembly, machines) -> Operation:
        machine = self._machine(machines, setup, OperationCapability.MILLING)
        top = setup.wcs.origin.z
        parameters = ParallelFinishingParameters(
            MachiningZone3DId.new(),
            1.0,
            clearance_z_mm=top + 50.0,
            retract_z_mm=top + 40.0,
            link_clearance_mm=1.0,
        )
        return Operation(
            OperationId.new(),
            CamNodeId.new(),
            OperationFamily.MILLING,
            setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly),
            (),
            parameters.to_operation_parameters(),
            _machine_requirement(machine, OperationCapability.MILLING),
        )

    def _z_level_operation(self, setup: Setup, assembly: ToolAssembly, machines) -> Operation:
        machine = self._machine(machines, setup, OperationCapability.MILLING)
        origin_z = setup.wcs.origin.z
        parameters = ZLevelFinishingParameters(
            MachiningZone3DId.new(),
            origin_z + 1.0,
            origin_z,
            1.0,
            clearance_z_mm=origin_z + 50.0,
            retract_z_mm=origin_z + 40.0,
            link_clearance_mm=1.0,
            setup_reference=str(setup.setup_id),
        )
        return Operation(
            OperationId.new(),
            CamNodeId.new(),
            OperationFamily.MILLING,
            setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly),
            (),
            parameters.to_operation_parameters(),
            _machine_requirement(machine, OperationCapability.MILLING),
        )

    def _drilling_operation(
        self,
        setup: Setup,
        assembly: ToolAssembly,
        machines,
        strategy: DrillingStrategy,
    ) -> Operation:
        machine = self._machine(machines, setup, OperationCapability.DRILLING)
        return Operation(
            OperationId.new(),
            CamNodeId.new(),
            OperationFamily.DRILLING,
            setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly),
            (),
            strategy.to_operation_parameters(),
            _machine_requirement(machine, OperationCapability.DRILLING),
        )


def _machine_requirement(machine, capability: OperationCapability) -> MachineRequirement:
    return MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (capability,),
    )


def _schema_values(schema, values: Mapping[str, PresentationValue]):
    """Mirror the production-session boundary's exact schema-field projection."""
    return {field.field_id: values[field.field_id] for field in schema.fields}


def _presentation_like(current: PresentationValue, value: object) -> PresentationValue:
    """Keep existing FunctionEditor primitive shape while replacing its value."""
    if isinstance(current, str) and not isinstance(value, str):
        return str(value)
    if not isinstance(value, (str, int, float, bool, type(None), tuple)):
        raise TypeError("Resolved Tool profile value is not presentation-safe.")
    return cast(PresentationValue, value)


def _surface_key(surface: CamSurfaceReference) -> tuple[object, ...]:
    geometry = surface.geometry
    return (
        geometry.source_id,
        geometry.scheme,
        geometry.scheme_version,
        geometry.occurrence_path,
        geometry.subshape_selector,
        geometry.expected_source_revision,
    )


def _surface_presentation(
    context: ParallelEditorContext | ZLevelEditorContext,
    surfaces: tuple[CamSurfaceReference, ...],
) -> dict[str, PresentationValue]:
    count = len(surfaces)
    return {
        "geometry_summary": f"{count} bề mặt gia công · {'DRAFT' if count else 'MISSING'}",
        "selected_face_count": str(count),
        "geometry_reference_summary": (
            ", ".join(str(item.geometry.reference_id)[:8] for item in surfaces)
            if surfaces
            else "Không có"
        ),
        "selected_body_setup_summary": (
            f"Setup {context.setup.name} · {context.setup.work_offset.name}"
        ),
    }


def _geometry_evidence(
    context: ParallelEditorContext | ZLevelEditorContext,
    bounds: tuple[object, ...],
) -> ParallelGeometryEvidence | ZLevelGeometryEvidence:
    frame = context.setup.wcs
    origin = frame.origin
    projected: list[tuple[float, float, float]] = []
    for value in bounds:
        coordinates = (
            getattr(value, "x_min"),
            getattr(value, "y_min"),
            getattr(value, "z_min"),
            getattr(value, "x_max"),
            getattr(value, "y_max"),
            getattr(value, "z_max"),
        )
        if not all(isinstance(item, (int, float)) for item in coordinates):
            raise TypeError("Hộp bao hình học không hợp lệ.")
        x_min, y_min, z_min, x_max, y_max, z_max = coordinates
        for x in (x_min, x_max):
            for y in (y_min, y_max):
                for z in (z_min, z_max):
                    dx, dy, dz = x - origin.x, y - origin.y, z - origin.z
                    projected.append(
                        (
                            dx * frame.x_axis.x + dy * frame.x_axis.y + dz * frame.x_axis.z,
                            dx * frame.y_axis.x + dy * frame.y_axis.y + dz * frame.y_axis.z,
                            dx * frame.z_axis.x + dy * frame.z_axis.y + dz * frame.z_axis.z,
                        )
                    )
    if isinstance(context, ParallelEditorContext):
        return ParallelGeometryEvidence(
            min(item[0] for item in projected),
            max(item[0] for item in projected),
            min(item[1] for item in projected),
            max(item[1] for item in projected),
            "Hộp bao các bề mặt được chọn trong viewport",
        )
    return ZLevelGeometryEvidence(
        min(item[0] for item in projected),
        max(item[0] for item in projected),
        min(item[1] for item in projected),
        max(item[1] for item in projected),
        min(item[2] for item in projected),
        max(item[2] for item in projected),
        "Hộp bao các bề mặt được chọn trong viewport",
    )


def _merge_geometry_evidence(current, added):
    if current is None:
        return added
    if isinstance(added, ParallelGeometryEvidence) and isinstance(
        current, ParallelGeometryEvidence
    ):
        return ParallelGeometryEvidence(
            min(current.u_min, added.u_min),
            max(current.u_max, added.u_max),
            min(current.v_min, added.v_min),
            max(current.v_max, added.v_max),
            "Hộp bao hợp nhất của các bề mặt được chọn",
        )
    if isinstance(added, ZLevelGeometryEvidence) and isinstance(
        current, ZLevelGeometryEvidence
    ):
        return ZLevelGeometryEvidence(
            min(current.u_min, added.u_min),
            max(current.u_max, added.u_max),
            min(current.v_min, added.v_min),
            max(current.v_max, added.v_max),
            min(current.w_min, added.w_min),
            max(current.w_max, added.w_max),
            "Hộp bao hợp nhất của các bề mặt được chọn",
        )
    raise TypeError("Bằng chứng hình học không cùng loại strategy.")


__all__ = ["Stage16AOperationCreationAdapter"]
