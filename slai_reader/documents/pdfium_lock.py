"""PDFium is not thread-safe: two threads drawing or reading pages at once can crash the process. The server answers requests on
many threads (a viewer asks for a dozen thumbnails at once) and a background thread indexes text, so every function that
touches pdfium holds this one re-entrant lock. Work is done per page, so a render can interleave with indexing."""
from __future__ import annotations

import functools
import threading
from typing import Any, Callable, TypeVar

PDFIUM_LOCK = threading.RLock()
F = TypeVar("F", bound=Callable[..., Any])


def pdfium_locked(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with PDFIUM_LOCK:
            return fn(*args, **kwargs)
    return wrapper  # type: ignore[return-value]
