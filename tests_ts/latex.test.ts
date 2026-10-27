import test from 'node:test';
import assert from 'node:assert/strict';
import { parseLatex, plain, isTall } from '../src/core/latex.js';

const p = (s: string): string => plain(parseLatex(s));

test('fractions, powers and roots', () => {
  assert.equal(p('\\frac{1}{2}'), '(1)/(2)');
  assert.equal(p('x^{2} + 1'), 'x^(2)+1');
  assert.equal(p('x^2'), 'x^(2)');
  assert.equal(p('\\sqrt{2}'), 'sqrt(2)');
  assert.equal(p('\\sqrt[3]{x}'), 'root(3, x)');
  assert.equal(p('a_{n}^{2}'), 'a_(n)^(2)');
});

test('functions, symbols and units', () => {
  assert.equal(p('\\sin\\left(x\\right)'), 'sin(x)');
  assert.equal(p('x\\,\\cos\\left(x\\right) + \\sin\\left(x\\right)'), 'x cos(x)+sin(x)');
  assert.equal(p('3000\\,\\mathrm{m}'), '3000 m');
  assert.equal(p('\\pi \\cdot 2'), 'π·2');
  assert.equal(p('x = -2\\ \\text{or}\\ x = 2'), 'x=−2 or x=2');
  assert.equal(p('50\\,\\mathrm{\\%}'), '50 %');
  assert.equal(p('1 \\leq x \\neq 3'), '1≤x≠3');
});

test('matrices and derivatives', () => {
  assert.equal(p('\\begin{bmatrix}1 & 2 \\\\ 3 & 4\\end{bmatrix}^{-1}'), '[[1, 2; 3, 4]]^(−1)');
  assert.equal(p('\\frac{\\mathrm{d}}{\\mathrm{d}x}\\left(x^{2}\\right)'), '(d)/(dx)(x^(2))');
  assert.equal(p('\\int_{0}^{1} x \\, \\mathrm{d}x'), '∫_(0)^(1)x dx');
});

test('chemistry output', () => {
  assert.equal(p('\\mathrm{N_{2}} + 3\\mathrm{H_{2}} \\longrightarrow 2\\mathrm{NH_{3}}'), 'N_(2)+3H_(2)⟶2NH_(3)');
});

test('assignment and text with spaces', () => {
  assert.equal(p('a := 5'), 'a:=5');
  assert.equal(p('\\mathrm{f(x) defined}'), 'f(x) defined');
});

test('malformed input never throws and keeps the text', () => {
  for (const bad of ['\\frac{1', '{{{{', '}}}', '\\left(x', '\\begin{bmatrix}1&2', '\\unknowncmd{x}', 'x^', '\\sqrt', '']) {
    const t = parseLatex(bad);
    assert.ok(t);
    plain(t);
  }
  assert.match(p('\\unknowncmd'), /unknowncmd/);
});

test('deep nesting is bounded', () => {
  const deep = '{'.repeat(500) + 'x' + '}'.repeat(500);
  assert.ok(plain(parseLatex(deep)).length >= 0);
});

test('tall content', () => {
  assert.equal(isTall(parseLatex('\\frac{1}{2}')), true);
  assert.equal(isTall(parseLatex('x+1')), false);
});
