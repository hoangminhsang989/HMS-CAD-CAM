"""Deterministic Stage 12.5A authored persistence fixtures."""

from __future__ import annotations

from uuid import UUID, uuid5, NAMESPACE_URL

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.lathe.application import LatheOperationService, LatheServiceSession
from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicToolMapping
from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.lathe_post.ir import NEUTRAL_PROFILE_ID
from hms_cadcam.cam.lathe.persistence import (
    LathePostConfiguration,
    LatheProgramState,
    LatheProjectSnapshot,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from tests.unit._lathe_fixtures import (
    complete_operation,
    service_for,
    stable_uuid,
    tool_reference,
)


def persistence_snapshot(
    *,
    project_id: UUID | None = None,
    document_id: CadDocumentId | None = None,
    source_id: UUID | None = None,
    setup_id: SetupId | None = None,
    generation: int = 0,
    strategies: tuple[LatheStrategyId, ...] = tuple(LatheStrategyId),
    with_profiles: bool = False,
) -> LatheProjectSnapshot:
    project = project_id or stable_uuid("persistence/project")
    document = document_id or CadDocumentId("lathe-persistence-document")
    source = source_id or stable_uuid("persistence/source")
    setup = setup_id or SetupId(stable_uuid("persistence/setup"))
    live = LatheServiceSession(project, document, source, generation, setup)
    operations = []
    mappings = []
    for index, strategy in enumerate(strategies, start=1):
        service, reference = service_for(
            strategy,
            live_session=live,
            reference=tool_reference(index, with_profile=with_profiles),
        )
        operation = complete_operation(service, reference, strategy, index=index)
        operations.append(operation)
        mappings.append(
            BasicToolMapping(str(reference.tool_id), index, index, None, True, "")
        )
    program_id = str(
        uuid5(
            NAMESPACE_URL,
            f"https://hms.local/stage12-5a/program/{project}/{document}/{setup}",
        )
    )
    program = LatheProgramState(
        LatheProgramIdentity(
            str(project),
            str(document),
            str(source),
            generation,
            str(setup),
            program_id,
            0,
        ),
        "LATHE_PROGRAM_V1",
        tuple(operations),
        NEUTRAL_PROFILE_ID,
        LathePostConfiguration(tool_mappings=tuple(mappings)),
    )
    return LatheProjectSnapshot((program,))


__all__ = ["persistence_snapshot"]
