"""Compatibility import surface for Lathe Post profiles."""

from hms_cadcam.cam.lathe.lathe_post.profile import *

__all__ = [name for name in globals() if not name.startswith("_")]
