"""Public semantic validation facade for Lathe Program IR V1."""

from __future__ import annotations

from hms_cadcam.cam.lathe.lathe_post.assembler import LatheProgramAssemblerV1, LatheProgramDiagnosticCode
from hms_cadcam.cam.lathe.lathe_post.ir import LatheProgramDiagnostic, LatheProgramIRV1


class LatheProgramValidatorV1:
    def __init__(self, assembler: LatheProgramAssemblerV1 | None = None) -> None:
        self._assembler = assembler or LatheProgramAssemblerV1()

    def validate(self, program: LatheProgramIRV1 | None) -> tuple[LatheProgramDiagnostic, ...]:
        return self._assembler.validate(program)


def validate_lathe_program(program: LatheProgramIRV1 | None) -> tuple[LatheProgramDiagnostic, ...]:
    return LatheProgramValidatorV1().validate(program)


LatheProgramValidator = LatheProgramValidatorV1

__all__ = ["LatheProgramDiagnosticCode", "LatheProgramValidator", "LatheProgramValidatorV1", "validate_lathe_program"]
