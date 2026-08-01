"""Project-bound lifecycle composition for the Stage 9A.9 Lathe UI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol
from uuid import UUID

from PySide6.QtCore import QObject, Signal

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.domain.setup import CylinderStock
from hms_cadcam.cam.domain.tooling import (
    HolderDefinition,
    ToolAssembly,
    ToolDefinition,
)
from hms_cadcam.cam.lathe.application import (
    LatheOperationService,
    LatheServiceSession,
)
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.presenter import (
    LathePresenterFacade,
    LathePresenterSnapshot,
)
from hms_cadcam.cam.lathe.toolpath.stock import (
    LatheStockSnapshotV1,
    lathe_stock_from_cylinder,
)
from hms_cadcam.cam.lathe.readiness import LatheWorkspaceReadiness
from hms_cadcam.cam.lathe.types import (
    LatheStage9A9State,
    LatheToolCapability,
    LatheWorkspaceReadinessReason,
    LatheWorkspaceReadinessState,
)
from hms_cadcam.cam.lathe.domain import LatheOperationState
from hms_cadcam.ui.lathe_adapters import (
    LatheSelectionContext,
    ProjectLatheToolCatalog,
)
from hms_cadcam.ui.lathe_presenter import LatheQtPresenter
from hms_cadcam.ui.lathe_toolpath import (
    LathePreviewSink,
    LatheToolpathUiController,
)


_ACTIVE_READINESS = LatheWorkspaceReadiness(
    LatheWorkspaceReadinessState.PRESENTER_ACTIVE,
    LatheWorkspaceReadinessReason.NONE,
    True,
    True,
    LatheStage9A9State.COMPLETE,
)


class ActiveLathePresenterFacade(LathePresenterFacade):
    """Stage 9A.9 facade projection that leaves the Stage 12 default unchanged."""

    def snapshot(self) -> LathePresenterSnapshot:
        return replace(
            super().snapshot(),
            workspace_readiness=_ACTIVE_READINESS,
        )

    def query_workspace_readiness(self) -> LatheWorkspaceReadiness:
        return _ACTIVE_READINESS


class LatheWorkspacePort(Protocol):
    """Small workspace boundary used by the lifecycle controller."""

    def bind_presenter(
        self,
        presenter: LatheQtPresenter | None,
        *,
        unavailable_reason: str = "lathe.presenter.unavailable",
    ) -> None: ...


class LathePersistencePort(Protocol):
    """Narrow project-service boundary; UI never opens SQLite directly."""

    @property
    def lathe_snapshot(self) -> object | None: ...

    def stage_lathe_operations(
        self,
        *,
        document_id: CadDocumentId,
        source_id: UUID,
        generation: int,
        setup_id: SetupId,
        operations: tuple[LatheOperationState, ...],
    ) -> object: ...

    def bind_toolpath_controller(
        self, controller: LatheToolpathUiController | None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LatheUiContext:
    """Exact live project/document/source/setup facts for one Lathe session."""

    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    generation: int
    setup_id: SetupId | None
    read_only: bool
    tools: tuple[ToolDefinition, ...] = ()
    holders: tuple[HolderDefinition, ...] = ()
    assemblies: tuple[ToolAssembly, ...] = ()
    stock: CylinderStock | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("Lathe UI project_id must be a non-nil UUID")
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("Lathe UI document_id is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("Lathe UI source_id must be a non-nil UUID")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("Lathe UI generation is invalid")
        if self.setup_id is not None and not isinstance(self.setup_id, SetupId):
            raise TypeError("Lathe UI setup_id is invalid")
        if type(self.read_only) is not bool:
            raise TypeError("Lathe UI read_only must be bool")
        typed_collections = (
            (self.tools, ToolDefinition),
            (self.holders, HolderDefinition),
            (self.assemblies, ToolAssembly),
        )
        if any(
            not isinstance(values, tuple)
            or any(not isinstance(item, item_type) for item in values)
            for values, item_type in typed_collections
        ):
            raise TypeError("Lathe UI Tool snapshot collections are invalid")
        if self.stock is not None and not isinstance(self.stock, CylinderStock):
            raise TypeError("Lathe UI stock snapshot must be CylinderStock or None")


class LatheSessionController(QObject):
    """Own one presenter/service for the current live project context."""

    availability_changed = Signal(bool, str)

    def __init__(
        self,
        workspace: LatheWorkspacePort,
        selection_provider: Callable[[], LatheSelectionContext | None],
        *,
        explicit_capabilities: Mapping[
            LatheToolReference, frozenset[LatheToolCapability]
        ] | None = None,
        toolpath_sink: LathePreviewSink | None = None,
        persistence_port: LathePersistencePort | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not callable(selection_provider):
            raise TypeError("selection_provider must be callable")
        self.setObjectName("LatheSessionController")
        self._workspace = workspace
        self._selection_provider = selection_provider
        self._explicit_capabilities = dict(explicit_capabilities or {})
        if toolpath_sink is not None and (
            not callable(getattr(toolpath_sink, "publish", None))
            or not callable(getattr(toolpath_sink, "clear", None))
        ):
            raise TypeError("Lathe session toolpath sink is invalid")
        self._toolpath_sink = toolpath_sink
        if persistence_port is not None and (
            not hasattr(persistence_port, "lathe_snapshot")
            or not callable(getattr(persistence_port, "stage_lathe_operations", None))
        ):
            raise TypeError("Lathe persistence port is invalid")
        self._persistence_port = persistence_port
        self._context: LatheUiContext | None = None
        self._catalog: ProjectLatheToolCatalog | None = None
        self._service: LatheOperationService | None = None
        self._presenter: LatheQtPresenter | None = None
        self._toolpath_controller: LatheToolpathUiController | None = None

    @property
    def context(self) -> LatheUiContext | None:
        return self._context

    @property
    def service(self) -> LatheOperationService | None:
        return self._service

    @property
    def presenter(self) -> LatheQtPresenter | None:
        return self._presenter

    @property
    def toolpath_controller(self) -> LatheToolpathUiController | None:
        return self._toolpath_controller

    def update_context(self, context: LatheUiContext | None) -> None:
        """Create, transition or tear down the exact current runtime session."""

        if context is not None and not isinstance(context, LatheUiContext):
            raise TypeError("context must be LatheUiContext or None")
        if context is None:
            self.teardown(reason="lathe.presenter.project_context_unavailable")
            return
        identity_changed = (
            self._context is None
            or self._context.project_id != context.project_id
            or self._context.document_id != context.document_id
            or self._service is None
            or self._presenter is None
            or self._service.session.closed
            or (
                self._persistence_port is not None
                and self._context is not None
                and (
                    self._context.source_id != context.source_id
                    or self._context.generation != context.generation
                    or self._context.setup_id != context.setup_id
                )
            )
        )
        if identity_changed:
            self._replace_session(context)
            return
        assert self._catalog is not None
        assert self._service is not None
        assert self._presenter is not None
        previous = self._context
        self._catalog.replace_snapshot(
            context.tools, context.holders, context.assemblies
        )
        if (
            previous.source_id != context.source_id
            or previous.generation != context.generation
        ):
            self._service.switch_source(context.source_id, context.generation)
        if previous.setup_id != context.setup_id:
            self._service.switch_setup(context.setup_id)
        if previous.read_only != context.read_only:
            self._service.set_read_only(context.read_only)
        self._context = context
        if self._toolpath_controller is not None and (
            previous.source_id != context.source_id
            or previous.generation != context.generation
            or previous.setup_id != context.setup_id
            or previous.read_only != context.read_only
            or previous.stock != context.stock
        ):
            self._toolpath_controller.transition(
                _lathe_stock_snapshot(context)
            )
        self._presenter.refresh()
        self.availability_changed.emit(True, "")

    def refresh(self) -> None:
        """Refresh current immutable presentation without recreating ownership."""

        if self._presenter is not None:
            self._presenter.refresh()

    def teardown(
        self,
        *,
        reason: str = "lathe.presenter.unavailable",
    ) -> None:
        """Disconnect and close owned runtime state idempotently."""

        presenter = self._presenter
        service = self._service
        toolpath = self._toolpath_controller
        self._workspace.bind_toolpath_controller(None)
        self._workspace.bind_presenter(None, unavailable_reason=reason)
        if toolpath is not None:
            toolpath.shutdown(wait=True)
            toolpath.setParent(None)
            toolpath.deleteLater()
        if presenter is not None:
            presenter.teardown()
            presenter.setParent(None)
            presenter.deleteLater()
        if service is not None:
            service.close()
        self._context = None
        self._catalog = None
        self._service = None
        self._presenter = None
        self._toolpath_controller = None
        self.availability_changed.emit(False, reason)

    def _replace_session(self, context: LatheUiContext) -> None:
        self.teardown(reason="lathe.presenter.context_replaced")
        catalog = ProjectLatheToolCatalog(
            context.tools,
            context.holders,
            context.assemblies,
            explicit_capabilities=self._explicit_capabilities,
        )
        service = LatheOperationService(
            LatheServiceSession(
                context.project_id,
                context.document_id,
                context.source_id,
                context.generation,
                context.setup_id,
                read_only=context.read_only,
            ),
            capability_resolver=catalog,
        )
        if self._persistence_port is not None:
            snapshot = self._persistence_port.lathe_snapshot
            if snapshot is not None:
                matches = tuple(
                    program
                    for program in getattr(snapshot, "programs", ())
                    if (
                        program.identity.project_id == str(context.project_id)
                        and program.identity.document_id == str(context.document_id)
                        and program.identity.source_id == str(context.source_id)
                        and program.identity.source_generation == context.generation
                        and context.setup_id is not None
                        and program.identity.setup_id == str(context.setup_id)
                    )
                )
                if len(matches) > 1:
                    raise ValueError("Multiple persisted Lathe programs match one context")
                if matches:
                    service.restore_operations(matches[0].operations)
        facade = ActiveLathePresenterFacade(service)
        presenter = LatheQtPresenter(
            facade,
            catalog,
            self._selection_provider,
            self,
        )
        if self._persistence_port is not None:
            presenter.command_completed.connect(self._stage_presenter_change)
        toolpath = (
            LatheToolpathUiController(
                service,
                _lathe_stock_snapshot(context),
                self._toolpath_sink,
                parent=self,
            )
            if self._toolpath_sink is not None
            else None
        )
        self._context = context
        self._catalog = catalog
        self._service = service
        self._presenter = presenter
        self._toolpath_controller = toolpath
        self._workspace.bind_presenter(presenter)
        self._workspace.bind_toolpath_controller(toolpath)
        self.availability_changed.emit(True, "")

    def _stage_presenter_change(self, result: object) -> None:
        """Stage accepted presenter mutations through one project service call."""

        if not bool(getattr(result, "changed", False)):
            return
        context = self._context
        service = self._service
        port = self._persistence_port
        if context is None or context.setup_id is None or service is None or port is None:
            raise RuntimeError("Lathe persistence context changed during a command")
        port.stage_lathe_operations(
            document_id=context.document_id,
            source_id=context.source_id,
            generation=context.generation,
            setup_id=context.setup_id,
            operations=service.list_operations(),
        )


def _lathe_stock_snapshot(
    context: LatheUiContext,
) -> LatheStockSnapshotV1 | None:
    if context.stock is None or context.setup_id is None:
        return None
    return lathe_stock_from_cylinder(
        context.stock,
        setup_id=context.setup_id,
        source_id=context.source_id,
        generation=context.generation,
    )


__all__ = [
    "ActiveLathePresenterFacade",
    "LatheSessionController",
    "LathePersistencePort",
    "LatheUiContext",
]
