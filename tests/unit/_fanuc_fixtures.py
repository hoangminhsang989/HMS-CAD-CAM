"""Small deterministic NCProgramIR fixtures for the Robodrill adapter tests."""

from __future__ import annotations

from dataclasses import replace

from tests.unit._post_fixtures import source_snapshot

from hms_cadcam.cam.domain import (
    ContentFingerprint, FeedRate, FeedUnit, Length, LengthUnit, NCProgramId,
    Point3, Revision, SpindleDirection, SpindleSpeed, Vector3,
)
from hms_cadcam.cam.post import (
    ControllerToolBinding, CoordinateMode, CoordinateModeRecord, NCProgramIR,
    Plane, PlaneRecord, ProgramBeginRecord,
)
from hms_cadcam.cam.post import (
    ProductionProgramContext, ToolActivationRecord,
    UnitsRecord, WorkOffsetRecord, FeedModeRecord,
    SpindleDirectionRecord, SpindleStartRecord, SpindleStopRecord,
    RapidMotionRecord, LinearMotionRecord, ArcMotionRecord, ProgramEndRecord,
    CoolantRecord,
)
from hms_cadcam.cam.toolpath import CoolantState, FeedMode, MotionClass, Pose


def fixture_context(source, *, file_name: str = "FACE.fn", cutter: bool = False) -> ProductionProgramContext:
    return ProductionProgramContext(
        file_name=file_name,
        safe_z=Length(10.0, LengthUnit.MM),
        tool_binding=ControllerToolBinding(source.assembly.content_fingerprint, 1, 1, 1, "Face mill"),
        tool_radius=Length(10.0, LengthUnit.MM),
        stock_allowance=Length(0.0, LengthUnit.MM),
        cut_depth=Length(-1.0, LengthUnit.MM),
        use_legacy_cutter_compensation=cutter,
    )


def pose(x: float, y: float, z: float) -> Pose:
    return Pose(Point3(x, y, z, LengthUnit.MM), Vector3(0.0, 0.0, 1.0))


def basic_program(*, strategy: str = "facing_2_5d", source=None, context=None, arc: bool = False, sweep: float | None = None, feed_mode: FeedMode = FeedMode.UNITS_PER_MINUTE, coolant: bool = False, peck: bool = False) -> NCProgramIR:
    source = source or source_snapshot(with_motion=False)
    context = context or fixture_context(source, file_name=f"{strategy}.fn", cutter=strategy == "contour_2d")
    p0 = pose(0.0, 0.0, 10.0)
    p1 = pose(10.0, 0.0, 0.0 if arc else 10.0)
    p2 = pose(10.0, 0.0, 0.0)
    records = [
        ProgramBeginRecord(0, (("format", "hms_post_neutral_v1"),)),
        UnitsRecord(1, LengthUnit.MM), CoordinateModeRecord(2), PlaneRecord(3),
        WorkOffsetRecord(4, source.setup.work_offset),
        ToolActivationRecord(5, source.assembly.assembly_id, source.assembly.content_fingerprint, source.tool.tool_id, None),
        FeedModeRecord(6, feed_mode),
        SpindleDirectionRecord(7, SpindleDirection.CLOCKWISE),
        SpindleStartRecord(8, SpindleDirection.CLOCKWISE, SpindleSpeed(4000.0)),
    ]
    if coolant:
        records.append(CoolantRecord(9, CoolantState.FLOOD))
    records.append(RapidMotionRecord(len(records), p0, p1, MotionClass.NON_CUTTING, FeedRate(1000.0, FeedUnit.MM_PER_MINUTE)))
    if arc:
        effective_sweep = sweep if sweep is not None else 3.0 * 3.141592653589793 / 2.0
        import math
        end_x = 5.0 + 5.0 * math.cos(effective_sweep)
        end_y = 5.0 * math.sin(effective_sweep)
        records.append(ArcMotionRecord(len(records), p1, pose(end_x, end_y, 0.0), Point3(5.0, 0.0, 0.0, LengthUnit.MM), Vector3(0.0, 0.0, 1.0), effective_sweep, FeedRate(1.25, FeedUnit.MM_PER_REVOLUTION) if feed_mode is FeedMode.UNITS_PER_REVOLUTION else FeedRate(100.0, FeedUnit.MM_PER_MINUTE), MotionClass.CUTTING))
    else:
        feed = FeedRate(1.25, FeedUnit.MM_PER_REVOLUTION) if feed_mode is FeedMode.UNITS_PER_REVOLUTION else FeedRate(100.0, FeedUnit.MM_PER_MINUTE)
        records.append(LinearMotionRecord(len(records), p1, p2, feed, MotionClass.CUTTING))
        if peck:
            records.append(LinearMotionRecord(len(records), p2, pose(10.0, 0.0, 2.0), feed, MotionClass.RETRACT))
            records.append(LinearMotionRecord(len(records), pose(10.0, 0.0, 2.0), pose(10.0, 0.0, -2.0), feed, MotionClass.CUTTING))
            records.append(LinearMotionRecord(len(records), pose(10.0, 0.0, -2.0), pose(10.0, 0.0, 5.0), feed, MotionClass.RETRACT))
    records.append(SpindleStopRecord(len(records)))
    records.append(ProgramEndRecord(len(records)))
    records = [replace(record, sequence_index=index) for index, record in enumerate(records)]
    return NCProgramIR.create(
        program_id=NCProgramId.new(), project_id=source.project_id,
        operation_id=source.operation.operation_id, artifact_id=source.artifact.artifact_id,
        artifact_fingerprint=source.artifact.artifact_fingerprint,
        strategy_key=strategy, strategy_version=1, unit=LengthUnit.MM,
        coordinate_mode=CoordinateMode.ABSOLUTE, plane=Plane.XY,
        setup_id=source.setup.setup_id, setup_revision=Revision(0), wcs=source.setup.wcs,
        work_offset=source.setup.work_offset, tool_assembly_id=source.assembly.assembly_id,
        tool_assembly_fingerprint=source.assembly.content_fingerprint,
        records=tuple(records), production_context=context,
    )
