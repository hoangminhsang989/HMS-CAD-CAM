from uuid import uuid4

from hms_cadcam.cam.domain import *
from hms_cadcam.cam.post.lowering import PostSourceSnapshot
from hms_cadcam.cam.toolpath import CoordinateSpace, Pose, ToolpathBuilder, ToolpathArtifact


def source_snapshot(*, with_motion: bool = True) -> PostSourceSnapshot:
    unit = LengthUnit.MM
    setup_id = SetupId.new()
    operation_id = OperationId.new()
    assembly_id = ToolAssemblyId.new()
    source_id = uuid4()
    frame = WcsFrame.identity(unit)
    reference = GeometryReference(GeometryReferenceId.new(), "hms_persistent_geometry", 1, source_id,
                                 GeometryReferenceKind.BODY, GeometryRepresentationKind.BREP,
                                 GeometryFingerprint.from_payload({"body": 1}), Revision(1), subshape_selector="body:1")
    setup = Setup(setup_id, "Setup 1", SetupKind.MILL, frame, WorkOffset("PRIMARY", 1),
                  BoxStock(Length(100, unit), Length(80, unit), Length(20, unit), frame),
                  reference, SourceScope(source_id))
    tool_id = ToolDefinitionId.new()
    tool = ToolDefinition(tool_id, "Face mill", ToolFamily.FACE_MILL, unit,
                          CylindricalGeometry(Length(20, unit), Length(10, unit)),
                          Length(60, unit), Length(30, unit), ShankGeometry(Length(20, unit), Length(40, unit)))
    tool_fp = tool.content_fingerprint
    assembly = ToolAssembly(assembly_id, "Assembly 1", unit, tool_id, tool.revision, tool_fp, unit,
                            Length(20, unit), Length(30, unit), revision=Revision(0))
    operation = Operation(operation_id, CamNodeId.new(), OperationFamily.MILLING, setup_id,
                          ToolAssemblyReference.from_assembly(assembly),
                          (), OperationParameterSet("facing_2_5d", 1), revision=Revision(0))
    input_fp = DependencyFingerprint.from_payload({"post-test": 1})
    state, token = operation.artifact_state.begin(input_fp)
    operation = Operation(operation.operation_id, operation.node_id, operation.family, operation.setup_id,
                          operation.tool_assembly, operation.geometry_inputs, operation.parameters,
                          operation.machine_requirement, operation.enabled, operation.revision, state, operation.diagnostics)
    artifact = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=operation_id,
                               operation_revision=operation.revision, computation_token=token,
                               input_fingerprint=input_fp, unit=unit, setup_id=setup_id,
                               setup_revision=setup.revision, wcs_fingerprint=ContentFingerprint.from_payload(frame.to_dict()),
                               tool_assembly_id=assembly_id, tool_assembly_fingerprint=ContentFingerprint.from_payload(assembly.to_dict()))
    artifact.set_initial_pose(Pose(Point3(0, 0, 0, unit), Vector3(0, 0, 1)))
    if with_motion:
        artifact.linear_to(Pose(Point3(10, 0, 0, unit), Vector3(0, 0, 1)), FeedRate(100, FeedUnit.MM_PER_MINUTE))
        artifact.rapid_to(Pose(Point3(10, 0, 5, unit), Vector3(0, 0, 1)))
    published = artifact.finalize()
    operation_state, accepted = state.publish(token, input_fp, published.artifact_fingerprint, enabled=True)
    assert accepted
    operation = Operation(operation.operation_id, operation.node_id, operation.family, operation.setup_id,
                          operation.tool_assembly, operation.geometry_inputs, operation.parameters,
                          operation.machine_requirement, operation.enabled, operation.revision, operation_state, operation.diagnostics)
    return PostSourceSnapshot(uuid4(), operation, published, setup, assembly, tool=tool)
