"""Small validated project resources used by the 7B.1 creation dialog."""

from hms_cadcam.cam.domain import (
    AffineTransform, Angle, AngleUnit, CylindricalGeometry, DrillGeometry,
    FeedRate, FeedUnit, HolderDefinition,
    HolderDefinitionId, HolderSection, KinematicChain, KinematicMount,
    KinematicNode, KinematicSide, Length, LengthUnit, MachineAxis,
    MachineAxisType, MachineCapabilities, MachineDefinition, MachineDefinitionId,
    MachineCoolantCapability, MachineKind, OperationCapability, ShankGeometry,
    SpindleCapability,
    SpindleDirection, SpindleSpeed, TapGeometry, TappingMode, ToolAssembly,
    ToolAssemblyId, ToolCoolantCapability, ToolDefinition, ToolDefinitionId,
    ToolFamily, ToolHand,
    Vector3, WorkEnvelope,
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
        tool_capacity=12, coolant=(),
        operations=(OperationCapability.DRILLING, OperationCapability.MILLING),
    )
    machine = MachineDefinition(
        MachineDefinitionId.new(), "Máy phay cơ bản", MachineKind.MILL, unit, (axis,),
        (SpindleCapability("main", SpindleSpeed(100), SpindleSpeed(10000)),),
        capabilities, chain,
        WorkEnvelope(Length(1000, unit), Length(500, unit), Length(500, unit)),
    )
    return tool, holder, assembly, machine


def basic_drilling_resources(unit: LengthUnit) -> tuple[
    ToolDefinition,
    ToolDefinition,
    HolderDefinition,
    ToolAssembly,
    ToolAssembly,
]:
    """Create one drill and one center-drill bundle for project-owned UI use."""
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    drill = ToolDefinition(
        ToolDefinitionId.new(), "Mũi khoan 6", ToolFamily.DRILL, unit,
        DrillGeometry(
            Length(6.0 * scale, unit),
            Length(30.0 * scale, unit),
            Angle(118.0, AngleUnit.DEGREE),
        ),
        Length(100.0 * scale, unit), Length(40.0 * scale, unit),
        ShankGeometry(
            Length(6.0 * scale, unit), Length(60.0 * scale, unit),
        ),
    )
    center_drill = ToolDefinition(
        ToolDefinitionId.new(), "Mũi khoan tâm 6", ToolFamily.CENTER_DRILL, unit,
        DrillGeometry(
            Length(6.0 * scale, unit),
            Length(12.0 * scale, unit),
            Angle(90.0, AngleUnit.DEGREE),
        ),
        Length(60.0 * scale, unit), Length(20.0 * scale, unit),
        ShankGeometry(
            Length(6.0 * scale, unit), Length(40.0 * scale, unit),
        ),
    )
    holder = HolderDefinition(
        HolderDefinitionId.new(), "Holder khoan cơ bản", unit,
        (HolderSection(
            Length(0, unit), Length(35.0 * scale, unit),
            Length(30.0 * scale, unit), Length(40.0 * scale, unit),
        ),),
        Length(0, unit), interface="generic_taper",
    )
    drill_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Cụm mũi khoan 6", drill,
        Length(35.0 * scale, unit), Length(80.0 * scale, unit), holder,
    )
    center_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Cụm mũi khoan tâm 6", center_drill,
        Length(18.0 * scale, unit), Length(55.0 * scale, unit), holder,
    )
    return drill, center_drill, holder, drill_assembly, center_assembly


def basic_tapping_resources(unit: LengthUnit) -> tuple[
    ToolDefinition,
    ToolDefinition,
    HolderDefinition,
    ToolAssembly,
    ToolAssembly,
    MachineDefinition,
]:
    """Create RH/LH M8-style taps and a controller-neutral tapping machine."""
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    diameter = Length(8.0 * scale, unit)
    pitch = Length(1.25 * scale, unit)
    threaded_length = Length(20.0 * scale, unit)

    def tap(name: str, hand: ToolHand) -> ToolDefinition:
        return ToolDefinition(
            ToolDefinitionId.new(),
            name,
            ToolFamily.TAP,
            unit,
            TapGeometry(diameter, threaded_length, pitch, hand),
            Length(80.0 * scale, unit),
            Length(30.0 * scale, unit),
            ShankGeometry(diameter, Length(50.0 * scale, unit)),
        )

    right_tap = tap("Tap M8 x 1.25 RH", ToolHand.RIGHT)
    left_tap = tap("Tap M8 x 1.25 LH", ToolHand.LEFT)
    holder = HolderDefinition(
        HolderDefinitionId.new(),
        "Holder tapping cơ bản",
        unit,
        (HolderSection(
            Length(0, unit),
            Length(35.0 * scale, unit),
            Length(30.0 * scale, unit),
            Length(40.0 * scale, unit),
        ),),
        Length(0, unit),
        interface="generic_tapping_holder",
    )
    right_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Cụm Tap M8 RH",
        right_tap,
        Length(25.0 * scale, unit),
        Length(65.0 * scale, unit),
        holder,
    )
    left_assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Cụm Tap M8 LH",
        left_tap,
        Length(25.0 * scale, unit),
        Length(65.0 * scale, unit),
        holder,
    )
    axis = MachineAxis(
        "axis_x",
        "longitudinal_motion",
        MachineAxisType.LINEAR,
        Vector3(1, 0, 0),
        Length(-500, unit),
        Length(500, unit),
        Length(0, unit),
    )
    chain = KinematicChain((
        KinematicNode(
            "base", None, None, KinematicSide.FIXED, KinematicMount.NONE,
            AffineTransform.identity(unit),
        ),
        KinematicNode(
            "slide", "base", "axis_x", KinematicSide.TOOL,
            KinematicMount.TOOL, AffineTransform.identity(unit),
        ),
    ))
    feed_unit = (
        FeedUnit.INCH_PER_MINUTE
        if unit is LengthUnit.INCH else FeedUnit.MM_PER_MINUTE
    )
    capabilities = MachineCapabilities(
        milling=True,
        turning=False,
        live_tooling=False,
        probing=False,
        tapping=True,
        threading=False,
        spindle_count=1,
        maximum_feed=FeedRate(5000.0 * scale, feed_unit),
        maximum_rapid=FeedRate(10000.0 * scale, feed_unit),
        tool_capacity=12,
        coolant=(),
        operations=(OperationCapability.MILLING, OperationCapability.TAPPING),
        tapping_modes=(TappingMode.RIGID, TappingMode.FLOATING),
    )
    machine = MachineDefinition(
        MachineDefinitionId.new(),
        "Máy phay tapping cơ bản",
        MachineKind.MILL,
        unit,
        (axis,),
        (SpindleCapability(
            "main",
            SpindleSpeed(100),
            SpindleSpeed(10000),
            directions=(
                SpindleDirection.CLOCKWISE,
                SpindleDirection.COUNTERCLOCKWISE,
            ),
            synchronized_feed=True,
        ),),
        capabilities,
        chain,
        WorkEnvelope(
            Length(1000, unit), Length(500, unit), Length(500, unit),
        ),
    )
    return (
        right_tap,
        left_tap,
        holder,
        right_assembly,
        left_assembly,
        machine,
    )


def basic_reaming_resources(unit: LengthUnit) -> tuple[
    ToolDefinition,
    HolderDefinition,
    ToolAssembly,
    MachineDefinition,
]:
    """Create one D8-style reamer and a compatible milling machine bundle."""
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    diameter = Length(8.0 * scale, unit)
    reamer = ToolDefinition(
        ToolDefinitionId.new(),
        "Dao doa D8",
        ToolFamily.REAMER,
        unit,
        CylindricalGeometry(diameter, Length(25.0 * scale, unit)),
        Length(90.0 * scale, unit),
        Length(45.0 * scale, unit),
        ShankGeometry(diameter, Length(55.0 * scale, unit)),
        coolant_capabilities=(
            ToolCoolantCapability.FLOOD,
            ToolCoolantCapability.MIST,
            ToolCoolantCapability.THROUGH_TOOL,
        ),
    )
    holder = HolderDefinition(
        HolderDefinitionId.new(),
        "Holder doa cơ bản",
        unit,
        (HolderSection(
            Length(0, unit),
            Length(35.0 * scale, unit),
            Length(30.0 * scale, unit),
            Length(40.0 * scale, unit),
        ),),
        Length(0, unit),
        interface="generic_taper",
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Cụm dao doa D8",
        reamer,
        Length(35.0 * scale, unit),
        Length(75.0 * scale, unit),
        holder,
    )
    axis = MachineAxis(
        "axis_x",
        "longitudinal_motion",
        MachineAxisType.LINEAR,
        Vector3(1, 0, 0),
        Length(-500, unit),
        Length(500, unit),
        Length(0, unit),
    )
    chain = KinematicChain((
        KinematicNode(
            "base", None, None, KinematicSide.FIXED, KinematicMount.NONE,
            AffineTransform.identity(unit),
        ),
        KinematicNode(
            "slide", "base", "axis_x", KinematicSide.TOOL,
            KinematicMount.TOOL, AffineTransform.identity(unit),
        ),
    ))
    feed_unit = (
        FeedUnit.INCH_PER_MINUTE
        if unit is LengthUnit.INCH else FeedUnit.MM_PER_MINUTE
    )
    capabilities = MachineCapabilities(
        milling=True,
        turning=False,
        live_tooling=False,
        probing=False,
        tapping=False,
        threading=False,
        spindle_count=1,
        maximum_feed=FeedRate(5000.0 * scale, feed_unit),
        maximum_rapid=FeedRate(10000.0 * scale, feed_unit),
        tool_capacity=12,
        coolant=(
            MachineCoolantCapability.FLOOD,
            MachineCoolantCapability.MIST,
            MachineCoolantCapability.THROUGH_SPINDLE,
        ),
        operations=(OperationCapability.MILLING, OperationCapability.DRILLING),
    )
    machine = MachineDefinition(
        MachineDefinitionId.new(),
        "Máy phay doa cơ bản",
        MachineKind.MILL,
        unit,
        (axis,),
        (SpindleCapability(
            "main",
            SpindleSpeed(100),
            SpindleSpeed(10000),
            directions=(
                SpindleDirection.CLOCKWISE,
                SpindleDirection.COUNTERCLOCKWISE,
            ),
        ),),
        capabilities,
        chain,
        WorkEnvelope(
            Length(1000, unit), Length(500, unit), Length(500, unit),
        ),
    )
    return reamer, holder, assembly, machine
