"""Workflow B - mark up text with the real mouse: highlight, underline, strike, note; edit, undo; everything survives a restart."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowB(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.pdf = fx.make_pdf(self.docs / "paper.pdf", pages=4)

    def test_mark_up_and_survive_restart(self):
        pg = self.page
        self.open(self.pdf)

        # highlight in a chosen colour (words 5..9 of the first body line)
        self.assertEqual(self.drag_words(5, 9).replace(" ", ""), "line1:Thequickbrown")
        pg.click(".seltoolspop button[aria-label='Highlight colour #86efac']")
        pg.wait_for_selector(".status >> text=1 annotation")
        hl = self.rows("highlight")
        self.assertEqual(len(hl), 1)
        self.assertEqual(hl[0]["anchor"]["segments"][0]["quote"], "line 1: The quick brown")
        self.assertEqual(hl[0]["style"]["color"], "#86efac")
        expect(pg.locator(".pagehost[data-page='0'] .markslayer > *")).not_to_have_count(0)

        # underline and strike-through with the toolbar tools (the tool stays on, like a marker pen)
        self.click_tool("button[aria-label='Underline text']")
        self.drag_words(12, 14, 0)                                     # a later word run on line 2
        pg.wait_for_selector(".status >> text=2 annotations")
        self.click_tool("button[aria-label='Strike through text']")
        self.drag_words(25, 27, 0)
        pg.wait_for_selector(".status >> text=3 annotations")
        self.click_tool("button[aria-label='Select text']")
        self.assertEqual({r["kind"] for r in self.rows()}, {"highlight", "underline", "strike"})

        # a note on selected text: the selection popover -> Note -> write -> Ctrl+Enter
        self.drag_words(40, 43, 0)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Remember to check this claim against the source.")
        pg.fill(".noteedit input[placeholder^='tag']", "check, claim")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=4 annotations")
        notes = [r for r in self.rows() if r.get("body", {}).get("text")]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["body"]["text"], "Remember to check this claim against the source.")
        self.assertEqual(sorted(notes[0]["body"]["tags"]), ["check", "claim"])
        self.shot("B1_four_annotations")

        # the notes panel lists them in reading order and filters work
        self.toggle_side()
        pg.click("[role=tab]:has-text('Notes')")
        expect(pg.locator(".side .ann")).to_have_count(4)
        pg.fill("input[aria-label='Filter annotations']", "claim")
        expect(pg.locator(".side .ann")).to_have_count(1)
        pg.fill("input[aria-label='Filter annotations']", "")
        self.shot("B2_notes_panel")

        # undo and redo through the keyboard
        self.focus_view()
        pg.keyboard.press("Control+z")
        pg.wait_for_selector(".status >> text=3 annotations")
        pg.keyboard.press("Control+y")
        pg.wait_for_selector(".status >> text=4 annotations")

        # delete one from the panel, then bring it back with undo
        pg.locator(".side .ann").first.locator("button:has-text('Delete')").click()
        pg.wait_for_selector(".status >> text=3 annotations")
        self.focus_view()
        pg.keyboard.press("Control+z")
        pg.wait_for_selector(".status >> text=4 annotations")
        self.assertEqual(len(self.rows()), 4)

        # a hard restart of the service and a fresh browser load: nothing is lost, and marks are drawn where they were
        pg.wait_for_timeout(600)
        self.svc.restart()
        self.reload_app()
        self.open_from_library("Fixture PDF")
        pg.wait_for_selector(".status >> text=4 annotations")
        expect(pg.locator(".pagehost[data-page='0'] .markslayer > *")).not_to_have_count(0)
        self.assertEqual(len(self.rows()), 4)
        self.shot("B3_after_restart")

    def test_overlapping_selection_does_not_duplicate(self):
        pg = self.page
        self.open(self.pdf)
        for _ in range(2):                                              # the same words twice
            self.drag_words(5, 9)
            pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
            pg.wait_for_timeout(500)
            pg.keyboard.press("Escape")
        self.assertEqual(len(self.rows("highlight")), 1, "marking the same words twice must not stack duplicate highlights")

    def test_edit_colour_and_note_text(self):
        pg = self.page
        self.open(self.pdf)
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        self.toggle_side()
        pg.click("[role=tab]:has-text('Notes')")
        pg.click(".side .ann button:has-text('Edit')")
        pg.click(".popover button[aria-label$='#f9a8d4']")
        pg.wait_for_timeout(600)
        self.assertEqual(self.rows("highlight")[0]["style"]["color"], "#f9a8d4")


if __name__ == "__main__":
    unittest.main()
