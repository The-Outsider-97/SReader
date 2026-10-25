"""Defences applied to every file before its content is interpreted. A document is hostile input.

* ZIP-based formats (DOCX, ODT, EPUB): entry count, total and per-entry uncompressed size, compression ratio, and unsafe member
  names are checked from the central directory **before** anything is decompressed. Nothing is ever extracted to disk.
* XML that Reader parses itself (EPUB package files) is refused if it declares entities or an internal DTD subset, which is how
  "billion laughs" and external-entity attacks are written.
* Links: only http, https and mailto are ever offered to the person, and only with an explicit click.
* Text taken from a document and given to an AI agent is wrapped and labelled as quoted material (see ``assist.py``).
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from .errors import FileRejected, LimitExceeded

MAX_ZIP_ENTRIES = 20_000
MAX_ZIP_TOTAL = 400 * 1024 * 1024
MAX_ZIP_ENTRY = 200 * 1024 * 1024
MAX_RATIO = 250
_RATIO_MIN_BYTES = 1024 * 1024
SAFE_LINK_SCHEMES = ("http", "https", "mailto")
_ENTITY = re.compile(rb"<!ENTITY|<!DOCTYPE[^>\[]*\[", re.IGNORECASE)


def check_zip(path: Path, label: str = "This document") -> zipfile.ZipFile:
    """Open a ZIP container and refuse it if it looks like a decompression bomb or has unsafe member names. Returns the open archive."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
        raise FileRejected(f"{label} is damaged: the ZIP container cannot be read ({type(exc).__name__})") from exc
    try:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise LimitExceeded(f"{label} holds {len(infos)} files; the limit is {MAX_ZIP_ENTRIES}")
        total = 0
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name.replace("\\", "/")).parts
            if name.startswith(("/", "\\")) or ".." in parts or (len(name) > 1 and name[1] == ":"):
                raise FileRejected(f"{label} contains an unsafe file name ({name[:80]!r}) and was not opened")
            if info.flag_bits & 0x1:
                raise FileRejected(f"{label} has encrypted entries; it cannot be read without the owner's tools")
            if info.file_size > MAX_ZIP_ENTRY:
                raise LimitExceeded(f"{label} holds a file larger than {MAX_ZIP_ENTRY // (1024 * 1024)} MB when unpacked")
            if info.file_size > _RATIO_MIN_BYTES and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise LimitExceeded(f"{label} compresses suspiciously well (more than {MAX_RATIO}:1) and was not opened")
            total += info.file_size
            if total > MAX_ZIP_TOTAL:
                raise LimitExceeded(f"{label} would unpack to more than {MAX_ZIP_TOTAL // (1024 * 1024)} MB")
    except Exception:
        archive.close()
        raise
    return archive


def read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Read one member with a hard cap on the bytes produced, whatever the header claims."""
    try:
        with archive.open(name) as handle:
            data = handle.read(limit + 1)
    except KeyError as exc:
        raise FileRejected(f"The package is missing {name}") from exc
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
        raise FileRejected(f"{name} cannot be read from the package ({type(exc).__name__})") from exc
    if len(data) > limit:
        raise LimitExceeded(f"{name} is larger than {limit // 1024} KB")
    return data


def refuse_xml_entities(data: bytes, label: str) -> bytes:
    """XML with entity declarations or an internal DTD subset is not parsed."""
    if _ENTITY.search(data[:200_000]):
        raise FileRejected(f"{label} declares XML entities or an internal DTD and was not parsed (it may be an entity-expansion attack)")
    return data


def safe_link(uri: str) -> str | None:
    """``uri`` if it is a scheme Reader will offer to open, else ``None`` (javascript:, file:, data:, ... are never offered)."""
    if not isinstance(uri, str):
        return None
    scheme = uri.strip().split(":", 1)[0].lower()
    return uri.strip() if scheme in SAFE_LINK_SCHEMES and len(uri) <= 2000 else None
