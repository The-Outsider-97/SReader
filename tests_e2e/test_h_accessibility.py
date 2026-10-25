"""Workflow H - accessibility, measured on the running app (no external checker is available offline, so these are our own rules, stated plainly):

* every control exposed to assistive technology has a name and a role (read from Chromium's real accessibility tree)
* text contrast in each reading mode meets WCAG 2.2 AA (4.5:1, or 3:1 for large text)
* every control can be reached and used with the keyboard alone, focus is visible, dialogs keep focus and give it back
* the layout reflows at 200 % zoom (640 px wide) without sideways scrolling, targets are at least 24 px
* reduced motion and forced colours are honoured
"""
from __future__ import annotations

import unittest

from harness import E2E, fx

INTERACTIVE = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "slider", "tab", "menuitem", "switch", "spinbutton", "listbox", "option"}

CONTRAST_JS = """() => {
  const parse = (c) => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const blend = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
  const backdrop = (el) => { const chain = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { chain.push(c); if (c.a >= 1) break; } }
    let base = { r: 255, g: 255, b: 255, a: 1 }; for (const c of chain.reverse()) base = blend(c, base); return base; };
  const out = []; const seen = new Set();
  const root = document.querySelector('.app') || document.body;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
    if (!n.textContent.trim()) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) continue;
    if (el.closest('.pagehost, .textlayer, svg, [aria-hidden="true"]')) continue;       // the picture of a PDF page is the document's own colours
    if (el.closest('button:disabled, [disabled]')) continue;                                  // disabled controls are exempt from the contrast rule
    const fg = parse(cs.color); if (!fg) continue;
    const bg = backdrop(el); const f = blend({ ...fg, a: fg.a * (parseFloat(cs.opacity) || 1) }, bg);
    const l1 = lum(f), l2 = lum(bg); const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const px = parseFloat(cs.fontSize); const large = px >= 24 || (px >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
    const need = large ? 3 : 4.5;
    if (ratio < need) out.push({ text: n.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100, need, cls: el.className.toString().slice(0, 40), tag: el.tagName });
  }
  return out;
}"""


class WorkflowH(E2E):
    def ax_nodes(self):
        cdp = self.ctx.new_cdp_session(self.page)
        cdp.send("Accessibility.enable")
        nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
        cdp.detach()
        return nodes

    def unnamed_controls(self):
        bad = []
        for n in self.ax_nodes():
            role = (n.get("role") or {}).get("value")
            if n.get("ignored") or role not in INTERACTIVE:
                continue
            name = ((n.get("name") or {}).get("value") or "").strip()
            if not name:
                bad.append((role, n.get("backendDOMNodeId")))
        return bad

    def describe_nodes(self, ids):
        cdp = self.ctx.new_cdp_session(self.page)
        out = []
        for _, bid in ids:
            try:
                out.append(cdp.send("DOM.describeNode", {"backendNodeId": bid})["node"]["nodeName"])
            except Exception:
                out.append("?")
        cdp.detach()
        return out

    def open_pdf(self):
        self.open(fx.make_pdf(self.docs / "a11y.pdf", pages=4))

    # ------------------------------------------------------------------ names and roles
    def test_every_control_has_a_name_library_and_document(self):
        pg = self.page
        self.assertEqual(self.unnamed_controls(), [], "library view")
        self.open_pdf()
        pg.click("button[aria-label='Show or hide the side panel']")
        for tab in ("Contents", "Pages", "Notes", "Search"):
            pg.click(f"[role=tab]:text-is('{tab}')")
            pg.wait_for_timeout(300)
            bad = self.unnamed_controls()
            self.assertEqual(self.describe_nodes(bad), [], f"{tab} panel: unnamed controls")
        pg.click("button[aria-label='Reading mode, text and layout']")
        self.assertEqual(self.describe_nodes(self.unnamed_controls()), [], "appearance panel")
        pg.keyboard.press("Escape")
        pg.click("button[aria-label='Export or import notes']")
        self.assertEqual(self.describe_nodes(self.unnamed_controls()), [], "export dialog")
        pg.keyboard.press("Escape")
        pg.click("button[aria-label='Pen']")
        self.assertEqual(self.describe_nodes(self.unnamed_controls()), [], "pen options")

    def test_landmarks_and_document_semantics(self):
        pg = self.page
        self.assertEqual(pg.locator("h1").count(), 1, "the library has one top-level heading")
        self.assertEqual(pg.locator("html").get_attribute("lang"), "en")
        self.open_pdf()
        self.assertEqual(pg.locator("[role=toolbar]").first.get_attribute("aria-label"), "Reader tools")
        self.assertEqual(pg.locator("#view").get_attribute("role"), "document")
        self.assertTrue(pg.locator("#view").get_attribute("aria-label"))
        self.assertTrue(pg.locator("[role=status]").count() >= 1, "there is a live region for announcements")
        # the page images carry labels, and the toolbar buttons expose pressed state
        self.assertEqual(pg.locator(".pagehost[data-page='0']").get_attribute("aria-label"), "Page 1 of 4")
        pg.click("button[aria-label='Pen']")
        self.assertEqual(pg.locator("button[aria-label='Pen']").get_attribute("aria-pressed"), "true")
        self.assertEqual(pg.locator("button[aria-label='Select text']").get_attribute("aria-pressed"), "false")

    # ------------------------------------------------------------------ contrast in every mode
    def test_contrast_in_every_reading_mode(self):
        pg = self.page
        self.open_pdf()
        pg.click("button[aria-label='Show or hide the side panel']")
        pg.click("[role=tab]:text-is('Notes')")
        self.drag_words(5, 9)
        pg.click(".seltoolspop button[aria-label='Add a note to the selection']")
        pg.fill(".noteedit textarea", "Contrast check note")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_selector(".status >> text=1 annotation")
        report = {}
        for name in ("Light", "Sepia", "Dark", "High contrast"):
            pg.click("button[aria-label='Reading mode, text and layout']")
            pg.click(f"label:has(strong:text-is('{name}'))")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(400)
            failures = pg.evaluate(CONTRAST_JS)
            report[name] = failures
        self.assertEqual(report, {k: [] for k in report}, "text below WCAG AA contrast")

    def test_contrast_of_flow_text_in_every_mode(self):
        pg = self.page
        self.open(fx.make_sldc(self.docs / "a11y.sldc", paragraphs=6))
        pg.wait_for_selector("#view .b .txt")
        report = {}
        for name in ("Light", "Sepia", "Dark", "High contrast"):
            pg.click("button[aria-label='Reading mode, text and layout']")
            pg.click(f"label:has(strong:text-is('{name}'))")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(400)
            report[name] = pg.evaluate(CONTRAST_JS.replace("closest('.pagehost, .textlayer, svg, [aria-hidden=\"true\"]')", "closest('svg, [aria-hidden=\"true\"]')"))
        self.assertEqual(report, {k: [] for k in report}, "text below WCAG AA contrast")

    # ------------------------------------------------------------------ keyboard only
    def test_keyboard_only_open_read_annotate(self):
        pg = self.page
        pdf = fx.make_pdf(self.docs / "kb.pdf", pages=4)
        pg.focus("input[aria-label='Full path of a file to open']")
        pg.keyboard.type(str(pdf))
        pg.keyboard.press("Tab")
        self.assertEqual(pg.evaluate("document.activeElement.textContent"), "Open path")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".toolbar")
        pg.wait_for_selector(".status [role=status]:not(:empty)")
        # focus lands on the document so arrows and PageDown work straight away
        self.assertEqual(pg.evaluate("document.activeElement.id"), "view")
        pg.keyboard.press("Control+=")
        pg.wait_for_timeout(300)
        # every control is reachable with Tab, and focus is always visible
        pg.focus(".toolbar button")
        seen, invisible = [], []
        for _ in range(80):
            pg.keyboard.press("Tab")
            if pg.evaluate("document.activeElement === document.body"):
                break                                                           # focus left the page (to the browser's own controls)
            info = pg.evaluate("""() => { const e = document.activeElement; const cs = getComputedStyle(e);
                const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== 'none';
                return { tag: e.tagName, label: e.getAttribute('aria-label') || e.textContent.trim().slice(0, 20) || e.id, ring, vis: e.getBoundingClientRect().width > 0 }; }""")
            seen.append(info["label"])
            if not info["ring"]:
                invisible.append(info)
        self.assertGreater(len(set(seen)), 20, "a keyboard user can reach the toolbar, the panel and the status controls")
        self.assertEqual(invisible, [], "every focused control shows a focus indicator")

    def test_dialog_traps_focus_and_returns_it(self):
        pg = self.page
        self.open_pdf()
        pg.focus("button[aria-label='Export or import notes']")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".dialog")
        inside = []
        for _ in range(12):
            pg.keyboard.press("Tab")
            inside.append(pg.evaluate("!!document.activeElement.closest('.dialog')"))
        self.assertTrue(all(inside), "Tab must stay inside an open dialog")
        pg.keyboard.press("Escape")
        pg.wait_for_selector(".dialog", state="detached")
        self.assertEqual(pg.evaluate("document.activeElement.getAttribute('aria-label')"), "Export or import notes", "focus goes back to where it was")

    def test_popover_escape_returns_focus(self):
        pg = self.page
        self.open_pdf()
        pg.focus("button[aria-label='Reading mode, text and layout']")
        pg.keyboard.press("Enter")
        pg.wait_for_selector(".popover")
        pg.keyboard.press("Escape")
        pg.wait_for_selector(".popover", state="detached")
        self.assertEqual(pg.evaluate("document.activeElement.getAttribute('aria-label')"), "Reading mode, text and layout")

    def test_selecting_and_marking_text_without_a_mouse(self):
        for kind in ("sldc", "pdf"):
            with self.subTest(kind):
                pg = self.page
                pg.goto(self.svc.url)
                self.open(fx.make_sldc(self.docs / "kb.sldc", paragraphs=4) if kind == "sldc" else fx.make_pdf(self.docs / "kb.pdf", pages=2))
                if kind == "pdf":
                    pg.wait_for_selector(".pagehost[data-page='0'] .textlayer span:nth-child(20)", state="attached")
                    pg.wait_for_selector(".pagehost[data-page='0'] img.page[src]", state="attached")
                else:
                    pg.wait_for_selector("#view .b .txt")
                self.focus_view()
                for _ in range(14):
                    pg.keyboard.press("Shift+ArrowRight")                      # no mouse, no caret-browsing mode: this starts a selection by itself
                pg.keyboard.press("Control+Shift+ArrowRight")                  # and Ctrl moves by whole words
                pg.wait_for_selector(".seltoolspop")                           # the same tools appear as for a mouse selection
                before = len(self.rows("highlight"))
                pg.keyboard.press("Control+Shift+h")
                pg.wait_for_selector(".status >> text=1 annotation")
                rows = self.rows("highlight")
                self.assertEqual(len(rows), before + 1)
                quote = rows[-1]["anchor"]["segments"][0]["quote"]
                self.assertGreaterEqual(len(quote), 14, quote)
                self.svc.restart()

    # ------------------------------------------------------------------ reflow, targets, motion, forced colours
    def test_reflow_at_200_percent_and_target_sizes(self):
        pg = self.page
        self.open_pdf()
        pg.set_viewport_size({"width": 640, "height": 800})         # 1280 px at 200 % browser zoom
        pg.wait_for_timeout(500)
        overflow = pg.evaluate("() => ({ doc: document.documentElement.scrollWidth - document.documentElement.clientWidth, app: document.querySelector('.app').scrollWidth - document.querySelector('.app').clientWidth })")
        self.shot("H1_reflow_640")
        self.assertLessEqual(overflow["doc"], 1, f"the page must not scroll sideways at 200 % zoom: {overflow}")
        small = pg.evaluate("""() => [...document.querySelectorAll('.toolbar button, .toolbar select, .toolbar input, .status input')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.width < 24 || r.height < 24); })
            .map((e) => (e.getAttribute('aria-label') || e.textContent) + ':' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height))""")
        self.assertEqual(small, [], "touch/click targets must be at least 24x24 CSS px")
        self.shot("H1_reflow_640")

    def test_reduced_motion_and_forced_colors(self):
        pg = self.page
        pg.emulate_media(reduced_motion="reduce")
        self.open_pdf()
        pg.click("button[aria-label='Show or hide the side panel']")
        durations = pg.evaluate("""() => [...document.querySelectorAll('*')].map((e) => getComputedStyle(e)).filter((c) => parseFloat(c.animationDuration) > 0.05 && c.animationName !== 'none').length""")
        self.assertEqual(durations, 0, "no animation runs when the person asked for reduced motion")
        pg.emulate_media(reduced_motion="no-preference", forced_colors="active")
        pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate("matchMedia('(forced-colors: active)').matches"), True)
        self.shot("H2_forced_colors")
        self.assertEqual(self.describe_nodes(self.unnamed_controls()), [])

    def test_system_dark_mode_is_followed_when_asked(self):
        pg = self.page
        pg.emulate_media(color_scheme="dark")
        pg.reload()
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")
        dark = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        pg.emulate_media(color_scheme="light")
        pg.reload()
        pg.wait_for_selector("input[aria-label='Full path of a file to open']")
        light = pg.evaluate("getComputedStyle(document.body).backgroundColor")
        self.assertNotEqual(dark, light, "the library follows the system colour scheme")


if __name__ == "__main__":
    unittest.main()
