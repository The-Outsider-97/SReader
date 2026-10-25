"""Real-browser harness: starts the actual Reader service on a free loopback port with a throw-away data folder, and drives the built
client in Chromium through Playwright with real mouse and keyboard input. Nothing is stubbed.

Run:  python -m unittest discover -s tests_e2e -v        (needs: pip install playwright; the Chromium that Playwright finds)
Screenshots of each workflow land in docs/screenshots/.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(APP / "tests_py"))
import fixtures as fx  # noqa: E402

SHOTS = APP / "docs" / "screenshots"
TOKEN = "e2e-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Service:
    """The real service in a subprocess (so a restart test really restarts it)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.log = root / "service.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={TOKEN}"

    def start(self) -> None:
        env = dict(os.environ)
        if fx.ROOT:
            env["SLAI_ROOT"] = str(fx.ROOT)
        cmd = [sys.executable, "-m", "slai_reader", "--no-browser", "--port", str(self.port), "--token", TOKEN,
               "--data-dir", str(self.root / "data"), "--output-dir", str(self.root / "out"), "--agents", "none"]
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(cmd, cwd=str(APP), env=env, stdout=out, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"service exited: {self.log.read_text(errors='replace')[-800:]}")
                time.sleep(0.1)
        raise RuntimeError("service did not start")

    def restart(self) -> None:
        """A real restart: the process is stopped and a new one started on a fresh port (the old one may linger in TIME_WAIT)."""
        self.stop()
        self.port = free_port()
        self.start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class E2E(unittest.TestCase):
    """One service + one browser per test class; each test gets a fresh page and clean data."""
    pw = None
    browser = None

    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright
        if not (APP / "dist" / "index.html").is_file():
            raise unittest.SkipTest("the client is not built (run: node tools/build-static.mjs)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser:
            cls.browser.close()
        if cls.pw:
            cls.pw.stop()

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reader-e2e-"))
        self.docs = self.tmp / "docs"
        self.docs.mkdir()
        self.svc = Service(self.tmp)
        self.svc.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.svc.stop)
        self.errors: list[str] = []
        self.ctx = self.browser.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True)
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
        self.page.goto(self.svc.url)
        self.page.wait_for_selector("input[aria-label='Full path of a file to open']")

    def tearDown(self) -> None:
        # a client error is a failure, with one exception: tests that provoke a refusal check for it themselves
        bad = [e for e in self.errors if not any(ok in e for ok in self.expected_errors)]
        self.assertEqual(bad, [], "the browser reported errors")

    expected_errors: tuple[str, ...] = ()

    # ---------------------------------------------------------------- helpers
    def shot(self, name: str) -> None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(SHOTS / f"{name}.png"))

    def open(self, path: Path, wait: str = ".toolbar") -> None:
        pg = self.page
        pg.fill("input[aria-label='Full path of a file to open']", str(path))
        pg.click("text=Open path")
        pg.wait_for_selector(wait, timeout=15000)
        pg.wait_for_selector(".status [role=status]:not(:empty)", timeout=15000)

    def open_from_library(self, title: str) -> None:
        card = self.page.locator(f"article.card[aria-label='{title}']")
        card.locator("button.primary").click()
        self.page.wait_for_selector(".toolbar", timeout=15000)
        self.page.wait_for_selector(".status [role=status]:not(:empty)", timeout=15000)

    def focus_view(self) -> None:
        self.page.focus("#view")

    def reload_app(self) -> None:
        self.page.goto(self.svc.url)
        self.page.wait_for_selector("input[aria-label='Full path of a file to open'], .toolbar")

    def words(self, page_no: int = 0):
        return self.page.query_selector_all(f".pagehost[data-page='{page_no}'] .textlayer span")

    def drag_words(self, first: int, last: int, page_no: int = 0) -> str:
        """Select with the real mouse from the start of word `first` to the end of word `last` on one text line."""
        pg = self.page
        pg.wait_for_selector(f".pagehost[data-page='{page_no}'] .textlayer span:nth-child({last + 1})", state="attached", timeout=15000)
        pg.wait_for_selector(f".pagehost[data-page='{page_no}'] img.page[src]", state="attached", timeout=15000)
        spans = self.words(page_no)
        a = spans[first].bounding_box(); z = spans[last].bounding_box()
        pg.mouse.move(a["x"] + 1, a["y"] + a["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(z["x"] + z["width"] - 1, z["y"] + z["height"] / 2, steps=8)
        pg.mouse.up()
        pg.wait_for_timeout(450)
        return pg.evaluate("String(getSelection())")

    def rows(self, kind: str | None = None) -> list[dict]:
        """The saved annotations, read straight from the SQLite file (independent of the API and the page)."""
        import json
        import sqlite3
        db = self.tmp / "data" / "reader.sqlite"
        if not db.is_file():
            return []
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            q = "SELECT kind, data, deleted FROM annotations WHERE deleted=0" + (" AND kind=?" if kind else "")
            out = []
            for k, data, _ in con.execute(q, (kind,) if kind else ()):
                d = json.loads(data)
                d.setdefault("kind", k)
                out.append(d)
            return out
        finally:
            con.close()

    def status_text(self) -> str:
        return self.page.inner_text(".status")

    # ---------------------------------------------------------------- text documents (flow view)
    def char_point(self, block: int, offset: int, end: bool = False) -> tuple[float, float]:
        """Screen position of a character boundary inside block `block` (UTF-16 offset into the visible text)."""
        return tuple(self.page.evaluate("""([n, off]) => {
            const txt = document.querySelector(`#view .b[data-n="${n}"] .txt`);
            const w = document.createTreeWalker(txt, NodeFilter.SHOW_TEXT);
            let seen = 0;
            for (let node = w.nextNode(); node; node = w.nextNode()) {
                if (off <= seen + node.length) {
                    const r = document.createRange(); r.setStart(node, off - seen); r.setEnd(node, off - seen);
                    const c = r.getClientRects()[0] || r.getBoundingClientRect();
                    return [c.left, c.top + (c.height || 14) / 2];
                }
                seen += node.length;
            }
            const b = txt.getBoundingClientRect(); return [b.right, b.top + 8];
        }""", [block, offset]))

    def drag_text(self, block_a: int, off_a: int, block_b: int, off_b: int) -> str:
        pg = self.page
        pg.wait_for_selector(f"#view .b[data-n='{block_a}'] .txt")
        pg.wait_for_selector(f"#view .b[data-n='{block_b}'] .txt")
        pg.locator(f"#view .b[data-n='{block_a}']").scroll_into_view_if_needed()
        pg.wait_for_timeout(150)
        ax, ay = self.char_point(block_a, off_a)
        bx, by = self.char_point(block_b, off_b)
        if not (0 < by < 840):                                           # the far end is off screen: bring it in and re-measure the start
            pg.locator(f"#view .b[data-n='{block_b}']").scroll_into_view_if_needed()
            pg.wait_for_timeout(150)
            ax, ay = self.char_point(block_a, off_a)
            bx, by = self.char_point(block_b, off_b)
        pg.mouse.move(ax, ay); pg.mouse.down(); pg.mouse.move(bx, by, steps=10); pg.mouse.up()
        pg.wait_for_timeout(450)
        return pg.evaluate("String(getSelection())")
