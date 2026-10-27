"""Workflow A - open a real PDF, read it, change how it looks, close it and pick up where you stopped."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowA(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.pdf = fx.make_pdf(self.docs / "paper.pdf", pages=8)

    def test_open_zoom_theme_resume(self):
        pg = self.page
        self.open(self.pdf)
        self.assertIn("Page 1 of 8", self.status_text())
        self.shot("A1_pdf_open")

        # zoom by typing, by button and by fit modes; the number on screen is the real scale
        pg.fill("input[aria-label^='Zoom percent']", "200")
        pg.press("input[aria-label^='Zoom percent']", "Enter")
        pg.wait_for_selector(".status >> text=200%")
        width200 = pg.eval_on_selector(".pagehost[data-page='0']", "e => e.getBoundingClientRect().width")
        pg.click("button:has-text('Fit width')")
        pg.wait_for_selector(".status span.num:last-child:not(:has-text('200%'))")
        fit = pg.eval_on_selector(".pagehost[data-page='0']", "e => e.getBoundingClientRect().width")
        view = pg.eval_on_selector(".main", "e => e.clientWidth")
        self.assertLess(fit, view, "fit-width must fit inside the reading area")
        self.assertGreater(fit, view - 90, "fit-width should use (nearly) the whole width")
        self.assertGreater(width200, fit * 1.1 if fit < 900 else 1)
        for key, label in (("Control+0", "100%"),):
            pg.keyboard.press(key)
            pg.wait_for_selector(f".status >> text={label}")
        width100 = pg.eval_on_selector(".pagehost[data-page='0']", "e => e.getBoundingClientRect().width")
        self.assertAlmostEqual(width100 / 612, 96 / 72, delta=0.02)        # 100% = 96 CSS px per inch of paper

        # bounds: 25 % to 400 %
        pg.fill("input[aria-label^='Zoom percent']", "9999"); pg.press("input[aria-label^='Zoom percent']", "Enter")
        pg.wait_for_selector(".status >> text=400%")
        pg.fill("input[aria-label^='Zoom percent']", "1"); pg.press("input[aria-label^='Zoom percent']", "Enter")
        pg.wait_for_selector(".status >> text=25%")
        pg.click("button:has-text('Fit width')")

        # reading modes are real colour schemes, not inversions: check what the page and the app actually paint
        seen = {}
        for name in ("Light", "Sepia", "Dark", "High contrast"):
            self.choose_mode(name)
            pg.wait_for_timeout(350)
            seen[name] = pg.evaluate("() => [document.documentElement.dataset.theme, getComputedStyle(document.body).backgroundColor, getComputedStyle(document.querySelector('.main')).backgroundColor]")
            if name in ("Light", "Dark"):
                self.shot(f"A2_theme_{name.lower().replace(' ', '_')}")
        self.assertEqual(len({v[0] for v in seen.values()}), 4, seen)
        self.assertEqual(len({v[1] for v in seen.values()}), 4, f"each mode paints its own background: {seen}")
        self.choose_mode("Light")

        # go to page 5 with the page box, then leave and come back: the position is remembered
        pg.fill("input[aria-label='Go to page']", "5"); pg.press("input[aria-label='Go to page']", "Enter")
        pg.wait_for_selector(".status >> text=Page 5 of 8")
        pg.wait_for_timeout(1500)                                              # the position is saved a moment after you stop scrolling
        pg.click(".toolbar button[aria-label='Back to library']")
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")
        self.shot("A3_library_with_recent")
        self.open_from_library("Fixture PDF")
        pg.wait_for_selector(".status >> text=Page 5 of 8", timeout=15000)

        # and after a full browser reload
        self.reload_app()
        self.open_from_library("Fixture PDF")
        pg.wait_for_selector(".status >> text=Page 5 of 8", timeout=15000)

    def test_keyboard_paging_and_layouts(self):
        pg = self.page
        self.open(self.pdf)
        pg.select_option("select[aria-label='Page layout']", "single")
        expect(pg.locator(".pagehost")).to_have_count(1)
        pg.keyboard.press("ArrowRight")
        pg.wait_for_selector(".status >> text=Page 2 of 8")
        pg.select_option("select[aria-label='Page layout']", "spread")
        pg.wait_for_selector(".status >> text=Pages 2")
        expect(pg.locator(".pagehost")).to_have_count(2)
        self.shot("A4_two_page_spread")
        self.focus_view()
        pg.keyboard.press("Home")
        pg.wait_for_selector(".status >> text=Page 1 of 8")              # the cover stands alone, on the right, facing an empty left page
        expect(pg.locator(".pagehost:not(.cover-slot)")).to_have_count(1)
        expect(pg.locator(".pagehost.cover-slot")).to_have_count(1)
        pg.select_option("select[aria-label='Page layout']", "continuous")
        pg.wait_for_selector(".status >> text=Page 1 of 8")
        pg.keyboard.press("F9")                                          # distraction-free
        pg.wait_for_selector(".app.focus-mode")
        self.assertFalse(pg.locator(".toolbar").is_visible())
        pg.keyboard.press("Escape")
        pg.wait_for_selector(".toolbar")


if __name__ == "__main__":
    unittest.main()
