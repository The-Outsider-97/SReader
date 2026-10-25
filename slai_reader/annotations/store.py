"""Crash-safe local storage for the library, reading positions and annotations (one SQLite file, standard library only).

Why SQLite: a commit is atomic and durable (``journal_mode=WAL``, ``synchronous=FULL``), so an interrupted save leaves either the
old state or the new one and never a half-written file; several Reader windows can use the file at once; and a schema version
lives inside it (``PRAGMA user_version``) so a newer Reader can migrate an older store and an older Reader refuses a newer one
instead of damaging it.

On top of that Reader keeps rolling JSON snapshots of every document's annotations (``backups/``). If the database is ever found
damaged it is moved aside (never deleted), a fresh one is created and the snapshots are restored into it.

Each annotation row carries ``rev``. An update names the revision it was based on; if someone else changed the row since, the
update is refused with a conflict instead of overwriting their change.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import ANNOTATION_SCHEMA
from ..errors import Conflict, NotFound, StoreCorrupt
from ..files import atomic_write
from . import model

SCHEMA_VERSION = 1
SNAPSHOT_EVERY = 10          # mutations of one document between automatic snapshots
SNAPSHOT_SECONDS = 15.0      # ... or this long after the previous snapshot, whichever comes first (the first change is backed up at once)
SNAPSHOT_KEEP = 8


def _v1(conn: sqlite3.Connection) -> None:
    # Statements are run one by one: executescript() would commit the migration's transaction before it finishes.
    for statement in """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY, title TEXT, format TEXT, layout TEXT, path TEXT, size INTEGER, fingerprint TEXT, revision TEXT,
            added TEXT, opened TEXT, position TEXT, progress REAL NOT NULL DEFAULT 0, doc_prefs TEXT, author TEXT, pages INTEGER);
        CREATE TABLE IF NOT EXISTS annotations (
            id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, kind TEXT NOT NULL, rev INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
            created TEXT, modified TEXT, fingerprint TEXT, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ann_doc ON annotations (doc_id, deleted);
        CREATE TABLE IF NOT EXISTS paths (path TEXT PRIMARY KEY, doc_id TEXT, fingerprint TEXT, seen TEXT);
    """.split(";"):
        if statement.strip():
            conn.execute(statement)


# version -> function that upgrades a store from (version - 1) to version
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _v1}


def _row_doc(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("position", "doc_prefs"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except ValueError:
                d[key] = None
    return d


class Store:
    def __init__(self, db_path: Path, backups_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.backups_dir = Path(backups_dir)
        self.lock = threading.RLock()
        self.conn: sqlite3.Connection | None = None
        self.recovered: dict[str, Any] | None = None       # set when a damaged store was replaced
        self._since_snapshot: dict[str, int] = {}
        self._last_snapshot: dict[str, float] = {}
        self._open()

    # ------------------------------------------------------------------ opening, migration, recovery
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, isolation_level=None, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except BaseException:
            # A failed PRAGMA (including a corrupt database header) must not leave an open Windows file handle that prevents recovery/quarantine.
            conn.close()
            raise

    def _open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise sqlite3.DatabaseError(f"integrity check failed: {ok}")
            self._migrate(conn)
            self.conn = conn
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                try:
                    conn.close()          # Windows cannot move a file that is still open, so the damaged one must be released first
                except sqlite3.Error:
                    pass
            self._recover(str(exc))

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            conn.close()
            raise StoreCorrupt(f"This annotation store was written by a newer SLAI Reader (schema {version}); this version understands "
                               f"up to {SCHEMA_VERSION}. It was not opened, so nothing in it can be damaged.",
                               details={"found": version, "supported": SCHEMA_VERSION})
        for target in range(version + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                MIGRATIONS[target](conn)
                conn.execute(f"PRAGMA user_version={target}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _recover(self, reason: str) -> None:
        """Move a damaged database aside (it is kept) and rebuild from the newest snapshot of every document."""
        try:
            if self.conn is not None:
                self.conn.close()
        except sqlite3.Error:
            pass
        self.conn = None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        moved = []
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.db_path) + suffix)
            if src.exists():
                dst = Path(str(self.db_path) + f".corrupt-{stamp}" + suffix)
                try:
                    os.replace(src, dst)
                    moved.append(dst.name)
                except OSError as exc:
                    # Do not silently reopen a known-corrupt database when Windows
                    # denies its quarantine (e.g. another process holds a lock).
                    raise StoreCorrupt(
                        "The damaged annotation database could not be quarantined. Close other Reader instances and retry; no replacement database was created."
                    ) from exc
        conn = self._connect()
        self._migrate(conn)
        self.conn = conn
        restored_docs = restored_ann = 0
        for snapshot in self._latest_snapshots():
            try:
                data = json.loads(snapshot.read_text(encoding="utf-8"))
                if data.get("schema") != 1:
                    continue
                self._restore(data)
                restored_docs += 1
                restored_ann += len(data.get("annotations", []))
            except (OSError, ValueError, KeyError, sqlite3.Error):
                continue
        self.recovered = {"reason": reason[:300], "quarantined": moved, "documents": restored_docs, "annotations": restored_ann}

    def _latest_snapshots(self) -> list[Path]:
        out = []
        if not self.backups_dir.is_dir():
            return out
        for directory in self.backups_dir.iterdir():
            if directory.is_dir():
                files = sorted(directory.glob("*.json"))
                if files:
                    out.append(files[-1])
        return out

    def _restore(self, data: dict[str, Any]) -> None:
        doc = data["doc"]
        with self.tx():
            self.conn.execute(  # type: ignore[union-attr]
                "INSERT OR REPLACE INTO documents (doc_id, title, format, layout, path, size, fingerprint, revision, added, opened, position, progress, doc_prefs, author, pages) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc["doc_id"], doc.get("title"), doc.get("format"), doc.get("layout"), doc.get("path"), doc.get("size"), doc.get("fingerprint"),
                 doc.get("revision"), doc.get("added"), doc.get("opened"), json.dumps(doc.get("position")) if doc.get("position") else None,
                 doc.get("progress") or 0, json.dumps(doc.get("doc_prefs")) if doc.get("doc_prefs") else None, doc.get("author"), doc.get("pages")))
            for ann in data.get("annotations", []):
                rev = int(ann.pop("rev", 1))
                self.conn.execute(  # type: ignore[union-attr]
                    "INSERT OR REPLACE INTO annotations (id, doc_id, kind, rev, deleted, created, modified, fingerprint, data) VALUES (?,?,?,?,0,?,?,?,?)",
                    (ann["id"], doc["doc_id"], ann["kind"], rev, ann.get("created"), ann.get("modified"), ann.get("fingerprint"), json.dumps(ann)))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            assert self.conn is not None
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def close(self) -> None:
        self.snapshot_pending()
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self.conn.close()
                self.conn = None

    def integrity(self) -> str:
        with self.lock:
            return self.conn.execute("PRAGMA integrity_check").fetchone()[0]  # type: ignore[union-attr]

    # ------------------------------------------------------------------ documents (the library)
    def touch_document(self, info: dict[str, Any]) -> dict[str, Any]:
        """Record that a document was opened. ``info`` has doc_id, title, format, layout, path, size, fingerprint, revision, pages, author."""
        stamp = model.now()
        with self.tx() as c:
            row = c.execute("SELECT added FROM documents WHERE doc_id=?", (info["doc_id"],)).fetchone()
            if row is None:
                c.execute("INSERT INTO documents (doc_id, title, format, layout, path, size, fingerprint, revision, added, opened, pages, author) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (info["doc_id"], info.get("title"), info.get("format"), info.get("layout"), info.get("path"), info.get("size"),
                           info.get("fingerprint"), info.get("revision"), stamp, stamp, info.get("pages"), info.get("author")))
            else:
                c.execute("UPDATE documents SET title=?, format=?, layout=?, path=?, size=?, fingerprint=?, revision=?, opened=?, pages=?, author=? WHERE doc_id=?",
                          (info.get("title"), info.get("format"), info.get("layout"), info.get("path"), info.get("size"), info.get("fingerprint"),
                           info.get("revision"), stamp, info.get("pages"), info.get("author"), info["doc_id"]))
            if info.get("path"):
                c.execute("INSERT OR REPLACE INTO paths (path, doc_id, fingerprint, seen) VALUES (?,?,?,?)",
                          (info["path"], info["doc_id"], info.get("fingerprint"), stamp))
        return self.get_document(info["doc_id"]) or {}

    def previous_for_path(self, path: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM paths WHERE path=?", (path,)).fetchone()  # type: ignore[union-attr]
            return dict(row) if row else None

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self.lock:
            return _row_doc(self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone())  # type: ignore[union-attr]

    def library(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(  # type: ignore[union-attr]
                "SELECT d.*, "
                "(SELECT COUNT(*) FROM annotations a WHERE a.doc_id=d.doc_id AND a.deleted=0 AND a.kind NOT IN ('bookmark')) AS annotation_count, "
                "(SELECT COUNT(*) FROM annotations a WHERE a.doc_id=d.doc_id AND a.deleted=0 AND a.kind='bookmark') AS bookmark_count "
                "FROM documents d WHERE d.opened IS NOT NULL ORDER BY d.opened DESC").fetchall()
        return [_row_doc(r) for r in rows if r is not None]  # type: ignore[misc]

    def set_position(self, doc_id: str, position: dict[str, Any], progress: float) -> None:
        with self.tx() as c:
            c.execute("UPDATE documents SET position=?, progress=? WHERE doc_id=?", (json.dumps(position), max(0.0, min(1.0, float(progress))), doc_id))

    def set_doc_prefs(self, doc_id: str, prefs: dict[str, Any] | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE documents SET doc_prefs=? WHERE doc_id=?", (json.dumps(prefs) if prefs else None, doc_id))

    def forget_document(self, doc_id: str, delete_annotations: bool = False) -> None:
        """Remove a document from the library. The file itself is never touched. Annotations stay unless ``delete_annotations``."""
        with self.tx() as c:
            if delete_annotations:
                c.execute("DELETE FROM annotations WHERE doc_id=?", (doc_id,))
                c.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
                c.execute("DELETE FROM paths WHERE doc_id=?", (doc_id,))
            else:
                c.execute("UPDATE documents SET opened=NULL WHERE doc_id=?", (doc_id,))   # hidden from recents, reading state and notes kept
        if delete_annotations:
            self._drop_snapshots(doc_id)

    # ------------------------------------------------------------------ annotations
    @staticmethod
    def _ann(row: sqlite3.Row) -> dict[str, Any]:
        ann = json.loads(row["data"])
        ann["rev"] = row["rev"]
        return ann

    def insert(self, ann: dict[str, Any]) -> dict[str, Any]:
        ann = dict(ann)
        ann.setdefault("schema", ANNOTATION_SCHEMA)
        data = {k: v for k, v in ann.items() if k != "rev"}
        with self.tx() as c:
            c.execute("INSERT INTO annotations (id, doc_id, kind, rev, deleted, created, modified, fingerprint, data) VALUES (?,?,?,?,0,?,?,?,?)",
                      (ann["id"], ann["doc_id"], ann["kind"], 1, ann["created"], ann["modified"], ann.get("fingerprint"), json.dumps(data)))
        self._changed(ann["doc_id"])
        return {**data, "rev": 1}

    def get(self, ann_id: str, include_deleted: bool = False) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM annotations WHERE id=?" + ("" if include_deleted else " AND deleted=0"), (ann_id,)).fetchone()  # type: ignore[union-attr]
        if row is None:
            raise NotFound("That annotation no longer exists")
        return self._ann(row)

    def get_or_none(self, ann_id: str | None, include_deleted: bool = False) -> dict[str, Any] | None:
        try:
            return self.get(ann_id, include_deleted) if ann_id else None
        except NotFound:
            return None

    def list(self, doc_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM annotations WHERE doc_id=? AND deleted=0 ORDER BY created, id", (doc_id,)).fetchall()  # type: ignore[union-attr]
        return [self._ann(r) for r in rows]

    def update(self, ann_id: str, new_data: dict[str, Any], base_rev: int | None) -> dict[str, Any]:
        stamp = model.now()
        data = {k: v for k, v in new_data.items() if k != "rev"}
        data["modified"] = stamp
        with self.tx() as c:
            row = c.execute("SELECT rev, doc_id FROM annotations WHERE id=? AND deleted=0", (ann_id,)).fetchone()
            if row is None:
                raise NotFound("That annotation no longer exists")
            if base_rev is not None and row["rev"] != base_rev:
                raise Conflict("This annotation was changed somewhere else since you opened it. Reload it to see the newer version.",
                               details={"id": ann_id, "currentRev": row["rev"], "yourRev": base_rev})
            c.execute("UPDATE annotations SET data=?, rev=rev+1, modified=?, fingerprint=? WHERE id=?", (json.dumps(data), stamp, data.get("fingerprint"), ann_id))
            rev = row["rev"] + 1
        self._changed(row["doc_id"])
        return {**data, "rev": rev}

    def delete(self, ann_id: str, base_rev: int | None = None) -> dict[str, Any]:
        """Soft delete: the row is kept (so undo works) until the document is closed or ``purge_deleted`` runs."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM annotations WHERE id=? AND deleted=0", (ann_id,)).fetchone()
            if row is None:
                raise NotFound("That annotation no longer exists")
            if base_rev is not None and row["rev"] != base_rev:
                raise Conflict("This annotation was changed somewhere else; it was not deleted.", details={"id": ann_id, "currentRev": row["rev"]})
            c.execute("UPDATE annotations SET deleted=1, rev=rev+1 WHERE id=?", (ann_id,))
        self._changed(row["doc_id"])
        return self._ann(row)

    def apply_state(self, ann_id: str, doc_id: str, state: dict[str, Any] | None) -> dict[str, Any] | None:
        """Undo/redo: make the row exactly ``state`` (or deleted if ``None``). The revision still moves forward."""
        with self.tx() as c:
            row = c.execute("SELECT rev FROM annotations WHERE id=?", (ann_id,)).fetchone()
            if state is None:
                if row is not None:
                    c.execute("UPDATE annotations SET deleted=1, rev=rev+1 WHERE id=?", (ann_id,))
                result = None
            else:
                data = {k: v for k, v in state.items() if k != "rev"}
                if row is None:
                    c.execute("INSERT INTO annotations (id, doc_id, kind, rev, deleted, created, modified, fingerprint, data) VALUES (?,?,?,?,0,?,?,?,?)",
                              (ann_id, doc_id, data["kind"], 1, data.get("created"), data.get("modified"), data.get("fingerprint"), json.dumps(data)))
                    result = {**data, "rev": 1}
                else:
                    c.execute("UPDATE annotations SET data=?, kind=?, deleted=0, rev=rev+1, modified=?, fingerprint=? WHERE id=?",
                              (json.dumps(data), data["kind"], data.get("modified"), data.get("fingerprint"), ann_id))
                    result = {**data, "rev": row["rev"] + 1}
        self._changed(doc_id)
        return result

    def purge_deleted(self, doc_id: str | None = None) -> int:
        with self.tx() as c:
            cur = c.execute("DELETE FROM annotations WHERE deleted=1" + (" AND doc_id=?" if doc_id else ""), (doc_id,) if doc_id else ())
            return cur.rowcount

    def reassign(self, from_doc: str, to_doc: str) -> int:
        """Move every annotation of one document identity to another (used when the same file was re-saved and its identity changed)."""
        with self.tx() as c:
            rows = c.execute("SELECT id, data FROM annotations WHERE doc_id=? AND deleted=0", (from_doc,)).fetchall()
            for row in rows:
                data = json.loads(row["data"])
                data["doc_id"] = to_doc
                c.execute("UPDATE annotations SET doc_id=?, data=?, rev=rev+1 WHERE id=?", (to_doc, json.dumps(data), row["id"]))
            return len(rows)

    def counts(self, doc_id: str) -> dict[str, int]:
        with self.lock:
            rows = self.conn.execute("SELECT kind, COUNT(*) AS n FROM annotations WHERE doc_id=? AND deleted=0 GROUP BY kind", (doc_id,)).fetchall()  # type: ignore[union-attr]
        return {r["kind"]: r["n"] for r in rows}

    def search_notes(self, query: str, doc_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Notes, highlights and bookmarks whose text, quote, title or tags contain ``query`` (case-insensitive), across one or all documents."""
        q = (query or "").strip().casefold()
        if not q:
            return []
        with self.lock:
            rows = self.conn.execute("SELECT * FROM annotations WHERE deleted=0" + (" AND doc_id=?" if doc_id else "") +  # type: ignore[union-attr]
                                     " AND kind NOT IN ('ink','shape') ORDER BY modified DESC", (doc_id,) if doc_id else ()).fetchall()
        out = []
        for row in rows:
            ann = self._ann(row)
            hay = " ".join([ann.get("body", {}).get("text", ""), ann.get("body", {}).get("title", ""), " ".join(ann.get("body", {}).get("tags", [])),
                            " ".join(seg.get("quote", "") for seg in ann.get("anchor", {}).get("segments", []))]).casefold()
            if q in hay:
                out.append(ann)
                if len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------ snapshots
    def _snap_dir(self, doc_id: str) -> Path:
        return self.backups_dir / hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:20]

    def _changed(self, doc_id: str) -> None:
        n = self._since_snapshot.get(doc_id, 0) + 1
        self._since_snapshot[doc_id] = n
        if n >= SNAPSHOT_EVERY or time.monotonic() - self._last_snapshot.get(doc_id, float("-inf")) >= SNAPSHOT_SECONDS:
            try:
                self.snapshot(doc_id)
            except OSError:
                pass                                    # a failed backup never fails the save that triggered it

    def snapshot(self, doc_id: str) -> Path | None:
        """Write the document's annotations to a JSON snapshot (atomic) and keep the newest few."""
        doc = self.get_document(doc_id)
        if doc is None:
            return None
        anns = self.list(doc_id)
        if not anns and not doc.get("position"):
            return None
        payload = {"schema": 1, "written": model.now(), "doc": {k: v for k, v in doc.items() if k not in ("annotation_count", "bookmark_count")},
                   "annotations": anns}
        directory = self._snap_dir(doc_id)
        name = time.strftime("%Y%m%dT%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.json"
        target = atomic_write(directory / name, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        files = sorted(directory.glob("*.json"))
        for old in files[:-SNAPSHOT_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        self._since_snapshot[doc_id] = 0
        self._last_snapshot[doc_id] = time.monotonic()
        return target

    def snapshot_pending(self, doc_id: str | None = None) -> int:
        """Back up every document that changed since its last snapshot (called when a document is closed and when Reader stops)."""
        done = 0
        for d in ([doc_id] if doc_id else list(self._since_snapshot)):
            if self._since_snapshot.get(d, 0) > 0:
                try:
                    if self.snapshot(d):
                        done += 1
                except (OSError, sqlite3.Error):
                    pass                                # a failed backup never stops a shutdown
        return done

    def _drop_snapshots(self, doc_id: str) -> None:
        directory = self._snap_dir(doc_id)
        if directory.is_dir():
            for f in directory.glob("*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
