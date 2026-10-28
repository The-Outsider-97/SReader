"""Scientific figures: a graph written by SciCal is shown as its picture with the plotted functions listed beneath it.

The picture and the functions travel as an ordinary SLDC ``figure`` block with a ``scical-graph/1`` record in its ``slai.scical``
extension. These tests open real SLDC files in a real Reader server and read what the client is sent, so a regression in either the
record, the checks, the asset route or the page icon fails here.
"""
from __future__ import annotations

import io
import re
import sys
import unittest
from pathlib import Path

from apiharness import APP_DIR, Api, ApiBase, fixtures  # noqa: F401

FIVE = ["x", "x^2", "x^3", "\\sin x", "a\\cos x"]
COLOURS = ["#b89b00", "#2f7dd1", "#d1432f", "#118833", "#7a3fb0"]
DASHES = ["solid", "dash", "dot", "dashdot", "solid"]


def five_series():
    return [{"id": f"L{i}", "type": "function", "expression": e.replace("\\", ""), "latex": f"f_{{{i}}}(x) = {e}", "variable": "x",
             "color": COLOURS[i - 1], "dash": DASHES[i - 1]} for i, e in enumerate(FIVE, 1)]


def make_graph_sldc(path: Path, series=None, picture: bool = True, schema: str = "scical-graph/1", image=None, extra_blocks: int = 0) -> Path:
    """A document with one graph exactly as SciCal writes it: a transparent PNG figure and its record (or, without a picture, the notice)."""
    from PIL import Image, ImageDraw
    from src.utils import sldc as s
    series = five_series() if series is None else series
    record = {"schema": schema, "id": "scical.graph.g1", "kind": "2d", "series": series, "image": image if image is not None else ({"width": 400, "height": 250, "transparent": True} if picture else None)}
    blocks = [s.make_block("heading", "Worksheet", block_id="h1", attributes={"level": 1})]
    assets = []
    if picture:
        img = Image.new("RGBA", (400, 250), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for i, c in enumerate(COLOURS):
            draw.line([(10, 240 - 30 * i), (390, 10 + 30 * i)], fill=c, width=3)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        asset = s.make_asset(buf.getvalue())
        assets.append(asset)
        fig = s.make_figure(asset, alt="Graph of x; x^2; x^3; sin(x); a*cos(x)", caption="Graph", align="center", block_id="g1")
        fig["extensions"] = {"slai.scical": {"graph": record}}
        blocks.append(fig)
    else:
        blocks.append(s.make_block("paragraph", "Graph: the picture of this graph was not stored with the worksheet entry, so it cannot be shown.", block_id="g1",
                                   extensions={"slai.scical": {"graph": record}}))
    for i in range(extra_blocks):
        blocks.append(s.make_block("paragraph", f"Paragraph {i + 1}. " + fixtures.LOREM * 3, block_id=f"p{i + 1}"))
    s.write_package(path, s.new_package("Worksheet", blocks, doc_id="graph-fixture", assets=assets), overwrite=True)
    return path


class GraphFigures(ApiBase):
    def blocks(self, path):
        d = self.api.open(path)
        return d, self.api.data(200, self.api.get(f"/api/doc/{d['sid']}/blocks?start=0&limit=50"))["blocks"]

    def test_the_picture_and_every_function_are_sent_in_order_with_their_styles(self):
        d, blocks = self.blocks(self.make("g.sldc", make_graph_sldc))
        fig = next(b for b in blocks if b["type"] == "figure")
        self.assertTrue(fig["figure"]["url"].startswith("asset/"))
        self.assertEqual(fig["figure"]["caption"], "Graph")
        g = fig["graph"]
        self.assertEqual([x["latex"] for x in g["series"]], [f"f_{{{i}}}(x) = {e}" for i, e in enumerate(FIVE, 1)])
        self.assertEqual([x["color"] for x in g["series"]], COLOURS)
        self.assertEqual([x["dash"] for x in g["series"]], DASHES)
        self.assertEqual(g["image"], {"width": 400, "height": 250, "transparent": True})
        self.assertEqual(g["skipped"], 0)

    def test_the_picture_is_served_with_its_transparency_intact(self):
        from PIL import Image
        d, blocks = self.blocks(self.make("g.sldc", make_graph_sldc))
        fig = next(b for b in blocks if b["type"] == "figure")
        status, headers, payload = self.api.request("GET", f"/api/doc/{d['sid']}/{fig['figure']['url']}")
        self.assertEqual(status, 200)
        self.assertEqual({k.lower(): v for k, v in headers.items()}["content-type"], "image/png")
        img = Image.open(io.BytesIO(payload))
        self.assertEqual((img.mode, img.size), ("RGBA", (400, 250)))
        self.assertEqual(img.getpixel((0, 0))[3], 0, "the background must stay transparent")
        self.assertTrue(any(img.getpixel((x, y))[3] == 255 for x in range(0, 400, 7) for y in range(0, 250, 7)), "the lines must be drawn")

    def test_a_graph_without_a_picture_is_an_explicit_missing_state_with_its_functions(self):
        d, blocks = self.blocks(self.make("m.sldc", make_graph_sldc, picture=False))
        b = next(b for b in blocks if b["id"] == "g1")
        self.assertEqual(b["type"], "paragraph")
        self.assertIsNone(b["graph"]["image"])
        self.assertEqual(len(b["graph"]["series"]), 5)
        self.assertIn("not stored", b["text"])

    def test_a_record_from_an_unknown_version_does_not_hide_the_picture(self):
        d, blocks = self.blocks(self.make("v.sldc", make_graph_sldc, schema="scical-graph/9"))
        fig = next(b for b in blocks if b["type"] == "figure")
        self.assertIn("error", fig["graph"])
        self.assertTrue(fig["figure"]["url"])

    def test_a_hostile_record_is_checked_and_bounded(self):
        hostile = [{"id": "A" * 500, "latex": "x\x00y\x07", "expression": "e" * 5000, "color": "red;background:url(//evil)", "dash": "javascript:1", "type": "t" * 99},
                   "not an object", None, 5, {"latex": None, "expression": None}, {"latex": "<img src=x onerror=alert(1)>", "color": "#ABCDEF", "dash": "dot"}]
        hostile += [{"latex": f"y_{{{i}}}", "expression": f"y{i}"} for i in range(60)]
        d, blocks = self.blocks(self.make("h.sldc", make_graph_sldc, series=hostile, image={"width": True, "height": "400", "transparent": "yes"}))
        g = next(b for b in blocks if b["type"] == "figure")["graph"]
        self.assertEqual(len(g["series"]), 24 - 4)                                       # 24 read, 4 of them unusable
        self.assertEqual(g["skipped"], 4 + (len(hostile) - 24))
        first = g["series"][0]
        self.assertLessEqual(len(first["id"]), 64)
        self.assertEqual(first["latex"], "xy")                                           # control characters are removed
        self.assertLessEqual(len(first["expression"]), 500)
        self.assertIsNone(first["color"])                                                # a colour is #rrggbb or nothing: it is never put in a style as typed
        self.assertEqual(first["dash"], "solid")
        xss = next(x for x in g["series"] if x["color"] == "#ABCDEF")
        self.assertEqual(xss["latex"], "<img src=x onerror=alert(1)>")                   # sent as data; the client puts it in the page as text only
        self.assertEqual(g["image"], {"width": None, "height": None, "transparent": False})

    def test_a_figure_takes_more_room_when_pages_are_filled(self):
        plain = self.make("p.sldc", fixtures.make_sldc, paragraphs=3, with_table=False, with_note=False)
        d1 = self.api.open(plain)
        d2 = self.api.open(self.make("g.sldc", make_graph_sldc, extra_blocks=3))
        a = self.api.app.sessions.get(d1["sid"]).doc
        b = self.api.app.sessions.get(d2["sid"]).doc
        from slai_reader.documents import figures
        fig = next(x for x in b.blocks if x["type"] == "figure")
        self.assertEqual(figures.extra_cost(fig), 900 + 110 * 5)
        self.assertEqual(figures.extra_cost(next(x for x in a.blocks if x["type"] == "paragraph")), 0)


class TabIcon(ApiBase):
    def test_the_page_names_the_sreader_icon(self):
        status, headers, payload = self.api.request("GET", "/")
        self.assertEqual(status, 200)
        html = payload.decode("utf-8")
        self.assertNotIn('href="data:,"', html, "a blank data: icon hides the logo")
        links = re.findall(r'<link rel="(icon|shortcut icon|apple-touch-icon)"[^>]*href="([^"]+)"', html)
        self.assertTrue(links)
        for _, href in links:
            status, headers, payload = self.api.request("GET", "/" + href)
            self.assertEqual(status, 200, href)
            self.assertTrue({k.lower(): v for k, v in headers.items()}["content-type"].startswith("image/"), href)
            self.assertTrue(payload.startswith((b"\x89PNG", b"\x00\x00\x01\x00")), href)

    def test_a_browser_that_asks_for_favicon_ico_gets_the_icon_and_logo_is_the_supplied_file(self):
        status, headers, payload = self.api.request("GET", "/favicon.ico")
        self.assertEqual(status, 200)
        self.assertEqual(payload[:4], b"\x00\x00\x01\x00")
        logo = APP_DIR.parent / "assets" / "sreader.png"
        if logo.is_file():
            status, _, served = self.api.request("GET", "/assets/sreader.png")
            self.assertEqual((status, served), (200, logo.read_bytes()), "the logo is the supplied file, not a redrawn one")

    def test_icon_sizes_keep_the_logo_transparency(self):
        from PIL import Image
        for name, size in (("icon-192.png", (192, 192)), ("favicon-32.png", (32, 32))):
            img = Image.open(APP_DIR / "dist" / "assets" / name)
            self.assertEqual((img.mode, img.size), ("RGBA", size), name)
            self.assertEqual(img.getpixel((0, 0))[3], 0, f"{name}: the corner must stay transparent")


class FromSciCal(ApiBase):
    """The real chain: SciCal's own export of a worksheet, opened in Reader."""

    def test_scical_worksheet_graph_opens_with_five_formulas(self):
        scical = APP_DIR.parent / "scical"
        if not (scical / "scical" / "services" / "figures.py").is_file():
            self.skipTest("SciCal was not found next to Reader")
        sys.path.insert(0, str(scical))
        try:
            from scical.services import exports, sessions
            from src.utils import sldc
            import importlib.util
            spec = importlib.util.spec_from_file_location("scical_test_figures", scical / "tests" / "test_figures.py")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            five_function_entry = helper.five_function_entry
            doc = {"id": "w1", "name": "Five functions", "entries": [sessions.clean_entry(five_function_entry(), 0)], "settings": {"angle": "rad"}}
            path = self.dir / "from-scical.sldc"
            path.write_bytes(exports.to_sldc(doc, sldc))
        finally:
            sys.path.remove(str(scical))
        d = self.api.open(path)
        blocks = self.api.data(200, self.api.get(f"/api/doc/{d['sid']}/blocks?start=0&limit=20"))["blocks"]
        fig = next(b for b in blocks if b["type"] == "figure")
        self.assertEqual([s["expression"] for s in fig["graph"]["series"]], ["x", "x^2", "x^3", "sin(x)", "a*cos(x)"])
        self.assertTrue(all(s["latex"] for s in fig["graph"]["series"]))
        self.assertEqual([s["dash"] for s in fig["graph"]["series"]], DASHES)
        self.assertFalse([b for b in blocks if b["type"] == "code" and b["text"].startswith("graph:")], "the graph must not be text")


if __name__ == "__main__":
    unittest.main()
