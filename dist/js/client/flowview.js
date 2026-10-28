// Reading text documents (SLDC, EPUB, DOCX, ODT, RTF, TXT, Markdown, HTML): scrolling in lazily loaded pieces, or paged
// (single page / two-page spread). Every block carries its id, so positions, bookmarks and annotations survive font and window changes.
import { bindKeyboardSelection } from './keyselect.js';
import { clamp } from '../core/geometry.js';
import { cpToU16, u16ToCp } from '../core/text.js';
import { buildSpreads, spreadIndexOf, turn } from '../core/spreads.js';
import { h, replace } from './dom.js';
import { InkController } from './ink.js';
import { renderBlock, TEXT_TYPES } from './flowrender.js';
import { qs } from '../core/api.js';
const CHUNK = 50;
const KEEP_CHUNKS = 28;
const FONTS = {
    serif: 'Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif',
    sans: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
    mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
};
export class FlowView {
    ctx;
    payload;
    el;
    root;
    info;
    total;
    chunks = [];
    loaded = new Map();
    ordered = null;
    io = null;
    avg = 72;
    mode = 'scroll';
    paged = { pages: [], spreads: [], at: 0, cache: new Map(), key: '' };
    extras = new Map();
    hitMap = new Map();
    hitList = [];
    currentHit = -1;
    ink;
    cleanup = [];
    lastSel = false;
    finalKey = '';
    selTimer = 0;
    posTimer = 0;
    resizeTimer = 0;
    destroyed = false;
    building = Promise.resolve();
    built = { layout: '', cover: true, fs: 0, family: '', spacing: 0, w: 0, zoom: 0, size: '' };
    constructor(ctx, payload) {
        this.ctx = ctx;
        this.payload = payload;
        this.info = payload.info;
        this.total = payload.info.blockCount ?? 0;
        this.el = h('div', { class: 'view flowview', id: 'view', attrs: { tabindex: 0, role: 'document', 'aria-label': payload.info.title || 'Document' } });
        this.root = h('div', { class: 'flow' });
        this.ink = new InkController(this.el, {
            state: ctx.state,
            hostAt: (t) => this.hostAt(t),
            inkOf: (host) => ctx.state.annotations.filter((a) => a.kind === 'ink' && a.anchor.kind === 'block' && a.anchor.block === host.key && a.status !== 'detached'),
            create: (host, tool, pts) => ctx.createInk({ kind: 'block', block: host.key }, tool, pts),
            erase: (ids) => ctx.eraseAnnotations(ids),
            move: (items) => ctx.moveInk(items),
            say: (m) => ctx.say(m),
        });
        this.ink.onSelect(() => ctx.inkSelection([...this.ink.selected]));
        this.bind();
    }
    // ------------------------------------------------------------------ life cycle
    get values() { return this.ctx.state.effective.values; }
    get theme() { return this.ctx.state.effective.theme; }
    desiredMode() { const l = this.values.layout; return l === 'single' || l === 'spread' ? 'paged' : 'scroll'; }
    async start(initial) {
        this.buildIndex();
        await this.build(initial && initial.type === 'flow' ? initial : null);
    }
    destroy() {
        this.destroyed = true;
        this.io?.disconnect();
        for (const fn of this.cleanup)
            fn();
        window.clearTimeout(this.selTimer);
        window.clearTimeout(this.posTimer);
        window.clearTimeout(this.resizeTimer);
        this.el.remove();
    }
    bind() {
        const on = (t, ev, fn) => { t.addEventListener(ev, fn); this.cleanup.push(() => t.removeEventListener(ev, fn)); };
        on(this.el, 'scroll', () => this.scheduleLocation());
        on(this.el, 'mouseup', () => window.setTimeout(() => this.emitSelection(true), 0));
        this.cleanup.push(bindKeyboardSelection(this.el));
        on(this.el, 'keyup', (e) => { if (e.key.startsWith('Arrow') || e.key === 'Shift')
            window.setTimeout(() => this.emitSelection(true), 0); });
        on(this.el, 'click', (e) => this.click(e));
        on(this.el, 'keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList?.contains('ref')) {
            e.preventDefault();
            this.click(e);
        } });
        on(this.el, 'copy', (e) => { if (!this.ctx.canCopy()) {
            e.preventDefault();
            this.ctx.say('This document does not allow copying text.');
        } });
        const sel = () => { window.clearTimeout(this.selTimer); this.selTimer = window.setTimeout(() => this.emitSelection(false), 160); };
        document.addEventListener('selectionchange', sel);
        this.cleanup.push(() => document.removeEventListener('selectionchange', sel));
        const rs = () => { window.clearTimeout(this.resizeTimer); this.resizeTimer = window.setTimeout(() => { if (!this.destroyed)
            this.appearanceChanged(); }, 250); };
        window.addEventListener('resize', rs);
        this.cleanup.push(() => window.removeEventListener('resize', rs));
    }
    async build(at) {
        const run = async () => {
            const keep = at ?? (this.root.isConnected ? this.safeLocation() : null);
            this.io?.disconnect();
            this.loaded.clear();
            this.ordered = null;
            this.chunks = [];
            this.mode = this.desiredMode();
            this.applyVars();
            const v = this.values;
            this.built = { layout: v.layout, cover: v.coverAlone, fs: v.fontSize, family: v.fontFamily, spacing: v.lineSpacing,
                w: v.columnWidth, zoom: this.zoomPercent(), size: `${this.el.clientWidth}x${this.el.clientHeight}` };
            replace(this.el);
            if (this.total === 0) {
                replace(this.el, h('div', { class: 'flow' }, h('p', { class: 'muted', text: 'This document has no readable text.' })));
                return;
            }
            if (this.mode === 'scroll')
                await this.buildScroll(keep);
            else
                await this.buildPaged(keep);
        };
        this.building = this.building.then(run, run);
        return this.building;
    }
    safeLocation() { try {
        return this.location();
    }
    catch {
        return null;
    } }
    zoomPercent() { const v = this.values; return v.zoomMode === 'custom' ? v.zoom : 100; }
    applyVars() {
        const v = this.values;
        const zf = this.zoomPercent() / 100;
        const rgb = (hex) => `${parseInt(hex.slice(1, 3), 16)} ${parseInt(hex.slice(3, 5), 16)} ${parseInt(hex.slice(5, 7), 16)}`;
        const colour = v.fontColor ?? this.ctx.state.effective.colors.text;
        for (const t of [this.el, this.root]) {
            t.style.setProperty('--zf', String(zf));
        }
        this.root.style.setProperty('--fs', `${(v.fontSize * zf).toFixed(2)}px`);
        this.root.style.setProperty('--lh', String(v.lineSpacing));
        this.root.style.setProperty('--measure', `${v.columnWidth}ch`);
        this.root.style.setProperty('--font', FONTS[v.fontFamily] ?? FONTS.serif);
        this.root.style.setProperty('--align', v.textAlign === 'justify' ? 'justify' : 'start');
        this.root.style.setProperty('--ink-rgb', rgb(colour));
        this.root.style.setProperty('--ink-a', String(v.fontOpacity / 100));
        this.root.setAttribute('dir', this.info.direction === 'rtl' ? 'rtl' : 'ltr');
        if (this.info.language)
            this.root.setAttribute('lang', this.info.language);
    }
    appearanceChanged() {
        if (this.destroyed)
            return;
        const v = this.values;
        const size = `${this.el.clientWidth}x${this.el.clientHeight}`;
        const structural = this.desiredMode() !== this.mode || (this.mode === 'paged' && (v.layout !== this.built.layout || v.coverAlone !== this.built.cover
            || v.fontSize !== this.built.fs || v.fontFamily !== this.built.family || v.lineSpacing !== this.built.spacing
            || v.columnWidth !== this.built.w || this.zoomPercent() !== this.built.zoom || size !== this.built.size));
        if (structural) {
            void this.build(null);
            return;
        }
        this.applyVars();
        this.built = { ...this.built, fs: v.fontSize, family: v.fontFamily, spacing: v.lineSpacing, w: v.columnWidth, zoom: this.zoomPercent() };
        if (this.mode === 'scroll')
            this.reestimate();
        else
            for (const page of this.root.querySelectorAll('.flow'))
                this.cloneVars(page);
        this.refreshBlocks();
    }
    // ------------------------------------------------------------------ scrolling layout
    async buildScroll(at) {
        this.root = h('div', { class: 'flow' });
        this.applyVars();
        const n = Math.ceil(this.total / CHUNK);
        for (let ci = 0; ci < n; ci++) {
            const count = Math.min(CHUNK, this.total - ci * CHUNK);
            const el = h('div', { class: 'chunk empty', data: { ci } });
            el.style.setProperty('--est', `${Math.round(count * this.avg)}px`);
            this.chunks.push({ ci, el, state: 'empty', count });
            this.root.appendChild(el);
        }
        this.el.appendChild(this.root);
        this.io = new IntersectionObserver((entries) => {
            for (const e of entries) {
                if (e.isIntersecting) {
                    const ci = Number(e.target.dataset.ci);
                    void this.loadChunk(ci);
                }
            }
        }, { root: this.el, rootMargin: '1400px 0px 1400px 0px' });
        for (const c of this.chunks)
            this.io.observe(c.el);
        if (at)
            await this.goToNow(at, false);
    }
    loadChunk(ci) {
        const c = this.chunks[ci];
        if (!c || this.destroyed)
            return Promise.resolve();
        if (c.state === 'loaded')
            return Promise.resolve();
        if (c.promise)
            return c.promise;
        c.state = 'loading';
        c.promise = (async () => {
            try {
                const res = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/blocks${qs({ start: ci * CHUNK, limit: CHUNK })}`);
                if (this.destroyed || !this.chunks.includes(c))
                    return;
                const frag = document.createDocumentFragment();
                for (const b of res.blocks)
                    frag.appendChild(this.draw(b));
                c.el.classList.remove('empty');
                c.el.style.removeProperty('min-height');
                replace(c.el, frag);
                c.state = 'loaded';
                this.ordered = null;
                this.learnHeight(c);
                this.trim(ci);
            }
            catch (err) {
                c.state = 'failed';
                const msg = err instanceof Error ? err.message : 'Could not load this part of the document.';
                replace(c.el, h('div', { class: 'banner bad' }, h('div', { class: 'grow', text: msg }), h('button', { class: 'btn small', text: 'Try again', attrs: { type: 'button' }, on: { click: () => { c.state = 'empty'; c.promise = undefined; void this.loadChunk(ci); } } })));
            }
            finally {
                c.promise = undefined;
            }
        })();
        return c.promise;
    }
    learnHeight(c) {
        const hgt = c.el.offsetHeight;
        if (hgt > 0 && c.count > 0)
            this.avg = this.avg * 0.7 + (hgt / c.count) * 0.3;
        this.reestimate();
    }
    reestimate() {
        for (const c of this.chunks)
            if (c.state !== 'loaded')
                c.el.style.setProperty('--est', `${Math.round(c.count * this.avg)}px`);
    }
    /** Give back the pieces that are far from the reader so a very long document does not pile up in memory. */
    trim(around) {
        const loadedChunks = this.chunks.filter((c) => c.state === 'loaded');
        if (loadedChunks.length <= KEEP_CHUNKS)
            return;
        const sel = window.getSelection();
        const center = this.centerChunk() ?? around;
        loadedChunks.sort((a, b) => Math.abs(b.ci - center) - Math.abs(a.ci - center));
        for (const c of loadedChunks.slice(0, loadedChunks.length - KEEP_CHUNKS)) {
            if (sel && !sel.isCollapsed && sel.containsNode(c.el, true))
                continue;
            const height = c.el.offsetHeight;
            for (const b of Array.from(c.el.querySelectorAll('.b')))
                this.loaded.delete(b.dataset.id ?? '');
            replace(c.el);
            c.el.classList.add('empty');
            c.el.style.setProperty('--est', `${height}px`);
            c.state = 'empty';
            this.ordered = null;
        }
    }
    centerChunk() {
        const el = this.firstVisibleBlock();
        const n = el ? Number(el.dataset.n) : NaN;
        return Number.isFinite(n) ? Math.floor(n / CHUNK) : null;
    }
    draw(b) {
        const extras = this.extrasFor(b.id);
        const el = renderBlock(b, { sid: this.ctx.sid, theme: this.theme, selected: this.selectedSet(), inkSelected: this.ctx.state.inkSelected, extras });
        this.loaded.set(b.id, { block: b, el, sig: this.signature(b.id, extras) });
        return el;
    }
    selectedSet() { const s = this.ctx.state.selectedId; return s ? new Set([s]) : new Set(); }
    // ------------------------------------------------------------------ paged layout
    budget() {
        const v = this.values;
        const px = v.fontSize * (this.zoomPercent() / 100);
        const spread = v.layout === 'spread';
        const pageW = Math.min(760, (this.el.clientWidth - 48 - (spread ? 24 : 0)) / (spread ? 2 : 1)) - 72;
        const pageH = this.el.clientHeight - 48 - 80;
        const perLine = Math.max(12, Math.min(v.columnWidth, Math.floor(pageW / (px * 0.5))));
        const lines = Math.max(4, Math.floor(pageH / (px * v.lineSpacing)));
        return clamp(Math.floor(perLine * lines * 0.62), 400, 60000);
    }
    async buildPaged(at) {
        const budget = this.budget();
        const res = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/paginate${qs({ chars: budget })}`);
        if (this.destroyed)
            return;
        this.paged.pages = res.pages;
        this.paged.cache.clear();
        this.paged.spreads = buildSpreads(res.pages.length, this.values.layout === 'spread' ? 'spread' : 'single', this.values.coverAlone);
        this.paged.key = `${budget}`;
        this.root = h('div', { class: 'pagedwrap' });
        this.el.appendChild(this.root);
        const n = at?.n ?? (at?.block ? await this.indexOf(at.block) : 0);
        this.paged.at = spreadIndexOf(this.paged.spreads, this.pageOfIndex(n ?? 0));
        await this.showSpread();
    }
    pageOfIndex(n) {
        const p = this.paged.pages;
        let lo = 0;
        let hi = p.length - 1;
        let ans = 0;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (p[mid].index <= n) {
                ans = mid;
                lo = mid + 1;
            }
            else
                hi = mid - 1;
        }
        return ans;
    }
    async pageBlocks(page) {
        const cached = this.paged.cache.get(page);
        if (cached)
            return cached;
        const p = this.paged.pages[page];
        const res = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/blocks${qs({ start: p.index, limit: Math.min(400, p.end - p.index) })}`);
        this.paged.cache.set(page, res.blocks);
        if (this.paged.cache.size > 12)
            this.paged.cache.delete(this.paged.cache.keys().next().value);
        return res.blocks;
    }
    async showSpread() {
        const spread = this.paged.spreads[this.paged.at] ?? [];
        const slots = spread.length === 2 ? [spread[0], spread[1]] : this.values.layout === 'spread' && this.values.coverAlone && spread[0] === 0 ? [null, 0] : this.values.layout === 'spread' ? [spread[0], null] : [spread[0]];
        const height = Math.max(240, this.el.clientHeight - 48);
        const parts = await Promise.all(slots.map(async (p) => (p === null ? null : { p, blocks: await this.pageBlocks(p) })));
        if (this.destroyed)
            return;
        this.loaded.clear();
        this.ordered = null;
        const pages = parts.map((part) => {
            const page = h('div', { class: 'flow' + (part ? '' : ' blank') });
            this.cloneVars(page);
            page.style.height = `${height}px`;
            page.style.overflow = 'auto';
            if (part) {
                page.setAttribute('aria-label', `Page ${part.p + 1}`);
                for (const b of part.blocks)
                    page.appendChild(this.draw(b));
            }
            return page;
        });
        replace(this.root, ...pages);
        this.el.scrollTop = 0;
        this.scheduleLocation();
    }
    cloneVars(page) {
        const src = this.varSource();
        for (const k of ['--fs', '--lh', '--measure', '--font', '--align', '--ink-rgb', '--ink-a'])
            page.style.setProperty(k, src[k] ?? '');
        page.setAttribute('dir', this.info.direction === 'rtl' ? 'rtl' : 'ltr');
        if (this.info.language)
            page.setAttribute('lang', this.info.language);
    }
    varSource() {
        const v = this.values;
        const zf = this.zoomPercent() / 100;
        const colour = v.fontColor ?? this.ctx.state.effective.colors.text;
        const rgb = `${parseInt(colour.slice(1, 3), 16)} ${parseInt(colour.slice(3, 5), 16)} ${parseInt(colour.slice(5, 7), 16)}`;
        return { '--fs': `${(v.fontSize * zf).toFixed(2)}px`, '--lh': String(v.lineSpacing), '--measure': `${v.columnWidth}ch`, '--font': FONTS[v.fontFamily] ?? FONTS.serif,
            '--align': v.textAlign === 'justify' ? 'justify' : 'start', '--ink-rgb': rgb, '--ink-a': String(v.fontOpacity / 100) };
    }
    async turnTo(index) {
        const next = clamp(index, 0, this.paged.spreads.length - 1);
        if (next === this.paged.at)
            return;
        this.paged.at = next;
        await this.showSpread();
    }
    // ------------------------------------------------------------------ DocView
    step(direction) {
        if (this.mode === 'paged') {
            void this.turnTo(turn(this.paged.spreads, this.paged.at, direction));
            return;
        }
        this.el.scrollBy({ top: direction * Math.max(80, this.el.clientHeight * 0.9) });
    }
    edge(which) {
        if (this.mode === 'paged') {
            void this.turnTo(which === 'start' ? 0 : this.paged.spreads.length - 1);
            return;
        }
        if (which === 'start') {
            this.el.scrollTop = 0;
            return;
        }
        void this.loadChunk(this.chunks.length - 1).then(() => { this.el.scrollTop = this.el.scrollHeight; });
    }
    async indexOf(block) {
        const hit = this.loaded.get(block);
        if (hit)
            return hit.block.n;
        const loc = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/location${qs({ block })}`);
        return loc.n ?? 0;
    }
    async goTo(loc, flash = true) {
        await this.building;
        return this.goToNow(loc, flash);
    }
    /** Does the jump without waiting for a build; build() itself uses this (waiting on itself would deadlock). */
    async goToNow(loc, flash) {
        if (loc.type !== 'flow') {
            const res = await this.ctx.api.get(`/api/doc/${this.ctx.sid}/location${qs({ percent: loc.percent })}`);
            return this.goToNow(res, flash);
        }
        let n = loc.n;
        if (n === undefined)
            n = loc.block ? await this.indexOf(loc.block) : Math.floor(this.total * clamp(loc.percent, 0, 1));
        n = clamp(n, 0, Math.max(0, this.total - 1));
        if (this.mode === 'paged') {
            this.paged.at = spreadIndexOf(this.paged.spreads, this.pageOfIndex(n));
            await this.showSpread();
        }
        else {
            const ci = Math.floor(n / CHUNK);
            await this.loadChunk(ci);
            // Pieces above the target may still be estimates; load the neighbours so the jump lands where it should.
            void this.loadChunk(ci - 1);
            void this.loadChunk(ci + 1);
        }
        const target = this.el.querySelector(`.b[data-n="${n}"]`);
        if (!target) {
            if (this.mode === 'scroll')
                this.chunks[Math.floor(n / CHUNK)]?.el.scrollIntoView({ block: 'start' });
            return;
        }
        const top = this.offsetInBlock(target, loc.offset ?? 0);
        const rect = this.el.getBoundingClientRect();
        if (this.mode === 'paged') {
            const page = target.closest('.flow');
            if (page)
                page.scrollTop = Math.max(0, top - page.getBoundingClientRect().top + page.scrollTop - 16);
        }
        else {
            this.el.scrollTop += top - rect.top - 12;
        }
        if (flash) {
            target.classList.add('flash');
            window.setTimeout(() => target.classList.remove('flash'), 1700);
        }
        this.scheduleLocation();
    }
    /** The screen position (top) of a character offset inside a block. */
    offsetInBlock(block, offset) {
        const txt = block.querySelector('.txt');
        if (!txt || offset <= 0)
            return block.getBoundingClientRect().top;
        const u16 = cpToU16(txt.textContent ?? '', offset);
        const w = document.createTreeWalker(txt, NodeFilter.SHOW_TEXT);
        let seen = 0;
        for (let node = w.nextNode(); node; node = w.nextNode()) {
            const len = node.length;
            if (seen + len >= u16) {
                const r = document.createRange();
                r.setStart(node, u16 - seen);
                r.collapse(true);
                const rects = r.getClientRects();
                const rect = rects.length ? rects[0] : r.getBoundingClientRect();
                if (rect.height)
                    return rect.top;
                break;
            }
            seen += len;
        }
        return block.getBoundingClientRect().top;
    }
    blockEls() {
        if (!this.ordered)
            this.ordered = Array.from(this.el.querySelectorAll('.b[data-id]'));
        return this.ordered;
    }
    firstVisibleBlock() {
        const els = this.blockEls();
        if (!els.length)
            return null;
        const top = this.el.getBoundingClientRect().top;
        let lo = 0;
        let hi = els.length - 1;
        let ans = hi;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (els[mid].getBoundingClientRect().bottom > top + 2) {
                ans = mid;
                hi = mid - 1;
            }
            else
                lo = mid + 1;
        }
        return els[ans];
    }
    location() {
        const el = this.firstVisibleBlock();
        if (!el)
            return { type: 'flow', block: null, offset: 0, percent: 0 };
        const id = el.dataset.id;
        const n = Number(el.dataset.n);
        const entry = this.loaded.get(id);
        let offset = 0;
        if (this.mode === 'scroll' && entry && TEXT_TYPES.has(entry.block.type))
            offset = this.caretOffset(el);
        const total = Math.max(1, this.info.totalChars ?? 1);
        const percent = entry ? Math.min(1, (entry.block.cum + offset) / total) : n / Math.max(1, this.total);
        return { type: 'flow', block: id, offset, percent, n };
    }
    caretOffset(block) {
        try {
            const txt = block.querySelector('.txt');
            if (!txt)
                return 0;
            const rect = this.el.getBoundingClientRect();
            const bt = block.getBoundingClientRect();
            if (bt.top >= rect.top)
                return 0; // the block starts in view
            const x = clamp(bt.left + 40, 0, window.innerWidth - 1);
            const pos = document.caretPositionFromPoint?.(x, rect.top + 6);
            let node = pos?.offsetNode ?? null;
            let off = pos?.offset ?? 0;
            if (!node) {
                const r = document.caretRangeFromPoint?.(x, rect.top + 6);
                node = r?.startContainer ?? null;
                off = r?.startOffset ?? 0;
            }
            if (!node || !txt.contains(node))
                return 0;
            const pre = document.createRange();
            pre.selectNodeContents(txt);
            pre.setEnd(node, off);
            return u16ToCp(txt.textContent ?? '', pre.toString().length);
        }
        catch {
            return 0;
        }
    }
    scheduleLocation() {
        window.clearTimeout(this.posTimer);
        this.posTimer = window.setTimeout(() => { if (!this.destroyed)
            this.ctx.location(this.location()); }, 250);
    }
    pageInfo() {
        if (this.mode === 'paged') {
            const sp = this.paged.spreads[this.paged.at] ?? [0];
            const label = sp.length === 2 ? `Pages ${sp[0] + 1}–${sp[1] + 1}` : `Page ${sp[0] + 1}`;
            return { current: sp[0] + 1, total: this.paged.pages.length, label: `${label} of ${this.paged.pages.length}`, approximate: true };
        }
        const starts = this.info.logicalPages ?? [];
        const loc = this.location();
        if (starts.length > 1) {
            let page = 0;
            for (let i = 0; i < starts.length; i++)
                if (starts[i] <= (loc.type === 'flow' ? loc.n ?? 0 : 0))
                    page = i;
            return { current: page + 1, total: starts.length, label: `Page ${page + 1} of ${starts.length}`, approximate: false };
        }
        return { current: 1, total: 1, label: `${Math.round(loc.percent * 100)}% read`, approximate: false };
    }
    // ------------------------------------------------------------------ annotations and hits
    buildIndex() {
        this.extras.clear();
        const get = (id) => { let e = this.extras.get(id); if (!e)
            this.extras.set(id, (e = { marks: [], hits: [], ink: [], stickies: [], bookmarks: [] })); return e; };
        for (const a of this.ctx.state.annotations) {
            if (a.visible === false)
                continue;
            if (a.kind === 'highlight' || a.kind === 'underline' || a.kind === 'strike' || a.kind === 'note') {
                for (const s of a.resolved.segments ?? []) {
                    if (!s.block || s.status === 'detached')
                        continue;
                    get(s.block).marks.push({ id: a.id, kind: a.kind, start: s.start, end: s.end, color: a.style.color, opacity: a.style.opacity, hasNote: !!a.body.text, moved: s.status === 'moved' });
                }
            }
            else if (a.status !== 'detached') {
                const block = a.anchor.block ?? a.resolved.block;
                if (!block)
                    continue;
                if (a.kind === 'ink')
                    get(block).ink.push(a);
                else if (a.kind === 'sticky')
                    get(block).stickies.push(a);
                else if (a.kind === 'bookmark')
                    get(block).bookmarks.push(a);
            }
        }
        for (const [id, hits] of this.hitMap)
            get(id).hits = hits;
    }
    extrasFor(id) { return this.extras.get(id) ?? { marks: [], hits: [], ink: [], stickies: [], bookmarks: [] }; }
    signature(id, e) {
        const sel = this.ctx.state.selectedId;
        return JSON.stringify([e.marks, e.hits, e.ink.map((a) => `${a.id}.${a.rev}`), e.stickies.map((a) => `${a.id}.${a.rev}`), e.bookmarks.map((a) => `${a.id}.${a.rev}`),
            sel && (e.marks.some((m) => m.id === sel) || e.stickies.some((a) => a.id === sel)) ? sel : '', [...this.ctx.state.inkSelected].filter((i) => e.ink.some((a) => a.id === i)), this.theme]);
    }
    annotationsChanged() { this.buildIndex(); this.refreshBlocks(); }
    /** Redraw only the blocks whose marks, drawings, hits or selection actually changed. */
    refreshBlocks() {
        this.ordered = null;
        for (const [id, entry] of this.loaded) {
            const extras = this.extrasFor(id);
            const sig = this.signature(id, extras);
            if (sig === entry.sig)
                continue;
            const next = renderBlock(entry.block, { sid: this.ctx.sid, theme: this.theme, selected: this.selectedSet(), inkSelected: this.ctx.state.inkSelected, extras });
            entry.el.replaceWith(next);
            this.loaded.set(id, { block: entry.block, el: next, sig });
        }
        this.ordered = null;
    }
    setInkSelection(ids) {
        this.ink.applySelection(ids);
        this.refreshBlocks();
    }
    setHits(hits, current) {
        const before = new Set(this.hitMap.keys());
        this.hitMap.clear();
        this.hitList = hits;
        this.currentHit = current;
        hits.forEach((hit, i) => {
            if (!hit.block || hit.start === null || hit.end === null || hit.inline === false)
                return;
            const list = this.hitMap.get(hit.block) ?? [];
            list.push({ start: hit.start, end: hit.end, current: i === current });
            this.hitMap.set(hit.block, list);
        });
        for (const id of before)
            if (!this.hitMap.has(id))
                this.extras.get(id) && (this.extras.get(id).hits = []);
        for (const [id, list] of this.hitMap) {
            const e = this.extrasFor(id);
            this.extras.set(id, { ...e, hits: list });
        }
        this.refreshBlocks();
        const hit = hits[current];
        if (hit?.block)
            void this.goTo({ type: 'flow', block: hit.block, offset: hit.start ?? 0, percent: hit.percent }, false).then(() => {
                const mark = this.el.querySelector('.searchhit.current');
                if (mark) {
                    const r = mark.getBoundingClientRect();
                    const c = this.el.getBoundingClientRect();
                    if (r.top < c.top + 40 || r.bottom > c.bottom - 40)
                        this.el.scrollTop += r.top - c.top - c.height / 3;
                }
            });
    }
    rectOf(id) {
        const el = this.el.querySelector(`[data-ann~="${CSS.escape(id)}"]`);
        if (el)
            return el.getBoundingClientRect();
        const a = this.ctx.state.annotations.find((x) => x.id === id);
        const block = a?.anchor.block ?? a?.resolved.block;
        const b = block ? this.loaded.get(block)?.el : null;
        return b ? b.getBoundingClientRect() : null;
    }
    // ------------------------------------------------------------------ selection
    computeSelection() {
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0 || sel.isCollapsed)
            return null;
        const range = sel.getRangeAt(0);
        if (!this.el.contains(range.commonAncestorContainer))
            return null;
        const segments = [];
        const parts = [];
        for (const blockEl of Array.from(this.el.querySelectorAll('.b[data-text="1"]'))) {
            if (!range.intersectsNode(blockEl))
                continue;
            const txt = blockEl.querySelector('.txt');
            if (!txt || !range.intersectsNode(txt))
                continue;
            const whole = document.createRange();
            whole.selectNodeContents(txt);
            const text = txt.textContent ?? '';
            let s = 0;
            let e = text.length;
            if (range.compareBoundaryPoints(Range.START_TO_START, whole) > 0) {
                const pre = document.createRange();
                pre.selectNodeContents(txt);
                pre.setEnd(range.startContainer, range.startOffset);
                s = pre.toString().length;
            }
            if (range.compareBoundaryPoints(Range.END_TO_END, whole) < 0) {
                const pre = document.createRange();
                pre.selectNodeContents(txt);
                pre.setEnd(range.endContainer, range.endOffset);
                e = pre.toString().length;
            }
            if (e <= s || !text.slice(s, e).trim())
                continue;
            segments.push({ block: blockEl.dataset.id, start: u16ToCp(text, s), end: u16ToCp(text, e) });
            parts.push(text.slice(s, e));
        }
        if (!segments.length)
            return null;
        const rects = range.getClientRects();
        const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
        return { segments, text: parts.join('\n'), rect, final: false };
    }
    emitSelection(final) {
        if (this.destroyed)
            return;
        const info = this.computeSelection();
        if (!info) {
            this.finalKey = '';
            if (this.lastSel) {
                this.lastSel = false;
                this.ctx.selection(null);
            }
            return;
        }
        const key = JSON.stringify(info.segments);
        if (final)
            this.finalKey = key;
        else if (key === this.finalKey)
            return; // the late debounced report must not close the tools
        this.lastSel = true;
        this.ctx.selection({ ...info, final });
    }
    selectedText() { return this.computeSelection()?.text ?? ''; }
    async liveSelection() { return this.computeSelection(); }
    clearSelection() { window.getSelection()?.removeAllRanges(); this.lastSel = false; this.finalKey = ''; }
    // ------------------------------------------------------------------ clicks
    hostAt(target) {
        const el = target?.closest?.('.b[data-id]');
        return el ? { kind: 'block', key: el.dataset.id, el } : null;
    }
    click(e) {
        const t = e.target;
        if (!t || this.ink.active)
            return;
        const xref = t.closest('a.xref');
        if (xref) {
            e.preventDefault();
            void this.indexOf(xref.dataset.target).then((n) => this.goTo({ type: 'flow', block: xref.dataset.target, n, offset: 0, percent: 0 }));
            return;
        }
        const ref = t.closest('.ref');
        if (ref) {
            this.ctx.openReference(ref.dataset.ref, ref);
            return;
        }
        const pin = t.closest('.stickypin, .bookmark-flag');
        if (pin?.dataset.ann) {
            this.ctx.openAnnotation(pin.dataset.ann, pin);
            return;
        }
        const sel = window.getSelection();
        if (this.ctx.state.tool === 'sticky') {
            const b = t.closest('.b[data-id]');
            if (b) {
                const r = b.getBoundingClientRect();
                const anchor = { kind: 'block', block: b.dataset.id, x: clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1), y: clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1) };
                this.ctx.placeSticky(anchor, new DOMRect(e.clientX, e.clientY, 1, 1));
            }
            return;
        }
        if (sel && !sel.isCollapsed)
            return;
        const mark = t.closest('.mark[data-ann]');
        if (mark?.dataset.ann) {
            const ids = mark.dataset.ann.split(' ');
            this.ctx.openAnnotation(ids[ids.length - 1], mark);
        }
    }
}
//# sourceMappingURL=flowview.js.map