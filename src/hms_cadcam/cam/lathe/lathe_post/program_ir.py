"""Compatibility import surface for the Stage 12.4A Program IR."""

from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.lathe_post.ir import *

__all__ = ["LatheProgramIdentity", *[name for name in globals() if not name.startswith("_")]]
