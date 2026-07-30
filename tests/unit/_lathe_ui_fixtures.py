"""Typed Stage 9A.9 Qt presenter/workspace fixtures."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.models import BoundingBox, CadDocumentId
from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    DrillGeometry,
    Length,
    LengthUnit,
    Revision,
    ShankGeometry,
    ToolAssembly,
    ToolAssemblyId,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    TurningInsertGeometry,
)
from hms_cadcam.cam.lathe.application import (
    LatheOperationService,
    LatheServiceSession,
)
from hms_cadcam.cam.lathe.capabilities import LatheToolReference
from hms_cadcam.cam.lathe.strategies import lathe_strategy_definition
from hms_cadcam.cam.lathe.types import (
    LatheStrategyId,
    LatheToolCapability,
)
from hms_cadcam.ui.lathe_adapters import (
    LatheSelectionContext,
    ProjectLatheToolCatalog,
)
from hms_cadcam.ui.lathe_presenter import LatheQtPresenter
from hms_cadcam.ui.lathe_session import ActiveLathePresenterFacade
from hms_cadcam.ui.lathe_workspace import LatheWorkspace
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

from _lathe_fixtures import session, stable_uuid


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mm(value: float) -> Length:
    return Length(value, LengthUnit.MM)


def tool_catalog_for(
    capability: LatheToolCapability,
) -> tuple[ProjectLatheToolCatalog, LatheToolReference]:
    """Create one real canonical Tool/Assembly with explicit typed capability."""

    if capability is LatheToolCapability.AXIAL_DRILLING:
        family = ToolFamily.DRILL
        geometry = DrillGeometry(_mm(8.0), _mm(30.0), Angle(118.0, AngleUnit.DEGREE))
        shank_diameter = 8.0
    else:
        family = ToolFamily.TURNING_INSERT
        geometry = TurningInsertGeometry(_mm(12.7), _mm(4.0), _mm(0.8))
        shank_diameter = 10.0
    tool = ToolDefinition(
        ToolDefinitionId(stable_uuid(f"ui-tool/{capability.value}")),
        f"Typed {capability.value}",
        family,
        LengthUnit.MM,
        geometry,
        _mm(100.0),
        _mm(40.0),
        ShankGeometry(_mm(shank_diameter), _mm(70.0)),
        Revision(2),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId(stable_uuid(f"ui-assembly/{capability.value}")),
        f"Assembly {capability.value}",
        tool,
        _mm(30.0),
        _mm(60.0),
    )
    reference = LatheToolReference(tool.tool_id, None, assembly.assembly_id)
    catalog = ProjectLatheToolCatalog(
        (tool,),
        (),
        (assembly,),
        explicit_capabilities={reference: frozenset({capability})},
    )
    return catalog, reference


def selection_context(
    mode: SelectionMode = SelectionMode.FACE,
    *,
    selection_ids: tuple[str, ...] = ("cad-doc:face:1",),
) -> LatheSelectionContext:
    live = session()
    return LatheSelectionContext(
        live.document_id,
        live.source_id,
        live.generation,
        tuple(
            SelectionMetadata(
                live.document_id,
                selection_id,
                mode,
                BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
            )
            for selection_id in selection_ids
        ),
    )


def presenter_for(
    strategy_id: LatheStrategyId = LatheStrategyId.FACE,
    *,
    selection_provider: Callable[[], LatheSelectionContext | None] | None = None,
    read_only: bool = False,
) -> tuple[LatheQtPresenter, ProjectLatheToolCatalog, LatheToolReference]:
    capability = next(
        iter(lathe_strategy_definition(strategy_id).required_tool_capabilities)
    )
    catalog, reference = tool_catalog_for(capability)
    live = session(read_only=read_only)
    service = LatheOperationService(live, capability_resolver=catalog)
    presenter = LatheQtPresenter(
        ActiveLathePresenterFacade(service),
        catalog,
        selection_provider or (lambda: selection_context()),
    )
    return presenter, catalog, reference


def workspace_for(
    strategy_id: LatheStrategyId = LatheStrategyId.FACE,
    *,
    selection_provider: Callable[[], LatheSelectionContext | None] | None = None,
) -> tuple[LatheWorkspace, LatheQtPresenter, LatheToolReference]:
    application()
    presenter, _catalog, reference = presenter_for(
        strategy_id,
        selection_provider=selection_provider,
    )
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    return workspace, presenter, reference


__all__ = [
    "application",
    "presenter_for",
    "selection_context",
    "tool_catalog_for",
    "workspace_for",
]
