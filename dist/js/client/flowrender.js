// Draw one block of a text document. The text of a block is always exactly block.text inside one `.txt` element (marks, formatting
// and search hits only wrap pieces of it), so a selection can be turned into offsets and an annotation back into a range.
import { markStyle } from '../core/appearance.js';
import { cpToU16, hasAstral } from '../core/text.js';
import { h } from './dom.js';
import { drawInk, inkLayer } from './ink.js';
import { safeHref } from './view.js';
export const TEXT_TYPES = new Set(['heading', 'paragraph', 'list_item', 'quote', 'code']);
const FLAGS = ['bold', 'italic', 'underline', 'strike', 'code', 'superscript', 'subscript'];
const FLAG_CLASS = { bold: 'f-b', italic: 'f-i', underline: 'f-u', strike: 'f-s', code: 'f-c', superscript: 'f-sup', subscript: 'f-sub' };
const toU16 = (text, items) => (hasAstral(text) ? items.map((m) => ({ ...m, start: cpToU16(text, m.start), end: cpToU16(text, m.end) })) : items);
function runElement(piece, spans, a, e) {
    const covering = spans.filter((s) => s.start <= a && s.end >= e);
    if (!covering.length)
        return document.createTextNode(piece);
    const classes = [];
    let link = null;
    let ref = null;
    let xref = null;
    for (const s of covering) {
        if (s.type === 'format') {
            for (const f of FLAGS)
                if (s[f] === true)
                    classes.push(FLAG_CLASS[f]);
        }
        else if (s.type === 'link')
            link = safeHref(s.href);
        else if (s.type === 'ref' && typeof s.ref === 'string')
            ref = s.ref;
        else if (s.type === 'xref' && typeof s.target === 'string')
            xref = s.target;
    }
    const cls = [...new Set(classes)].join(' ');
    if (link)
        return h('a', { class: cls, text: piece, attrs: { href: link, rel: 'noopener noreferrer nofollow', target: '_blank' } });
    if (ref)
        return h('span', { class: `ref ${cls}`.trim(), text: piece, attrs: { role: 'button', tabindex: 0, 'aria-label': 'Show reference' }, data: { ref } });
    if (xref)
        return h('a', { class: `xref ${cls}`.trim(), text: piece, attrs: { href: '#' }, data: { target: xref } });
    if (cls)
        return h('span', { class: cls, text: piece });
    return document.createTextNode(piece);
}
/** The inline content: text split at every formatting, mark and hit boundary. */
export function inlineContent(b, extras, theme, selected) {
    const text = b.text;
    const frag = document.createDocumentFragment();
    const spans = toU16(text, (b.spans ?? []).filter((s) => ['format', 'link', 'ref', 'xref'].includes(s.type)));
    const marks = toU16(text, extras.marks);
    const hits = toU16(text, extras.hits);
    if (!spans.length && !marks.length && !hits.length) {
        frag.appendChild(document.createTextNode(text));
        return frag;
    }
    const cuts = new Set([0, text.length]);
    for (const x of [...spans, ...marks, ...hits]) {
        cuts.add(Math.max(0, Math.min(text.length, x.start)));
        cuts.add(Math.max(0, Math.min(text.length, x.end)));
    }
    const order = [...cuts].sort((x, y) => x - y);
    for (let i = 0; i + 1 < order.length; i++) {
        const a = order[i];
        const e = order[i + 1];
        if (e <= a)
            continue;
        let node = runElement(text.slice(a, e), spans, a, e);
        const m = marks.filter((x) => x.start <= a && x.end >= e);
        const hit = hits.find((x) => x.start <= a && x.end >= e);
        if (hit)
            node = h('span', { class: 'searchhit' + (hit.current ? ' current' : '') }, node);
        if (m.length) {
            const el = h('span', { class: 'mark', data: { ann: m.map((x) => x.id).join(' ') } }, node);
            const hl = [...m].reverse().find((x) => x.kind === 'highlight');
            const lines = m.filter((x) => x.kind === 'underline' || x.kind === 'strike');
            if (hl) {
                const st = markStyle(hl.color, hl.opacity, theme);
                el.style.setProperty('--mark-bg', st.background);
                if (st.text)
                    el.style.setProperty('--mark-fg', st.text);
            }
            else
                el.style.setProperty('--mark-bg', 'transparent');
            if (lines.length)
                el.style.setProperty('--mark-line', lines[lines.length - 1].color);
            if (m.some((x) => x.kind === 'underline'))
                el.classList.add('u');
            if (m.some((x) => x.kind === 'strike'))
                el.classList.add('s');
            if (m.some((x) => x.hasNote))
                el.classList.add('hasnote');
            if (m.some((x) => x.moved))
                el.classList.add('moved');
            if (m.some((x) => selected.has(x.id)))
                el.classList.add('sel');
            node = el;
        }
        frag.appendChild(node);
    }
    return frag;
}
function table(b) {
    const t = h('table', { class: 'b', data: { id: b.id, n: b.n, type: 'table' } });
    if (b.table?.caption)
        t.appendChild(h('caption', { text: b.table.caption }));
    const head = b.table?.header_rows ?? 0;
    (b.table?.rows ?? []).forEach((row, r) => {
        const tr = h('tr');
        for (const cell of row) {
            const c = typeof cell === 'string' ? { text: cell } : cell;
            const th = r < head || (typeof cell !== 'string' && cell.header === true);
            const el = h(th ? 'th' : 'td', { text: c.text ?? '', attrs: { colspan: c.colspan && c.colspan > 1 ? c.colspan : undefined, rowspan: c.rowspan && c.rowspan > 1 ? c.rowspan : undefined, scope: th ? (r < head ? 'col' : 'row') : undefined } });
            tr.appendChild(el);
        }
        t.appendChild(tr);
    });
    return t;
}
export function renderBlock(b, o) {
    let el;
    const level = Math.min(6, Math.max(1, Number(b.attributes?.level ?? 1) || 1));
    const common = { data: { id: b.id, n: b.n, type: b.type, text: TEXT_TYPES.has(b.type) ? '1' : '0' } };
    switch (b.type) {
        case 'heading':
            el = h(`h${level}`, { class: 'b', ...common });
            break;
        case 'quote':
            el = h('blockquote', { class: 'b', ...common });
            break;
        case 'code':
            el = h('pre', { class: 'b', ...common, attrs: { lang: typeof b.attributes?.language === 'string' ? String(b.attributes.language) : undefined } });
            break;
        case 'list_item': {
            el = h('p', { class: 'b li', ...common });
            const at = (b.attributes ?? {});
            el.style.setProperty('--depth', String(Number(at.depth ?? 0) || 0));
            el.style.setProperty('--marker', at.ordered ? `"${b.number ?? 1}."` : '"•"');
            break;
        }
        case 'table':
            el = table(b);
            break;
        case 'figure': {
            el = h('figure', { class: 'b', ...common });
            if (b.figure) {
                const img = h('img', { attrs: { src: `/api/doc/${o.sid}/${b.figure.url}`, alt: b.figure.decorative ? '' : (b.figure.alt ?? ''), loading: 'lazy', decoding: 'async' } });
                el.appendChild(img);
                if (b.figure.caption)
                    el.appendChild(h('figcaption', { text: b.figure.caption }));
            }
            break;
        }
        case 'embed': {
            const emb = b.embed;
            el = h('div', { class: 'b embed', ...common, text: `${emb?.kind ?? 'Embedded content'}. ${emb?.note ?? 'Embedded content is shown as a declaration and is never run.'}` });
            break;
        }
        default: el = h('p', { class: 'b', ...common });
    }
    if (TEXT_TYPES.has(b.type)) {
        // Each paragraph takes its direction from its own first strong character, so an Arabic or Hebrew paragraph in a document whose
        // language says "ltr" (or the reverse) is laid out, punctuated and aligned correctly. Code and list markers stay as authored.
        if (b.type === 'paragraph' || b.type === 'heading' || b.type === 'quote')
            el.setAttribute('dir', 'auto');
        const txt = h('span', { class: 'txt' });
        txt.appendChild(inlineContent(b, o.extras, o.theme, o.selected));
        el.appendChild(txt);
    }
    if (b.attributes?.page_break_before)
        el.classList.add('pb');
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
//# sourceMappingURL=flowrender.js.map