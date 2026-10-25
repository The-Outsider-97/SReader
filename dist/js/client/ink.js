// Handwriting and drawing, shared by the PDF and the text views. A stroke is stored as fractions of its host (the displayed page, or
// the block it began in), so it follows the content through zoom, resize and reflow. Each gesture is one undo step.
import { bounds, eraserTouches, limitMove, simplify, smoothPath, toFraction, translate } from '../core/geometry.js';
import { toolLook, widthAt } from '../core/strokes.js';
import { svg } from './dom.js';
const NS = 'http://www.w3.org/2000/svg';
const FRAME = { page: [0, 0, 1, 1], block: [-0.5, -1, 1.5, 3] };
/** The drawing layer of a host: one SVG whose coordinates 0..1 are the host's own box. */
export function inkLayer(host) {
    let layer = host.querySelector(':scope > svg.inklayer');
    if (!layer) {
        layer = svg('svg', { class: 'inklayer ink', attrs: { viewBox: '0 0 1 1', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' } });
        host.appendChild(layer);
    }
    return layer;
}
function pointsOf(a) {
    return (a.geometry?.points ?? []).map((p) => ({ x: p[0], y: p[1], p: p[2] ?? 0.5 }));
}
/** Style an SVG path as a stroke. Width is in screen pixels and follows the view's zoom factor (--zf) without a redraw. */
export function styleStroke(path, tool, color, width, opacity, pressure) {
    const look = toolLook(tool, width, opacity);
    const w = Math.max(0.5, widthAt(look.width, pressure, look.pressureGain));
    path.style.stroke = color;
    path.style.strokeWidth = `calc(var(--zf, 1) * ${w.toFixed(2)}px)`;
    path.style.strokeOpacity = String(look.opacity);
    path.style.strokeLinecap = look.cap;
    path.style.strokeLinejoin = 'round';
    path.style.fill = 'none';
    path.style.setProperty('vector-effect', 'non-scaling-stroke');
    if (look.blend !== 'normal')
        path.style.mixBlendMode = look.blend;
}
const fractionPath = (pts) => {
    // smoothPath rounds to 2 decimals, so give it a 1000x scale and let the SVG viewBox bring it back
    return smoothPath(pts.map((p) => ({ x: p.x * 1000, y: p.y * 1000 })));
};
/** Redraw every stroke of one host. */
export function drawInk(layer, anns, selected, ghost = new Set()) {
    layer.setAttribute('viewBox', '0 0 1000 1000');
    while (layer.firstChild)
        layer.removeChild(layer.firstChild);
    for (const a of anns) {
        if (a.kind !== 'ink' || a.visible === false)
            continue;
        const pts = pointsOf(a);
        if (!pts.length)
            continue;
        const g = document.createElementNS(NS, 'g');
        g.setAttribute('data-id', a.id);
        const d = fractionPath(pts);
        const mean = pts.reduce((s, p) => s + (p.p ?? 0.5), 0) / pts.length;
        const stroke = document.createElementNS(NS, 'path');
        stroke.setAttribute('d', d);
        stroke.setAttribute('class', 'stroke' + (selected.has(a.id) ? ' picked' : '') + (ghost.has(a.id) ? ' ghost' : ''));
        styleStroke(stroke, a.style.tool ?? 'pen', a.style.color, a.style.width ?? 3, a.style.opacity, mean);
        const hit = document.createElementNS(NS, 'path');
        hit.setAttribute('d', d);
        hit.setAttribute('class', 'hit');
        hit.setAttribute('data-id', a.id);
        hit.style.strokeWidth = '14px';
        hit.style.setProperty('vector-effect', 'non-scaling-stroke');
        g.appendChild(stroke);
        g.appendChild(hit);
        layer.appendChild(g);
    }
}
export class InkController {
    root;
    env;
    live = null;
    selected = new Set();
    onSelectionChange = [];
    constructor(root, env) {
        this.root = root;
        this.env = env;
        root.addEventListener('pointerdown', (e) => this.down(e));
        root.addEventListener('pointermove', (e) => this.move(e));
        root.addEventListener('pointerup', (e) => { void this.up(e); });
        root.addEventListener('pointercancel', (e) => this.cancel(e));
    }
    onSelect(fn) { this.onSelectionChange.push(fn); }
    fire() { for (const f of this.onSelectionChange)
        f(); }
    get active() { const t = this.env.state.tool; return t === 'pen' || t === 'pencil' || t === 'highlighter' || t === 'eraser' || t === 'move'; }
    /** The reader changed the selection (a click on a stroke, Escape, a delete): tell the owner. */
    setSelected(ids) {
        this.applySelection(ids);
        this.fire();
    }
    /** The owner changed the selection and is telling us: update without echoing it back (that would loop forever). */
    applySelection(ids) {
        this.selected.clear();
        for (const i of ids)
            this.selected.add(i);
    }
    accepts(e) {
        // A pen or a mouse draws; a finger keeps scrolling the page.
        return this.active && e.pointerType !== 'touch' && (e.button === 0 || e.pointerType === 'pen');
    }
    down(e) {
        if (!this.accepts(e))
            return;
        const tool = this.env.state.tool;
        const host = this.env.hostAt(e.target) ?? this.env.hostAt(document.elementFromPoint(e.clientX, e.clientY));
        if (!host) {
            if (tool === 'move') {
                this.setSelected([]);
            }
            return;
        }
        const layer = inkLayer(host.el);
        const box = host.el.getBoundingClientRect();
        const start = toFraction(e.clientX, e.clientY, box, host.kind === 'block');
        this.live = { host, layer, pts: [{ ...start, p: this.pressure(e) }], preview: null, tool, start, dragged: new Set(), erased: new Set(), moved: new Map(), pointerId: e.pointerId, box, out: false };
        if (tool === 'move') {
            const id = e.target?.closest?.('[data-id]')?.getAttribute('data-id') ?? null;
            const picked = id && this.env.inkOf(host).some((a) => a.id === id) ? id : null;
            if (!picked) {
                this.setSelected([]);
                this.live = null;
                return;
            }
            if (e.shiftKey) {
                if (this.selected.has(picked))
                    this.selected.delete(picked);
                else
                    this.selected.add(picked);
                this.fire();
            }
            else if (!this.selected.has(picked))
                this.setSelected([picked]);
            for (const g of Array.from(layer.querySelectorAll('g[data-id]'))) {
                const gid = g.getAttribute('data-id') ?? '';
                if (this.selected.has(gid)) {
                    const ann = this.env.inkOf(host).find((a) => a.id === gid);
                    if (ann)
                        this.live.moved.set(gid, { g, orig: pointsOf(ann) });
                }
            }
        }
        else if (tool !== 'eraser') {
            const p = document.createElementNS(NS, 'path');
            p.setAttribute('class', 'stroke live');
            const t = tool;
            const look = this.env.state.prefs.tool[t];
            styleStroke(p, t, look.color, look.width, t === 'highlighter' ? 0.4 : 1, e.pressure || 0.5);
            layer.setAttribute('viewBox', '0 0 1000 1000');
            layer.appendChild(p);
            this.live.preview = p;
        }
        try {
            this.root.setPointerCapture(e.pointerId);
        }
        catch { /* the pointer may already be gone */ }
        e.preventDefault();
    }
    pressure(e) { return e.pointerType === 'pen' && e.pressure > 0 ? Math.round(e.pressure * 100) / 100 : 0.5; }
    move(e) {
        const live = this.live;
        if (!live || e.pointerId !== live.pointerId)
            return;
        const events = typeof e.getCoalescedEvents === 'function' ? e.getCoalescedEvents() : [];
        for (const ev of events.length ? events : [e])
            this.sample(live, ev);
        e.preventDefault();
    }
    sample(live, e) {
        const pt = toFraction(e.clientX, e.clientY, live.box, live.host.kind === 'block');
        pt.p = this.pressure(e);
        if (live.tool === 'move') {
            const dx = pt.x - live.start.x;
            const dy = pt.y - live.start.y;
            const all = [...live.moved.values()].flatMap((m) => m.orig);
            const lim = limitMove(bounds(all), dx, dy, FRAME[live.host.kind]);
            for (const m of live.moved.values())
                m.g.setAttribute('transform', `translate(${(lim.dx * 1000).toFixed(2)} ${(lim.dy * 1000).toFixed(2)})`);
            return;
        }
        if (live.tool === 'eraser') {
            live.pts.push(pt);
            const radius = 9;
            for (const a of this.env.inkOf(live.host)) {
                if (live.erased.has(a.id))
                    continue;
                if (eraserTouches([pt], pointsOf(a), radius, live.box)) {
                    live.erased.add(a.id);
                    live.layer.querySelector(`g[data-id="${CSS.escape(a.id)}"]`)?.setAttribute('opacity', '0.2');
                }
            }
            return;
        }
        const last = live.pts[live.pts.length - 1];
        if (Math.abs(last.x - pt.x) * live.box.width + Math.abs(last.y - pt.y) * live.box.height < 0.8)
            return;
        if (live.host.kind === 'block' && (pt.x < -0.5 || pt.x > 1.5 || pt.y < -1 || pt.y > 3)) {
            if (!live.out) {
                live.out = true;
                this.env.say('A drawing in a text document belongs to the paragraph it starts in, so it stops here. Start a new stroke in the next paragraph.');
            }
            return;
        }
        live.pts.push(pt);
        live.preview?.setAttribute('d', fractionPath(live.pts));
    }
    async up(e) {
        const live = this.live;
        if (!live || e.pointerId !== live.pointerId)
            return;
        this.live = null;
        try {
            this.root.releasePointerCapture(e.pointerId);
        }
        catch { /* ignore */ }
        live.preview?.remove();
        if (live.tool === 'eraser') {
            if (live.erased.size)
                await this.env.erase([...live.erased]);
            return;
        }
        if (live.tool === 'move') {
            const dx = toFraction(e.clientX, e.clientY, live.box, true).x - live.start.x;
            const dy = toFraction(e.clientX, e.clientY, live.box, true).y - live.start.y;
            if (Math.abs(dx) * live.box.width + Math.abs(dy) * live.box.height < 2) {
                for (const m of live.moved.values())
                    m.g.removeAttribute('transform');
                return;
            }
            const all = [...live.moved.values()].flatMap((m) => m.orig);
            const lim = limitMove(bounds(all), dx, dy, FRAME[live.host.kind]);
            const items = [...live.moved.entries()].map(([id, m]) => ({ id, points: translate(m.orig, lim.dx, lim.dy).map((p) => [p.x, p.y, p.p ?? 0.5]) }));
            await this.env.move(items);
            return;
        }
        // Pen and friends. A tap makes a dot.
        const raw = live.pts;
        const eps = 0.6 / Math.max(live.box.width, live.box.height, 1);
        const pts = raw.length > 2 ? simplify(raw, eps) : raw;
        await this.env.create(live.host, live.tool, pts);
    }
    cancel(e) {
        const live = this.live;
        if (!live || e.pointerId !== live.pointerId)
            return;
        live.preview?.remove();
        for (const m of live.moved.values())
            m.g.removeAttribute('transform');
        for (const id of live.erased)
            live.layer.querySelector(`g[data-id="${CSS.escape(id)}"]`)?.removeAttribute('opacity');
        this.live = null;
    }
}
//# sourceMappingURL=ink.js.map