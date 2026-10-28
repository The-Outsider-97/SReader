// Draws a parsed LaTeX tree (core/latex.ts) with ordinary elements, so a formula looks in Reader the way it does in SciCal. Only text
// nodes and class names are created: nothing in a document ever becomes markup. Kept in step with SciCal's client/math-dom.ts.
import { isTall, parseLatex, plain } from '../core/latex.js';
import { h, svg } from './dom.js';
const REL = new Set([':=', '=', '≤', '≥', '≠', '≈', '≡', '∼', '→', '⟶', '↔', '⇌', '⇒', '⇔', '⟹', '∈', '∉', '⊂', '⊆', '<', '>', '∝', ':']);
const BIN = new Set(['+', '−', '×', '·', '±', '∓', '÷', '∗', '∪', '∩', '∧', '∨']);
const OPENERS = new Set(['(', '[', '{', '|', ',', ';', '⟨']);
function isOperatorNode(n) {
    return !!n && n.t === 'ord' && n.v === 'sym' && (REL.has(n.s) || BIN.has(n.s) || OPENERS.has(n.s));
}
function drawRow(items, tall) {
    const out = [];
    let prev;
    for (const it of items) {
        if (it.t === 'ord' && it.v === 'sym') {
            if (REL.has(it.s)) {
                out.push(h('span', { class: 'mrel', text: it.s }));
                prev = it;
                continue;
            }
            if (BIN.has(it.s)) {
                const binary = prev !== undefined && prev.t !== 'space' && !isOperatorNode(prev);
                out.push(h('span', { class: binary ? 'mbin' : 'mun', text: it.s }));
                prev = it;
                continue;
            }
            if (it.s === ',' || it.s === ';') {
                out.push(h('span', { class: 'mcomma', text: it.s }));
                prev = it;
                continue;
            }
        }
        out.push(draw(it, tall));
        prev = it;
    }
    return out;
}
const DELIM_CLASS = { '(': 'dl-paren-l', ')': 'dl-paren-r', '[': 'dl-brack-l', ']': 'dl-brack-r', '|': 'dl-bar', '‖': 'dl-dbar', '{': 'dl-brace-l', '}': 'dl-brace-r', '⟨': 'dl-ang-l', '⟩': 'dl-ang-r' };
function delim(ch, tall) {
    if (!ch)
        return null;
    const cls = DELIM_CLASS[ch];
    if (tall && cls)
        return h('span', { class: `mdelim tall ${cls}`, attrs: { 'aria-hidden': 'true' } });
    return h('span', { class: 'mdelim', text: ch, attrs: { 'aria-hidden': 'true' } });
}
/** Scripts on a tall base (a matrix) hang from its top or bottom corner instead of floating at mid height. */
function scriptedClass(base) { return base.t === 'matrix' ? 'mscripted tallbase' : 'mscripted'; }
export function draw(n, tallCtx = false) {
    switch (n.t) {
        case 'row': return h('span', { class: 'mrow' }, ...drawRow(n.c, tallCtx));
        case 'ord': {
            const cls = { it: 'mi', rm: 'mr', num: 'mn', sym: 'ms', op: 'mo', bf: 'mb', err: 'merr' }[n.v];
            return h('span', { class: cls, text: n.s });
        }
        case 'frac': return h('span', { class: 'mfrac' }, h('span', { class: 'mnum' }, draw(n.n)), h('span', { class: 'mden' }, draw(n.d)));
        case 'sqrt': {
            const sign = svg('svg', { class: 'mrad', attrs: { viewBox: '0 0 10 20', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' } }, svg('path', { attrs: { d: 'M0.4 11.2 L2.6 9.4 L5.2 18.6 L9.6 0' } }));
            const radical = h('span', { class: 'msqrt' }, sign, h('span', { class: 'mradb' }, draw(n.b)));
            return n.i ? h('span', { class: 'mroot' }, h('span', { class: 'mindex' }, draw(n.i)), radical) : radical;
        }
        case 'sup': return h('span', { class: scriptedClass(n.b) }, draw(n.b, tallCtx), h('span', { class: 'msup' }, draw(n.e)));
        case 'sub': return h('span', { class: scriptedClass(n.b) }, draw(n.b, tallCtx), h('span', { class: 'msub' }, draw(n.e)));
        case 'subsup': return h('span', { class: scriptedClass(n.b) }, draw(n.b, tallCtx), h('span', { class: 'msubsup' }, h('span', { class: 'msup2' }, draw(n.hi)), h('span', { class: 'msub2' }, draw(n.lo))));
        case 'fence': {
            const tall = isTall(n.b);
            return h('span', { class: 'mfence' + (tall ? ' tall' : '') }, delim(n.l, tall), draw(n.b, tall), delim(n.r, tall));
        }
        case 'matrix': {
            const cols = Math.max(1, ...n.rows.map((r) => r.length));
            const grid = h('span', { class: 'mgrid' + (n.cases ? ' cases' : '') });
            grid.style.setProperty('grid-template-columns', `repeat(${cols}, auto)`);
            for (const row of n.rows)
                for (let c = 0; c < cols; c++)
                    grid.appendChild(h('span', { class: 'mcell' }, row[c] ? draw(row[c]) : null));
            return h('span', { class: 'mmat' }, delim(n.l, true), grid, delim(n.r, true));
        }
        case 'space': {
            const sp = h('span', { class: 'msp', attrs: { 'aria-hidden': 'true' } });
            sp.style.setProperty('width', `${n.w}em`);
            return sp;
        }
        case 'over': return h('span', { class: `mover ov-${n.mark}` }, draw(n.b));
        case 'bigop': {
            const limitsAbove = n.s === '∑' || n.s === '∏' || n.s === '⋃' || n.s === '⋂';
            if (limitsAbove)
                return h('span', { class: 'mbigop above' }, n.hi ? h('span', { class: 'mlim' }, draw(n.hi)) : null, h('span', { class: 'mbig', text: n.s }), n.lo ? h('span', { class: 'mlim' }, draw(n.lo)) : null);
            return h('span', { class: 'mbigop beside' }, h('span', { class: 'mbig int', text: n.s }), (n.lo || n.hi) ? h('span', { class: 'msubsup int' }, h('span', { class: 'msup2' }, n.hi ? draw(n.hi) : null), h('span', { class: 'msub2' }, n.lo ? draw(n.lo) : null)) : null);
        }
    }
}
/** A formula as a single element: readable by screen readers through its plain-text label, copyable as LaTeX. */
export function math(latex, opts = {}) {
    let tree;
    try {
        tree = parseLatex(latex);
    }
    catch {
        tree = { t: 'ord', s: latex, v: 'rm' };
    }
    return h('span', { class: 'm' + (opts.display ? ' mdisplay' : ''), attrs: { role: 'math', 'aria-label': opts.label ?? plain(tree) }, data: { latex } }, draw(tree));
}
//# sourceMappingURL=mathdom.js.map