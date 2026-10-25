"""Edge cases in a real browser: characters outside the BMP, right-to-left text, and PDFs whose pages differ in size and rotation."""
from __future__ import annotations

import unittest

from harness import E2E, fx


def write_sldc(path, paragraphs, title="Edge document"):
    from src.utils import sldc as s
    blocks = [s.make_block("heading", title, block_id="h1", attributes={"level": 1})]
    blocks += [s.make_block("paragraph", t, block_id=f"p{i + 1}") for i, t in enumerate(paragraphs)]
    s.write_package(path, s.new_package(title, blocks, doc_id="edge-doc"), overwrite=True)
    return path


class EdgeCases(E2E):
    def marked_text(self) -> str:
        return self.page.evaluate("() => [...document.querySelectorAll('#view .mark')].map(e => e.textContent).join('')")

    def test_astral_characters_do_not_shift_the_highlight(self):
        """Python counts code points, JavaScript counts UTF-16 units. A highlight after emoji and mathematical letters must land on the words."""
        pg = self.page
        p1 = "Start \U0001F600\U0001F600 then the quick brown fox jumps over the lazy dog near the river bank in the early morning."
        p2 = "\U0001D4B3\U0001D4B4\U0001D4B5 equations are written with script letters and then explained in plain words afterwards."
        self.open(write_sldc(self.docs / "astral.sldc", [p1, p2]))

        sel = self.drag_text(1, 11, 1, 25)                                   # UTF-16 offsets: "then the quick"
        self.assertEqual(sel.strip(), "then the quick")
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        seg = self.rows("highlight")[0]["anchor"]["segments"][0]
        self.assertEqual(seg["quote"], "then the quick")
        self.assertEqual((seg["start"], seg["end"]), (9, 23), "stored in code points, not UTF-16 units")
        self.assertEqual(self.marked_text(), "then the quick")

        sel = self.drag_text(2, 7, 2, 16)                                    # "equations" after three 2-unit letters and a space
        self.assertEqual(sel.strip(), "equations")
        pg.click(".seltoolspop button[aria-label='Highlight colour #86efac']")
        pg.wait_for_selector(".status >> text=2 annotations")
        quotes = sorted(r["anchor"]["segments"][0]["quote"] for r in self.rows("highlight"))
        self.assertEqual(quotes, ["equations", "then the quick"])

        self.svc.restart()                                                  # the marks come back on the same words
        self.reload_app()
        self.open_from_library("Edge document")
        pg.wait_for_selector(".status >> text=2 annotations")
        pg.locator("#view .mark").first.wait_for(state="attached")
        self.assertEqual(self.marked_text(), "then the quickequations")          # both marks, in reading order

    def test_right_to_left_text_is_selected_and_stored_as_shown(self):
        pg = self.page
        arabic = "هذا نص عربي للاختبار وهو يحتوي على كلمات كثيرة لكي نتحقق من الاختيار والتظليل بشكل صحيح."
        latin = "A paragraph in English comes first so that the reader has something on both sides of the Arabic one."
        self.open(write_sldc(self.docs / "rtl.sldc", [latin, arabic, latin.replace("first", "last")], "RTL document"))
        pg.wait_for_selector("#view .b[data-n='2'] .txt", timeout=15000)
        self.assertEqual(pg.evaluate("() => getComputedStyle(document.querySelector('#view .b[data-n=\"2\"] .txt')).direction"), "rtl",
                         "the Arabic paragraph must be laid out right to left")
        sel = self.drag_text(2, 4, 2, 20)
        self.assertTrue(sel.strip(), "something was selected")
        pg.click(".seltoolspop button[aria-label='Highlight colour #7dd3fc']")
        pg.wait_for_selector(".status >> text=1 annotation")
        seg = self.rows("highlight")[0]["anchor"]["segments"][0]
        self.assertIn(seg["quote"], arabic)
        self.assertEqual(arabic[seg["start"]:seg["end"]], seg["quote"])
        self.assertEqual(self.marked_text().strip(), seg["quote"].strip())
        self.assertEqual(sel.strip(), seg["quote"].strip(), "what the person selected is what was stored")
        self.shot("I1_rtl_highlight")

    def drag_along(self, first: int, last: int, page_no: int, rotation: int) -> str:
        """Select from the start of word `first` to the end of word `last` the way a person would: along the direction the text runs
        on screen (left to right on an upright page, top to bottom on a page rotated 90 degrees, and so on)."""
        pg = self.page
        pg.wait_for_selector(f".pagehost[data-page='{page_no}'] img.page[src]", state="attached", timeout=15000)
        spans = self.words(page_no)
        spans[first].scroll_into_view_if_needed()
        pg.wait_for_timeout(150)
        a, z = spans[first].bounding_box(), spans[last].bounding_box()
        cx = lambda b: b["x"] + b["width"] / 2
        cy = lambda b: b["y"] + b["height"] / 2
        start, end = {0: ((a["x"] + 1, cy(a)), (z["x"] + z["width"] - 1, cy(z))),
                      90: ((cx(a), a["y"] + 1), (cx(z), z["y"] + z["height"] - 1)),
                      180: ((a["x"] + a["width"] - 1, cy(a)), (z["x"] + 1, cy(z))),
                      270: ((cx(a), a["y"] + a["height"] - 1), (cx(z), z["y"] + 1))}[rotation]
        pg.mouse.move(*start); pg.mouse.down(); pg.mouse.move(*end, steps=8); pg.mouse.up()
        pg.wait_for_timeout(450)
        return pg.evaluate("String(getSelection())")

    def test_pdf_with_mixed_page_sizes_and_rotations(self):
        pg = self.page
        sizes = [(612, 792), (792, 612), (400, 600), (612, 792)]
        rots = [0, 90, 0, 180]
        self.open(fx.make_pdf(self.docs / "mixed.pdf", pages=4, sizes=sizes, rotations=rots))
        dragged = {}
        for n in range(4):
            host = pg.locator(f".pagehost[data-page='{n}']")
            host.scroll_into_view_if_needed()
            pg.wait_for_selector(f".pagehost[data-page='{n}'] .textlayer span:nth-child(8)", state="attached", timeout=15000)
            sel = self.drag_along(3, 6, n, rots[n])
            dragged[n] = sel
            self.assertTrue(sel.strip(), f"page {n + 1}: nothing selected")
            self.assertLess(len(sel), 40, f"page {n + 1}: four words were dragged but {sel!r} was selected")
            pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
            pg.wait_for_selector(f".status >> text={n + 1} annotation")
            pg.wait_for_selector(f".pagehost[data-page='{n}'] .markslayer > *", state="attached", timeout=10000)
            # the painted mark covers the selected words: its box sits inside the page and overlaps the text
            geo = pg.evaluate("""(n) => {
                const host = document.querySelector(`.pagehost[data-page="${n}"]`);
                const page = host.getBoundingClientRect();
                const marks = [...host.querySelectorAll('.markslayer .m')].map((m) => m.getBoundingClientRect());
                const spans = [...host.querySelectorAll('.textlayer span')].slice(3, 7).map((s) => s.getBoundingClientRect());
                return {page: [page.left, page.top, page.right, page.bottom], marks: marks.map((r) => [r.left, r.top, r.right, r.bottom]),
                        spans: spans.map((r) => [r.left, r.top, r.right, r.bottom])};
            }""", n)
            self.assertTrue(geo["marks"], f"page {n + 1}: no mark drawn")
            pl, pt, pr, pb = geo["page"]
            for l, t, r, b in geo["marks"]:
                self.assertGreaterEqual(l, pl - 1); self.assertLessEqual(r, pr + 1)
                self.assertGreaterEqual(t, pt - 1); self.assertLessEqual(b, pb + 1)
            sl = min(s[0] for s in geo["spans"]); sr = max(s[2] for s in geo["spans"])
            st = min(s[1] for s in geo["spans"]); sb = max(s[3] for s in geo["spans"])
            ml = min(m[0] for m in geo["marks"]); mr = max(m[2] for m in geo["marks"])
            mt = min(m[1] for m in geo["marks"]); mb = max(m[3] for m in geo["marks"])
            self.assertLess(abs(ml - sl), 6, f"page {n + 1}: mark starts {ml - sl:.1f}px from the words")
            self.assertLess(abs(mr - sr), 6, f"page {n + 1}: mark ends {mr - sr:.1f}px from the words")
            self.assertLess(abs((mt + mb) / 2 - (st + sb) / 2), 8, f"page {n + 1}: mark is not on the words' line")
        for r in self.rows("highlight"):
            seg = r["anchor"]["segments"][0]
            self.assertEqual("".join(seg["quote"].split()), "".join(dragged[seg["page"]].split()), f"page {seg['page'] + 1}: stored words differ from the dragged words")
        pages = sorted(r["anchor"]["segments"][0]["page"] for r in self.rows("highlight"))
        self.assertEqual(pages, [0, 1, 2, 3])
        self.shot("I2_mixed_pages")


if __name__ == "__main__":
    unittest.main()
