"""Exports against real documents: the output files are re-opened and inspected, PDFs are rendered and pixels compared."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from harness import Base, fixtures

from slai_reader.annotations import export
from slai_reader.bridge import sldc
from slai_reader.errors import BadRequest, UnsupportedOperation


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def render(path, page=0, scale=1.0, password=None):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path), password=password)
    try:
        return pdf[page].render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()


def paragraph_blocks(doc):
    return [b for b in doc.blocks if b["type"] == "paragraph" and len(b["text"]) > 60]


class Notes(Base):
    def setUp(self):
        super().setUp()
        self.svc = self.env.service(self.env.make("a.sldc", fixtures.make_sldc))
        self.doc = self.svc.doc
        p = paragraph_blocks(self.doc)
        self.svc.create({"kind": "highlight", "anchor": {"kind": "text", "segments": [{"block": p[1]["id"], "start": 3, "end": 30}]}})
        self.svc.create({"kind": "note", "body": {"text": "Check **this**\nsecond line", "tags": ["todo"]},
                         "anchor": {"kind": "text", "segments": [{"block": p[0]["id"], "start": 0, "end": 20}]}})
        self.svc.create({"kind": "bookmark", "anchor": {"kind": "block", "block": p[2]["id"]}})
        self.anns = self.svc.list()["annotations"]
        self.p = p

    def test_markdown_orders_by_position_and_keeps_quotes_and_notes(self):
        md = export.notes_markdown(self.doc, self.anns)
        self.assertIn(self.p[0]["text"][0:20], md)
        self.assertIn(self.p[1]["text"][3:30], md)
        self.assertIn("Check **this**", md)
        self.assertLess(md.index(self.p[0]["text"][0:20]), md.index(self.p[1]["text"][3:30]))
        self.assertIn("Tags: `todo`", md)
        self.assertIn(self.doc.doc_id, md)

    def test_json_roundtrip(self):
        data = export.notes_json(self.doc, self.anns)
        again = json.loads(json.dumps(data))
        self.assertEqual(again["schema"], "slai-reader-annotations/1")
        self.assertEqual(again["document"]["id"], self.doc.doc_id)
        self.assertEqual(len(again["annotations"]), 3)
        self.assertTrue(all("resolved" not in a for a in again["annotations"]))

    def test_notes_sldc_opens_in_reader_and_holds_quotes(self):
        out = export.notes_sldc(self.doc, self.anns, self.env.dir / "out")
        d2 = self.env.open(out)
        self.env.docs.append(d2)
        text = d2.plain_text()
        self.assertIn(self.p[1]["text"][3:30], text)
        self.assertIn("Check **this**", text)
        self.assertEqual(d2.format, "sldc")

    def test_sldc_with_comments_adds_review_threads_and_keeps_content(self):
        out, report = export.sldc_with_comments(self.doc, self.anns, self.env.dir / "out")
        self.assertEqual(report["comments"], 1)
        self.assertEqual(report["skipped"], 2)         # the plain highlight and the bookmark have no text to carry
        d2 = self.env.open(out)
        self.env.docs.append(d2)
        self.assertEqual(d2.revision, self.doc.revision)           # content revision unchanged: comments are not content
        info = d2.info()
        self.assertEqual(len(info["comments"]), 1)
        self.assertEqual(info["comments"][0]["anchorState"], "ok")

    def test_signed_sldc_signatures_survive_comment_export(self):
        svc = self.env.service(self.env.make("signed.sldc", fixtures.make_signed_sldc))
        block = svc.doc.blocks[0]
        svc.create({"kind": "note", "body": {"text": "Looks right"}, "anchor": {"kind": "text", "segments": [{"block": block["id"], "start": 0, "end": 6}]}})
        out, report = export.sldc_with_comments(svc.doc, svc.list()["annotations"], self.env.dir / "out")
        self.assertEqual(report["signaturesKept"], 1)
        d2 = self.env.open(out)
        self.env.docs.append(d2)
        states = [x["state"] for x in d2.signatures()]
        self.assertEqual(states, ["valid_unknown_signer"])

    def test_encrypted_sldc_copy_needs_a_password_and_stays_encrypted(self):
        path = self.env.make("enc.sldc", fixtures.make_encrypted_sldc, "correct horse battery")
        svc = self.env.service(path, password="correct horse battery")
        b = svc.doc.blocks[0]
        svc.create({"kind": "note", "body": {"text": "n"}, "anchor": {"kind": "text", "segments": [{"block": b["id"], "start": 0, "end": 3}]}})
        with self.assertRaises(BadRequest):
            export.sldc_with_comments(svc.doc, svc.list()["annotations"], self.env.dir / "out")
        out, _ = export.sldc_with_comments(svc.doc, svc.list()["annotations"], self.env.dir / "out", password="another long passphrase")
        self.assertEqual(sldc().inspect_package(out)["container"], "encrypted")

    def test_output_never_overwrites(self):
        a = export.notes_sldc(self.doc, self.anns, self.env.dir / "out")
        b = export.notes_sldc(self.doc, self.anns, self.env.dir / "out")
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())

    def test_pdf_only_export_refused_for_flow(self):
        with self.assertRaises(UnsupportedOperation):
            export.annotated_pdf(self.doc, self.anns, self.env.dir / "out")


class PdfExport(Base):
    def setUp(self):
        super().setUp()
        self.path = self.env.make("a.pdf", fixtures.make_pdf, pages=2)
        self.svc = self.env.service(self.path)
        self.doc = self.svc.doc
        text = self.doc.page_text(0).text
        i = text.index("Page 1 line 3")
        self.svc.create({"kind": "highlight", "style": {"color": "#00ff00", "opacity": 0.5}, "anchor": {"kind": "pdf-text", "segments": [{"page": 0, "start": i, "end": i + 13}]}})
        self.svc.create({"kind": "ink", "style": {"color": "#ff0000", "width": 6}, "geometry": {"points": [[0.1, 0.8], [0.5, 0.85], [0.9, 0.8]]},
                         "anchor": {"kind": "page", "page": 0}})
        self.svc.create({"kind": "sticky", "body": {"text": "see here"}, "anchor": {"kind": "page", "page": 1, "x": 0.5, "y": 0.5}})
        self.svc.create({"kind": "shape", "style": {"color": "#0000ff", "width": 4}, "geometry": {"shape": "rect", "x0": 0.2, "y0": 0.2, "x1": 0.4, "y1": 0.3},
                         "anchor": {"kind": "page", "page": 1}})
        self.svc.create({"kind": "bookmark", "anchor": {"kind": "page", "page": 1}})
        self.anns = self.svc.list()["annotations"]
        self.before = sha(self.path)
        self.rect = self.svc.page_marks(0)[0]["rects"][0]

    def px(self, img, nx, ny):
        return img.getpixel((int(nx), int(ny)))

    def test_native_export_writes_standard_annotations_and_leaves_original_alone(self):
        out, report = export.annotated_pdf(self.doc, self.anns, self.env.dir / "out", mode="native")
        self.assertEqual(sha(self.path), self.before)
        import pypdf
        r = pypdf.PdfReader(str(out))
        subtypes0 = sorted(a.get_object()["/Subtype"] for a in r.pages[0]["/Annots"] if a.get_object()["/Subtype"] != "/Link")   # the document's own link is kept
        subtypes1 = sorted(a.get_object()["/Subtype"] for a in r.pages[1]["/Annots"])
        self.assertEqual(subtypes0, ["/Highlight", "/Ink"])
        self.assertEqual(subtypes1, ["/Square", "/Text"])
        hl = [a.get_object() for a in r.pages[0]["/Annots"] if a.get_object()["/Subtype"] == "/Highlight"][0]
        self.assertEqual(len(hl["/QuadPoints"]) % 8, 0)
        titles = [i.title for i in r.outline if not isinstance(i, list)]
        self.assertIn("Reader bookmarks", titles)
        self.assertIn("Page 1", titles)          # the PDF's own outline is kept
        self.assertEqual(report["annotations"]["highlight"], 1)
        # the highlight is visible when another PDF engine (pdfium) draws the output
        orig, marked = render(self.path), render(out)
        x0, y0, x1, y1 = self.rect
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        o, m = self.px(orig, cx, y0 + 1), self.px(marked, cx, y0 + 1)
        self.assertNotEqual(o, m)
        self.assertGreater(m[1], m[0] + 20)          # green tint
        self.assertEqual(self.px(orig, 5, 5), self.px(marked, 5, 5))     # page elsewhere untouched
        # text is still extractable from the copy
        self.assertIn("Page 1 line 3", r.pages[0].extract_text())

    def test_flatten_export_draws_into_page_and_keeps_text(self):
        out, report = export.annotated_pdf(self.doc, self.anns, self.env.dir / "out", mode="flatten")
        import pypdf
        r = pypdf.PdfReader(str(out))
        self.assertEqual([a.get_object()["/Subtype"] for a in r.pages[0].get("/Annots", [])], ["/Link"])      # only the original link
        self.assertIn("Page 1 line 3", r.pages[0].extract_text())
        orig, flat = render(self.path), render(out)
        x0, y0, x1, y1 = self.rect
        o, f = self.px(orig, (x0 + x1) / 2, y0 + 1), self.px(flat, (x0 + x1) / 2, y0 + 1)
        self.assertGreater(f[1], f[0] + 20)
        self.assertNotEqual(o, f)
        # ink stroke is red where the path crosses the page (y = 0.8..0.85 of the page, x = 0.5)
        w, h = orig.size
        ink = self.px(flat, 0.5 * w, 0.85 * h)
        self.assertGreater(ink[0], 180)
        self.assertLess(ink[1], 90)

    def test_rotated_pdf_marks_land_on_the_text(self):
        svc = self.env.service(self.env.make("rot.pdf", fixtures.make_pdf, pages=1, rotate=90))
        d = svc.doc
        t = d.page_text(0).text
        i = t.index("line 5")
        svc.create({"kind": "highlight", "style": {"color": "#00ff00", "opacity": 0.6}, "anchor": {"kind": "pdf-text", "segments": [{"page": 0, "start": i, "end": i + 6}]}})
        svc.create({"kind": "ink", "style": {"color": "#ff0000", "width": 8}, "geometry": {"points": [[0.2, 0.2], [0.8, 0.2]]}, "anchor": {"kind": "page", "page": 0}})
        anns = svc.list()["annotations"]
        rect = svc.page_marks(0)[0]["rects"][0]
        for mode in ("native", "flatten"):
            out, _ = export.annotated_pdf(d, anns, self.env.dir / "out", mode=mode)
            orig, marked = render(d.path), render(out)
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            m = marked.getpixel((int(cx), int(cy)))
            o = orig.getpixel((int(cx), int(cy)))
            # sample a point inside the box that is not text: the left edge of the first character cell
            m2, o2 = marked.getpixel((int(rect[0] + 1), int(rect[1] + 1))), orig.getpixel((int(rect[0] + 1), int(rect[1] + 1)))
            self.assertTrue(m != o or m2 != o2, (mode, m, o, m2, o2))
            w, h = orig.size
            ink = marked.getpixel((int(0.5 * w), int(0.2 * h)))
            self.assertGreater(ink[0], 170, (mode, ink))
            self.assertLess(ink[2], 120, (mode, ink))

    def test_detached_and_hidden_annotations_are_not_exported(self):
        ink = next(a for a in self.anns if a["kind"] == "ink")       # list order between page-level marks is not fixed, so pick by kind
        self.svc.update(ink["id"], {"visible": False})
        anns = self.svc.list()["annotations"]
        out, report = export.annotated_pdf(self.doc, anns, self.env.dir / "out", mode="native")
        import pypdf
        r = pypdf.PdfReader(str(out))
        self.assertEqual([a.get_object()["/Subtype"] for a in r.pages[0]["/Annots"] if a.get_object()["/Subtype"] != "/Link"], ["/Highlight"])
        self.assertEqual(report["skipped"], 1)

    def test_encrypted_pdf_copy_stays_encrypted(self):
        src = self.env.make("plain.pdf", fixtures.make_pdf, pages=1)
        enc = self.env.make("enc.pdf", fixtures.make_encrypted_pdf, src, "open-sesame")
        svc = self.env.service(enc, password="open-sesame")
        svc.create({"kind": "sticky", "body": {"text": "n"}, "anchor": {"kind": "page", "page": 0, "x": 0.5, "y": 0.5}})
        out, report = export.annotated_pdf(svc.doc, svc.list()["annotations"], self.env.dir / "out")
        import pypdf
        r = pypdf.PdfReader(str(out))
        self.assertTrue(r.is_encrypted)
        self.assertEqual(r.decrypt("open-sesame") != 0, True)
        self.assertIn("password", report["note"])

    def test_pdf_that_forbids_annotation_is_not_modified(self):
        import pypdf
        from pypdf.constants import UserAccessPermissions as P
        src = self.env.make("plain2.pdf", fixtures.make_pdf, pages=1)
        reader = pypdf.PdfReader(str(src))
        w = pypdf.PdfWriter(clone_from=reader)
        w.encrypt("pw-open-1", "owner-secret", algorithm="AES-256", permissions_flag=P.PRINT)
        locked = self.env.dir / "locked.pdf"
        with open(locked, "wb") as fh:
            w.write(fh)
        svc = self.env.service(locked, password="pw-open-1")
        self.assertFalse(svc.doc.restrictions["annotate"])
        svc.create({"kind": "sticky", "body": {"text": "n"}, "anchor": {"kind": "page", "page": 0, "x": 0.5, "y": 0.5}})
        with self.assertRaises(UnsupportedOperation):
            export.annotated_pdf(svc.doc, svc.list()["annotations"], self.env.dir / "out")
        self.assertFalse((self.env.dir / "out").exists() and any((self.env.dir / "out").iterdir()))


if __name__ == "__main__":
    unittest.main()
