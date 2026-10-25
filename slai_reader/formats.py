"""What Reader can open, how a file is recognised, and what each format supports.

Recognition looks at the bytes first and the extension second. SLDC is checked before Reader's ``DetectionEngine`` because that
engine reports an SLDC file as a plain ZIP archive. An extension that disagrees with the content is reported, never trusted.

The capability table here is the single source for the format matrix (report C) and for the buttons the client greys out.
``LAYOUT`` says how a format is shown: ``fixed`` pages (PDF) are drawn by pdfium and annotated in page coordinates; every
other format is shown as ``flow`` - a reflowable SLDC document, either native (SLDC) or built in memory from the file.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bridge import ReaderEngines, sldc


@dataclass(frozen=True)
class FormatInfo:
    id: str
    label: str
    exts: tuple[str, ...]
    layout: str                 # "flow" | "fixed"
    native: bool                # shown without converting to another format first
    via: str                    # who parses it
    notes: tuple[str, ...] = ()


FORMATS: dict[str, FormatInfo] = {f.id: f for f in (
    FormatInfo("sldc", "SLDC", (".sldc",), "flow", True, "src.utils.sldc (authoritative)"),
    FormatInfo("pdf", "PDF", (".pdf",), "fixed", True, "pypdfium2 (drawing, text) + pypdf (structure)",
               ("Scanned pages have no text layer: search and highlighting need OCR, which Reader does not run.",)),
    FormatInfo("epub", "EPUB", (".epub",), "flow", False, "Reader EPUB reader -> Documaster HTML reader -> SLDC in memory",
               ("Only EPUB packages whose chapters are XHTML are read. Fixed-layout EPUB is not supported. DRM-protected books are refused.",)),
    FormatInfo("docx", "Word (DOCX)", (".docx",), "flow", False, "Documaster (python-docx) -> SLDC in memory",
               ("Page layout of the original is not reproduced; Reader shows the content as reflowable text.",)),
    FormatInfo("odt", "OpenDocument Text", (".odt",), "flow", False, "Documaster (zipfile + XML) -> SLDC in memory"),
    FormatInfo("rtf", "Rich Text", (".rtf",), "flow", False, "Documaster via LibreOffice (when installed) -> SLDC in memory",
               ("Needs LibreOffice (soffice) on this computer.",)),
    FormatInfo("html", "HTML", (".html", ".htm", ".xhtml"), "flow", False, "Documaster HTML reader -> SLDC in memory",
               ("Scripts, styles and external resources are never run or fetched.",)),
    FormatInfo("md", "Markdown", (".md", ".markdown"), "flow", False, "SLDC Markdown blocks (via Documaster) -> SLDC in memory"),
    FormatInfo("txt", "Plain text", (".txt", ".text", ".log"), "flow", False, "Reader ParserEngine / SLDC text blocks -> SLDC in memory"),
)}

EXT_TO_FORMAT: dict[str, str] = {ext: f.id for f in FORMATS.values() for ext in f.exts}


def format_for_ext(name: str) -> str | None:
    return EXT_TO_FORMAT.get(Path(name).suffix.lower())


def label_for(fmt: str | None) -> str:
    return FORMATS[fmt].label if fmt in FORMATS else (fmt or "unknown")


def identify(path: Path, name: str | None = None, engines: ReaderEngines | None = None) -> dict[str, Any]:
    """Identify a file from its bytes.

    Returns ``{"format", "claimed", "mismatch", "encrypted", "damaged", "detected_by", "note"}``. ``format`` is ``None`` when the
    content is not something Reader opens (the note says what it looks like instead).
    """
    p = Path(path)
    claimed = format_for_ext(name or p.name)
    out: dict[str, Any] = {"format": None, "claimed": claimed, "mismatch": False, "encrypted": None, "damaged": None,
                           "detected_by": "signature", "note": ""}
    try:
        with open(p, "rb") as handle:
            head = handle.read(4096)
    except OSError as exc:
        out["note"] = f"unreadable: {exc.strerror or exc}"
        return out
    found: str | None = None
    try:
        if head.startswith(b"PK\x03\x04") and sldc().sniff_sldc(p):
            found = "sldc"
    except Exception:  # noqa: BLE001 - sniffing never raises for the caller
        found = None
    if found is None:
        if head.startswith(b"%PDF-") or b"%PDF-" in head[:1024]:
            found = "pdf"
        elif head.startswith(b"{\\rtf"):
            found = "rtf"
        elif head.startswith(b"PK\x03\x04"):
            found, note = _sniff_zip(p)
            out["note"] = note
        elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            out["note"] = "Legacy binary Office file (.doc/.xls/.ppt). Convert it to DOCX with Documaster or LibreOffice first."
        elif head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
            out["note"] = "This is an image, not a document."
        else:
            found = _sniff_text(head, claimed)
            if found is None and not out["note"]:
                out["note"] = "The content is binary and not a format Reader opens."
    out["format"] = found
    if found and claimed and found != claimed and not _compatible(found, claimed):
        out["mismatch"] = True
    if found in ("pdf", "docx", "odt", "epub") and engines is not None:
        detection = engines.detect(p)
        if detection:
            out["damaged"] = detection.get("damaged")
            out["encrypted"] = detection.get("encrypted")
    return out


def _compatible(found: str, claimed: str) -> bool:
    text_like = {"txt", "md", "html"}
    return found in text_like and claimed in text_like


def _sniff_zip(path: Path) -> tuple[str | None, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "META-INF/encryption.xml" in names:
                return None, "This EPUB uses DRM or font obfuscation (META-INF/encryption.xml). Reader does not remove DRM."
            if "mimetype" in names:
                try:
                    mime = archive.read("mimetype")[:100].strip()
                except (KeyError, RuntimeError, zipfile.BadZipFile):
                    mime = b""
                if mime == b"application/epub+zip":
                    return "epub", ""
                if mime == b"application/vnd.oasis.opendocument.text":
                    return "odt", ""
            if "word/document.xml" in names:
                return "docx", ""
            if "[Content_Types].xml" in names:
                return None, "This Office file is not a Word document."
    except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
        return None, f"The ZIP container is damaged ({type(exc).__name__})."
    return None, "A ZIP archive that is not a document format Reader opens."


def _sniff_text(head: bytes, claimed: str | None) -> str | None:
    if b"\x00" in head[:2048] and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    probe = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:200].lower()
    if probe.startswith((b"<!doctype html", b"<html")):
        return "html"
    if claimed in ("md", "html", "txt"):
        return claimed
    if probe.startswith(b"<?xml") or probe.startswith(b"<"):
        return None if claimed not in ("html",) else "html"
    return "txt"


def capability_row(fmt: str) -> dict[str, Any]:
    """Static part of the format matrix; the open document refines it (see ``documents.capabilities``)."""
    info = FORMATS[fmt]
    fixed = info.layout == "fixed"
    return {
        "id": fmt, "label": info.label, "extensions": list(info.exts), "layout": info.layout, "native": info.native, "via": info.via,
        "render": True, "textSelection": True, "reflow": not fixed, "search": True,
        "highlight": True, "underline": True, "typedNotes": True, "handwritten": True, "bookmarks": True,
        "annotationStorage": "companion store (reader.sqlite)",
        "export": ["notes (Markdown, JSON)", "annotated PDF (standard annotations)" if fixed else "reading notes as SLDC"],
        "limitations": list(info.notes),
    }
