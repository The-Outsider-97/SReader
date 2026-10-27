"""Workflow J - the Appearance panel in a real browser: every control changes the page at once and survives a restart, unsupported
controls say why they are off, failed saves are reported without losing the confirmed values, rapid changes end in one consistent
state, and a pop-up never lets a click reach the page beneath it."""
from __future__ import annotations

import json
import re
import shutil
import unittest

from playwright.sync_api import expect

from harness import E2E, fx


def rgba(text: str) -> tuple[float, float, float, float]:
    nums = [float(x) for x in re.findall(r"[\d.]+", text)]
    return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)


class AppearancePanel(E2E):
    def setUp(self) -> None:
        super().setUp()
        self.sldc = fx.make_sldc(self.docs / "reading.sldc", paragraphs=40, title="Reading text", doc_id="reading-doc")

    # ------------------------------------------------------------------ helpers
    def open_panel(self) -> None:
        pg = self.page
        if pg.locator(".toolbar button[aria-label='Reading appearance']").is_visible():
            pg.click(".toolbar button[aria-label='Reading appearance']")
        else:                                                        # narrow windows move it into the menu
            pg.click(".toolbar button[aria-label='Open menu']")
            pg.click("[role=menuitem]:text-is('Reading appearance')")
        pg.wait_for_selector(".appearance")

    def close_panel(self) -> None:
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(".appearance", state="detached")

    def slider(self, label: str):
        return self.page.locator(f".appearance .field:has(> label:has-text('{label}')) input[type=range]")

    def select(self, label: str):
        return self.page.locator(f".appearance .field:has(> label:text-is('{label}')) select")

    def mode(self, name: str):
        return self.page.locator(f".appearance label.mode:has(strong:text-is('{name}'))")

    def wait_saved(self) -> None:
        self.page.wait_for_function("() => document.querySelector('.appearance .save-status')?.dataset.phase !== 'saving'", timeout=8000)
        self.page.wait_for_timeout(100)

    def flow_style(self, prop: str) -> str:
        return self.page.evaluate(f"() => getComputedStyle(document.querySelector('#view .flow'))['{prop}']")

    DEFAULTS = {"fontSize": 18, "fontFamily": "serif", "theme": "system", "lineSpacing": 1.6, "columnWidth": 70, "textAlign": "start",
                "layout": "continuous", "coverAlone": True, "reduceMotion": False, "pdfAppearance": "match"}

    def saved_prefs(self) -> dict:
        """What is on disk in the general settings file (defaults where nothing was ever saved)."""
        path = self.tmp / "data" / "prefs.json"
        return {**self.DEFAULTS, **(json.loads(path.read_text())["values"] if path.is_file() else {})}

    def doc_prefs_row(self) -> dict:
        import sqlite3
        con = sqlite3.connect(f"file:{self.tmp / 'data' / 'reader.sqlite'}?mode=ro", uri=True)
        try:
            rows = [json.loads(r[0]) for r in con.execute("SELECT doc_prefs FROM documents WHERE doc_prefs IS NOT NULL")]
        finally:
            con.close()
        return rows[0] if rows else {}

    # ------------------------------------------------------------------ every control changes the page, immediately
    def test_every_text_document_control_changes_the_page_without_reopening(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()

        seen = {}
        for name, key in (("Light", "light"), ("Sepia", "sepia"), ("Dark", "dark"), ("High contrast", "contrast")):
            self.mode(name).click()
            pg.wait_for_function(f"() => document.documentElement.dataset.theme === '{key}'")
            seen[key] = pg.evaluate("() => getComputedStyle(document.querySelector('#view .flow')).backgroundColor")
        self.assertEqual(len(set(seen.values())), 4, f"each reading mode paints its own paper: {seen}")
        self.mode("Sepia").click()
        pg.wait_for_function("() => document.documentElement.dataset.theme === 'sepia'")

        self.select("Typeface").select_option("mono")
        pg.wait_for_function("() => /mono|courier|consolas/i.test(getComputedStyle(document.querySelector('#view .flow')).fontFamily)")
        self.select("Typeface").select_option("sans")
        pg.wait_for_function("() => /sans|helvetica|arial|segoe/i.test(getComputedStyle(document.querySelector('#view .flow')).fontFamily)")

        self.slider("Text size").fill("30")
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#view .flow')).fontSize === '30px'")
        self.slider("Line spacing").fill("2")
        pg.wait_for_function("() => parseFloat(getComputedStyle(document.querySelector('#view .flow')).lineHeight) / parseFloat(getComputedStyle(document.querySelector('#view .flow')).fontSize) > 1.95")
        before = pg.evaluate("() => document.querySelector('#view .flow').getBoundingClientRect().width")
        self.slider("Line length").fill("35")
        pg.wait_for_function(f"() => document.querySelector('#view .flow').getBoundingClientRect().width < {before} - 20")

        self.page.locator(".appearance .seg label:text-is('Justified')").click()
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#view .flow')).textAlign === 'justify'")

        self.page.locator(".appearance input[aria-label='Text colour']").fill("#336699")
        pg.wait_for_function("() => getComputedStyle(document.querySelector('#view .flow')).color.startsWith('rgb(51, 102, 153')")
        self.slider("Text opacity").fill("50")
        pg.wait_for_function("() => { const c = getComputedStyle(document.querySelector('#view .flow')).color; return /0\\.5/.test(c) || /\\/ 0\\.5/.test(c); }")
        self.page.locator(".appearance button:has-text('Use the mode')").click()
        pg.wait_for_function("() => !getComputedStyle(document.querySelector('#view .flow')).color.startsWith('rgb(51, 102, 153')")
        self.slider("Text opacity").fill("100")

        self.select("Page layout").select_option("single")
        pg.wait_for_selector("#view .pagedwrap")
        self.select("Page layout").select_option("spread")
        pg.wait_for_function("() => document.querySelectorAll('#view .pagedwrap .flow').length >= 2")
        cover = self.page.locator(".appearance .switch:has-text('cover on its own') input")
        expect(cover).to_be_enabled()
        cover.uncheck()
        self.wait_saved()
        self.assertFalse(cover.is_checked())
        self.assertFalse(self.saved_prefs()["coverAlone"])
        self.select("Page layout").select_option("continuous")
        expect(cover).to_be_disabled()
        self.assertIn("two-page spread", self.page.locator(".appearance .field.switchrow:has(.switch:has-text('cover on its own')) .hint").inner_text())

        # the panel is still the same panel: nothing reopened it, and the position/annotations were not disturbed
        expect(pg.locator(".appearance")).to_have_count(1)
        self.wait_saved()

        # everything chosen was saved, and survives a real restart of the service
        want = self.saved_prefs()
        self.assertEqual((want["theme"], want["fontFamily"], want["fontSize"], want["lineSpacing"], want["columnWidth"], want["textAlign"], want["layout"]),
                         ("sepia", "sans", 30, 2.0, 35, "justify", "continuous"))
        self.svc.restart()
        self.reload_app()
        self.open_from_library("Reading text")
        pg.wait_for_function("() => document.documentElement.dataset.theme === 'sepia'")
        self.assertEqual(self.flow_style("fontSize"), "30px")
        self.assertEqual(self.flow_style("textAlign"), "justify")
        self.open_panel()
        self.assertEqual(self.slider("Text size").input_value(), "30")
        expect(self.mode("Sepia").locator("xpath=preceding-sibling::input[1]")).to_be_checked()
        self.shot("J1_appearance_panel_text")

    def test_reduce_motion_setting_is_applied_and_saved(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()
        pg.locator(".appearance .switch:has-text('Reduce motion') input").check()
        pg.wait_for_function("() => document.documentElement.classList.contains('reduce-motion')")
        self.wait_saved()
        self.assertTrue(self.saved_prefs()["reduceMotion"])
        dur = pg.evaluate("() => [getComputedStyle(document.querySelector('.side')).transitionDuration, getComputedStyle(document.querySelector('.side-edge-toggle')).transitionDuration]")
        self.assertEqual(dur, ["0s", "0s"], "no sliding when the person asked for less motion")

    def test_motion_is_short_by_default_and_off_when_the_system_asks_for_less(self) -> None:
        pg = self.page
        self.open(self.sldc)
        ms = pg.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.side')).transitionDuration) * 1000")
        self.assertTrue(120 <= ms <= 220, f"the panel slides in {ms} ms")
        pg.emulate_media(reduced_motion="reduce")
        pg.wait_for_timeout(100)
        self.assertEqual(pg.evaluate("() => getComputedStyle(document.querySelector('.side')).transitionDuration"), "0s")

    # ------------------------------------------------------------------ scope: this document only, resets
    def test_document_only_scope_resets_and_independence(self) -> None:
        pg = self.page
        other = fx.make_sldc(self.docs / "other.sldc", paragraphs=10, title="Other text", doc_id="other-doc")
        self.open(self.sldc)
        self.open_panel()
        self.page.locator(".appearance .switch:has-text('this document only') input").check()
        self.slider("Text size").fill("26")
        self.select("Typeface").select_option("mono")
        self.wait_saved()
        self.assertEqual(self.doc_prefs_row().get("fontSize"), 26)
        self.assertEqual(self.saved_prefs()["fontSize"], 18, "a document-only change must not touch the general settings")
        self.assertEqual(self.flow_style("fontSize"), "26px")
        self.close_panel()

        # another document keeps the general look
        pg.click("button[aria-label='Back to library']")
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")
        self.open(other)
        self.assertEqual(self.flow_style("fontSize"), "18px")
        pg.click("button[aria-label='Back to library']")
        self.open_from_library("Reading text")
        self.assertEqual(self.flow_style("fontSize"), "26px", "the document's own size is back when it is reopened")

        # a general change releases the document's own value for that setting, so it shows at once
        self.open_panel()
        self.page.locator(".appearance .switch:has-text('this document only') input").uncheck()
        self.slider("Text size").fill("22")
        self.wait_saved()
        self.assertEqual(self.flow_style("fontSize"), "22px")
        self.assertEqual(self.saved_prefs()["fontSize"], 22)
        self.assertNotIn("fontSize", self.doc_prefs_row())
        self.assertEqual(self.doc_prefs_row().get("fontFamily"), "mono", "other document-only values are kept")

        # Reset this document
        self.page.locator(".appearance button:has-text('Reset this document')").click()
        self.wait_saved()
        pg.wait_for_function("() => !/mono|courier|consolas/i.test(getComputedStyle(document.querySelector('#view .flow')).fontFamily)")
        self.assertEqual(self.doc_prefs_row(), {})
        self.assertEqual(self.saved_prefs()["fontSize"], 22, "resetting a document leaves the general settings alone")

        # Reset everything asks first, inside the panel, and then restores the defaults
        self.page.locator(".appearance button:has-text('Reset everything')").click()
        expect(pg.locator(".appearance .confirm")).to_be_visible()
        self.page.locator(".appearance .confirm button:has-text('Cancel')").click()
        expect(pg.locator(".appearance")).to_have_count(1)
        self.assertEqual(self.saved_prefs()["fontSize"], 22)
        self.page.locator(".appearance button:has-text('Reset everything')").click()
        self.page.locator(".appearance .confirm button:text-is('Reset')").click()
        self.wait_saved()
        self.assertEqual(self.saved_prefs()["fontSize"], 18)
        self.assertEqual(self.slider("Text size").input_value(), "18")
        self.assertEqual(self.flow_style("fontSize"), "18px")

    # ------------------------------------------------------------------ formats: unsupported controls are off, with the reason
    def test_pdf_disables_text_layout_controls_and_says_why_but_modes_work(self) -> None:
        pg = self.page
        self.open(fx.make_pdf(self.docs / "paper.pdf", pages=4))
        self.open_panel()
        for label in ("Text size", "Line spacing", "Line length"):
            expect(self.slider(label)).to_be_disabled()
        expect(self.select("Typeface")).to_be_disabled()
        why = self.page.locator(".appearance .field:has(> label:has-text('Text size')) .hint").inner_text()
        self.assertIn("fixed layout", why.lower())
        self.assertTrue(self.page.locator(".appearance .field:has(> label:text-is('PDF colours'))").is_visible())
        before = pg.evaluate("() => getComputedStyle(document.querySelector('#view')).backgroundColor")
        self.mode("Dark").click()
        pg.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        self.assertNotEqual(before, pg.evaluate("() => getComputedStyle(document.querySelector('#view')).backgroundColor"))
        self.select("PDF colours").select_option("original")
        self.wait_saved()
        self.assertEqual(self.saved_prefs()["pdfAppearance"], "original")
        self.select("Page layout").select_option("single")
        expect(pg.locator(".pagehost")).to_have_count(1)
        self.shot("J2_pdf_panel")

    # ------------------------------------------------------------------ failures
    def break_storage(self) -> None:
        path = self.tmp / "data" / "prefs.json"
        path.unlink(missing_ok=True)
        path.mkdir()                                             # a folder where the file belongs: the real service cannot replace it

    def repair_storage(self) -> None:
        shutil.rmtree(self.tmp / "data" / "prefs.json")

    def test_failed_save_is_reported_keeps_confirmed_values_and_can_be_retried(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()
        self.slider("Text size").fill("20")
        self.wait_saved()
        panel = pg.locator(".appearance").element_handle()
        self.break_storage()
        self.slider("Text size").fill("34")
        pg.wait_for_selector(".appearance .ap-error", timeout=8000)
        err = pg.locator(".appearance .ap-error")
        self.assertEqual(err.get_attribute("role"), "alert")
        self.assertIn("Not saved", err.inner_text())
        self.assertEqual(pg.locator(".appearance .save-status").get_attribute("data-phase"), "error")
        self.assertNotEqual(pg.locator(".appearance .save-status").inner_text(), "Saved")
        # the page and the control are back on what was confirmed, in the same panel
        pg.wait_for_function("() => document.querySelector('.appearance input[type=range]') !== null")
        self.assertEqual(self.flow_style("fontSize"), "20px")
        self.assertEqual(self.slider("Text size").input_value(), "20")
        expect(pg.locator(".appearance")).to_have_count(1)
        self.assertTrue(panel.evaluate("e => e.isConnected"), "the same panel element is still on screen: it was not rebuilt")
        # the service named the cause (not "across volumes") and the console carries the details
        self.assertNotIn("across volumes", err.inner_text())
        self.shot("J3_save_error")
        # repair and retry: the same change goes through, and the status turns to Saved
        self.repair_storage()
        pg.locator(".appearance .ap-error button:has-text('Try again')").click()
        pg.wait_for_selector(".appearance .ap-error", state="hidden", timeout=8000)
        self.wait_saved()
        self.assertEqual(self.saved_prefs()["fontSize"], 34)
        self.assertEqual(self.flow_style("fontSize"), "34px")
        self.assertEqual(self.slider("Text size").input_value(), "34")
        self.errors[:] = [e for e in self.errors if "500" not in e and "Failed to load resource" not in e]

    expected_errors = ("Failed to load resource",)

    def test_failed_save_with_the_panel_closed_is_announced_and_nothing_pretends_it_saved(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()
        self.slider("Text size").fill("20"); self.wait_saved()
        self.break_storage()
        self.slider("Text size").fill("33")
        self.close_panel()                                       # closed before the save was even sent
        pg.wait_for_selector(".toast", timeout=8000)
        self.assertIn("Could not save", pg.inner_text(".toast"))
        self.assertEqual(self.flow_style("fontSize"), "20px")
        self.open_panel()
        self.assertEqual(self.slider("Text size").input_value(), "20")

    # ------------------------------------------------------------------ rapid changes
    def test_a_slider_drag_sends_few_requests_and_ends_in_the_last_value(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()
        puts: list[str] = []
        pg.on("request", lambda r: puts.append(r.url) if r.method == "PUT" and "/prefs" in r.url else None)
        self.slider("Text size").focus()
        for _ in range(20):
            pg.keyboard.press("ArrowRight")                      # 18 -> 38, one real key event after another
        self.wait_saved()
        self.assertEqual(self.slider("Text size").input_value(), "38")
        self.assertEqual(self.flow_style("fontSize"), "38px")
        self.assertEqual(self.saved_prefs()["fontSize"], 38)
        self.assertLess(len(puts), 10, f"20 key presses should not become 20 requests: {len(puts)}")

    def test_switching_scope_between_two_quick_changes_keeps_each_in_its_scope(self) -> None:
        pg = self.page
        self.open(self.sldc)
        self.open_panel()
        switch = self.page.locator(".appearance .switch:has-text('this document only') input")
        self.slider("Text size").fill("24")
        switch.check()
        self.select("Typeface").select_option("mono")
        self.wait_saved()
        self.assertEqual(self.saved_prefs()["fontSize"], 24)
        self.assertEqual(self.saved_prefs()["fontFamily"], "serif")
        self.assertEqual(self.doc_prefs_row(), {"fontFamily": "mono"})

    # ------------------------------------------------------------------ the pop-up and the page beneath it
    def test_clicking_away_from_the_panel_does_not_draw_or_select(self) -> None:
        pg = self.page
        self.open(self.sldc)
        pg.click(".toolbar button[aria-label='Show annotation tools']")
        pg.click(".toolbar button[aria-label='Pen']")
        self.open_panel()
        a = pg.locator("#view .b[data-n='3']").bounding_box()
        pg.mouse.move(a["x"] + 30, a["y"] + 20); pg.mouse.down(); pg.mouse.move(a["x"] + 180, a["y"] + 60, steps=10); pg.mouse.up()
        pg.wait_for_selector(".appearance", state="detached")
        pg.wait_for_timeout(700)
        self.assertEqual(self.rows("ink"), [], "the click that closes the panel must not start a drawing")
        # with the panel closed, the same drag does draw
        pg.mouse.move(a["x"] + 30, a["y"] + 20); pg.mouse.down(); pg.mouse.move(a["x"] + 180, a["y"] + 60, steps=10); pg.mouse.up()
        pg.wait_for_timeout(700)
        self.assertEqual(len(self.rows("ink")), 1)
        # and interacting inside the panel never draws
        self.open_panel()
        s = self.slider("Text size").bounding_box()
        pg.mouse.move(s["x"] + 10, s["y"] + 8); pg.mouse.down(); pg.mouse.move(s["x"] + 120, s["y"] + 8, steps=6); pg.mouse.up()
        self.wait_saved()
        pg.wait_for_timeout(500)
        self.assertEqual(len(self.rows("ink")), 1)

    def test_panel_works_from_the_library_page_for_all_documents(self) -> None:
        pg = self.page
        pg.click("button:has-text('Appearance')")
        pg.wait_for_selector(".appearance")
        switch = pg.locator(".appearance .switch:has-text('this document only') input")
        expect(switch).to_be_disabled()
        self.assertIn("Open a document", pg.locator(".appearance .field.switchrow:has(.switch:has-text('this document only')) .hint").inner_text())
        self.mode("Dark").click()
        pg.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        self.wait_saved()
        self.assertEqual(self.saved_prefs()["theme"], "dark")
        self.assertIn("every document", pg.locator(".appearance .scope > .hint").inner_text())
        pg.keyboard.press("Escape")
        self.open(self.sldc)
        self.assertEqual(pg.evaluate("() => document.documentElement.dataset.theme"), "dark")

    # ------------------------------------------------------------------ geometry at the supported widths
    def test_panel_fits_the_viewport_without_sideways_scrolling(self) -> None:
        pg = self.page
        self.open(self.sldc)
        for width in (1920, 1440, 1280, 1024, 768, 420):
            pg.set_viewport_size({"width": width, "height": 800})
            pg.wait_for_timeout(250)
            self.open_panel()
            box = pg.evaluate("""() => { const r = document.querySelector('.popover.appearance-pop').getBoundingClientRect();
                const body = document.querySelector('.ap-body');
                return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, sx: body.scrollWidth - body.clientWidth, page: document.documentElement.scrollWidth - innerWidth}; }""")
            self.assertLessEqual(box["w"], 420, width)
            self.assertGreaterEqual(box["l"], 0, width); self.assertLessEqual(box["r"], width, width)
            self.assertGreaterEqual(box["t"], 0, width); self.assertLessEqual(box["b"], 800, width)
            self.assertLessEqual(box["sx"], 1, f"{width}px: the panel body scrolls sideways")
            self.assertLessEqual(box["page"], 0, f"{width}px: the page scrolls sideways")
            if width in (1440, 768):
                self.shot(f"J4_panel_{width}")
            self.close_panel()

    def test_panel_is_usable_by_keyboard_and_names_its_controls(self) -> None:
        pg = self.page
        self.open(self.sldc)
        pg.focus("button[aria-label='Reading appearance']")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".appearance")
        focused = pg.evaluate("() => document.activeElement && document.activeElement.type")
        self.assertEqual(focused, "radio")
        pg.keyboard.press("ArrowRight")                          # moves along the reading modes
        self.wait_saved()
        pg.keyboard.press("Escape")
        pg.wait_for_selector(".appearance", state="detached")
        self.assertEqual(pg.evaluate("() => document.activeElement.getAttribute('aria-label')"), "Reading appearance")
        self.open_panel()
        unnamed = pg.evaluate("""() => [...document.querySelectorAll('.appearance input, .appearance select, .appearance button')]
            .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.textContent.trim()).length""")
        self.assertEqual(unnamed, 0, "every control has an accessible name")


class SidePanelAndHeader(E2E):
    def test_edge_toggle_follows_the_panel_boundary_with_the_right_icons(self) -> None:
        pg = self.page
        self.open(fx.make_sldc(self.docs / ("a-very-long-file-name-" + "x" * 90 + ".sldc"), title="A very long title " * 8))
        toggle = pg.locator("button.side-edge-toggle")
        self.assertEqual(toggle.get_attribute("aria-label"), "Show side panel")
        self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
        toggle.click()
        pg.wait_for_timeout(350)
        self.assertEqual(toggle.get_attribute("aria-label"), "Hide side panel")
        side = pg.locator(".side").bounding_box()
        edge = toggle.bounding_box()
        self.assertAlmostEqual(edge["x"], side["x"] + side["width"], delta=2, msg="the toggle sits on the panel's right edge")
        self.shot("J5_sidebar_open_long_title")
        # the header never overflows horizontally, however long the title is
        over = pg.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        self.assertLessEqual(over, 0)
        pg.click("button.side-edge-toggle")
        pg.wait_for_timeout(350)
        expect(pg.locator(".side")).to_be_hidden()
        self.assertEqual(pg.locator("button.side-edge-toggle").bounding_box()["x"], 0)
        # a hidden panel cannot take keyboard focus
        pg.keyboard.press("Tab")
        self.assertFalse(pg.evaluate("() => !!document.activeElement.closest('.side')"))


if __name__ == "__main__":
    unittest.main()
