"""Open documents. A session is one document opened by one window: the parsed document, its annotation service, its undo history.

Sessions are kept in memory only. They are closed on request, after a period of disuse, or when too many are open (the least
recently used goes first; its annotations are already saved, only its undo history is lost).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import prefs as prefs_mod
from .annotations.service import AnnotationService
from .annotations.store import Store
from .bridge import DocumasterLink, ReaderEngines
from .config import Settings
from .documents.base import OpenDocument
from .documents.loader import open_document
from .errors import BadRequest, NotFound, ReaderError
from .files import atomic_write, resolve_user_path, safe_filename, sha256_bytes


@dataclass
class Session:
    sid: str
    doc: OpenDocument
    service: AnnotationService
    opened: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    carry_over: dict[str, Any] | None = None
    outputs: dict[str, Path] = field(default_factory=dict)
    password_used: bool = False
    assist_allowed: bool = False          # the person's per-document permission to send text to an agent (never persisted as a default)

    def touch(self) -> None:
        self.last_used = time.time()


class SessionManager:
    def __init__(self, settings: Settings, engines: ReaderEngines, link: DocumasterLink, store: Store, prefs: prefs_mod.PrefsFile) -> None:
        self.settings = settings
        self.engines = engines
        self.link = link
        self.store = store
        self.prefs = prefs
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------ opening
    def open_path(self, raw_path: str, *, password: str | None = None, managed_copy: bool = False, name: str | None = None) -> Session:
        path = resolve_user_path(raw_path, max_bytes=self.settings.max_open_bytes, allow_network=self.settings.allow_network_paths)
        return self._open(path, password=password, managed_copy=managed_copy, name=name)

    def open_upload(self, name: str, data: bytes, *, password: str | None = None) -> Session:
        if not data:
            raise BadRequest("The file is empty")
        stem = safe_filename(Path(name).stem or "document", "document", 80)
        suffix = Path(name).suffix[:12]
        target = self.settings.uploads_dir / f"{sha256_bytes(data)[:16]}-{stem}{suffix}"
        if not target.exists():
            atomic_write(target, data)
        return self._open(target, password=password, managed_copy=True, name=name)

    def _open(self, path: Path, *, password: str | None, managed_copy: bool, name: str | None) -> Session:
        doc = open_document(path, settings=self.settings, engines=self.engines, link=self.link, password=password, name=name, managed_copy=managed_copy)
        previous = self.store.previous_for_path(str(path))
        try:
            self.store.touch_document({"doc_id": doc.doc_id, "title": doc.title or (name or path.name), "format": doc.format, "layout": doc.layout,
                                       "path": str(path), "size": path.stat().st_size, "fingerprint": doc.fingerprint, "revision": doc.revision,
                                       "pages": getattr(doc, "pages", None), "author": getattr(doc, "author", None)})
        except Exception:
            doc.close()
            raise
        carry = None
        if previous and previous.get("doc_id") and previous["doc_id"] != doc.doc_id:
            n = self.store.counts(previous["doc_id"])
            total = sum(n.values())
            if total:
                carry = {"fromDocId": previous["doc_id"], "annotations": total,
                         "message": f"This file has changed since you last read it, and {total} annotation{'s' if total != 1 else ''} "
                                    f"from the earlier version can be carried over."}
        sess = Session(sid=secrets.token_hex(12), doc=doc, service=AnnotationService(doc, self.store), carry_over=carry, password_used=bool(password))
        with self.lock:
            self._evict()
            self.sessions[sess.sid] = sess
        return sess

    def _evict(self) -> None:
        now = time.time()
        for sid, s in list(self.sessions.items()):
            if now - s.last_used > self.settings.session_ttl_s:
                self.close(sid)
        while len(self.sessions) >= self.settings.max_sessions:
            oldest = min(self.sessions.values(), key=lambda s: s.last_used)
            self.close(oldest.sid)

    # ------------------------------------------------------------------ access
    def get(self, sid: str) -> Session:
        with self.lock:
            s = self.sessions.get(sid)
        if s is None:
            raise NotFound("That document is no longer open. Open it again from Recent documents.")
        s.touch()
        return s

    def close(self, sid: str) -> bool:
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s is None:
            return False
        try:
            s.service.close()
            self.store.snapshot_pending(s.doc.doc_id)
        finally:
            s.doc.close()
        return True

    def close_all(self) -> None:
        for sid in list(self.sessions):
            try:
                self.close(sid)
            except Exception:  # noqa: BLE001 - shutting down
                pass

    # ------------------------------------------------------------------ state of one session
    def file_status(self, s: Session) -> dict[str, Any]:
        """Has the file on disk changed since it was opened? (Reader never overwrites it, but the person should know.)"""
        path = s.doc.path
        if path is None:
            return {"state": "unknown"}
        try:
            st = path.stat()
        except OSError:
            return {"state": "missing", "message": "The file is no longer where it was opened from. What is on screen is still readable, "
                                                    "and your annotations are saved."}
        if (st.st_size, st.st_mtime_ns) != s.doc.stat_signature:
            return {"state": "changed", "message": "The file was changed by another program since you opened it. Reload to read the new version; "
                                                   "your annotations are kept and re-checked against it."}
        return {"state": "same"}

    def reload(self, s: Session, password: str | None = None) -> Session:
        """Open the file again at the same place (after an outside change). Annotations stay with the document identity."""
        if s.doc.path is None:
            raise BadRequest("This document has no file to reload")
        name = s.doc.path.name
        managed = s.doc.managed_copy
        path = s.doc.path
        self.close(s.sid)
        return self._open(path, password=password, managed_copy=managed, name=name)

    def carry_over(self, s: Session) -> dict[str, Any]:
        if not s.carry_over:
            raise BadRequest("There is nothing to carry over for this document")
        moved = self.store.reassign(s.carry_over["fromDocId"], s.doc.doc_id)
        s.carry_over = None
        return {"moved": moved, **s.service.list()}

    def reading_state(self, s: Session, dark: bool = False) -> dict[str, Any]:
        rec = self.store.get_document(s.doc.doc_id) or {}
        doc_prefs = prefs_mod.lenient_doc_prefs(rec.get("doc_prefs") or {})
        eff = prefs_mod.effective(self.prefs.get(), doc_prefs, dark_system=dark)
        return {"position": rec.get("position"), "progress": rec.get("progress", 0), "docPrefs": doc_prefs, "effective": eff}

    def save_position(self, s: Session, location: Any, progress: Any) -> None:
        if not isinstance(location, dict):
            raise BadRequest("location must be an object")
        norm = s.doc.normalise_location(location)
        try:
            pct = float(progress) if progress is not None else float(norm.get("percent", 0.0))
        except (TypeError, ValueError) as exc:
            raise BadRequest("progress must be a number") from exc
        self.store.set_position(s.doc.doc_id, norm, pct)

    def set_doc_prefs(self, s: Session, patch: Any, reset: bool = False, dark: bool = False, unset: Any = None) -> dict[str, Any]:
        rec = self.store.get_document(s.doc.doc_id) or {}
        current = prefs_mod.lenient_doc_prefs(rec.get("doc_prefs") or {})
        if unset is not None and (not isinstance(unset, list) or not all(isinstance(k, str) and k in prefs_mod.DOC_FIELDS for k in unset)):
            raise BadRequest("unset must be a list of document setting names")
        drop = set(unset or [])
        new = {} if reset else {k: v for k, v in {**current, **prefs_mod.clean_doc_prefs(patch)}.items() if k not in drop}
        self.store.set_doc_prefs(s.doc.doc_id, new or None)
        return prefs_mod.effective(self.prefs.get(), new, dark_system=dark)

    def appearance_for(self, s: Session, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """The appearance dict PdfAppearance.from_prefs understands, derived from the effective preferences."""
        eff = self.reading_state(s)["effective"]
        v = {**eff["values"], **(overrides or {})}
        if v.get("pdfAppearance") == "original":
            return {"mode": "light"}
        theme = v["theme"]
        mode = "light" if theme == "system" else theme
        return {"mode": mode, "textColor": v.get("fontColor"), "textOpacity": v.get("fontOpacity", 100) / 100.0}
