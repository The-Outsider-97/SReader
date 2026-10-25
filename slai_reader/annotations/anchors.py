"""Anchors: how an annotation stays attached to the text it is about.

An anchor never depends on screen pixels. Text anchors carry the position in the document's own coordinates **and** the
text itself (the quote plus a little context on both sides), so the annotation can be checked, and where it is safe
re-found, after the document changes:

* flow documents (SLDC and everything shown as SLDC):  ``{"block": id, "start": s, "end": e}``  - the same shape SLDC's own
  review comments use (``anchor.block / start / end / block_sha256``), extended with the quote selectors;
* PDF text:  ``{"page": i, "start": s, "end": e}``  - character offsets in pdfium's text of page ``i``.

``resolve_text_segment`` returns one of

``ok``        the quote is exactly where the anchor says it is;
``moved``     the quote was found again, at one unambiguous place, and the new place is returned (the person is told);
``detached``  the text is gone or could be several places; nothing is guessed and the annotation is kept as it was.

This is the rule from the specification: never silently attach a note to an unrelated passage.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

CONTEXT = 32                 # characters of context stored on each side of a quote
MIN_QUOTE_FOR_GLOBAL = 12    # a shorter quote is too common to be re-found in a different block
MAX_OCCURRENCES = 2000       # bounds the work done on a hostile or repetitive document


def selectors(text: str, start: int, end: int) -> dict[str, Any]:
    """The text selectors for ``text[start:end]``."""
    return {"quote": text[start:end], "prefix": text[max(0, start - CONTEXT):start], "suffix": text[end:end + CONTEXT]}


def _common_suffix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        n += 1
    return n


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def occurrences(text: str, quote: str, limit: int = MAX_OCCURRENCES) -> list[int]:
    found: list[int] = []
    if not quote:
        return found
    index = text.find(quote)
    while index != -1 and len(found) < limit:
        found.append(index)
        index = text.find(quote, index + 1)
    return found


def relocate_in_text(text: str, quote: str, prefix: str, suffix: str, hint: int | None = None) -> tuple[str, int] | None:
    """Find ``quote`` in ``text``. ``("ok", start)`` if it sits at ``hint``; ``("moved", start)`` if there is exactly one best
    place; ``None`` if it is absent or the best place is ambiguous."""
    if not quote:
        return None
    if hint is not None and text[hint:hint + len(quote)] == quote:
        return "ok", hint
    spots = occurrences(text, quote)
    if not spots:
        return None
    if len(spots) == 1:
        # A single exact occurrence is only trusted when the context agrees at least a little, or the quote is distinctive.
        i = spots[0]
        score = _common_suffix(prefix, text[max(0, i - CONTEXT):i]) + _common_prefix(suffix, text[i + len(quote):i + len(quote) + CONTEXT])
        if score > 0 or len(quote) >= MIN_QUOTE_FOR_GLOBAL or not (prefix or suffix):
            return "moved", i
        return None
    scored = []
    for i in spots:
        score = _common_suffix(prefix, text[max(0, i - CONTEXT):i]) + _common_prefix(suffix, text[i + len(quote):i + len(quote) + CONTEXT])
        scored.append((score, i))
    scored.sort(reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return "moved", scored[0][1]
    return None


def resolve_flow_segment(seg: dict[str, Any], block_text: Callable[[str], str | None],
                         all_blocks: Callable[[], Iterable[tuple[str, str]]]) -> dict[str, Any]:
    """Resolve one flow-text segment against the current document.

    ``block_text(id)`` gives the text of a block or ``None``; ``all_blocks()`` yields ``(id, text)`` in order (used only when the
    original block no longer holds the quote).
    """
    quote = seg.get("quote", "")
    prefix, suffix = seg.get("prefix", ""), seg.get("suffix", "")
    block, start = seg.get("block"), seg.get("start")
    text = block_text(block) if isinstance(block, str) else None
    if text is not None:
        hit = relocate_in_text(text, quote, prefix, suffix, start if isinstance(start, int) else None)
        if hit:
            status, new_start = hit
            return {"status": status, "block": block, "start": new_start, "end": new_start + len(quote)}
    # The block is gone or its text no longer holds the quote: look elsewhere, but only for a distinctive quote with matching context.
    if len(quote) >= MIN_QUOTE_FOR_GLOBAL:
        candidates: list[tuple[int, str, int]] = []
        for bid, btxt in all_blocks():
            if bid == block:
                continue
            for i in occurrences(btxt, quote, 4):
                score = _common_suffix(prefix, btxt[max(0, i - CONTEXT):i]) + _common_prefix(suffix, btxt[i + len(quote):i + len(quote) + CONTEXT])
                candidates.append((score, bid, i))
            if len(candidates) > 50:
                break
        candidates.sort(key=lambda c: -c[0])
        if candidates and candidates[0][0] > 0 and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
            _, bid, i = candidates[0]
            return {"status": "moved", "block": bid, "start": i, "end": i + len(quote)}
    return {"status": "detached", "block": block, "start": start, "end": seg.get("end")}


def resolve_pdf_segment(seg: dict[str, Any], page_text: Callable[[int], str | None], page_count: int) -> dict[str, Any]:
    """Resolve one PDF text segment. A quote is re-found on its own page only; pages are never searched blindly."""
    page = seg.get("page")
    quote = seg.get("quote", "")
    if not isinstance(page, int) or not 0 <= page < page_count:
        return {"status": "detached", "page": page, "start": seg.get("start"), "end": seg.get("end")}
    text = page_text(page)
    if text is not None:
        hit = relocate_in_text(text, quote, seg.get("prefix", ""), seg.get("suffix", ""), seg.get("start") if isinstance(seg.get("start"), int) else None)
        if hit:
            status, new_start = hit
            return {"status": status, "page": page, "start": new_start, "end": new_start + len(quote)}
    return {"status": "detached", "page": page, "start": seg.get("start"), "end": seg.get("end")}


def combine_status(statuses: Sequence[str]) -> str:
    """One status for an annotation with several segments: the worst one decides."""
    for level in ("detached", "moved"):
        if level in statuses:
            return level
    return "ok"
