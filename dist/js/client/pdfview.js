// Reading PDFs: pages are drawn by the service (so the reading colours apply to the page itself), with a transparent text layer on
// top for selection, a layer for highlights, a layer for drawings, and the page's links. Only pages near the screen hold images.
import { bindKeyboardSelection } from './keyselect.js';
import { markStyle } from '../core/appearance.js';
import { qs } from '../core/api.js';
import { clamp } from '../core/geometry.js';
import { buildSpreads, slotsOf, spreadIndexOf, turn } from '../core/spreads.js';
import { cpToU16, hasAstral, u16ToCp } from '../core/text.js';
import { CSS_PX_PER_POINT, pageScale, renderScale, scaleToPercent } from '../core/zoom.js';
import { h, replace } from './dom.js';
import { drawInk, InkController, inkLayer } from './ink.js';
import { safeHref } from './view.js';
const MAX_WORDS = 8000;
const MAX_RENDER_PIXELS = 40_000_000;
export class PdfView {
    ctx;
    payload;
    el;
    wrap;
    sizes;
    count;
    pages = new Map();
    spreads = [];
    at = 0; // current spread (paged layouts)
    scale = CSS_PX_PER_POINT;
    near = null;
    far = null;
    textCache = new Map();
    textLoads = new Map();
    rectCache = new Map();
    hits = new Map();
    ink;
    cleanup = [];
    lastSel = false;
    finalKey = '';
    selTimer = 0;
    posTimer = 0;
    resizeTimer = 0;
    imgTimer = 0;
    destroyed = false;
    building = Promise.resolve();
    built = { layout: '', cover: true, size: '', mode: '', zoom: 0 };
    byPage = new Map();
    constructor(ctx, payload) {
        this.ctx = ctx;
        this.payload = payload;
        this.sizes = payload.info.pageSizes ?? [];
        this.count = payload.info.pages ?? this.sizes.length;
        this.el = h('div', { class: 'view pdfview', id: 'view', attrs: { tabindex: 0, role: 'document', 'aria-label': payload.info.title || 'PDF document' } });
        this.wrap = h('div', { class: 'pdfwrap' });
        this.ink = new InkController(this.el, {
            state: ctx.state,
            hostAt: (t) => this.hostAt(t),
            inkOf: (host) => this.byPage.get(Number(host.key))?.ink.filter((a) => a.status !== 'detached') ?? [],
            create: (host, tool, pts) => ctx.createInk({ kind: 'page', page: Number(host.key) }, tool, pts),
            erase: (ids) => ctx.eraseAnnotations(ids),
            move: (items) => ctx.moveInk(items),
            say: (m) => ctx.say(m),
        });
        this.ink.onSelect(() => ctx.inkSelection([...this.ink.selected]));
        this.bind();
    }
    get values() { return this.ctx.state.effective.values; }
    get theme() { return this.ctx.state.effective.theme; }
    get paged() { const l = this.values.layout; return l === 'single' || l === 'spread' || l === 'focus'; }
    async start(initial) {
        this.index();
        await this.build(initial && initial.type === 'page' ? initial : null);
    }
    destroy() {
        this.destroyed = true;
        this.near?.disconnect();
        this.far?.disconnect();
        for (const fn of this.cleanup)
            fn();
        for (const t of [this.selTimer, this.posTimer, this.resizeTimer, this.imgTimer])
            window.clearTimeout(t);
        for (const p of this.pages.values())
            p.img.removeAttribute('src');
        this.el.remove();
    }
    bind() {
        const on = (t, ev, fn) => { t.addEventListener(ev, fn); this.cleanup.push(() => t.removeEventListener(ev, fn)); };
        on(this.el, 'scroll', () => this.scheduleLocation());
        on(this.el, 'mouseup', () => window.setTimeout(() => { void this.emitSelection(true); }, 0));
        on(this.el, 'keyup', (e) => { if (e.key.startsWith('Arrow') || e.key === 'Shift')
            window.setTimeout(() => { void this.emitSelection(true); }, 0); });
        this.cleanup.push(bindKeyboardSelection(this.el));
        on(this.el, 'click', (e) => this.click(e));
        on(this.el, 'copy', (e) => this.copy(e));
        const sel = () => { window.clearTimeout(this.selTimer); this.selTimer = window.setTimeout(() => { void this.emitSelection(false); }, 160); };
        document.addEventListener('selectionchange', sel);
        this.cleanup.push(() => document.removeEventListener('selectionchange', sel));
        const rs = () => { window.clearTimeout(this.resizeTimer); this.resizeTimer = window.setTimeout(() => { if (!this.destroyed)
            this.appearanceChanged(); }, 200); };
        window.addEventListener('resize', rs);
        this.cleanup.push(() => window.removeEventListener('resize', rs));
    }
    // ------------------------------------------------------------------ layout
    computeScale() {
        const v = this.values;
        const viewport = { width: Math.max(200, this.el.clientWidth - 24), height: Math.max(200, this.el.clientHeight - 24) };
        const cols = v.layout === 'spread' ? 2 : 1;
        const widest = this.sizes.reduce((m, s) => (s[0] > m[0] ? s : m), this.sizes[0] ?? [612, 792]);
        const ref = v.zoomMode === 'fit-page' ? (this.sizes[this.currentPage()] ?? widest) : widest;
        return pageScale(v.zoomMode, v.zoom, { width: ref[0], height: ref[1] }, viewport, cols, 18);
    }
    currentPage() {
        const sp = this.spreads[this.at];
        return this.paged ? (sp ? sp[0] : 0) : this.pages.size ? this.safePage() : 0;
    }
    safePage() { try {
        const l = this.location();
        return l.type === 'page' ? l.page : 0;
    }
    catch {
        return 0;
    } }
    async build(at) {
        const run = async () => {
            const keep = at ?? (this.pages.size ? this.location() : null);
            this.near?.disconnect();
            this.far?.disconnect();
            for (const p of this.pages.values())
                p.img.removeAttribute('src');
            this.pages.clear();
            const v = this.values;
            this.spreads = buildSpreads(this.count, v.layout, v.coverAlone);
            this.scale = this.computeScale();
            this.built = { layout: v.layout, cover: v.coverAlone, size: `${this.el.clientWidth}x${this.el.clientHeight}`, mode: v.zoomMode, zoom: v.zoom };
            this.el.style.setProperty('--zf', String(this.scale / CSS_PX_PER_POINT));
            this.wrap = h('div', { class: 'pdfwrap' });
            this.wrap.style.setProperty('--s', String(this.scale));
            replace(this.el, this.wrap);
            this.near = new IntersectionObserver((es) => { for (const e of es)
                if (e.isIntersecting)
                    this.activate(Number(e.target.dataset.page)); }, { root: this.el, rootMargin: '900px 0px 900px 0px' });
            this.far = new IntersectionObserver((es) => { for (const e of es)
                if (!e.isIntersecting)
                    this.deactivate(Number(e.target.dataset.page)); }, { root: this.el, rootMargin: '3500px 0px 3500px 0px' });
            this.at = keep ? spreadIndexOf(this.spreads, keep.page) : 0;
            if (this.paged)
                this.showSpread();
            else
                this.buildAllRows();
            if (keep && !this.paged)
                this.place(keep, false);
            this.scheduleLocation();
        };
        this.building = this.building.then(run, run);
        return this.building;
    }
    makeHost(n) {
        const [w, hgt] = this.sizes[n] ?? [612, 792];
        const host = h('div', { class: 'pagehost', data: { page: n }, attrs: { role: 'img', 'aria-label': `Page ${n + 1} of ${this.count}` } });
        host.style.width = `${Math.round(w * this.scale)}px`;
        host.style.height = `${Math.round(hgt * this.scale)}px`;
        host.style.setProperty('--s', String(this.scale));
        const img = h('img', { class: 'page', attrs: { alt: '', draggable: 'false', decoding: 'async' } });
        const marks = h('div', { class: 'markslayer', attrs: { 'aria-hidden': 'true' } });
        const text = h('div', { class: 'textlayer', attrs: { lang: this.payload.info.language || undefined } });
        const links = h('div', { class: 'linklayer' });
        const pins = h('div', { class: 'pinlayer' });
        const label = h('div', { class: 'pagelabel', text: `${n + 1}` });
        host.append(img, marks, text, links, pins, label);
        const st = { n, host, img, text, marks, links, pins, active: false, imgKey: '', sig: '', token: 0, hasText: false, hasMarks: false, hasLinks: false };
        this.pages.set(n, st);
        this.near?.observe(host);
        this.far?.observe(host);
        return st;
    }
    buildAllRows() {
        const frag = document.createDocumentFragment();
        for (const sp of this.spreads)
            frag.appendChild(this.makeRow(sp));
        this.wrap.appendChild(frag);
    }
    makeRow(sp) {
        const row = h('div', { class: 'row-pages' });
        const slots = this.values.layout === 'spread' ? slotsOf(sp, this.count, this.values.coverAlone) : [sp[0]];
        for (const slot of slots) {
            if (slot === null) {
                const ref = this.sizes[sp[0]] ?? [612, 792];
                const blank = h('div', { class: 'pagehost cover-slot', attrs: { 'aria-hidden': 'true' } });
                blank.style.width = `${Math.round(ref[0] * this.scale)}px`;
                blank.style.height = `${Math.round(ref[1] * this.scale)}px`;
                row.appendChild(blank);
            }
            else
                row.appendChild(this.makeHost(slot).host);
        }
        return row;
    }
    showSpread() {
        this.near?.disconnect();
        this.far?.disconnect();
        for (const p of this.pages.values())
            p.img.removeAttribute('src');
        this.pages.clear();
        this.wrap.style.setProperty('--s', String(this.scale));
        replace(this.wrap, this.makeRow(this.spreads[this.at] ?? [0]));
        this.el.scrollTop = 0;
        this.scheduleLocation();
    }
    appearanceChanged() {
        if (this.destroyed)
            return;
        const v = this.values;
        const size = `${this.el.clientWidth}x${this.el.clientHeight}`;
        if (v.layout !== this.built.layout || v.coverAlone !== this.built.cover) {
            void this.build(null);
            return;
        }
        const scale = this.computeScale();
        const resized = size !== this.built.size || v.zoomMode !== this.built.mode || v.zoom !== this.built.zoom;
        this.built = { ...this.built, size, mode: v.zoomMode, zoom: v.zoom };
        if (Math.abs(scale - this.scale) > 1e-4) {
            const keep = this.safeKeep();
            this.scale = scale;
            this.el.style.setProperty('--zf', String(scale / CSS_PX_PER_POINT));
            this.wrap.style.setProperty('--s', String(scale));
            for (const p of this.pages.values())
                this.sizeHost(p);
            for (const blank of Array.from(this.wrap.querySelectorAll('.cover-slot'))) {
                const ref = this.sizes[0] ?? [612, 792];
                blank.style.width = `${Math.round(ref[0] * scale)}px`;
                blank.style.height = `${Math.round(ref[1] * scale)}px`;
            }
            if (keep && !this.paged)
                void this.goTo(keep, false);
        }
        else if (!resized) { /* colours only */ }
        window.clearTimeout(this.imgTimer);
        this.imgTimer = window.setTimeout(() => { for (const p of this.pages.values())
            if (p.active)
                this.loadImage(p); this.refreshLayers(); }, 120);
    }
    zoomPercent() { return scaleToPercent(this.scale); }
    safeKeep() { try {
        return this.location();
    }
    catch {
        return null;
    } }
    sizeHost(p) {
        const [w, hgt] = this.sizes[p.n] ?? [612, 792];
        p.host.style.width = `${Math.round(w * this.scale)}px`;
        p.host.style.height = `${Math.round(hgt * this.scale)}px`;
        p.host.style.setProperty('--s', String(this.scale));
    }
    // ------------------------------------------------------------------ page content
    imageUrl(p) {
        const v = this.values;
        const size = this.sizes[p.n] ?? [612, 792];
        const wanted = renderScale(this.scale, window.devicePixelRatio || 1, MAX_RENDER_PIXELS, { width: size[0], height: size[1] });
        const q = Math.min(8, Math.max(0.1, Math.ceil(wanted * 8) / 8));
        return `/api/doc/${this.ctx.sid}/page/${p.n}/image${qs({ scale: q, theme: this.theme, pa: v.pdfAppearance, fc: v.fontColor ? v.fontColor.slice(1) : undefined, fo: v.fontOpacity < 100 ? v.fontOpacity : undefined })}`;
    }
    loadImage(p) {
        const url = this.imageUrl(p);
        if (p.imgKey === url)
            return;
        p.imgKey = url;
        p.img.alt = `Page ${p.n + 1}`;
        p.img.src = url;
    }
    activate(n) {
        const p = this.pages.get(n);
        if (!p || this.destroyed)
            return;
        if (!p.active) {
            p.active = true;
            p.token++;
        }
        this.loadImage(p);
        void this.fillText(p);
        void this.fillMarks(p);
        void this.fillLinks(p);
        this.fillPins(p);
    }
    deactivate(n) {
        const p = this.pages.get(n);
        if (!p || !p.active)
            return;
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed && sel.containsNode(p.host, true))
            return; // never pull a page out from under a selection
        p.active = false;
        p.token++;
        p.img.removeAttribute('src');
        p.imgKey = '';
        replace(p.text);
        replace(p.marks);
        replace(p.links);
        replace(p.pins);
        p.hasText = p.hasMarks = p.hasLinks = false;
        p.sig = '';
        p.host.querySelector('svg.inklayer')?.remove();
    }
    pageText(n) {
        const hit = this.textCache.get(n);
        if (hit)
            return Promise.resolve(hit);
        let load = this.textLoads.get(n);
        if (!load) {
            load = this.ctx.api.get(`/api/doc/${this.ctx.sid}/page/${n}/text`).then((t) => {
                this.textCache.set(n, t);
                if (this.textCache.size > 80)
                    this.textCache.delete(this.textCache.keys().next().value);
                this.textLoads.delete(n);
                return t;
            }, (err) => { this.textLoads.delete(n); throw err; });
            this.textLoads.set(n, load);
        }
        return load;
    }
    async fillText(p) {
        if (p.hasText)
            return;
        p.hasText = true;
        const token = p.token;
        try {
            if (!this.payload.capabilities.textSelection)
                return;
            const t = await this.pageText(p.n);
            if (this.destroyed || !p.active || p.token !== token)
                return;
            const words = t.words.slice(0, MAX_WORDS);
            if (t.words.length > MAX_WORDS)
                this.ctx.say(`Page ${p.n + 1} has an unusually large number of words; only the first ${MAX_WORDS} can be selected.`);
            const astral = hasAstral(t.text);
            const frag = document.createDocumentFragment();
            const spans = [];
            for (const w of words) {
                const [s, e, x0, y0, x1, y1] = w;
                const turn = w[6] ?? 0; // 0 left to right, 90 top to bottom, 180 right to left, 270 bottom to top
                const text = astral ? t.text.slice(cpToU16(t.text, s), cpToU16(t.text, e)) : t.text.slice(s, e);
                const span = h('span', { text, data: { s, e, w: (x1 - x0).toFixed(2), h: (y1 - y0).toFixed(2), o: String(turn) } });
                if (turn === 90 || turn === 270)
                    span.classList.add('v');
                span.style.left = `${(x0 / t.width * 100).toFixed(3)}%`;
                span.style.top = `${(y0 / t.height * 100).toFixed(3)}%`;
                // The letters' height is the line thickness: the box's height for upright text, its width for text running down or up the page.
                const thickness = turn === 90 || turn === 270 ? x1 - x0 : y1 - y0;
                span.style.fontSize = `calc(var(--s) * ${Math.max(1, thickness).toFixed(2)}px)`;
                spans.push(span);
                frag.appendChild(span);
            }
            replace(p.text, frag);
            // Stretch each word along its line to the length the page gives it, so selection rectangles follow the printed text. Words on a
            // rotated page run down, up or backwards; the transform puts the word's own letter order over the printed one.
            const natural = spans.map((s) => s.getBoundingClientRect());
            spans.forEach((s, i) => {
                const turn = Number(s.dataset.o);
                const tw = Number(s.dataset.w) * this.scale, th = Number(s.dataset.h) * this.scale;
                const along = turn === 90 || turn === 270;
                const nat = along ? natural[i].height : natural[i].width;
                const target = along ? th : tw;
                if (!(nat > 0.5 && target > 0.5))
                    return;
                const k = clamp(target / nat, 0.2, 5).toFixed(4);
                const stretch = along ? `scale(1, ${k})` : `scale(${k}, 1)`;
                s.style.transform = turn >= 180 ? `translate(${tw.toFixed(2)}px, ${th.toFixed(2)}px) rotate(180deg) ${stretch}` : stretch;
            });
        }
        catch {
            p.hasText = false;
        }
    }
    async fillMarks(p) {
        const token = p.token;
        const wantsMarks = (this.byPage.get(p.n)?.text.length ?? 0) > 0 || this.hits.has(p.n);
        if (!wantsMarks) {
            replace(p.marks);
            p.hasMarks = false;
            return;
        }
        try {
            const marks = (this.byPage.get(p.n)?.text.length ?? 0) > 0 ? await this.ctx.api.get(`/api/doc/${this.ctx.sid}/page/${p.n}/marks`) : [];
            const hits = await Promise.all((this.hits.get(p.n) ?? []).map(async (hit) => ({ hit, rects: await this.hitRects(p.n, hit.start, hit.end) })));
            if (this.destroyed || !p.active || p.token !== token)
                return;
            const [W, H] = this.sizes[p.n] ?? [612, 792];
            const frag = document.createDocumentFragment();
            const rect = (r, cls, ann, extra) => {
                const el = h('div', { class: `m ${cls}`.trim(), data: { ann: ann?.id } });
                el.style.left = `${(r[0] / W * 100).toFixed(3)}%`;
                el.style.top = `${(r[1] / H * 100).toFixed(3)}%`;
                el.style.width = `${((r[2] - r[0]) / W * 100).toFixed(3)}%`;
                el.style.height = `${((r[3] - r[1]) / H * 100).toFixed(3)}%`;
                extra?.(el);
                frag.appendChild(el);
            };
            for (const m of marks) {
                const ann = this.ctx.state.annotations.find((a) => a.id === m.id);
                if (!ann)
                    continue;
                const sel = this.ctx.state.selectedId === ann.id ? ' sel' : '';
                const moved = m.status === 'moved' ? ' moved' : '';
                const cls = (ann.kind === 'underline' ? 'u' : ann.kind === 'strike' ? 's' : ann.kind === 'note' ? 'n' : '') + sel + moved;
                for (const r of m.rects)
                    rect(r, cls, ann, (el) => {
                        const st = markStyle(ann.style.color, ann.style.opacity, this.theme);
                        el.style.setProperty('--mark-bg', ann.kind === 'highlight' ? st.background : 'transparent');
                        el.style.setProperty('--mark-line', ann.style.color);
                    });
            }
            for (const { hit, rects } of hits)
                for (const r of rects)
                    rect(r, 'hit' + (hit.current ? ' current' : ''));
            replace(p.marks, frag);
            p.hasMarks = true;
        }
        catch { /* the page is still readable without its marks; they are retried on the next refresh */ }
    }
    async hitRects(page, start, end) {
        const key = `${page}:${start}:${end}`;
        const hit = this.rectCache.get(key);
        if (hit)
            return hit;
        const rects = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/page/${page}/range${qs({ start, end })}`);
        this.rectCache.set(key, rects);
        if (this.rectCache.size > 3000)
            this.rectCache.delete(this.rectCache.keys().next().value);
        return rects;
    }
    async fillLinks(p) {
        if (p.hasLinks)
            return;
        p.hasLinks = true;
        const token = p.token;
        try {
            const links = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/page/${p.n}/links`);
            if (this.destroyed || !p.active || p.token !== token)
                return;
            const [W, H] = this.sizes[p.n] ?? [612, 792];
            const frag = document.createDocumentFragment();
            for (const l of links.slice(0, 400)) {
                const r = l.rect;
                if (!r || r.length < 4)
                    continue;
                const uri = l.uri ? safeHref(l.uri) : null;
                const dest = typeof l.page === 'number' ? l.page : null;
                if (!uri && dest === null)
                    continue;
                const a = uri ? h('a', { class: 'plink', attrs: { href: uri, target: '_blank', rel: 'noopener noreferrer nofollow', 'aria-label': `Link to ${uri}` }, title: uri })
                    : h('a', { class: 'plink', attrs: { href: '#', 'aria-label': `Go to page ${dest + 1}` }, title: `Go to page ${dest + 1}`, on: { click: (ev) => { ev.preventDefault(); void this.goTo({ type: 'page', page: dest, y: 0, percent: 0 }); } } });
                a.style.left = `${(r[0] / W * 100).toFixed(3)}%`;
                a.style.top = `${(r[1] / H * 100).toFixed(3)}%`;
                a.style.width = `${((r[2] - r[0]) / W * 100).toFixed(3)}%`;
                a.style.height = `${((r[3] - r[1]) / H * 100).toFixed(3)}%`;
                frag.appendChild(a);
            }
            replace(p.links, frag);
        }
        catch {
            p.hasLinks = false;
        }
    }
    fillPins(p) {
        const extra = this.byPage.get(p.n) ?? { text: [], ink: [], stickies: [], bookmarks: [] };
        const sel = this.ctx.state.selectedId;
        const sig = JSON.stringify([extra.ink.map((a) => `${a.id}.${a.rev}`), extra.stickies.map((a) => `${a.id}.${a.rev}`), extra.bookmarks.map((a) => `${a.id}.${a.rev}`), sel, [...this.ctx.state.inkSelected], this.theme]);
        if (sig === p.sig)
            return;
        p.sig = sig;
        const frag = document.createDocumentFragment();
        for (const s of extra.stickies) {
            const pin = h('button', { class: 'stickypin' + (sel === s.id ? ' open' : ''), text: '✎', title: s.body.text ? s.body.text.slice(0, 80) : 'Sticky note',
                attrs: { type: 'button', 'aria-label': `Sticky note${s.body.text ? ': ' + s.body.text.slice(0, 60) : ''}` }, data: { ann: s.id } });
            pin.style.left = `${Math.round((s.anchor.x ?? 0.5) * 1000) / 10}%`;
            pin.style.top = `${Math.round((s.anchor.y ?? 0.1) * 1000) / 10}%`;
            pin.style.background = s.style.color;
            frag.appendChild(pin);
        }
        if (extra.bookmarks.length) {
            const bm = extra.bookmarks[0];
            frag.appendChild(h('button', { class: 'bookmark-flag', text: '⚑', title: `Bookmark: ${bm.body.title ?? ''}`, attrs: { type: 'button', 'aria-label': `Bookmark: ${bm.body.title ?? ''}` }, data: { ann: bm.id } }));
        }
        replace(p.pins, frag);
        const layer = inkLayer(p.host);
        drawInk(layer, extra.ink, this.ctx.state.inkSelected);
    }
    refreshLayers() {
        for (const p of this.pages.values())
            if (p.active) {
                p.sig = '';
                this.fillPins(p);
                void this.fillMarks(p);
            }
    }
    // ------------------------------------------------------------------ annotations and hits
    index() {
        this.byPage.clear();
        const get = (n) => { let e = this.byPage.get(n); if (!e)
            this.byPage.set(n, (e = { text: [], ink: [], stickies: [], bookmarks: [] })); return e; };
        for (const a of this.ctx.state.annotations) {
            if (a.visible === false)
                continue;
            if (a.anchor.kind === 'pdf-text') {
                for (const s of a.resolved.segments ?? [])
                    if (s.page !== undefined && s.status !== 'detached') {
                        const e = get(s.page);
                        if (!e.text.includes(a))
                            e.text.push(a);
                    }
            }
            else if (a.status !== 'detached') {
                const page = a.anchor.page ?? a.resolved.page;
                if (page === undefined)
                    continue;
                if (a.kind === 'ink')
                    get(page).ink.push(a);
                else if (a.kind === 'sticky')
                    get(page).stickies.push(a);
                else if (a.kind === 'bookmark')
                    get(page).bookmarks.push(a);
            }
        }
    }
    textSig = '';
    annotationsChanged() {
        const sig = JSON.stringify(this.ctx.state.annotations.filter((a) => a.anchor.kind === 'pdf-text').map((a) => [a.id, a.rev, a.status, a.style.color, a.style.opacity, a.visible]));
        const selected = this.ctx.state.selectedId;
        this.index();
        const textChanged = sig !== this.textSig || selected !== this.lastSelected;
        this.textSig = sig;
        this.lastSelected = selected;
        for (const p of this.pages.values()) {
            if (!p.active)
                continue;
            this.fillPins(p);
            if (textChanged)
                void this.fillMarks(p);
        }
    }
    lastSelected = null;
    setInkSelection(ids) {
        this.ink.applySelection(ids);
        for (const p of this.pages.values())
            if (p.active) {
                p.sig = '';
                this.fillPins(p);
            }
    }
    setHits(hits, current) {
        this.hits.clear();
        hits.forEach((hit, i) => {
            if (hit.page === undefined || hit.start === null || hit.end === null)
                return;
            const list = this.hits.get(hit.page) ?? [];
            list.push({ start: hit.start, end: hit.end, current: i === current });
            this.hits.set(hit.page, list);
        });
        for (const p of this.pages.values())
            if (p.active)
                void this.fillMarks(p);
        const hit = hits[current];
        if (hit?.page !== undefined) {
            void this.goTo({ type: 'page', page: hit.page, y: 0, percent: 0 }, false).then(async () => {
                if (hit.start === null || hit.end === null)
                    return;
                const rects = await this.hitRects(hit.page, hit.start, hit.end);
                const p = this.pages.get(hit.page);
                if (!p || !rects.length)
                    return;
                const [, H] = this.sizes[hit.page] ?? [612, 792];
                const rel = p.host.getBoundingClientRect().top + (rects[0][1] / H) * p.host.getBoundingClientRect().height;
                const box = this.el.getBoundingClientRect();
                if (rel < box.top + 60 || rel > box.bottom - 60)
                    this.el.scrollTop += rel - box.top - box.height / 3;
            });
        }
    }
    rectOf(id) {
        const el = this.el.querySelector(`[data-ann="${CSS.escape(id)}"]`);
        if (el)
            return el.getBoundingClientRect();
        const a = this.ctx.state.annotations.find((x) => x.id === id);
        const page = a?.anchor.page ?? a?.resolved.page ?? a?.resolved.segments?.[0]?.page;
        return page === undefined ? null : this.pages.get(page)?.host.getBoundingClientRect() ?? null;
    }
    // ------------------------------------------------------------------ navigation
    hosts() { return [...this.pages.values()].sort((a, b) => a.n - b.n); }
    location() {
        const hosts = this.hosts();
        if (!hosts.length)
            return { type: 'page', page: 0, y: 0, percent: 0 };
        const top = this.el.getBoundingClientRect().top;
        let lo = 0;
        let hi = hosts.length - 1;
        let ans = hi;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (hosts[mid].host.getBoundingClientRect().bottom > top + 4) {
                ans = mid;
                hi = mid - 1;
            }
            else
                lo = mid + 1;
        }
        const p = hosts[ans];
        const r = p.host.getBoundingClientRect();
        const y = clamp((top - r.top) / Math.max(1, r.height), 0, 0.999);
        return { type: 'page', page: p.n, y: Math.round(y * 1000) / 1000, percent: (p.n + y) / Math.max(1, this.count) };
    }
    scheduleLocation() {
        window.clearTimeout(this.posTimer);
        this.posTimer = window.setTimeout(() => { if (!this.destroyed)
            this.ctx.location(this.location()); }, 250);
    }
    async goTo(loc, flash = true) {
        await this.building;
        this.place(loc, flash);
    }
    /** Scrolls to a location without waiting for a build (build() itself calls this, so waiting would deadlock). */
    place(loc, flash) {
        const page = clamp(loc.type === 'page' ? loc.page : Math.floor(loc.percent * this.count), 0, this.count - 1);
        const y = loc.type === 'page' ? loc.y : 0;
        if (this.paged) {
            const idx = spreadIndexOf(this.spreads, page);
            if (idx !== this.at || !this.pages.has(page)) {
                this.at = idx;
                this.showSpread();
            }
        }
        const p = this.pages.get(page);
        if (!p)
            return;
        const box = this.el.getBoundingClientRect();
        const r = p.host.getBoundingClientRect();
        this.el.scrollTop += r.top - box.top + y * r.height - (y > 0 ? 0 : 14);
        this.activate(page);
        if (flash) {
            p.host.classList.add('flash');
            window.setTimeout(() => p.host.classList.remove('flash'), 1700);
        }
        this.scheduleLocation();
    }
    step(direction) {
        if (this.paged) {
            const next = turn(this.spreads, this.at, direction);
            if (next !== this.at) {
                this.at = next;
                this.showSpread();
            }
            return;
        }
        this.el.scrollBy({ top: direction * Math.max(80, this.el.clientHeight * 0.9) });
    }
    edge(which) {
        if (this.paged) {
            this.at = which === 'start' ? 0 : this.spreads.length - 1;
            this.showSpread();
            return;
        }
        this.el.scrollTop = which === 'start' ? 0 : this.el.scrollHeight;
    }
    pageInfo() {
        if (this.paged) {
            const sp = this.spreads[this.at] ?? [0];
            const label = sp.length === 2 ? `Pages ${sp[0] + 1}–${sp[1] + 1}` : `Page ${sp[0] + 1}`;
            return { current: sp[0] + 1, total: this.count, label: `${label} of ${this.count}`, approximate: false };
        }
        const loc = this.location();
        const page = loc.type === 'page' ? loc.page : 0;
        return { current: page + 1, total: this.count, label: `Page ${page + 1} of ${this.count}`, approximate: false };
    }
    // ------------------------------------------------------------------ selection
    spanTexts() {
        const out = new Map();
        for (const p of this.pages.values())
            if (p.hasText && p.text.firstChild)
                out.set(p.n, { layer: p.text, spans: Array.from(p.text.children) });
        return out;
    }
    rangeSegments(range) {
        const segs = [];
        for (const [page, { layer, spans }] of [...this.spanTexts()].sort((a, b) => a[0] - b[0])) {
            if (!range.intersectsNode(layer))
                continue;
            let lo = Infinity;
            let hi = -Infinity;
            for (const span of spans) {
                if (!range.intersectsNode(span))
                    continue;
                const node = span.firstChild;
                const text = span.textContent ?? '';
                const whole = document.createRange();
                whole.selectNodeContents(span);
                let a = 0;
                let b = text.length;
                if (range.compareBoundaryPoints(Range.START_TO_START, whole) > 0)
                    a = range.startContainer === node ? range.startOffset : range.startContainer === span ? (range.startOffset === 0 ? 0 : text.length) : text.length;
                if (range.compareBoundaryPoints(Range.END_TO_END, whole) < 0)
                    b = range.endContainer === node ? range.endOffset : range.endContainer === span ? (range.endOffset === 0 ? 0 : text.length) : 0;
                if (b <= a)
                    continue;
                const s = Number(span.dataset.s);
                lo = Math.min(lo, s + u16ToCp(text, a));
                hi = Math.max(hi, s + u16ToCp(text, b));
            }
            if (hi > lo)
                segs.push({ page, start: lo, end: hi });
        }
        return segs;
    }
    textOf(segs) {
        return segs.map((s) => { const t = this.textCache.get(s.page); return t ? t.text.slice(cpToU16(t.text, s.start), cpToU16(t.text, s.end)) : ''; }).filter(Boolean).join('\n');
    }
    async computeSelection(final) {
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed)
            return null;
        const range = sel.getRangeAt(0);
        if (!this.el.contains(range.commonAncestorContainer))
            return null;
        let segs = this.rangeSegments(range);
        if (!segs.length)
            return null;
        if (final && segs.length > 1) {
            // Pages between the first and last that are not on screen any more still belong to the selection.
            const first = segs[0].page;
            const last = segs[segs.length - 1].page;
            const have = new Set(segs.map((s) => s.page));
            const filled = [...segs];
            for (let n = first + 1; n < last; n++) {
                if (have.has(n))
                    continue;
                try {
                    const t = await this.pageText(n);
                    if (t.text.length)
                        filled.push({ page: n, start: 0, end: t.text.length });
                }
                catch { /* skip an unreadable page */ }
            }
            segs = filled.sort((a, b) => a.page - b.page);
        }
        const rects = range.getClientRects();
        const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
        const text = this.textOf(segs);
        if (!text.trim())
            return null;
        return { segments: segs, text, rect, final };
    }
    async emitSelection(final) {
        if (this.destroyed)
            return;
        const info = await this.computeSelection(final);
        if (!info) {
            this.finalKey = '';
            if (this.lastSel) {
                this.lastSel = false;
                this.ctx.selection(null);
            }
            return;
        }
        // The debounced "selection changed" report arrives just after the mouse is released; it must not close the tools that release opened.
        const key = JSON.stringify(info.segments);
        if (final)
            this.finalKey = key;
        else if (key === this.finalKey)
            return;
        this.lastSel = true;
        this.ctx.selection(info);
    }
    selectedText() {
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed)
            return '';
        return this.textOf(this.rangeSegments(sel.getRangeAt(0)));
    }
    clearSelection() { window.getSelection()?.removeAllRanges(); this.lastSel = false; this.finalKey = ''; }
    copy(e) {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed || !this.el.contains(sel.anchorNode))
            return;
        e.preventDefault();
        if (!this.ctx.canCopy()) {
            this.ctx.say('This PDF does not allow copying text.');
            return;
        }
        // Words are separate pieces on screen, so the text comes from the document itself (spacing and line breaks as written).
        e.clipboardData?.setData('text/plain', this.selectedText());
    }
    // ------------------------------------------------------------------ pointer
    hostAt(target) {
        const el = target?.closest?.('.pagehost[data-page]');
        return el ? { kind: 'page', key: el.dataset.page, el } : null;
    }
    click(e) {
        const t = e.target;
        if (!t || this.ink.active)
            return;
        const pin = t.closest('.stickypin, .bookmark-flag');
        if (pin?.dataset.ann) {
            this.ctx.openAnnotation(pin.dataset.ann, pin);
            return;
        }
        if (t.closest('.plink'))
            return;
        const hostEl = t.closest('.pagehost[data-page]');
        if (!hostEl)
            return;
        const r = hostEl.getBoundingClientRect();
        if (this.ctx.state.tool === 'sticky') {
            const anchor = { kind: 'page', page: Number(hostEl.dataset.page), x: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1), y: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1) };
            this.ctx.placeSticky(anchor, new DOMRect(e.clientX, e.clientY, 1, 1));
            return;
        }
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed)
            return;
        for (const m of Array.from(hostEl.querySelectorAll('.markslayer .m[data-ann]')).reverse()) {
            const b = m.getBoundingClientRect();
            if (e.clientX >= b.left - 1 && e.clientX <= b.right + 1 && e.clientY >= b.top - 2 && e.clientY <= b.bottom + 2) {
                this.ctx.openAnnotation(m.dataset.ann, m);
                return;
            }
        }
    }
}
//# sourceMappingURL=pdfview.js.map