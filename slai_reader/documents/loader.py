"""Open a file as a document: identify it from its bytes, check it, read it. Raises a typed error for anything it will not open."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bridge import DocumasterLink, ReaderEngines, sldc
from ..config import Settings
from ..errors import (DependencyMissing, FileRejected, LimitExceeded, PasswordRequired, ReaderError, UnsupportedFormat,
                      WrongPassword)
from ..files import sha256_file
from ..formats import FORMATS, identify, label_for
from ..safety import check_zip
from .base import OpenDocument
from .flow import FlowDocument
from .pdf import PdfDocument


def open_document(path: Path, *, settings: Settings, engines: ReaderEngines, link: DocumasterLink, password: str | None = None,
                  name: str | None = None, managed_copy: bool = False) -> OpenDocument:
    size = path.stat().st_size
    ident = identify(path, name or path.name, engines)
    fmt = ident["format"]
    if fmt is None:
        raise UnsupportedFormat(ident["note"] or f"Reader does not open this kind of file ({Path(name or path.name).suffix or 'no extension'})",
                                details={"claimed": ident["claimed"]})
    if ident.get("damaged"):
        raise FileRejected(f"This {label_for(fmt)} file looks damaged and was not opened.", details={"format": fmt})
    fingerprint = sha256_file(path)
    notes: list[str] = []
    if ident["mismatch"]:
        notes.append(f"The file name says {label_for(ident['claimed'])} but the content is {label_for(fmt)}; it was opened as {label_for(fmt)}.")

    if fmt == "pdf":
        doc: OpenDocument = PdfDocument(path, fingerprint=fingerprint, size=size, password=password, max_pages=settings.max_pdf_pages,
                                        managed_copy=managed_copy)
        doc.warnings = notes + doc.warnings
    elif fmt == "sldc":
        doc = _open_sldc(path, fingerprint, size, password, settings)
        doc.warnings = notes + doc.warnings
    else:
        doc = _open_converted(path, fmt, fingerprint, size, name or path.name, settings, link, engines, password)
        doc.warnings = notes + doc.warnings
    doc.path = path
    doc.managed_copy = managed_copy
    doc.format = fmt
    try:
        doc.stat_signature = (size, path.stat().st_mtime_ns)
    except OSError:
        pass
    return doc


# ---------------------------------------------------------------------------------------------- SLDC
def _open_sldc(path: Path, fingerprint: str, size: int, password: str | None, settings: Settings) -> FlowDocument:
    s = sldc()
    try:
        report = s.inspect_package(path)
    except s.SLDCError as exc:
        raise FileRejected(f"This is not a valid SLDC document: {exc}") from exc
    warnings: list[str] = []
    if report["container"] == "encrypted" and not password:
        raise PasswordRequired("This SLDC document is encrypted; a password is needed to read it")
    if report.get("unsupported_requires"):
        raise UnsupportedFormat("This SLDC document needs features this version of Reader does not understand "
                                f"({', '.join(report['unsupported_requires'])}). Reader will not show it with parts silently missing.",
                                details={"requires": report["unsupported_requires"]})
    try:
        package = s.read_package(path, password=password)
    except s.SLDCDecryptionError as exc:
        raise (WrongPassword("That password does not unlock this SLDC document") if password else PasswordRequired("This SLDC document is encrypted")) from exc
    except s.SLDCDependencyError as exc:
        # SLDCDependencyError also covers an unavailable scrypt implementation
        # or OpenSSL resource failure, even when cryptography is installed.
        # Preserve its actionable diagnostic instead of falsely blaming the
        # cryptography package for every HTTP 501 on an encrypted document.
        raise DependencyMissing(f"The encrypted SLDC could not be unlocked by this Python runtime: {exc}") from exc
    except s.SLDCUnsupportedError as exc:
        raise UnsupportedFormat(str(exc)) from exc
    except s.SLDCIntegrityError as exc:
        raise FileRejected(f"This SLDC document is damaged: {exc}") from exc
    except s.SLDCError as exc:
        raise FileRejected(f"This is not a valid SLDC document: {exc}") from exc
    if package.version == s.LEGACY_VERSION:
        package, _ = s.migrate_package(package)
        warnings.append("This is a legacy SLDC 0.1.0 text document. It was converted to the current model in memory; the file is not changed.")
    if len(s.extract_text(package, validate=False)) > settings.max_text_chars:
        raise LimitExceeded("This document has more text than the configured limit")
    doc = FlowDocument(package, fmt="sldc", path=path, fingerprint=fingerprint, size=size, backend="SLDC (src.utils.sldc)",
                       warnings=warnings, features=["blocks", "styles", "sections", "tables", "figures", "equations", "references", "comments", "revisions"])
    doc.encrypted = report["container"] == "encrypted"
    return doc


# ---------------------------------------------------------------------------------------------- everything else -> SLDC in memory
def _open_converted(path: Path, fmt: str, fingerprint: str, size: int, name: str, settings: Settings, link: DocumasterLink,
                    engines: ReaderEngines, password: str | None) -> FlowDocument:
    s = sldc()
    link.load()
    warnings: list[str] = []
    dropped: list[str] = []
    features: list[str] = []
    backend = ""
    package: Any = None
    if fmt in ("docx", "odt"):
        check_zip(path, f"This {label_for(fmt)} file")
    if fmt == "epub":
        from .epub import read_epub
        result = read_epub(path, link)
        package, backend = result["package"], result["backend"]
        warnings, dropped, features = result["warnings"], result["dropped"], result["features"]
    elif link.available:
        try:
            if fmt == "rtf":
                ir = _read_rtf(link, path, name)
            else:
                ir = link.read_document(path, fmt, password=password, name=name)
        except ReaderError:
            raise
        except Exception as exc:  # noqa: BLE001 - Documaster raises its own typed errors; map what we do not know
            text = type(exc).__name__
            if text in ("PasswordRequired",):
                raise PasswordRequired(str(exc)) from exc
            if text in ("WrongPassword",):
                raise WrongPassword(str(exc)) from exc
            if text in ("DependencyMissing", "UnsupportedOperation") and not (text == "DependencyMissing" and fmt in ("txt", "md")):
                raise DependencyMissing(str(exc)) from exc
            if text in ("LimitExceeded",):
                raise LimitExceeded(str(exc)) from exc
            if text != "DependencyMissing":
                raise FileRejected(f"This {label_for(fmt)} file could not be read: {str(exc)[:300]}") from exc
            # Plain text needs no parser: if the SLAI Reader engine cannot start on this computer (for example an old `colorama`),
            # SLDC itself builds the blocks. The reason is shown with the document.
            package = _plain_package(s, path, name, fmt, settings, warnings)
            warnings.append(f"The SLAI Reader parser could not start ({str(exc)[:160]}), so this file was read directly as text.")
            ir = None
        if ir is not None:
            package, backend = ir.package, f"{ir.backend} (via Documaster)"
            warnings, dropped, features = warnings + list(ir.warnings), list(ir.dropped), list(ir.features)
        else:
            backend, features = "SLDC text blocks (src.utils.sldc)", ["text"]
    elif fmt in ("txt", "md", "html", "docx") and engines.available("parser"):
        try:
            parsed = engines.parse(path)
        except Exception as exc:  # noqa: BLE001
            raise FileRejected(f"The SLAI Reader engine could not read this file: {str(exc)[:300]}") from exc
        doc_dict = s.document_from_reader_result(parsed, title=Path(name).stem or "Untitled", markdown=(fmt == "md"))
        package = _wrap(s, doc_dict)
        backend = "SLAI Reader ParserEngine"
        warnings.append("Documaster is not available, so only the text of this file is shown (structure and styling are not carried over). "
                        + (link.reason or ""))
        features = ["text"]
    else:
        raise DependencyMissing(f"Reading {label_for(fmt)} files needs Documaster. " + (link.reason or ""))
    if len(s.extract_text(package, validate=False)) > settings.max_text_chars:
        raise LimitExceeded("This document has more text than the configured limit")
    doc = FlowDocument(package, fmt=fmt, path=path, fingerprint=fingerprint, size=size, backend=backend, warnings=warnings,
                       dropped=dropped, features=features, converted=True)
    return doc


def _plain_package(s: Any, path: Path, name: str, fmt: str, settings: Settings, warnings: list[str]) -> Any:
    """Plain text or Markdown straight into SLDC blocks. The file is read once, with a size limit, and decoded as UTF-8 (BOMs honoured);
    bytes that are not UTF-8 are read as Windows-1252 and the document says so."""
    with open(path, "rb") as handle:
        raw = handle.read(settings.max_open_bytes + 1)
    if len(raw) > settings.max_open_bytes:
        raise LimitExceeded("This file is larger than the configured limit")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
            warnings.append("This file is not UTF-8; it was read as Windows-1252 text.")
    if len(text) > settings.max_text_chars:
        raise LimitExceeded("This document has more text than the configured limit")
    return _wrap(s, s.document_from_text(text.replace("\r\n", "\n").replace("\r", "\n"), title=Path(name).stem or "Untitled", markdown=(fmt == "md")))


def _wrap(s: Any, document: dict[str, Any]) -> Any:
    """A 0.1.0 text document dict -> an SLDC 1.x package, in memory."""
    legacy = s.SLDCPackage(document, version=s.LEGACY_VERSION)
    package, _ = s.migrate_package(legacy)
    return package


def _read_rtf(link: DocumasterLink, path: Path, name: str) -> Any:
    """RTF has no parser of its own anywhere in SLAI: LibreOffice converts it to ODT and Documaster's ODT reader reads that.

    (Documaster's own RTF route goes through DOCX; python-docx 1.2 cannot read the alignment values LibreOffice writes there, so ODT is
    used instead.)
    """
    import tempfile
    readers = link.module("docmodel.readers")
    tools = link.services.tools
    if not tools.soffice:
        raise DependencyMissing("Reading Rich Text (RTF) needs LibreOffice (soffice), which was not found on this computer. "
                                "Install LibreOffice, or convert the file to DOCX first.")
    with tempfile.TemporaryDirectory(prefix="reader-rtf-") as tmp:
        produced = readers.soffice_convert(link.services, path, "odt", Path(tmp))
        ir = readers.read_document(produced, "odt", link.services, name=name)
    ir.backend = "LibreOffice -> ODT -> " + ir.backend
    ir.warnings.append("Rich Text was converted to ODT by LibreOffice, then read")
    return ir
