// Draw one block of a text document. The text of a block is always exactly block.text inside one `.txt` element (marks, formatting
// and search hits only wrap pieces of it), so a selection can be turned into offsets and an annotation back into a range.
import { markStyle } from '../core/appearance.js';
import { cpToU16, hasAstral } from '../core/text.js';
import type { Annotation, FlowBlock, GraphView } from '../core/types.js';
import { h, replace, svg } from './dom.js';
import { drawInk, inkLayer } from './ink.js';
import { math } from './mathdom.js';
import { safeHref } from './view.js';

export interface MarkSpec { id: string; kind: 'highlight' | 'underline' | 'strike' | 'note'; start: number; end: number; color: string; opacity: number; hasNote: boolean; moved: boolean }
export interface HitSpec { start: number; end: number; current: boolean }
export interface BlockExtras { marks: MarkSpec[]; hits: HitSpec[]; ink: Annotation[]; stickies: Annotation[]; bookmarks: Annotation[] }
export interface RenderOpts { sid: string; theme: 'light' | 'dark' | 'sepia' | 'contrast'; selected: ReadonlySet<string>; inkSelected: ReadonlySet<string>; extras: BlockExtras }

export const TEXT_TYPES = new Set(['heading', 'paragraph', 'list_item', 'quote', 'code']);
const FLAGS = ['bold', 'italic', 'underline', 'strike', 'code', 'superscript', 'subscript'] as const;
const FLAG_CLASS: Record<string, string> = { bold: 'f-b', italic: 'f-i', underline: 'f-u', strike: 'f-s', code: 'f-c', superscript: 'f-sup', subscript: 'f-sub' };

interface Cut { start: number; end: number }
const toU16 = <T extends Cut>(text: string, items: T[]): T[] => (hasAstral(text) ? items.map((m) => ({ ...m, start: cpToU16(text, m.start), end: cpToU16(text, m.end) })) : items);

function runElement(piece: string, spans: FlowBlock['spans'] & object, a: number, e: number): Node {
  const covering = spans.filter((s) => s.start <= a && s.end >= e);
  if (!covering.length) return document.createTextNode(piece);
  const classes: string[] = [];
  let link: string | null = null;
  let ref: string | null = null;
  let xref: string | null = null;
  for (const s of covering) {
    if (s.type === 'format') {
      for (const f of FLAGS) if (s[f] === true) classes.push(FLAG_CLASS[f]);
    } else if (s.type === 'link') link = safeHref(s.href);
    else if (s.type === 'ref' && typeof s.ref === 'string') ref = s.ref;
    else if (s.type === 'xref' && typeof s.target === 'string') xref = s.target;
  }
  const cls = [...new Set(classes)].join(' ');
  if (link) return h('a', { class: cls, text: piece, attrs: { href: link, rel: 'noopener noreferrer nofollow', target: '_blank' } });
  if (ref) return h('span', { class: `ref ${cls}`.trim(), text: piece, attrs: { role: 'button', tabindex: 0, 'aria-label': 'Show reference' }, data: { ref } });
  if (xref) return h('a', { class: `xref ${cls}`.trim(), text: piece, attrs: { href: '#' }, data: { target: xref } });
  if (cls) return h('span', { class: cls, text: piece });
  return document.createTextNode(piece);
}

/** The inline content: text split at every formatting, mark and hit boundary. */
export function inlineContent(b: FlowBlock, extras: BlockExtras, theme: RenderOpts['theme'], selected: ReadonlySet<string>): DocumentFragment {
  const text = b.text;
  const frag = document.createDocumentFragment();
  const spans = toU16(text, (b.spans ?? []).filter((s) => ['format', 'link', 'ref', 'xref'].includes(s.type)));
  const marks = toU16(text, extras.marks);
  const hits = toU16(text, extras.hits);
  if (!spans.length && !marks.length && !hits.length) { frag.appendChild(document.createTextNode(text)); return frag; }
  const cuts = new Set<number>([0, text.length]);
  for (const x of [...spans, ...marks, ...hits]) { cuts.add(Math.max(0, Math.min(text.length, x.start))); cuts.add(Math.max(0, Math.min(text.length, x.end))); }
  const order = [...cuts].sort((x, y) => x - y);
  for (let i = 0; i + 1 < order.length; i++) {
    const a = order[i];
    const e = order[i + 1];
    if (e <= a) continue;
    let node: Node = runElement(text.slice(a, e), spans, a, e);
    const m = marks.filter((x) => x.start <= a && x.end >= e);
    const hit = hits.find((x) => x.start <= a && x.end >= e);
    if (hit) node = h('span', { class: 'searchhit' + (hit.current ? ' current' : '') }, node);
    if (m.length) {
      const el = h('span', { class: 'mark', data: { ann: m.map((x) => x.id).join(' ') } }, node);
      const hl = [...m].reverse().find((x) => x.kind === 'highlight');
      const lines = m.filter((x) => x.kind === 'underline' || x.kind === 'strike');
      if (hl) { const st = markStyle(hl.color, hl.opacity, theme); el.style.setProperty('--mark-bg', st.background); if (st.text) el.style.setProperty('--mark-fg', st.text); }
      else el.style.setProperty('--mark-bg', 'transparent');
      if (lines.length) el.style.setProperty('--mark-line', lines[lines.length - 1].color);
      if (m.some((x) => x.kind === 'underline')) el.classList.add('u');
      if (m.some((x) => x.kind === 'strike')) el.classList.add('s');
      if (m.some((x) => x.hasNote)) el.classList.add('hasnote');
      if (m.some((x) => x.moved)) el.classList.add('moved');
      if (m.some((x) => selected.has(x.id))) el.classList.add('sel');
      node = el;
    }
    frag.appendChild(node);
  }
  return frag;
}

function table(b: FlowBlock): HTMLTableElement {
  const t = h('table', { class: 'b', data: { id: b.id, n: b.n, type: 'table' } });
  if (b.table?.caption) t.appendChild(h('caption', { text: b.table.caption }));
  const head = b.table?.header_rows ?? 0;
  (b.table?.rows ?? []).forEach((row, r) => {
    const tr = h('tr');
    for (const cell of row) {
      const c = typeof cell === 'string' ? { text: cell } : cell;
      const th = r < head || (typeof cell !== 'string' && (cell as { header?: boolean }).header === true);
      const el = h(th ? 'th' : 'td', { text: c.text ?? '', attrs: { colspan: c.colspan && c.colspan > 1 ? c.colspan : undefined, rowspan: c.rowspan && c.rowspan > 1 ? c.rowspan : undefined, scope: th ? (r < head ? 'col' : 'row') : undefined } });
      tr.appendChild(el);
    }
    t.appendChild(tr);
  });
  return t;
}

// ---------------------------------------------------------------------------------------------------- figures and graphs
// A figure is its picture. A graph written by SciCal also lists the functions it plots, one per line directly beneath the picture, each
// with a small sample of the line drawn the way the picture draws it (colour *and* dash pattern, so the list still reads in grayscale).
const KEY_DASH: Record<string, string> = { solid: 'none', dash: '7 4', dot: '1.5 4', dashdot: '8 3 1.5 3' };

function lineKey(color: string | null, dash: string): SVGSVGElement {
  const key = svg('svg', { class: 'gf-key', attrs: { viewBox: '0 0 34 10', width: 34, height: 10, 'aria-hidden': 'true', focusable: 'false' } });
  const line = svg('line', { attrs: { x1: 2, y1: 5, x2: 32, y2: 5, 'stroke-width': 2.6, 'stroke-linecap': 'round', 'stroke-dasharray': KEY_DASH[dash] ?? 'none' } });
  line.style.setProperty('stroke', color ?? 'currentColor');
  key.appendChild(line);
  return key;
}

/** The formulas of a graph, one per line (never side by side). Returns null when the record says why there are none. */
export function graphFormulas(g: GraphView): HTMLElement | null {
  const series = g.series ?? [];
  if (!series.length) return null;
  const list = h('ol', { class: 'gf-formulas', attrs: { 'aria-label': 'Functions plotted in this graph' } });
  for (const s of series) {
    const formula = s.latex
      ? math(s.latex, { label: s.expression ?? undefined })
      : h('code', { class: 'eqsrc', text: s.expression ?? '', attrs: { role: 'math' } });
    list.appendChild(h('li', { class: 'gf-item', data: { series: s.id } }, lineKey(s.color, s.dash), h('span', { class: 'eq gf-eq' }, formula)));
  }
  if (g.skipped) list.appendChild(h('li', { class: 'gf-note', text: `${g.skipped} more ${g.skipped === 1 ? 'function was' : 'functions were'} not listed because the record was incomplete.` }));
  return list;
}

function missingFigure(reason: string, alt: string | undefined): HTMLElement {
  return h('div', { class: 'gf-missing', attrs: { role: 'note' } }, h('strong', { text: 'Figure not shown. ' }), h('span', { text: reason + (alt ? ` It was described as: ${alt}` : '') }));
}

function figure(b: FlowBlock, o: RenderOpts, common: { data: Record<string, string | number> }): HTMLElement {
  const g = b.graph;
  const el = h('figure', { class: g ? 'b graph-figure' : 'b', ...common });
  const f = b.figure;
  if (!f) return el;
  const alt = f.decorative ? '' : (f.alt ?? '');
  const attrs: Record<string, string | number> = { src: `/api/doc/${o.sid}/${f.url}`, alt, loading: 'lazy', decoding: 'async' };
  const dims = g?.image;
  if (dims && dims.width && dims.height) { attrs.width = dims.width; attrs.height = dims.height; }     // reserve the room: no jump when the picture arrives
  const img = h('img', { attrs });
  const frame = g ? h('div', { class: 'gf-plot' }, img) : el;
  img.addEventListener('error', () => {
    // The picture is missing or cannot be decoded: say so where it should be and keep the rest of the figure (and document) intact.
    const note = missingFigure('The picture could not be loaded; it may be missing or damaged.', alt || undefined);
    if (g) replace(frame, note); else img.replaceWith(note);
  });
  if (g) el.appendChild(frame); else el.appendChild(img);
  if (g) {
    if (g.error) el.appendChild(h('p', { class: 'gf-note', text: g.error }));
    else { const list = graphFormulas(g); if (list) el.appendChild(list); }
  }
  if (f.caption) el.appendChild(h('figcaption', { text: f.caption }));
  return el;
}

export function renderBlock(b: FlowBlock, o: RenderOpts): HTMLElement {
  let el: HTMLElement;
  const level = Math.min(6, Math.max(1, Number((b.attributes as Record<string, unknown> | undefined)?.level ?? 1) || 1));
  const common = { data: { id: b.id, n: b.n, type: b.type, text: TEXT_TYPES.has(b.type) ? '1' : '0' } };
  switch (b.type) {
    case 'heading': el = h(`h${level}` as 'h1', { class: 'b', ...common }); break;
    case 'quote': el = h('blockquote', { class: 'b', ...common }); break;
    case 'code': el = h('pre', { class: 'b', ...common, attrs: { lang: typeof (b.attributes as Record<string, unknown> | undefined)?.language === 'string' ? String((b.attributes as Record<string, unknown>).language) : undefined } }); break;
    case 'list_item': {
      el = h('p', { class: 'b li', ...common });
      const at = (b.attributes ?? {}) as Record<string, unknown>;
      el.style.setProperty('--depth', String(Number(at.depth ?? 0) || 0));
      el.style.setProperty('--marker', at.ordered ? `"${b.number ?? 1}."` : '"•"');
      break;
    }
    case 'table': el = table(b); break;
    case 'figure': el = figure(b, o, common); break;
    case 'equation': {
      // A display equation is typeset from its LaTeX source (the same drawing SciCal uses). Other syntaxes are shown as their source,
      // labelled, rather than dropped. An equation has no text of its own, so there is nothing in it to select, highlight or anchor to.
      el = h('div', { class: 'b eq', ...common });
      const eq = b.equation;
      if (eq) {
        el.style.setProperty('--eq-just', eq.align === 'left' ? 'flex-start' : eq.align === 'right' ? 'flex-end' : 'center');
        el.appendChild(eq.syntax === 'latex' ? math(eq.source, { display: true })
          : h('code', { class: 'eqsrc', text: eq.source, title: `Shown as ${eq.syntax} source; Reader typesets LaTeX only`, attrs: { role: 'math', 'aria-label': `${eq.syntax} equation` } }));
      }
      break;
    }
    case 'embed': {
      const emb = (b as unknown as { embed?: { kind?: string; note?: string } }).embed;
      el = h('div', { class: 'b embed', ...common, text: `${emb?.kind ?? 'Embedded content'}. ${emb?.note ?? 'Embedded content is shown as a declaration and is never run.'}` });
      break;
    }
    // A paragraph that carries a graph record is SciCal's "the picture of this graph is missing" notice: it keeps its text and lists the
    // functions below it. (A <div>, because a list is not allowed inside a <p>.)
    default: el = b.type === 'paragraph' && b.graph ? h('div', { class: 'b graph-missing', ...common, attrs: { role: 'note' } }) : h('p', { class: 'b', ...common });
  }
  if (TEXT_TYPES.has(b.type)) {
    // Each paragraph takes its direction from its own first strong character, so an Arabic or Hebrew paragraph in a document whose
    // language says "ltr" (or the reverse) is laid out, punctuated and aligned correctly. Code and list markers stay as authored.
    if (b.type === 'paragraph' || b.type === 'heading' || b.type === 'quote') el.setAttribute('dir', 'auto');
    const txt = h('span', { class: 'txt' });
    txt.appendChild(inlineContent(b, o.extras, o.theme, o.selected));
    el.appendChild(txt);
    if (b.type === 'paragraph' && b.graph) { const list = graphFormulas(b.graph); if (list) el.appendChild(list); }
  }
  if ((b.attributes as Record<string, unknown> | undefined)?.page_break_before) el.classList.add('pb');
  // Pins, ribbons and drawings sit over the block, never inside its text.
  for (const s of o.extras.stickies) {
    const pin = h('button', { class: 'stickypin' + (o.selected.has(s.id) ? ' open' : ''), text: '✎', title: s.body.text ? s.body.text.slice(0, 80) : 'Sticky note', attrs: { type: 'button', 'aria-label': `Sticky note${s.body.text ? ': ' + s.body.text.slice(0, 60) : ''}` }, data: { ann: s.id } });
    pin.style.left = `${Math.round((s.anchor.x ?? 0.95) * 1000) / 10}%`;
    pin.style.top = `${Math.round((s.anchor.y ?? 0.1) * 1000) / 10}%`;
    pin.style.background = s.style.color;
    el.appendChild(pin);
  }
  if (o.extras.bookmarks.length) {
    const bm = o.extras.bookmarks[0];
    el.appendChild(h('button', { class: 'bookmark-flag', text: '⚑', title: `Bookmark: ${bm.body.title ?? ''}`, attrs: { type: 'button', 'aria-label': `Bookmark: ${bm.body.title ?? ''}` }, data: { ann: bm.id } }));
  }
  if (o.extras.ink.length) {
    const layer = inkLayer(el);
    drawInk(layer, o.extras.ink, o.inkSelected);
  }
  return el;
}

