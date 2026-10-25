"""Workflow F - things going wrong: passwords, scans, damaged and missing files, a file that changes, a damaged database, a stopped service."""
from __future__ import annotations

import shutil
import sqlite3
import time
import unittest
from pathlib import Path

from playwright.sync_api import expect

from harness import E2E, fx


class WorkflowF(E2E):
    # every refused request (401 for a password, 422 for a damaged file) is logged by the browser itself; they are expected here
    expected_errors = ("Failed to load resource", "net::ERR", "Failed to fetch")

    def test_password_protected_pdf(self):
        pg = self.page
        plain = fx.make_pdf(self.docs / "plain.pdf", pages=2)
        locked = fx.make_encrypted_pdf(self.docs / "locked.pdf", plain, user_password="open-sesame")
        pg.fill("input[aria-label='Full path of a file to open']", str(locked))
        pg.click("text=Open path")
        pg.wait_for_selector(".dialog input[aria-label='Password']")
        self.shot("F1_password_prompt")
        pg.fill(".dialog input[aria-label='Password']", "not-the-password")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".dialog [role=alert]:has-text('did not work')")
        pg.fill(".dialog input[aria-label='Password']", "open-sesame")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".toolbar")
        pg.wait_for_selector(".status [role=status]:not(:empty)")
        self.assertIn("Page 1 of 2", self.status_text())
        # the password is not kept anywhere Reader writes
        blob = b"".join(p.read_bytes() for p in (self.tmp / "data").rglob("*") if p.is_file())
        self.assertNotIn(b"open-sesame", blob)
        # cancelling the prompt leaves the library usable
        pg.click("button:has-text('Library')")
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")

    def test_cancel_password_prompt(self):
        pg = self.page
        plain = fx.make_pdf(self.docs / "plain.pdf", pages=2)
        locked = fx.make_encrypted_pdf(self.docs / "locked.pdf", plain)
        pg.fill("input[aria-label='Full path of a file to open']", str(locked))
        pg.click("text=Open path")
        pg.wait_for_selector(".dialog input[aria-label='Password']")
        pg.click(".dialog button:text-is('Cancel')")
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")
        self.assertEqual(pg.locator(".pdfview, .flowview").count(), 0)

    def test_scanned_pdf_is_honest_about_text_but_still_annotatable(self):
        pg = self.page
        self.open(fx.make_scanned_pdf(self.docs / "scan.pdf", pages=2))
        pg.click("button[aria-label='Show or hide the side panel']")
        pg.click("[role=tab]:text-is('Search')")
        pg.fill("input[aria-label='Search in this document']", "anything")
        pg.wait_for_selector("#search-status:has-text('scan')")
        self.assertIn("does not run OCR", pg.inner_text("#search-status"))
        self.assertTrue(pg.locator(".toolbar button[aria-label='Highlight text']").is_disabled())
        self.assertIn("scanned PDF", pg.locator(".toolbar button[aria-label^='Highlight text']").get_attribute("title"))
        # drawing, sticky notes and bookmarks work without a text layer
        pg.click(".toolbar button[aria-label='Pen']")
        b = pg.locator(".pagehost[data-page='0']").bounding_box()
        pg.mouse.move(b["x"] + 200, b["y"] + 200); pg.mouse.down(); pg.mouse.move(b["x"] + 400, b["y"] + 260, steps=12); pg.mouse.up()
        pg.wait_for_selector(".status >> text=1 annotation")
        self.assertEqual(len(self.rows("ink")), 1)
        self.shot("F2_scanned_pdf")

    def test_damaged_missing_and_wrong_files_give_a_clear_message(self):
        pg = self.page
        good = fx.make_pdf(self.docs / "good.pdf", pages=2)
        cases = {
            "truncated.pdf": lambda p: fx.make_truncated_pdf(p, good),
            "garbage.pdf": lambda p: fx.make_garbage(p, b"%PDF-1.4\\n"),
            "fake.docx": lambda p: fx.make_garbage(p, b"PK\\x03\\x04"),
            "fake.sldc": lambda p: fx.make_garbage(p, b"PK\\x03\\x04"),
            "empty.txt": lambda p: p.write_bytes(b""),
            "binary.epub": lambda p: fx.make_garbage(p, b"PK\\x03\\x04"),
        }
        for name, make in cases.items():
            with self.subTest(name):
                path = self.docs / name
                make(path)
                pg.goto(self.svc.url)
                pg.fill("input[aria-label='Full path of a file to open']", str(path))
                pg.click("text=Open path")
                alert = pg.locator(".banner.bad[role=alert]")
                alert.wait_for(timeout=15000)
                msg = alert.inner_text()
                self.assertGreater(len(msg), 15)
                self.assertNotIn("Traceback", msg)
                self.assertNotIn(str(self.docs), msg, "error text must not leak internal paths or stack traces")
                self.assertEqual(pg.locator(".pdfview, .flowview").count(), 0)
        pg.goto(self.svc.url)
        pg.fill("input[aria-label='Full path of a file to open']", str(self.docs / "no-such-file.pdf"))
        pg.click("text=Open path")
        pg.locator(".banner.bad[role=alert]").wait_for()
        self.assertIn("no file", pg.inner_text(".banner.bad").lower())
        # and the good one still opens afterwards
        pg.fill("input[aria-label='Full path of a file to open']", str(good))
        pg.click("text=Open path")
        pg.wait_for_selector(".toolbar")
        self.shot("F3_after_errors")

    def test_file_replaced_while_open(self):
        pg = self.page
        pdf = fx.make_pdf(self.docs / "live.pdf", pages=3)
        self.open(pdf)
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        time.sleep(1.1)
        fx.make_pdf(pdf, pages=5)                                       # replaced by a longer version of the same document
        pg.wait_for_selector(".banner:has-text('changed')", timeout=25000)
        pg.click(".banner button:has-text('Reload')")
        pg.wait_for_selector(".status >> text=of 5")
        # the rewritten file has a new identity, so Reader offers (rather than assumes) to carry the notes over
        pg.wait_for_selector(".banner:has-text('can be carried over')")
        self.assertIn("0 annotations", self.status_text())
        pg.click(".banner button:has-text('Carry them over')")
        pg.wait_for_selector(".status >> text=1 annotation")
        self.assertEqual(self.rows("highlight")[0]["anchor"]["segments"][0]["quote"], "line 1: The quick brown")
        expect(pg.locator(".pagehost[data-page='0'] .markslayer > *")).not_to_have_count(0)

    def test_file_deleted_then_found_again_elsewhere(self):
        pg = self.page
        pdf = fx.make_pdf(self.docs / "moves.pdf", pages=2)
        self.open(pdf)
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector(".status >> text=1 annotation")
        pg.click("button:has-text('Library')")
        new_home = self.tmp / "elsewhere"
        new_home.mkdir()
        shutil.move(str(pdf), str(new_home / "renamed copy.pdf"))
        pg.goto(self.svc.url)
        pg.wait_for_selector("article.card .warn:has-text('not where it was')")
        self.assertTrue(pg.locator("article.card button.primary").is_disabled())
        self.shot("F4_missing_file")
        self.open(new_home / "renamed copy.pdf")                          # opened again from its new place: the notes come back
        pg.wait_for_selector(".status >> text=1 annotation")
        self.assertEqual(len(self.rows("highlight")), 1)

    def test_damaged_database_is_replaced_and_notes_restored_from_backup(self):
        pg = self.page
        pdf = fx.make_pdf(self.docs / "keep.pdf", pages=2)
        self.open(pdf)
        for k in range(3):
            self.drag_words(5 + 6 * k, 8 + 6 * k)
            pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
            pg.wait_for_selector(f".status >> text={k + 1} annotation")
            pg.keyboard.press("Escape")
        pg.wait_for_timeout(800)
        self.svc.stop()
        db = self.tmp / "data" / "reader.sqlite"
        db.write_bytes(b"this is not a sqlite file" * 200)
        for extra in ("reader.sqlite-wal", "reader.sqlite-shm"):
            (self.tmp / "data" / extra).unlink(missing_ok=True)
        self.svc.restart()
        pg.goto(self.svc.url)
        pg.wait_for_selector(".banner:has-text('damaged')", timeout=15000)
        self.assertIn("restored", pg.inner_text(".banner:has-text('damaged')"))
        self.shot("F5_database_recovered")
        self.assertTrue(any((self.tmp / "data").glob("*corrupt*")) or any((self.tmp / "data").glob("*quarantine*")) or any(p.name.startswith("reader.sqlite.") for p in (self.tmp / "data").iterdir()),
                        "the damaged file is kept aside, not deleted")
        self.open(pdf)
        pg.wait_for_selector(".status >> text=3 annotations")

    def test_service_stops_while_reading(self):
        pg = self.page
        pdf = fx.make_pdf(self.docs / "stops.pdf", pages=2)
        self.open(pdf)
        self.svc.stop()
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Highlight colour #facc15']")
        pg.wait_for_selector("[role=status]:has-text('save'), .toast, [role=alert]", timeout=10000)
        # nothing claims to be saved that is not: the count did not go up
        self.assertIn("0 annotations", self.status_text())


if __name__ == "__main__":
    unittest.main()
