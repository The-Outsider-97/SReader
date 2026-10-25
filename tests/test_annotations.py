"""Annotation service and store against real documents: create, resolve, move, detach, undo, conflict, duplicates."""
from __future__ import annotations

import json
import unittest

from harness import Base, fixtures

from slai_reader.errors import BadRequest, Conflict, NotFound, UnsupportedOperation


def first_text_block(doc):
    for b in doc.blocks:
        if b["type"] == "paragraph" and len(b["text"]) > 60:
            return b
    raise AssertionError("no paragraph")


class FlowAnnotations(Base):
    def setUp(self):
        super().setUp()
        self.path = self.env.make("a.sldc", fixtures.make_sldc)
        self.svc = self.env.service(self.path)
        self.doc = self.svc.doc
        self.block = first_text_block(self.doc)

    def seg(self, s=4, e=20, block=None):
        return {"block": (block or self.block)["id"], "start": s, "end": e}

    def highlight(self, **kw):
        return self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [self.seg(**kw)]}})

    def test_highlight_reads_its_quote_from_the_document(self):
        a = self.highlight()
        self.assertEqual(a["text"], self.block["text"][4:20])
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["anchor"]["segments"][0]["quote"], self.block["text"][4:20])
        self.assertEqual(a["style"]["color"], "#facc15")

    def test_client_cannot_choose_the_quote(self):
        a = self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [{**self.seg(), "quote": "forged"}]}})
        self.assertEqual(a["anchor"]["segments"][0]["quote"], self.block["text"][4:20])

    def test_bad_ranges_and_blocks_are_refused(self):
        for seg in ({"block": "nope", "start": 0, "end": 3}, {**self.seg(), "end": 10**6}, {**self.seg(), "start": 9, "end": 9},
                    {**self.seg(), "start": -1}, {"block": self.block["id"], "start": "0", "end": 3}):
            with self.assertRaises(BadRequest):
                self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [seg]}})
        self.assertEqual(self.svc.store.counts(self.doc.doc_id), {})

    def test_duplicate_highlight_is_not_stored_twice(self):
        a = self.highlight()
        b = self.highlight()
        self.assertTrue(b.get("duplicate"))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(self.svc.store.counts(self.doc.doc_id), {"highlight": 1})
        c = self.svc.create({"kind": "highlight", "style": {"color": "#00ff00"}, "anchor": {"kind": "text", "segments": [self.seg()]}})
        self.assertFalse(c.get("duplicate"))

    def test_multi_segment_highlight_across_blocks(self):
        paras = [b for b in self.doc.blocks if b["type"] == "paragraph" and len(b["text"]) > 30][:3]
        segs = [{"block": b["id"], "start": 0, "end": 25} for b in paras]
        a = self.svc.create({"kind": "underline", "anchor": {"kind": "text", "segments": segs}})
        self.assertEqual(len(a["resolved"]["segments"]), 3)
        self.assertEqual(a["text"].count(" … "), 2)

    def test_style_validation(self):
        for style in ({"color": "red"}, {"opacity": 0}, {"opacity": 2}, {"width": -1}, {"tool": "pen"}):
            with self.assertRaises(BadRequest):
                self.svc.create({"kind": "highlight", "style": style, "anchor": {"kind": "text", "segments": [self.seg()]}})
        a = self.svc.create({"kind": "highlight", "style": {"color": "#ABCDEF", "opacity": 0.3}, "anchor": {"kind": "text", "segments": [self.seg(30, 40)]}})
        self.assertEqual(a["style"], {"color": "#abcdef", "opacity": 0.3})

    def test_typed_note_with_tags_and_empty_note_refused(self):
        with self.assertRaises(BadRequest):
            self.svc.create({"kind": "note", "body": {"text": ""}, "anchor": {"kind": "block", "block": self.block["id"]}})
        n = self.svc.create({"kind": "note", "body": {"text": "Check this claim", "tags": ["todo", " Todo ", "evidence"]},
                             "anchor": {"kind": "text", "segments": [self.seg()]}})
        self.assertEqual(n["body"]["tags"], ["evidence", "todo"])        # trimmed; "todo" and "Todo" are one tag
        self.assertEqual(n["status"], "ok")

    def test_bookmark_gets_a_title_from_the_text(self):
        b = self.svc.create({"kind": "bookmark", "anchor": {"kind": "block", "block": self.block["id"]}})
        self.assertTrue(b["body"]["title"])
        self.assertIn(b["body"]["title"][:10], " ".join(self.block["text"].split()))

    def test_ink_and_shape_geometry(self):
        pts = [[0.1, 0.1, 0.5], [0.2, 0.15], [0.3, 0.1, 1.0]]
        ink = self.svc.create({"kind": "ink", "style": {"tool": "pencil", "width": 2}, "geometry": {"points": pts},
                               "anchor": {"kind": "block", "block": self.block["id"]}})
        self.assertEqual(ink["geometry"]["points"][1], [0.2, 0.15, 0.5])
        for bad in ({"points": []}, {"points": [[0, 0, 5]]}, {"points": [[float("nan"), 0]]}, {"points": "x"}, {"points": [[0]]}):
            with self.assertRaises(BadRequest):
                self.svc.create({"kind": "ink", "geometry": bad, "anchor": {"kind": "block", "block": self.block["id"]}})
        with self.assertRaises(BadRequest):
            self.svc.create({"kind": "ink", "geometry": {"points": pts}, "anchor": {"kind": "text", "segments": [self.seg()]}})
        shape = self.svc.create({"kind": "shape", "geometry": {"shape": "arrow", "x0": 0, "y0": 0, "x1": 1, "y1": 1}, "anchor": {"kind": "block", "block": self.block["id"]}})
        self.assertEqual(shape["geometry"]["shape"], "arrow")

    def test_update_delete_undo_redo_cycle(self):
        a = self.highlight()
        u = self.svc.update(a["id"], {"style": {"color": "#112233"}}, a["rev"])
        self.assertEqual(u["style"]["color"], "#112233")
        self.assertEqual(u["rev"], a["rev"] + 1)
        self.svc.delete_many([a["id"]])
        self.assertEqual(self.svc.list()["annotations"], [])
        self.svc.undo()                                     # restores the deleted highlight
        self.assertEqual(self.svc.list()["annotations"][0]["style"]["color"], "#112233")
        self.svc.undo()                                     # the colour change
        self.assertEqual(self.svc.list()["annotations"][0]["style"]["color"], "#facc15")
        self.svc.undo()                                     # the creation
        self.assertEqual(self.svc.list()["annotations"], [])
        self.assertFalse(self.svc.list()["history"]["canUndo"])
        self.svc.redo(); self.svc.redo(); self.svc.redo()
        self.assertEqual(self.svc.list()["annotations"], [])
        with self.assertRaises(Conflict):
            self.svc.redo()
        self.assertFalse(self.svc.list()["history"]["canRedo"])

    def test_new_change_clears_redo(self):
        a = self.highlight()
        self.svc.undo()
        self.assertTrue(self.svc.list()["history"]["canRedo"])
        self.highlight(s=30, e=44)
        self.assertFalse(self.svc.list()["history"]["canRedo"])

    def test_grouped_erase_is_one_undo_step(self):
        ids = [self.highlight(s=i * 10, e=i * 10 + 8)["id"] for i in range(5)]
        self.svc.delete_many(ids)
        self.assertEqual(len(self.svc.list()["annotations"]), 0)
        self.svc.undo()
        self.assertEqual(len(self.svc.list()["annotations"]), 5)

    def test_conflict_detected_on_stale_revision(self):
        a = self.highlight()
        self.svc.update(a["id"], {"style": {"color": "#111111"}}, a["rev"])
        with self.assertRaises(Conflict) as cm:
            self.svc.update(a["id"], {"style": {"color": "#222222"}}, a["rev"])
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(cm.exception.details["currentRev"], a["rev"] + 1)

    def test_unknown_fields_and_other_documents_refused(self):
        a = self.highlight()
        with self.assertRaises(BadRequest):
            self.svc.update(a["id"], {"anchor": {"kind": "block", "block": "x"}})
        with self.assertRaises(NotFound):
            self.svc.update("a" + "0" * 20, {"visible": False})

    def test_too_large_note_refused(self):
        with self.assertRaises(BadRequest):
            self.svc.create({"kind": "note", "body": {"text": "x" * 20001}, "anchor": {"kind": "block", "block": self.block["id"]}})

    def test_edit_moves_with_text_when_a_block_changes(self):
        """The document is edited so the quoted words move: the highlight follows only where it is unambiguous."""
        a = self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [self.seg(0, 30)]}})
        quote = a["anchor"]["segments"][0]["quote"]
        # A second revision of the document: same text, preceded by an extra paragraph inside the same block.
        edited = self.doc  # resolve against a document whose block text gained a prefix
        original = edited.block_text(self.block["id"])
        edited._texts[edited.index[self.block["id"]]] = "Inserted words. " + original
        edited.blocks[edited.index[self.block["id"]]]["text"] = "Inserted words. " + original
        v = self.svc.view(self.svc.store.get(a["id"]))
        self.assertEqual(v["status"], "moved")
        self.assertEqual(v["text"], quote)
        # remove the quote entirely -> detached, annotation kept
        edited._texts[edited.index[self.block["id"]]] = "Completely different words here."
        edited.blocks[edited.index[self.block["id"]]]["text"] = "Completely different words here."
        v = self.svc.view(self.svc.store.get(a["id"]))
        self.assertEqual(v["status"], "detached")
        self.assertEqual(self.svc.store.get(a["id"])["anchor"]["segments"][0]["quote"], quote)

    def test_accept_moved_rewrites_the_anchor(self):
        a = self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [self.seg(0, 30)]}})
        i = self.doc.index[self.block["id"]]
        original = self.doc.block_text(self.block["id"])
        self.doc._texts[i] = "Inserted words. " + original
        self.doc.blocks[i]["text"] = "Inserted words. " + original
        [acc] = self.svc.accept_moved([a["id"]])
        self.assertEqual(acc["status"], "ok")
        self.assertEqual(acc["anchor"]["segments"][0]["start"], 16)

    def test_reanchor_detached(self):
        a = self.svc.create({"kind": "note", "body": {"text": "n"}, "anchor": {"kind": "text", "segments": [self.seg(0, 30)]}})
        paras = [b for b in self.doc.blocks if b["type"] == "paragraph" and len(b["text"]) > 60]
        r = self.svc.reanchor(a["id"], {"kind": "text", "segments": [{"block": paras[1]["id"], "start": 2, "end": 12}]})
        self.assertEqual(r["text"], paras[1]["text"][2:12])
        self.assertEqual(r["body"]["text"], "n")
        self.svc.undo()
        self.assertEqual(self.svc.view(self.svc.store.get(a["id"]))["text"], self.block["text"][0:30])


class PdfAnnotations(Base):
    def setUp(self):
        super().setUp()
        self.path = self.env.make("a.pdf", fixtures.make_pdf, pages=3)
        self.svc = self.env.service(self.path)
        self.doc = self.svc.doc

    def sel(self, page, needle):
        text = self.doc.page_text(page).text
        i = text.index(needle)
        return {"page": page, "start": i, "end": i + len(needle)}

    def test_pdf_highlight_text_and_rectangles(self):
        a = self.svc.create({"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [self.sel(1, "Page 2 line 3")]}})
        self.assertEqual(a["text"], "Page 2 line 3")
        marks = self.svc.page_marks(1)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["id"], a["id"])
        r = marks[0]["rects"][0]
        self.assertTrue(0 < r[0] < r[2] < 612 and 0 < r[1] < r[3] < 792)
        self.assertEqual(self.svc.page_marks(0), [])

    def test_pdf_page_note_and_ink(self):
        n = self.svc.create({"kind": "sticky", "body": {"text": "look"}, "anchor": {"kind": "page", "page": 2, "x": 0.3, "y": 0.4}})
        self.assertEqual(n["resolved"]["page"], 2)
        ink = self.svc.create({"kind": "ink", "geometry": {"points": [[0.1, 0.1], [0.5, 0.5]]}, "anchor": {"kind": "page", "page": 0}})
        self.assertEqual(ink["status"], "ok")
        with self.assertRaises(BadRequest):
            self.svc.create({"kind": "sticky", "anchor": {"kind": "page", "page": 9}})
        with self.assertRaises(BadRequest):
            self.svc.create({"kind": "ink", "geometry": {"points": [[0.1, 2.0]]}, "anchor": {"kind": "page", "page": 0}})

    def test_scanned_pdf_refuses_text_marks_but_takes_ink(self):
        scan = self.env.service(self.env.make("scan.pdf", fixtures.make_scanned_pdf))
        with self.assertRaises(UnsupportedOperation):
            scan.create({"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [{"page": 0, "start": 0, "end": 3}]}})
        ink = scan.create({"kind": "ink", "geometry": {"points": [[0.1, 0.1], [0.4, 0.4]]}, "anchor": {"kind": "page", "page": 0}})
        self.assertEqual(ink["kind"], "ink")

    def test_rotated_page_rectangles_stay_inside_displayed_page(self):
        svc = self.env.service(self.env.make("rot.pdf", fixtures.make_pdf, pages=1, rotate=90))
        d = svc.doc
        t = d.page_text(0).text
        i = t.index("line 2")
        svc.create({"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [{"page": 0, "start": i, "end": i + 6}]}})
        w, h = d.page_text(0).width, d.page_text(0).height
        for r in svc.page_marks(0)[0]["rects"]:
            self.assertTrue(0 <= r[0] < r[2] <= w + 0.5 and 0 <= r[1] < r[3] <= h + 0.5, (r, w, h))


class Persistence(Base):
    def test_annotations_survive_closing_and_reopening_the_store(self):
        path = self.env.make("a.sldc", fixtures.make_sldc)
        svc = self.env.service(path)
        b = first_text_block(svc.doc)
        a = svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [{"block": b["id"], "start": 0, "end": 12}]}})
        svc.close()
        self.env.store.close()
        from slai_reader.annotations.store import Store
        store2 = Store(self.env.settings.db_path, self.env.settings.backups_dir)
        self.addCleanup(store2.close)
        again = store2.list(svc.doc.doc_id)
        self.assertEqual([x["id"] for x in again], [a["id"]])

    def test_same_sldc_copied_to_another_path_shares_annotations(self):
        import shutil
        path = self.env.make("a.sldc", fixtures.make_sldc)
        svc = self.env.service(path)
        b = first_text_block(svc.doc)
        svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [{"block": b["id"], "start": 0, "end": 12}]}})
        copy = self.env.dir / "copy.sldc"
        shutil.copy(path, copy)
        svc2 = self.env.service(copy)
        self.assertEqual(svc2.doc.doc_id, svc.doc.doc_id)
        self.assertEqual(len(svc2.list()["annotations"]), 1)


if __name__ == "__main__":
    unittest.main()


class WordDirection(Base):
    """Words on a rotated page tell the client which way their letters run, so its selection layer can follow the printed text."""

    def test_each_page_reports_the_way_its_words_run(self):
        p = self.env.make("mixed.pdf", fixtures.make_pdf, pages=4, sizes=[(612, 792), (792, 612), (400, 600), (612, 792)], rotations=[0, 90, 0, 180])
        doc = self.env.service(p).doc
        expected = {0: 0, 1: 90, 2: 0, 3: 180}
        for page, turn in expected.items():
            words = doc.text_payload(page)["words"]
            self.assertTrue(words)
            seen = {w[6] if len(w) > 6 else 0 for w in words}
            self.assertEqual(seen, {turn}, f"page {page + 1}")

    def test_270_runs_bottom_to_top(self):
        p = self.env.make("r270.pdf", fixtures.make_pdf, pages=1, rotations=[270])
        words = self.env.service(p).doc.text_payload(0)["words"]
        self.assertEqual({w[6] for w in words}, {270})
