"""HTTP routes. Every handler validates its input, talks to one document session, and returns JSON (or bytes for images and files).

Handlers never trust paths, ids or numbers from the client: sessions are looked up by an unguessable id, annotation ids are checked
against the open document, and every file the client names is resolved and size-checked before it is read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote
from typing import Any

from . import interop, prefs as prefs_mod
from .annotations import export
from .app import ReaderApp
from .documents.appearance import PdfAppearance
from .errors import BadRequest, Conflict, NotFound, PasswordRequired, UnsupportedOperation
from .files import safe_filename
from .formats import FORMATS, capability_row
from .picker import pick_file
from .router import Request, Response, ok, route
from .sessions import Session

EXPORT_KINDS = {
    "markdown": ("Notes (Markdown)", ".md"), "json": ("Notes (JSON)", ".json"), "sldc-notes": ("Notes as an SLDC document", ".sldc"),
    "sldc-comments": ("Copy of the document with comments (SLDC)", ".sldc"), "pdf-native": ("PDF with annotations", ".pdf"),
    "pdf-flatten": ("PDF with marks drawn in", ".pdf"),
}


# ---------------------------------------------------------------------------------------------- helpers
def _dark(req: Request) -> bool:
    """Does the person's system prefer dark colours? (Reader's page sends it with every request; it only matters when the theme is "system".)"""
    return req.headers.get("x-reader-dark") == "1" or req.query.get("dark") == "1"


def _password(req: Request) -> str | None:
    value = req.headers.get("x-reader-password")
    return value if value else None


def _session(app: ReaderApp, req: Request) -> Session:
    return app.sessions.get(req.params["sid"])


def _payload(app: ReaderApp, s: Session, dark: bool = False) -> dict[str, Any]:
    doc = s.doc
    state = app.sessions.reading_state(s, dark)
    return {"sid": s.sid, "docId": doc.doc_id, "info": doc.info(), "capabilities": doc.capabilities(), "reading": state,
            "file": app.sessions.file_status(s), "carryOver": s.carry_over, "warnings": doc.warnings,
            "annotations": s.service.list(), "assist": app.assistant.status(s)["allowedForThisDocument"],
            "managedCopy": doc.managed_copy, "path": str(doc.path) if (doc.path and not doc.managed_copy) else None}


def _ids(body: dict[str, Any], key: str = "ids") -> list[str]:
    ids = body.get(key)
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise BadRequest(f"'{key}' must be a list of annotation ids")
    return ids


def _pdf(s: Session) -> Any:
    if s.doc.layout != "fixed":
        raise UnsupportedOperation("This request is for PDF documents")
    return s.doc


def _flow(s: Session) -> Any:
    if s.doc.layout != "flow":
        raise UnsupportedOperation("This request is for text documents (not PDF)")
    return s.doc


def _page_no(req: Request, doc: Any) -> int:
    try:
        n = int(req.params["n"])
    except ValueError as exc:
        raise BadRequest("Page numbers are whole numbers") from exc
    if not 0 <= n < doc.pages:
        raise NotFound(f"Page {n + 1} does not exist (the document has {doc.pages} pages)")
    return n


# ---------------------------------------------------------------------------------------------- app
@route("GET", "/api/status")
def status(app: ReaderApp, req: Request) -> Response:
    return ok(app.status())


@route("GET", "/api/startup")
def startup(app: ReaderApp, req: Request) -> Response:
    """Documents named on the command line: opened once, the first time a window asks."""
    pending, app.pending = list(getattr(app, "pending", [])), []
    opened, problems = [], []
    for kind, path in pending:
        try:
            if kind == "handoff":
                data = interop.read_handoff(app.settings, path)
                s = app.sessions.open_path(data["document"]["path"])
                opened.append({**_payload(app, s, _dark(req)), "arrived": interop.apply_handoff(s, data)})
            else:
                opened.append(_payload(app, app.sessions.open_path(path), _dark(req)))
        except PasswordRequired as exc:
            problems.append({"path": path, "error": exc.to_dict()})
        except Exception as exc:  # noqa: BLE001
            from .errors import ReaderError
            problems.append({"path": path, "error": exc.to_dict() if isinstance(exc, ReaderError) else {"code": "error", "message": str(exc)[:200]}})
    return ok({"opened": opened, "problems": problems})


@route("GET", "/api/formats")
def formats(app: ReaderApp, req: Request) -> Response:
    return ok([capability_row(f) for f in FORMATS])


@route("GET", "/api/prefs")
def get_prefs(app: ReaderApp, req: Request) -> Response:
    return ok({"values": app.prefs.get(), "defaults": prefs_mod.DEFAULTS, "effective": prefs_mod.effective(app.prefs.get(), None, dark_system=_dark(req))})


@route("PUT", "/api/prefs")
def put_prefs(app: ReaderApp, req: Request) -> Response:
    values = app.prefs.update(req.json())
    return ok({"values": values, "effective": prefs_mod.effective(values, None, dark_system=_dark(req))})


@route("POST", "/api/prefs/reset")
def reset_prefs(app: ReaderApp, req: Request) -> Response:
    return ok({"values": app.prefs.reset()})


# ---------------------------------------------------------------------------------------------- opening
@route("POST", "/api/open")
def open_doc(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    path = body.get("path")
    if not isinstance(path, str):
        raise BadRequest("Give the full path of the file to open")
    pw = body.get("password")
    if pw is not None and not isinstance(pw, str):
        raise BadRequest("password must be text")
    s = app.sessions.open_path(path, password=pw)
    return ok(_payload(app, s, _dark(req)))


@route("POST", "/api/upload")
def upload(app: ReaderApp, req: Request) -> Response:
    name = req.query.get("name") or "document"
    data = req.body_bytes(app.settings.max_upload_bytes)
    s = app.sessions.open_upload(name, data, password=_password(req))
    return ok(_payload(app, s, _dark(req)))


@route("POST", "/api/pick")
def pick(app: ReaderApp, req: Request) -> Response:
    res = pick_file()
    if res.get("path"):
        s = app.sessions.open_path(res["path"])
        return ok({"picked": res["path"], **_payload(app, s, _dark(req))})
    return ok({"picked": None, "unavailable": res.get("error") == "no-dialog", "detail": res.get("detail")})


@route("POST", "/api/handoff/open")
def handoff_open(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    data = interop.read_handoff(app.settings, str(body.get("path", "")))
    s = app.sessions.open_path(data["document"]["path"], password=body.get("password") if isinstance(body.get("password"), str) else None)
    arrived = interop.apply_handoff(s, data)
    return ok({**_payload(app, s, _dark(req)), "arrived": arrived})


@route("GET", "/api/library")
def library(app: ReaderApp, req: Request) -> Response:
    rows = app.store.library()
    for r in rows:
        p = r.get("path")
        local = bool(p) and not str(p).startswith("\\\\") and not str(p).startswith("//")
        r["available"] = bool(p) and (Path(p).is_file() if local else None)
        r["managedCopy"] = bool(p) and str(app.settings.uploads_dir) in str(p)
    return ok(rows)


@route("POST", "/api/library/open")
def library_open(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    rec = app.store.get_document(str(body.get("docId", "")))
    if rec is None or not rec.get("path"):
        raise NotFound("That document is not in your library")
    if not Path(rec["path"]).is_file():
        raise NotFound("The file is no longer where it was. Your annotations are kept; open the file again from its new place and they will return.",
                       details={"path": rec["path"]})
    s = app.sessions.open_path(rec["path"], password=body.get("password") if isinstance(body.get("password"), str) else None)
    return ok(_payload(app, s, _dark(req)))


@route("POST", "/api/library/forget")
def library_forget(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    doc_id = str(body.get("docId", ""))
    if app.store.get_document(doc_id) is None:
        raise NotFound("That document is not in your library")
    app.store.forget_document(doc_id, delete_annotations=body.get("deleteAnnotations") is True)
    return ok({"forgotten": doc_id, "annotationsDeleted": body.get("deleteAnnotations") is True})


@route("GET", "/api/search")
def global_search(app: ReaderApp, req: Request) -> Response:
    """Library-wide search over document titles, notes, highlights' text and bookmarks. (Full text of documents is searched per document.)"""
    q = (req.query.get("q") or "").strip()
    if not q:
        return ok({"query": q, "documents": [], "annotations": []})
    low = q.casefold()
    docs = [{k: d[k] for k in ("doc_id", "title", "format", "path", "progress")} for d in app.store.library() if low in (d.get("title") or "").casefold()
            or low in Path(d.get("path") or "").name.casefold()]
    notes = app.store.search_notes(q, None, req.int_query("limit", 100, 1, 500))
    titles = {d["doc_id"]: d.get("title") for d in app.store.library()}
    return ok({"query": q, "documents": docs, "annotations": [{**a, "docTitle": titles.get(a["doc_id"])} for a in notes],
               "scope": "titles, notes, highlighted text and bookmarks of every document in your library"})


# ---------------------------------------------------------------------------------------------- one document
@route("GET", "/api/doc/{sid}")
def doc_get(app: ReaderApp, req: Request) -> Response:
    return ok(_payload(app, _session(app, req), _dark(req)))


@route("DELETE", "/api/doc/{sid}")
def doc_close(app: ReaderApp, req: Request) -> Response:
    return ok({"closed": app.sessions.close(req.params["sid"])})


@route("GET", "/api/doc/{sid}/file-status")
def file_status(app: ReaderApp, req: Request) -> Response:
    return ok(app.sessions.file_status(_session(app, req)))


@route("POST", "/api/doc/{sid}/reload")
def reload_doc(app: ReaderApp, req: Request) -> Response:
    s = app.sessions.reload(_session(app, req), password=_password(req))
    return ok(_payload(app, s, _dark(req)))


@route("POST", "/api/doc/{sid}/carry-over")
def carry_over(app: ReaderApp, req: Request) -> Response:
    return ok(app.sessions.carry_over(_session(app, req)))


@route("PUT", "/api/doc/{sid}/position")
def position(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    app.sessions.save_position(_session(app, req), body.get("location"), body.get("progress"))
    return ok({"saved": True})


@route("GET", "/api/doc/{sid}/prefs")
def doc_prefs_get(app: ReaderApp, req: Request) -> Response:
    """The confirmed settings, read without writing anything (the client uses it to recover after a failed save)."""
    state = app.sessions.reading_state(_session(app, req), _dark(req))
    return ok({"values": app.prefs.get(), "docPrefs": state["docPrefs"], "effective": state["effective"]})


@route("PUT", "/api/doc/{sid}/prefs")
def doc_prefs(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    return ok(app.sessions.set_doc_prefs(_session(app, req), body.get("values", {}), reset=body.get("reset") is True, dark=_dark(req), unset=body.get("unset")))


@route("GET", "/api/doc/{sid}/outline")
def outline(app: ReaderApp, req: Request) -> Response:
    return ok(_session(app, req).doc.outline())


@route("GET", "/api/doc/{sid}/pages")
def pages(app: ReaderApp, req: Request) -> Response:
    doc = _session(app, req).doc
    if doc.layout == "fixed":
        return ok({"layout": "fixed", "pages": doc.pages, "sizes": [list(x) for x in doc.sizes]})
    starts = doc.logical_pages()
    out = []
    for n, i in enumerate(starts):
        b = doc.blocks[i]
        out.append({"n": n, "block": b["id"], "snippet": " ".join((doc.block_text(b["id"]) or "").split())[:90],
                    "percent": doc.cum[i] / max(1, doc.total_chars)})
    return ok({"layout": "flow", "pages": out, "note": "SLDC has no fixed pages; these are the page and section breaks defined by the document"})


@route("GET", "/api/doc/{sid}/paginate")
def paginate(app: ReaderApp, req: Request) -> Response:
    doc = _flow(_session(app, req))
    budget = req.int_query("chars", 2400, 400, 60000)
    starts = doc.paginate(budget)
    return ok({"budget": budget, "total": len(doc.blocks), "pages": [
        {"n": n, "index": i, "end": starts[n + 1] if n + 1 < len(starts) else len(doc.blocks), "block": doc.blocks[i]["id"], "percent": doc.cum[i] / max(1, doc.total_chars)}
        for n, i in enumerate(starts)]})


@route("GET", "/api/doc/{sid}/blocks")
def blocks(app: ReaderApp, req: Request) -> Response:
    doc = _flow(_session(app, req))
    return ok(doc.blocks_range(req.int_query("start", 0, 0, 10 ** 9), req.int_query("limit", 60, 1, 400)))


@route("GET", "/api/doc/{sid}/asset/{aid}")
def asset(app: ReaderApp, req: Request) -> Response:
    data, mime = _flow(_session(app, req)).asset(req.params["aid"])
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise UnsupportedOperation("Only raster images are shown")
    return Response(200, data, mime, {"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"})


@route("GET", "/api/doc/{sid}/location")
def location(app: ReaderApp, req: Request) -> Response:
    """Turn a percentage into a place (or the reverse for a block) - used by the progress slider and go-to-page."""
    doc = _session(app, req).doc
    if "percent" in req.query:
        pct = req.float_query("percent", 0.0, 0.0, 1.0)
        if doc.layout == "fixed":
            page = min(doc.pages - 1, int(pct * doc.pages))
            return ok({"type": "page", "page": page, "y": 0.0, "percent": page / doc.pages})
        return ok(doc.location_at_percent(pct))
    return ok(doc.normalise_location({"type": "page" if doc.layout == "fixed" else "flow", "page": req.int_query("page", 0, 0, 10 ** 7),
                                      "block": req.query.get("block"), "offset": req.int_query("offset", 0, 0, 10 ** 9)}))


# ---------------------------------------------------------------------------------------------- PDF pages
def _appearance(app: ReaderApp, s: Session, req: Request) -> PdfAppearance:
    over: dict[str, Any] = {}
    if "theme" in req.query:
        over["theme"] = prefs_mod.validate_field("theme", req.query["theme"])
    if "fc" in req.query:
        over["fontColor"] = prefs_mod.validate_field("fontColor", "#" + req.query["fc"]) if req.query["fc"] else None
    if "fo" in req.query:
        over["fontOpacity"] = prefs_mod.validate_field("fontOpacity", req.int_query("fo", 100, 0, 1000))
    if "pa" in req.query:
        over["pdfAppearance"] = prefs_mod.validate_field("pdfAppearance", req.query["pa"])
    if over.get("theme") == "system" or (over == {} and app.sessions.reading_state(s)["effective"]["values"]["theme"] == "system"):
        over["theme"] = "dark" if _dark(req) else "light"
    return PdfAppearance.from_prefs(app.sessions.appearance_for(s, over))


@route("GET", "/api/doc/{sid}/page/{n}/image")
def page_image(app: ReaderApp, req: Request) -> Response:
    s = _session(app, req)
    doc = _pdf(s)
    n = _page_no(req, doc)
    scale = req.float_query("scale", 1.0, 0.03, 8.0)
    fmt = req.query.get("fmt", "png")
    if fmt not in ("png", "jpeg", "webp"):
        raise BadRequest("fmt must be png, jpeg or webp")
    region = None
    if "region" in req.query:
        try:
            parts = [float(x) for x in req.query["region"].split(",")]
        except ValueError as exc:
            raise BadRequest("region must be four numbers: x0,y0,x1,y1 in points") from exc
        if len(parts) != 4 or any(p != p for p in parts):
            raise BadRequest("region must be four numbers: x0,y0,x1,y1 in points")
        region = (parts[0], parts[1], parts[2], parts[3])
    data, mime, size = doc.render(n, scale=scale, fmt=fmt, appearance=_appearance(app, s, req), region=region, max_pixels=app.settings.max_render_pixels)
    return Response(200, data, mime, {"Cache-Control": "private, max-age=3600", "X-Image-Size": f"{size[0]}x{size[1]}"})


@route("GET", "/api/doc/{sid}/page/{n}/text")
def page_text(app: ReaderApp, req: Request) -> Response:
    doc = _pdf(_session(app, req))
    return ok(doc.text_payload(_page_no(req, doc)))


@route("GET", "/api/doc/{sid}/page/{n}/links")
def page_links(app: ReaderApp, req: Request) -> Response:
    doc = _pdf(_session(app, req))
    return ok(doc.links(_page_no(req, doc)))


@route("GET", "/api/doc/{sid}/page/{n}/marks")
def page_marks(app: ReaderApp, req: Request) -> Response:
    s = _session(app, req)
    return ok(s.service.page_marks(_page_no(req, _pdf(s))))


@route("GET", "/api/doc/{sid}/page/{n}/range")
def page_range(app: ReaderApp, req: Request) -> Response:
    """Rectangles (points, displayed page) for a range of the page's text - used to show search results."""
    doc = _pdf(_session(app, req))
    n = _page_no(req, doc)
    return ok(doc.rects_for_range(n, req.int_query("start", 0, 0, 10 ** 7), req.int_query("end", 0, 0, 10 ** 7)))


@route("GET", "/api/doc/{sid}/page/{n}/coords")
def page_coords(app: ReaderApp, req: Request) -> Response:
    """Map displayed-page fractions to PDF user space (used by the ink tool tests and exports)."""
    doc = _pdf(_session(app, req))
    n = _page_no(req, doc)
    x, y = doc.display_to_user(n, req.float_query("x", 0.0, 0.0, 1.0), req.float_query("y", 0.0, 0.0, 1.0))
    return ok({"x": x, "y": y})


# ---------------------------------------------------------------------------------------------- search
@route("GET", "/api/doc/{sid}/search")
def search(app: ReaderApp, req: Request) -> Response:
    doc = _session(app, req).doc
    return ok(doc.search(req.query.get("q", ""), limit=req.int_query("limit", 200, 1, 2000), case_sensitive=req.query.get("case") == "1"))


@route("GET", "/api/doc/{sid}/search-notes")
def search_notes(app: ReaderApp, req: Request) -> Response:
    s = _session(app, req)
    found = app.store.search_notes(req.query.get("q", ""), s.doc.doc_id, req.int_query("limit", 200, 1, 1000))
    return ok([s.service.view(a) for a in found])


# ---------------------------------------------------------------------------------------------- annotations
@route("GET", "/api/doc/{sid}/annotations")
def ann_list(app: ReaderApp, req: Request) -> Response:
    return ok(_session(app, req).service.list())


@route("POST", "/api/doc/{sid}/annotations")
def ann_create(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    svc = _session(app, req).service
    if "annotations" in body:
        made = svc.create_many(body["annotations"], label=body.get("label") if isinstance(body.get("label"), str) else None)
    else:
        made = [svc.create(body, label=body.get("label") if isinstance(body.get("label"), str) else None)]
    return ok({"created": made, "history": svc._history_state()})


@route("PATCH", "/api/doc/{sid}/annotations/{aid}")
def ann_update(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    svc = _session(app, req).service
    rev = body.get("rev")
    if rev is not None and (not isinstance(rev, int) or isinstance(rev, bool)):
        raise BadRequest("rev must be a whole number")
    one = svc.update(req.params["aid"], body.get("patch", {}), rev, label=body.get("label") if isinstance(body.get("label"), str) else None)
    return ok({"annotation": one, "history": svc._history_state()})


@route("POST", "/api/doc/{sid}/annotations/update-many")
def ann_update_many(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    svc = _session(app, req).service
    out = svc.update_many(body.get("items"), label=body.get("label") if isinstance(body.get("label"), str) else None)
    return ok({"updated": out, "history": svc._history_state()})


@route("POST", "/api/doc/{sid}/annotations/delete")
def ann_delete(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    svc = _session(app, req).service
    removed = svc.delete_many(_ids(body), label=body.get("label") if isinstance(body.get("label"), str) else None)
    return ok({"deleted": removed, "history": svc._history_state()})


@route("POST", "/api/doc/{sid}/annotations/undo")
def ann_undo(app: ReaderApp, req: Request) -> Response:
    return ok(_session(app, req).service.undo())


@route("POST", "/api/doc/{sid}/annotations/redo")
def ann_redo(app: ReaderApp, req: Request) -> Response:
    return ok(_session(app, req).service.redo())


@route("POST", "/api/doc/{sid}/annotations/accept")
def ann_accept(app: ReaderApp, req: Request) -> Response:
    svc = _session(app, req).service
    return ok({"updated": svc.accept_moved(_ids(req.json())), "history": svc._history_state()})


@route("POST", "/api/doc/{sid}/annotations/{aid}/reanchor")
def ann_reanchor(app: ReaderApp, req: Request) -> Response:
    svc = _session(app, req).service
    return ok({"annotation": svc.reanchor(req.params["aid"], req.json().get("anchor")), "history": svc._history_state()})


@route("POST", "/api/doc/{sid}/annotations/import")
def ann_import(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    if body.get("schema") != export.EXPORT_SCHEMA:
        raise BadRequest("This is not a SLAI Reader annotation export (schema slai-reader-annotations/1)")
    return ok(_session(app, req).service.import_many(body.get("annotations")))


# ---------------------------------------------------------------------------------------------- export
@route("POST", "/api/doc/{sid}/export")
def export_doc(app: ReaderApp, req: Request) -> Response:
    s = _session(app, req)
    body = req.json()
    kind = body.get("kind")
    if kind not in EXPORT_KINDS:
        raise BadRequest(f"kind must be one of {', '.join(EXPORT_KINDS)}")
    anns = s.service.list()["annotations"]
    ids = body.get("ids")
    if ids is not None:
        wanted = set(_ids(body))
        anns = [a for a in anns if a["id"] in wanted]
    if body.get("kinds") is not None:
        wanted_kinds = body["kinds"]
        if not isinstance(wanted_kinds, list) or not all(isinstance(k, str) for k in wanted_kinds):
            raise BadRequest("kinds must be a list of annotation kinds")
        anns = [a for a in anns if a["kind"] in wanted_kinds]
    out_dir = app.settings.output_dir
    doc = s.doc
    title = doc.title or (doc.path.stem if doc.path else "document")
    report: dict[str, Any] = {}
    if kind == "markdown":
        path = export.write_text(out_dir, f"{title} - notes", ".md", export.notes_markdown(doc, anns).encode("utf-8"))
    elif kind == "json":
        path = export.write_text(out_dir, f"{title} - notes", ".json", json.dumps(export.notes_json(doc, anns), indent=2, ensure_ascii=False).encode("utf-8"))
    elif kind == "sldc-notes":
        path = export.notes_sldc(doc, anns, out_dir)
    elif kind == "sldc-comments":
        path, report = export.sldc_with_comments(doc, anns, out_dir, password=body.get("password") if isinstance(body.get("password"), str) else None)
    else:
        path, report = export.annotated_pdf(doc, anns, out_dir, mode="native" if kind == "pdf-native" else "flatten")
    s.outputs[path.name] = path
    return ok({"kind": kind, "label": EXPORT_KINDS[kind][0], "path": str(path), "name": path.name, "count": len(anns), "report": report,
               "download": f"/api/doc/{s.sid}/download/{quote(path.name)}"})


@route("GET", "/api/doc/{sid}/download/{name}")
def download(app: ReaderApp, req: Request) -> Response:
    s = _session(app, req)
    path = s.outputs.get(req.params["name"])
    if path is None or not path.is_file():
        raise NotFound("That export is not available any more")
    return Response(200, b"", "application/octet-stream", {"Cache-Control": "no-store"}, file_path=str(path), download_name=safe_filename(path.name))


# ---------------------------------------------------------------------------------------------- assistant
@route("GET", "/api/assist/status")
def assist_status(app: ReaderApp, req: Request) -> Response:
    return ok(app.assistant.status())


@route("POST", "/api/assist/probe")
def assist_probe(app: ReaderApp, req: Request) -> Response:
    return ok(app.assistant.probe())


@route("POST", "/api/doc/{sid}/assist/consent")
def assist_consent(app: ReaderApp, req: Request) -> Response:
    allow = req.json().get("allow")
    if not isinstance(allow, bool):
        raise BadRequest("allow must be true or false")
    return ok(app.assistant.set_consent(_session(app, req), allow))


@route("POST", "/api/doc/{sid}/assist/summary")
def assist_summary(app: ReaderApp, req: Request) -> Response:
    return ok(app.assistant.summary(_session(app, req), req.json()))


@route("POST", "/api/doc/{sid}/assist/passages")
def assist_passages(app: ReaderApp, req: Request) -> Response:
    return ok(app.assistant.passages(_session(app, req), req.json()))


@route("POST", "/api/doc/{sid}/assist/check")
def assist_check(app: ReaderApp, req: Request) -> Response:
    return ok(app.assistant.check(_session(app, req)))


# ---------------------------------------------------------------------------------------------- other SLAI applications
@route("POST", "/api/doc/{sid}/handoff")
def handoff(app: ReaderApp, req: Request) -> Response:
    body = req.json()
    s = _session(app, req)
    target = body.get("target")
    pos = body.get("position")
    if pos is not None and not isinstance(pos, dict):
        raise BadRequest("position must be an object")
    sel = body.get("selection")
    if sel is not None and not isinstance(sel, dict):
        raise BadRequest("selection must be an object")
    return ok(interop.send(app.settings, s, str(target), s.doc.normalise_location(pos) if pos else None, sel))
