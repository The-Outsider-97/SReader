"""Workflow C - draw with pen, pencil and marker; erase, select-and-move, undo; sticky notes and bookmarks; all stored as page fractions."""
from __future__ import annotations

import math
import unittest

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowC(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.pdf = fx.make_pdf(self.docs / "sketch.pdf", pages=3)
        self.open(self.pdf)
        self.host = self.page.locator(".pagehost[data-page='0']")

    def host_box(self):
        return self.host.bounding_box()

    def draw(self, fx_: float, y: float, length: float = 240, amp: float = 30, steps: int = 30) -> None:
        """A wavy stroke with the real mouse; (fx_, y) is the start as a fraction of the page width and a pixel offset from its top."""
        pg = self.page
        b = self.host_box()
        x0, y0 = b["x"] + b["width"] * fx_, b["y"] + y
        pg.mouse.move(x0, y0)
        pg.mouse.down()
        for i in range(1, steps):
            pg.mouse.move(x0 + length * i / steps, y0 + amp * math.sin(i / 3))
        pg.mouse.up()
        pg.wait_for_timeout(500)

    def ink(self):
        return self.rows("ink")

    @staticmethod
    def bbox(row):
        pts = row["geometry"]["points"]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def test_pen_pencil_marker_eraser_move_undo(self):
        pg = self.page
        for tool, y in (("Pen", 160), ("Pencil", 260), ("Marker", 360)):
            pg.click(f".toolbar button[aria-label='{tool if tool != 'Marker' else 'Highlighter pen'}']")
            self.draw(0.2, y)
        rows = self.ink()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["style"]["tool"] for r in rows}, {"pen", "pencil", "highlighter"})
        for r in rows:
            self.assertEqual(r["anchor"], {"kind": "page", "page": 0})
            for x, y, *_ in r["geometry"]["points"]:
                self.assertTrue(0 <= x <= 1 and 0 <= y <= 1, "ink is stored as fractions of the page, never as pixels")
        self.assertLess([r for r in rows if r["style"]["tool"] == "highlighter"][0]["style"]["opacity"], 1)
        self.shot("C1_three_tools")

        # ink stays on the same piece of paper when the page is zoomed (compare the stroke's place relative to the page)
        def rel() -> float:
            return pg.evaluate("""() => { const h = document.querySelector('.pagehost[data-page="0"]').getBoundingClientRect();
                const s = document.querySelector('.pagehost[data-page="0"] svg.ink path.stroke').getBoundingClientRect();
                return (s.left - h.left) / h.width; }""")
        before = rel()
        pg.fill("input[aria-label^='Zoom percent']", "250"); pg.press("input[aria-label^='Zoom percent']", "Enter")
        pg.wait_for_selector(".status >> text=250%")
        self.assertAlmostEqual(rel(), before, delta=0.01)
        pg.click("button:has-text('Fit width')")
        pg.wait_for_selector(".status span.num:last-child:not(:has-text('250%'))")

        # erase the pen stroke (whole-stroke eraser), then undo
        pg.click(".toolbar button[aria-label^='Eraser']")
        b = self.host_box()
        pen = [r for r in rows if r["style"]["tool"] == "pen"][0]
        x0, y0, x1, y1 = self.bbox(pen)
        pg.mouse.move(b["x"] + b["width"] * (x0 + x1) / 2 - 40, b["y"] + b["height"] * (y0 + y1) / 2 - 60)
        pg.mouse.down()
        pg.mouse.move(b["x"] + b["width"] * (x0 + x1) / 2 + 40, b["y"] + b["height"] * (y0 + y1) / 2 + 60, steps=25)
        pg.mouse.up()
        pg.wait_for_timeout(600)
        self.assertEqual(len(self.ink()), 2)
        self.focus_view()
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(600)
        self.assertEqual(len(self.ink()), 3)

        # select and move the pencil stroke 120 px right and 80 px down
        pencil = [r for r in rows if r["style"]["tool"] == "pencil"][0]
        ox0, oy0, ox1, oy1 = self.bbox(pencil)
        pg.click(".toolbar button[aria-label^='Select and move']")
        b = self.host_box()
        # grab the stroke at the middle of its path
        mid = pencil["geometry"]["points"][len(pencil["geometry"]["points"]) // 2]
        gx, gy = b["x"] + b["width"] * mid[0], b["y"] + b["height"] * mid[1]
        pg.mouse.move(gx, gy)
        pg.mouse.down()
        pg.mouse.move(gx + 120, gy + 80, steps=12)
        pg.mouse.up()
        pg.wait_for_timeout(700)
        moved = [r for r in self.ink() if r["style"]["tool"] == "pencil"][0]
        nx0, ny0, nx1, ny1 = self.bbox(moved)
        self.assertAlmostEqual((nx0 - ox0) * b["width"], 120, delta=3)
        self.assertAlmostEqual((ny0 - oy0) * b["height"], 80, delta=3)
        self.assertAlmostEqual((nx1 - nx0), (ox1 - ox0), delta=1e-3, msg="moving must not distort the stroke")
        self.focus_view()
        pg.keyboard.press("Control+z")
        pg.wait_for_timeout(600)
        back = [r for r in self.ink() if r["style"]["tool"] == "pencil"][0]
        self.assertAlmostEqual(self.bbox(back)[0], ox0, delta=1e-3)

        # a move cannot push a drawing off the page
        pg.keyboard.press("Escape")
        pg.click(".toolbar button[aria-label^='Select and move']")
        b = self.host_box()
        pg.mouse.move(gx, gy); pg.mouse.down(); pg.mouse.move(gx + 5000, gy, steps=10); pg.mouse.up()
        pg.wait_for_timeout(600)
        edge = [r for r in self.ink() if r["style"]["tool"] == "pencil"][0]
        self.assertLessEqual(self.bbox(edge)[2], 1.0001)

        # the notes panel counts drawings
        pg.click(".toolbar button[aria-label='Select text']")
        pg.click("button[aria-label='Show or hide the side panel']")
        pg.click("[role=tab]:has-text('Notes')")
        pg.click(".filters button:has-text('Drawings')")
        expect(pg.locator(".side .ann")).to_have_count(1)                  # strokes drawn together are listed as one piece of handwriting
        expect(pg.locator(".side .ann strong")).to_have_text("Drawing (3 strokes)")
        self.shot("C2_drawings_listed")

    def test_sticky_note_and_bookmark(self):
        pg = self.page
        pg.click(".toolbar button[aria-label^='Place a sticky note']")
        b = self.host_box()
        pg.mouse.click(b["x"] + b["width"] * 0.8, b["y"] + 300)
        pg.fill("textarea[aria-label='Sticky note text']", "Ask the author about this figure.")
        pg.click(".popover button:has-text('Add note')")
        pg.wait_for_selector(".pagehost[data-page='0'] .stickypin")
        st = self.rows("sticky")
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["body"]["text"], "Ask the author about this figure.")
        self.assertAlmostEqual(st[0]["anchor"]["x"], 0.8, delta=0.01)
        self.assertAlmostEqual(st[0]["anchor"]["y"] * b["height"], 300, delta=3)
        self.shot("C3_sticky_note")

        # reopen the pin to edit it, then bookmark this place from the keyboard
        pg.click(".pagehost[data-page='0'] .stickypin")
        pg.wait_for_selector(".popover textarea")
        pg.fill(".popover textarea", "Ask the author about figure 2.")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        self.assertEqual(self.rows("sticky")[0]["body"]["text"], "Ask the author about figure 2.")
        self.focus_view()
        pg.keyboard.press("Control+d")
        pg.wait_for_timeout(600)
        self.assertEqual(len(self.rows("bookmark")), 1)
        pg.keyboard.press("Control+d")                                   # toggles off: no duplicate bookmark for the same place
        pg.wait_for_timeout(600)
        self.assertEqual(len(self.rows("bookmark")), 0)

        # restart: the sticky is still there
        self.svc.restart()
        self.reload_app()
        self.open_from_library("Fixture PDF")
        pg.wait_for_selector(".pagehost[data-page='0'] .stickypin")


if __name__ == "__main__":
    unittest.main()
