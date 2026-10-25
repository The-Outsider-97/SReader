"""Reading preferences: one validated, versioned file for the app and a validated override per document.

Nothing in here is trusted: the file may have been edited by hand or damaged, and the values come from a browser. Every field
has a type, a range and a default. Unknown fields are dropped, out-of-range values are refused (when they come from the client) or
replaced with the default (when they come from a damaged file, which is then kept aside as ``prefs.json.corrupt-<time>``).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import PREFS_SCHEMA
from .documents.appearance import PRESETS, contrast_ratio, parse_hex
from .errors import BadRequest
from .files import atomic_write

THEMES = ("light", "dark", "sepia", "contrast", "system")
LAYOUTS = ("continuous", "single", "spread", "focus")
ZOOM_MODES = ("custom", "fit-width", "fit-page")
FONT_FAMILIES = ("serif", "sans", "mono")
ALIGNS = ("start", "justify")
PDF_APPEARANCE = ("match", "original")
SIDEBARS = ("none", "outline", "thumbnails", "annotations", "search", "assistant")

# name -> (default, validator description)
DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "zoomMode": "fit-width",
    "zoom": 100,                     # percent, used when zoomMode == "custom"
    "layout": "continuous",
    "coverAlone": True,              # two-page spread: the first page stands alone, like a book cover
    "fontSize": 18,                  # px, flow documents
    "fontFamily": "serif",
    "lineSpacing": 1.6,
    "columnWidth": 70,               # characters, flow documents
    "textAlign": "start",
    "fontColor": None,               # None = the theme's text colour
    "fontOpacity": 100,              # percent, 30..100
    "pdfAppearance": "match",        # match = recolour PDF text/backgrounds with the theme; original = leave the PDF as drawn
    "sidebar": "none",
    "reduceMotion": False,
    "highlightColors": ["#facc15", "#86efac", "#7dd3fc", "#f9a8d4", "#fdba74"],
    "recentColors": [],
    "tool": {"pen": {"color": "#e11d48", "width": 3}, "pencil": {"color": "#374151", "width": 2}, "highlighter": {"color": "#facc15", "width": 14}},
    "assistantEnabled": False,       # reading never needs it; off until the person turns it on
    "assistantMayReadDocument": False,
}

# Fields a single document may override.
DOC_FIELDS = ("theme", "zoomMode", "zoom", "layout", "coverAlone", "fontSize", "fontFamily", "lineSpacing", "columnWidth", "textAlign",
              "fontColor", "fontOpacity", "pdfAppearance")


def _hex(value: Any, label: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        raise BadRequest(f"{label} must look like #rrggbb")
    if parse_hex(value) is None:
        raise BadRequest(f"{label} must look like #rrggbb")
    return value.lower()


def _num(value: Any, low: float, high: float, label: str, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not low <= value <= high:
        raise BadRequest(f"{label} must be a number between {low:g} and {high:g}")
    return int(round(value)) if integer else round(float(value), 3)


def _choice(value: Any, options: tuple[str, ...], label: str) -> str:
    if value not in options:
        raise BadRequest(f"{label} must be one of {', '.join(options)}")
    return value


def _flag(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BadRequest(f"{label} must be true or false")
    return value


def _palette(value: Any, label: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise BadRequest(f"{label} must be a list of at most {limit} colours")
    out = []
    for item in value:
        c = _hex(item, label)
        if c not in out:
            out.append(c)
    return out


def _tool(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BadRequest("tool must be an object")
    out = {}
    for name, default in DEFAULTS["tool"].items():
        item = value.get(name, default)
        if not isinstance(item, dict):
            raise BadRequest(f"tool.{name} must be an object")
        out[name] = {"color": _hex(item.get("color", default["color"]), f"tool.{name}.color"),
                     "width": _num(item.get("width", default["width"]), 0.5, 64, f"tool.{name}.width")}
    return out


def validate_field(key: str, value: Any) -> Any:
    if key == "theme":
        return _choice(value, THEMES, key)
    if key == "zoomMode":
        return _choice(value, ZOOM_MODES, key)
    if key == "zoom":
        return _num(value, 25, 400, "zoom (percent)", integer=True)
    if key == "layout":
        return _choice(value, LAYOUTS, key)
    if key in ("coverAlone", "reduceMotion", "assistantEnabled", "assistantMayReadDocument"):
        return _flag(value, key)
    if key == "fontSize":
        return _num(value, 10, 48, "fontSize (px)", integer=True)
    if key == "fontFamily":
        return _choice(value, FONT_FAMILIES, key)
    if key == "lineSpacing":
        return _num(value, 1.0, 2.5, key)
    if key == "columnWidth":
        return _num(value, 30, 140, "columnWidth (characters)", integer=True)
    if key == "textAlign":
        return _choice(value, ALIGNS, key)
    if key == "fontColor":
        return _hex(value, key, allow_none=True)
    if key == "fontOpacity":
        return _num(value, 30, 100, "fontOpacity (percent)", integer=True)
    if key == "pdfAppearance":
        return _choice(value, PDF_APPEARANCE, key)
    if key == "sidebar":
        return _choice(value, SIDEBARS, key)
    if key == "highlightColors":
        return _palette(value, key, 12)
    if key == "recentColors":
        return _palette(value, key, 12)
    if key == "tool":
        return _tool(value)
    raise BadRequest(f"Unknown setting: {key}")


def validate(patch: Any, allowed: tuple[str, ...] | None = None) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise BadRequest("Settings must be an object")
    out = {}
    for key, value in patch.items():
        if allowed is not None and key not in allowed:
            raise BadRequest(f"{key} cannot be set for a single document")
        out[key] = validate_field(key, value)
    return out


def _lenient(raw: Any, allowed: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Whatever in a file read from disk is valid; everything else is dropped (the default shows through)."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if allowed is not None and key not in allowed:
            continue
        try:
            out[key] = validate_field(key, value)
        except BadRequest:
            continue
    return out


def effective(global_prefs: dict[str, Any], doc_prefs: dict[str, Any] | None, *, dark_system: bool = False) -> dict[str, Any]:
    """The preferences that apply to one document, plus the colour pair they produce and any readability warnings."""
    merged = {**global_prefs, **(doc_prefs or {})}
    theme = merged["theme"]
    resolved = ("dark" if dark_system else "light") if theme == "system" else theme
    preset = PRESETS[resolved]
    bg = preset["bg"]
    text = preset["text"] or (0, 0, 0)
    warnings: list[str] = []
    fg = parse_hex(merged["fontColor"]) if merged.get("fontColor") else text
    eff = merged["fontOpacity"] / 100
    blended = tuple(round(bg[i] + (fg[i] - bg[i]) * eff) for i in range(3))      # opacity blends the text toward the background
    ratio = contrast_ratio(blended, bg)                                          # type: ignore[arg-type]
    if ratio < 3.0:
        warnings.append(f"This text is hard to read on the {resolved} background (contrast {ratio:.1f}:1; 4.5:1 is recommended).")
    elif ratio < 4.5:
        warnings.append(f"Contrast is {ratio:.1f}:1, below the recommended 4.5:1 for body text.")
    hexed = lambda rgb: "#%02x%02x%02x" % tuple(rgb)                              # noqa: E731
    return {"values": merged, "theme": resolved, "colors": {"bg": hexed(bg), "text": hexed(text), "dark": preset["dark"]},
            "contrast": round(ratio, 2), "warnings": warnings}


class PrefsFile:
    """The global preferences file: loaded once, written atomically on every change."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.values: dict[str, Any] = {}
        self.recovered: str | None = None
        self._load()

    def _load(self) -> None:
        stored: dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("not an object")
                if raw.get("schema", 1) > PREFS_SCHEMA:
                    # written by a newer Reader: use what we understand, never overwrite their file until the person changes something
                    self.recovered = "These settings were saved by a newer SLAI Reader; settings this version does not know are ignored."
                stored = _lenient(raw.get("values", {}))
            except (OSError, ValueError) as exc:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + f".corrupt-{stamp}"))
                except OSError:
                    pass
                self.recovered = f"The settings file was damaged ({str(exc)[:80]}); it was kept aside and defaults are in use."
        self.values = {**json.loads(json.dumps(DEFAULTS)), **stored}

    def get(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.values))

    def update(self, patch: Any) -> dict[str, Any]:
        clean = validate(patch)
        with self.lock:
            self.values.update(clean)
            self._write()
            return self.get()

    def reset(self) -> dict[str, Any]:
        with self.lock:
            self.values = json.loads(json.dumps(DEFAULTS))
            self._write()
            return self.get()

    def _write(self) -> None:
        payload = {"schema": PREFS_SCHEMA, "values": self.values}
        atomic_write(self.path, json.dumps(payload, indent=2).encode("utf-8"))


def clean_doc_prefs(patch: Any) -> dict[str, Any]:
    return validate(patch, DOC_FIELDS)


def lenient_doc_prefs(raw: Any) -> dict[str, Any]:
    return _lenient(raw, DOC_FIELDS)
