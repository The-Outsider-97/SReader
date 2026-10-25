"""Hand-over between Reader and the other SLAI applications.

The contract is one small JSON file, ``slai-handoff/1``, written atomically into the shared ``handoff`` folder:

    {"schema": "slai-handoff/1", "created": "...", "source": "slai-reader", "target": "documaster" | "writer",
     "document": {"id", "path", "format", "title", "fingerprint", "revision"},
     "position": {"type": "flow"|"page", "block"|"page", "offset"|"y", "percent"},
     "selection": {"kind": "text", "segments": [{"block"|"page", "start", "end", "quote", "prefix", "suffix"}]} | null}

Reader reads the same file when another application sends a reader there (``/api/handoff/open``, ``--handoff FILE``): the document
is opened by its path, checked against ``fingerprint``/``revision`` (a different revision is reported, not hidden) and the selection
is resolved with the same anchor rules the annotations use. Anything in the file is untrusted input.

What exists on the other side today is stated honestly in the API result: Documaster has no handoff reader yet (Reader starts it,
and gives the path); SLAI Writer is not part of this checkout, so it is launched only when ``SLAI_WRITER_CMD`` /
``--writer-command`` names a program.
"""
from __future__ import annotations

import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import time

from pathlib import Path
from typing import Any

from .config import Settings
from .errors import BadRequest, DependencyMissing, FileRejected, UnsupportedOperation
from .files import atomic_write
from .sessions import Session

SCHEMA = "slai-handoff/1"
MAX_BYTES = 1024 * 1024
TARGETS = ("documaster", "writer")


def build_context(session: Session, target: str, position: dict[str, Any] | None, selection: dict[str, Any] | None) -> dict[str, Any]:
    doc = session.doc
    sel = None
    if selection:
        anchor = doc.make_text_anchor(selection.get("segments"))                      # type: ignore[attr-defined]
        sel = anchor
    return {"schema": SCHEMA, "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "source": "slai-reader", "target": target,
            "document": {"id": doc.doc_id, "path": str(doc.path) if doc.path else None, "format": doc.format, "title": doc.title,
                         "fingerprint": doc.fingerprint, "revision": doc.revision},
            "position": position, "selection": sel}


def write_handoff(settings: Settings, context: dict[str, Any]) -> Path:
    settings.handoff_dir.mkdir(parents=True, exist_ok=True)
    name = f"reader-to-{context['target']}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.json"
    return atomic_write(settings.handoff_dir / name, json.dumps(context, indent=2).encode("utf-8"))


def read_handoff(settings: Settings, raw_path: str) -> dict[str, Any]:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise BadRequest("The handoff file was not found")
    if path.stat().st_size > MAX_BYTES:
        raise FileRejected("The handoff file is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileRejected(f"The handoff file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise FileRejected("This is not a SLAI handoff file (schema slai-handoff/1 expected)")
    doc = data.get("document")
    if not isinstance(doc, dict) or not isinstance(doc.get("path"), str) or not doc["path"]:
        raise FileRejected("The handoff names no document path")
    for key in ("fingerprint", "revision", "id", "format", "title"):
        if doc.get(key) is not None and not isinstance(doc[key], str):
            raise FileRejected(f"document.{key} must be text")
    pos, sel = data.get("position"), data.get("selection")
    if pos is not None and not isinstance(pos, dict):
        raise FileRejected("position must be an object")
    if sel is not None and not (isinstance(sel, dict) and isinstance(sel.get("segments"), list) and len(sel["segments"]) <= 400):
        raise FileRejected("selection must hold text segments")
    return data


def apply_handoff(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """After the document is open: compare identity, resolve the selection, choose where to land."""
    # Session.doc is typed as OpenDocument, while these navigation/anchor
    # helpers are provided by its concrete document implementations.
    doc: Any = session.doc
    want = data["document"]
    notes: list[str] = []
    if want.get("fingerprint") and want["fingerprint"] != doc.fingerprint:
        if want.get("revision") and want["revision"] == doc.revision:
            notes.append("The file differs byte for byte but has the same SLDC revision.")
        else:
            notes.append("This is not exactly the file the other application had open; the position and selection were re-checked against it.")
    elif want.get("revision") and doc.revision and want["revision"] != doc.revision:
        notes.append("The document's revision differs from the one the other application sent.")
    landing: dict[str, Any] | None = doc.normalise_location(data["position"]) if isinstance(data.get("position"), dict) else None
    selection = None
    if data.get("selection"):
        anchor = {"kind": "pdf-text" if doc.layout == "fixed" else "text", "segments": data["selection"]["segments"]}
        resolved = doc.resolve_anchor(anchor)
        selection = {"status": resolved["status"], "segments": resolved.get("segments", []), "text": doc.text_of_segments(resolved.get("segments", []))
                     if resolved.get("segments") else ""}
        segs = resolved.get("segments") or []
        if segs and selection["status"] != "detached":
            first = segs[0]
            landing = ({"type": "page", "page": first["page"], "y": 0.0, "percent": first["page"] / max(1, doc.pages)}      # type: ignore[attr-defined]
                       if doc.layout == "fixed" else doc.normalise_location({"type": "flow", "block": first["block"], "offset": first["start"]}))
        else:
            notes.append("The selected text could not be found in this version of the document.")
    return {"landing": landing, "selection": selection, "notes": notes}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def send(settings: Settings, session: Session, target: str, position: dict[str, Any] | None, selection: dict[str, Any] | None) -> dict[str, Any]:
    if target not in TARGETS:
        raise BadRequest(f"target must be one of {', '.join(TARGETS)}")
    if session.doc.path is None:
        raise UnsupportedOperation("This document has no file to hand over")
    context = build_context(session, target, position, selection)
    handoff = write_handoff(settings, context)
    result: dict[str, Any] = {"handoff": str(handoff), "document": context["document"]["path"], "target": target}
    if target == "writer":
        if not settings.writer_command:
            raise DependencyMissing("SLAI Writer is not installed here: no Writer program is configured. Set SLAI_WRITER_CMD (or --writer-command) to the "
                                    "command that starts it; use {handoff} where the handoff file path belongs. The handoff file was written to "
                                    f"{handoff} and contains everything Writer needs to receive.", details=result)
        if os.name == "nt":
            # Windows CreateProcess parses a command line, not POSIX shell tokens.
            # shlex(posix=False) retains executable quotes, causing WinError 2.
            quoted_handoff = subprocess.list2cmdline([str(handoff)])
            command = settings.writer_command.replace('"{handoff}"', quoted_handoff).replace("{handoff}", quoted_handoff)
            if "{handoff}" not in settings.writer_command:
                command += " " + quoted_handoff
        else:
            command = [part.replace("{handoff}", str(handoff))
                       for part in shlex.split(settings.writer_command)]
            if "{handoff}" not in settings.writer_command:
                command.append(str(handoff))
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as exc:
            raise DependencyMissing(f"The Writer command could not be started: {exc}", details=result) from exc
        result["launched"] = True
        result["note"] = "Writer was started with the handoff file."
        return result
    # Documaster
    root = settings.slai_root
    pkg = root / "applications" / "Documaster" if root else None
    result["running"] = _port_open(8766)
    if result["running"]:
        result["note"] = ("Documaster is already running. It does not read handoff files yet, so open the document there from the path above; "
                          "the handoff file holds the position and selection.")
        return result
    if pkg is None or not (pkg / "slai" / "__main__.py").is_file():
        raise DependencyMissing("Documaster was not found next to Reader, so it was not started.", details=result)
    cmd = [sys.executable, "-m", "slai", "--slai-root", str(root)]
    try:
        subprocess.Popen(cmd, cwd=str(pkg), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise DependencyMissing(f"Documaster could not be started: {exc}", details=result) from exc
    result["launched"] = True
    result["note"] = ("Documaster was started in your browser. It does not read handoff files yet, so open the document there from the path above; "
                      "the handoff file holds the position and selection.")
    return result
