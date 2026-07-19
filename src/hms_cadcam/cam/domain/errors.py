"""Public errors raised by the pure-Python CAM domain."""


class CamError(Exception):
    """Base class for CAM domain errors."""


class CamValidationError(CamError, ValueError):
    """A CAM value violates a domain invariant."""


class CamUnitError(CamValidationError):
    """A unit is invalid or cannot be converted explicitly."""


class GeometryReferenceError(CamValidationError):
    """A geometry reference is malformed or internally inconsistent."""


class UnsupportedCamSchemaError(CamValidationError):
    """Serialized CAM data uses an unsupported format or version."""
