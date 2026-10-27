"""Typed errors. Every one carries an HTTP status, a stable ``code`` for the client and a message a person can act on."""
from __future__ import annotations

from typing import Any


class ReaderError(Exception):
    status = 500
    code = "reader_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class BadRequest(ReaderError):
    status, code = 400, "bad_request"


class Unauthorized(ReaderError):
    status, code = 401, "unauthorized"


class NotFound(ReaderError):
    status, code = 404, "not_found"


class Conflict(ReaderError):
    """The stored object changed since the client last saw it (``rev`` mismatch) or the file changed on disk."""
    status, code = 409, "conflict"


class FileRejected(ReaderError):
    """The file is not something Reader will open (damaged, not what it claims to be, hostile limits exceeded)."""
    status, code = 422, "file_rejected"


class UnsupportedFormat(ReaderError):
    status, code = 415, "unsupported_format"


class PasswordRequired(ReaderError):
    status, code = 401, "password_required"


class WrongPassword(ReaderError):
    status, code = 401, "wrong_password"


class LimitExceeded(ReaderError):
    status, code = 413, "limit_exceeded"


class DependencyMissing(ReaderError):
    status, code = 501, "dependency_missing"


class UnsupportedOperation(ReaderError):
    """The open document's format cannot do this (for example font colour on a fixed-layout PDF)."""
    status, code = 422, "unsupported_operation"


class AgentUnavailable(ReaderError):
    status, code = 503, "agent_unavailable"


class TimedOut(ReaderError):
    status, code = 504, "timed_out"


class Cancelled(ReaderError):
    status, code = 499, "cancelled"


class StoreCorrupt(ReaderError):
    status, code = 500, "store_corrupt"


STORAGE_REASONS = {
    # reason: (HTTP status, what a person can do about it)
    "cross_volume": (500, "The settings folder is on a redirected, mounted or virtualised location that Windows will not rename files within."),
    "permission_denied": (403, "Reader is not allowed to write there. Check that the folder and the file are not read-only and that your account may change them."),
    "locked": (423, "Another program has the file open (a backup, sync or anti-virus tool, for example). Wait a moment and try again."),
    "disk_full": (507, "There is no space left on that drive. Free some space and try again."),
    "invalid_path": (500, "The data folder path is not usable (missing, too long, or containing characters Windows does not allow)."),
    "unsupported": (501, "That folder's file system does not support the operation Reader needs."),
    "unexpected": (500, "Reader could not write its files. The technical details are in its console window."),
}


class StorageError(ReaderError, OSError):
    """A write to Reader's own storage failed. ``reason`` is one of :data:`STORAGE_REASONS`; ``details`` carries the failing step,
    errno / winerror, the exception type and a path with the user's home folder removed - never file contents or tokens.

    It is also an :class:`OSError` so older ``except OSError`` guards around best-effort writes (backups) keep working.
    """
    status, code = 500, "storage_error"

    def __init__(self, message: str, *, reason: str = "unexpected", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)
        self.reason = reason
        self.code = f"storage_{reason}"
        self.status = STORAGE_REASONS.get(reason, STORAGE_REASONS["unexpected"])[0]
