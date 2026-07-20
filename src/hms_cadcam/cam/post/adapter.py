"""Controller-neutral post adapter protocol."""

from __future__ import annotations

from typing import Protocol

from hms_cadcam.cam.post.model import NCProgramIR, PostDiagnostic, PostProcessorCapabilities, PostProcessorDefinition, PostRequest


class PostProcessorAdapter(Protocol):
    """Adapter boundary; implementations cannot access UI, CAD or generators."""

    def capabilities(self) -> PostProcessorCapabilities: ...

    def validate_request(self, request: PostRequest) -> tuple[PostDiagnostic, ...]: ...

    def validate_program_ir(self, program: NCProgramIR) -> tuple[PostDiagnostic, ...]: ...

    def lower_program_ir(self, program: NCProgramIR) -> NCProgramIR: ...

    def format_program(self, program: NCProgramIR, definition: PostProcessorDefinition) -> str: ...

    def validate_output(self, text: str, program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]: ...
