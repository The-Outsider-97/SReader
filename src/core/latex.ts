// A small LaTeX-subset parser. The server prints results as LaTeX; this turns that text into a plain data tree that
// client/math-dom.ts draws with ordinary DOM elements. No HTML is ever produced from the text.

export type MNode =
  | { t: 'row'; c: MNode[] }
  | { t: 'ord'; s: string; v: 'it' | 'rm' | 'num' | 'sym' | 'op' | 'bf' | 'err' }
  | { t: 'frac'; n: MNode; d: MNode }
  | { t: 'sqrt'; b: MNode; i?: MNode }
  | { t: 'sup'; b: MNode; e: MNode }
  | { t: 'sub'; b: MNode; e: MNode }
  | { t: 'subsup'; b: MNode; lo: MNode; hi: MNode }
  | { t: 'fence'; l: string; r: string; b: MNode }
  | { t: 'matrix'; rows: MNode[][]; l: string; r: string; cases?: boolean }
  | { t: 'space'; w: number }
  | { t: 'over'; b: MNode; mark: 'bar' | 'hat' | 'vec' | 'dot' | 'tilde' | 'under' }
  | { t: 'bigop'; s: string; lo?: MNode; hi?: MNode };

const SYMBOLS: Record<string, string> = {
  cdot: '·', times: '×', div: '÷', pm: '±', mp: '∓', leq: '≤', le: '≤', geq: '≥', ge: '≥', neq: '≠', ne: '≠', approx: '≈', equiv: '≡', sim: '∼', propto: '∝',
  to: '→', rightarrow: '→', longrightarrow: '⟶', leftarrow: '←', leftrightarrow: '↔', rightleftharpoons: '⇌', Rightarrow: '⇒', Leftrightarrow: '⇔', implies: '⟹', uparrow: '↑', downarrow: '↓',
  infty: '∞', partial: '∂', nabla: '∇', circ: '°', degree: '°', angle: '∠', in: '∈', notin: '∉', subset: '⊂', subseteq: '⊆', cup: '∪', cap: '∩', emptyset: '∅', varnothing: '∅',
  forall: '∀', exists: '∃', land: '∧', lor: '∨', neg: '¬', cdots: '⋯', ldots: '…', dots: '…', vdots: '⋮', ddots: '⋱', hbar: 'ℏ', ell: 'ℓ', prime: '′', star: '⋆', bullet: '•',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ',
  pi: 'π', rho: 'ρ', sigma: 'σ', tau: 'τ', upsilon: 'υ', phi: 'φ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π', Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
  lvert: '|', rvert: '|', vert: '|', lVert: '‖', rVert: '‖', Vert: '‖', langle: '⟨', rangle: '⟩', lfloor: '⌊', rfloor: '⌋', lceil: '⌈', rceil: '⌉',
  lbrace: '{', rbrace: '}', backslash: '\\', mid: '|',
};
const OPERATORS = new Set(['sin', 'cos', 'tan', 'cot', 'sec', 'csc', 'sinh', 'cosh', 'tanh', 'coth', 'arcsin', 'arccos', 'arctan', 'ln', 'log', 'exp', 'det', 'min', 'max', 'gcd', 'lim', 'arg', 'dim', 'ker', 'sup', 'inf', 'Pr', 'mod', 'lcm', 'rank', 'tr']);
const BIGOPS: Record<string, string> = { int: '∫', iint: '∬', oint: '∮', sum: '∑', prod: '∏', bigcup: '⋃', bigcap: '⋂' };
const SPACES: Record<string, number> = { ',': 0.17, ':': 0.22, ';': 0.28, '!': -0.1, ' ': 0.28, quad: 1, qquad: 2, thinspace: 0.17, enspace: 0.5 };
const BLACKBOARD: Record<string, string> = { R: 'ℝ', N: 'ℕ', Z: 'ℤ', Q: 'ℚ', C: 'ℂ', P: 'ℙ' };
const IGNORED = new Set(['displaystyle', 'textstyle', 'scriptstyle', 'limits', 'nolimits', 'bigl', 'bigr', 'Bigl', 'Bigr', 'biggl', 'biggr', 'big', 'Big', 'bigg', 'Bigg', 'relax', 'nonumber']);
const MATRIX_ENV: Record<string, [string, string]> = { bmatrix: ['[', ']'], pmatrix: ['(', ')'], vmatrix: ['|', '|'], Vmatrix: ['‖', '‖'], matrix: ['', ''], Bmatrix: ['{', '}'], cases: ['{', ''], array: ['', ''], aligned: ['', ''] };
const MAX_DEPTH = 60;

export class LatexParser {
  private i = 0;
  private depth = 0;
  constructor(private readonly src: string) {}

  parseAll(): MNode {
    const row = this.row(() => false);
    // anything left over (an unmatched brace) is shown as it is instead of being dropped
    const rest: MNode[] = [];
    while (this.i < this.src.length) {
      rest.push({ t: 'ord', s: this.src[this.i++], v: 'err' });
      const more = this.row(() => false);
      rest.push(more);
    }
    return rest.length ? { t: 'row', c: [row, ...rest] } : row;
  }

  private peek(): string { return this.src[this.i] ?? ''; }
  private eof(): boolean { return this.i >= this.src.length; }

  private command(): string {
    // at a backslash
    this.i++;
    const m = /^[A-Za-z]+/.exec(this.src.slice(this.i, this.i + 40));
    if (m) { this.i += m[0].length; return m[0]; }
    const ch = this.src[this.i] ?? '';
    this.i += ch ? 1 : 0;
    return ch;
  }

  /** Read items until `stop` says so (without consuming the stopper). */
  private row(stop: () => boolean): MNode {
    const items: MNode[] = [];
    if (++this.depth > MAX_DEPTH) { this.depth--; this.i = this.src.length; return { t: 'ord', s: '…', v: 'err' }; }
    while (!this.eof() && !stop()) {
      const c = this.peek();
      if (c === '}') break;
      const atom = this.atom();
      if (atom) items.push(this.scripts(atom));
    }
    this.depth--;
    return items.length === 1 ? items[0] : { t: 'row', c: items };
  }

  private group(): MNode {
    // at '{'
    this.i++;
    const r = this.row(() => false);
    if (this.peek() === '}') this.i++;
    return r;
  }

  private arg(): MNode {
    this.skipSpace();
    if (this.peek() === '{') return this.group();
    if (this.peek() === '\\') return this.atom() ?? { t: 'row', c: [] };
    const ch = this.src[this.i++] ?? '';
    return ch ? { t: 'ord', s: ch, v: /\d/.test(ch) ? 'num' : /[A-Za-z]/.test(ch) ? 'it' : 'sym' } : { t: 'row', c: [] };
  }

  private skipSpace(): void { while (/\s/.test(this.peek())) this.i++; }

  private scripts(base: MNode): MNode {
    let lo: MNode | undefined;
    let hi: MNode | undefined;
    for (;;) {
      this.skipSpace();
      const c = this.peek();
      if (c === '^' && hi === undefined) { this.i++; hi = this.arg(); }
      else if (c === '_' && lo === undefined) { this.i++; lo = this.arg(); }
      else break;
    }
    if (lo && hi) return base.t === 'bigop' ? { ...base, lo, hi } : { t: 'subsup', b: base, lo, hi };
    if (hi) return base.t === 'bigop' ? { ...base, hi } : { t: 'sup', b: base, e: hi };
    if (lo) return base.t === 'bigop' ? { ...base, lo } : { t: 'sub', b: base, e: lo };
    return base;
  }

  private rawGroup(): string {
    this.skipSpace();
    if (this.peek() !== '{') return this.src[this.i++] ?? '';
    let depth = 0;
    let out = '';
    while (!this.eof()) {
      const c = this.src[this.i++];
      if (c === '{') { depth++; if (depth === 1) continue; }
      else if (c === '}') { depth--; if (depth === 0) break; }
      out += c;
    }
    return out;
  }

  private textNode(raw: string, v: 'rm' | 'bf'): MNode {
    const items: MNode[] = [];
    let buf = '';
    const flush = (): void => { if (buf) { items.push({ t: 'ord', s: buf, v }); buf = ''; } };
    for (let k = 0; k < raw.length; k++) {
      const c = raw[k];
      if (c === '\\' && k + 1 < raw.length) {
        const nx = raw[k + 1];
        if (/[A-Za-z]/.test(nx)) {
          const m = /^[A-Za-z]+/.exec(raw.slice(k + 1)) as RegExpExecArray;
          const sym = SYMBOLS[m[0]];
          if (m[0] === 'quad' || m[0] === 'qquad') { flush(); items.push({ t: 'space', w: SPACES[m[0]] }); }
          else buf += sym ?? m[0];
          k += m[0].length;
        } else {
          if (nx in SPACES) { flush(); items.push({ t: 'space', w: SPACES[nx] }); }
          else buf += nx;
          k++;
        }
      } else buf += c;
    }
    flush();
    return items.length === 1 ? items[0] : { t: 'row', c: items };
  }

  private delimiter(): string {
    this.skipSpace();
    const c = this.peek();
    if (c === '\\') {
      const name = this.command();
      if (name === '{' || name === '}') return name;
      return SYMBOLS[name] ?? (name === '|' ? '‖' : '');
    }
    this.i++;
    return c === '.' ? '' : c;
  }

  private atom(): MNode | null {
    const c = this.peek();
    if (c === '') return null;
    if (c === '{') return this.group();
    if (/\s/.test(c)) { this.i++; return null; }
    if (/\d/.test(c) || (c === '.' && /\d/.test(this.src[this.i + 1] ?? ''))) {
      const m = /^(\d+(\.\d*)?|\.\d+)/.exec(this.src.slice(this.i)) as RegExpExecArray;
      this.i += m[0].length;
      return { t: 'ord', s: m[0], v: 'num' };
    }
    if (/[A-Za-z]/.test(c)) { this.i++; return { t: 'ord', s: c, v: 'it' }; }
    if (c === '\\') return this.control();
    this.i++;
    if (c === ':' && this.peek() === '=') { this.i++; return { t: 'ord', s: ':=', v: 'sym' }; }
    if (c === '-') return { t: 'ord', s: '−', v: 'sym' };
    if (c === '*') return { t: 'ord', s: '∗', v: 'sym' };
    if (c === '&' || c === '~') return c === '~' ? { t: 'space', w: 0.28 } : { t: 'ord', s: '&', v: 'sym' };
    return { t: 'ord', s: c, v: 'sym' };
  }

  private control(): MNode | null {
    const name = this.command();
    if (name === '\\') return { t: 'ord', s: '\\\\', v: 'err' };            // a row break outside a matrix
    if (name in SPACES) return { t: 'space', w: SPACES[name] };
    if (IGNORED.has(name)) return null;
    switch (name) {
      case 'frac': case 'dfrac': case 'tfrac': { const n = this.arg(); const d = this.arg(); return { t: 'frac', n, d }; }
      case 'sqrt': {
        this.skipSpace();
        let idx: MNode | undefined;
        if (this.peek() === '[') {
          this.i++;
          const start = this.i;
          while (!this.eof() && this.peek() !== ']') this.i++;
          const sub = new LatexParser(this.src.slice(start, this.i)).parseAll();
          this.i++;
          idx = sub;
        }
        return { t: 'sqrt', b: this.arg(), i: idx };
      }
      case 'text': case 'textrm': case 'textsf': return this.textNode(this.rawGroup(), 'rm');
      case 'textbf': return this.textNode(this.rawGroup(), 'bf');
      case 'mathrm': case 'operatorname': case 'mathsf': case 'mathtt': {
        const raw = this.rawGroup();
        return /\s/.test(raw) ? this.textNode(raw, 'rm') : restyle(new LatexParser(raw).parseAll(), 'rm');          // words with spaces stay text
      }
      case 'mathbf': case 'boldsymbol': case 'bm': return restyle(new LatexParser(this.rawGroup()).parseAll(), 'bf');
      case 'mathit': case 'textit': case 'mathcal': case 'mathscr': { const r = this.rawGroup(); return { t: 'ord', s: r, v: 'it' }; }
      case 'mathbb': { const r = this.rawGroup(); return { t: 'ord', s: BLACKBOARD[r] ?? r, v: 'rm' }; }
      case 'left': {
        const l = this.delimiter();
        const body = this.row(() => this.src.startsWith('\\right', this.i));
        let r = '';
        if (this.src.startsWith('\\right', this.i)) { this.i += 6; r = this.delimiter(); }
        return { t: 'fence', l, r, b: body };
      }
      case 'right': { this.delimiter(); return null; }
      case 'begin': return this.environment(this.rawGroup());
      case 'end': this.rawGroup(); return null;
      case 'overline': case 'bar': return { t: 'over', b: this.arg(), mark: 'bar' };
      case 'underline': return { t: 'over', b: this.arg(), mark: 'under' };
      case 'hat': case 'widehat': return { t: 'over', b: this.arg(), mark: 'hat' };
      case 'vec': return { t: 'over', b: this.arg(), mark: 'vec' };
      case 'dot': return { t: 'over', b: this.arg(), mark: 'dot' };
      case 'tilde': case 'widetilde': return { t: 'over', b: this.arg(), mark: 'tilde' };
      case 'color': case 'textcolor': this.rawGroup(); return null;
      default: break;
    }
    if (name in BIGOPS) return { t: 'bigop', s: BIGOPS[name] };
    if (OPERATORS.has(name)) return { t: 'ord', s: name, v: 'op' };
    if (name in SYMBOLS) return { t: 'ord', s: SYMBOLS[name], v: SYMBOLS[name] === '°' || /[Α-ω]/.test(SYMBOLS[name]) ? 'it' : 'sym' };
    if (name.length === 1 && !/[A-Za-z]/.test(name)) return { t: 'ord', s: name, v: 'sym' };      // \% \_ \{ \} \& \# \$
    return { t: 'ord', s: '\\' + name, v: 'err' };
  }

  private environment(env: string): MNode {
    const [l, r] = MATRIX_ENV[env] ?? ['', ''];
    if (env === 'array') this.rawGroup();                       // column spec
    const rows: MNode[][] = [];
    let cells: MNode[] = [];
    const endTag = '\\end';
    for (;;) {
      const cell = this.row(() => this.src.startsWith(endTag, this.i) || this.src.startsWith('\\\\', this.i) || this.peek() === '&');
      cells.push(cell);
      if (this.eof()) break;
      if (this.peek() === '&') { this.i++; continue; }
      if (this.src.startsWith('\\\\', this.i)) { this.i += 2; rows.push(cells); cells = []; continue; }
      if (this.src.startsWith(endTag, this.i)) { this.i += endTag.length; this.rawGroup(); break; }
      this.i++;                                                  // a stray '}' inside the environment
    }
    if (cells.length && !(cells.length === 1 && cells[0].t === 'row' && cells[0].c.length === 0 && rows.length)) rows.push(cells);
    return { t: 'matrix', rows, l, r, cases: env === 'cases' };
  }
}

/** Upright (or bold) letters inside \mathrm and \mathbf, keeping the structure (subscripts, powers) of the content. */
function restyle(n: MNode, v: 'rm' | 'bf'): MNode {
  switch (n.t) {
    case 'ord': return n.v === 'it' || n.v === 'op' ? { ...n, v } : n;
    case 'row': return { t: 'row', c: n.c.map((x) => restyle(x, v)) };
    case 'sup': return { t: 'sup', b: restyle(n.b, v), e: n.e };
    case 'sub': return { t: 'sub', b: restyle(n.b, v), e: restyle(n.e, v) };
    case 'subsup': return { t: 'subsup', b: restyle(n.b, v), lo: restyle(n.lo, v), hi: n.hi };
    default: return n;
  }
}

export function parseLatex(src: string): MNode {
  return new LatexParser(String(src)).parseAll();
}

/** A compact text form of a tree (used by the tests, and as a text alternative). */
export function plain(n: MNode): string {
  switch (n.t) {
    case 'row': return n.c.map(plain).join('');
    case 'ord': return n.s;
    case 'frac': return `(${plain(n.n)})/(${plain(n.d)})`;
    case 'sqrt': return n.i ? `root(${plain(n.i)}, ${plain(n.b)})` : `sqrt(${plain(n.b)})`;
    case 'sup': return `${plain(n.b)}^(${plain(n.e)})`;
    case 'sub': return `${plain(n.b)}_(${plain(n.e)})`;
    case 'subsup': return `${plain(n.b)}_(${plain(n.lo)})^(${plain(n.hi)})`;
    case 'fence': return `${n.l}${plain(n.b)}${n.r}`;
    case 'matrix': return `${n.l}[${n.rows.map((r) => r.map(plain).join(', ')).join('; ')}]${n.r}`;
    case 'space': return n.w > 0 ? ' ' : '';
    case 'over': return n.mark === 'bar' ? `bar(${plain(n.b)})` : plain(n.b);
    case 'bigop': return n.s + (n.lo ? `_(${plain(n.lo)})` : '') + (n.hi ? `^(${plain(n.hi)})` : '');
  }
}

/** True when a node is tall enough that surrounding brackets should grow. */
export function isTall(n: MNode): boolean {
  switch (n.t) {
    case 'row': return n.c.some(isTall);
    case 'frac': case 'matrix': case 'bigop': case 'sqrt': return true;
    case 'sup': return isTall(n.b) || isTall(n.e);
    case 'sub': return isTall(n.b);
    case 'subsup': return true;
    case 'fence': return isTall(n.b);
    case 'over': return isTall(n.b);
    default: return false;
  }
}
