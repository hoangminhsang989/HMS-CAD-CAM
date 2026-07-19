"""Small validated project resources used by the 7B.1 creation dialog."""

from hms_cadcam.cam.domain import (
    AffineTransform, CylindricalGeometry, FeedRate, FeedUnit, HolderDefinition,
    HolderDefinitionId, HolderSection, KinematicChain, KinematicMount,
    KinematicNode, KinematicSide, Length, LengthUnit, MachineAxis,
    MachineAxisType, MachineCapabilities, MachineDefinition, MachineDefinitionId,
    MachineKind, OperationCapability, ShankGeometry, SpindleCapability,
    SpindleSpeed, ToolAssembly, ToolAssemblyId, ToolDefinition, ToolDefinitionId,
    ToolFamily, Vector3, WorkEnvelope,
)


def basic_mill_resources(unit: LengthUnit) -> tuple[
    ToolDefinition, HolderDefinition, ToolAssembly, MachineDefinition
]:
    """Create a conservative end mill, holder, assembly and 1-axis MILL snapshot."""
    tool = ToolDefinition(
        ToolDefinitionId.new(), "Dao phay ngón 10", ToolFamily.END_MILL, unit,
        CylindricalGeometry(Length(10, unit), Length(20, unit)),
        Length(100, unit), Length(30, unit),
        ShankGeometry(Length(10, unit), Length(70, unit)),
    )
    holder = HolderDefinition(
        HolderDefinitionId.new(), "Holder cơ bản", unit,
        (HolderSection(Length(0, unit), Length(40, unit), Length(30, unit), Length(40, unit)),),
        Length(0, unit), interface="generic_taper",
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Cụm dao cơ bản", tool, Length(40, unit), Length(80, unit), holder
    )
    axis = MachineAxis("axis_x", "longitudinal_motion", MachineAxisType.LINEAR,
                       Vector3(1, 0, 0), Length(-500, unit), Length(500, unit), Length(0, unit))
    chain = KinematicChain((
        KinematicNode("base", None, None, KinematicSide.FIXED, KinematicMount.NONE, AffineTransform.identity(unit)),
        KinematicNode("slide", "base", "axis_x", KinematicSide.TOOL, KinematicMount.TOOL, AffineTransform.identity(unit)),
    ))
    feed_unit = FeedUnit.INCH_PER_MINUTE if unit is LengthUnit.INCH else FeedUnit.MM_PER_MINUTE
    capabilities = MachineCapabilities(
        milling=True, turning=False, live_tooling=False, probing=False,
        tapping=False, threading=False, spindle_count=1,
        maximum_feed=FeedRate(5000, feed_unit), maximum_rapid=FeedRate(10000, feed_unit),
        tool_capacity=12, coolant=(), operations=(OperationCapability.MILLING,),
    )
    machine = MachineDefinition(
        MachineDefinitionId.new(), "Máy phay cơ bản", MachineKind.MILL, unit, (axis,),
        (SpindleCapability("main", SpindleSpeed(100), SpindleSpeed(10000)),),
        capabilities, chain,
        WorkEnvelope(Length(1000, unit), Length(500, unit), Length(500, unit)),
    )
    return tool, holder, assembly, machine
