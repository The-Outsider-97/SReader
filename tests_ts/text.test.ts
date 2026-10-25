import test from 'node:test';
import assert from 'node:assert/strict';
import { u16ToCp, cpToU16, cpLength, cpSlice, hasAstral, marksToU16 } from '../src/core/text.js';

test('offsets agree with the service for text with emoji', () => {
  const t = 'ab😀cd𝔘ef';                       // two astral characters
  assert.equal(t.length, 10);
  assert.equal(cpLength(t), 8);
  assert.equal(u16ToCp(t, 4), 3);              // after the emoji
  assert.equal(cpToU16(t, 3), 4);
  for (let cp = 0; cp <= 8; cp++) assert.equal(u16ToCp(t, cpToU16(t, cp)), cp);
  assert.equal(cpSlice(t, 2, 3), '😀');
  assert.equal(cpSlice(t, 5, 6), '𝔘');
  assert.equal(cpSlice(t, 0, 8), t);
  assert.ok(hasAstral(t));
  assert.ok(!hasAstral('plain'));
  assert.equal(u16ToCp('plain', 3), 3);
  assert.deepEqual(marksToU16(t, [{ start: 3, end: 6 }]), [{ start: 4, end: 8 }]);
  assert.deepEqual(marksToU16('plain', [{ start: 1, end: 2 }]), [{ start: 1, end: 2 }]);
  assert.equal(cpToU16('😀', 99), 2);          // out of range stays inside the string
  assert.equal(cpSlice('x', 0, 5), 'x');
});

test('a lone surrogate does not crash the conversion', () => {
  const t = 'a\ud83db';
  assert.equal(u16ToCp(t, 3), 3);
  assert.equal(cpToU16(t, 2), 2);
});
