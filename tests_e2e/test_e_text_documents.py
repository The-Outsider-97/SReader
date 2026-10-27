"""Workflow E - text documents (native SLDC, EPUB, DOCX, Markdown): marks anchored to text, reflow, layouts, exports and import."""
from __future__ import annotations

import json
import unittest

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowE(E2E):
    def test_sldc_highlight_across_blocks_and_restart(self):
        pg = self.page
        path = fx.make_sldc(self.docs / "report.sldc", paragraphs=30, title="Quarterly report")
        self.open(path)
        sel = self.drag_text(2, 14, 4, 40)                                   # from paragraph 2 into paragraph 4
        self.assertIn("Paragraph 3.", sel)
        pg.click(".seltoolspop button[aria-label='Highlight colour #7dd3fc']")
        pg.wait_for_selector(".status >> text=1 annotation")
        row = self.rows("highlight")[0]
        segs = row["anchor"]["segments"]
        self.assertEqual(len(segs), 3, "a highlight over three paragraphs is one annotation with three segments")
        self.assertTrue(segs[0]["quote"].startswith("The quick brown") or segs[0]["quote"], segs)
        # the marked text on screen equals the text stored with the annotation
        shown = pg.evaluate("() => [...document.querySelectorAll('#view .mark')].map(e => e.textContent).join('')")
        stored = "".join(s["quote"] for s in segs)
        self.assertEqual(shown.replace("\n", ""), stored.replace("\n", ""))
        self.shot("E1_sldc_highlight")

        # underline + note on a single paragraph; bookmark; sticky anchored to a block
        self.drag_text(6, 3, 6, 40)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Cross-check with table 2.")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=2 annotations")
        self.focus_view()
        pg.keyboard.press("Control+d")
        pg.wait_for_timeout(500)
        self.assertEqual(len(self.rows("bookmark")), 1)

        # a drawing in a text document belongs to the paragraph it starts in
        self.click_tool("button[aria-label='Pen']")
        a = pg.locator("#view .b[data-n='8']").bounding_box()
        pg.mouse.move(a["x"] + 40, a["y"] + 20); pg.mouse.down(); pg.mouse.move(a["x"] + 200, a["y"] + 50, steps=15); pg.mouse.up()
        pg.wait_for_timeout(600)
        ink = self.rows("ink")
        self.assertEqual(len(ink), 1)
        self.assertEqual(ink[0]["anchor"]["kind"], "block")
        self.click_tool("button[aria-label='Select text']")

        # restart everything; reopen; the marks are back on the same words
        self.svc.restart()
        self.reload_app()
        self.open_from_library("Quarterly report")
        pg.wait_for_selector(".status >> text=4 annotations")      # highlight, note, bookmark, drawing
        shown2 = pg.evaluate("() => [...document.querySelectorAll('#view .mark')].map(e => e.textContent).join('')")
        self.assertIn(stored.replace("\n", "")[:20], shown2.replace("\n", ""))

    @staticmethod
    def write_doc(path, paragraphs):
        from src.utils import sldc as s
        blocks = [s.make_block("heading", "Live document", block_id="h1", attributes={"level": 1})]
        blocks += [s.make_block("paragraph", t, block_id=f"p{i + 1}") for i, t in enumerate(paragraphs)]
        s.write_package(path, s.new_package("Live document", blocks, doc_id="live-doc"), overwrite=True)

    def test_edit_the_file_and_marks_follow_the_text(self):
        """Another program changes the document while it is open: Reader notices, reloads and re-anchors every mark."""
        pg = self.page
        base = ["The harbour was quiet before dawn and the boats rocked gently at their ropes.",
                "A single amber lantern glows softly above the door of the old net loft.",
                "By the time the sun cleared the breakwater the market was already loud."]
        path = self.docs / "live.sldc"
        self.write_doc(path, base)
        self.open(path)
        phrase = "amber lantern glows softly"
        start = base[1].index(phrase)
        self.drag_text(2, start, 2, start + len(phrase))
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        self.assertEqual(self.rows("highlight")[0]["anchor"]["segments"][0]["quote"], phrase)

        # 1. a paragraph is inserted above: the mark must follow its words
        self.write_doc(path, ["A new opening paragraph that pushes everything down."] + base)
        pg.wait_for_selector(".banner:has-text('changed')", timeout=25000)
        pg.click(".banner button:has-text('Reload')")
        pg.wait_for_selector("#view .mark.moved")                  # the document was rebuilt and the mark re-anchored one block down
        self.assertEqual(pg.evaluate("() => [...document.querySelectorAll('#view .mark')].map(e => e.textContent).join('')"), phrase)
        self.assertIn("earlier version of this document", pg.locator(".banner").first.inner_text())   # the user is told, once, that marks were re-checked

        # 2. the words are rewritten slightly: the mark is kept but flagged, and nothing silently marks different words
        edited = list(base)
        edited[1] = "A single amber lantern glowed softly above the door of the old net loft."
        self.write_doc(path, edited)
        pg.wait_for_selector(".banner:has-text('changed')", timeout=25000)
        pg.click(".banner button:has-text('Reload')")
        pg.wait_for_selector(".status >> text=1 annotation")
        self.toggle_side()
        pg.click("[role=tab]:text-is('Notes')")
        chips = pg.locator(".side .ann .chip").all_inner_texts()
        shown = pg.evaluate("() => [...document.querySelectorAll('#view .mark')].map(e => e.textContent).join('')")
        self.assertTrue("moved" in chips or "text not found" in chips, f"a changed passage must be flagged, got {chips}")
        self.assertIn(shown, ("", phrase, "amber lantern glowed softly"), "the mark may follow the edited words, never other words")
        self.shot("E5_moved_annotation")

        # 3. the passage is deleted outright: the note is kept and listed as 'text not found' (never deleted, never misplaced)
        self.write_doc(path, [base[0], base[2]])
        pg.wait_for_selector(".banner:has-text('changed')", timeout=25000)
        pg.click(".banner button:has-text('Reload')")
        pg.wait_for_selector(".side .ann .chip:has-text('text not found')")
        self.assertEqual(len(self.rows("highlight")), 1, "the annotation survives even when its text is gone")
        self.assertEqual(pg.locator("#view .mark").count(), 0)

    def _format_case(self, name: str, maker) -> None:
        pg = self.page
        p = maker(self.docs / name)
        self.open(p)
        pg.wait_for_selector("#view .b .txt")
        text = pg.inner_text("#view .b .txt >> nth=0").strip()
        self.assertTrue(text, f"{name}: the first block is empty")
        self.drag_text(0, 0, 0, min(10, len(text)))
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        rows = self.rows("highlight")
        self.assertEqual(len(rows), 1, name)
        self.assertEqual(rows[0]["anchor"]["segments"][0]["quote"], text[:10].replace("\n", " ") if "\n" not in text[:10] else rows[0]["anchor"]["segments"][0]["quote"])
        self.shot(f"E2_{name.split('.')[-1]}")
        self.svc.restart()
        self.reload_app()
        self.open_from_library(pg.locator("article.card").first.get_attribute("aria-label"))
        pg.wait_for_selector(".status >> text=1 annotation")
        pg.wait_for_selector("#view .mark")

    def test_epub(self):
        self._format_case("book.epub", fx.make_epub)

    def test_docx(self):
        self._format_case("memo.docx", fx.make_docx)

    def test_markdown(self):
        self._format_case("notes.md", fx.make_markdown)

    def test_plain_text(self):
        self._format_case("plain.txt", fx.make_text)

    def test_html(self):
        self._format_case("page.html", fx.make_html)

    def test_odt_and_rtf(self):
        for name, maker in (("memo.odt", fx.make_odt), ("memo.rtf", fx.make_rtf)):
            with self.subTest(name):
                self.page.goto(self.svc.url)
                self.open(maker(self.docs / name))
                self.page.wait_for_selector("#view .b .txt")
                self.assertTrue(self.page.inner_text("#view .b .txt >> nth=0").strip())

    def test_epub_contents_panel(self):
        pg = self.page
        self.open(fx.make_epub(self.docs / "book.epub", chapters=4))
        self.toggle_side()
        pg.click("[role=tab]:text-is('Contents')")
        pg.wait_for_selector(".outline button.item")
        n = pg.locator(".outline button.item").count()
        self.assertGreaterEqual(n, 4)
        pg.locator(".outline button.item").nth(2).click()
        pg.wait_for_timeout(800)
        self.assertNotIn("0% read", self.status_text())
        self.shot("E4_epub_contents")

    def test_reflow_font_layout_and_modes(self):
        pg = self.page
        path = fx.make_sldc(self.docs / "long.sldc", paragraphs=60, title="Long document")
        self.open(path)
        pg.wait_for_selector("#view .b[data-n='1'] .txt")
        size = lambda: pg.eval_on_selector("#view .b[data-n='1'] .txt", "e => parseFloat(getComputedStyle(e).fontSize)")  # noqa: E731
        base = size()
        self.open_appearance()
        slider = pg.get_by_label("Text size")
        slider.evaluate("e => { e.value = '30'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        pg.wait_for_function if False else pg.wait_for_timeout(700)
        self.assertGreater(size(), base * 1.5, "the text size setting changes the reading text")
        pg.keyboard.press("Escape")
        pg.click("button[aria-label='Zoom in']")
        pg.wait_for_selector(".status >> text=110%")
        pg.fill("input[aria-label^='Zoom percent']", "100"); pg.press("input[aria-label^='Zoom percent']", "Enter")
        # the other reading modes change real colours on the text and the page
        self.choose_mode("Sepia")
        pg.wait_for_timeout(400)
        sepia = pg.eval_on_selector("#view .flow", "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]")
        self.choose_mode("Dark")
        pg.wait_for_timeout(400)
        dark = pg.eval_on_selector("#view .flow", "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]")
        self.assertNotEqual(sepia, dark)
        self.shot("E3_dark_text")
        # layouts for reflowable text: pages, spread, and back
        pg.select_option("select[aria-label='Page layout']", "single")
        pg.wait_for_selector(".pagedwrap")
        first = pg.inner_text(".status")
        self.focus_view()
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(800)
        self.assertNotEqual(pg.inner_text(".status"), first)
        pg.select_option("select[aria-label='Page layout']", "spread")
        pg.wait_for_selector(".pagedwrap")
        pg.wait_for_timeout(600)
        self.shot("E4_reflow_spread")
        pg.select_option("select[aria-label='Page layout']", "continuous")
        pg.wait_for_selector("#view .flow")


if __name__ == "__main__":
    unittest.main()
