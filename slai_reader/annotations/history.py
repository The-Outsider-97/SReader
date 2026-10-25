"""Undo and redo for annotation changes.

An entry is a list of changes made together (one eraser sweep that removed five strokes is one entry). Each change is
``{"id", "before", "after"}``: ``before`` is ``None`` for a creation and ``after`` is ``None`` for a deletion. Undoing applies
every change backwards, redoing applies it forwards. A new change clears what could have been redone. The history belongs to one
open document session and is not kept after it closes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entry:
    label: str
    changes: list[dict[str, Any]]


@dataclass
class History:
    limit: int = 300
    undo_stack: list[Entry] = field(default_factory=list)
    redo_stack: list[Entry] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, label: str, changes: list[dict[str, Any]]) -> None:
        if not changes:
            return
        with self.lock:
            self.undo_stack.append(Entry(label, changes))
            if len(self.undo_stack) > self.limit:
                del self.undo_stack[0]
            self.redo_stack.clear()

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def peek(self) -> dict[str, str | None]:
        return {"undo": self.undo_stack[-1].label if self.undo_stack else None,
                "redo": self.redo_stack[-1].label if self.redo_stack else None}

    def pop_undo(self) -> Entry | None:
        with self.lock:
            if not self.undo_stack:
                return None
            entry = self.undo_stack.pop()
            self.redo_stack.append(entry)
            return entry

    def pop_redo(self) -> Entry | None:
        with self.lock:
            if not self.redo_stack:
                return None
            entry = self.redo_stack.pop()
            self.undo_stack.append(entry)
            return entry

    def clear(self) -> None:
        with self.lock:
            self.undo_stack.clear()
            self.redo_stack.clear()
