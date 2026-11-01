"""Permanent icon-rail and contextual-drawer regression tests."""
from __future__ import annotations

from harness import E2E, fx


class SidebarRail(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.open(fx.make_pdf(self.docs / "rail.pdf", pages=5))

    def test_all_sections_open_without_rebuilding_the_rail(self) -> None:
        pg = self.page
        self.assertTrue(pg.locator(".side-rail").is_visible())
        for label, section in (
            ("Contents", "outline"),
            ("Pages", "thumbnails"),
            ("Notes", "annotations"),
            ("Search", "search"),
            ("Assistant", "assistant"),
        ):
            self.select_side(label)
            self.assertEqual(pg.locator("#reader-side-title").inner_text(), label)
            self.assertEqual(pg.locator(f"#reader-rail-{section}").get_attribute("aria-expanded"), "true")
            self.assertEqual(pg.locator('.side-rail button[aria-expanded="true"]').count(), 1)

    def test_active_icon_closes_only_the_drawer(self) -> None:
        pg = self.page
        self.select_side("Contents")
        pg.locator("#reader-rail-outline").click()
        self.assertEqual(pg.locator(".side-drawer").get_attribute("aria-hidden"), "true")
        self.assertTrue(pg.locator(".side-rail").is_visible())

    def test_ctrl_b_restores_selected_panel(self) -> None:
        pg = self.page
        self.select_side("Notes")
        pg.locator("#reader-rail-annotations").click()
        pg.locator("#view").focus()
        pg.keyboard.press("Control+b")
        pg.wait_for_function("() => document.querySelector('#reader-rail-annotations').getAttribute('aria-expanded') === 'true'")
        self.assertEqual(pg.locator("#reader-side-title").inner_text(), "Notes")

    def test_ctrl_f_opens_and_focuses_search(self) -> None:
        pg = self.page
        self.select_side("Contents")
        pg.locator("#reader-rail-outline").click()
        pg.locator("#view").focus()
        pg.keyboard.press("Control+f")
        pg.wait_for_function("() => document.activeElement?.getAttribute('aria-label') === 'Search in this document'")

    def test_narrow_viewport_uses_a_drawer_overlay(self) -> None:
        pg = self.page
        pg.set_viewport_size({"width": 620, "height": 720})
        self.select_side("Contents")
        self.assertTrue(pg.locator(".side-rail").is_visible())
        self.assertTrue(pg.locator("#view").is_visible())
        self.assertLessEqual(pg.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0)
