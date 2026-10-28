"""Make the browser-tab icons from the SReader logo (development tool; the results are committed in ``assets/``).

The logo itself, ``applications/assets/sreader.png``, is the one canonical file; nothing is redrawn or recoloured. This only scales it
(keeping its transparency) to the sizes browsers ask for:

    assets/sreader.png      the logo as supplied (copied from ../assets/sreader.png when that is present)
    assets/icon-192.png     192 x 192  (bookmarks, home-screen shortcuts)
    assets/favicon-32.png   32 x 32    (the tab)
    assets/favicon.ico      16, 32 and 48 pixels in one file (what a browser asks for at /favicon.ico)

Run:  python tools/make_icons.py        (needs Pillow, which Reader already requires)
Then: node tools/build-static.mjs       (copies assets/ into dist/)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
APP = HERE.parent
CANONICAL = APP.parent / "assets" / "sreader.png"
ASSETS = APP / "assets"


def fitted(logo: Image.Image, side: int) -> Image.Image:
    """The logo scaled to fit a transparent square (it is not stretched; an off-square logo is centred)."""
    scale = side / max(logo.size)
    size = (max(1, round(logo.width * scale)), max(1, round(logo.height * scale)))
    small = logo.resize(size, Image.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(small, ((side - size[0]) // 2, (side - size[1]) // 2), small)
    return canvas


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    target = ASSETS / "sreader.png"
    if CANONICAL.is_file():
        shutil.copyfile(CANONICAL, target)
    if not target.is_file():
        print(f"The SReader logo was not found at {CANONICAL} or {target}", file=sys.stderr)
        return 1
    logo = Image.open(target).convert("RGBA")
    fitted(logo, 192).save(ASSETS / "icon-192.png", optimize=True)
    fitted(logo, 32).save(ASSETS / "favicon-32.png", optimize=True)
    fitted(logo, 48).save(ASSETS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    for name in ("sreader.png", "icon-192.png", "favicon-32.png", "favicon.ico"):
        print(f"{name}: {(ASSETS / name).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
