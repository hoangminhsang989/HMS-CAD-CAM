"""Deterministic human-readable renderer for the neutral Lathe Program IR."""

from __future__ import annotations

from hms_cadcam.cam.lathe.lathe_post.ir import LatheProgramBlock, LatheProgramIRV1, NEUTRAL_LISTING_VERSION


WARNING_LINES = (
    "HMS LATHE CONTROLLER-NEUTRAL PROGRAM IR",
    "PREVIEW ONLY",
    "NOT MACHINE-READY",
    "NO CONTROLLER POST PROFILE",
    "DO NOT RUN ON A CNC MACHINE",
)


def _number(value: object) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _payload_summary(block: LatheProgramBlock) -> str:
    payload = block.payload
    values: list[str] = []
    if hasattr(payload, "start") and hasattr(payload, "end"):
        start = payload.start
        end = payload.end
        values.append(f"Xdiam/Z=({_number(start.x_diameter_mm)},{_number(start.z_mm)})->({_number(end.x_diameter_mm)},{_number(end.z_mm)})")
        if payload.feed_mm_per_rev is not None:
            values.append(f"feed_mm_per_rev={_number(payload.feed_mm_per_rev)}")
    for name in ("operation_id", "strategy_id", "thread_strategy", "action", "direction", "speed_rpm", "units", "plane", "duration_seconds", "pitch_mm", "thread_hand", "phase_neutral"):
        if hasattr(payload, name):
            value = getattr(payload, name)
            raw = getattr(value, "value", value)
            values.append(f"{name}={_number(raw)}")
    return " ".join(values)


def render_neutral_listing(program: LatheProgramIRV1) -> str:
    """Render a listing without writing a file or emitting controller syntax."""

    if not isinstance(program, LatheProgramIRV1):
        raise TypeError("program must be LatheProgramIRV1")
    lines = [f"{line}" for line in WARNING_LINES]
    lines.extend(
        (
            f"LISTING_VERSION={NEUTRAL_LISTING_VERSION}",
            f"PROGRAM_ID={program.identity.program_id}",
            f"FINGERPRINT={program.fingerprint}",
            f"PROFILE_ID={program.profile_id}",
            "",
        )
    )
    for block in program.blocks:
        summary = _payload_summary(block)
        suffix = f" {summary}" if summary else ""
        owner = f" owner={block.operation_id}" if block.operation_id is not None else ""
        lines.append(f"{block.sequence_index:04d} {block.kind.value}{owner}{suffix}")
    return "\n".join(lines) + "\n"


neutral_program_listing = render_neutral_listing
render_program_ir_listing = render_neutral_listing


__all__ = ["WARNING_LINES", "neutral_program_listing", "render_neutral_listing", "render_program_ir_listing"]
