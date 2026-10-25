"""The seams between Reader and the rest of SLAI.

* ``Contract``   the small Documaster surface Reader consumes, against the real Documaster in this checkout
* ``Handoff``    the ``slai-handoff/1`` file: written, read back, resolved against the document, and refused when malformed
* ``AssistantRules``   the assistant's privacy gate, Safety block, grounding check and honest fallbacks.

The assistant tests exercise Reader's *plumbing* with stand-in agents installed through the gateway's own ``inject`` hook. The stand-ins
are labelled ``Stub...`` and prove what Reader does with an answer (gate it, check it, label it, discard it). They say nothing about
the quality of the real SLAI agents; no test here claims they do.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from apiharness import Api, ApiBase, fixtures
from harness import Base, ROOT

from slai_reader import interop
from slai_reader.errors import FileRejected


# ============================================================================================ Documaster contract
class Contract(Base):
    def setUp(self):
        super().setUp()
        self.link = self.env.link.load()
        if not self.link.available:
            self.skipTest(f"Documaster is not present: {self.link.reason}")

    def test_the_named_surface_exists(self):
        readers = self.link.module("docmodel.readers")
        self.assertTrue(callable(readers.read_document))
        self.assertTrue(callable(self.link.module("docmodel.read_html").read_html_bytes))
        self.assertTrue(callable(self.link.module("bridge.agents").AgentGateway))
        calls = self.link.module("analysis.agent_calls")
        for name in ("Consent", "privacy_gate", "safety_review", "safety_blocks", "abstract_summary", "rerank", "quality_review"):
            self.assertTrue(hasattr(calls, name), name)
        self.assertTrue(callable(self.link.module("analysis.pii").scan_text))
        gw = self.link.services.agents
        for role in ("privacy", "safety", "language", "quality"):
            self.assertIn(role, gw.slots)
        self.assertTrue(callable(gw.inject))

    def test_read_document_returns_a_valid_sldc_package(self):
        p = self.env.make("c.docx", fixtures.make_docx)
        res = self.link.read_document(p, "docx")
        from slai_reader.bridge import sldc
        pkg = res.package
        sldc().validate_package(pkg)                                  # raises if it is not a valid SLDC package
        self.assertTrue(pkg.document["blocks"] if hasattr(pkg, "document") else pkg["blocks"])

    def test_html_reader_keeps_text_and_drops_scripts(self):
        mod = self.link.module("docmodel.read_html")
        res = mod.read_html_bytes(b"<html><body><h1>Title</h1><p>Hello <b>world</b></p><script>alert(1)</script></body></html>")
        text = json.dumps(res.package if hasattr(res, "package") else res, default=str)
        self.assertIn("Hello", text)
        self.assertNotIn("alert(1)", text)


# ============================================================================================ hand-over
class Handoff(ApiBase):
    def _open(self, name="h.sldc", **kw):
        p = self.make(name, fixtures.make_sldc, **kw)
        d = self.api.open(p)
        return p, d["sid"], self.api.app.sessions.get(d["sid"])

    def _para(self, doc):
        return next(b for b in doc.blocks if b["id"].startswith("p") and len(b["text"]) > 60)

    def test_written_file_follows_the_documented_schema(self):
        p, sid, s = self._open()
        b = self._para(s.doc)
        sel = {"segments": [{"block": b["id"], "start": 10, "end": 30}]}
        with self.assertRaises(Exception):                                  # an unknown target is refused
            self.api.data(200, self.api.post(f"/api/doc/{sid}/handoff", {"target": "nowhere"}))
        self.api.settings.writer_command = None
        status, js = self.api.post(f"/api/doc/{sid}/handoff", {"target": "writer", "selection": sel,
                                                               "position": {"type": "flow", "block": b["id"], "offset": 5}})
        self.assertEqual(status, 501, js)                                    # Writer is not installed here: said plainly, not faked
        self.assertEqual(js["error"]["code"], "dependency_missing")
        self.assertIn("SLAI_WRITER_CMD", js["error"]["message"])
        written = Path(js["error"]["details"]["handoff"])
        data = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "slai-handoff/1")
        self.assertEqual((data["source"], data["target"]), ("slai-reader", "writer"))
        self.assertEqual(data["document"]["path"], str(p))
        self.assertEqual(data["document"]["fingerprint"], s.doc.fingerprint)
        seg = data["selection"]["segments"][0]
        self.assertEqual((seg["block"], seg["start"], seg["end"]), (b["id"], 10, 30))
        self.assertEqual(seg["quote"], b["text"][10:30])
        self.assertEqual(data["position"]["block"], b["id"])

    def test_reader_opens_its_own_handoff_at_the_selection(self):
        p, sid, s = self._open()
        b = self._para(s.doc)
        self.api.settings.writer_command = None
        _st, js = self.api.post(f"/api/doc/{sid}/handoff", {"target": "writer", "selection": {"segments": [{"block": b["id"], "start": 12, "end": 40}]}})
        handoff = js["error"]["details"]["handoff"]
        arrived = self.api.data(200, self.api.post("/api/handoff/open", {"path": handoff}))["arrived"]
        self.assertEqual(arrived["selection"]["status"], "ok")
        self.assertEqual(arrived["selection"]["text"], b["text"][12:40])
        self.assertEqual(arrived["landing"]["block"], b["id"])
        self.assertEqual(arrived["notes"], [])

    def test_a_changed_document_is_reported_not_hidden(self):
        p, sid, s = self._open(paragraphs=12)
        b = self._para(s.doc)
        self.api.settings.writer_command = None
        _st, js = self.api.post(f"/api/doc/{sid}/handoff", {"target": "writer", "selection": {"segments": [{"block": b["id"], "start": 0, "end": 25}]}})
        handoff = js["error"]["details"]["handoff"]
        fixtures.make_sldc(p, paragraphs=12, title="Fixture Document", extra_text=" Added later.")           # another program saved a new version
        arrived = self.api.data(200, self.api.post("/api/handoff/open", {"path": handoff}))["arrived"]
        self.assertTrue(arrived["notes"], "a different file must be reported")
        self.assertTrue(any("not exactly the file" in n or "revision" in n for n in arrived["notes"]), arrived["notes"])
        # the quoted words are still in the paragraph, so the selection is found again - but its status is never silently 'ok' for missing text
        self.assertIn(arrived["selection"]["status"], ("ok", "moved"))

    def test_selection_text_that_is_gone_is_reported_as_detached(self):
        p, sid, s = self._open()
        data = {"schema": "slai-handoff/1", "source": "writer", "target": "slai-reader",
                "document": {"path": str(p), "fingerprint": "x" * 10, "revision": None},
                "position": None,
                "selection": {"kind": "text", "segments": [{"block": "p2", "start": 0, "end": 22, "quote": "words that never occurred in it", "prefix": "", "suffix": ""}]}}
        f = self.api.settings.handoff_dir / "from-writer.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
        arrived = self.api.data(200, self.api.post("/api/handoff/open", {"path": str(f)}))["arrived"]
        self.assertEqual(arrived["selection"]["status"], "detached")
        self.assertTrue(any("could not be found" in n for n in arrived["notes"]))

    def test_malformed_handoff_files_are_refused(self):
        d = self.dir / "bad"
        d.mkdir()
        good_doc = {"path": str(self.make("g.sldc", fixtures.make_sldc))}
        cases = {
            "notjson.json": "{not json",
            "array.json": "[1,2]",
            "schema.json": json.dumps({"schema": "slai-handoff/2", "document": good_doc}),
            "nopath.json": json.dumps({"schema": "slai-handoff/1", "document": {}}),
            "badfield.json": json.dumps({"schema": "slai-handoff/1", "document": {**good_doc, "fingerprint": 5}}),
            "badpos.json": json.dumps({"schema": "slai-handoff/1", "document": good_doc, "position": [1]}),
            "badsel.json": json.dumps({"schema": "slai-handoff/1", "document": good_doc, "selection": {"segments": "x"}}),
            "manysegs.json": json.dumps({"schema": "slai-handoff/1", "document": good_doc, "selection": {"segments": [{}] * 401}}),
        }
        for name, text in cases.items():
            f = d / name
            f.write_text(text, encoding="utf-8")
            status, js = self.api.post("/api/handoff/open", {"path": str(f)})
            self.assertIn(status, (400, 415, 422), (name, status, js))
            self.assertFalse(js.get("ok", False), name)
        big = d / "big.json"
        big.write_bytes(b" " * (interop.MAX_BYTES + 1))
        self.assertEqual(self.api.post("/api/handoff/open", {"path": str(big)})[0], 422)
        self.assertEqual(self.api.post("/api/handoff/open", {"path": str(d / "missing.json")})[0], 400)

    def test_a_handoff_cannot_make_reader_open_something_it_cannot_read(self):
        d = self.dir / "bad2"
        d.mkdir()
        f = d / "h.json"
        f.write_text(json.dumps({"schema": "slai-handoff/1", "document": {"path": str(d / "gone.pdf")}}), encoding="utf-8")
        status, js = self.api.post("/api/handoff/open", {"path": str(f)})
        self.assertGreaterEqual(status, 400)
        self.assertNotEqual(status, 500, js)

    def test_writer_command_is_started_with_the_handoff_file(self):
        p, sid, _s = self._open()
        marker = self.dir / "marker.txt"
        script = self.dir / "fake_writer.py"
        script.write_text("import sys, pathlib, json\n"
                          f"pathlib.Path({str(marker)!r}).write_text(json.load(open(sys.argv[1]))['schema'])\n", encoding="utf-8")
        self.api.settings.writer_command = f'"{sys.executable}" "{script}" {{handoff}}'
        res = self.api.data(200, self.api.post(f"/api/doc/{sid}/handoff", {"target": "writer"}))
        self.assertTrue(res["launched"])
        for _ in range(100):
            if marker.exists() and marker.read_text():
                break
            __import__("time").sleep(0.05)
        self.assertEqual(marker.read_text(), "slai-handoff/1")

    def test_a_writer_command_that_does_not_exist_is_reported(self):
        _p, sid, _s = self._open()
        self.api.settings.writer_command = str(self.dir / "no-such-program.exe")
        status, js = self.api.post(f"/api/doc/{sid}/handoff", {"target": "writer"})
        self.assertEqual(status, 501)
        self.assertIn("could not be started", js["error"]["message"])

    def test_documaster_is_not_started_when_it_cannot_be_found(self):
        _p, sid, _s = self._open()
        try:
            socket.create_connection(("127.0.0.1", 8766), timeout=0.3).close()
            self.skipTest("something is listening on 8766")
        except OSError:
            pass
        self.api.settings.slai_root = self.dir                       # a checkout without applications/Documaster
        status, js = self.api.post(f"/api/doc/{sid}/handoff", {"target": "documaster"})
        self.assertEqual(status, 501)
        self.assertIn("not found", js["error"]["message"])
        self.assertTrue(Path(js["error"]["details"]["handoff"]).is_file())


# ============================================================================================ assistant (plumbing only)
class StubPrivacy:
    def __init__(self, decision="allow"):
        self.decision, self.seen = decision, []

    def register_consent(self, **kw):
        return {"ok": True}

    def bind_purpose(self, **kw):
        return {"ok": True}

    def evaluate_privacy(self, payload, **kw):
        self.seen.append(payload["content"])
        return {"decision": self.decision, "summary": f"stub says {self.decision}", "stages": {"identification": {"sensitivity_score": 0.0, "entity_count": 0}}}


class StubSafety:
    def __init__(self, block=False):
        self.block, self.calls = block, 0

    def perform_task(self, text, ctx):
        self.calls += 1
        return {"decision": "block" if self.block else "allow", "blockers": ["stub"] if self.block else []}


class StubLanguage:
    """``mode`` decides what the stand-in answers; the point is what Reader does with each kind of answer."""

    def __init__(self, mode="faithful"):
        self.mode, self.summaries, self.reranks = mode, 0, 0

    def summarize_text(self, text, max_length=90):
        self.summaries += 1
        if self.mode == "invented":
            return "Quarterly revenue doubled because the board approved a merger with a rival shipping company overseas."
        if self.mode == "error":
            raise RuntimeError("model crashed")
        return " ".join(text.split()[:14])

    def rerank_texts(self, query, candidates, top_k=5):
        self.reranks += 1
        if self.mode == "garbage":
            return {"unexpected": "shape"}
        return list(reversed(range(len(candidates))))[:top_k]


class StubQuality:
    def perform_task(self, payload):
        return {"evaluated": len(payload["records"])}


class AssistantRules(ApiBase):
    agents = "auto"

    def setUp(self):
        super().setUp()
        if not self.api.app.link.available:
            self.skipTest("Documaster is not present")
        self.gw = self.api.app.link.services.agents
        self.privacy, self.safety, self.lang, self.quality = StubPrivacy(), StubSafety(), StubLanguage(), StubQuality()
        for role, inst in (("privacy", self.privacy), ("safety", self.safety), ("language", self.lang), ("quality", self.quality)):
            self.gw.inject(role, inst)
        self.p = self.make("a.sldc", fixtures.make_sldc, paragraphs=14)
        self.sid = self.api.open(self.p)["sid"]
        self.doc = self.api.app.sessions.get(self.sid).doc

    def summary(self, **kw):
        return self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/summary", {"scope": "document", "words": 60, **kw}))

    def allow(self, flag=True):
        return self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/consent", {"allow": flag}))

    def test_nothing_is_sent_until_the_person_allows_it_for_this_document(self):
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertEqual(res["produced_by"], "extractive")
        self.assertIn("not AI-written", res["label"])
        self.assertIn("have not allowed", res["why"])
        self.assertEqual((self.privacy.seen, self.safety.calls, self.lang.summaries), ([], 0, 0))

    def test_allowed_text_goes_through_privacy_then_safety_then_the_language_agent(self):
        self.allow()
        res = self.summary()
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["produced_by"], "language-agent")
        self.assertIn("Machine-written", res["label"])
        self.assertEqual((len(self.privacy.seen), self.safety.calls, self.lang.summaries), (1, 1, 1))
        roles = [n["role"] for n in res["notes"]]
        self.assertEqual(roles, ["privacy", "safety", "language"])
        self.assertTrue(res["sources"], "an answer must point at the passages it rests on")
        first_words = " ".join(self.doc.block_text(self.doc.blocks[0]["id"]).split())
        self.assertTrue(res["text"])
        self.assertGreaterEqual(res["grounded"], 0.6)
        self.assertNotIn("Quarterly revenue", res["text"])

    def test_switching_consent_off_stops_use_again(self):
        self.allow()
        self.summary()
        before = self.lang.summaries
        self.allow(False)
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertEqual(self.lang.summaries, before)

    def test_privacy_agent_that_does_not_allow_keeps_the_text_local(self):
        self.privacy.decision = "deny"
        self.allow()
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertEqual(self.lang.summaries, 0)
        self.assertEqual(self.safety.calls, 0)

    def test_privacy_modify_is_not_treated_as_permission(self):
        self.privacy.decision = "modify"
        self.allow()
        res = self.summary()
        self.assertEqual((res["status"], self.lang.summaries), ("fallback", 0))

    def test_safety_block_stops_the_request_before_the_language_agent(self):
        self.safety.block = True
        self.allow()
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertIn("Safety agent flagged", res["why"])
        self.assertEqual(self.lang.summaries, 0)

    def test_an_invented_summary_is_discarded_and_the_reason_is_shown(self):
        self.lang.mode = "invented"
        self.allow()
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertEqual(res["produced_by"], "extractive")
        self.assertIn("discarded", res["why"])
        quoted = [s["text"] for s in res["sentences"]]
        whole = " ".join(self.doc.block_text(b["id"]) for b in self.doc.blocks)
        self.assertTrue(all(q in whole for q in quoted), "the fallback may only quote the document")

    def test_a_crashing_agent_falls_back_without_breaking_the_request(self):
        self.lang.mode = "error"
        self.allow()
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertIn("RuntimeError", res["why"])

    def test_question_passages_are_always_quotations_with_places(self):
        self.allow()
        res = self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/passages", {"query": "paragraph chapter", "top": 3}))
        self.assertEqual(res["status"], "done")
        whole = " ".join(self.doc.block_text(b["id"]) for b in self.doc.blocks)
        for p in res["passages"]:
            self.assertIn(p["text"][:40], whole)
            self.assertIn("block", p["loc"])
            self.assertTrue(p["label"])

    def test_unrecognised_ranking_answer_falls_back_to_text_match(self):
        self.lang.mode = "garbage"
        self.allow()
        res = self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/passages", {"query": "paragraph", "top": 2}))
        self.assertEqual((res["status"], res["produced_by"]), ("fallback", "text-match"))
        self.assertTrue(res["passages"])

    def test_a_question_with_no_overlap_gets_no_invented_answer(self):
        self.allow()
        res = self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/passages", {"query": "zzyzx qwertyuiop"}))
        self.assertEqual((res["status"], res["passages"]), ("none", []))

    def test_document_check_sends_counts_not_text(self):
        res = self.api.data(200, self.api.post(f"/api/doc/{self.sid}/assist/check"))
        self.assertEqual(res["status"], "done")
        self.assertGreater(res["local"]["words"], 0)
        self.assertEqual(self.privacy.seen, [])

    def test_agents_none_switches_everything_off(self):
        self.api.app.settings.agents = "none"
        st = self.api.data(200, self.api.get("/api/assist/status"))
        self.assertFalse(st["gateway"])
        self.assertIn("switched off", st["reason"])
        res = self.summary()
        self.assertEqual(res["status"], "fallback")
        self.assertEqual(self.lang.summaries, 0)

    def test_bad_requests_are_refused(self):
        for body in ({"scope": "nonsense"}, {"scope": "page", "page": 3}):
            status, js = self.api.post(f"/api/doc/{self.sid}/assist/summary", body)
            self.assertEqual(status, 400, (body, js))
        status, _ = self.api.post(f"/api/doc/{self.sid}/assist/passages", {"query": ""})
        self.assertEqual(status, 400)
        status, _ = self.api.post(f"/api/doc/{self.sid}/assist/consent", {"allow": "yes"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
