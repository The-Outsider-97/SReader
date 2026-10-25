"""Workflow G - get your notes out and back in: Markdown, JSON (round trip), PDF with real annotations, flattened PDF, SLDC comments."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from playwright.sync_api import expect

from harness import E2E, fx


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class WorkflowG(E2E):
    def mark_up_pdf(self):
        pg = self.page
        self.pdf = fx.make_pdf(self.docs / "paper.pdf", pages=3)
        self.original = sha(self.pdf)
        self.open(self.pdf)
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        pg.click(".toolbar button[aria-label='Underline text']")
        self.drag_words(12, 14)
        pg.wait_for_selector(".status >> text=2 annotations")
        pg.click(".toolbar button[aria-label='Select text']")
        self.drag_words(25, 28)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Verify this sentence — it uses “smart quotes” and an em dash.")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=3 annotations")
        pg.click(".toolbar button[aria-label='Pen']")
        b = pg.locator(".pagehost[data-page='0']").bounding_box()
        pg.mouse.move(b["x"] + 300, b["y"] + 150); pg.mouse.down(); pg.mouse.move(b["x"] + 500, b["y"] + 190, steps=15); pg.mouse.up()
        pg.wait_for_selector(".status >> text=4 annotations")
        pg.click(".toolbar button[aria-label^='Place a sticky note']")
        pg.mouse.click(b["x"] + b["width"] * 0.85, b["y"] + 400)
        pg.fill("textarea[aria-label='Sticky note text']", "Sticky for the margin.")
        pg.click(".popover button:has-text('Add note')")
        pg.wait_for_selector(".status >> text=5 annotations")
        pg.click(".toolbar button[aria-label='Select text']")

    def export(self, label: str):
        pg = self.page
        pg.click("button[aria-label='Export or import notes']")
        pg.select_option("select[aria-label='What to export']", label=label)
        pg.click(".dialog button:has-text('Export')")
        pg.wait_for_selector(".dialog .ok", timeout=30000)
        saved = pg.inner_text(".dialog p.small.muted").replace("Also saved to: ", "").strip()
        out = Path(saved)
        self.assertTrue(out.is_file(), saved)
        self.shot("G1_export_dialog")
        pg.keyboard.press("Escape")
        return out

    def test_markdown_json_roundtrip(self):
        pg = self.page
        self.mark_up_pdf()
        md = self.export("Notes as Markdown")
        text = md.read_text(encoding="utf-8")
        self.assertIn("line 1: The quick brown", text)
        self.assertIn("Verify this sentence — it uses “smart quotes” and an em dash.", text)
        self.assertIn("Sticky for the margin.", text)
        self.assertLess(text.index("line 1: The quick brown"), text.index("Sticky for the margin.") if text.index("Sticky for the margin.") > text.index("line 1: The quick brown") else 10**9)

        js = self.export("Notes as JSON (can be imported back)")
        data = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "slai-reader-annotations/1")
        self.assertEqual(len(data["annotations"]), 5)
        self.assertEqual(sha(self.pdf), self.original, "exporting must never modify the document")

        # a brand-new Reader (empty data folder) imports the file for the same PDF; importing again adds nothing
        self.svc.stop()
        import shutil
        shutil.rmtree(self.tmp / "data")
        self.svc.restart()
        self.reload_app()
        self.open(self.pdf)
        pg.wait_for_selector(".status >> text=0 annotations")
        for expected in ("Imported 5. 0 were already here.", "Imported 0. 5 were already here."):
            pg.click("button[aria-label='Export or import notes']")
            pg.set_input_files("input[aria-label='Choose a Reader notes file to import']", str(js))
            pg.click(".dialog button:text-is('Import')")
            pg.wait_for_selector(f".dialog >> text={expected}")
            pg.keyboard.press("Escape")
        pg.wait_for_selector(".status >> text=5 annotations")
        self.assertEqual(len(self.rows()), 5)
        expect(pg.locator(".pagehost[data-page='0'] .markslayer > *")).not_to_have_count(0)

    def test_pdf_with_real_annotations_and_flattened(self):
        self.mark_up_pdf()
        native = self.export("PDF with standard annotations")
        self.assertEqual(sha(self.pdf), self.original)
        from pypdf import PdfReader
        r = PdfReader(str(native))
        subtypes = sorted(str(a.get_object()["/Subtype"]) for a in (r.pages[0].get("/Annots") or []))
        # what the reader made (highlight, underline, highlight-with-note, ink, sticky) plus the PDF's own link, which must survive untouched
        self.assertEqual(subtypes, ["/Highlight", "/Highlight", "/Ink", "/Link", "/Text", "/Underline"], subtypes)
        contents = sorted(str(a.get_object().get("/Contents", "")) for a in r.pages[0]["/Annots"])
        self.assertIn("Verify this sentence — it uses “smart quotes” and an em dash.", contents)
        self.assertIn("Sticky for the margin.", contents)
        hl = [a.get_object() for a in r.pages[0]["/Annots"] if str(a.get_object()["/Subtype"]) == "/Highlight"][0]
        self.assertEqual(len(hl["/QuadPoints"]) % 8, 0)
        self.assertIn("line 1", PdfReader(str(native)).pages[0].extract_text().replace("\n", " "))

        flat = self.export("PDF with marks drawn in")
        import pypdfium2 as pdfium
        a = pdfium.PdfDocument(str(self.pdf))[0].render(scale=1).to_pil().convert("RGB")
        b = pdfium.PdfDocument(str(flat))[0].render(scale=1).to_pil().convert("RGB")
        from PIL import ImageChops
        self.assertIsNotNone(ImageChops.difference(a, b).getbbox(), "the flattened copy must show the marks")
        self.assertEqual(len(PdfReader(str(flat)).pages), 3)
        self.assertIn("Page 1 heading", PdfReader(str(flat)).pages[0].extract_text(), "text stays selectable after flattening")

    def test_sldc_comments_keep_signatures(self):
        pg = self.page
        signed = fx.make_signed_sldc(self.docs / "contract.sldc")
        before = sha(signed)
        self.open(signed)
        pg.wait_for_selector("#view .b .txt")
        self.drag_text(0, 0, 0, 8)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Please confirm the date.")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=1 annotation")
        out = self.export("This document with your notes as review comments (SLDC)")
        self.assertEqual(sha(signed), before, "the original signed file is never touched")
        self.assertNotEqual(sha(out), before)
        # open the exported copy in Reader itself: the signature is still valid and the comment is there
        pg.goto(self.svc.url)
        self.open(out)
        pg.click("button[aria-label='Document info, hand-off, shortcuts']")
        pg.click(".popover >> text=About this document")
        pg.wait_for_selector(".dialog >> text=Signatures")
        dialog = pg.inner_text(".dialog")
        self.assertIn("valid_unknown_signer", dialog)
        self.assertIn("1 review comment thread", dialog)
        self.shot("G2_signed_copy_info")


if __name__ == "__main__":
    unittest.main()
