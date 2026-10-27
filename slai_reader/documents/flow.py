"""Flow (reflowable) documents: native SLDC, and every other non-PDF format after it has been read into SLDC in memory.

Nothing here writes. The package is loaded once, never modified, and its blocks are served to the client in ranges so that a
20,000-block document does not have to be sent (or drawn) all at once.

Positions inside the document are ``(block id, character offset)``. They do not depend on pages, fonts or the window, which is
what lets a highlight survive a change of font size or window width: the client reflows, the anchor stays where it is.
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from ..annotations import anchors
from ..bridge import sldc
from ..errors import BadRequest, NotFound
from .base import OpenDocument

TEXT_BLOCKS = frozenset({"heading", "paragraph", "list_item", "quote", "code"})
_RTL_LANGS = frozenset({"ar", "he", "fa", "ur", "yi", "ps", "sd", "ug", "dv"})


def text_direction(language: str, sample: str) -> str:
    """"rtl" or "ltr" for the document as a whole (individual paragraphs still use dir=auto in the client)."""
    base = (language or "").split("-")[0].lower()
    rtl = ltr = 0
    for ch in sample[:2000]:
        d = unicodedata.bidirectional(ch)
        if d in ("R", "AL"):
            rtl += 1
        elif d == "L":
            ltr += 1
    if rtl + ltr == 0:
        return "rtl" if base in _RTL_LANGS else "ltr"
    return "rtl" if rtl > ltr else "ltr"


class FlowDocument(OpenDocument):
    layout = "flow"

    def __init__(self, package: Any, *, fmt: str, path: Path | None, fingerprint: str, size: int,
                 backend: str, warnings: Iterable[str] = (), dropped: Iterable[str] = (), features: Iterable[str] = (),
                 converted: bool = False) -> None:
        super().__init__()
        self.package = package
        self.format = fmt
        self.path = path
        self.fingerprint = fingerprint
        self.size = size
        self.backend = backend
        self.warnings = list(warnings)
        self.dropped = list(dropped)
        self.features = list(features)
        self.converted = converted
        s = sldc()
        doc = package.document
        self.doc = doc
        self.title = str(doc.get("title") or (path.stem if path else "Untitled"))
        if fmt == "sldc":
            self.doc_id = "sldc:" + str(doc["id"])
            try:
                self.revision = s.revision_id(package) if package.version != s.LEGACY_VERSION else s.document_digest(package)
            except Exception:  # noqa: BLE001
                self.revision = None
        else:
            self.doc_id = "sha256:" + fingerprint
            self.revision = fingerprint
        self.blocks: list[dict[str, Any]] = doc["blocks"]
        self.index: dict[str, int] = {b["id"]: i for i, b in enumerate(self.blocks)}
        self._texts: list[str] = [s.block_text(b) for b in self.blocks]
        self._item_no = self._number_list_items()
        # Cumulative characters before each block: progress is measured in text, not in rendered pages.
        self.cum: list[int] = []
        total = 0
        for t in self._texts:
            self.cum.append(total)
            total += len(t) + 1
        self.total_chars = max(total, 1)
        self._digests: dict[str, str] = {}
        self._signatures: list[dict[str, Any]] | None = None
        self.direction = text_direction(str(doc.get("language", "")), " ".join(self._texts[:40]))

    # ------------------------------------------------------------------ identity / info
    def block(self, block_id: str) -> dict[str, Any]:
        i = self.index.get(block_id)
        if i is None:
            raise NotFound(f"There is no block {block_id!r} in this document")
        return self.blocks[i]

    def block_text(self, block_id: str) -> str | None:
        i = self.index.get(block_id)
        return None if i is None else self._texts[i]

    def digest(self, block_id: str) -> str:
        if block_id not in self._digests:
            self._digests[block_id] = sldc().block_digest(self.block(block_id))
        return self._digests[block_id]

    def logical_pages(self) -> list[int]:
        """Block indexes at which a logical page begins: the start, ``page_break_before`` blocks and section changes. These are
        the pages a PDF import or a DOCX page break defined; SLDC itself has no fixed pages."""
        starts = [0] if self.blocks else []
        last_section = self.blocks[0].get("section") if self.blocks else None
        for i, b in enumerate(self.blocks):
            if i == 0:
                continue
            if (b.get("attributes") or {}).get("page_break_before") or b.get("section") != last_section:
                starts.append(i)
            last_section = b.get("section")
        return starts

    def paginate(self, budget: int) -> list[int]:
        """Block index at which each reading page starts, for the paged layouts (single page, two-page spread).

        SLDC has no fixed pages, so a page here is a run of whole blocks of about ``budget`` characters, started afresh at every page or
        section break the document itself defines. A heading is never left alone at the bottom of a page. The result depends on the
        font size and window the client asked for; it is a reading convenience, not a property of the document.
        """
        budget = max(400, min(int(budget), 60_000))
        forced = set(self.logical_pages())
        starts: list[int] = []
        used = 0
        for i, text in enumerate(self._texts):
            cost = max(len(text), 1) + (120 if self.blocks[i]["type"] == "heading" else 0)
            if i == 0 or i in forced or (used > 0 and (used + cost > budget or i - starts[-1] >= 150)):
                cut = i
                if i not in forced and i - 1 > starts[-1] and self.blocks[i - 1]["type"] == "heading":
                    cut = i - 1                                   # keep a heading with the text that follows it
                starts.append(cut)
                used = sum(max(len(self._texts[j]), 1) for j in range(cut, i))
            used += cost
        return starts

    def signatures(self) -> list[dict[str, Any]]:
        """Signature states (valid / modified / invalid ...) reported by SLDC. Reader never signs and never changes a signed file."""
        if self._signatures is None:
            self._signatures = []
            if self.package.signatures and self.package.version != sldc().LEGACY_VERSION:
                try:
                    self._signatures = sldc().verify_package(self.package)
                except Exception as exc:  # noqa: BLE001 - e.g. the 'cryptography' package is missing
                    self._signatures = [{"id": "?", "state": "indeterminate", "problems": [f"{type(exc).__name__}: {str(exc)[:160]}"]}]
        return self._signatures

    def info(self) -> dict[str, Any]:
        doc = self.doc
        pub = dict(doc.get("publication") or {})
        has_cover = bool(pub.get("cover_asset"))
        pub.pop("cover_asset", None)
        comment_state: dict[str, str] = {}
        if self.package.comments and self.package.version != sldc().LEGACY_VERSION:
            try:
                comment_state = sldc().comment_anchor_status(self.package)
            except Exception:  # noqa: BLE001
                comment_state = {}
        threads = []
        for t in self.package.comments:
            threads.append({"id": t["id"], "anchor": t["anchor"], "status": t["status"], "author": t["author"],
                            "created": t["created"], "messages": t["messages"], "anchorState": comment_state.get(t["id"], "ok")})
        refs = {r["id"]: r for r in (doc.get("references") or [])}
        return {
            "title": self.title, "language": doc.get("language", "en"), "direction": self.direction,
            "format": self.format, "layout": "flow", "blockCount": len(self.blocks), "totalChars": self.total_chars,
            "publication": pub, "hasCover": has_cover, "styles": doc.get("styles") or {}, "sections": doc.get("sections") or [],
            "references": refs, "comments": threads, "signatures": self.signatures(),
            "revision": self.revision, "fingerprint": self.fingerprint, "docId": self.doc_id,
            "logicalPages": self.logical_pages(), "metadata": doc.get("metadata") or {},
            "converted": self.converted, "backend": self.backend,
            "warnings": self.warnings, "dropped": self.dropped, "features": self.features,
            "sldcVersion": self.package.version if self.format == "sldc" else None,
            "provenance": [p for p in (doc.get("provenance") or [])][-5:],
        }

    def capabilities(self) -> dict[str, Any]:
        cap = self.base_capabilities()
        cap.update({
            "reflow": True, "fontSize": True, "fontFamily": True, "fontColor": True, "fontOpacity": True, "lineSpacing": True,
            "columnWidth": True, "textAlign": True, "layouts": ["continuous", "single", "spread", "focus"],
            "converted": self.converted,
            "nativeAnnotations": False,
            "nativeAnnotationNote": ("SLDC review comments are shown read-only. Personal reading annotations are kept in Reader's own store: "
                                     "SLDC's format notes say private notes should not be written into a shared document, and writing "
                                     "into a signed file would invalidate its signatures."),
            "textAnchorBlocks": sorted(TEXT_BLOCKS),
            "ocrNeeded": False,
        })
        return cap

    # ------------------------------------------------------------------ content
    def _number_list_items(self) -> dict[int, int]:
        """Position of each ordered list item within its list (by depth), so a viewer that shows the document in pieces numbers it correctly."""
        numbers: dict[int, int] = {}
        counters: dict[int, int] = {}
        for i, b in enumerate(self.blocks):
            if b["type"] != "list_item":
                counters.clear()
                continue
            attrs = b.get("attributes") or {}
            depth = int(attrs.get("depth", 0) or 0)
            for d in [d for d in counters if d > depth]:
                del counters[d]
            if attrs.get("ordered"):
                counters[depth] = counters.get(depth, 0) + 1
                numbers[i] = counters[depth]
            else:
                counters.pop(depth, None)
        return numbers

    def block_view(self, i: int) -> dict[str, Any]:
        b = self.blocks[i]
        v: dict[str, Any] = {"id": b["id"], "type": b["type"], "text": b["text"], "n": i, "cum": self.cum[i]}
        if i in self._item_no:
            v["number"] = self._item_no[i]
        for key in ("spans", "attributes", "style", "section"):
            if key in b:
                v[key] = b[key]
        if "table" in b:
            v["table"] = b["table"]
        if "equation" in b:
            v["equation"] = b["equation"]
        if "figure" in b:
            v["figure"] = {**b["figure"], "url": f"asset/{b['figure']['asset']}"}
        if "embed" in b:
            embed = next((e for e in (self.doc.get("embeds") or []) if e["id"] == b["embed"]), {})
            v["embed"] = {"id": b["embed"], "kind": embed.get("kind", "unknown"),
                          "note": "Embedded content. Reader shows what the document declares and never runs it."}
        return v

    def blocks_range(self, start: int, limit: int) -> dict[str, Any]:
        start = max(0, start)
        end = min(len(self.blocks), start + max(1, limit))
        return {"start": start, "end": end, "total": len(self.blocks), "blocks": [self.block_view(i) for i in range(start, end)]}

    def asset(self, asset_id: str) -> tuple[bytes, str]:
        a = self.package.assets.get(asset_id)
        if a is None:
            raise NotFound("No such image in this document")
        return a.data, a.media_type

    def outline(self) -> list[dict[str, Any]]:
        out = []
        for i, b in enumerate(self.blocks):
            if b["type"] == "heading":
                out.append({"title": " ".join(b["text"].split())[:200] or "(untitled heading)",
                            "level": int((b.get("attributes") or {}).get("level", 1)),
                            "location": {"type": "flow", "block": b["id"], "n": i, "offset": 0, "percent": self.cum[i] / self.total_chars}})
        return out

    # ------------------------------------------------------------------ position <-> percent
    def percent_at(self, block_id: str, offset: int = 0) -> float:
        i = self.index.get(block_id)
        if i is None:
            return 0.0
        return min(1.0, (self.cum[i] + max(0, min(offset, len(self._texts[i])))) / self.total_chars)

    def location_at_percent(self, percent: float) -> dict[str, Any]:
        if not self.blocks:
            return {"type": "flow", "block": None, "n": 0, "offset": 0, "percent": 0.0}
        target = max(0.0, min(1.0, percent)) * self.total_chars
        i = max(0, bisect.bisect_right(self.cum, target) - 1)
        offset = int(min(max(0, target - self.cum[i]), len(self._texts[i])))
        return {"type": "flow", "block": self.blocks[i]["id"], "n": i, "offset": offset, "percent": self.percent_at(self.blocks[i]["id"], offset)}

    def normalise_location(self, loc: dict[str, Any]) -> dict[str, Any]:
        """A saved location, checked against this document: unknown blocks fall back to the saved percentage."""
        block = loc.get("block")
        if isinstance(block, str) and block in self.index:
            offset = loc.get("offset", 0)
            offset = offset if isinstance(offset, int) and offset >= 0 else 0
            return {"type": "flow", "block": block, "n": self.index[block], "offset": offset, "percent": self.percent_at(block, offset)}
        pct = loc.get("percent")
        return self.location_at_percent(float(pct) if isinstance(pct, (int, float)) else 0.0)

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, limit: int = 200, case_sensitive: bool = False) -> dict[str, Any]:
        q = unicodedata.normalize("NFC", query or "")
        if not q.strip():
            return {"query": q, "hits": [], "total": 0, "truncated": False}
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(q), flags | re.UNICODE)
        hits: list[dict[str, Any]] = []
        truncated = False
        for i, text in enumerate(self._texts):
            if not text:
                continue
            for m in pattern.finditer(text):
                if len(hits) >= limit:
                    truncated = True
                    break
                b = self.blocks[i]
                a, z = m.start(), m.end()
                inline = b["type"] in TEXT_BLOCKS
                hits.append({"block": b["id"], "n": i, "start": a if inline else None, "end": z if inline else None, "inline": inline,
                             "before": " ".join(text[max(0, a - 40):a].split()), "match": text[a:z],
                             "after": " ".join(text[z:z + 60].split()), "percent": self.percent_at(b["id"], a)})
            if truncated:
                break
        return {"query": q, "hits": hits, "total": len(hits), "truncated": truncated}

    # ------------------------------------------------------------------ anchors
    def make_text_anchor(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(segments, list) or not segments or len(segments) > 400:
            raise BadRequest("A text selection needs between 1 and 400 segments")
        out = []
        for seg in segments:
            block_id, start, end = seg.get("block"), seg.get("start"), seg.get("end")
            if not isinstance(block_id, str) or block_id not in self.index:
                raise BadRequest("The selection refers to a block that is not in this document")
            b = self.blocks[self.index[block_id]]
            if b["type"] not in TEXT_BLOCKS:
                raise BadRequest(f"Text cannot be selected inside a {b['type']} block; add a note to the block instead")
            text = b["text"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise BadRequest("The selection is outside the block's text")
            out.append({"block": block_id, "start": start, "end": end, **anchors.selectors(text, start, end),
                        "block_sha256": self.digest(block_id)})
        return {"kind": "text", "segments": out}

    def make_block_anchor(self, block_id: Any, x: float | None = None, y: float | None = None, offset: int | None = None) -> dict[str, Any]:
        if not isinstance(block_id, str) or block_id not in self.index:
            raise BadRequest("That block is not in this document")
        anchor: dict[str, Any] = {"kind": "block", "block": block_id, "block_sha256": self.digest(block_id)}
        if x is not None and y is not None:
            anchor["x"], anchor["y"] = float(x), float(y)
        if offset is not None:
            anchor["offset"] = int(offset)
        return anchor

    def resolve_anchor(self, anchor: dict[str, Any]) -> dict[str, Any]:
        """Check an anchor against the document as it is now. Never changes the stored anchor."""
        kind = anchor.get("kind")
        if kind == "text":
            resolved = [anchors.resolve_flow_segment(seg, self.block_text, self._all_blocks) for seg in anchor.get("segments", [])]
            return {"status": anchors.combine_status([r["status"] for r in resolved]), "segments": resolved}
        if kind == "block":
            bid = anchor.get("block")
            if bid not in self.index:
                return {"status": "detached", "block": bid}
            sha = anchor.get("block_sha256")
            status = "ok" if (not sha or sha == self.digest(bid)) else "stale"
            return {"status": status, "block": bid}
        return {"status": "ok"}

    def _all_blocks(self) -> Iterable[tuple[str, str]]:
        for b, t in zip(self.blocks, self._texts):
            if b["type"] in TEXT_BLOCKS:
                yield b["id"], t

    def text_of_segments(self, resolved: Iterable[dict[str, Any]]) -> str:
        parts = []
        for r in resolved:
            t = self.block_text(r.get("block") or "")
            if t is not None and isinstance(r.get("start"), int) and isinstance(r.get("end"), int):
                parts.append(t[r["start"]:r["end"]])
        return " … ".join(parts)

    def plain_text(self, limit: int | None = None) -> str:
        text = "\n\n".join(self._texts)
        return text if limit is None else text[:limit]

    def section_text(self, block_id: str, max_chars: int = 12000) -> tuple[str, list[str]]:
        """The text of the section a block belongs to (from its nearest heading to the next heading of the same or higher
        level), with the ids of the blocks it covers - what 'summarise this chapter' works on."""
        i = self.index.get(block_id)
        if i is None:
            raise NotFound("That block is not in this document")
        start = i
        level = 7
        while start >= 0:
            b = self.blocks[start]
            if b["type"] == "heading":
                level = int((b.get("attributes") or {}).get("level", 1))
                break
            start -= 1
        start = max(start, 0)
        end = start + 1
        while end < len(self.blocks):
            b = self.blocks[end]
            if b["type"] == "heading" and int((b.get("attributes") or {}).get("level", 1)) <= level:
                break
            end += 1
        ids, parts, size = [], [], 0
        for j in range(start, end):
            t = self._texts[j]
            if size + len(t) > max_chars:
                break
            ids.append(self.blocks[j]["id"])
            parts.append(t)
            size += len(t) + 2
        return "\n\n".join(parts), ids
