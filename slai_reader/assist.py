"""The optional reading assistant. Reading, searching and annotating never depend on it.

What it does, and which real SLAI agent does it (through Documaster's ``AgentGateway`` and ``analysis.agent_calls``):

=========================  ==========================================  ======================================================
feature                    agents used                                 what you get when no agent is available
=========================  ==========================================  ======================================================
Summary                    Privacy (gate), Safety (review), Language   Key sentences picked by word frequency, marked
                           (abstractive summary, checked against       "extractive, no AI"
                           the source text)
Related passages / ask     Privacy (gate), Safety, Language (re-rank)  The same passages in plain text-match order,
                                                                        marked as such
Document check             Quality (reviews text-per-page statistics;  Local statistics only
                           no document text is sent)
=========================  ==========================================  ======================================================

Rules, all enforced here and not by the page:

* nothing is sent to an agent unless the person allowed it **for this document** (``--agents none`` switches the whole thing off);
* the Privacy agent decides on every use; if it is unavailable, text that looks like personal data (``analysis.pii``) is not sent;
* the Safety agent reviews the text before the Language agent sees it; a "block" verdict stops the request;
* every result says who produced it, shows the passages it is based on (with their places in the document), and is labelled
  machine-written; an agent's answer that does not check out against the source is discarded and the reason shown;
* if an agent is missing the result says so and falls back to the deterministic path - an answer is never made up.

The Reasoning and Knowledge agents are not used: a grounded answer here is a quotation of the document, which needs no inference,
and the document is not a knowledge base. The Planning, Handler and Observability agents are not used either.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .bridge import DocumasterLink
from .config import Settings
from .errors import AgentUnavailable, BadRequest, UnsupportedOperation
from .sessions import Session

SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[\"'“‘(\[]?[A-Z0-9À-Ý])")
STOP = frozenset("""a an and are as at be but by for from has have he her his i in is it its of on or our she that the their them they this to
was we were what when where which who will with you your not no so if then than there these those been being do does did can could
should would may might must into over under about after before between also more most other some such only own same too very""".split())
WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

PURPOSE_NOTE = "Summaries and answers are produced from the passages shown below them. Check them against those passages."


class Assistant:
    def __init__(self, settings: Settings, link: DocumasterLink) -> None:
        self.settings = settings
        self.link = link

    # ------------------------------------------------------------------ state
    def _calls(self) -> Any:
        return self.link.module("analysis.agent_calls")

    def usable(self) -> tuple[bool, str | None]:
        if self.settings.agents == "none":
            return False, "Agents are switched off (Reader was started with --agents none)."
        self.link.load()
        if not self.link.available:
            return False, f"Documaster's agent gateway is not available: {self.link.reason}"
        return True, None

    def status(self, session: Session | None = None) -> dict[str, Any]:
        ok, why = self.usable()
        roles: dict[str, Any] = {}
        if ok:
            gw = self.link.services.agents
            for role in ("privacy", "safety", "language", "quality"):
                slot = gw.slots[role]
                roles[role] = {"state": slot.state, "class": slot.cls or None, "error": slot.error, "tested": slot.test}
        out = {"mode": self.settings.agents, "gateway": ok, "reason": why, "roles": roles,
               "agentsNotUsed": {"reasoning": "answers are quotations of the document; no inference is needed",
                                 "knowledge": "the document is not a knowledge base", "planning": "no multi-step plan is made",
                                 "handler": "failures are shown as they are", "observability": "no telemetry is sent"}}
        if session is not None:
            out["allowedForThisDocument"] = session.assist_allowed
        return out

    def probe(self) -> dict[str, Any]:
        """Create the agents now (this can take a while on first use) and report the real state."""
        ok, why = self.usable()
        if not ok:
            return self.status()
        self.link.services.agents.status(probe=True)
        return self.status()

    def set_consent(self, session: Session, allow: bool) -> dict[str, Any]:
        ok, why = self.usable()
        session.assist_allowed = bool(allow)
        note = None
        if allow and ok:
            note = self._consent(session).grant(self.link.services, session.doc.doc_id).public()
        elif ok:
            self._consent(session).revoke(self.link.services, session.doc.doc_id)
        return {"allowed": session.assist_allowed, "privacyAgent": note, "agentsUsable": ok, "reason": why}

    def _consent(self, session: Session) -> Any:
        holder = getattr(self, "_consents", None)
        if holder is None:
            holder = self._consents = {}
        if "c" not in holder:
            holder["c"] = self._calls().Consent(default=False)
        return holder["c"]

    # ------------------------------------------------------------------ text sources
    def _source(self, s: Session, req: dict[str, Any]) -> dict[str, Any]:
        """The text a request is about, with a list of places (so every sentence can point back into the document)."""
        doc = s.doc
        scope = req.get("scope", "selection")
        units: list[dict[str, Any]] = []
        if scope == "selection":
            segs = req.get("segments")
            if doc.layout == "fixed":
                anchor = doc.make_text_anchor(segs)          # type: ignore[attr-defined]
                for seg in anchor["segments"]:
                    units.append({"loc": {"page": seg["page"], "start": seg["start"]}, "text": doc.page_text(seg["page"]).text[seg["start"]:seg["end"]]})  # type: ignore[attr-defined]
            else:
                anchor = doc.make_text_anchor(segs)          # type: ignore[attr-defined]
                for seg in anchor["segments"]:
                    units.append({"loc": {"block": seg["block"], "start": seg["start"]}, "text": doc.block_text(seg["block"])[seg["start"]:seg["end"]]})  # type: ignore[attr-defined]
        elif scope == "section" and doc.layout == "flow":
            _text, ids = doc.section_text(req.get("block") or "")          # type: ignore[attr-defined]
            units = [{"loc": {"block": i, "start": 0}, "text": doc.block_text(i)} for i in ids if doc.block_text(i)]       # type: ignore[attr-defined]
        elif scope == "page" and doc.layout == "fixed":
            page = req.get("page")
            if type(page) is not int or not 0 <= page < doc.pages:       # type: ignore[attr-defined]
                raise BadRequest("That page is not in this document")
            units = [{"loc": {"page": page, "start": 0}, "text": doc.page_plain(page)}]         # type: ignore[attr-defined]
        elif scope == "document":
            if doc.layout == "fixed":
                total = 0
                for p in range(doc.pages):                                                   # type: ignore[attr-defined]
                    t = doc.page_plain(p)                                                    # type: ignore[attr-defined]
                    units.append({"loc": {"page": p, "start": 0}, "text": t})
                    total += len(t)
                    if total > 60_000:
                        break
            else:
                total = 0
                for b in doc.blocks:                                                         # type: ignore[attr-defined]
                    t = doc.block_text(b["id"])                                              # type: ignore[attr-defined]
                    if t:
                        units.append({"loc": {"block": b["id"], "start": 0}, "text": t})
                        total += len(t)
                    if total > 60_000:
                        break
        else:
            raise BadRequest(f"scope '{scope}' is not available for this document")
        units = [u for u in units if u["text"].strip()]
        if not units:
            raise UnsupportedOperation("There is no text there to work with" + (" (this PDF is a scan; Reader does not run OCR)" if getattr(doc, "_scan", {}).get("ocrNeeded") else ""))
        return {"scope": scope, "units": units, "text": "\n\n".join(u["text"] for u in units)}

    # ------------------------------------------------------------------ the gate
    def _gate(self, s: Session, text: str) -> dict[str, Any]:
        """Run Privacy then Safety. Returns {forward, reasons, notes}. ``forward`` False means: use the local path."""
        notes: list[dict[str, Any]] = []
        ok, why = self.usable()
        if not ok:
            return {"forward": False, "reason": why, "notes": notes}
        if not s.assist_allowed:
            return {"forward": False, "reason": "You have not allowed SLAI agents to read this document. "
                                                "Turn on 'Let SLAI agents read this document' in the assistant panel to use them.", "notes": notes}
        calls = self._calls()
        pii = self.link.module("analysis.pii").scan_text(text[:60_000])
        consent = self._consent(s)
        if not consent.is_explicit(s.doc.doc_id):
            consent.grant(self.link.services, s.doc.doc_id)
        gate = calls.privacy_gate(self.link.services, consent, s.doc.doc_id, text, len(pii))
        if gate.get("note") is not None:
            notes.append(gate["note"].public())
        if not gate["forward"]:
            return {"forward": False, "reason": gate["reason"], "notes": notes}
        safety = calls.safety_review(self.link.services, text)
        notes.append(safety.public())
        if calls.safety_blocks(safety):
            return {"forward": False, "reason": "The Safety agent flagged this text, so it was not sent to the language agent.", "notes": notes}
        return {"forward": True, "reason": None, "notes": notes}

    # ------------------------------------------------------------------ summary
    def summary(self, s: Session, req: dict[str, Any]) -> dict[str, Any]:
        src = self._source(s, req)
        words = int(max(20, min(300, req.get("words", 90) if isinstance(req.get("words", 90), (int, float)) else 90)))
        gate = self._gate(s, src["text"])
        result: dict[str, Any] = {"feature": "summary", "scope": src["scope"], "note": PURPOSE_NOTE, "notes": gate["notes"], "sources": self._cite(s, src["units"])}
        if gate["forward"]:
            note = self._calls().abstract_summary(self.link.services, src["text"], words)
            result["notes"] = gate["notes"] + [note.public()]
            if note.status == "used":
                result.update(status="done", label=f"Machine-written by the SLAI language agent ({note.agent}). Check it against the passages below.",
                              text=note.output["text"], grounded=note.output["grounded"], produced_by="language-agent")
                return result
            result["agentProblem"] = note.reason
        else:
            result["agentProblem"] = gate["reason"]
        sentences = self.extractive(src["units"], max(1, min(8, words // 25)))
        result.update(status="fallback", label="Key sentences picked by word frequency. This is not AI-written and not a rewrite: "
                                              "each sentence is quoted from the document.", produced_by="extractive", sentences=sentences,
                      text=" ".join(x["text"] for x in sentences), why=result["agentProblem"])
        return result

    @staticmethod
    def extractive(units: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        """The ``count`` most representative sentences, in document order, each with its place. Deterministic; no model involved."""
        sentences: list[dict[str, Any]] = []
        for u in units:
            pos = 0
            for part in SENTENCE.split(u["text"]):
                idx = u["text"].find(part, pos)
                pos = idx + len(part) if idx >= 0 else pos
                part = part.strip()
                if len(part) >= 25:
                    sentences.append({"text": part, "loc": {**u["loc"], "start": u["loc"]["start"] + max(idx, 0)}})
        if not sentences:
            return []
        freq = Counter(w for sent in sentences for w in {x.lower() for x in WORD.findall(sent["text"]) if x.lower() not in STOP})
        scored = []
        for i, sent in enumerate(sentences):
            terms = [w.lower() for w in WORD.findall(sent["text"]) if w.lower() not in STOP]
            score = sum(freq[w] for w in set(terms)) / (len(terms) ** 0.5 + 1) if terms else 0.0
            scored.append((score, -i, i))
        top = sorted(scored, reverse=True)[:count]
        return [sentences[i] for _s, _n, i in sorted(top, key=lambda t: t[2])]

    # ------------------------------------------------------------------ related passages / questions
    def passages(self, s: Session, req: dict[str, Any]) -> dict[str, Any]:
        query = req.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise BadRequest("Ask with a short question or a few key words")
        top = int(max(1, min(10, req.get("top", 5) if isinstance(req.get("top", 5), (int, float)) else 5)))
        terms = {w.lower() for w in WORD.findall(query) if w.lower() not in STOP}
        units = self._candidates(s, terms)
        if not units:
            return {"feature": "passages", "status": "none", "label": "No passage in this document shares words with the question.",
                    "passages": [], "notes": [], "query": query}
        local = sorted(units, key=lambda u: -u["score"])[:max(top * 4, 12)]
        text = "\n\n".join(u["text"] for u in local)
        gate = self._gate(s, text)
        result: dict[str, Any] = {"feature": "passages", "query": query, "notes": gate["notes"], "note": "These are quotations from the document, not generated answers."}
        if gate["forward"]:
            note, order = self._calls().rerank(self.link.services, query, [u["text"] for u in local], top)
            result["notes"] = gate["notes"] + [note.public()]
            if note.status == "used" and order:
                chosen = [local[i] for i in order[:top]]
                result.update(status="done", label="Passages ranked by the SLAI language agent.", produced_by="language-agent")
                result["passages"] = [self._passage(s, u) for u in chosen]
                return result
            result["agentProblem"] = note.reason
        else:
            result["agentProblem"] = gate["reason"]
        result.update(status="fallback", label="Passages ranked by how many of your words they contain (no agent).", produced_by="text-match",
                      why=result["agentProblem"], passages=[self._passage(s, u) for u in local[:top]])
        return result

    def _candidates(self, s: Session, terms: set[str]) -> list[dict[str, Any]]:
        doc = s.doc
        out: list[dict[str, Any]] = []
        if not terms:
            return out
        if doc.layout == "fixed":
            for p in range(doc.pages):                                   # type: ignore[attr-defined]
                if not doc.index_state()["complete"] and p >= 50:        # type: ignore[attr-defined]
                    break
                for chunk, start in self._chunks(doc.page_plain(p)):     # type: ignore[attr-defined]
                    score = self._score(chunk, terms)
                    if score:
                        out.append({"text": chunk, "loc": {"page": p, "start": start}, "score": score})
        else:
            for b in doc.blocks:                                         # type: ignore[attr-defined]
                t = doc.block_text(b["id"])                              # type: ignore[attr-defined]
                if not t:
                    continue
                for chunk, start in self._chunks(t):
                    score = self._score(chunk, terms)
                    if score:
                        out.append({"text": chunk, "loc": {"block": b["id"], "start": start}, "score": score})
        return out

    @staticmethod
    def _chunks(text: str, size: int = 500) -> list[tuple[str, int]]:
        if len(text) <= size:
            return [(text, 0)]
        out, pos = [], 0
        while pos < len(text):
            end = min(len(text), pos + size)
            if end < len(text):
                cut = max(text.rfind(". ", pos, end), text.rfind("\n", pos, end))
                end = cut + 1 if cut > pos + size // 2 else end
            out.append((text[pos:end].strip(), pos))
            pos = end
        return out

    @staticmethod
    def _score(chunk: str, terms: set[str]) -> float:
        words = {w.lower() for w in WORD.findall(chunk)}
        hit = len(terms & words)
        return hit / (len(terms) ** 0.5) if hit else 0.0

    def _passage(self, s: Session, u: dict[str, Any]) -> dict[str, Any]:
        return {"text": u["text"], "loc": u["loc"], "label": self._label(s, u["loc"])}

    def _cite(self, s: Session, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for u in units[:12]:
            out.append({"loc": u["loc"], "label": self._label(s, u["loc"]), "text": u["text"][:240]})
        return out

    @staticmethod
    def _label(s: Session, loc: dict[str, Any]) -> str:
        if "page" in loc:
            return f"Page {loc['page'] + 1}"
        doc = s.doc
        pct = doc.percent_at(loc["block"], loc.get("start", 0))                        # type: ignore[attr-defined]
        return f"{round(pct * 100)}% through the document"

    # ------------------------------------------------------------------ document check (Quality agent)
    def check(self, s: Session) -> dict[str, Any]:
        doc = s.doc
        if doc.layout == "fixed":
            records = []
            for p in range(min(doc.pages, 500)):                                          # type: ignore[attr-defined]
                t = doc.page_plain(p)                                                     # type: ignore[attr-defined]
                records.append({"page": p + 1, "characters": len(t), "words": len(t.split()), "empty": not t.strip()})
            unit = "page"
        else:
            records = []
            starts = doc.logical_pages() or [0]                                           # type: ignore[attr-defined]
            bounds = starts + [len(doc.blocks)]                                           # type: ignore[attr-defined]
            for n in range(min(len(starts), 500)):
                t = " ".join(doc.block_text(doc.blocks[i]["id"]) for i in range(bounds[n], bounds[n + 1]))   # type: ignore[attr-defined]
                records.append({"page": n + 1, "characters": len(t), "words": len(t.split()), "empty": not t.strip()})
            unit = "section"
        local = {"units": len(records), "emptyUnits": sum(1 for r in records if r["empty"]), "unit": unit,
                 "words": sum(r["words"] for r in records), "ocrNeeded": bool(getattr(doc, "_scan", {}).get("ocrNeeded"))}
        result: dict[str, Any] = {"feature": "check", "local": local, "notes": [],
                                  "label": "Counts computed by Reader. The Quality agent's review is shown when it is available; "
                                           "no document text is sent for this check, only these counts."}
        ok, why = self.usable()
        if ok:
            note = self._calls().quality_review(self.link.services, records)
            result["notes"].append(note.public())
            result["status"] = "done" if note.status == "used" else "local-only"
            if note.status != "used":
                result["why"] = note.reason
        else:
            result["status"], result["why"] = "local-only", why
        return result
