"""Alias module retained for callers that name the feature Post IR."""

from hms_cadcam.cam.lathe.lathe_post.program_ir import *

__all__ = [name for name in globals() if not name.startswith("_")]
