"""Infrastructure errors for CAM project persistence."""


class CamPersistenceError(Exception):
    """Base error raised at CAM persistence boundaries."""


class CamPersistencePayloadError(CamPersistenceError):
    """Editable CAM payload cannot be decoded atomically."""


class ToolpathArtifactStoreError(CamPersistenceError):
    """Toolpath artifact file or metadata is unsafe or invalid."""
