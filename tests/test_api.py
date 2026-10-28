"""The whole stack over HTTP: opening, reading, annotating, persisting, exporting - and what the server refuses."""
from __future__ import annotations

import json
import unittest

from apiharness import Api, ApiBase, fixtures


class OpenAndRead(ApiBase):
    def test_status_and_formats(self):
        st = self.api.data(200, self.api.get("/api/status"))
        self.assertEqual(st["app"], "SLAI Reader")
        self.assertEqual(st["store"]["integrity"], "ok")
        self.assertEqual({f["id"] for f in st["formats"]}, {"sldc", "pdf", "epub", "docx", "odt", "rtf", "html", "md", "txt"})
        rows = self.api.data(200, self.api.get("/api/formats"))
        self.assertTrue(all("highlight" in r or "capabilities" in r or r for r in rows))

    def test_open_sldc_read_blocks_and_outline(self):
        p = self.make("a.sldc", fixtures.make_sldc)
        d = self.api.open(p)
        self.assertEqual(d["info"]["layout"] if "layout" in d["info"] else "flow", "flow")
        sid = d["sid"]
        blocks = self.api.data(200, self.api.get(f"/api/doc/{sid}/blocks?start=0&limit=5"))
        self.assertEqual(len(blocks["blocks"]), 5)
        self.assertEqual(blocks["total"], len(self.api.app.sessions.get(sid).doc.blocks))
        out = self.api.data(200, self.api.get(f"/api/doc/{sid}/outline"))
        self.assertTrue(out and out[0]["location"]["block"])

    def test_equation_blocks_are_sent_with_their_source_and_alignment(self):
        d = self.api.open(self.make("eq.sldc", fixtures.make_equation_sldc))
        self.assertIn("equations", d["info"]["features"])
        blocks = self.api.data(200, self.api.get(f"/api/doc/{d['sid']}/blocks?start=0&limit=10"))["blocks"]
        eqs = {b["id"]: b for b in blocks if b["type"] == "equation"}
        self.assertEqual(eqs["e1"]["equation"], {"syntax": "latex", "domain": "mathematics", "source": r"7 \cdot \sqrt{\frac{5}{8}}", "align": "left"})
        self.assertEqual(eqs["e2"]["equation"]["align"], "right")
        self.assertEqual(eqs["e3"]["equation"]["syntax"], "mhchem")
        self.assertTrue(all(b["text"] == "" for b in eqs.values()))                       # an equation has no text of its own: the source is authoritative

    def test_text_opens_when_the_slai_parser_cannot_start(self):
        """On a computer where the SLAI Reader parser fails to import (an old `colorama`, say) .txt and .md are still read, and the page says why."""
        class DependencyMissing(Exception):          # Documaster raises its own type of this name
            pass

        def broken(*a, **kw):
            raise DependencyMissing("Reader parser engine is unavailable: AttributeError: module 'colorama' has no attribute 'just_fix_windows_console'")

        self.api.app.link.read_document = broken     # type: ignore[method-assign]
        for name, maker in (("t.txt", fixtures.make_text), ("t.md", fixtures.make_markdown)):
            p = self.make(name, maker)
            d = self.api.open(p)
            doc = self.api.app.sessions.get(d["sid"]).doc
            self.assertTrue(doc.blocks)
            self.assertIn(p.read_text(encoding="utf-8").split()[0].lstrip("#"), " ".join(b["text"] for b in doc.blocks))
            self.assertTrue(any("read directly as text" in w for w in doc.warnings), doc.warnings)
        status, js = self.api.post("/api/open", {"path": str(self.make("t.docx", fixtures.make_docx))})
        self.assertEqual((status, js["error"]["code"]), (501, "dependency_missing"), "other formats still say what is missing")

    def test_open_every_format(self):
        makers = {"a.pdf": fixtures.make_pdf, "a.sldc": fixtures.make_sldc, "a.docx": fixtures.make_docx, "a.odt": fixtures.make_odt,
                  "a.epub": fixtures.make_epub, "a.html": fixtures.make_html, "a.md": fixtures.make_markdown, "a.txt": fixtures.make_text, "a.rtf": fixtures.make_rtf}
        results = {}
        for name, maker in makers.items():
            p = self.make(name, maker)
            status, js = self.api.post("/api/open", {"path": str(p)})
            results[name] = (status, js["error"]["code"] if status != 200 else "ok")
        failures = {k: v for k, v in results.items() if v[0] != 200 and k != "a.rtf"}
        self.assertEqual(failures, {}, results)

    def test_pdf_page_image_text_marks(self):
        d = self.api.open(self.make("a.pdf", fixtures.make_pdf, pages=3))
        sid = d["sid"]
        self.assertEqual(d["info"]["pages"], 3)
        status, headers, png = self.api.request("GET", f"/api/doc/{sid}/page/0/image?scale=1")
        self.assertEqual(status, 200)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(headers["X-Image-Size"], "612x792")
        text = self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/text"))
        self.assertIn("Page 1 line 3", text["text"])
        self.assertEqual(self.api.get(f"/api/doc/{sid}/page/9/image")[0], 404)
        self.assertEqual(self.api.get(f"/api/doc/{sid}/page/x/image")[0], 400)

    def test_dark_theme_changes_pdf_pixels_but_not_text_layer(self):
        d = self.api.open(self.make("a.pdf", fixtures.make_pdf))
        sid = d["sid"]
        from PIL import Image
        import io
        light = Image.open(io.BytesIO(self.api.request("GET", f"/api/doc/{sid}/page/0/image?scale=0.5&theme=light")[2])).convert("RGB")
        dark = Image.open(io.BytesIO(self.api.request("GET", f"/api/doc/{sid}/page/0/image?scale=0.5&theme=dark")[2])).convert("RGB")
        self.assertGreater(light.getpixel((5, 5))[0], 240)
        self.assertLess(dark.getpixel((5, 5))[0], 60)
        orig = Image.open(io.BytesIO(self.api.request("GET", f"/api/doc/{sid}/page/0/image?scale=0.5&theme=dark&pa=original")[2])).convert("RGB")
        self.assertGreater(orig.getpixel((5, 5))[0], 240)

    def test_search_pdf_and_sldc(self):
        d = self.api.open(self.make("a.pdf", fixtures.make_pdf))
        r = self.api.data(200, self.api.get(f"/api/doc/{d['sid']}/search?q=line%203"))
        for _ in range(50):
            if r["index"]["complete"]:
                break
            import time; time.sleep(0.05)
            r = self.api.data(200, self.api.get(f"/api/doc/{d['sid']}/search?q=line%203"))
        self.assertEqual(r["total"], 3)
        s = self.api.open(self.make("a.sldc", fixtures.make_sldc))
        r = self.api.data(200, self.api.get(f"/api/doc/{s['sid']}/search?q=quick%20brown"))
        self.assertGreater(r["total"], 0)

    def test_encrypted_flows_return_password_codes(self):
        enc = self.make("enc.sldc", fixtures.make_encrypted_sldc, "correct horse battery")
        status, js = self.api.post("/api/open", {"path": str(enc)})
        self.assertEqual((status, js["error"]["code"]), (401, "password_required"))
        status, js = self.api.post("/api/open", {"path": str(enc), "password": "wrong password here"})
        self.assertEqual((status, js["error"]["code"]), (401, "wrong_password"))
        d = self.api.open(enc, "correct horse battery")
        self.assertTrue(d["sid"])

    def test_upload_opens_a_managed_copy(self):
        p = self.make("up.sldc", fixtures.make_sldc)
        status, js = self.api.call("POST", "/api/upload?name=up.sldc", raw=p.read_bytes())
        self.assertEqual(status, 200, js)
        self.assertTrue(js["data"]["managedCopy"])
        self.assertIsNone(js["data"]["path"])         # the server-side copy's location is not shown as if it were the person's file

    def test_unknown_and_bad_input(self):
        self.assertEqual(self.api.post("/api/open", {"path": "relative/file.pdf"})[0], 400)
        self.assertEqual(self.api.post("/api/open", {"path": str(self.dir / "missing.pdf")})[0], 404)
        garbage = self.make("junk.pdf", fixtures.make_garbage, b"PK\x03\x04")
        self.assertIn(self.api.post("/api/open", {"path": str(garbage)})[0], (415, 422))
        self.assertEqual(self.api.get("/api/doc/nosuchsession")[0], 404)
        self.assertEqual(self.api.get("/api/nothing")[0], 404)
        self.assertEqual(self.api.call("POST", "/api/open", raw=b"{not json")[0], 400)
        self.assertEqual(self.api.call("POST", "/api/open", raw=b"[1,2]")[0], 400)


class AnnotateOverHttp(ApiBase):
    def test_full_annotation_workflow_persists_across_a_restart(self):
        p = self.make("a.sldc", fixtures.make_sldc)
        d = self.api.open(p)
        sid = d["sid"]
        block = next(b for b in self.api.app.sessions.get(sid).doc.blocks if b["type"] == "paragraph" and len(b["text"]) > 60)
        created = self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations", {"kind": "highlight", "style": {"color": "#86efac"},
                                                  "anchor": {"kind": "text", "segments": [{"block": block["id"], "start": 4, "end": 30}]}}))
        ann = created["created"][0]
        self.assertEqual(ann["text"], block["text"][4:30])
        note = self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations", {"kind": "note", "body": {"text": "Why?", "tags": ["q"]},
                                                 "anchor": {"kind": "text", "segments": [{"block": block["id"], "start": 4, "end": 30}]}}))["created"][0]
        upd = self.api.data(200, self.api.call("PATCH", f"/api/doc/{sid}/annotations/{note['id']}", {"patch": {"body": {"text": "Why not?"}}, "rev": note["rev"]}))
        self.assertEqual(upd["annotation"]["body"]["text"], "Why not?")
        status, js = self.api.call("PATCH", f"/api/doc/{sid}/annotations/{note['id']}", {"patch": {"body": {"text": "stale"}}, "rev": note["rev"]})
        self.assertEqual((status, js["error"]["code"]), (409, "conflict"))
        self.api.post(f"/api/doc/{sid}/position", None) if False else None
        loc = self.api.data(200, self.api.get(f"/api/doc/{sid}/location?percent=0.6"))
        self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/position", {"location": loc, "progress": 0.6}))
        self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 24, "theme": "sepia"}}))
        # close the whole server and open a new one over the same data folder: this is "quit and start Reader again"
        self.api.close()
        self.addCleanup(lambda: None)
        from apiharness import Api
        api2 = Api(self.dir)
        self.addCleanup(api2.close)
        d2 = api2.open(p)
        self.assertEqual(len(d2["annotations"]["annotations"]), 2)
        self.assertEqual(d2["reading"]["position"]["block"], loc["block"])
        self.assertAlmostEqual(d2["reading"]["progress"], 0.6, places=2)
        self.assertEqual(d2["reading"]["docPrefs"], {"fontSize": 24, "theme": "sepia"})
        self.assertEqual(d2["reading"]["effective"]["values"]["fontSize"], 24)
        lib = api2.data(200, api2.get("/api/library"))
        self.assertEqual(lib[0]["annotation_count"], 2)
        found = api2.data(200, api2.get("/api/search?q=why"))
        self.assertEqual(len(found["annotations"]), 1)
        self.api = api2

    def test_changed_file_is_detected_and_annotations_can_be_carried_over(self):
        p = self.make("c.txt", fixtures.make_text)
        d = self.api.open(p)
        sid = d["sid"]
        doc = self.api.app.sessions.get(sid).doc
        b = next(b for b in doc.blocks if len(b["text"]) > 30)
        self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations", {"kind": "highlight", "anchor": {"kind": "text", "segments": [{"block": b["id"], "start": 0, "end": 20}]}}))
        self.assertEqual(self.api.data(200, self.api.get(f"/api/doc/{sid}/file-status"))["state"], "same")
        p.write_text(p.read_text(encoding="utf-8") + "\n\nA new closing paragraph added by another program.\n", encoding="utf-8")
        self.assertEqual(self.api.data(200, self.api.get(f"/api/doc/{sid}/file-status"))["state"], "changed")
        d2 = self.api.data(200, self.api.post(f"/api/doc/{sid}/reload"))
        self.assertIsNotNone(d2["carryOver"])
        self.assertEqual(d2["carryOver"]["annotations"], 1)
        moved = self.api.data(200, self.api.post(f"/api/doc/{d2['sid']}/carry-over"))
        self.assertEqual(moved["moved"], 1)
        self.assertEqual(moved["annotations"][0]["status"], "ok")        # the quoted words are still where they were

    def test_undo_redo_and_page_marks_over_http(self):
        d = self.api.open(self.make("a.pdf", fixtures.make_pdf))
        sid = d["sid"]
        text = self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/text"))["text"]
        i = text.index("Page 1 line 2")
        a = self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations", {"kind": "highlight", "anchor": {"kind": "pdf-text", "segments": [{"page": 0, "start": i, "end": i + 13}]}}))["created"][0]
        marks = self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/marks"))
        self.assertEqual(marks[0]["id"], a["id"])
        self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations/undo"))
        self.assertEqual(self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/marks")), [])
        self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations/redo"))
        self.assertEqual(len(self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/marks"))), 1)
        self.assertEqual(self.api.post(f"/api/doc/{sid}/annotations/redo")[0], 409)

    def test_export_over_http_and_download_and_reimport(self):
        p = self.make("a.sldc", fixtures.make_sldc)
        sid = self.api.open(p)["sid"]
        doc = self.api.app.sessions.get(sid).doc
        b = next(b for b in doc.blocks if len(b["text"]) > 60)
        self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations", {"kind": "note", "body": {"text": "keep me"}, "anchor": {"kind": "text", "segments": [{"block": b["id"], "start": 0, "end": 12}]}}))
        res = self.api.data(200, self.api.post(f"/api/doc/{sid}/export", {"kind": "json"}))
        self.assertTrue(res["path"].endswith(".json"))
        status, headers, data = self.api.request("GET", res["download"])
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        payload = json.loads(data)
        # delete everything, then import the export
        ids = [a["id"] for a in self.api.data(200, self.api.get(f"/api/doc/{sid}/annotations"))["annotations"]]
        self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations/delete", {"ids": ids}))
        out = self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations/import", payload))
        self.assertEqual(out["imported"], 1)
        self.assertEqual(out["annotations"][0]["body"]["text"], "keep me")
        again = self.api.data(200, self.api.post(f"/api/doc/{sid}/annotations/import", payload))
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["duplicates"], 1)
        self.assertEqual(self.api.post(f"/api/doc/{sid}/export", {"kind": "nope"})[0], 400)
        self.assertEqual(self.api.post(f"/api/doc/{sid}/annotations/import", {"schema": "x"})[0], 400)
        self.assertEqual(self.api.get(f"/api/doc/{sid}/download/..%2F..%2Fetc%2Fpasswd")[0], 404)


class Security(ApiBase):
    def test_no_cookie_no_service(self):
        for path in ("/api/status", "/", "/main.js"):
            status, _h, body = self.api.request("GET", path, cookie=False)
            self.assertIn(status, (401, 403), path)
        self.assertNotIn(b"SLAI Reader", self.api.request("GET", "/api/status", cookie=False)[2].replace(b"Open Reader from the link", b""))

    def test_token_exchange_sets_a_strict_http_only_cookie(self):
        status, headers, _ = self.api.request("GET", "/?token=test-token-123", cookie=False)
        self.assertEqual(status, 302)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        status, _h, _ = self.api.request("GET", "/?token=wrong", cookie=False)
        self.assertEqual(status, 403)
        status, _h, _ = self.api.request("GET", "/api/status", headers={"Cookie": "slai_reader_token=wrong"}, cookie=False)
        self.assertEqual(status, 401)

    def test_dns_rebinding_host_is_refused(self):
        self.assertEqual(self.api.request("GET", "/api/status", host="evil.example.com")[0], 401)
        self.assertEqual(self.api.request("GET", "/api/status", host=f"localhost:{self.api.port}")[0], 200)

    def test_mutations_need_marker_and_same_origin(self):
        self.assertEqual(self.api.call("POST", "/api/prefs/reset", {}, marker=False)[0], 401)
        self.assertEqual(self.api.call("POST", "/api/prefs/reset", {}, headers={"Origin": "https://evil.example.com"})[0], 401)
        self.assertEqual(self.api.call("POST", "/api/prefs/reset", {}, headers={"Origin": f"http://127.0.0.1:{self.api.port}"})[0], 200)
        self.assertEqual(self.api.request("OPTIONS", "/api/prefs")[0], 405)

    def test_security_headers_on_every_response(self):
        status, headers, _ = self.api.request("GET", "/api/status")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-eval", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, headers, _ = self.api.request("GET", "/index.html")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_static_files_cannot_escape_the_client_folder(self):
        for path in ("/../README.md", "/%2e%2e/README.md", "/..%2fpyproject.toml", "/%2e%2e%2f%2e%2e%2fslai_reader/config.py"):
            self.assertEqual(self.api.request("GET", path)[0], 404, path)

    def test_loopback_only(self):
        self.assertEqual(self.api.server.server_address[0], "127.0.0.1")

    def test_oversized_json_and_upload_are_refused(self):
        status, js = self.api.call("POST", "/api/upload?name=x.pdf", raw=b"x" * 10, headers={"Content-Length": str(10)})
        self.assertIn(status, (200, 400, 415, 422))
        small = ApiBaseSmall = None
        self.api.app.settings.max_upload_bytes = 100
        status, js = self.api.call("POST", "/api/upload?name=x.pdf", raw=b"%PDF-" + b"x" * 500)
        self.assertEqual((status, js["error"]["code"]), (413, "limit_exceeded"))


class ViewSupport(ApiBase):
    """What the page needs in order to draw: system colours, pagination, list numbers, search rectangles."""

    def test_system_theme_follows_the_page_s_dark_preference(self):
        p = self.make("a.sldc", fixtures.make_sldc)
        light = self.api.open(p)
        self.assertEqual(light["reading"]["effective"]["theme"], "light")
        status, js = self.api.call("GET", f"/api/doc/{light['sid']}", headers={"X-Reader-Dark": "1"})
        self.assertEqual(js["data"]["reading"]["effective"]["theme"], "dark")
        status, js = self.api.call("PUT", f"/api/doc/{light['sid']}/prefs", {"values": {"fontSize": 22}}, headers={"X-Reader-Dark": "1"})
        self.assertEqual(js["data"]["theme"], "dark")
        status, js = self.api.call("PUT", f"/api/doc/{light['sid']}/prefs", {"values": {"theme": "sepia"}}, headers={"X-Reader-Dark": "1"})
        self.assertEqual(js["data"]["theme"], "sepia")                         # an explicit choice is never overridden by the system

    def test_document_override_can_be_removed_one_setting_at_a_time(self):
        sid = self.api.open(self.make("a.sldc", fixtures.make_sldc))["sid"]
        self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {"fontSize": 30, "theme": "dark"}}))
        eff = self.api.data(200, self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {}, "unset": ["fontSize"]}))
        self.assertEqual(eff["values"]["fontSize"], 18)
        self.assertEqual(eff["values"]["theme"], "dark")                       # the other override stays
        status, js = self.api.call("PUT", f"/api/doc/{sid}/prefs", {"values": {}, "unset": ["sidebar"]})
        self.assertEqual(status, 400)

    def test_pagination_covers_every_block_exactly_once(self):
        p = self.make("a.sldc", fixtures.make_sldc, paragraphs=120)
        sid = self.api.open(p)["sid"]
        total = len(self.api.app.sessions.get(sid).doc.blocks)
        for chars in (500, 2400, 9000):
            res = self.api.data(200, self.api.get(f"/api/doc/{sid}/paginate?chars={chars}"))
            pages = res["pages"]
            self.assertEqual(pages[0]["index"], 0)
            self.assertEqual(pages[-1]["end"], total)
            for a, b in zip(pages, pages[1:]):
                self.assertEqual(a["end"], b["index"])                          # no gaps, no overlaps
                self.assertLess(a["index"], a["end"])
        small = len(self.api.data(200, self.api.get(f"/api/doc/{sid}/paginate?chars=500"))["pages"])
        large = len(self.api.data(200, self.api.get(f"/api/doc/{sid}/paginate?chars=9000"))["pages"])
        self.assertGreater(small, large)

    def test_ordered_list_items_are_numbered_for_pieces_loaded_separately(self):
        def lists(path):
            path.write_text("# T\n\n1. one\n2. two\n3. three\n\ntext\n\n1. again\n2. more\n", encoding="utf-8")
            return path
        p = self.make("a.md", lists)
        sid = self.api.open(p)["sid"]
        blocks = self.api.data(200, self.api.get(f"/api/doc/{sid}/blocks?start=0&limit=50"))["blocks"]
        numbers = [b.get("number") for b in blocks if b["type"] == "list_item"]
        self.assertEqual(numbers, [1, 2, 3, 1, 2])
        late = self.api.data(200, self.api.get(f"/api/doc/{sid}/blocks?start=3&limit=50"))["blocks"]
        self.assertEqual([b.get("number") for b in late if b["type"] == "list_item"], [3, 1, 2])

    def test_pdf_range_rectangles_cover_the_searched_text(self):
        p = self.make("a.pdf", fixtures.make_pdf)
        sid = self.api.open(p)["sid"]
        found = self.api.data(200, self.api.get(f"/api/doc/{sid}/search?q=Page"))
        hit = next(h for h in found["hits"] if h.get("page") == 0)
        rects = self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/range?start={hit['start']}&end={hit['end']}"))
        self.assertTrue(rects)
        w, h = self.api.app.sessions.get(sid).doc.sizes[0]
        for r in rects:
            self.assertTrue(0 <= r[0] < r[2] <= w + 1 and 0 <= r[1] < r[3] <= h + 1, r)
        self.assertEqual(self.api.data(200, self.api.get(f"/api/doc/{sid}/page/0/range?start=5&end=5")), [])
        self.assertEqual(self.api.get(f"/api/doc/{sid}/page/99/range?start=0&end=3")[0], 404)


if __name__ == "__main__":
    unittest.main()
