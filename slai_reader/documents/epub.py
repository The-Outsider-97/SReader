"""EPUB: read the package (container.xml -> OPF -> spine), turn each XHTML chapter into SLDC blocks with Documaster's HTML reader,
and join them into one in-memory SLDC document. Chapters become logical pages (``page_break_before``).

Only reflowable EPUB with XHTML chapters is read. Fixed-layout EPUB, DRM (``META-INF/encryption.xml``) and scripted content are
refused or ignored; nothing is fetched and nothing is executed.
"""
from __future__ import annotations

import base64
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..bridge import DocumasterLink, sldc
from ..errors import DependencyMissing, FileRejected, UnsupportedFormat
from ..safety import check_zip, read_member, refuse_xml_entities

_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
_MAX_CHAPTER = 8 * 1024 * 1024
_MAX_IMAGE = 3 * 1024 * 1024
_MAX_SPINE = 2000
_IMG = re.compile(rb"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])([^\"']+)\2", re.IGNORECASE)
_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _xml(data: bytes, label: str) -> ET.Element:
    refuse_xml_entities(data, label)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise FileRejected(f"{label} is not well-formed XML: {exc}") from exc


def _join(base: str, href: str) -> str:
    href = href.split("#", 1)[0].split("?", 1)[0]
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), href))


def read_epub(path: Path, link: DocumasterLink) -> dict[str, Any]:
    """Returns ``{"package", "backend", "warnings", "dropped", "features"}``."""
    if not link.available:
        raise DependencyMissing("Reading EPUB needs Documaster's HTML reader. " + (link.reason or ""))
    html_module = link.module("docmodel.read_html")
    s = sldc()
    archive = check_zip(path, "This EPUB")
    warnings: list[str] = []
    try:
        names = set(archive.namelist())
        if "META-INF/encryption.xml" in names:
            raise UnsupportedFormat("This EPUB uses DRM or font obfuscation. Reader does not remove DRM and cannot open it.")
        container = _xml(read_member(archive, "META-INF/container.xml", 64 * 1024), "container.xml")
        root = container.find(".//c:rootfile", _NS)
        if root is None or not root.get("full-path"):
            raise FileRejected("This EPUB has no package document (container.xml names no rootfile)")
        opf_path = posixpath.normpath(root.get("full-path"))
        if opf_path.startswith(("..", "/")) or opf_path not in names:
            raise FileRejected("This EPUB's package document is missing or has an unsafe path")
        opf = _xml(read_member(archive, opf_path, 4 * 1024 * 1024), "the package document")
        meta = opf.find("o:metadata", _NS)

        def dc(tag: str) -> list[str]:
            return [(e.text or "").strip() for e in (meta.findall(f"dc:{tag}", _NS) if meta is not None else []) if (e.text or "").strip()]

        title = (dc("title") or [path.stem])[0][:300]
        authors = dc("creator")[:20]
        language = (dc("language") or ["en"])[0].split("_")[0][:39] or "en"
        manifest = {i.get("id"): i for i in opf.findall("o:manifest/o:item", _NS) if i.get("id")}
        spine = [r.get("idref") for r in opf.findall("o:spine/o:itemref", _NS) if r.get("idref")][:_MAX_SPINE]
        if not spine:
            raise FileRejected("This EPUB has an empty reading order (spine)")
        meta_props = " ".join(str(e.attrib) for e in opf.findall(".//o:meta", _NS)).lower()
        if "pre-paginated" in meta_props:
            raise UnsupportedFormat("This is a fixed-layout EPUB. Reader only shows reflowable EPUB books.")
        blocks: list[dict[str, Any]] = []
        assets: dict[str, Any] = {}
        used = skipped = 0
        for idref in spine:
            item = manifest.get(idref)
            if item is None or "html" not in (item.get("media-type") or ""):
                skipped += 1
                continue
            href = _join(opf_path, item.get("href", ""))
            if href.startswith(("..", "/")) or href not in names:
                skipped += 1
                continue
            raw = read_member(archive, href, _MAX_CHAPTER)
            raw = _inline_images(raw, href, archive, names)
            result = html_module.read_html_bytes(raw)
            chapter = result["blocks"]
            if not chapter:
                continue
            attrs = dict(chapter[0].get("attributes") or {})
            attrs["page_break_before"] = True
            chapter[0] = {**chapter[0], "attributes": attrs}
            blocks.extend(chapter)
            for a in result["assets"]:
                assets.setdefault(a.id, a)
            warnings.extend(w for w in result["warnings"] if w not in warnings)
            used += 1
            if len(blocks) > s.MAX_BLOCKS - 100:
                warnings.append(f"Only the first {len(blocks)} blocks were read (the SLDC block limit)")
                break
        if skipped:
            warnings.append(f"{skipped} item(s) of the reading order were not XHTML or were missing and were skipped")
        if not blocks:
            raise FileRejected("No readable text was found in this EPUB")
        publication = {"authors": authors} if authors else None
        package = s.new_package(title, blocks, language=language, assets=list(assets.values()), publication=publication,
                                provenance=[s.provenance_entry("reader.epub", agent="slai-reader", source=path.name, chapters=used)])
        s.validate_package(package)
        return {"package": package, "backend": "Reader EPUB reader + Documaster HTML reader", "warnings": warnings,
                "dropped": ["EPUB styling (CSS), scripts, audio/video and fonts"],
                "features": ["chapters", "headings", "paragraphs", "lists", "tables", "images", "links"]}
    finally:
        archive.close()


def _inline_images(raw: bytes, chapter: str, archive: Any, names: set[str]) -> bytes:
    """Replace ``<img src="images/a.png">`` by an embedded data URI read from the package (PNG/JPEG/GIF/WebP only), so the HTML reader
    can import it. Nothing outside the package is read and nothing is downloaded."""
    def sub(m: re.Match[bytes]) -> bytes:
        src = m.group(3).decode("utf-8", "ignore")
        if src.startswith(("data:", "http:", "https:", "//")):
            return m.group(0)
        target = _join(chapter, src)
        ext = posixpath.splitext(target)[1].lower()
        if target.startswith(("..", "/")) or target not in names or ext not in _MEDIA:
            return m.group(0)
        try:
            data = read_member(archive, target, _MAX_IMAGE)
        except Exception:  # noqa: BLE001 - one unreadable image does not stop the chapter
            return m.group(0)
        uri = f"data:{_MEDIA[ext]};base64,{base64.b64encode(data).decode('ascii')}".encode("ascii")
        return m.group(1) + m.group(2) + uri + m.group(2)
    return _IMG.sub(sub, raw)
