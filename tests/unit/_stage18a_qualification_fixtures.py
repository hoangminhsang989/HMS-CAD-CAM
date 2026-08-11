"""Production-shaped Stage18A fixtures; values remain engineering-only."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Callable

from hms_cadcam.cam.post import ProgramAssemblyService
from hms_cadcam.cam.post.assembly_model import ProgramAssemblyResult
from hms_cadcam.cam.post.model import NCProgramIR, NCRecord, PostStatistics
from hms_cadcam.cam.qualification import (
    StaticQualificationInput,
    StockEnvelope,
    ToolQualificationInput,
    robodrill_alpha_d21mib_contract,
)
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_program_assembly import _request, _source_variant


def assembly_result(
    strategies: tuple[str, ...] = ("facing_2_5d",),
) -> ProgramAssemblyResult:
    first = _runtime_source()
    sources = [first]
    sources.extend(_source_variant(first, strategy) for strategy in strategies[1:])
    execution = ProgramAssemblyService().assemble(_request(sources))
    assert execution.accepted and execution.result is not None
    return execution.result


def tool_inputs(
    result: ProgramAssemblyResult,
    *,
    diameter_mm: float | None = 10.0,
    overall_length_mm: float | None = 100.0,
    taper: str | None = "BT30",
) -> tuple[ToolQualificationInput, ...]:
    unique = {}
    for section in result.plan.sections:
        binding = section.tool_binding
        unique.setdefault(
            section.tool_assembly_fingerprint,
            ToolQualificationInput(
                section.tool_assembly_fingerprint,
                binding.tool_station,
                binding.length_offset,
                binding.diameter_offset,
                diameter_mm,
                overall_length_mm,
                taper,
            ),
        )
    return tuple(unique.values())


def qualification_input(
    result: ProgramAssemblyResult | None = None,
    *,
    stock: StockEnvelope | None = StockEnvelope(100.0, 100.0, 50.0),
    tools: tuple[ToolQualificationInput, ...] | None = None,
):
    result = result or assembly_result()
    return StaticQualificationInput(
        result,
        robodrill_alpha_d21mib_contract(),
        tools if tools is not None else tool_inputs(result),
        stock,
    )


def mutate_first_program(
    result: ProgramAssemblyResult,
    mutation: Callable[[NCProgramIR], NCProgramIR],
) -> ProgramAssemblyResult:
    sections = list(result.plan.sections)
    program = mutation(sections[0].program_ir)
    sections[0] = replace(sections[0], program_ir=program, section_fingerprint=None)
    plan = replace(result.plan, sections=tuple(sections), plan_fingerprint=None)
    return replace(result, plan=plan, result_fingerprint=None)


def mutate_first_record(
    result: ProgramAssemblyResult,
    predicate: Callable[[NCRecord], bool],
    mutation: Callable[[NCRecord], NCRecord],
) -> ProgramAssemblyResult:
    def mutate_program(program: NCProgramIR) -> NCProgramIR:
        records = list(program.records)
        index = next(index for index, record in enumerate(records) if predicate(record))
        records[index] = mutation(records[index])
        values = tuple(records)
        return replace(
            program,
            records=values,
            statistics=PostStatistics.calculate(values),
            program_fingerprint=None,
        )

    return mutate_first_program(result, mutate_program)


def mutate_text(result: ProgramAssemblyResult, text: str) -> ProgramAssemblyResult:
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return replace(
        result,
        canonical_text=text,
        output_checksum=checksum,
        result_fingerprint=None,
    )


__all__ = [
    "assembly_result", "mutate_first_program", "mutate_first_record",
    "mutate_text", "qualification_input", "tool_inputs",
]
