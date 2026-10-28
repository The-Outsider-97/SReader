import test from 'node:test';
import assert from 'node:assert/strict';
import { parseLatex, plain } from '../src/core/latex.js';

// The LaTeX SciCal writes for the functions of a graph (scical/services/figures.py). Reader typesets it under the picture, so every
// command it uses must be one Reader understands: a command that is not would be shown as raw source (a backslash in the output).
const p = (s: string): string => plain(parseLatex(s));

const CASES: Array<[string, string]> = [
  ['f_{1}(x) = x', 'f_(1)(x)=x'],
  ['f_{2}(x) = x^{2}', 'f_(2)(x)=x^(2)'],
  ['f_{4}(x) = \\sin\\left(x\\right)', 'f_(4)(x)=sin(x)'],
  ['f_{5}(x) = a \\cdot \\cos\\left(x\\right),\\quad a = 2', 'f_(5)(x)=a·cos(x), a=2'],
  ['f_{7}(x) = \\frac{1}{x - 2}', 'f_(7)(x)=(1)/(x−2)'],
  ['\\mathbf{r}_{4}(t) = \\left(\\cos\\left(t\\right),\\ \\sin\\left(t\\right)\\right),\\quad 0 \\le t \\le 2 \\cdot \\pi', 'r_(4)(t)=(cos(t), sin(t)), 0≤t≤2·π'],
  ['r_{2}(t) = 1 + \\cos\\left(t\\right),\\quad 0 \\le t \\le 2 \\cdot \\pi', 'r_(2)(t)=1+cos(t), 0≤t≤2·π'],
  ['a_{3}(n) = 2^{n},\\quad 1 \\le n \\le 8', 'a_(3)(n)=2^(n), 1≤n≤8'],
  ['\\mathbf{F}_{7}(x, y) = \\left(y,\\ -x\\right)', 'F_(7)(x,y)=(y, −x)'],
  ['\\mathrm{data}_{6}\\ \\left(3\\ \\mathrm{points},\\ \\mathrm{linear\\ fit}\\right)', 'data_(6) (3 points, linear fit)'],
  ['y = x^{2} + 1', 'y=x^(2)+1'],
];

test('every kind of graph formula SciCal writes is typeset', () => {
  for (const [src, want] of CASES) assert.equal(p(src), want, src);
});

test('none of them leaves a raw command in the output', () => {
  for (const [src] of CASES) assert.ok(!/\\/.test(p(src)), `${src} -> ${p(src)}`);
});
