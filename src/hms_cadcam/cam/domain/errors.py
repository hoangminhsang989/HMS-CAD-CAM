"""Public errors raised by the pure-Python CAM domain."""


class CamError(Exception):
    """Base class for CAM domain errors."""


class CamValidationError(CamError, ValueError):
    """A CAM value violates a domain invariant."""


class CamUnitError(CamValidationError):
    """A unit is invalid or cannot be converted explicitly."""


class GeometryReferenceError(CamValidationError):
    """A geometry reference is malformed or internally inconsistent."""


class CamInvariantError(CamValidationError):
    """A change conflicts with an aggregate invariant."""


class DuplicateCamIdError(CamInvariantError):
    """A child identity is already present in its aggregate."""


class CamChildNotFoundError(CamInvariantError):
    """A requested aggregate child does not exist."""


class CamSourceScopeError(CamInvariantError):
    """A geometry reference uses an undeclared project source."""


class CamRevisionConflictError(CamInvariantError):
    """A library update expected a different current revision."""


class UnsupportedCamSchemaError(CamValidationError):
    """Serialized CAM data uses an unsupported format or version."""
