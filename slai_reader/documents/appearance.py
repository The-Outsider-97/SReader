"""Reading appearance for fixed-layout pages, applied while drawing and never saved into the document.

Dark, sepia and high-contrast are **semantic recolouring**, not an inverted picture: pdfium draws the page and, before it does,
each page object is given a new colour - text becomes the reading text colour, near-white fills become the reading background,
dark lines and fills become light, mid-tone colours (charts, highlights) keep their hue, and embedded images are never touched.
The PDF file itself is not changed. Pages that are only a scanned image have no text objects, so only the paper colour changes.
"""
from __future__ import annotations

import colorsys
import ctypes
from dataclasses import dataclass
from typing import Any

_HEX = "0123456789abcdefABCDEF"

PRESETS: dict[str, dict[str, Any]] = {
    "light": {"bg": (255, 255, 255), "text": None, "dark": False},
    "sepia": {"bg": (244, 236, 216), "text": (67, 52, 34), "dark": False},
    "dark": {"bg": (22, 27, 34), "text": (221, 225, 231), "dark": True},
    "contrast": {"bg": (0, 0, 0), "text": (255, 255, 255), "dark": True},
}


def parse_hex(value: Any) -> tuple[int, int, int] | None:
    if isinstance(value, str) and len(value) == 7 and value[0] == "#" and all(c in _HEX for c in value[1:]):
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    return None


def luminance(rgb: tuple[int, int, int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    def lin(c: int) -> float:
        x = c / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    la = 0.2126 * lin(a[0]) + 0.7152 * lin(a[1]) + 0.0722 * lin(a[2])
    lb = 0.2126 * lin(b[0]) + 0.7152 * lin(b[1]) + 0.0722 * lin(b[2])
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class PdfAppearance:
    mode: str = "light"
    bg: tuple[int, int, int] = (255, 255, 255)
    text: tuple[int, int, int] | None = None
    alpha: int = 255
    dark: bool = False
    invert: bool = False        # client-side CSS inversion instead of recolouring (the server then draws the page unchanged)

    @property
    def recolours(self) -> bool:
        return not self.invert and (self.mode != "light" or self.text is not None or self.alpha < 255)

    def key(self) -> str:
        return f"{self.mode}:{self.bg}:{self.text}:{self.alpha}:{int(self.invert)}"

    @classmethod
    def from_prefs(cls, appearance: dict[str, Any] | None) -> "PdfAppearance":
        a = appearance or {}
        mode = a.get("mode", "light")
        preset = PRESETS.get(mode)
        if preset is None:                                   # "custom"
            bg = parse_hex(a.get("customBg")) or (255, 255, 255)
            text = parse_hex(a.get("customText"))
            dark = luminance(bg) < 0.5
            mode = "custom"
        else:
            bg, text, dark = preset["bg"], preset["text"], preset["dark"]
        override = parse_hex(a.get("textColor"))
        if override:
            text = override
        alpha = a.get("textOpacity", 1.0)
        alpha = int(round(max(0.3, min(1.0, float(alpha))) * 255)) if isinstance(alpha, (int, float)) else 255
        invert = bool(dark and a.get("pdfDark") == "invert" and mode in ("dark",))
        return cls(mode=mode, bg=bg, text=text, alpha=alpha, dark=dark, invert=invert)


def _recolour_path(rgb: tuple[int, int, int], app: PdfAppearance) -> tuple[int, int, int]:
    r, g, b = rgb
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if app.dark:
        if l > 0.97:
            return app.bg
        if l > 0.65 or l < 0.35:
            l = 1 - l
        r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
        return round(r2 * 255), round(g2 * 255), round(b2 * 255)
    # light paper with a tint (sepia / custom light): white becomes paper, other colours are multiplied by the paper colour
    if l > 0.97:
        return app.bg
    return round(r * app.bg[0] / 255), round(g * app.bg[1] / 255), round(b * app.bg[2] / 255)


def apply_to_page(page: Any, app: PdfAppearance) -> None:
    """Recolour the objects of an in-memory pdfium page. The page must be closed afterwards, never saved."""
    from pypdfium2 import raw
    r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    recolour_paths = app.mode != "light"
    for obj in page.get_objects(max_depth=12):
        kind = obj.type
        if kind == 1:                                            # text
            if app.text is not None:
                raw.FPDFPageObj_SetFillColor(obj.raw, app.text[0], app.text[1], app.text[2], app.alpha)
                raw.FPDFPageObj_SetStrokeColor(obj.raw, app.text[0], app.text[1], app.text[2], app.alpha)
            elif app.alpha < 255 and raw.FPDFPageObj_GetFillColor(obj.raw, r, g, b, a):
                raw.FPDFPageObj_SetFillColor(obj.raw, r.value, g.value, b.value, min(a.value, app.alpha))
        elif kind == 2 and recolour_paths:                       # path (lines, rectangles, vector figures)
            if raw.FPDFPageObj_GetFillColor(obj.raw, r, g, b, a):
                nr = _recolour_path((r.value, g.value, b.value), app)
                raw.FPDFPageObj_SetFillColor(obj.raw, nr[0], nr[1], nr[2], a.value)
            if raw.FPDFPageObj_GetStrokeColor(obj.raw, r, g, b, a):
                nr = _recolour_path((r.value, g.value, b.value), app)
                raw.FPDFPageObj_SetStrokeColor(obj.raw, nr[0], nr[1], nr[2], a.value)
        # images (3), shadings (4) and forms (5, already flattened by max_depth) are left exactly as they are
