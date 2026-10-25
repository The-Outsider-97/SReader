import test from 'node:test';
import assert from 'node:assert/strict';
import { clamp01, simplify, eraserTouches, hitsPolyline, limitMove, bounds, translate, toFraction, smoothPath, distanceToSegment } from '../src/core/geometry.js';
import { ZOOM_MIN, ZOOM_MAX, clampZoom, stepZoom, pageScale, scaleToPercent, renderScale, CSS_PX_PER_POINT } from '../src/core/zoom.js';
import { buildSpreads, spreadIndexOf, turn, slotsOf } from '../src/core/spreads.js';
import { readability, contrastRatio, parseHex, blend, PALETTES, resolveTheme, markStyle } from '../src/core/appearance.js';
import { toolLook, widthAt, groupId } from '../src/core/strokes.js';
import { commandFor, SHORTCUTS, describe as describeKey } from '../src/core/keys.js';
import { parseTags, parsePageNumber, parsePercent, formatBytes, relativeTime, titleOf } from '../src/core/format.js';
import { Api, ApiError, qs } from '../src/core/api.js';

const key = (k: string, o: Partial<{ ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean }> = {}) =>
  ({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });

test('zoom is limited to 25..400 and steps through a fixed ladder', () => {
  assert.equal(clampZoom(10), ZOOM_MIN);
  assert.equal(clampZoom(900), ZOOM_MAX);
  assert.equal(clampZoom(NaN), 100);
  assert.equal(stepZoom(100, 1), 110);
  assert.equal(stepZoom(100, -1), 90);
  assert.equal(stepZoom(400, 1), 400);
  assert.equal(stepZoom(25, -1), 25);
  assert.equal(stepZoom(105, 1), 110);          // between steps goes to the next step
  assert.equal(stepZoom(105, -1), 100);
  let z = 25;
  const seen = [z];
  for (let i = 0; i < 40; i++) { const n = stepZoom(z, 1); if (n === z) break; z = n; seen.push(z); }
  assert.equal(seen.at(-1), 400);
  assert.deepEqual(seen, [...new Set(seen)].sort((a, b) => a - b));
});

test('fit-width and fit-page compute from the viewport and stay inside the zoom range', () => {
  const page = { width: 612, height: 792 };
  const vp = { width: 1000, height: 800 };
  const fw = pageScale('fit-width', 100, page, vp);
  assert.ok(Math.abs(fw * 612 + 32 - 1000) < 0.01, 'page plus margins fills the width');
  const fp = pageScale('fit-page', 100, page, vp);
  assert.ok(fp * 792 + 32 <= 800.01);
  assert.ok(fp <= fw);
  assert.equal(pageScale('custom', 100, page, vp), CSS_PX_PER_POINT);
  assert.equal(scaleToPercent(pageScale('custom', 250, page, vp)), 250);
  assert.ok(pageScale('fit-width', 100, page, { width: 20, height: 20 }) >= 0.25 * CSS_PX_PER_POINT - 1e-9);
  assert.ok(pageScale('fit-width', 100, { width: 10, height: 10 }, { width: 5000, height: 5000 }) <= 4 * CSS_PX_PER_POINT + 1e-9);
  const two = pageScale('fit-width', 100, page, vp, 2);
  assert.ok(two < fw);
});

test('render scale is capped by pixel budget and device ratio', () => {
  const page = { width: 612, height: 792 };
  assert.ok(renderScale(5, 2, 60_000_000, page) * renderScale(5, 2, 60_000_000, page) * 612 * 792 <= 60_000_001);
  assert.equal(renderScale(1, 1, 60_000_000, page), 1);
  assert.equal(renderScale(1, 9, 60_000_000, page), 3);
  assert.ok(renderScale(0.001, 1, 60_000_000, page) >= 0.05);
});

test('spreads: cover alone, then pairs; odd page counts end alone', () => {
  assert.deepEqual(buildSpreads(5, 'spread', true), [[0], [1, 2], [3, 4]]);
  assert.deepEqual(buildSpreads(4, 'spread', true), [[0], [1, 2], [3]]);
  assert.deepEqual(buildSpreads(4, 'spread', false), [[0, 1], [2, 3]]);
  assert.deepEqual(buildSpreads(1, 'spread', true), [[0]]);
  assert.deepEqual(buildSpreads(0, 'spread', true), []);
  assert.deepEqual(buildSpreads(3, 'continuous', true), [[0], [1], [2]]);
  const s = buildSpreads(7, 'spread', true);
  assert.equal(spreadIndexOf(s, 4), 2);
  assert.equal(turn(s, 0, -1), 0);
  assert.equal(turn(s, s.length - 1, 1), s.length - 1);
  assert.deepEqual(slotsOf([0], 7, true), [null, 0]);
  assert.deepEqual(slotsOf([6], 7, true), [6, null]);
  assert.deepEqual(slotsOf([1, 2], 7, true), [1, 2]);
  // every page appears exactly once
  const flat = s.flat();
  assert.deepEqual(flat, [0, 1, 2, 3, 4, 5, 6]);
});

test('contrast maths and readability warnings', () => {
  const white = parseHex('#ffffff')!;
  const black = parseHex('#000000')!;
  assert.ok(Math.abs(contrastRatio(white, black) - 21) < 0.01);
  assert.equal(parseHex('red'), null);
  assert.equal(parseHex('#12345'), null);
  assert.equal(readability(null, 'light', 100).level, 'ok');
  assert.equal(readability('#cccccc', 'light', 100).level, 'poor');
  assert.equal(readability(null, 'light', 30).level, 'poor');
  assert.notEqual(readability(null, 'dark', 60).level, 'poor');
  assert.equal(readability('#ffffff', 'contrast', 100).level, 'ok');
  assert.deepEqual(blend([0, 0, 0], [255, 255, 255], 0.5), [128, 128, 128]);
  for (const [name, p] of Object.entries(PALETTES)) {
    const r = contrastRatio(parseHex(p.text)!, parseHex(p.bg)!);
    assert.ok(r >= 7, `${name} palette body text contrast ${r.toFixed(1)} should reach AAA`);
    const l = contrastRatio(parseHex(p.link)!, parseHex(p.bg)!);
    assert.ok(l >= 4.5, `${name} link contrast ${l.toFixed(1)}`);
  }
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
  assert.equal(resolveTheme('nonsense', true), 'light');
  assert.equal(markStyle('#ffff00', 0.9, 'dark').background, 'rgba(255, 255, 0, 0.5)');
});

test('themes are different palettes, not inversions of each other', () => {
  const dark = PALETTES.dark, light = PALETTES.light;
  assert.notEqual(dark.bg, '#000000');                       // dark is not pure black paper
  assert.notEqual(dark.text, '#ffffff');                     // and not pure white ink (less glare)
  assert.notEqual(PALETTES.sepia.bg, light.bg);
  assert.equal(PALETTES.contrast.bg, '#000000');
});

test('simplify keeps the shape of a stroke with far fewer points', () => {
  const pts = Array.from({ length: 500 }, (_, i) => ({ x: i / 499, y: 0.5 + 0.2 * Math.sin((i / 499) * Math.PI * 2) }));
  const s = simplify(pts, 0.002);
  assert.ok(s.length < 120 && s.length > 8, `got ${s.length}`);
  assert.deepEqual(s[0], pts[0]);
  assert.deepEqual(s.at(-1), pts.at(-1));
  for (const p of pts) {
    let best = Infinity;
    for (let i = 1; i < s.length; i++) best = Math.min(best, distanceToSegment(p, s[i - 1], s[i]));
    assert.ok(best <= 0.0021);
  }
  assert.deepEqual(simplify([{ x: 0, y: 0 }], 0.1), [{ x: 0, y: 0 }]);
  const straight = Array.from({ length: 50 }, (_, i) => ({ x: i / 49, y: 0.3 }));
  assert.equal(simplify(straight, 0.001).length, 2);
});

test('eraser touches only strokes it crosses, measured in pixels', () => {
  const box = { width: 1000, height: 500 };
  const stroke = [{ x: 0.1, y: 0.5 }, { x: 0.9, y: 0.5 }];
  assert.ok(eraserTouches([{ x: 0.5, y: 0.5 }], stroke, 6, box));
  assert.ok(eraserTouches([{ x: 0.5, y: 0.5 + 5 / 500 }], stroke, 6, box));
  assert.ok(!eraserTouches([{ x: 0.5, y: 0.5 + 20 / 500 }], stroke, 6, box));
  assert.ok(!eraserTouches([{ x: 0.95, y: 0.5 }], stroke, 6, box));
  assert.ok(hitsPolyline({ x: 0.2, y: 0.2 }, [{ x: 0.2, y: 0.2 }], 0.001));
});

test('moving drawings never leaves the frame and never distorts them', () => {
  const r = bounds([{ x: 0.2, y: 0.2 }, { x: 0.4, y: 0.3 }]);
  const frame: [number, number, number, number] = [0, 0, 1, 1];
  assert.deepEqual(limitMove(r, 0.9, 0.9, frame), { dx: 0.6, dy: 0.7 });
  const m = limitMove(r, -0.9, -0.9, frame);
  assert.ok(Math.abs(m.dx + 0.2) < 1e-9 && Math.abs(m.dy + 0.2) < 1e-9);
  const moved = translate([{ x: 0.2, y: 0.2, p: 0.7 }], 0.1, -0.1);
  assert.ok(Math.abs(moved[0].x - 0.3) < 1e-9 && moved[0].p === 0.7);
  assert.deepEqual(limitMove([-1, 0, 2, 1], 0.1, 0, frame), { dx: 0, dy: 0 });   // bigger than the frame: stays put
});

test('pointer positions become fractions of the host', () => {
  const box = { left: 100, top: 50, width: 400, height: 200 };
  assert.deepEqual(toFraction(300, 150, box), { x: 0.5, y: 0.5 });
  assert.deepEqual(toFraction(50, 0, box), { x: 0, y: 0 });
  assert.equal(toFraction(700, 150, box, true).x, 1.5);
  assert.equal(clamp01(2), 1);
  assert.equal(smoothPath([]), '');
  assert.match(smoothPath([{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 0 }]), /^M0 0Q1 1 1.5 0.5L2 0$/);
});

test('tools look different and pressure scales width', () => {
  assert.equal(toolLook('highlighter', 2, 1).cap, 'butt');
  assert.ok(toolLook('highlighter', 2, 1).width >= 6);
  assert.ok(toolLook('highlighter', 2, 1).opacity <= 0.4);
  assert.equal(toolLook('pen', 3, 1).width, 3);
  assert.equal(widthAt(4, 0.5, 0.35), 4);
  assert.ok(widthAt(4, 1, 0.35) > 4 && widthAt(4, 0, 0.35) < 4);
  assert.equal(widthAt(4, NaN, 0.35), 4);
  const a = groupId(null, 'p1', 1000, 2500, () => 'g1');
  const b = groupId(a, 'p1', 2000, 2500, () => 'g2');
  const c = groupId(b, 'p2', 2500, 2500, () => 'g3');
  const d = groupId(c, 'p2', 9000, 2500, () => 'g4');
  assert.deepEqual([a.id, b.id, c.id, d.id], ['g1', 'g1', 'g3', 'g4']);
});

test('shortcuts: unique, and plain letters are ignored while typing', () => {
  const keys = SHORTCUTS.map((s) => s.keys);
  assert.equal(new Set(keys).size, keys.length, 'no two commands share a shortcut');
  assert.equal(new Set(SHORTCUTS.map((s) => s.id)).size, SHORTCUTS.length);
  assert.equal(commandFor(key('f', { ctrlKey: true }), false), 'search');
  assert.equal(commandFor(key('f', { ctrlKey: true }), true), 'search');       // Ctrl shortcuts still work in a field
  assert.equal(commandFor(key('p'), false), 'toolPen');
  assert.equal(commandFor(key('p'), true), null);                              // typing a 'p' in a note must not pick the pen
  assert.equal(commandFor(key('Escape'), true), 'escape');
  assert.equal(commandFor(key('F11'), true), 'fullscreen');
  assert.equal(commandFor(key('=', { ctrlKey: true }), false), 'zoomIn');
  assert.equal(commandFor(key('+', { ctrlKey: true, shiftKey: true }), false), 'zoomIn');
  assert.equal(commandFor(key('z', { ctrlKey: true, shiftKey: true }), false), 'redo');
  assert.equal(commandFor(key('Z', { ctrlKey: true }), false), 'undo');
  assert.equal(commandFor(key('q'), false), null);
  assert.equal(describeKey(key('H', { ctrlKey: true, shiftKey: true })), 'Ctrl+Shift+H');
  assert.equal(commandFor(key('H', { ctrlKey: true, shiftKey: true }), true), 'highlight');
});

test('format helpers', () => {
  assert.deepEqual(parseTags('To Do, #idea, to do,\n evidence ,'), ['To Do', 'idea', 'evidence']);
  assert.equal(parseTags('x'.repeat(41)).length, 0);
  assert.equal(parsePageNumber('12', 20), 11);
  assert.equal(parsePageNumber('p. 3', 20), 2);
  assert.equal(parsePageNumber('21', 20), null);
  assert.equal(parsePageNumber('0', 20), null);
  assert.equal(parsePageNumber('abc', 20), null);
  assert.equal(parsePercent('45%'), 0.45);
  assert.equal(parsePercent('101%'), null);
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(-1), '');
  assert.equal(relativeTime('2026-01-01T00:00:00Z', Date.parse('2026-01-01T00:00:30Z')), 'just now');
  assert.equal(relativeTime('2026-01-01T00:00:00Z', Date.parse('2026-01-01T03:00:00Z')), '3 h ago');
  assert.equal(relativeTime('nonsense'), '');
  assert.equal(titleOf({ title: null, path: 'C:\\docs\\Report.pdf' }), 'Report.pdf');
  assert.equal(qs({ a: 1, b: undefined, c: 'x y', d: '' }), '?a=1&c=x%20y');
});

test('api client sends the marker on changes, keeps error codes and maps network failure', async () => {
  const calls: Array<{ path: string; init: RequestInit }> = [];
  const mk = (status: number, body: unknown) => async (path: string, init?: RequestInit) => {
    calls.push({ path, init: init ?? {} });
    return new Response(JSON.stringify(body), { status });
  };
  let api = new Api(mk(200, { ok: true, data: { n: 1 } }));
  assert.deepEqual(await api.get('/api/x'), { n: 1 });
  assert.equal((calls[0].init.headers as Record<string, string>)['X-SLAI-Reader'], undefined);
  await api.post('/api/y', { a: 1 });
  assert.equal((calls[1].init.headers as Record<string, string>)['X-SLAI-Reader'], '1');
  assert.equal(calls[1].init.body, '{"a":1}');
  api = new Api(mk(409, { ok: false, error: { code: 'conflict', message: 'changed elsewhere', details: { currentRev: 3 } } }));
  await assert.rejects(() => api.patch('/api/z', {}), (e: unknown) => e instanceof ApiError && e.status === 409 && e.code === 'conflict' && e.details.currentRev === 3 && e.message === 'changed elsewhere');
  api = new Api(async () => { throw new TypeError('network'); });
  await assert.rejects(() => api.get('/api/x'), (e: unknown) => e instanceof ApiError && e.code === 'offline');
  api = new Api(async () => new Response('<html>oops</html>', { status: 502 }));
  await assert.rejects(() => api.get('/api/x'), (e: unknown) => e instanceof ApiError && e.status === 502);
  api = new Api(mk(200, { ok: true, data: 1 }));
  await api.upload('a b.pdf', new ArrayBuffer(4), 'secret');
  assert.equal(calls.at(-1)!.path, '/api/upload?name=a%20b.pdf');
  assert.equal((calls.at(-1)!.init.headers as Record<string, string>)['X-Reader-Password'], 'secret');
});
