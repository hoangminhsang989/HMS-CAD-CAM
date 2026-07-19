"""Single-use controlled builder for immutable Toolpath IR artifacts."""

from __future__ import annotations

from uuid import UUID, uuid5

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.ids import (
    MachineDefinitionId, OperationId, SetupId, ToolAssemblyId,
    ToolpathArtifactId, ToolpathEventId,
)
from hms_cadcam.cam.domain.operation import ComputationToken
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, LengthUnit, SpindleSpeed
from hms_cadcam.cam.toolpath.events import (
    AnyToolpathEvent, ArcMove, CoolantState, CoolantStateEvent, DwellEvent,
    FeedMode, FeedModeEvent, LinearMove, MarkerEvent, MotionClass, RapidMove,
    SpindleState, SpindleStateEvent, ToolContextEvent,
)
from hms_cadcam.cam.toolpath.geometry import CoordinateSpace, Pose, same_pose
from hms_cadcam.cam.toolpath.model import (
    ToolpathArtifact, ToolpathCompletionStatus, ToolpathDiagnostic,
)

_EVENT_NAMESPACE = UUID("c39eceec-e491-4d36-9900-c99534b44d80")


class ToolpathBuilder:
    """Mutable construction boundary that publishes exactly one frozen artifact."""

    __slots__ = ("_aborted", "_artifact_id", "_coordinate_space", "_created_at", "_diagnostics",
        "_events", "_feed_mode", "_finalized", "_initial_pose", "_input_fingerprint",
        "_machine_fingerprint", "_machine_id", "_operation_id", "_operation_revision",
        "_setup_id", "_setup_revision", "_spindle", "_coolant", "_token", "_tool_fingerprint",
        "_tool_id", "_unit", "_wcs_fingerprint", "_current_pose")

    def __init__(self, *, artifact_id: ToolpathArtifactId, operation_id: OperationId,
                 operation_revision: Revision, computation_token: ComputationToken,
                 input_fingerprint: DependencyFingerprint, unit: LengthUnit,
                 setup_id: SetupId, setup_revision: Revision, wcs_fingerprint: ContentFingerprint,
                 tool_assembly_id: ToolAssemblyId, tool_assembly_fingerprint: ContentFingerprint,
                 machine_id: MachineDefinitionId | None = None,
                 machine_fingerprint: ContentFingerprint | None = None,
                 coordinate_space: CoordinateSpace = CoordinateSpace.SETUP_WCS,
                 created_at: str | None = None) -> None:
        if not isinstance(artifact_id, ToolpathArtifactId) or not isinstance(operation_id, OperationId):
            raise CamValidationError("Builder artifact or operation identity is invalid")
        if not isinstance(operation_revision, Revision) or not isinstance(computation_token, ComputationToken):
            raise CamValidationError("Builder computation provenance is invalid")
        if not isinstance(input_fingerprint, DependencyFingerprint) or not isinstance(unit, LengthUnit) or unit is LengthUnit.UNKNOWN:
            raise CamValidationError("Builder input fingerprint or unit is invalid")
        if not isinstance(setup_id, SetupId) or not isinstance(setup_revision, Revision) or not isinstance(wcs_fingerprint, ContentFingerprint):
            raise CamValidationError("Builder setup provenance is invalid")
        if not isinstance(tool_assembly_id, ToolAssemblyId) or not isinstance(tool_assembly_fingerprint, ContentFingerprint):
            raise CamValidationError("Builder tool provenance is invalid")
        if (machine_id is None) != (machine_fingerprint is None):
            raise CamInvariantError("Builder machine provenance must be complete")
        if machine_id is not None and (not isinstance(machine_id, MachineDefinitionId) or not isinstance(machine_fingerprint, ContentFingerprint)):
            raise CamValidationError("Builder machine provenance is invalid")
        if coordinate_space is not CoordinateSpace.SETUP_WCS:
            raise CamInvariantError("ToolpathBuilder v1 only writes SETUP_WCS")
        self._artifact_id = artifact_id
        self._operation_id = operation_id
        self._operation_revision = operation_revision
        self._token = computation_token
        self._input_fingerprint = input_fingerprint
        self._unit = unit
        self._setup_id = setup_id
        self._setup_revision = setup_revision
        self._wcs_fingerprint = wcs_fingerprint
        self._tool_id = tool_assembly_id
        self._tool_fingerprint = tool_assembly_fingerprint
        self._machine_id = machine_id
        self._machine_fingerprint = machine_fingerprint
        self._coordinate_space = coordinate_space
        self._created_at = created_at
        self._initial_pose: Pose | None = None
        self._current_pose: Pose | None = None
        self._events: list[AnyToolpathEvent] = []
        self._diagnostics: tuple[ToolpathDiagnostic, ...] = ()
        self._spindle: tuple[SpindleState, SpindleSpeed | None] | None = None
        self._coolant: CoolantState | None = None
        self._feed_mode: FeedMode | None = None
        self._finalized = False
        self._aborted = False

    @property
    def current_pose(self) -> Pose | None:
        return self._current_pose

    @property
    def event_count(self) -> int:
        return len(self._events)

    def set_initial_pose(self, pose: Pose) -> None:
        self._ensure_open()
        if self._initial_pose is not None or self._events:
            raise CamInvariantError("Initial pose can only be set once before events")
        if not isinstance(pose, Pose) or pose.position.unit is not self._unit:
            raise CamValidationError("Initial pose is invalid for builder unit")
        self._initial_pose = pose
        self._current_pose = pose

    def set_initial_process_state(self, *, feed_mode: FeedMode,
                                  spindle: SpindleState = SpindleState.OFF,
                                  spindle_speed: SpindleSpeed | None = None,
                                  coolant: CoolantState = CoolantState.OFF) -> None:
        self._ensure_position()
        if self._events:
            raise CamInvariantError("Initial process state must precede all events")
        self.set_feed_mode(feed_mode, provenance="state.initial.feed")
        self.set_spindle(spindle, spindle_speed, provenance="state.initial.spindle")
        self.set_coolant(coolant, provenance="state.initial.coolant")
        self.tool_context(provenance="state.initial.tool")

    def rapid_to(self, end: Pose, *, motion_class: MotionClass = MotionClass.NON_CUTTING,
                 rapid_rate: FeedRate | None = None, provenance: str = "motion.rapid") -> None:
        start = self._ensure_position()
        event = RapidMove(**self._common(provenance), start=start, end=end,
                          motion_class=motion_class, rapid_rate=rapid_rate)
        self._append(event)
        self._current_pose = end

    def linear_to(self, end: Pose, feed_rate: FeedRate, *, motion_class: MotionClass = MotionClass.CUTTING,
                  engagement: tuple[tuple[str, str], ...] = (), provenance: str = "motion.linear") -> None:
        start = self._ensure_position()
        event = LinearMove(**self._common(provenance), start=start, end=end, feed_rate=feed_rate,
                           motion_class=motion_class, engagement=engagement)
        self._append(event)
        self._current_pose = end

    def arc_to(self, end: Pose, *, center: Point3, plane_normal: Vector3, sweep_radians: float,
               feed_rate: FeedRate, motion_class: MotionClass = MotionClass.CUTTING,
               provenance: str = "motion.arc") -> None:
        start = self._ensure_position()
        event = ArcMove(**self._common(provenance), start=start, end=end, center=center,
                        plane_normal=plane_normal, sweep_radians=sweep_radians,
                        feed_rate=feed_rate, motion_class=motion_class)
        self._append(event)
        self._current_pose = end

    def dwell(self, duration_seconds: float, *, provenance: str = "process.dwell") -> None:
        self._ensure_position()
        self._append(DwellEvent(**self._common(provenance), duration_seconds=duration_seconds))

    def set_spindle(self, state: SpindleState, speed: SpindleSpeed | None = None,
                    *, provenance: str = "state.spindle") -> None:
        self._ensure_position()
        selected = (state, speed)
        if selected == self._spindle:
            raise CamInvariantError("Redundant spindle state transition")
        event = SpindleStateEvent(**self._common(provenance), state=state, speed=speed)
        self._append(event)
        self._spindle = selected

    def set_coolant(self, state: CoolantState, *, provenance: str = "state.coolant") -> None:
        self._ensure_position()
        if state == self._coolant:
            raise CamInvariantError("Redundant coolant state transition")
        self._append(CoolantStateEvent(**self._common(provenance), state=state))
        self._coolant = state

    def set_feed_mode(self, mode: FeedMode, *, provenance: str = "state.feed") -> None:
        self._ensure_position()
        if mode == self._feed_mode:
            raise CamInvariantError("Redundant feed-mode transition")
        self._append(FeedModeEvent(**self._common(provenance), mode=mode))
        self._feed_mode = mode

    def tool_context(self, *, provenance: str = "state.tool_context") -> None:
        self._ensure_position()
        self._append(ToolContextEvent(**self._common(provenance), tool_assembly_id=self._tool_id))

    def marker(
        self,
        semantic_key: str,
        message: str | None = None,
        *,
        metadata: tuple[tuple[str, str], ...] = (),
        provenance: str = "semantic.marker",
    ) -> None:
        self._ensure_position()
        self._append(MarkerEvent(
            **self._common(provenance), semantic_key=semantic_key,
            message=message, metadata=metadata,
        ))

    def append_event(self, event: AnyToolpathEvent) -> None:
        """Append a prebuilt event after enforcing builder identity and continuity."""
        current = self._ensure_position()
        if event.source_operation_id != self._operation_id or event.sequence_index != len(self._events):
            raise CamInvariantError("Prebuilt event does not match builder operation or sequence")
        if isinstance(event, (RapidMove, LinearMove, ArcMove)) and not same_pose(event.start, current):
            raise CamInvariantError("Prebuilt movement is discontinuous")
        self._append(event)
        if isinstance(event, (RapidMove, LinearMove, ArcMove)):
            self._current_pose = event.end

    def finalize(self, *, diagnostics: tuple[ToolpathDiagnostic, ...] = (),
                 completion_status: ToolpathCompletionStatus = ToolpathCompletionStatus.COMPLETE) -> ToolpathArtifact:
        self._ensure_open()
        self._ensure_position()
        if self._initial_pose is None:
            raise CamInvariantError("Toolpath builder requires an initial position")
        initial = self._initial_pose
        if not isinstance(diagnostics, tuple):
            raise CamValidationError("Builder diagnostics must be an immutable tuple")
        artifact = ToolpathArtifact.create(artifact_id=self._artifact_id, source_operation_id=self._operation_id,
            operation_revision=self._operation_revision, computation_token=self._token,
            input_fingerprint=self._input_fingerprint, coordinate_space=self._coordinate_space,
            unit=self._unit, setup_id=self._setup_id, setup_revision=self._setup_revision,
            wcs_fingerprint=self._wcs_fingerprint, tool_assembly_id=self._tool_id,
            tool_assembly_fingerprint=self._tool_fingerprint, machine_id=self._machine_id,
            machine_fingerprint=self._machine_fingerprint, initial_pose=initial,
            events=tuple(self._events), diagnostics=diagnostics, completion_status=completion_status,
            created_at=self._created_at)
        self._finalized = True
        return artifact

    def abort(self) -> None:
        self._ensure_open()
        self._events.clear()
        self._current_pose = None
        self._initial_pose = None
        self._aborted = True

    def _common(self, provenance: str) -> dict[str, object]:
        index = len(self._events)
        seed = f"{self._operation_id}|{self._input_fingerprint.digest}|{index}|{provenance}"
        return {"event_id": ToolpathEventId(uuid5(_EVENT_NAMESPACE, seed)), "sequence_index": index,
                "source_operation_id": self._operation_id, "provenance": provenance}

    def _append(self, event: AnyToolpathEvent) -> None:
        if any(item.event_id == event.event_id for item in self._events):
            raise CamInvariantError("Duplicate toolpath event ID")
        self._events.append(event)

    def _ensure_position(self) -> Pose:
        self._ensure_open()
        if self._current_pose is None:
            raise CamInvariantError("Toolpath builder requires an initial position")
        return self._current_pose

    def _ensure_open(self) -> None:
        if self._finalized:
            raise CamInvariantError("Toolpath builder is already finalized")
        if self._aborted:
            raise CamInvariantError("Toolpath builder was aborted")
