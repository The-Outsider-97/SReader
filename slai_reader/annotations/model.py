"""The annotation model: one record shape for every kind of mark, validated and bounded before it is stored.

``kind``       highlight | underline | strike | note | sticky | ink | shape | bookmark
``anchor``     where it is attached (see ``anchors.py``): text segments, a block, or a page. Never screen pixels.
``style``      colour, opacity, line width, tool
``body``       title, text, tags (typed notes and bookmarks)
``geometry``   ink: one stroke ``{"points": [[x, y, pressure], ...]}``; shape: ``{"shape": "rect|ellipse|line|arrow", "x0", "y0", "x1", "y1"}``
               Coordinates are fractions of the host (the displayed page, or the block's box for flow documents), so they scale with
               zoom and never depend on the window.

One annotation is one stroke, so erasing a stroke, undoing it and moving it are all the same operation as for any other
annotation. Strokes that were drawn together share a ``group`` id so the sidebar can show them as one piece of handwriting.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..errors import BadRequest

KINDS = ("highlight", "underline", "strike", "note", "sticky", "ink", "shape", "bookmark")
TEXT_KINDS = ("highlight", "underline", "strike")
ANCHOR_KINDS = ("text", "pdf-text", "block", "page")
INK_TOOLS = ("pen", "pencil", "highlighter")
SHAPES = ("rect", "ellipse", "line", "arrow")
MAX_POINTS = 20_000
MAX_BYTES = 512 * 1024
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

DEFAULT_STYLE = {
    "highlight": {"color": "#facc15", "opacity": 0.45},
    "underline": {"color": "#ef4444", "opacity": 1.0, "width": 2},
    "strike": {"color": "#ef4444", "opacity": 1.0, "width": 2},
    "note": {"color": "#fde047", "opacity": 1.0},
    "sticky": {"color": "#fde047", "opacity": 1.0},
    "ink": {"color": "#e11d48", "opacity": 1.0, "width": 3, "tool": "pen"},
    "shape": {"color": "#e11d48", "opacity": 1.0, "width": 3},
    "bookmark": {"color": "#6366f1", "opacity": 1.0},
}


def new_id() -> str:
    return "a" + uuid.uuid4().hex[:20]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _num(value: Any, low: float, high: float, label: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise BadRequest(f"{label} must be a number between {low} and {high}")
    return float(value)


def clean_style(kind: str, style: Any) -> dict[str, Any]:
    base = dict(DEFAULT_STYLE[kind])
    if style is None:
        return base
    if not isinstance(style, dict):
        raise BadRequest("style must be an object")
    out = dict(base)
    if "color" in style:
        if not isinstance(style["color"], str) or not _COLOR.match(style["color"]):
            raise BadRequest("style.color must look like #rrggbb")
        out["color"] = style["color"].lower()
    if "opacity" in style:
        out["opacity"] = round(_num(style["opacity"], 0.05, 1.0, "style.opacity"), 3)
    if "width" in style:
        out["width"] = round(_num(style["width"], 0.5, 64, "style.width"), 2)
    if "tool" in style:
        if kind != "ink" or style["tool"] not in INK_TOOLS:
            raise BadRequest(f"style.tool must be one of {INK_TOOLS} (ink only)")
        out["tool"] = style["tool"]
    if "fill" in style and kind == "shape":
        if style["fill"] is not None and (not isinstance(style["fill"], str) or not _COLOR.match(style["fill"])):
            raise BadRequest("style.fill must look like #rrggbb")
        out["fill"] = style["fill"]
    return out


def clean_body(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise BadRequest("body must be an object")
    out: dict[str, Any] = {}
    for key, limit in (("title", 200), ("text", 20_000)):
        if key in body and body[key] is not None:
            if not isinstance(body[key], str) or len(body[key]) > limit:
                raise BadRequest(f"body.{key} must be text of at most {limit} characters")
            out[key] = body[key]
    if "tags" in body:
        tags = body["tags"]
        if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(t, str) and 0 < len(t.strip()) <= 40 for t in tags):
            raise BadRequest("body.tags must be up to 20 short labels")
        unique: dict[str, str] = {}
        for t in tags:
            unique.setdefault(t.strip().casefold(), t.strip())          # one tag per spelling ignoring case; the first spelling is kept
        out["tags"] = sorted(unique.values(), key=str.casefold)
    return out


def clean_geometry(kind: str, geometry: Any, host: str) -> dict[str, Any] | None:
    """``host`` is "page" or "block": which coordinate frame the numbers are fractions of."""
    if kind == "ink":
        if not isinstance(geometry, dict) or not isinstance(geometry.get("points"), list):
            raise BadRequest("An ink stroke needs geometry.points")
        pts = geometry["points"]
        if not 1 <= len(pts) <= MAX_POINTS:
            raise BadRequest(f"A stroke has between 1 and {MAX_POINTS} points")
        ylow, yhigh = (-1.0, 3.0) if host == "block" else (-0.5, 1.5)
        out = []
        for p in pts:
            if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
                raise BadRequest("Each point is [x, y] or [x, y, pressure]")
            x = _num(p[0], -0.5, 1.5, "point x")
            y = _num(p[1], ylow, yhigh, "point y")
            pr = _num(p[2], 0.0, 1.0, "pressure") if len(p) == 3 else 0.5
            out.append([round(x, 5), round(y, 5), round(pr, 3)])
        return {"points": out}
    if kind == "shape":
        if not isinstance(geometry, dict) or geometry.get("shape") not in SHAPES:
            raise BadRequest(f"geometry.shape must be one of {SHAPES}")
        g = {"shape": geometry["shape"]}
        for key in ("x0", "y0", "x1", "y1"):
            g[key] = round(_num(geometry.get(key), -1.0, 3.0, f"geometry.{key}"), 5)
        return g
    return None


def validate_new(raw: dict[str, Any]) -> dict[str, Any]:
    """The part of an annotation that comes from the client. Identity, times, revision and the anchor are added by the service."""
    kind = raw.get("kind")
    if kind not in KINDS:
        raise BadRequest(f"kind must be one of {', '.join(KINDS)}")
    out: dict[str, Any] = {"kind": kind, "style": clean_style(kind, raw.get("style")), "body": clean_body(raw.get("body"))}
    group = raw.get("group")
    if group is not None:
        if not isinstance(group, str) or not _ID.match(group):
            raise BadRequest("group must be an identifier")
        out["group"] = group
    if kind in ("note", "sticky", "bookmark") and not (out["body"].get("text") or out["body"].get("title")) and kind == "note":
        raise BadRequest("A note needs some text")
    layout = raw.get("layout")
    if layout is not None:
        if not isinstance(layout, dict) or len(str(layout)) > 2000:
            raise BadRequest("layout must be a small object")
        out["layout"] = {k: v for k, v in layout.items() if isinstance(k, str) and isinstance(v, (int, float, str, bool))}
    return out


def check_size(ann: dict[str, Any]) -> None:
    import json
    if len(json.dumps(ann, separators=(",", ":"))) > MAX_BYTES:
        raise BadRequest("That annotation is too large")
