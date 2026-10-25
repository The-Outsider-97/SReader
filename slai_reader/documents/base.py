"""What every open document offers. The two layouts (flow and fixed) implement the same small surface; the API and the
annotation store only ever talk to this interface, never to a format."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class OpenDocument:
    """Common base: identity, change detection and the capability report."""

    layout = "flow"                 # "flow" | "fixed"
    format = ""
    doc_id = ""                     # stable identity of the *document* (not of one file on one disk)
    fingerprint = ""                # sha256 of the exact bytes opened
    revision: str | None = None     # format-level revision (SLDC revision_id), if the format has one
    title = ""
    path: Path | None = None
    managed_copy = False            # True for an uploaded copy kept in Reader's own folder
    stat_signature: tuple[int, int] = (0, 0)

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ to be provided
    def info(self) -> dict[str, Any]:                     # pragma: no cover - interface
        raise NotImplementedError

    def capabilities(self) -> dict[str, Any]:             # pragma: no cover - interface
        raise NotImplementedError

    def outline(self) -> list[dict[str, Any]]:            # pragma: no cover - interface
        raise NotImplementedError

    def search(self, query: str, *, limit: int = 200, case_sensitive: bool = False) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------ shared
    def base_capabilities(self) -> dict[str, Any]:
        return {
            "layout": self.layout, "format": self.format,
            "render": True, "textSelection": True, "search": True,
            "highlight": True, "underline": True, "strike": True, "typedNotes": True, "stickyNotes": True,
            "ink": True, "eraser": "stroke", "bookmarks": True, "outline": True,
            "zoom": True, "colorModes": True,
            "nativeAnnotations": False,
        }
