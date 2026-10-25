"""Fixed-layout documents: PDF.

pdfium draws the pages and reads their text; pypdf reads the file's structure (identity, permissions, links). The PDF is never
executed and never modified. Annotations are stored beside it (see ``annotations.store``) in page coordinates, so they follow the
page at any zoom: positions are fractions of the *displayed* page (rotation applied), and text highlights are character ranges in
pdfium's text of that page, which are turned into rectangles only when they are drawn or exported.
"""
from __future__ import annotations

import ctypes
import io
import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..annotations import anchors
from ..errors import BadRequest, DependencyMissing, FileRejected, LimitExceeded, PasswordRequired, UnsupportedOperation, WrongPassword
from .appearance import PdfAppearance, apply_to_page
from .base import OpenDocument
from .pdfium_lock import PDFIUM_LOCK, pdfium_locked

_SCALE = 64                      # sub-point precision for pdfium's integer device coordinates
_MAX_INDEX_CHARS = 120_000_000   # the background text index stops here (search then reports a partial result)
_PAGE_TEXT_CACHE = 96
_SAFE_SCHEMES = ("http", "https", "mailto")


def _pdfium() -> Any:
    try:
        import pypdfium2
    except ImportError as exc:
        raise DependencyMissing("Reading PDF files needs the 'pypdfium2' package (pip install pypdfium2)") from exc
    return pypdfium2


@dataclass
class PageText:
    text: str
    # (start, end, x0, y0, x1, y1) in displayed points, y down. A word whose letters run in another direction than left to right on the
    # displayed page (a rotated page) carries a seventh number: 90 = top to bottom, 180 = right to left, 270 = bottom to top.
    words: list[tuple[int, ...]]
    width: float
    height: float
    u16: list[int] | None                                         # python index -> pdfium (UTF-16) index; None if identical


class PdfDocument(OpenDocument):
    layout = "fixed"
    format = "pdf"

    def __init__(self, path: Path, *, fingerprint: str, size: int, password: str | None, max_pages: int,
                 managed_copy: bool = False) -> None:
        super().__init__()
        self.path = path
        self.fingerprint = fingerprint
        self.size = size
        self.password = password
        self.managed_copy = managed_copy
        self.revision = fingerprint
        self._cache: "OrderedDict[tuple, tuple[bytes, str, tuple[int, int]]]" = OrderedDict()
        self._cache_bytes = 0
        self._cache_limit = 96 * 1024 * 1024
        self._texts: "OrderedDict[int, PageText]" = OrderedDict()
        self._index: list[str | None] = []
        self._index_chars = 0
        self._index_done = threading.Event()
        self._stop = threading.Event()
        self._indexer: threading.Thread | None = None
        self._pypdf: Any = None
        self._pypdf_failed = False
        self._page_ids: dict[int, int] | None = None
        self._doc = self._open(path, password)
        self.pages = len(self._doc)
        if self.pages == 0:
            self._doc.close()
            raise FileRejected("This PDF has no pages")
        if self.pages > max_pages:
            self._doc.close()
            raise LimitExceeded(f"This PDF has {self.pages} pages; the limit is {max_pages}", details={"limit_pages": max_pages})
        self.sizes: list[tuple[float, float]] = self._read_sizes()
        meta = self._metadata()
        self.title = meta.get("Title") or path.stem
        self.meta = meta
        self.pdf_id = self._pdf_id()
        self.doc_id = ("pdf-id:" + self.pdf_id) if self.pdf_id else ("sha256:" + fingerprint)
        self.restrictions = self._restrictions()
        self._scan = self._scan_sample()
        self._index = [None] * self.pages
        self._start_indexer()

    # ------------------------------------------------------------------ opening
    @staticmethod
    @pdfium_locked
    def _open(path: Path, password: str | None) -> Any:
        pdfium = _pdfium()
        try:
            return pdfium.PdfDocument(str(path), password=password)
        except pdfium.PdfiumError as exc:
            text = str(exc).lower()
            if "password" in text:
                if password:
                    raise WrongPassword("That password does not open this PDF") from exc
                raise PasswordRequired("This PDF is password-protected") from exc
            raise FileRejected(f"This PDF could not be opened: it looks damaged or is not a PDF ({str(exc)[:160]})") from exc
        except OSError as exc:
            raise FileRejected(f"This PDF could not be read: {exc.strerror or exc}") from exc

    @pdfium_locked
    def _read_sizes(self) -> list[tuple[float, float]]:
        out = []
        for i in range(self.pages):
            w, h = self._doc.get_page_size(i)
            out.append((round(float(w), 2), round(float(h), 2)))
        return out

    @pdfium_locked
    def _metadata(self) -> dict[str, str]:
        try:
            return {k: v for k, v in self._doc.get_metadata_dict(skip_empty=True).items() if isinstance(v, str)}
        except Exception:  # noqa: BLE001
            return {}

    def _reader(self) -> Any:
        """pypdf reader for structure that pdfium does not expose (identity, permissions, links). Opened once, lazily."""
        if self._pypdf is None and not self._pypdf_failed:
            try:
                import pypdf
                reader = pypdf.PdfReader(str(self.path), strict=False)
                if reader.is_encrypted:
                    reader.decrypt(self.password or "")
                self._pypdf = reader
            except Exception:  # noqa: BLE001
                self._pypdf_failed = True
        return self._pypdf

    def _pdf_id(self) -> str | None:
        reader = self._reader()
        if reader is None:
            return None
        try:
            ident = reader.trailer.get("/ID")
            if ident:
                first = ident[0]
                raw = first.original_bytes if hasattr(first, "original_bytes") else bytes(first)
                return raw.hex() if raw else None
        except Exception:  # noqa: BLE001
            return None
        return None

    def _restrictions(self) -> dict[str, Any]:
        """What the document's own permission flags allow. Reader honours them: it does not remove restrictions."""
        out = {"copy": True, "annotate": True, "print": True, "encrypted": False}
        reader = self._reader()
        if reader is None:
            return out
        try:
            if reader.is_encrypted:
                out["encrypted"] = True
                perms = reader.user_access_permissions
                if perms is not None:
                    from pypdf.constants import UserAccessPermissions as P
                    out["copy"] = bool(perms & P.EXTRACT)
                    out["annotate"] = bool(perms & P.ADD_OR_MODIFY)
                    out["print"] = bool(perms & P.PRINT)
        except Exception:  # noqa: BLE001
            pass
        return out

    @pdfium_locked
    def _scan_sample(self) -> dict[str, Any]:
        """Do the pages carry text? A page with an image and no text is a scan: search and highlighting need OCR."""
        n = self.pages
        picks = sorted({int(i * (n - 1) / max(1, min(n, 12) - 1)) for i in range(min(n, 12))}) if n > 1 else [0]
        textless = scanned = 0
        for i in picks:
            page = self._doc[i]
            try:
                tp = page.get_textpage()
                try:
                    chars = tp.count_chars()
                finally:
                    tp.close()
                if chars < 5:
                    textless += 1
                    if any(o.type == 3 for o in page.get_objects(max_depth=3)):
                        scanned += 1
            finally:
                page.close()
        return {"sampled": len(picks), "withoutText": textless, "scanned": scanned,
                "ocrNeeded": len(picks) > 0 and scanned * 2 >= len(picks)}

    # ------------------------------------------------------------------ interface
    def info(self) -> dict[str, Any]:
        sizes = self.sizes
        return {
            "title": self.title, "format": "pdf", "layout": "fixed", "pages": self.pages,
            "pageSizes": [list(s) for s in sizes], "uniformSize": len(set(sizes)) == 1,
            "metadata": {k: self.meta[k] for k in ("Author", "Subject", "Creator", "Producer", "CreationDate", "ModDate") if k in self.meta},
            "fingerprint": self.fingerprint, "revision": self.revision, "docId": self.doc_id,
            "restrictions": self.restrictions, "scan": self._scan, "hasCover": True,
            "warnings": self.warnings, "converted": False, "backend": "pypdfium2",
            "language": self.meta.get("Language", ""), "direction": "ltr",
        }

    def capabilities(self) -> dict[str, Any]:
        cap = self.base_capabilities()
        no_text = self._scan["ocrNeeded"]
        copy_ok = self.restrictions["copy"]
        cap.update({
            "reflow": False, "fontSize": False, "fontFamily": False, "lineSpacing": False, "columnWidth": False, "textAlign": False,
            # Vector text can be recoloured while drawing; text inside a scanned image cannot.
            "fontColor": True, "fontOpacity": True,
            "fontColorNote": "Recolours the text objects of the page while it is drawn. Text that is part of a scanned image is not affected.",
            "fontSizeNote": "PDF pages have a fixed layout: zoom enlarges the page, it does not change the document's font size.",
            "layouts": ["continuous", "single", "spread", "focus"],
            "textSelection": not no_text, "search": not no_text, "highlight": not no_text, "underline": not no_text, "strike": not no_text,
            "ocrNeeded": no_text,
            "copyAllowed": copy_ok, "annotateAllowed": self.restrictions["annotate"],
            "nativeAnnotations": True,
            "nativeAnnotationNote": "Annotations are kept in Reader's store and can be exported as standard PDF annotations in a new file; the original is never changed.",
        })
        return cap

    # ------------------------------------------------------------------ rendering
    def render(self, index: int, *, scale: float, fmt: str, appearance: PdfAppearance, region: tuple[float, float, float, float] | None = None,
               max_pixels: int = 60_000_000) -> tuple[bytes, str, tuple[int, int]]:
        if not 0 <= index < self.pages:
            raise BadRequest(f"Page {index + 1} does not exist (the document has {self.pages} pages)")
        scale = min(max(scale, 0.03), 8.0)
        w, h = self.sizes[index]
        key = (index, round(scale * 1000), fmt, appearance.key() if not appearance.invert else "orig", region)
        with self.lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        if region:
            rx0, ry0, rx1, ry1 = region
            rx0, rx1 = max(0.0, min(rx0, w)), max(0.0, min(rx1, w))
            ry0, ry1 = max(0.0, min(ry0, h)), max(0.0, min(ry1, h))
            if rx1 - rx0 < 1 or ry1 - ry0 < 1:
                raise BadRequest("The requested region is empty")
            area = (rx1 - rx0) * (ry1 - ry0) * scale * scale
            crop = (rx0, h - ry1, w - rx1, ry0)         # left, bottom, right, top margins to remove (points)
        else:
            area = w * h * scale * scale
            crop = (0, 0, 0, 0)
        if area > max_pixels:
            raise LimitExceeded("That render size is too large; zoom is limited to what the screen can show", details={"pixels": int(area)})
        with PDFIUM_LOCK:
            page = self._doc[index]
            try:
                if appearance.recolours:
                    apply_to_page(page, appearance)
                bg = appearance.bg if not appearance.invert else (255, 255, 255)
                bitmap = page.render(scale=scale, crop=crop, may_draw_forms=True, draw_annots=True, fill_color=(bg[0], bg[1], bg[2], 255))
                image = bitmap.to_pil().convert("RGB")
            finally:
                page.close()
        buf = io.BytesIO()
        if fmt == "jpeg":
            image.save(buf, "JPEG", quality=82, optimize=False)
            media = "image/jpeg"
        else:
            image.save(buf, "PNG", compress_level=1)
            media = "image/png"
        result = (buf.getvalue(), media, image.size)
        with self.lock:
            self._cache[key] = result
            self._cache_bytes += len(result[0])
            while self._cache_bytes > self._cache_limit and len(self._cache) > 1:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= len(old[0])
        return result

    # ------------------------------------------------------------------ text
    @pdfium_locked
    def page_text(self, index: int) -> PageText:
        if not 0 <= index < self.pages:
            raise BadRequest(f"Page {index + 1} does not exist (the document has {self.pages} pages)")
        with self.lock:
            cached = self._texts.get(index)
            if cached is not None:
                self._texts.move_to_end(index)
                return cached
        from pypdfium2 import raw
        page = self._doc[index]
        try:
            w, h = self.sizes[index]
            tp = page.get_textpage()
            try:
                text = tp.get_text_range()
                count = tp.count_chars()
                u16: list[int] | None = None
                if len(text) != count or any(ord(c) > 0xFFFF for c in text):
                    u16, pos = [], 0
                    for c in text:
                        u16.append(pos)
                        pos += 2 if ord(c) > 0xFFFF else 1
                    u16.append(pos)
                boxes = self._char_boxes(page, tp, text, u16, w, h, raw)
            finally:
                tp.close()
        finally:
            page.close()
        words: list[tuple[int, ...]] = []
        turns: list[int | None] = []
        i, n = 0, len(text)
        while i < n:
            if text[i].isspace() or ord(text[i]) < 32:
                i += 1
                continue
            j = i
            x0 = y0 = 1e9
            x1 = y1 = -1e9
            first = last = None
            while j < n and not text[j].isspace() and ord(text[j]) >= 32:
                b = boxes[j]
                if b is not None:
                    x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
                    first = first or b
                    last = b
                j += 1
            if x1 > x0 and y1 > y0:
                words.append((i, j, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))
                turns.append(self._direction(first, last))
            i = j
        if any(t is not None for t in turns):
            # A one-letter word has no direction of its own: it runs the way most words on its page do.
            seen = [t for t in turns if t is not None]
            common = max(set(seen), key=seen.count)
            words = [w + (t if t is not None else common,) if (t if t is not None else common) else w for w, t in zip(words, turns)]
        result = PageText(text=text, words=words, width=w, height=h, u16=u16)
        with self.lock:
            self._texts[index] = result
            while len(self._texts) > _PAGE_TEXT_CACHE:
                self._texts.popitem(last=False)
        return result

    @pdfium_locked
    def plain_page(self, index: int) -> str:
        """The text of one page, exactly as ``page_text(index).text`` has it, without measuring every character (about 30x cheaper).

        Anchors, search and exports only need the text. The characters' boxes are needed only to draw something on the page, so
        resolving 2,000 annotations over 400 pages must not pay for 400 pages of boxes. Pages already indexed are served from the index.
        """
        if not 0 <= index < self.pages:
            raise BadRequest(f"Page {index + 1} does not exist (the document has {self.pages} pages)")
        cached = self._index[index] if index < len(self._index) else None
        if cached is not None:
            return cached
        with self.lock:
            full = self._texts.get(index)
        if full is not None:
            return full.text
        page = self._doc[index]
        try:
            tp = page.get_textpage()
            try:
                text = tp.get_text_range()
            finally:
                tp.close()
        finally:
            page.close()
        if index < len(self._index) and self._index_chars + len(text) <= _MAX_INDEX_CHARS:
            with self.lock:
                if self._index[index] is None:
                    self._index[index] = text
                    self._index_chars += len(text)
        return text

    @staticmethod
    def _direction(first: tuple[float, float, float, float] | None, last: tuple[float, float, float, float] | None) -> int | None:
        """Which way a word's letters run on the displayed page: 0 left to right, 90 down, 180 right to left, 270 up (None: one letter)."""
        if first is None or last is None or first is last:
            return None
        dx = (last[0] + last[2]) / 2 - (first[0] + first[2]) / 2
        dy = (last[1] + last[3]) / 2 - (first[1] + first[3]) / 2
        if abs(dx) >= abs(dy):
            return 0 if dx >= 0 else 180
        return 90 if dy > 0 else 270

    @staticmethod
    def _char_boxes(page: Any, tp: Any, text: str, u16: list[int] | None, w: float, h: float, raw: Any) -> list[tuple[float, float, float, float] | None]:
        """Box of every character in displayed points (rotation applied, y down). ``None`` for characters without a box."""
        dx, dy = ctypes.c_int(), ctypes.c_int()
        out: list[tuple[float, float, float, float] | None] = []
        W, H = int(w * _SCALE), int(h * _SCALE)

        def dev(x: float, y: float) -> tuple[float, float]:
            raw.FPDF_PageToDevice(page.raw, 0, 0, W, H, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
            return dx.value / _SCALE, dy.value / _SCALE

        for i, ch in enumerate(text):
            if ch.isspace() or ord(ch) < 32:
                out.append(None)
                continue
            idx = u16[i] if u16 is not None else i
            try:
                left, bottom, right, top = tp.get_charbox(idx)
            except Exception:  # noqa: BLE001
                out.append(None)
                continue
            if right <= left or top <= bottom:
                out.append(None)
                continue
            ax, ay = dev(left, top)
            bx, by = dev(right, bottom)
            out.append((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))
        return out

    def text_payload(self, index: int) -> dict[str, Any]:
        pt = self.page_text(index)
        return {"page": index, "width": pt.width, "height": pt.height, "text": pt.text,
                "words": [list(w) for w in pt.words]}

    def rects_for_range(self, index: int, start: int, end: int) -> list[list[float]]:
        """Highlight rectangles (displayed points, y down) for ``text[start:end]`` of a page: one rectangle per visual line."""
        pt = self.page_text(index)
        start, end = max(0, start), min(len(pt.text), end)
        if end <= start:
            return []
        with PDFIUM_LOCK:
            from pypdfium2 import raw
            page = self._doc[index]
            try:
                tp = page.get_textpage()
                try:
                    boxes = self._char_boxes(page, tp, pt.text, pt.u16, pt.width, pt.height, raw)
                finally:
                    tp.close()
            finally:
                page.close()
        lines: list[list[float]] = []
        for i in range(start, end):
            b = boxes[i]
            if b is None:
                continue
            if lines:
                cur = lines[-1]
                cy, ch = (cur[1] + cur[3]) / 2, cur[3] - cur[1]
                by, bh = (b[1] + b[3]) / 2, b[3] - b[1]
                same_line = abs(cy - by) < 0.55 * max(ch, bh) and b[0] - cur[2] < 3.0 * max(ch, bh) and b[0] >= cur[0] - 0.5 * max(ch, bh)
                if same_line:
                    cur[0], cur[1], cur[2], cur[3] = min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])
                    continue
            lines.append(list(b))
        return [[round(v, 2) for v in r] for r in lines]

    @pdfium_locked
    def user_space_quads(self, index: int, start: int, end: int) -> list[list[float]]:
        """The same rectangles in PDF user space (origin bottom-left, rotation not applied) - what standard annotations need."""
        pt = self.page_text(index)
        start, end = max(0, start), min(len(pt.text), end)
        page = self._doc[index]
        out: list[list[float]] = []
        try:
            tp = page.get_textpage()
            try:
                cur: list[float] | None = None
                for i in range(start, end):
                    ch = pt.text[i]
                    if ch.isspace() or ord(ch) < 32:
                        continue
                    idx = pt.u16[i] if pt.u16 is not None else i
                    l, b, r, t = tp.get_charbox(idx)
                    if r <= l or t <= b:
                        continue
                    if cur is not None and abs(((cur[1] + cur[3]) / 2) - ((b + t) / 2)) < 0.55 * max(cur[3] - cur[1], t - b) \
                            and l - cur[2] < 3.0 * max(cur[3] - cur[1], t - b):
                        cur = [min(cur[0], l), min(cur[1], b), max(cur[2], r), max(cur[3], t)]
                        out[-1] = cur
                    else:
                        cur = [l, b, r, t]
                        out.append(cur)
            finally:
                tp.close()
        finally:
            page.close()
        return out

    @pdfium_locked
    def display_to_user(self, index: int, nx: float, ny: float) -> tuple[float, float]:
        """A point given as fractions of the displayed page (x right, y down) -> PDF user space."""
        from pypdfium2 import raw
        w, h = self.sizes[index]
        page = self._doc[index]
        try:
            px, py = ctypes.c_double(), ctypes.c_double()
            raw.FPDF_DeviceToPage(page.raw, 0, 0, int(w * _SCALE), int(h * _SCALE), 0, int(nx * w * _SCALE), int(ny * h * _SCALE),
                                  ctypes.byref(px), ctypes.byref(py))
            return px.value, py.value
        finally:
            page.close()

    # ------------------------------------------------------------------ search
    def _start_indexer(self) -> None:
        if self.pages <= 40:
            self._index_all()
            self._index_done.set()
            return
        self._indexer = threading.Thread(target=self._index_loop, daemon=True, name="reader-pdf-index")
        self._indexer.start()

    def _index_all(self) -> None:
        for i in range(self.pages):
            self._index_page(i)

    def _index_page(self, i: int) -> None:
        if self._index[i] is not None or self._stop.is_set():
            return
        try:
            self.plain_page(i)                    # stores the text in the index itself (up to the size limit)
        except Exception:  # noqa: BLE001 - one unreadable page does not stop the index
            with self.lock:
                if self._index[i] is None:
                    self._index[i] = ""

    def _index_loop(self) -> None:
        for i in range(self.pages):
            if self._stop.is_set():
                return
            self._index_page(i)
        self._index_done.set()

    def index_state(self) -> dict[str, Any]:
        done = sum(1 for t in self._index if t is not None)
        return {"indexed": done, "total": self.pages, "complete": done == self.pages}

    def search(self, query: str, *, limit: int = 200, case_sensitive: bool = False) -> dict[str, Any]:
        q = unicodedata.normalize("NFC", query or "")
        state = self.index_state()
        if not q.strip():
            return {"query": q, "hits": [], "total": 0, "truncated": False, "index": state}
        if self._scan["ocrNeeded"] and state["complete"] and self._index_chars < 5:
            return {"query": q, "hits": [], "total": 0, "truncated": False, "index": state, "ocrNeeded": True}
        pattern = re.compile(re.escape(q), (0 if case_sensitive else re.IGNORECASE) | re.UNICODE)
        hits: list[dict[str, Any]] = []
        truncated = False
        for i, text in enumerate(self._index):
            if text is None:
                continue
            for m in pattern.finditer(text):
                if len(hits) >= limit:
                    truncated = True
                    break
                a, z = m.start(), m.end()
                hits.append({"page": i, "start": a, "end": z, "before": " ".join(text[max(0, a - 40):a].split()), "match": text[a:z],
                             "after": " ".join(text[z:z + 60].split()), "percent": i / self.pages})
            if truncated:
                break
        return {"query": q, "hits": hits, "total": len(hits), "truncated": truncated, "index": state}

    # ------------------------------------------------------------------ structure
    @pdfium_locked
    def outline(self) -> list[dict[str, Any]]:
        out = []
        try:
            for item in self._doc.get_toc(max_depth=12):
                dest = item.get_dest()
                page = dest.get_index() if dest is not None else None
                if page is None or not 0 <= page < self.pages:
                    continue
                out.append({"title": (item.get_title() or "").strip()[:200] or "(untitled)", "level": item.level + 1,
                            "location": {"type": "page", "page": page, "y": 0.0, "percent": page / self.pages}})
                if len(out) >= 5000:
                    break
        except Exception:  # noqa: BLE001 - a damaged outline is not a reason to refuse the document
            return out
        return out

    def links(self, index: int) -> list[dict[str, Any]]:
        """Clickable regions of a page: internal jumps and web/mail links. Other actions (launch, JavaScript, files) are dropped."""
        reader = self._reader()
        if reader is None or not 0 <= index < self.pages:
            return []
        out: list[dict[str, Any]] = []
        try:
            page = reader.pages[index]
            annots = page.get("/Annots") or []
            for ref in annots[:400]:
                a = ref.get_object()
                if a.get("/Subtype") != "/Link" or "/Rect" not in a:
                    continue
                rect = [float(v) for v in a["/Rect"]]
                target: int | None = None
                uri: str | None = None
                action = a.get("/A")
                dest = a.get("/Dest")
                if action is not None:
                    action = action.get_object()
                    kind = action.get("/S")
                    if kind == "/URI":
                        candidate = str(action.get("/URI", ""))
                        if candidate.split(":", 1)[0].lower() in _SAFE_SCHEMES:
                            uri = candidate[:2000]
                    elif kind == "/GoTo":
                        dest = action.get("/D")
                if target is None and uri is None and dest is not None:
                    target = self._dest_page(reader, dest)
                if uri is None and (target is None or not 0 <= target < self.pages):
                    continue
                out.append({"rect": self._rect_to_display(index, rect), "page": target, "uri": uri})
        except Exception:  # noqa: BLE001
            return out
        return out

    def _dest_page(self, reader: Any, dest: Any) -> int | None:
        """The page a link destination points at (a destination array or a named destination), or ``None``."""
        try:
            obj = dest.get_object() if hasattr(dest, "get_object") else dest
            if isinstance(obj, (str, bytes)):
                name = obj.decode("latin-1") if isinstance(obj, bytes) else str(obj)
                named = reader.named_destinations.get(name)
                return reader.get_destination_page_number(named) if named is not None else None
            if isinstance(obj, list) and obj:
                ref = obj[0]
                if isinstance(ref, int):
                    return ref
                idnum = getattr(ref, "idnum", None)
                if idnum is None:
                    return None
                if self._page_ids is None:
                    self._page_ids = {}
                    for i, pg in enumerate(reader.pages):
                        ind = getattr(pg, "indirect_reference", None)
                        if ind is not None:
                            self._page_ids[ind.idnum] = i
                return self._page_ids.get(idnum)
        except Exception:  # noqa: BLE001
            return None
        return None

    @pdfium_locked
    def _rect_to_display(self, index: int, rect: list[float]) -> list[float]:
        from pypdfium2 import raw
        w, h = self.sizes[index]
        page = self._doc[index]
        try:
            dx, dy = ctypes.c_int(), ctypes.c_int()
            pts = []
            for x, y in ((rect[0], rect[1]), (rect[2], rect[3])):
                raw.FPDF_PageToDevice(page.raw, 0, 0, int(w * _SCALE), int(h * _SCALE), 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
                pts.append((dx.value / _SCALE, dy.value / _SCALE))
        finally:
            page.close()
        (ax, ay), (bx, by) = pts
        return [round(min(ax, bx), 2), round(min(ay, by), 2), round(max(ax, bx), 2), round(max(ay, by), 2)]

    # ------------------------------------------------------------------ positions and anchors
    def percent_at(self, page: int, y: float = 0.0) -> float:
        return min(1.0, max(0.0, (page + max(0.0, min(1.0, y))) / self.pages))

    def normalise_location(self, loc: dict[str, Any]) -> dict[str, Any]:
        page = loc.get("page")
        if isinstance(page, int) and 0 <= page < self.pages:
            y = loc.get("y", 0.0)
            y = float(y) if isinstance(y, (int, float)) and 0 <= y <= 1 else 0.0
            return {"type": "page", "page": page, "y": y, "percent": self.percent_at(page, y)}
        pct = loc.get("percent")
        pct = float(pct) if isinstance(pct, (int, float)) else 0.0
        p = min(self.pages - 1, max(0, int(pct * self.pages)))
        return {"type": "page", "page": p, "y": 0.0, "percent": self.percent_at(p, 0.0)}

    def make_text_anchor(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        # (the permission flags govern copying and export; marking text does not extract it, so it stays available)
        if self._scan["ocrNeeded"]:
            raise UnsupportedOperation("This PDF is a scan without a text layer; text cannot be selected or highlighted without OCR")
        if not isinstance(segments, list) or not segments or len(segments) > 400:
            raise BadRequest("A text selection needs between 1 and 400 segments")
        out = []
        for seg in segments:
            page, start, end = seg.get("page"), seg.get("start"), seg.get("end")
            if type(page) is not int or not 0 <= page < self.pages:
                raise BadRequest("The selection refers to a page that is not in this document")
            text = self.plain_page(page)
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise BadRequest("The selection is outside the page's text")
            out.append({"page": page, "start": start, "end": end, **anchors.selectors(text, start, end)})
        return {"kind": "pdf-text", "segments": out}

    def make_page_anchor(self, page: Any, x: float | None = None, y: float | None = None) -> dict[str, Any]:
        if type(page) is not int or not 0 <= page < self.pages:
            raise BadRequest("That page is not in this document")
        anchor: dict[str, Any] = {"kind": "page", "page": page}
        if x is not None and y is not None:
            anchor["x"], anchor["y"] = max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))
        return anchor

    def resolve_anchor(self, anchor: dict[str, Any]) -> dict[str, Any]:
        kind = anchor.get("kind")
        if kind == "pdf-text":
            resolved = [anchors.resolve_pdf_segment(seg, self.plain_page, self.pages) for seg in anchor.get("segments", [])]
            return {"status": anchors.combine_status([r["status"] for r in resolved]), "segments": resolved}
        if kind == "page":
            page = anchor.get("page")
            return {"status": "ok" if isinstance(page, int) and 0 <= page < self.pages else "detached", "page": page}
        return {"status": "ok"}

    def text_of_segments(self, resolved: list[dict[str, Any]]) -> str:
        parts = []
        for r in resolved:
            if isinstance(r.get("page"), int) and 0 <= r["page"] < self.pages and isinstance(r.get("start"), int) and isinstance(r.get("end"), int):
                parts.append(self.plain_page(r["page"])[r["start"]:r["end"]].replace("\r\n", " ").replace("\n", " "))
        return " … ".join(parts)

    def plain_text(self, limit: int | None = None) -> str:
        parts, size = [], 0
        for i in range(self.pages):
            t = self.plain_page(i).replace("\r\n", "\n")
            parts.append(t)
            size += len(t)
            if limit is not None and size >= limit:
                break
        text = "\n\n".join(parts)
        return text if limit is None else text[:limit]

    def page_plain(self, index: int) -> str:
        return self.plain_page(index).replace("\r\n", "\n")

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self._stop.set()
        thread = self._indexer
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        with PDFIUM_LOCK:
            try:
                self._doc.close()
            except Exception:  # noqa: BLE001
                pass
        with self.lock:
            self._cache.clear()
            self._texts.clear()
            self._index = []
            self._pypdf = None
