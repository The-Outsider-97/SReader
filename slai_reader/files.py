"""Safe file handling: hashing, crash-safe writes, unique output names, path validation.

Reader never writes to a document it opened. Everything it creates (exports, backups, uploaded copies) goes through
:func:`atomic_write` or :func:`reserve_path`, so a crash never leaves a half-written file under its final name and an
existing file is never overwritten by accident.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterator

from .errors import BadRequest, FileRejected, LimitExceeded, NotFound

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


def atomic_write(path: str | os.PathLike[str], data: bytes) -> Path:
    """Write ``data`` so that ``path`` holds either the old content or the new content, never a mixture."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    fsync_dir(target.parent)
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
