"""Production Rest Pocket adapter constrained by a validated MaterialState.

The normal Pocket generator remains responsible for safe motion, depth levels,
linking and publication shape.  This adapter only selects offset loops whose
material-state cells contain meaningful residue, so Rest Pocket cannot silently
degenerate into a full Pocket rerun.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math
from uuid import UUID, uuid5

from hms_cadcam.cam.application.pocket import (
    PocketGenerationError,
    PocketGenerator,
    PocketInputs,
    build_pocket_offset_loops,
    _lead_start,
    pocket_lead_independent_fingerprint,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContourCurveKind,
    ContourLoop,
    ContourSegment,
    ContentFingerprint,
    DiagnosticCode,
    PocketCuttingDirection,
    Point3,
    ToolpathArtifactId,
    Vector3,
)
from hms_cadcam.cam.material_state import MaterialState
from hms_cadcam.cam.application.rest_region import RestRegion, extract_rest_regions
from hms_cadcam.cam.toolpath import Pose, ToolpathArtifact, ToolpathBuilder


_NO_REST_ARTIFACT_NAMESPACE = UUID("c366eff7-45a7-4e09-92d8-c20f9279d22f")


@dataclass(frozen=True, slots=True)
class RestPocketInputs:
    pocket: PocketInputs
    parent_state: MaterialState
    regions: tuple[RestRegion, ...] = ()
    no_rest_material: bool = False


class MaterialStateResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NO_COMPATIBLE_MATERIAL_STATE = "NO_COMPATIBLE_MATERIAL_STATE"
    AMBIGUOUS = "AMBIGUOUS"
    STALE = "STALE"
    CORRUPT = "CORRUPT"
    UNSUPPORTED = "UNSUPPORTED"
    NO_REST_MATERIAL = "NO_REST_MATERIAL"


_MATERIAL_STATE_STATUS_VI = {
    MaterialStateResolutionStatus.RESOLVED: "Sẵn sàng",
    MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE: (
        "Không tìm thấy nguồn phần dư phù hợp"
    ),
    MaterialStateResolutionStatus.AMBIGUOUS: "Có nhiều nguồn phần dư phù hợp",
    MaterialStateResolutionStatus.STALE: "Nguồn phần dư cần tính lại",
    MaterialStateResolutionStatus.CORRUPT: "Dữ liệu phần dư không hợp lệ",
    MaterialStateResolutionStatus.UNSUPPORTED: (
        "Cấu hình phần dư chưa được hỗ trợ"
    ),
    MaterialStateResolutionStatus.NO_REST_MATERIAL: (
        "Không còn phần dư cần gia công"
    ),
}


def material_state_status_vi(status: MaterialStateResolutionStatus) -> str:
    """Return the frozen Vietnamese-first Rest material-state status text."""
    if not isinstance(status, MaterialStateResolutionStatus):
        raise TypeError("Material-state status is invalid")
    return _MATERIAL_STATE_STATUS_VI[status]


@dataclass(frozen=True, slots=True)
class MaterialStateResolution:
    status: MaterialStateResolutionStatus
    state: MaterialState | None = None
    producer_operation_id: object | None = None
    message: str = ""


def resolve_material_state(candidates: tuple[tuple[object, MaterialState], ...],
                           *, setup_fingerprint: ContentFingerprint) -> MaterialStateResolution:
    """Resolve one compatible COMPLETE state without relying on list position."""
    compatible = tuple(
        item for item in candidates
        if item[1].status.value == "COMPLETE"
        and item[1].setup_fingerprint == setup_fingerprint
    )
    if not compatible:
        return MaterialStateResolution(MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE,
                                       message="No compatible complete material state")
    if len(compatible) > 1:
        return MaterialStateResolution(MaterialStateResolutionStatus.AMBIGUOUS,
                                       message="Multiple compatible material states")
    producer, state = compatible[0]
    return MaterialStateResolution(MaterialStateResolutionStatus.RESOLVED, state, producer)


class RestPocketGenerator:
    """Generate a standard HMS toolpath over state-proven residual loops."""

    def __init__(self) -> None:
        self._pocket = PocketGenerator()

    def resolve_inputs(self, *args, parent_state: MaterialState, **kwargs) -> RestPocketInputs:
        cached = kwargs.pop("cached_template", None)
        if not isinstance(parent_state, MaterialState):
            raise PocketGenerationError(
                DiagnosticCode.POCKET_GENERATION_FAILED,
                "Rest Pocket requires a valid MaterialState",
            )
        inputs = self._pocket.resolve_inputs(*args, **kwargs)
        if isinstance(cached, RestPocketInputs):
            candidate = RestPocketInputs(
                replace(inputs, offset_loops=cached.pocket.offset_loops),
                parent_state,
                cached.regions,
                cached.no_rest_material,
            )
            if (
                cached.parent_state.fingerprint == parent_state.fingerprint
                and pocket_lead_independent_fingerprint(candidate.pocket)
                == pocket_lead_independent_fingerprint(cached.pocket)
            ):
                return candidate
        if not parent_state.has_rest_material:
            return RestPocketInputs(
                replace(inputs, offset_loops=()), parent_state,
                no_rest_material=True,
            )
        regions = extract_rest_regions(parent_state)
        if not regions:
            return RestPocketInputs(
                replace(inputs, offset_loops=()), parent_state,
                no_rest_material=True,
            )
        loops = self._region_offset_loops(inputs, regions, parent_state)
        if not loops:
            return RestPocketInputs(
                replace(inputs, offset_loops=()), parent_state, regions,
                no_rest_material=True,
            )
        return RestPocketInputs(replace(inputs, offset_loops=loops), parent_state, regions)

    def begin(self, inputs: RestPocketInputs):
        computing, token = self._pocket.begin(inputs.pocket)
        return replace(inputs, pocket=computing), token

    def generate(self, inputs: RestPocketInputs):
        if inputs.no_rest_material:
            return self._generate_no_rest_artifact(inputs.pocket)
        self._validate_leads(inputs)
        return self._pocket.generate(inputs.pocket)

    def regenerate_lead_only(self, inputs: RestPocketInputs):
        """Reassemble cached Rest cut geometry with one changed safe Lead-In."""
        if inputs.no_rest_material:
            return self._generate_no_rest_artifact(inputs.pocket)
        self._validate_leads(inputs)
        return self._pocket.regenerate_lead_only(inputs.pocket)

    @staticmethod
    def _validate_leads(inputs: RestPocketInputs) -> None:
        length = inputs.pocket.strategy.lead_in_length.value
        if length <= 0.0:
            return
        for loop in inputs.pocket.offset_loops:
            lead_start = _lead_start(loop, length)
            lead = ContourSegment(
                ContourCurveKind.LINE, lead_start, loop.segments[0].start,
            )
            if not RestPocketGenerator._segments_are_contained_in_residue(
                (lead,), inputs.parent_state,
            ):
                raise PocketGenerationError(
                    DiagnosticCode.POCKET_ENTRY_UNSAFE,
                    "Rest Pocket Lead-In leaves residual material",
                )

    @staticmethod
    def _generate_no_rest_artifact(inputs: PocketInputs) -> ToolpathArtifact:
        """Publish a valid COMPLETE zero-motion result without fake cutting."""
        operation = inputs.operation
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise PocketGenerationError(
                DiagnosticCode.POCKET_GENERATION_FAILED,
                "NO_REST_MATERIAL publication requires a current computation token",
            )
        builder = ToolpathBuilder(
            artifact_id=ToolpathArtifactId(uuid5(
                _NO_REST_ARTIFACT_NAMESPACE,
                f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}",
            )),
            operation_id=operation.operation_id,
            operation_revision=operation.revision,
            computation_token=token,
            input_fingerprint=inputs.input_fingerprint,
            unit=inputs.strategy.unit,
            setup_id=inputs.setup.setup_id,
            setup_revision=inputs.setup.revision,
            wcs_fingerprint=ContentFingerprint.from_payload(inputs.setup.wcs.to_dict()),
            tool_assembly_id=inputs.assembly.assembly_id,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(
                inputs.assembly.to_dict()
            ),
            machine_id=inputs.machine.machine_id,
            machine_fingerprint=inputs.machine.content_fingerprint,
        )
        builder.set_initial_pose(Pose(
            Point3(
                0.0,
                0.0,
                inputs.strategy.clearance_height.value,
                inputs.strategy.unit,
            ),
            Vector3(0.0, 0.0, 1.0),
        ))
        return builder.finalize()

    @staticmethod
    def _region_offset_loops(
        inputs: PocketInputs,
        regions: tuple[RestRegion, ...],
        state: MaterialState,
    ) -> tuple[ContourLoop, ...]:
        """Build deterministic per-region paths; never bridge disconnected residue."""
        selected: list[ContourLoop] = []
        initial_offset = (
            inputs.tool_diameter / 2.0
            + inputs.strategy.radial_stock_allowance.value
        )
        for region in regions:
            try:
                candidates = build_pocket_offset_loops(
                    RestPocketGenerator._coalesce_collinear_lines(
                        region.exterior, inputs.strategy.tolerance.value
                    ),
                    initial_offset,
                    inputs.strategy.stepover.value,
                    inputs.strategy.tolerance.value,
                    terminal_coverage_radius=inputs.tool_diameter / 2.0,
                )
            except PocketGenerationError:
                continue
            if inputs.strategy.cutting_direction is PocketCuttingDirection.CONVENTIONAL:
                candidates = tuple(loop.reversed() for loop in candidates)
            selected.extend(
                loop for loop in candidates
                if RestPocketGenerator._loop_is_contained_in_residue(
                    loop, state,
                )
            )
        return tuple(selected)

    @staticmethod
    def _coalesce_collinear_lines(
        loop: ContourLoop, tolerance: float,
    ) -> ContourLoop:
        """Collapse grid-boundary runs before the standard Pocket offset core."""
        merged: list[ContourSegment] = []
        for segment in loop.segments:
            if not merged:
                merged.append(segment)
                continue
            previous = merged[-1]
            if (
                previous.kind is ContourCurveKind.LINE
                and segment.kind is ContourCurveKind.LINE
            ):
                first = (
                    previous.end.x - previous.start.x,
                    previous.end.y - previous.start.y,
                )
                second = (
                    segment.end.x - segment.start.x,
                    segment.end.y - segment.start.y,
                )
                cross = first[0] * second[1] - first[1] * second[0]
                dot = first[0] * second[0] + first[1] * second[1]
                if abs(cross) <= tolerance and dot > 0.0:
                    merged[-1] = ContourSegment(
                        ContourCurveKind.LINE, previous.start, segment.end
                    )
                    continue
            merged.append(segment)
        return ContourLoop(tuple(merged), loop.orientation)

    @staticmethod
    def _loop_has_residue(loop: ContourLoop, state: MaterialState) -> bool:
        """Bounded cell-mask extraction; covers interiors and edge corridors.

        Every precision-policy cell in the loop bounds is considered, not only
        polygon vertices.  The conservative mask intentionally may retain a
        small valid corridor, but never drops enclosed/edge residue.
        """
        points = [segment.start for segment in loop.segments] + [segment.end for segment in loop.segments]
        if not points:
            return False
        min_x = max(0, math.floor(min(point.x for point in points) / state.cell_size_x) - 1)
        max_x = min(state.width - 1, math.ceil(max(point.x for point in points) / state.cell_size_x) + 1)
        min_y = max(0, math.floor(min(point.y for point in points) / state.cell_size_y) - 1)
        max_y = min(state.height - 1, math.ceil(max(point.y for point in points) / state.cell_size_y) + 1)
        threshold = state.precision.residual_threshold
        for row in range(min_y, max_y + 1):
            for column in range(min_x, max_x + 1):
                if state.top_heights[row * state.width + column] > threshold:
                    return True
        return False

    @staticmethod
    def _loop_is_contained_in_residue(
        loop: ContourLoop,
        state: MaterialState,
        *,
        cutter_radius: float = 0.0,
    ) -> bool:
        """Prove every densely sampled cutter centre stays in one residue region.

        MaterialState construction already accounts for the cutter profile.  At
        an exterior residue edge the cutter may safely overlap air/cleared stock;
        requiring the full disc inside residue would reject valid corner paths.
        Centre containment is the authority that prevents bridges through gaps
        and holes while tool-profile removal remains modeled by MaterialState.
        """
        return RestPocketGenerator._segments_are_contained_in_residue(
            loop.segments, state, cutter_radius=cutter_radius,
        )

    @staticmethod
    def _segments_are_contained_in_residue(
        segments: tuple[ContourSegment, ...],
        state: MaterialState,
        *,
        cutter_radius: float = 0.0,
    ) -> bool:
        if cutter_radius < 0.0 or not math.isfinite(cutter_radius):
            raise ValueError("Rest cutter radius is invalid")
        maximum_step = min(state.cell_size_x, state.cell_size_y) * 0.5
        threshold = state.precision.residual_threshold
        maximum_x = state.width * state.cell_size_x
        maximum_y = state.height * state.cell_size_y
        for segment in segments:
            length = math.hypot(
                segment.end.x - segment.start.x,
                segment.end.y - segment.start.y,
            )
            sample_count = max(1, math.ceil(length / maximum_step))
            for index in range(sample_count + 1):
                ratio = index / sample_count
                x = segment.start.x + (segment.end.x - segment.start.x) * ratio
                y = segment.start.y + (segment.end.y - segment.start.y) * ratio
                if (
                    x - cutter_radius < 0.0
                    or y - cutter_radius < 0.0
                    or x + cutter_radius > maximum_x
                    or y + cutter_radius > maximum_y
                ):
                    return False
                min_column = max(
                    0, math.floor((x - cutter_radius) / state.cell_size_x)
                )
                max_column = min(
                    state.width - 1,
                    math.floor((x + cutter_radius) / state.cell_size_x),
                )
                min_row = max(
                    0, math.floor((y - cutter_radius) / state.cell_size_y)
                )
                max_row = min(
                    state.height - 1,
                    math.floor((y + cutter_radius) / state.cell_size_y),
                )
                for row in range(min_row, max_row + 1):
                    cell_y = (row + 0.5) * state.cell_size_y
                    for column in range(min_column, max_column + 1):
                        cell_x = (column + 0.5) * state.cell_size_x
                        if math.hypot(cell_x - x, cell_y - y) > cutter_radius:
                            continue
                        if state.top_heights[row * state.width + column] <= threshold:
                            return False
        return True
