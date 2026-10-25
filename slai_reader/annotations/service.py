"""Annotations of one open document: create, change, erase, undo and redo, and a view that says where each one stands now.

The service is the only place that turns what the client sends into stored data. The client names a place (text segments, a
block, a page) and Reader builds the anchor from the document itself: it reads the quote and its context from the document's own
text, so a client can never store an anchor that does not match the text it claims to mark.
"""
from __future__ import annotations

import copy
from typing import Any

from ..documents.base import OpenDocument
from ..errors import BadRequest, Conflict, NotFound, UnsupportedOperation
from . import model
from .history import History
from .store import Store

PATCHABLE = ("style", "body", "visible", "geometry", "group")


class AnnotationService:
    def __init__(self, doc: OpenDocument, store: Store, author: str | None = None) -> None:
        self.doc = doc
        self.store = store
        self.author = author
        self.history = History()
        self._rect_cache: dict[tuple[int, int, int], list[list[float]]] = {}

    # ------------------------------------------------------------------ reading
    @property
    def host(self) -> str:
        return "page" if self.doc.layout == "fixed" else "block"

    def view(self, ann: dict[str, Any]) -> dict[str, Any]:
        """A stored annotation plus where it stands in the document as it is now."""
        out = dict(ann)
        resolved = self.doc.resolve_anchor(ann["anchor"])
        out["resolved"] = resolved
        out["status"] = resolved["status"]
        segs = resolved.get("segments")
        if segs:
            out["text"] = self.doc.text_of_segments(segs)
        out["pos"] = self._sort_key(ann["anchor"], resolved)
        return out

    def _sort_key(self, anchor: dict[str, Any], resolved: dict[str, Any]) -> list[int]:
        """Where in the document this annotation sits, as [page or block number, offset]: lets a list be shown in reading order."""
        try:
            segs = resolved.get("segments") or []
            if segs:
                s = segs[0]
                first = s["page"] if "page" in s else self.doc.index.get(s.get("block"), 0)      # type: ignore[attr-defined]
                return [int(first), int(s.get("start", 0))]
            if self.doc.layout == "fixed":
                return [int(resolved.get("page", anchor.get("page", 0)) or 0), 0]
            return [int(self.doc.index.get(resolved.get("block") or anchor.get("block"), 0)), int(anchor.get("offset", 0) or 0)]   # type: ignore[attr-defined]
        except (KeyError, TypeError, ValueError, AttributeError):
            return [0, 0]

    def list(self) -> dict[str, Any]:
        anns = [self.view(a) for a in self.store.list(self.doc.doc_id)]
        changed = sum(1 for a in anns if a.get("fingerprint") and a["fingerprint"] != self.doc.fingerprint)
        return {"annotations": anns, "counts": self.store.counts(self.doc.doc_id), "history": self._history_state(),
                "changedSinceAnnotated": changed}

    def _history_state(self) -> dict[str, Any]:
        peek = self.history.peek()
        return {"canUndo": self.history.can_undo(), "canRedo": self.history.can_redo(), "undoLabel": peek["undo"], "redoLabel": peek["redo"]}

    # ------------------------------------------------------------------ anchors
    def _anchor_for(self, kind: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise BadRequest("An annotation needs an anchor")
        want = raw.get("kind")
        doc = self.doc
        if kind in model.TEXT_KINDS or want in ("text", "pdf-text"):
            if kind not in model.TEXT_KINDS and kind != "note":
                raise BadRequest(f"A {kind} cannot be attached to selected text")
            return doc.make_text_anchor(raw.get("segments"))      # type: ignore[attr-defined]
        if doc.layout == "fixed":
            x, y = raw.get("x"), raw.get("y")
            if (x is None) != (y is None):
                raise BadRequest("A position needs both x and y")
            return doc.make_page_anchor(raw.get("page"), x, y)    # type: ignore[attr-defined]
        return doc.make_block_anchor(raw.get("block"), raw.get("x"), raw.get("y"), raw.get("offset"))  # type: ignore[attr-defined]

    def _default_title(self, kind: str, anchor: dict[str, Any]) -> str:
        doc = self.doc
        if kind != "bookmark":
            return ""
        if doc.layout == "fixed":
            return f"Page {int(anchor.get('page', 0)) + 1}"
        text = doc.block_text(anchor.get("block", "")) or ""     # type: ignore[attr-defined]
        return " ".join(text.split())[:60] or "Bookmark"

    # ------------------------------------------------------------------ create
    def _build(self, raw: dict[str, Any]) -> dict[str, Any]:
        base = model.validate_new(raw)
        kind = base["kind"]
        if kind in model.TEXT_KINDS and not self.doc.capabilities().get(kind, True):
            raise UnsupportedOperation(f"This document has no text layer to {kind}")
        anchor = self._anchor_for(kind, raw.get("anchor"))
        if kind in model.TEXT_KINDS and anchor["kind"] not in ("text", "pdf-text"):
            raise BadRequest(f"A {kind} needs selected text")
        geometry = model.clean_geometry(kind, raw.get("geometry"), self.host)
        if kind in ("ink", "shape") and anchor["kind"] not in ("block", "page"):
            raise BadRequest("Drawings are attached to a block or a page")
        if kind == "bookmark" and not base["body"].get("title"):
            base["body"]["title"] = self._default_title(kind, anchor)
        stamp = model.now()
        wanted_id = raw.get("id")
        if isinstance(wanted_id, str) and model._ID.match(wanted_id) and self.store.get_or_none(wanted_id, include_deleted=True) is None:
            ann_id = wanted_id                                # an imported annotation keeps its identity when that is free
        else:
            ann_id = model.new_id()
        ann: dict[str, Any] = {"id": ann_id, "doc_id": self.doc.doc_id, "kind": kind, "anchor": anchor, **{k: v for k, v in base.items() if k != "kind"},
                               "created": stamp, "modified": stamp, "fingerprint": self.doc.fingerprint, "visible": True}
        if geometry is not None:
            ann["geometry"] = geometry
        if self.author:
            ann["author"] = self.author
        model.check_size(ann)
        return ann

    @staticmethod
    def _same_place(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if a["anchor"].get("kind") not in ("text", "pdf-text") or a["kind"] != b["kind"]:
            return False
        def key(x: dict[str, Any]) -> list[tuple]:
            return [(s.get("block", s.get("page")), s["start"], s["end"]) for s in x["anchor"]["segments"]]
        return key(a) == key(b) and a["style"].get("color") == b["style"].get("color")

    @staticmethod
    def _position_key(ann: dict[str, Any]) -> Any:
        a = ann["anchor"]
        segs = [(s.get("block", s.get("page")), s["start"], s["end"]) for s in a.get("segments", [])]
        return (a.get("kind"), tuple(segs), a.get("block"), a.get("page"), a.get("x"), a.get("y"), a.get("offset"))

    def _same_content(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        return (a["kind"] == b["kind"] and self._position_key(a) == self._position_key(b) and a.get("body", {}) == b.get("body", {})
                and a.get("geometry") == b.get("geometry") and a.get("style") == b.get("style"))

    def create(self, raw: dict[str, Any], *, label: str | None = None) -> dict[str, Any]:
        return self.create_many([raw], label=label)[0]

    def create_many(self, raws: list[dict[str, Any]], *, label: str | None = None) -> list[dict[str, Any]]:
        if not isinstance(raws, list) or not 1 <= len(raws) <= 500:
            raise BadRequest("Create between 1 and 500 annotations at a time")
        built = [self._build(r) for r in raws]
        existing = self.store.list(self.doc.doc_id)
        out, changes = [], []
        for ann in built:
            twin = next((e for e in existing if self._same_place(e, ann)), None)
            if twin is not None and ann["kind"] in model.TEXT_KINDS:
                out.append({**self.view(twin), "duplicate": True})
                continue
            stored = self.store.insert(ann)
            existing.append(stored)
            changes.append({"id": stored["id"], "before": None, "after": copy.deepcopy(stored)})
            out.append(self.view(stored))
        self.history.record(label or ("Add " + built[0]["kind"] + ("s" if len(built) > 1 else "")), changes)
        return out

    # ------------------------------------------------------------------ change
    def _apply_patch(self, current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise BadRequest("The change must be an object")
        unknown = set(patch) - set(PATCHABLE) - {"rev"}
        if unknown:
            raise BadRequest(f"These fields cannot be changed this way: {', '.join(sorted(unknown))}")
        new = copy.deepcopy(current)
        kind = current["kind"]
        if "style" in patch:
            new["style"] = model.clean_style(kind, {**current.get("style", {}), **(patch["style"] or {})})
        if "body" in patch:
            merged = {**current.get("body", {}), **(patch["body"] or {})}
            new["body"] = model.clean_body({k: v for k, v in merged.items() if v not in (None, "")} if kind != "note" else merged)
            if kind == "note" and not (new["body"].get("text") or new["body"].get("title")):
                raise BadRequest("A note needs some text (delete it to remove it)")
        if "visible" in patch:
            if not isinstance(patch["visible"], bool):
                raise BadRequest("visible must be true or false")
            new["visible"] = patch["visible"]
        if "geometry" in patch:
            new["geometry"] = model.clean_geometry(kind, patch["geometry"], self.host)
            if new["geometry"] is None:
                raise BadRequest("Only drawings have geometry")
        if "group" in patch:
            new["group"] = model.validate_new({"kind": kind, "group": patch["group"]}).get("group")
        model.check_size(new)
        return new

    def update(self, ann_id: str, patch: dict[str, Any], base_rev: int | None = None, *, label: str | None = None) -> dict[str, Any]:
        return self.update_many([{"id": ann_id, "patch": patch, "rev": base_rev}], label=label)[0]

    def update_many(self, items: list[dict[str, Any]], *, label: str | None = None) -> list[dict[str, Any]]:
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise BadRequest("Change between 1 and 500 annotations at a time")
        out, changes = [], []
        for item in items:
            ann_id = item.get("id")
            current = self.store.get(ann_id)
            if current["doc_id"] != self.doc.doc_id:
                raise NotFound("That annotation belongs to another document")
            new = self._apply_patch(current, item.get("patch") or {})
            stored = self.store.update(ann_id, new, item.get("rev"))
            changes.append({"id": ann_id, "before": copy.deepcopy(current), "after": copy.deepcopy(stored)})
            out.append(self.view(stored))
        self.history.record(label or "Edit", changes)
        return out

    def delete_many(self, ids: list[str], *, label: str | None = None) -> list[str]:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 2000 or not all(isinstance(i, str) for i in ids):
            raise BadRequest("Give between 1 and 2000 annotation ids")
        changes, removed = [], []
        for ann_id in ids:
            try:
                before = self.store.delete(ann_id)
            except NotFound:
                continue
            changes.append({"id": ann_id, "before": copy.deepcopy(before), "after": None})
            removed.append(ann_id)
        self.history.record(label or ("Delete annotation" + ("s" if len(removed) > 1 else "")), changes)
        return removed

    def accept_moved(self, ids: list[str]) -> list[dict[str, Any]]:
        """The person confirms that the place Reader re-found is right: the anchor is rewritten with fresh context."""
        out, changes = [], []
        for ann_id in ids:
            current = self.store.get(ann_id)
            resolved = self.doc.resolve_anchor(current["anchor"])
            if resolved["status"] == "detached":
                raise BadRequest("This annotation could not be re-found; choose a new place for it instead")
            new = copy.deepcopy(current)
            segs = resolved.get("segments")
            if segs:
                new["anchor"] = self.doc.make_text_anchor([{k: s[k] for k in s if k in ("block", "page", "start", "end")} for s in segs])  # type: ignore[attr-defined]
            new["fingerprint"] = self.doc.fingerprint
            stored = self.store.update(ann_id, new, None)
            changes.append({"id": ann_id, "before": copy.deepcopy(current), "after": copy.deepcopy(stored)})
            out.append(self.view(stored))
        self.history.record("Confirm new position", changes)
        return out

    def reanchor(self, ann_id: str, raw_anchor: dict[str, Any]) -> dict[str, Any]:
        """Attach an annotation (typically a detached one) to a place the person chose. Drawings keep their shape."""
        current = self.store.get(ann_id)
        new = copy.deepcopy(current)
        new["anchor"] = self._anchor_for(current["kind"], raw_anchor)
        if current["kind"] in model.TEXT_KINDS and new["anchor"]["kind"] not in ("text", "pdf-text"):
            raise BadRequest("Choose text to attach this to")
        if current["kind"] in ("ink", "shape") and new["anchor"]["kind"] not in ("block", "page"):
            raise BadRequest("Drawings are attached to a block or a page")
        new["fingerprint"] = self.doc.fingerprint
        stored = self.store.update(ann_id, new, None)
        self.history.record("Move annotation", [{"id": ann_id, "before": copy.deepcopy(current), "after": copy.deepcopy(stored)}])
        return self.view(stored)

    # ------------------------------------------------------------------ undo / redo
    def _replay(self, entry: Any, backwards: bool) -> list[dict[str, Any]]:
        touched = []
        for change in (reversed(entry.changes) if backwards else entry.changes):
            state = change["before"] if backwards else change["after"]
            result = self.store.apply_state(change["id"], self.doc.doc_id, copy.deepcopy(state) if state is not None else None)
            touched.append({"id": change["id"], "annotation": self.view(result) if result is not None else None})
        return touched

    def undo(self) -> dict[str, Any]:
        entry = self.history.pop_undo()
        if entry is None:
            raise Conflict("There is nothing to undo")
        return {"label": entry.label, "changes": self._replay(entry, True), "history": self._history_state()}

    def redo(self) -> dict[str, Any]:
        entry = self.history.pop_redo()
        if entry is None:
            raise Conflict("There is nothing to redo")
        return {"label": entry.label, "changes": self._replay(entry, False), "history": self._history_state()}

    # ------------------------------------------------------------------ import
    def import_many(self, items: Any) -> dict[str, Any]:
        """Bring in annotations from an export (``slai-reader-annotations/1``). Each one is re-anchored to this document from its quote
        and checked like a new annotation; text that cannot be found is reported and skipped, never attached somewhere else."""
        if not isinstance(items, list) or len(items) > 5000:
            raise BadRequest("The file holds no annotation list, or too many annotations (limit 5000)")
        imported, duplicates, skipped = [], 0, []
        for item in items:
            ident = item.get("id") if isinstance(item, dict) else None
            try:
                if not isinstance(item, dict) or not isinstance(item.get("anchor"), dict):
                    raise BadRequest("not an annotation")
                anchor = item["anchor"]
                raw: dict[str, Any] = {k: item[k] for k in ("id", "kind", "style", "body", "geometry", "group") if k in item}
                if anchor.get("kind") in ("text", "pdf-text"):
                    resolved = self.doc.resolve_anchor(anchor)
                    if resolved["status"] == "detached":
                        raise BadRequest("its text was not found in this document")
                    keep = ("page", "block", "start", "end")
                    raw["anchor"] = {"kind": anchor["kind"], "segments": [{k: seg[k] for k in keep if k in seg} for seg in resolved["segments"]]}
                else:
                    raw["anchor"] = anchor
                candidate = self._build(raw)
                if any(self._same_content(e, candidate) for e in self.store.list(self.doc.doc_id)):
                    duplicates += 1
                    continue
                made = self.create(raw, label="Import")
                if made.get("duplicate"):
                    duplicates += 1
                else:
                    imported.append(made["id"])
            except (BadRequest, UnsupportedOperation) as exc:
                skipped.append({"id": ident, "reason": exc.message})
        return {"imported": len(imported), "duplicates": duplicates, "skipped": skipped, **self.list()}

    # ------------------------------------------------------------------ fixed-layout drawing support
    def page_marks(self, page: int) -> list[dict[str, Any]]:
        """Highlight rectangles for every text annotation that touches ``page`` (PDF). One list entry per annotation segment."""
        if self.doc.layout != "fixed":
            raise UnsupportedOperation("Page rectangles exist for PDF documents only")
        out = []
        for ann in self.store.list(self.doc.doc_id):
            if ann["anchor"].get("kind") != "pdf-text" or ann.get("visible") is False:
                continue
            if not any(isinstance(sg, dict) and sg.get("page") == page for sg in ann["anchor"].get("segments", [])):
                continue                      # a PDF segment is always resolved on its own page, so nothing else needs to be looked at
            resolved = self.doc.resolve_anchor(ann["anchor"])
            for seg in resolved.get("segments", []):
                if seg.get("page") != page or seg["status"] == "detached":
                    continue
                key = (page, seg["start"], seg["end"])
                if key not in self._rect_cache:
                    self._rect_cache[key] = self.doc.rects_for_range(page, seg["start"], seg["end"])  # type: ignore[attr-defined]
                    if len(self._rect_cache) > 5000:
                        self._rect_cache.pop(next(iter(self._rect_cache)))
                out.append({"id": ann["id"], "kind": ann["kind"], "status": seg["status"], "rects": self._rect_cache[key]})
        return out

    def close(self) -> None:
        """Called when the document is closed: erased annotations are removed for good and a snapshot is written."""
        try:
            self.store.purge_deleted(self.doc.doc_id)
            self.store.snapshot(self.doc.doc_id)
        except Exception:  # noqa: BLE001 - closing never fails because a backup could not be written
            pass
        self.history.clear()
