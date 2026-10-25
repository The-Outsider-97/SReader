"""Many annotations on a long PDF: resolving anchors must use the cheap page text, not measure every character of every page."""
from __future__ import annotations

import time
from unittest import mock

from harness import Base, fixtures


class PdfScale(Base):
    PAGES = 120

    def setUp(self) -> None:
        super().setUp()
        self.svc = self.env.service(self.env.make("long.pdf", fixtures.make_big_pdf, self.PAGES))
        self.doc = self.svc.doc

    def test_plain_text_is_identical_to_the_measured_text(self):
        """plain_page is only a faster route to the same characters: offsets stored by either must be interchangeable."""
        for rotate in (0, 90):
            path = self.env.make(f"r{rotate}.pdf", fixtures.make_pdf, pages=3, rotate=rotate)
            doc = self.env.open(path)
            self.env.docs.append(doc)
            for i in range(doc.pages):
                self.assertEqual(doc.plain_page(i), doc.page_text(i).text, f"rotation {rotate}, page {i}")
        astral = self.env.make("astral.pdf", fixtures.make_pdf, pages=1, text_prefix="Ünïcödé 𝔘 ")
        doc = self.env.open(astral)
        self.env.docs.append(doc)
        self.assertEqual(doc.plain_page(0), doc.page_text(0).text)

    def test_listing_and_marks_do_not_measure_pages(self):
        for n in range(self.PAGES):
            self.svc.create({"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [{"page": n, "start": 10, "end": 30}]}})
        self.assertEqual(len(self.svc.store.list(self.doc.doc_id)), self.PAGES)
        self.doc._texts.clear()
        with mock.patch.object(type(self.doc), "page_text", side_effect=AssertionError("page_text (per-character boxes) must not be used to resolve anchors")):
            t0 = time.perf_counter()
            listing = self.svc.list()
            took = time.perf_counter() - t0
        self.assertEqual(len(listing["annotations"]), self.PAGES)
        self.assertTrue(all(a["status"] == "ok" for a in listing["annotations"]))
        self.assertTrue(all(a["text"] for a in listing["annotations"]))
        self.assertLess(took, 5.0, "120 annotations over 120 pages must list in well under a few seconds")
        marks = self.svc.page_marks(5)                       # needs the boxes of that one page only
        self.assertEqual(len(marks), 1)
        self.assertTrue(marks[0]["rects"])

    def test_marks_only_look_at_annotations_on_that_page(self):
        for n in range(self.PAGES):
            self.svc.create({"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [{"page": n, "start": 10, "end": 30}]}})
        calls = []
        real = self.doc.plain_page
        self.doc.plain_page = lambda i: (calls.append(i), real(i))[1]        # type: ignore[method-assign]
        self.svc.page_marks(7)
        self.assertEqual(set(calls), {7}, "resolving the marks of page 8 must not read any other page")

    def test_background_index_uses_plain_text_and_search_still_works(self):
        deadline = time.time() + 30
        while not self.doc.index_state()["complete"] and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.doc.index_state()["complete"])
        res = self.doc.search("needle7", limit=500)
        self.assertEqual(res["total"], len({i for i in range(self.PAGES) if i % 50 == 7}) * 40)
        h = res["hits"][0]
        self.assertEqual(self.doc.page_text(h["page"]).text[h["start"]:h["end"]], "needle7")
