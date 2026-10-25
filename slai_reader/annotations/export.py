"""Taking annotations out of Reader: notes as Markdown / JSON / SLDC, an SLDC copy that carries review comments, and PDF copies that
carry the marks (as standard PDF annotations, or flattened into the page).

Nothing here changes the file that was opened. Every result is a new file in the output folder, created exclusively so an existing
file is never overwritten. Documents are never decrypted or stripped of restrictions to produce an export.
"""
from __future__ import annotations

import io
import json
import math
import time
from pathlib import Path
from typing import Any

from ..bridge import sldc
from ..documents.base import OpenDocument
from ..errors import BadRequest, DependencyMissing, UnsupportedOperation
from ..files import atomic_write, reserve_path
from . import model

EXPORT_SCHEMA = "slai-reader-annotations/1"
READER_AUTHOR = "SLAI Reader"

_COLOR_NAMES = {"#facc15": "yellow", "#86efac": "green", "#7dd3fc": "blue", "#f9a8d4": "pink", "#fdba74": "orange", "#ef4444": "red",
                "#e11d48": "red", "#6366f1": "indigo", "#fde047": "yellow"}


def colour_name(hex_: str | None) -> str:
    if not hex_:
        return ""
    h = hex_.lower()
    if h in _COLOR_NAMES:
        return _COLOR_NAMES[h]
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    best, dist = h, 10 ** 9
    for key, name in _COLOR_NAMES.items():
        d = (int(key[1:3], 16) - r) ** 2 + (int(key[3:5], 16) - g) ** 2 + (int(key[5:7], 16) - b) ** 2
        if d < dist:
            best, dist = name, d
    return best if dist < 3000 else h


def _clean(text: str) -> str:
    return "".join(ch for ch in text if ch >= " " or ch in "\n\t")


# --------------------------------------------------------------------------------------------- where an annotation is
def _heading_before(doc: OpenDocument, block_id: str) -> str:
    blocks = getattr(doc, "blocks", None)
    index = getattr(doc, "index", None)
    if not blocks or index is None or block_id not in index:
        return ""
    for i in range(index[block_id], -1, -1):
        if blocks[i]["type"] == "heading":
            return " ".join(blocks[i]["text"].split())[:120]
    return ""


def place(doc: OpenDocument, ann: dict[str, Any]) -> dict[str, Any]:
    """A readable place for an annotation: a page number for PDFs, the nearest heading and a percentage for flow documents."""
    anchor = ann.get("anchor", {})
    if doc.layout == "fixed":
        page = anchor.get("page")
        if page is None and anchor.get("segments"):
            page = anchor["segments"][0].get("page")
        return {"label": f"Page {int(page) + 1}" if isinstance(page, int) else "", "page": page, "sort": (page if isinstance(page, int) else 10 ** 9, 0)}
    block = anchor.get("block")
    if block is None and anchor.get("segments"):
        block = anchor["segments"][0].get("block")
    heading = _heading_before(doc, block or "")
    pct = doc.percent_at(block, (anchor.get("segments") or [{}])[0].get("start", 0)) if block else 0.0     # type: ignore[attr-defined]
    return {"label": (heading + " · " if heading else "") + f"{round(pct * 100)}%", "heading": heading, "percent": pct, "sort": (0, pct)}


def _ordered(doc: OpenDocument, anns: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs = [(a, place(doc, a)) for a in anns]
    pairs.sort(key=lambda p: (p[1]["sort"], p[0].get("created", "")))
    return pairs


def _quote(ann: dict[str, Any]) -> str:
    segs = ann.get("anchor", {}).get("segments") or []
    return " … ".join(s.get("quote", "") for s in segs if s.get("quote"))


# --------------------------------------------------------------------------------------------- notes: Markdown / JSON
def notes_markdown(doc: OpenDocument, anns: list[dict[str, Any]]) -> str:
    out = [f"# Notes: {_clean(doc.title or 'Untitled')}", "",
           f"*Exported from SLAI Reader on {time.strftime('%Y-%m-%d %H:%M')}. {len(anns)} annotation{'s' if len(anns) != 1 else ''}. "
           f"Document id `{doc.doc_id}`" + (f", revision `{doc.revision}`" if doc.revision else "") + ".*", ""]
    current = None
    for ann, where in _ordered(doc, anns):
        group = where.get("heading") if doc.layout == "flow" else where["label"]
        if group != current and group is not None:
            current = group
            out += [f"## {_clean(group) or 'Start of document'}", ""]
        kind = ann["kind"]
        tag = f"**{kind.capitalize()}**" + (f" ({colour_name(ann.get('style', {}).get('color'))})" if kind in model.TEXT_KINDS else "")
        loc = where["label"] if doc.layout == "flow" else where["label"]
        quote = _quote(ann)
        status = ann.get("status")
        out.append(f"- {tag} · {loc}" + (f" · *{status}*" if status in ("moved", "detached", "stale") else ""))
        if quote:
            out += ["", *("  > " + _clean(line) for line in quote.splitlines() or [quote]), ""]
        body = ann.get("body", {})
        if body.get("title"):
            out.append(f"  **{_clean(body['title'])}**")
        if body.get("text"):
            out += ["", *("  " + _clean(line) for line in body["text"].splitlines()), ""]
        if body.get("tags"):
            out.append("  Tags: " + ", ".join("`" + _clean(t) + "`" for t in body["tags"]))
        if kind in ("ink", "shape"):
            out.append("  *(drawing, shown in the document)*")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def notes_json(doc: OpenDocument, anns: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": EXPORT_SCHEMA, "exported": model.now(), "generator": "SLAI Reader",
            "document": {"id": doc.doc_id, "title": doc.title, "format": doc.format, "fingerprint": doc.fingerprint, "revision": doc.revision,
                         "layout": doc.layout, "pages": getattr(doc, "pages", None)},
            "annotations": [{k: v for k, v in a.items() if k not in ("resolved",)} for a in anns]}


def write_text(directory: Path, stem: str, ext: str, data: bytes) -> Path:
    target = reserve_path(directory, stem, ext)
    atomic_write(target, data)
    return target


# --------------------------------------------------------------------------------------------- notes: SLDC
def notes_sldc(doc: OpenDocument, anns: list[dict[str, Any]], directory: Path) -> Path:
    s = sldc()
    blocks = [s.make_block("heading", f"Notes: {doc.title or 'Untitled'}", attributes={"level": 1}),
              s.make_block("paragraph", f"Reading notes taken in SLAI Reader. Source document id {doc.doc_id}"
                                        + (f", revision {doc.revision}" if doc.revision else "") + f", {len(anns)} annotations.")]
    current = None
    for ann, where in _ordered(doc, anns):
        group = where.get("heading") if doc.layout == "flow" else where["label"]
        if group != current and group:
            current = group
            blocks.append(s.make_block("heading", _clean(group)[:200], attributes={"level": 2}))
        quote = _quote(ann)
        if quote:
            blocks.append(s.make_block("quote", _clean(quote)[:20000]))
        body = ann.get("body", {})
        line = " ".join(x for x in (body.get("title"), body.get("text")) if x)
        label = ann["kind"].capitalize() + (f" ({colour_name(ann.get('style', {}).get('color'))})" if ann["kind"] in model.TEXT_KINDS else "")
        text = _clean(f"{label}, {where['label']}." + (f" {line}" if line else ""))
        if body.get("tags"):
            text += " Tags: " + ", ".join(body["tags"]) + "."
        blocks.append(s.make_block("paragraph", text[:20000]))
    package = s.new_package(f"Notes: {doc.title or 'Untitled'}"[:300], blocks, provenance=[
        s.provenance_entry("export-annotations", agent=READER_AUTHOR, source=doc.doc_id)])
    target = reserve_path(directory, f"{doc.title or 'document'} - notes", ".sldc")
    s.write_package(target, package, overwrite=True)
    return target


def sldc_with_comments(doc: OpenDocument, anns: list[dict[str, Any]], directory: Path, *, author: str = READER_AUTHOR,
                       password: str | None = None) -> tuple[Path, dict[str, Any]]:
    """A copy of the document with the notes added as SLDC review comments. Content, assets and signatures are carried over unchanged."""
    s = sldc()
    package = getattr(doc, "package", None)
    if package is None or doc.layout != "flow":
        raise UnsupportedOperation("Only text documents can be saved as an SLDC copy with comments")
    if getattr(doc, "encrypted", False) and not password:
        raise BadRequest("The original is encrypted. Give a password for the copy (it is not written unencrypted).")
    new = package.copy()
    added = skipped = 0
    for ann, _ in _ordered(doc, anns):
        body = ann.get("body", {})
        text = " ".join(x for x in (body.get("title"), body.get("text")) if x)
        anchor = ann.get("anchor", {})
        if not text or ann["kind"] in ("ink", "shape", "bookmark"):
            skipped += 1
            continue
        seg = (anchor.get("segments") or [None])[0]
        block = (seg or anchor).get("block")
        if block is None or block not in doc.index:                              # type: ignore[attr-defined]
            skipped += 1
            continue
        try:
            if seg is not None and ann.get("status") in (None, "ok"):
                new, _tid = s.add_comment(new, block, text[:10000], author=author, start=seg["start"], end=seg["end"])
            else:
                new, _tid = s.add_comment(new, block, text[:10000], author=author)
            added += 1
        except s.SLDCError:
            skipped += 1
    recipients = [s.password_recipient(password)] if password else None
    target = reserve_path(directory, f"{doc.title or 'document'} - with comments", ".sldc")
    s.write_package(target, new, overwrite=True, recipients=recipients)
    return target, {"comments": added, "skipped": skipped, "signaturesKept": len(new.signatures),
                    "converted": bool(getattr(doc, "converted", False)),
                    "note": ("The original document content was not changed." if not getattr(doc, "converted", False) else
                             "This document was converted to SLDC for reading; the copy contains the converted text, not the original file.")}


# --------------------------------------------------------------------------------------------- PDF
class _Affine:
    """Displayed-page fractions (x right, y down) -> PDF user space, taken from pdfium's own transform so rotation is right."""

    def __init__(self, doc: Any, page: int) -> None:
        ox, oy = doc.display_to_user(page, 0.0, 0.0)
        ax, ay = doc.display_to_user(page, 1.0, 0.0)
        bx, by = doc.display_to_user(page, 0.0, 1.0)
        self.o, self.a, self.b = (ox, oy), (ax - ox, ay - oy), (bx - ox, by - oy)

    def __call__(self, nx: float, ny: float) -> tuple[float, float]:
        return self.o[0] + nx * self.a[0] + ny * self.b[0], self.o[1] + nx * self.a[1] + ny * self.b[1]


def _rgb(hex_: str) -> tuple[float, float, float]:
    return int(hex_[1:3], 16) / 255, int(hex_[3:5], 16) / 255, int(hex_[5:7], 16) / 255


def _page_of(ann: dict[str, Any]) -> int | None:
    a = ann.get("anchor", {})
    if isinstance(a.get("page"), int):
        return a["page"]
    segs = a.get("segments") or []
    return segs[0].get("page") if segs else None


def _check_pdf_export(doc: Any, mode: str) -> None:
    if doc.layout != "fixed":
        raise UnsupportedOperation("Only PDF documents can be exported as PDF")
    if mode not in ("native", "flatten"):
        raise BadRequest("mode must be native or flatten")
    if doc.restrictions.get("encrypted") and not doc.restrictions.get("annotate", True):
        raise UnsupportedOperation("This PDF's permissions do not allow adding annotations, so Reader will not write a modified copy. "
                                   "Export the notes as Markdown, JSON or SLDC instead.")
    if doc.path is None:
        raise UnsupportedOperation("The PDF is not on disk")


def annotated_pdf(doc: Any, anns: list[dict[str, Any]], directory: Path, *, mode: str = "native") -> tuple[Path, dict[str, Any]]:
    """A copy of the PDF with the annotations. ``native``: standard PDF annotations other readers can show and edit.
    ``flatten``: drawn into the page content (always visible, not editable, text stays selectable)."""
    _check_pdf_export(doc, mode)
    try:
        import pypdf
        from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject)
    except ImportError as exc:
        raise DependencyMissing("Exporting PDFs needs the 'pypdf' package (pip install pypdf)") from exc
    reader = pypdf.PdfReader(str(doc.path))
    encrypted = reader.is_encrypted
    if encrypted:
        reader.decrypt(doc.password or "")
    writer = pypdf.PdfWriter(clone_from=reader)
    live = [a for a in anns if a.get("visible", True) is not False and a.get("status") != "detached"]
    dropped = len(anns) - len(live)
    counts: dict[str, int] = {}
    if mode == "native":
        for ann in live:
            page = _page_of(ann)
            if page is None or not 0 <= page < len(writer.pages):
                dropped += 1
                continue
            made = _native_annotations(doc, ann, page, (DictionaryObject, ArrayObject, FloatObject, NameObject, NumberObject, TextStringObject))
            for d in made:
                page_obj = writer.pages[page]
                if "/Annots" not in page_obj:
                    page_obj[NameObject("/Annots")] = ArrayObject()
                page_obj["/Annots"].append(writer._add_object(d))
            if made:
                counts[ann["kind"]] = counts.get(ann["kind"], 0) + 1
        bookmarks = [a for a in live if a["kind"] == "bookmark" and _page_of(a) is not None]
        if bookmarks:
            parent = writer.add_outline_item("Reader bookmarks", 0)
            for b in sorted(bookmarks, key=_page_of):
                writer.add_outline_item(b.get("body", {}).get("title") or f"Page {_page_of(b) + 1}", _page_of(b), parent=parent)
                counts["bookmark"] = counts.get("bookmark", 0) + 1
    else:
        counts = _flatten(doc, live, writer, pypdf)
    if encrypted:
        writer.encrypt(user_password=doc.password or "", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    target = reserve_path(directory, f"{doc.title or 'document'} - annotated" + ("" if mode == "native" else " (flattened)"), ".pdf")
    atomic_write(target, buffer.getvalue())
    note = ("Marks are standard PDF annotations; other PDF readers show them." if mode == "native" else
            "Marks are drawn into the pages and cannot be edited afterwards.")
    if encrypted:
        note += " The copy is protected with the same open password; the original's owner password and permission flags are not carried over."
    return target, {"mode": mode, "annotations": counts, "skipped": dropped, "note": note}


def _native_annotations(doc: Any, ann: dict[str, Any], page: int, types: tuple) -> list[Any]:
    Dict, Arr, Flt, Name, Num, Txt = types
    kind, style, body = ann["kind"], ann.get("style", {}), ann.get("body", {})
    contents = " ".join(x for x in (body.get("title"), body.get("text")) if x)

    def common(subtype: str, rect: list[float]) -> Any:
        d = Dict()
        d[Name("/Type")] = Name("/Annot")
        d[Name("/Subtype")] = Name("/" + subtype)
        d[Name("/Rect")] = Arr([Flt(round(v, 3)) for v in rect])
        d[Name("/C")] = Arr([Flt(v) for v in _rgb(style.get("color", "#facc15"))])
        d[Name("/CA")] = Flt(float(style.get("opacity", 1.0)))
        d[Name("/F")] = Num(4)
        d[Name("/T")] = Txt(READER_AUTHOR)
        d[Name("/NM")] = Txt(ann["id"])
        if contents:
            d[Name("/Contents")] = Txt(contents[:5000])
        return d

    out = []
    if kind in model.TEXT_KINDS or (kind == "note" and ann["anchor"].get("kind") == "pdf-text"):
        subtype = {"highlight": "Highlight", "underline": "Underline", "strike": "StrikeOut", "note": "Highlight"}[kind]
        for seg in ann["anchor"]["segments"]:
            quads = doc.user_space_quads(seg["page"], seg["start"], seg["end"])
            if not quads:
                continue
            rect = [min(q[0] for q in quads), min(q[1] for q in quads), max(q[2] for q in quads), max(q[3] for q in quads)]
            d = common(subtype, rect)
            if kind == "note":
                d[Name("/CA")] = Flt(0.3)
            d[Name("/QuadPoints")] = Arr([Flt(round(v, 3)) for q in quads for v in (q[0], q[3], q[2], q[3], q[0], q[1], q[2], q[1])])
            out.append(d)
        return out
    aff = _Affine(doc, page)
    if kind in ("sticky", "note"):
        a = ann["anchor"]
        x, y = aff(a.get("x", 0.05), a.get("y", 0.05))
        d = common("Text", [x, y - 20, x + 20, y])
        d[Name("/Name")] = Name("/Note")
        return [d]
    if kind == "ink":
        pts = ann["geometry"]["points"]
        coords = [v for p in pts for v in aff(p[0], p[1])]
        xs, ys = coords[0::2], coords[1::2]
        pad = float(style.get("width", 3))
        d = common("Ink", [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad])
        d[Name("/InkList")] = Arr([Arr([Flt(round(v, 3)) for v in coords])])
        bs = Dict()
        bs[Name("/W")] = Flt(float(style.get("width", 3)))
        d[Name("/BS")] = bs
        return [d]
    if kind == "shape":
        g = ann["geometry"]
        (x0, y0), (x1, y1) = aff(g["x0"], g["y0"]), aff(g["x1"], g["y1"])
        rect = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        shape = g["shape"]
        d = common({"rect": "Square", "ellipse": "Circle", "line": "Line", "arrow": "Line"}[shape], rect)
        bs = Dict()
        bs[Name("/W")] = Flt(float(style.get("width", 3)))
        d[Name("/BS")] = bs
        if shape in ("line", "arrow"):
            d[Name("/L")] = Arr([Flt(round(v, 3)) for v in (x0, y0, x1, y1)])
            if shape == "arrow":
                d[Name("/LE")] = Arr([Name("/None"), Name("/OpenArrow")])
        return [d]
    return []


def _flatten(doc: Any, anns: list[dict[str, Any]], writer: Any, pypdf: Any) -> dict[str, int]:
    try:
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise DependencyMissing("Flattened PDF export needs the 'reportlab' package (pip install reportlab)") from exc
    counts: dict[str, int] = {}
    by_page: dict[int, list[dict[str, Any]]] = {}
    for ann in anns:
        p = _page_of(ann)
        if p is not None and 0 <= p < len(writer.pages) and ann["kind"] != "bookmark":
            by_page.setdefault(p, []).append(ann)
    for page_no, items in by_page.items():
        page = writer.pages[page_no]
        box = page.mediabox
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(float(box.width), float(box.height)))
        aff = _Affine(doc, page_no)
        for ann in items:
            st = ann.get("style", {})
            r, g, b = _rgb(st.get("color", "#facc15"))
            alpha = float(st.get("opacity", 1.0))
            c.saveState()
            c.setStrokeColorRGB(r, g, b)
            c.setFillColorRGB(r, g, b)
            c.setStrokeAlpha(alpha)
            c.setFillAlpha(alpha)
            c.setLineWidth(float(st.get("width", 2)))
            kind = ann["kind"]
            if kind in model.TEXT_KINDS or (kind == "note" and ann["anchor"].get("kind") == "pdf-text"):
                for seg in ann["anchor"]["segments"]:
                    for l, bt, rr, t in doc.user_space_quads(seg["page"], seg["start"], seg["end"]):
                        if kind in ("highlight", "note"):
                            c.setFillAlpha(alpha if kind == "highlight" else 0.3)
                            c.rect(l, bt, rr - l, t - bt, stroke=0, fill=1)
                        elif kind == "underline":
                            c.line(l, bt + 1, rr, bt + 1)
                        else:
                            c.line(l, (bt + t) / 2, rr, (bt + t) / 2)
            elif kind in ("sticky", "note"):
                x, y = aff(ann["anchor"].get("x", 0.05), ann["anchor"].get("y", 0.05))
                c.setFillAlpha(1.0)
                c.rect(x, y - 18, 18, 18, stroke=1, fill=1)
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(x + 5, y - 13, "i")
            elif kind == "ink":
                pts = [aff(p[0], p[1]) for p in ann["geometry"]["points"]]
                c.setLineCap(1)
                c.setLineJoin(1)
                path = c.beginPath()
                path.moveTo(*pts[0])
                if len(pts) == 1:
                    path.lineTo(pts[0][0] + 0.01, pts[0][1])
                for p in pts[1:]:
                    path.lineTo(*p)
                c.drawPath(path, stroke=1, fill=0)
            elif kind == "shape":
                gm = ann["geometry"]
                (x0, y0), (x1, y1) = aff(gm["x0"], gm["y0"]), aff(gm["x1"], gm["y1"])
                if gm["shape"] == "rect":
                    c.rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), stroke=1, fill=0)
                elif gm["shape"] == "ellipse":
                    c.ellipse(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), stroke=1, fill=0)
                else:
                    c.line(x0, y0, x1, y1)
                    if gm["shape"] == "arrow":
                        ang = math.atan2(y1 - y0, x1 - x0)
                        for turn in (0.5, -0.5):
                            c.line(x1, y1, x1 - 10 * math.cos(ang + turn), y1 - 10 * math.sin(ang + turn))
            c.restoreState()
            counts[kind] = counts.get(kind, 0) + 1
        c.showPage()
        c.save()
        buf.seek(0)
        page.merge_page(pypdf.PdfReader(buf).pages[0])
    return counts
