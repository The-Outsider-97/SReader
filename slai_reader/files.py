"""Safe file handling: hashing, crash-safe writes, unique output names, path validation.

Reader never writes to a document it opened. Everything it creates (exports, backups, uploaded copies) goes through
:func:`atomic_write` or :func:`reserve_path`, so a crash never leaves a half-written file under its final name and an
existing file is never overwritten by accident.
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import stat
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Iterator

from .errors import STORAGE_REASONS, BadRequest, FileRejected, LimitExceeded, NotFound, StorageError

log = logging.getLogger("slai_reader.storage")

_RESERVED = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def safe_filename(name: str, fallback: str = "document", max_len: int = 120) -> str:
    """A name valid on Windows, macOS and Linux that cannot leave its directory."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").split("/")[-1]
    text = _BAD.sub("_", text).strip(" .")
    if not text:
        text = fallback
    stem, dot, ext = text.rpartition(".")
    if not dot:
        stem, ext = text, ""
    if stem.upper() in _RESERVED:
        stem = f"_{stem}"
    ext = ext[:16]
    stem = stem[: max(1, max_len - len(ext) - 1)].rstrip(" .") or fallback
    return f"{stem}.{ext}" if ext else stem


def sha256_file(path: str | os.PathLike[str], chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_dir(directory: Path) -> None:
    """Flush a directory entry (not possible on Windows, where it is skipped)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------------------------------------------- storage errors
# Windows error numbers that matter here (winerror, not errno: Python maps several of them onto the same errno).
_WIN_CROSS_VOLUME = {17}                              # ERROR_NOT_SAME_DEVICE
_WIN_DISK_FULL = {39, 112}                            # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_WIN_LOCKED = {32, 33}                                # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_WIN_PERMISSION = {5, 19, 1314}                       # ACCESS_DENIED, WRITE_PROTECT, PRIVILEGE_NOT_HELD
_WIN_INVALID_PATH = {3, 87, 123, 161, 206, 267}       # PATH_NOT_FOUND, INVALID_PARAMETER, INVALID_NAME, BAD_PATHNAME, FILENAME_EXCED_RANGE, DIRECTORY
_WIN_UNSUPPORTED = {1, 50, 4390}                      # INVALID_FUNCTION, NOT_SUPPORTED, NOT_A_REPARSE_POINT

# Only failures that really are momentary are retried, a few times, with short waits (about 0.45 s in all): a file held open by a
# backup, search-indexer or anti-virus scan. Everything else fails at once.
_RETRY_DELAYS = (0.03, 0.06, 0.12, 0.24)
_sleep = time.sleep
_fallback_lock = threading.Lock()
_warned: set[str] = set()


def sanitize_path(path: object) -> str:
    """A path safe to show or log: the person's home folder is replaced by ``~``. Never contains file contents."""
    text = str(path)
    home = str(Path.home())
    if home and len(home) > 3 and text.lower().startswith(home.lower()):
        text = "~" + text[len(home):]
    return text


def os_error_info(exc: BaseException) -> dict[str, object]:
    """Everything useful in an exception that is safe to report: type, errno, winerror, strerror and the file names it carries."""
    info: dict[str, object] = {"exception": type(exc).__name__}
    for name in ("errno", "winerror", "strerror"):
        value = getattr(exc, name, None)
        if value is not None:
            info[name] = value
    for name in ("filename", "filename2"):
        value = getattr(exc, name, None)
        if value is not None:
            info[name] = sanitize_path(value)
    return info


def storage_reason(exc: BaseException, step: str = "") -> str:
    """Sort an OS error into one of :data:`slai_reader.errors.STORAGE_REASONS`."""
    win = getattr(exc, "winerror", None)
    code = getattr(exc, "errno", None)
    if win in _WIN_CROSS_VOLUME or code == errno.EXDEV:
        return "cross_volume"
    if win in _WIN_DISK_FULL or code in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return "disk_full"
    if win in _WIN_LOCKED or code in (errno.EBUSY, getattr(errno, "ETXTBSY", -1)):
        return "locked"
    if win in _WIN_PERMISSION or code in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "permission_denied"
    if win in _WIN_UNSUPPORTED or code in (errno.ENOSYS, getattr(errno, "ENOTSUP", -1), getattr(errno, "EOPNOTSUPP", -1)):
        return "unsupported"
    if win in _WIN_INVALID_PATH or code in (errno.ENOTDIR, errno.ENAMETOOLONG, errno.EINVAL, errno.EISDIR):
        return "invalid_path"
    if code in (errno.ENOENT, errno.EEXIST) and step in ("mkdir", "create_temp"):
        return "invalid_path"
    return "unexpected"


def classify_oserror(exc: BaseException, step: str, path: object, *, label: str = "the file", unchanged: bool | None = None,
                     extra: dict[str, object] | None = None) -> StorageError:
    """Turn an OS error from one named step of a write into a :class:`StorageError` with a machine-readable code."""
    reason = storage_reason(exc, step)
    details: dict[str, object] = {"operation": "write", "step": step, "reason": reason, "path": sanitize_path(path), **os_error_info(exc)}
    if unchanged is not None:
        details["previous_kept"] = unchanged
    if extra:
        details.update(extra)
    message = f"Could not save {label}. {STORAGE_REASONS[reason][1]}"
    if unchanged:
        message += f" The previous {label} was kept."
    return StorageError(message, reason=reason, details=details)


def classify_sqlite(exc: BaseException, path: object) -> StorageError:
    """SQLite's own messages for a store that cannot be written (it raises ``sqlite3.Error``, not ``OSError``)."""
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        reason = "locked"
    elif "readonly" in text or "read-only" in text or "permission" in text:
        reason = "permission_denied"
    elif "full" in text:
        reason = "disk_full"
    elif "unable to open" in text or "no such file" in text:
        reason = "invalid_path"
    else:
        reason = "unexpected"
    details = {"operation": "database", "step": "sqlite", "reason": reason, "path": sanitize_path(path), "exception": type(exc).__name__}
    return StorageError(f"Could not save to Reader's library. {STORAGE_REASONS[reason][1]}", reason=reason, details=details)


def _is_transient(exc: OSError) -> bool:
    win = getattr(exc, "winerror", None)
    return win in _WIN_LOCKED or win == 5 or exc.errno == errno.EBUSY


def _replace_with_retry(source: str, target: Path) -> int:
    """``os.replace`` with a bounded retry for momentary sharing/lock errors. Returns the number of retries that were needed."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return attempt
        except OSError as exc:
            if attempt >= len(_RETRY_DELAYS) or not _is_transient(exc):
                exc.retries = attempt                                   # type: ignore[attr-defined]
                raise
            _sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def _write_in_place(target: Path, data: bytes, backup: Path) -> None:
    """Compatibility path for locations where renaming within one folder is refused (see :func:`atomic_write`).

    Guarantees, and their limits:
    * The previous valid content is first written to ``<name>.bak`` and flushed to disk.
    * The destination is then truncated and written in full. This is **not atomic**. A crash or power cut in this window leaves an
      empty or cut-off file, which can never be mistaken for valid JSON, and ``<name>.bak`` still holds the previous content.
    * The result is read back and compared. If it differs, or if writing fails, the previous content is put back.
    * ``<name>.bak`` is removed only after the new content has been verified.
    """
    previous: bytes | None = None
    try:
        previous = target.read_bytes()
    except FileNotFoundError:
        previous = None
    if previous is not None:
        with open(backup, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
    try:
        with open(target, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.read_bytes() != data:
            raise OSError(errno.EIO, "The saved file does not match what was written")
    except BaseException:
        if previous is not None:
            try:
                with open(target, "wb") as handle:
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                pass                                                    # the .bak copy stays, and PrefsFile recovers from it
        raise
    try:
        backup.unlink()
    except OSError:
        pass


def atomic_write(path: str | os.PathLike[str], data: bytes, *, label: str = "the file", inplace_fallback: bool = False) -> Path:
    """Write ``data`` so that ``path`` holds either the old content or the new content, never a mixture.

    The data goes to a temporary file in the same folder, is flushed to disk, and replaces the destination with ``os.replace``.
    Errors are raised as :class:`StorageError` naming the step that failed (``mkdir``, ``create_temp``, ``write``, ``fsync``,
    ``replace``) with errno / winerror and a path safe to show. The temporary file is always removed.

    Momentary sharing violations (another program holding the file) are retried a few times. A refused rename between two
    places that Windows treats as different volumes (``ERROR_NOT_SAME_DEVICE``, reached through junctions, mounted folders or
    virtualised app data) is **not** worked around by copy-and-delete. Only callers that pass ``inplace_fallback=True``
    (small files that have a recovery path, such as the preferences) get :func:`_write_in_place`, whose guarantees are weaker and
    are spelled out there.
    """
    target = Path(path)
    tmp_name: str | None = None
    step = "mkdir"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        step = "create_temp"
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            step = "write"
            handle.write(data)
            handle.flush()
            step = "fsync"
            os.fsync(handle.fileno())
        step = "replace"
        _replace_with_retry(tmp_name, target)
    except OSError as exc:
        if isinstance(exc, StorageError):
            raise
        cleaned = _remove_quietly(tmp_name)
        extra: dict[str, object] = {"temp_removed": cleaned} if tmp_name else {}
        if getattr(exc, "retries", 0):
            extra["retries"] = exc.retries                              # type: ignore[attr-defined]
        if step == "replace" and inplace_fallback and storage_reason(exc, step) == "cross_volume":
            return _fallback_after_refused_replace(target, data, exc, label, extra)
        raise classify_oserror(exc, step, target, label=label, unchanged=True, extra=extra) from exc
    except BaseException:
        _remove_quietly(tmp_name)
        raise
    fsync_dir(target.parent)
    return target


def _remove_quietly(tmp_name: str | None) -> bool:
    if not tmp_name:
        return True
    try:
        os.unlink(tmp_name)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _read_ok(target: Path, backup: Path) -> bool:
    """After a failed in-place write: is the previous content still available, in the file or in the backup?"""
    return backup.exists() or target.exists()


def _fallback_after_refused_replace(target: Path, data: bytes, original: OSError, label: str, extra: dict[str, object]) -> Path:
    backup = target.with_name(target.name + ".bak")
    with _fallback_lock:
        try:
            _write_in_place(target, data, backup)
        except OSError as second:
            err = classify_oserror(second, "in_place_write", target, label=label, unchanged=None,
                                   extra={**extra, "fallback": "in_place", "replace_error": os_error_info(original),
                                          "backup_kept": backup.exists()})
            err.details["previous_kept"] = _read_ok(target, backup)
            raise err from second
    if str(target) not in _warned:                                      # once per file: a slider produces many saves
        _warned.add(str(target))
        log.warning("storage: rename refused (%s); settings are written in place with a backup: %s", os_error_info(original), sanitize_path(target))
    return target


def reserve_path(directory: Path, stem: str, ext: str) -> Path:
    """Exclusively create an empty file named ``stem[ (n)]ext`` and return it. An existing file is never reused."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(stem, "document", 100)
    ext = ext if ext.startswith(".") or not ext else f".{ext}"
    for attempt in range(10_000):
        name = f"{stem}{ext}" if attempt == 0 else f"{stem} ({attempt}){ext}"
        target = directory / name
        try:
            with open(target, "xb"):
                pass
            return target
        except FileExistsError:
            continue
    raise FileRejected("Could not find a free file name for the result")


def ensure_inside(root: Path, candidate: Path) -> Path:
    """``candidate`` resolved, and proven to be inside ``root`` (no ``..``, no symlink escape)."""
    root_r = root.resolve()
    cand = candidate.resolve()
    try:
        cand.relative_to(root_r)
    except ValueError as exc:
        raise NotFound("Not found") from exc
    return cand


def resolve_user_path(raw: str, *, max_bytes: int, allow_network: bool = False) -> Path:
    """Validate a path the person chose. It must name an existing regular file within the size limit."""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise BadRequest("Give the full path of a file")
    text = raw.strip().strip('"')
    if not allow_network and (text.startswith("\\\\") or text.startswith("//")):
        raise FileRejected("Network paths (\\\\server\\share) are not opened by default; copy the file to this computer, "
                           "or start Reader with --allow-network-paths")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise BadRequest("Give the full path of a file (it must start with a drive letter or a slash)")
    try:
        info = os.stat(path)
    except FileNotFoundError as exc:
        raise NotFound(f"There is no file at {path}") from exc
    except OSError as exc:
        raise FileRejected(f"That file cannot be accessed: {exc.strerror or exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise FileRejected("That is not a regular file")
    if info.st_size > max_bytes:
        raise LimitExceeded(f"That file is {info.st_size // (1024 * 1024)} MB; the limit is {max_bytes // (1024 * 1024)} MB",
                            details={"limit_bytes": max_bytes})
    if info.st_size == 0:
        raise FileRejected("That file is empty")
    return path.resolve()


def stat_signature(path: Path) -> tuple[int, int]:
    """(size, mtime_ns): the cheap part of detecting that a file changed on disk."""
    info = os.stat(path)
    return info.st_size, info.st_mtime_ns


def iter_chunks(path: Path, size: int = 1 << 20) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        while chunk := handle.read(size):
            yield chunk
