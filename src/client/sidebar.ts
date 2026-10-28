// The side panel: contents, pages, your annotations, search, and the optional reading assistant.
import { qs } from '../core/api.js';
import { relativeTime } from '../core/format.js';
import type { AgentNote, Annotation, AssistResult, Location, OutlineItem, SearchHit, SearchResult } from '../core/types.js';
import { button, clear, h, nextId, replace } from './dom.js';
import { materialButton } from './material.js';
import type { ReaderSession } from './reader.js';
import type { AppState, SideTab } from './state.js';
import { toast } from './ui.js';

export const TAB_LABELS: Record<Exclude<SideTab, 'none'>, string> = { outline: 'Contents', thumbnails: 'Pages', annotations: 'Notes', search: 'Search', assistant: 'Assistant' };

const KIND_FILTERS: Array<{ id: string; label: string; test: (a: Annotation) => boolean }> = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'marks', label: 'Highlights', test: (a) => ['highlight', 'underline', 'strike'].includes(a.kind) },
  { id: 'notes', label: 'Notes', test: (a) => a.kind === 'note' || a.kind === 'sticky' || !!a.body.text },
  { id: 'bookmarks', label: 'Bookmarks', test: (a) => a.kind === 'bookmark' },
  { id: 'drawings', label: 'Drawings', test: (a) => a.kind === 'ink' || a.kind === 'shape' },
];
const KIND_NAME: Record<string, string> = { highlight: 'Highlight', underline: 'Underline', strike: 'Strikethrough', note: 'Note', sticky: 'Sticky note', ink: 'Drawing', shape: 'Shape', bookmark: 'Bookmark' };

export class Sidebar {
  readonly el: HTMLElement;
  private tabs: HTMLElement;
  private panel: HTMLElement;
  private session: ReaderSession | null = null;
  private outline: OutlineItem[] | null = null;
  private filter = { kind: 'all', text: '', tag: '', sort: 'position' };
  private search = { query: '', caseSensitive: false, result: null as SearchResult | null, current: -1, notes: [] as Annotation[], busy: false, timer: 0, error: '' };
  private assist = { status: null as Record<string, unknown> | null, result: null as AssistResult | null, busy: false, error: '', question: '', scope: 'selection', checked: null as AssistResult | null, passages: null as AssistResult | null };

  constructor(private readonly state: AppState, private readonly onClose: () => void) {
    this.tabs = h('div', { class: 'tabs', attrs: { role: 'tablist', 'aria-label': 'Side panel' } });
    this.panel = h('div', { class: 'panel', attrs: { role: 'tabpanel', tabindex: 0 } });
    this.el = h('aside', { class: 'side', attrs: { 'aria-label': 'Side panel' } }, this.tabs, this.panel);
    state.on('side', () => this.render());
    state.on('annotations', () => { if (this.state.side === 'annotations') this.renderNotes(); });
    state.on('status', () => { if (this.state.side === 'outline') this.markCurrentOutline(); if (this.state.side === 'thumbnails') this.markCurrentPage(); });
  }

  attach(session: ReaderSession | null): void {
    this.session = session;
    this.outline = null;
    this.search = { ...this.search, result: null, current: -1, notes: [], error: '' };
    this.assist = { ...this.assist, result: null, status: null, checked: null, passages: null, error: '' };
    this.render();
  }

  render(): void {
    const side = this.state.side;
    this.el.hidden = !this.session;                      // closing only collapses the panel (CSS slides it), so its content stays for the animation
    if (this.el.hidden || side === 'none') return;
    replace(this.tabs);
    (Object.keys(TAB_LABELS) as Array<Exclude<SideTab, 'none'>>).forEach((id) => {
      const on = side === id;
      const b = h('button', { class: 'btn small' + (on ? ' active' : ''), text: TAB_LABELS[id], attrs: { role: 'tab', type: 'button', 'aria-selected': String(on), id: `tab-${id}` }, on: { click: () => this.state.setSide(id) } });
      this.tabs.appendChild(b);
    });
    this.tabs.appendChild(h('span', { class: 'spacer' }));
    const close = materialButton('close', 'Close side panel', () => this.onClose(), { cls: 'side-close flat', keys: 'Ctrl+B' });
    this.tabs.appendChild(close);
    this.panel.setAttribute('aria-labelledby', `tab-${side}`);
    switch (side) {
      case 'outline': void this.renderOutline(); break;
      case 'thumbnails': void this.renderPages(); break;
      case 'annotations': this.renderNotes(); break;
      case 'search': this.renderSearch(); break;
      case 'assistant': void this.renderAssistant(); break;
    }
  }

  // ------------------------------------------------------------------ contents
  private async renderOutline(): Promise<void> {
    const s = this.session; if (!s) return;
    replace(this.panel, h('p', { class: 'muted', text: 'Loading contents…' }));
    try {
      this.outline ??= await s.api.get<OutlineItem[]>(`/api/doc/${s.sid}/outline`);
    } catch (e) { replace(this.panel, h('p', { class: 'error', text: e instanceof Error ? e.message : 'Could not load the contents.' })); return; }
    if (this.state.side !== 'outline') return;
    if (!this.outline.length) {
      replace(this.panel, h('p', { class: 'muted', text: s.fixed ? 'This PDF has no table of contents. Use the Pages tab or Go to page.' : 'This document has no headings to list.' }));
      return;
    }
    const list = h('ul', { class: 'list outline' });
    this.outline.forEach((item, i) => {
      const b = h('button', { class: 'item', text: item.title, data: { i }, attrs: { type: 'button' }, on: { click: () => { void s.view.goTo(item.location); } } });
      b.style.setProperty('--lvl', String(Math.max(0, item.level - 1)));
      list.appendChild(h('li', {}, b));
    });
    replace(this.panel, list);
    this.markCurrentOutline();
  }

  private markCurrentOutline(): void {
    const s = this.session; if (!s || !this.outline) return;
    const loc = s.lastLoc ?? s.view.location();
    let cur = -1;
    this.outline.forEach((o, i) => { if (locBefore(o.location, loc)) cur = i; });
    this.panel.querySelectorAll('li').forEach((li, i) => { li.classList.toggle('current', i === cur); li.querySelector('button')?.setAttribute('aria-current', i === cur ? 'location' : 'false'); });
  }

  // ------------------------------------------------------------------ pages
  private async renderPages(): Promise<void> {
    const s = this.session; if (!s) return;
    if (s.fixed) {
      const n = s.payload.info.pages ?? 0;
      const v = this.state.effective!.values;
      const grid = h('div', { class: 'thumbs' });
      for (let i = 0; i < n; i++) {
        const [w, hgt] = s.payload.info.pageSizes?.[i] ?? [612, 792];
        const url = `/api/doc/${s.sid}/page/${i}/image${qs({ scale: 0.22, fmt: 'jpeg', theme: this.state.effective!.theme, pa: v.pdfAppearance, fc: v.fontColor ? v.fontColor.slice(1) : undefined, fo: v.fontOpacity < 100 ? v.fontOpacity : undefined })}`;
        const img = h('img', { attrs: { src: url, loading: 'lazy', alt: '', width: Math.round(w * 0.22), height: Math.round(hgt * 0.22), decoding: 'async' } });
        grid.appendChild(h('button', { attrs: { type: 'button', 'aria-label': `Go to page ${i + 1}` }, data: { p: i }, on: { click: () => { void s.view.goTo({ type: 'page', page: i, y: 0, percent: i / n }); } } }, img, h('div', { class: 'small', text: String(i + 1) })));
      }
      replace(this.panel, grid);
      this.markCurrentPage();
      return;
    }
    replace(this.panel, h('p', { class: 'muted', text: 'Loading…' }));
    try {
      const res = await s.api.get<{ pages: Array<{ n: number; block: string; snippet: string; percent: number }>; note?: string }>(`/api/doc/${s.sid}/pages`);
      if (this.state.side !== 'thumbnails') return;
      if (res.pages.length <= 1) { replace(this.panel, h('p', { class: 'muted', text: 'This document has no page breaks. It flows continuously; use Contents, search or the progress bar to move around.' })); return; }
      const grid = h('div', { class: 'thumbs' });
      for (const p of res.pages.slice(0, 3000)) grid.appendChild(h('button', { attrs: { type: 'button', 'aria-label': `Go to page ${p.n + 1}` }, data: { p: p.n }, on: { click: () => { void s.view.goTo({ type: 'flow', block: p.block, offset: 0, percent: p.percent }); } } }, h('div', { class: 'small', text: `Page ${p.n + 1}` }), h('div', { class: 'snip', text: p.snippet })));
      replace(this.panel, h('p', { class: 'hint', text: res.note ?? '' }), grid);
    } catch (e) { replace(this.panel, h('p', { class: 'error', text: e instanceof Error ? e.message : 'Could not load pages.' })); }
  }

  private markCurrentPage(): void {
    const s = this.session; if (!s) return;
    const info = s.view.pageInfo();
    this.panel.querySelectorAll<HTMLElement>('.thumbs button').forEach((b) => b.setAttribute('aria-current', String(Number(b.dataset.p) + 1 === info.current)));
  }

  // ------------------------------------------------------------------ annotations
  private renderNotes(): void {
    const s = this.session; if (!s) return;
    const all = this.state.annotations;
    const chips = h('div', { class: 'filters', attrs: { role: 'group', 'aria-label': 'Show' } });
    for (const f of KIND_FILTERS) {
      const n = all.filter(f.test).length;
      chips.appendChild(h('button', { class: 'btn small' + (this.filter.kind === f.id ? ' active' : ''), text: `${f.label} ${n}`, attrs: { type: 'button', 'aria-pressed': String(this.filter.kind === f.id) }, on: { click: () => { this.filter.kind = f.id; this.renderNotes(); } } }));
    }
    const q = h('input', { attrs: { type: 'search', placeholder: 'Filter text, notes, tags', 'aria-label': 'Filter annotations' }, value: this.filter.text });
    q.addEventListener('input', () => { this.filter.text = q.value; this.list(listBox); });
    const tags = [...new Set(all.flatMap((a) => a.body.tags ?? []))].sort((a, b) => a.localeCompare(b));
    const tagSel = h('select', { attrs: { 'aria-label': 'Filter by tag' } }, h('option', { text: 'All tags', value: '' }), ...tags.map((t) => h('option', { text: t, value: t })));
    tagSel.value = this.filter.tag;
    tagSel.addEventListener('change', () => { this.filter.tag = tagSel.value; this.list(listBox); });
    const sort = h('select', { attrs: { 'aria-label': 'Order' } }, h('option', { text: 'In reading order', value: 'position' }), h('option', { text: 'Newest first', value: 'new' }));
    sort.value = this.filter.sort;
    sort.addEventListener('change', () => { this.filter.sort = sort.value; this.list(listBox); });
    const listBox = h('div');
    const banner = this.state.changedSince ? h('div', { class: 'banner warn' }, h('div', { class: 'grow', text: `${this.state.changedSince} annotation${this.state.changedSince === 1 ? ' was' : 's were'} made on an earlier version of this document. They were re-checked against the current text; marks that could not be placed are listed as “text not found”.` })) : null;
    replace(this.panel, banner, chips, h('div', { class: 'row', style: { marginBottom: '8px' } }, q, tagSel, sort), listBox);
    this.list(listBox);
  }

  private list(box: HTMLElement): void {
    const s = this.session; if (!s) return;
    const test = KIND_FILTERS.find((f) => f.id === this.filter.kind)!.test;
    const text = this.filter.text.trim().toLocaleLowerCase();
    let items = this.state.annotations.filter((a) => test(a)
      && (!this.filter.tag || (a.body.tags ?? []).includes(this.filter.tag))
      && (!text || [a.text ?? '', a.body.text ?? '', a.body.title ?? '', ...(a.body.tags ?? [])].join('\n').toLocaleLowerCase().includes(text)));
    // strokes drawn together are one piece of handwriting
    const groups = new Map<string, Annotation[]>();
    const rows: Array<{ lead: Annotation; members: Annotation[] }> = [];
    for (const a of items) {
      if (a.kind === 'ink' && a.group) {
        const g = groups.get(a.group);
        if (g) { g.push(a); continue; }
        groups.set(a.group, [a]);
        rows.push({ lead: a, members: groups.get(a.group)! });
      } else rows.push({ lead: a, members: [a] });
    }
    items = rows.map((r) => r.lead);
    rows.sort((x, y) => this.filter.sort === 'new' ? y.lead.created.localeCompare(x.lead.created) : cmpPos(x.lead.pos, y.lead.pos) || x.lead.created.localeCompare(y.lead.created));
    if (!rows.length) { replace(box, h('p', { class: 'muted', text: this.state.annotations.length ? 'Nothing matches that filter.' : 'No annotations yet. Select text to highlight it or add a note, use a pen, or press Ctrl+D to bookmark this place.' })); return; }
    const ul = h('ul', { class: 'list' });
    for (const r of rows) ul.appendChild(h('li', {}, this.card(s, r.lead, r.members)));
    replace(box, ul);
  }

  private where(s: ReaderSession, a: Annotation): string {
    if (s.fixed) { const p = a.resolved.segments?.[0]?.page ?? a.anchor.page ?? a.resolved.page; return p === undefined ? '' : `Page ${p + 1}`; }
    const n = a.pos?.[0] ?? 0; const total = Math.max(1, s.payload.info.blockCount ?? 1);
    return `${Math.round((n / total) * 100)}% through`;
  }

  private card(s: ReaderSession, a: Annotation, members: Annotation[]): HTMLElement {
    const card = h('div', { class: 'ann' + (a.status === 'detached' ? ' detached' : '') + (this.state.selectedId === a.id ? ' sel' : ''), data: { id: a.id } });
    card.style.setProperty('--c', a.style.color);
    const title = a.kind === 'ink' && members.length > 1 ? `Drawing (${members.length} strokes)` : KIND_NAME[a.kind] ?? a.kind;
    card.appendChild(h('div', { class: 'meta' }, h('strong', { text: title }), h('span', { text: this.where(s, a) }), h('span', { text: relativeTime(a.modified) }),
      a.visible === false ? h('span', { class: 'chip', text: 'hidden' }) : null,
      a.status === 'moved' ? h('span', { class: 'chip', text: 'moved' }) : null,
      a.status === 'detached' ? h('span', { class: 'chip warn', text: 'text not found' }) : null));
    const quote = a.text ?? a.body.title ?? '';
    if (quote) card.appendChild(h('div', { class: 'q', text: quote }));
    if (a.body.text) card.appendChild(h('div', { class: 'nt', text: a.body.text }));
    if (a.body.tags?.length) card.appendChild(h('div', { class: 'meta' }, ...a.body.tags.map((t) => h('span', { class: 'chip', text: t }))));
    const tools = h('div', { class: 'tools' });
    if (a.status !== 'detached') tools.appendChild(button('Go to', () => { void s.jumpTo(a); }, { cls: 'small' }));
    tools.appendChild(button('Edit', () => { s.ui.editAnnotation(a.id, card.getBoundingClientRect()); }, { cls: 'small' }));
    tools.appendChild(button(a.visible === false ? 'Show' : 'Hide', () => { for (const m of members) void s.patch(m.id, { visible: m.visible === false }, 'Show or hide'); }, { cls: 'small', title: 'Hide or show this on the page without deleting it' }));
    if (a.status === 'moved') tools.appendChild(button('Accept position', () => { void this.accept(s, members.map((m) => m.id)); }, { cls: 'small', title: 'The text changed a little. Keep the mark where it is now.' }));
    if (a.status === 'detached' && a.anchor.kind !== 'block' && a.anchor.kind !== 'page') tools.appendChild(button('Re-attach to selection', () => { void this.reattach(s, a); }, { cls: 'small', title: 'Select the text it should belong to, then press this' }));
    tools.appendChild(button('Delete', () => { void s.remove(members.map((m) => m.id)); }, { cls: 'small danger' }));
    card.appendChild(tools);
    return card;
  }

  private async accept(s: ReaderSession, ids: string[]): Promise<void> {
    try { await s.api.post(`/api/doc/${s.sid}/annotations/accept`, { ids }); await s.refresh(); toast('Kept at the new position.'); } catch (e) { toast(e instanceof Error ? e.message : 'Could not update.'); }
  }

  private async reattach(s: ReaderSession, a: Annotation): Promise<void> {
    if (!s.sel) { toast('First select the text this should belong to in the document.'); return; }
    try { await s.api.post(`/api/doc/${s.sid}/annotations/${a.id}/reanchor`, { anchor: s.textAnchor(s.sel.segments) }); await s.refresh(); toast('Re-attached.'); }
    catch (e) { toast(e instanceof Error ? e.message : 'Could not re-attach.'); }
  }

  // ------------------------------------------------------------------ search
  focusSearch(): void { this.state.setSide('search'); window.setTimeout(() => this.panel.querySelector<HTMLInputElement>('input[type="search"]')?.select(), 20); }

  private renderSearch(): void {
    const s = this.session; if (!s) return;
    const st = this.search;
    if (this.state.pendingSearch) { st.query = this.state.pendingSearch; this.state.pendingSearch = ''; window.setTimeout(() => { void this.runSearch(); }, 0); }
    const input = h('input', { attrs: { type: 'search', 'aria-label': 'Search in this document', placeholder: 'Search this document' }, value: st.query });
    input.addEventListener('input', () => { st.query = input.value; window.clearTimeout(st.timer); st.timer = window.setTimeout(() => { void this.runSearch(); }, 300); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); this.step(e.shiftKey ? -1 : 1); } });
    const cs = h('input', { attrs: { type: 'checkbox' }, checked: st.caseSensitive });
    const csId = nextId('cs'); cs.id = csId;
    cs.addEventListener('change', () => { st.caseSensitive = cs.checked; void this.runSearch(); });
    const status = h('div', { class: 'hint', id: 'search-status', attrs: { role: 'status' } });
    const list = h('ul', { class: 'list', id: 'search-hits' });
    replace(this.panel, h('div', { class: 'row' }, input, button('', () => this.step(-1), { icon: '↑', title: 'Previous match', cls: 'small' }), button('', () => this.step(1), { icon: '↓', title: 'Next match', cls: 'small' })),
      h('div', { class: 'row', style: { margin: '6px 0' } }, cs, h('label', { text: 'Match case', attrs: { for: csId } })), status, list, h('div', { id: 'search-notes' }));
    this.paintSearch();
  }

  private async runSearch(): Promise<void> {
    const s = this.session; const st = this.search; if (!s) return;
    const q = st.query.trim();
    if (!q) { st.result = null; st.current = -1; st.notes = []; s.view.setHits([], -1); this.paintSearch(); return; }
    st.busy = true; st.error = '';
    this.paintSearch();
    try {
      const [res, notes] = await Promise.all([s.search(q, st.caseSensitive), s.api.get<Annotation[]>(`/api/doc/${s.sid}/search-notes${qs({ q })}`)]);
      if (q !== st.query.trim()) return;
      st.result = res; st.notes = notes; st.current = res.hits.length ? 0 : -1;
      s.view.setHits(res.hits, st.current);
    } catch (e) { st.error = e instanceof Error ? e.message : 'The search failed.'; }
    st.busy = false;
    this.paintSearch();
  }

  private paintSearch(): void {
    const s = this.session; const st = this.search; if (!s) return;
    const status = this.panel.querySelector<HTMLElement>('#search-status');
    const list = this.panel.querySelector<HTMLElement>('#search-hits');
    const notes = this.panel.querySelector<HTMLElement>('#search-notes');
    if (!status || !list || !notes) return;
    clear(list); clear(notes);
    if (st.busy) { status.textContent = 'Searching…'; return; }
    if (st.error) { status.textContent = st.error; status.className = 'error'; return; }
    status.className = 'hint';
    const r = st.result;
    if (!r) { status.textContent = ''; return; }
    if (r.ocrNeeded) { status.textContent = 'This PDF is a scan: it has no text to search. Reader does not run OCR.'; return; }
    const parts = [r.total === 0 ? 'No matches.' : `${st.current + 1} of ${r.total}${r.truncated ? '+' : ''} match${r.total === 1 ? '' : 'es'}`];
    if (r.index && !r.index.complete) parts.push(`Only ${r.index.indexed} of ${r.index.total} pages have been read for searching so far. Search again in a moment for the rest.`);
    status.textContent = parts.join(' ');
    r.hits.forEach((hit, i) => list.appendChild(h('li', { class: i === st.current ? 'current' : '' }, this.hitButton(hit, i))));
    if (st.notes.length) {
      notes.appendChild(h('h4', { text: 'In your notes', style: { marginTop: '14px' } }));
      const ul = h('ul', { class: 'list' });
      for (const a of st.notes) ul.appendChild(h('li', {}, this.card(s, a, [a])));
      notes.appendChild(ul);
    }
  }

  private hitButton(hit: SearchHit, i: number): HTMLElement {
    const where = hit.page !== undefined ? `Page ${hit.page + 1}` : `${Math.round(hit.percent * 100)}%`;
    return h('button', { class: 'item hit-item', attrs: { type: 'button' }, on: { click: () => { this.search.current = i; this.session?.view.setHits(this.search.result!.hits, i); this.paintSearch(); } } },
      h('span', { class: 'muted small', text: where + '  ' }), h('span', { text: hit.before + ' ' }), h('b', { text: hit.match }), h('span', { text: ' ' + hit.after }));
  }

  step(direction: 1 | -1): void {
    const st = this.search; const r = st.result;
    if (!r || !r.hits.length || !this.session) return;
    st.current = (st.current + direction + r.hits.length) % r.hits.length;
    this.session.view.setHits(r.hits, st.current);
    this.paintSearch();
    this.panel.querySelector('li.current')?.scrollIntoView({ block: 'nearest' });
  }

  clearSearch(): void { this.search.result = null; this.search.current = -1; this.session?.view.setHits([], -1); }

  // ------------------------------------------------------------------ assistant
  private async renderAssistant(): Promise<void> {
    const s = this.session; if (!s) return;
    const prefs = this.state.prefs;
    if (!prefs.assistantEnabled) {
      replace(this.panel, h('p', { text: 'The reading assistant is off. Everything else in Reader works without it, and no text leaves this program.' }),
        button('Turn the assistant on', () => { void s.api.put('/api/prefs', { assistantEnabled: true }).then(() => { prefs.assistantEnabled = true; this.render(); }); }, { cls: 'primary' }));
      return;
    }
    replace(this.panel, h('p', { class: 'muted', text: 'Checking which SLAI agents are available…' }));
    try { this.assist.status = await s.api.get<Record<string, unknown>>('/api/assist/status'); } catch (e) { this.assist.error = e instanceof Error ? e.message : 'Could not reach the assistant.'; }
    if (this.state.side !== 'assistant') return;
    const st = this.assist.status as { mode?: string; gateway?: boolean; reason?: string | null; roles?: Record<string, { state: string; class: string | null; error: string | null }> } | null;
    const usable = !!st?.gateway;
    const allowed = s.payload.assist || this.allowed;
    const box = h('div');
    box.appendChild(h('div', { class: 'banner ' + (usable ? 'info' : 'warn') },
      h('div', { class: 'grow' }, h('strong', { text: usable ? 'SLAI agents are connected.' : 'SLAI agents are not available.' }),
        h('div', { class: 'small', text: usable ? 'Summaries and passage ranking can use them, only if you allow it for this document.' : (st?.reason ?? this.assist.error ?? 'No agent could be reached.') + ' Reader still gives you extractive summaries and passage search that run here, without any agent. They are labelled as such.' }))));
    if (usable && st?.roles) {
      const rows = Object.entries(st.roles).map(([role, v]) => h('li', { text: `${role}: ${v.state}${v.class ? ' (' + v.class + ')' : ''}${v.error ? ' – ' + v.error : ''}` }));
      box.appendChild(h('ul', { class: 'small muted' }, ...rows));
    }
    const consent = h('input', { attrs: { type: 'checkbox' }, checked: allowed, disabled: !usable });
    const cid = nextId('consent'); consent.id = cid;
    consent.addEventListener('change', () => { void s.api.post(`/api/doc/${s.sid}/assist/consent`, { allow: consent.checked }).then(() => { this.allowed = consent.checked; toast(consent.checked ? 'SLAI agents may read this document (this session only).' : 'SLAI agents can no longer read this document.'); }).catch((e: unknown) => { consent.checked = !consent.checked; toast(e instanceof Error ? e.message : 'Could not change that.'); }); });
    box.appendChild(h('div', { class: 'row', style: { margin: '8px 0' } }, consent, h('label', { text: 'Let SLAI agents read this document', attrs: { for: cid } })));
    box.appendChild(h('p', { class: 'hint', text: 'Text goes to an agent only when this box is ticked and the Privacy agent allows it. Nothing is stored by Reader beyond this session.' }));

    const scope = h('select', { attrs: { 'aria-label': 'What to summarise' } },
      h('option', { value: 'selection', text: 'Selected text' }), s.fixed ? h('option', { value: 'page', text: 'Current page' }) : h('option', { value: 'section', text: 'Current section' }), h('option', { value: 'document', text: 'Whole document (first 60,000 characters)' }));
    scope.value = this.assist.scope;
    scope.addEventListener('change', () => { this.assist.scope = scope.value; });
    const run = button('Summarise', () => { void this.summarise(s, scope.value); }, { cls: 'primary', disabled: this.assist.busy });
    box.appendChild(h('div', { class: 'row' }, scope, run));
    const q = h('input', { attrs: { type: 'text', placeholder: 'Ask where something is discussed', 'aria-label': 'Question for passage search' }, value: this.assist.question });
    q.addEventListener('input', () => { this.assist.question = q.value; });
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); void this.findPassages(s, q.value); } });
    box.appendChild(h('div', { class: 'row', style: { marginTop: '10px' } }, q, button('Find passages', () => { void this.findPassages(s, q.value); })));
    box.appendChild(h('div', { class: 'row', style: { marginTop: '10px' } }, button('Check text extraction', () => { void this.checkDoc(s); }, { title: 'Counts words and empty pages. Only counts are sent to the Quality agent, never the text.' })));
    const out = h('div', { id: 'assist-out', style: { marginTop: '12px' }, attrs: { 'aria-live': 'polite' } });
    box.appendChild(out);
    replace(this.panel, box);
    this.paintAssist(out);
  }

  private allowed = false;

  private async summarise(s: ReaderSession, scope: string): Promise<void> {
    const body: Record<string, unknown> = { scope };
    if (scope === 'selection') { if (!s.sel) { toast('Select some text first.'); return; } body.segments = s.sel.segments; }
    if (scope === 'page') body.page = s.view.pageInfo().current - 1;
    if (scope === 'section') { const l = s.view.location(); if (l.type === 'flow') body.block = l.block; }
    await this.callAssist(s, 'summary', body, 'result');
  }
  private async findPassages(s: ReaderSession, question: string): Promise<void> {
    if (!question.trim()) { toast('Type a question or a few key words first.'); return; }
    await this.callAssist(s, 'passages', { query: question }, 'passages');
  }
  private async checkDoc(s: ReaderSession): Promise<void> { await this.callAssist(s, 'check', {}, 'checked'); }

  private async callAssist(s: ReaderSession, what: string, body: Record<string, unknown>, slot: 'result' | 'passages' | 'checked'): Promise<void> {
    this.assist.busy = true; this.assist.error = '';
    const out = this.panel.querySelector<HTMLElement>('#assist-out');
    if (out) replace(out, h('p', { class: 'muted', text: 'Working…' }));
    try { this.assist[slot] = await s.api.post<AssistResult>(`/api/doc/${s.sid}/assist/${what}`, body); } catch (e) { this.assist.error = e instanceof Error ? e.message : 'The request failed.'; }
    this.assist.busy = false;
    const o2 = this.panel.querySelector<HTMLElement>('#assist-out');
    if (o2) this.paintAssist(o2);
  }

  private paintAssist(out: HTMLElement): void {
    const s = this.session; if (!s) return;
    clear(out);
    if (this.assist.error) out.appendChild(h('p', { class: 'error', text: this.assist.error }));
    const jump = (loc: Record<string, number | string>): void => {
      if ('page' in loc) void s.view.goTo({ type: 'page', page: Number(loc.page), y: 0, percent: 0 });
      else void s.view.goTo({ type: 'flow', block: String(loc.block), offset: Number(loc.start ?? 0), percent: 0 });
    };
    const r = this.assist.result;
    if (r) {
      const box = h('div', { class: 'assist-result' }, h('div', { class: 'label', text: r.label }));
      if (r.sentences?.length) for (const x of r.sentences) box.appendChild(h('div', { class: 'passage' }, h('div', { text: x.text }), button(labelOf(x.loc), () => jump(x.loc), { cls: 'small flat' })));
      else if (r.text) box.appendChild(h('p', { text: r.text }));
      if (r.why) box.appendChild(h('div', { class: 'hint', text: `Why no agent was used: ${r.why}` }));
      if (r.sources?.length) { box.appendChild(h('div', { class: 'small muted', text: 'Passages this was made from:' })); for (const x of r.sources.slice(0, 6)) box.appendChild(h('div', { class: 'small' }, button(x.label, () => jump(x.loc), { cls: 'small flat' }), h('span', { class: 'muted', text: ' ' + x.text.slice(0, 100) }))); }
      this.notes(box, r.notes);
      out.appendChild(box);
    }
    const p = this.assist.passages;
    if (p) {
      const box = h('div', { class: 'assist-result' }, h('div', { class: 'label', text: p.label }));
      for (const x of p.passages ?? []) box.appendChild(h('div', { class: 'passage' }, h('div', { text: x.text }), button(x.label, () => jump(x.loc), { cls: 'small flat' })));
      if (p.why) box.appendChild(h('div', { class: 'hint', text: `Why no agent was used: ${p.why}` }));
      this.notes(box, p.notes);
      out.appendChild(box);
    }
    const c = this.assist.checked;
    if (c) {
      const l = (c.local ?? {}) as Record<string, unknown>;
      const box = h('div', { class: 'assist-result' }, h('div', { class: 'label', text: c.label }),
        h('p', { text: `${l.units} ${l.unit}${l.units === 1 ? '' : 's'}, ${l.words} words, ${l.emptyUnits} without text${l.ocrNeeded ? ' (this looks like a scan)' : ''}.` }));
      if (c.why) box.appendChild(h('div', { class: 'hint', text: `Quality agent: ${c.why}` }));
      this.notes(box, c.notes);
      out.appendChild(box);
    }
  }

  private notes(box: HTMLElement, notes: AgentNote[]): void {
    for (const n of notes) box.appendChild(h('div', { class: 'agentnote', text: `${n.role} agent: ${n.status}${n.agent ? ' (' + n.agent + ')' : ''}${n.reason ? ' – ' + n.reason : ''}` }));
  }
}

function labelOf(loc: Record<string, number | string>): string {
  if ('page' in loc) return `Page ${Number(loc.page) + 1}`;
  return 'Go to passage';
}

function cmpPos(a?: [number, number], b?: [number, number]): number {
  const x = a ?? [0, 0]; const y = b ?? [0, 0];
  return x[0] - y[0] || x[1] - y[1];
}

function locBefore(a: Location, b: Location): boolean {
  if (a.type === 'page' && b.type === 'page') return a.page < b.page || (a.page === b.page && a.y <= b.y + 0.02);
  if (a.type === 'flow' && b.type === 'flow') return (a.n ?? 0) <= (b.n ?? 0);
  return a.percent <= b.percent;
}

