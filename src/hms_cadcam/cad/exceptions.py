"""Controlled exceptions raised by the CAD kernel boundary."""


class CadKernelError(RuntimeError):
    """Base error for product CAD-kernel operations."""


class CadKernelUnavailableError(CadKernelError):
    """Raised when an operation requires a backend that failed to load."""


class CadDocumentNotFoundError(CadKernelError, KeyError):
    """Raised when a document ID is not owned by the current kernel."""


class CadImportError(CadKernelError):
    """Internal controlled error while reading or translating CAD input."""
