"""What Reader shows beneath a scientific figure: the formulas of the functions it plots.

SLDC has no "graph" block. A graph written by SciCal is an ordinary SLDC ``figure`` block (the picture is a PNG asset) whose
``extensions["slai.scical"]["graph"]`` record says which functions the picture plots. The record is *inert data*: Reader reads it,
checks it, bounds it, and gives the client plain strings (LaTeX for the formula, a colour, a line style). Nothing in it is ever run.

A record that is malformed does not stop the document from opening and does not hide the picture; the client is told why the
formulas are missing (``{"error": ...}``) and shows the figure without them.

Record (``scical-graph/1``)::

    {"schema": "scical-graph/1", "id": "...", "kind": "2d", "angle": "rad", "viewport": {"xmin": .., "xmax": .., "ymin": .., "ymax": ..},
     "image": {"width": 800, "height": 500, "transparent": true} | null,
     "series": [{"id": "L1", "type": "function", "expression": "x*sin(1/x)", "latex": "f_{2}(x) = x\\sin(1/x)", "variable": "x",
                 "domain": {"variable": "t", "min": "0", "max": "2*pi"} | null, "color": "#b89b00", "dash": "solid|dash|dot|dashdot"}]}

A 3D graph (``"kind": "3d"``) has the same shape: one series per layer, its formula in ``latex`` (``z_{1}(x, y) = ...``, ``\\mathbf{r}_{2}(u, v) = ...``),
``"variable"`` naming its free variables (``"x, y"``, ``"u, v"``, ``"t"``), and ``"coordinates"`` with a ``"depth"`` axis. Reader draws nothing from it:
the picture is the PNG and the formulas are listed beneath it, one per line, exactly as for a 2D graph.

``image`` is ``null`` when SciCal had no picture for the entry (an old worksheet entry): Reader then shows an explicit "missing
figure" state with the formulas, instead of pretending the graph was drawn.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

EXTENSION = "slai.scical"
SCHEMA = "scical-graph/1"
MAX_SERIES = 24
MAX_LATEX = 2_000
MAX_TEXT = 500
MAX_SIDE = 8_000
DASHES = frozenset({"solid", "dash", "dot", "dashdot"})
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL.sub("", value).strip()
    return cleaned[:limit] if cleaned else None


def _dimension(value: Any) -> int | None:
    return value if type(value) is int and 1 <= value <= MAX_SIDE else None


def graph_record(block: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The raw ``graph`` record of a block, or ``None`` when the block has none."""
    ext = block.get("extensions")
    if not isinstance(ext, Mapping):
        return None
    mine = ext.get(EXTENSION)
    if not isinstance(mine, Mapping):
        return None
    graph = mine.get("graph")
    return graph if isinstance(graph, Mapping) else None


def graph_view(block: Mapping[str, Any]) -> dict[str, Any] | None:
    """The checked, bounded view of a block's graph record for the client; ``None`` if the block has no record."""
    raw = graph_record(block)
    if raw is None:
        return None
    if raw.get("schema") != SCHEMA:
        return {"error": "This graph was written by a newer or unknown version of SciCal, so its formulas are not shown."}
    series_raw = raw.get("series")
    if not isinstance(series_raw, list) or not series_raw:
        return {"error": "This graph record lists no functions."}
    series: list[dict[str, Any]] = []
    skipped = 0
    for i, item in enumerate(series_raw[:MAX_SERIES]):
        if not isinstance(item, Mapping):
            skipped += 1
            continue
        latex = _text(item.get("latex"), MAX_LATEX)
        expression = _text(item.get("expression"), MAX_TEXT)
        if latex is None and expression is None:
            skipped += 1
            continue
        colour = item.get("color")
        dash = item.get("dash")
        series.append({
            "id": _text(item.get("id"), 64) or f"s{i + 1}",
            "latex": latex,                                     # None -> the client shows `expression` as plain text
            "expression": expression,
            "type": _text(item.get("type"), 32) or "function",
            "color": colour if isinstance(colour, str) and _COLOUR.match(colour) else None,
            "dash": dash if isinstance(dash, str) and dash in DASHES else "solid",
        })
    skipped += max(0, len(series_raw) - MAX_SERIES)
    image = raw.get("image")
    dims = None
    if isinstance(image, Mapping):
        w, h = _dimension(image.get("width")), _dimension(image.get("height"))
        dims = {"width": w, "height": h, "transparent": image.get("transparent") is True} if w and h else {"width": None, "height": None, "transparent": image.get("transparent") is True}
    view: dict[str, Any] = {"id": _text(raw.get("id"), 64), "kind": _text(raw.get("kind"), 8) or "2d", "series": series, "image": dims,
                            "skipped": skipped}
    note = _text(raw.get("note"), MAX_TEXT)
    if note:
        view["note"] = note
    return view


def extra_cost(block: Mapping[str, Any]) -> int:
    """Characters' worth of room a figure takes when a reading page is filled (an image is far taller than its caption)."""
    if block.get("type") != "figure":
        return 0
    graph = graph_record(block)
    lines = len(graph.get("series", [])) if graph is not None and isinstance(graph.get("series"), list) else 0
    return 900 + 110 * min(lines, MAX_SERIES)
