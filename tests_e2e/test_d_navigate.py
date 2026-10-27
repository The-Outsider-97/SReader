"""Workflow D - find your way around: contents, thumbnails, go-to, search (document, notes, whole library), progress, bookmarks."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowD(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.pdf = fx.make_pdf(self.docs / "manual.pdf", pages=12)
        self.open(self.pdf)

    def side(self, tab: str) -> None:
        pg = self.page
        if not pg.locator(".side").is_visible():
            self.toggle_side()
        pg.click(f"[role=tab]:text-is('{tab}')")

    def test_contents_pages_and_goto(self):
        pg = self.page
        self.side("Contents")
        expect(pg.locator(".outline button.item")).to_have_count(12)
        pg.click(".outline button.item >> text='Page 7'")
        pg.wait_for_selector(".status >> text=Page 7 of 12")
        expect(pg.locator(".outline li.current")).to_have_text("Page 7")
        self.side("Pages")
        expect(pg.locator(".thumbs button")).to_have_count(12)
        pg.click(".thumbs button[aria-label='Go to page 11']")
        pg.wait_for_selector(".status >> text=Page 11 of 12")
        expect(pg.locator(".thumbs button[aria-current='true']")).to_have_attribute("aria-label", "Go to page 11")
        self.shot("D1_pages_panel")

        # page box: a bad number is refused politely, a good one jumps
        pg.fill("input[aria-label='Go to page']", "99"); pg.press("input[aria-label='Go to page']", "Enter")
        pg.wait_for_selector("[role=status]:has-text('Type a page between 1 and 12')")
        pg.fill("input[aria-label='Go to page']", "2"); pg.press("input[aria-label='Go to page']", "Enter")
        pg.wait_for_selector(".status >> text=Page 2 of 12")

        # the progress bar seeks
        bar = pg.locator("input[aria-label='Reading progress']")
        bar.focus()
        bar.evaluate("e => { e.value = '1000'; e.dispatchEvent(new Event('change')); }")
        pg.wait_for_selector(".status >> text=Page 12 of 12")
        # in-link: the first page has a link to page 2 in its heading area
        self.focus_view()
        pg.keyboard.press("Control+Home")
        pg.wait_for_selector(".status >> text=Page 1 of 12")
        pg.locator(".pagehost[data-page='0'] .linklayer a, .pagehost[data-page='0'] .plink").first.click()
        pg.wait_for_selector(".status >> text=Page 2 of 12")

    def test_search_document_notes_and_library(self):
        pg = self.page
        self.side("Search")
        box = pg.locator("input[aria-label='Search in this document']")
        box.fill("Page 9 heading")
        pg.wait_for_selector("#search-status:has-text('1 of 1 match')")
        expect(pg.locator("#search-hits li")).to_have_count(1)
        pg.wait_for_selector(".pagehost[data-page='8'] .markslayer .m.hit")           # hit drawn on the page where it is
        box.fill("quick brown")
        pg.wait_for_selector("#search-status:has-text('matches')")
        status = pg.inner_text("#search-status")
        self.assertRegex(status, r"1 of \d+\+? matches")
        n = len(pg.locator("#search-hits li").all())
        self.assertGreater(n, 12 * 12 - 1, "every one of the 12 lines on each of 12 pages should match (or the list is capped and says so)")
        box.press("Enter")
        pg.wait_for_selector("#search-status:has-text('2 of')")
        box.fill("zzyzx not in the book")
        pg.wait_for_selector("#search-status:has-text('No matches')")

        # case sensitivity
        box.fill("PAGE 3 HEADING")
        pg.wait_for_selector("#search-status:has-text('1 of 1')")
        pg.check("label:has-text('Match case') >> xpath=../input") if pg.locator("label:has-text('Match case') >> xpath=../input").count() else pg.click("label:has-text('Match case')")
        pg.wait_for_selector("#search-status:has-text('No matches')")
        self.shot("D2_search")

        # a note's own text is searched too, and so is the whole library
        self.focus_view()
        pg.keyboard.press("Control+Home")
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Zebrafish protocol reminder")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=1 annotation")
        self.side("Search")
        pg.fill("input[aria-label='Search in this document']", "zebrafish")
        pg.wait_for_selector("#search-notes >> text=Zebrafish protocol reminder")
        pg.click(".toolbar button[aria-label='Back to library']")
        pg.fill("input[aria-label^='Search titles, notes']", "zebrafish")
        pg.wait_for_selector("text=Zebrafish protocol reminder")
        self.shot("D3_library_search")

    def test_bookmarks_listed_and_followed(self):
        pg = self.page
        for page in (3, 8):
            pg.fill("input[aria-label='Go to page']", str(page)); pg.press("input[aria-label='Go to page']", "Enter")
            pg.wait_for_selector(f".status >> text=Page {page} of 12")
            self.focus_view()
            pg.keyboard.press("Control+d")
            pg.wait_for_timeout(500)
        self.assertEqual(len(self.rows("bookmark")), 2)
        self.side("Notes")
        pg.click(".filters button:has-text('Bookmarks')")
        expect(pg.locator(".side .ann")).to_have_count(2)
        pg.locator(".side .ann").first.locator("button:has-text('Go to')").click()
        pg.wait_for_selector(".status >> text=Page 3 of 12")
        self.shot("D4_bookmarks")


if __name__ == "__main__":
    unittest.main()
