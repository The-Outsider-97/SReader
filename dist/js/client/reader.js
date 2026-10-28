// One open document: owns the view, turns what the person does (select, draw, click) into annotation requests, and keeps the
// reading position. Everything that changes an annotation goes through the service; this class only mirrors its answers.
import { ApiError, qs } from '../core/api.js';
import { groupId } from '../core/strokes.js';
import { AnnotateUi } from './annotate-ui.js';
import { FlowView } from './flowview.js';
import { PdfView } from './pdfview.js';
import { announce } from './dom.js';
import { toast } from './ui.js';
const rand = () => 'g' + Math.random().toString(36).slice(2, 12);
const r5 = (n) => Math.round(n * 100000) / 100000;
export class ReaderSession {
    state;
    payload;
    sid;
    api;
    view;
    ui;
    sel = null;
    docPrefs;
    lastLoc;
    savedLoc;
    posTimer = 0;
    group = null;
    busy = Promise.resolve();
    onHide = () => { void this.flushPosition(true); };
    constructor(state, payload) {
        this.state = state;
        this.payload = payload;
        this.sid = payload.sid;
        this.api = state.api;
        this.lastLoc = payload.reading.position;
        this.docPrefs = { ...payload.reading.docPrefs };
        this.savedLoc = payload.reading.position;
        state.doc = payload;
        state.effective = payload.reading.effective;
        state.setAnnotations(payload.annotations);
        state.selectedId = null;
        state.inkSelected = new Set();
        this.view = payload.info.layout === 'fixed' ? new PdfView(this, payload) : new FlowView(this, payload);
        this.ui = new AnnotateUi(this);
        window.addEventListener('pagehide', this.onHide);
    }
    async start(at) {
        await this.view.start(at ?? this.payload.arrived?.landing ?? this.payload.reading.position);
    }
    async close() {
        window.removeEventListener('pagehide', this.onHide);
        window.clearTimeout(this.posTimer);
        this.ui.dispose();
        await this.flushPosition(false);
        this.view.destroy();
        try {
            await this.api.del(`/api/doc/${this.sid}`);
        }
        catch { /* the service may already have closed it */ }
    }
    // ------------------------------------------------------------------ small things views call
    say(message) { toast(message); }
    canCopy() { return this.payload.capabilities.copyAllowed !== false; }
    location(loc) { this.lastLoc = loc; this.state.emit('status'); window.clearTimeout(this.posTimer); this.posTimer = window.setTimeout(() => { void this.flushPosition(false); }, 900); }
    selection(info) { this.sel = info; this.ui.selectionChanged(info); }
    openAnnotation(id, near) { this.ui.editAnnotation(id, near.getBoundingClientRect()); }
    openReference(id, near) { this.ui.showReference(id, near.getBoundingClientRect()); }
    placeSticky(anchor, near) { this.ui.newSticky(anchor, near); }
    inkSelection(ids) { this.state.inkSelected = new Set(ids); this.state.selectedId = ids.length === 1 ? ids[0] : null; this.view.setInkSelection(this.state.inkSelected); this.state.emit('annotations'); }
    async flushPosition(keepalive) {
        const loc = this.lastLoc;
        if (!loc || loc === this.savedLoc)
            return;
        this.savedLoc = loc;
        try {
            await this.api.put(`/api/doc/${this.sid}/position`, { location: loc, progress: loc.percent }, keepalive);
        }
        catch {
            this.savedLoc = null;
        }
    }
    // ------------------------------------------------------------------ serialised annotation operations
    run(fn) {
        const next = this.busy.then(fn, fn).catch(async (err) => {
            if (err instanceof ApiError) {
                if (err.code === 'conflict') {
                    toast('That change was not saved because the annotation was changed somewhere else. The list has been refreshed.');
                    await this.refresh();
                }
                else
                    toast(err.message);
            }
            else
                toast('Something went wrong. Your annotations are unchanged.');
            return undefined;
        });
        this.busy = next.then(() => undefined, () => undefined);
        return next;
    }
    async refresh() {
        const list = await this.api.get(`/api/doc/${this.sid}/annotations`);
        this.state.setAnnotations(list);
        this.sync();
    }
    sync() {
        this.view.annotationsChanged();
        this.state.emit('annotations');
    }
    apply(changes, history) {
        for (const c of changes) {
            if (c.annotation)
                this.state.upsert(c.annotation);
            else
                this.state.remove(c.id);
        }
        this.state.history = history;
        this.sync();
    }
    create(raws, label) {
        return this.run(async () => {
            const res = await this.api.post(`/api/doc/${this.sid}/annotations`, raws.length === 1 && !label ? raws[0] : { annotations: raws, label });
            const dup = res.created.filter((a) => a.duplicate).length;
            this.apply(res.created.map((a) => ({ id: a.id, annotation: a })), res.history);
            if (dup)
                toast(dup === res.created.length ? 'That text already has this mark.' : `${dup} of these were already marked.`);
            return res.created;
        });
    }
    patch(id, patch, label) {
        return this.run(async () => {
            const cur = this.state.annotations.find((a) => a.id === id);
            const res = await this.api.patch(`/api/doc/${this.sid}/annotations/${id}`, { patch, rev: cur?.rev, label });
            this.apply([{ id, annotation: res.annotation }], res.history);
            return res.annotation;
        });
    }
    remove(ids, label) {
        return this.run(async () => {
            if (!ids.length)
                return;
            const res = await this.api.post(`/api/doc/${this.sid}/annotations/delete`, { ids, label });
            this.apply(res.deleted.map((id) => ({ id, annotation: null })), res.history);
            if (ids.some((i) => this.state.inkSelected.has(i)))
                this.inkSelection([...this.state.inkSelected].filter((i) => !ids.includes(i)));
            if (this.state.selectedId && ids.includes(this.state.selectedId))
                this.state.selectedId = null;
            announce(ids.length === 1 ? 'Annotation deleted. Press Control Z to undo.' : `${ids.length} annotations deleted. Press Control Z to undo.`);
        });
    }
    undo() { return this.history('undo'); }
    redo() { return this.history('redo'); }
    history(which) {
        return this.run(async () => {
            if (which === 'undo' ? !this.state.history.canUndo : !this.state.history.canRedo) {
                toast(which === 'undo' ? 'Nothing to undo.' : 'Nothing to redo.');
                return;
            }
            const res = await this.api.post(`/api/doc/${this.sid}/annotations/${which}`, {});
            this.apply(res.changes, res.history);
            announce(`${which === 'undo' ? 'Undid' : 'Redid'}: ${res.label}`);
        });
    }
    // ------------------------------------------------------------------ the things the person does
    get fixed() { return this.payload.info.layout === 'fixed'; }
    textAnchor(segs) {
        return { kind: this.fixed ? 'pdf-text' : 'text', segments: segs.map((s) => ({ ...(s.block !== undefined ? { block: s.block } : {}), ...(s.page !== undefined ? { page: s.page } : {}), start: s.start, end: s.end })) };
    }
    /** Highlight, underline or strike the current selection. */
    async markSelection(kind, color, opacity) {
        const sel = (await this.view.liveSelection().catch(() => null)) ?? this.sel; // what is selected now, not what was reported a moment ago
        if (!sel) {
            toast('Select some text first.');
            return;
        }
        const style = { color: color ?? (kind === 'highlight' ? this.state.highlightColor : '#ef4444') };
        if (opacity !== undefined)
            style.opacity = opacity;
        const made = await this.create([{ kind, anchor: this.textAnchor(sel.segments), style }]);
        if (made?.length) {
            this.view.clearSelection();
            this.selection(null);
        }
    }
    async createInk(anchor, tool, pts) {
        const look = this.state.prefs.tool[tool];
        const host = String(anchor.block ?? anchor.page);
        this.group = groupId(this.group, host, Date.now(), 2500, rand);
        const points = pts.map((p) => [r5(p.x), r5(p.y), p.p ?? 0.5]);
        await this.create([{ kind: 'ink', anchor, group: this.group.id, geometry: { points }, style: { color: look.color, width: look.width, opacity: tool === 'highlighter' ? 0.4 : 1, tool } }], 'Draw');
    }
    async eraseAnnotations(ids) { await this.remove(ids, 'Erase'); }
    async moveInk(items) {
        await this.run(async () => {
            const res = await this.api.post(`/api/doc/${this.sid}/annotations/update-many`, { items: items.map((i) => ({ id: i.id, rev: this.state.annotations.find((a) => a.id === i.id)?.rev, patch: { geometry: { points: i.points.map((p) => [r5(p[0]), r5(p[1]), p[2] ?? 0.5]) } } })), label: 'Move drawing' });
            this.apply(res.updated.map((a) => ({ id: a.id, annotation: a })), res.history);
        });
    }
    deleteSelection() {
        const ids = [...this.state.inkSelected];
        if (!ids.length && this.state.selectedId)
            ids.push(this.state.selectedId);
        return this.remove(ids);
    }
    /** Add a bookmark here, or take it away if this place is already bookmarked. */
    async toggleBookmark() {
        const loc = this.view.location();
        const anchor = loc.type === 'page' ? { kind: 'page', page: loc.page } : { kind: 'block', block: loc.block ?? undefined };
        if (loc.type === 'flow' && !loc.block) {
            toast('There is nothing to bookmark yet.');
            return;
        }
        const same = this.state.annotations.find((a) => a.kind === 'bookmark' && (loc.type === 'page' ? a.anchor.page === loc.page : a.anchor.block === loc.block));
        if (same) {
            await this.remove([same.id], 'Remove bookmark');
            toast('Bookmark removed. Press Ctrl+Z to undo.');
            return;
        }
        const made = await this.create([{ kind: 'bookmark', anchor }], 'Add bookmark');
        if (made?.length)
            toast(`Bookmarked: ${made[0].body.title ?? ''}`);
    }
    async jumpTo(a) {
        this.state.selectedId = a.id;
        const seg = a.resolved.segments?.[0];
        if (this.fixed) {
            const page = seg?.page ?? a.anchor.page ?? a.resolved.page;
            if (page !== undefined)
                await this.view.goTo({ type: 'page', page, y: a.anchor.y ?? 0, percent: 0 });
        }
        else {
            const block = seg?.block ?? a.anchor.block ?? a.resolved.block;
            if (block)
                await this.view.goTo({ type: 'flow', block, offset: seg?.start ?? 0, percent: 0 });
        }
        this.sync();
    }
    async search(q, caseSensitive = false) {
        return this.api.get(`/api/doc/${this.sid}/search${qs({ q, limit: 500, case: caseSensitive ? 1 : undefined })}`);
    }
}
//# sourceMappingURL=reader.js.map