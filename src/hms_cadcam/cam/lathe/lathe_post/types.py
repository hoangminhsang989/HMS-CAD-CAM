"""Stable semantic type aliases for Lathe Post Foundation V1."""

from hms_cadcam.cam.lathe.lathe_post.assembler import LatheProgramDiagnosticCode
from hms_cadcam.cam.lathe.lathe_post.ir import LatheProgramBlockKind, LatheSemanticPlane, LatheSpindleAction, LatheSpindleDirection, LatheUnits
from hms_cadcam.cam.lathe.lathe_post.service import LatheProgramReadiness

LatheProgramBlockType = LatheProgramBlockKind
LatheProgramReadinessState = LatheProgramReadiness

__all__ = ["LatheProgramBlockKind", "LatheProgramBlockType", "LatheProgramDiagnosticCode", "LatheProgramReadiness", "LatheProgramReadinessState", "LatheSemanticPlane", "LatheSpindleAction", "LatheSpindleDirection", "LatheUnits"]
