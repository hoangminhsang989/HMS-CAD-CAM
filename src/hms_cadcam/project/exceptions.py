"""Project-specific failures exposed to the application layer."""


class ProjectError(Exception):
    """Base class for controlled HMS project failures."""


class InvalidProjectNameError(ProjectError):
    """The requested Windows project name is invalid."""


class SourceFileNotFoundError(ProjectError):
    """A requested source file is missing or is not a regular file."""


class ProjectAlreadyExistsError(ProjectError):
    """The destination project already exists."""


class ProjectPermissionError(ProjectError):
    """A project path cannot be read or written."""


class ManifestMissingError(ProjectError):
    """The project manifest does not exist."""


class ManifestDecodeError(ProjectError):
    """The project manifest is not valid UTF-8 JSON."""


class UnsupportedProjectFormatError(ProjectError):
    """The manifest belongs to another format."""


class UnsupportedFormatVersionError(ProjectError):
    """The manifest or database version is not supported."""


class DatabaseMissingError(ProjectError):
    """The project database does not exist."""


class ProjectDatabaseError(ProjectError):
    """SQLite could not initialize, validate, migrate, or copy the database."""


class ProjectTransactionError(ProjectError):
    """A transactional filesystem operation failed."""


class UnsavedChangesError(ProjectError):
    """The current project has unsaved changes."""


class SessionLockError(ProjectError):
    """A project session lock could not be read, created, or released."""


class ProjectLockedError(SessionLockError):
    """Another active session owns the requested project."""


class ProjectLockUnknownError(SessionLockError):
    """A lock exists but its owner cannot be classified safely."""


class AutosaveError(ProjectError):
    """An autosave snapshot could not be created or validated."""


class AutosaveBusyError(AutosaveError):
    """Another autosave operation is already running."""


class AutosaveSnapshotError(AutosaveError):
    """Autosave snapshot data or metadata is incomplete or invalid."""


class RecoveryError(ProjectError):
    """A recovery candidate could not be assessed or restored safely."""


class RecoveryRequiredError(RecoveryError):
    """Opening must pause until the user chooses whether to recover."""

    def __init__(self, assessment: object) -> None:
        super().__init__("A valid autosave snapshot is available for recovery")
        self.assessment = assessment


class RecoverySnapshotInvalidError(RecoveryError):
    """Autosave recovery data is invalid or does not match the crashed session."""


class RecoveryTransactionError(RecoveryError):
    """Recovery failed and the original main files were restored."""


class RecoveryRollbackError(RecoveryError):
    """Recovery failed and automatic rollback could not be completed."""


class ReplacedProjectRecoveryRequiredError(RecoveryError):
    """A single valid .replaced project can be restored with user approval."""

    def __init__(self, assessment: object) -> None:
        super().__init__("A valid replaced project is available for restoration")
        self.assessment = assessment


class ReplacedProjectAmbiguousError(RecoveryError):
    """More than one .replaced candidate exists for the missing project."""


class ReplacedProjectInvalidError(RecoveryError):
    """A .replaced candidate exists but cannot be validated safely."""
