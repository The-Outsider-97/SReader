import test from 'node:test';
import assert from 'node:assert/strict';
import { PatchBatcher, scopeFor, DOC_KEYS, type Scope, type Timers } from '../src/core/commit.js';

/** Timers the test controls. */
function fakeTimers(): Timers & { run(): void; pending(): number } {
  let next = 1; const jobs = new Map<number, () => void>();
  return { set: (fn) => { jobs.set(next, fn); return next++; }, clear: (id) => { jobs.delete(id); },
    run: () => { const all = [...jobs.values()]; jobs.clear(); for (const fn of all) fn(); }, pending: () => jobs.size };
}
const tick = () => new Promise<void>((r) => globalThis.setTimeout(r, 0));

function make(send: (scope: Scope, patch: Record<string, unknown>) => Promise<void>) {
  const timers = fakeTimers();
  const b = new PatchBatcher({ delay: 150, send, timers });
  return { b, timers };
}

test('only document settings can be kept for one document, and only when asked and open', () => {
  assert.equal(scopeFor('fontSize', true, true), 'doc');
  assert.equal(scopeFor('fontSize', false, true), 'global');
  assert.equal(scopeFor('fontSize', true, false), 'global');
  assert.equal(scopeFor('reduceMotion', true, true), 'global', 'a setting a document cannot override always goes to the general settings');
  assert.ok(DOC_KEYS.includes('theme') && !DOC_KEYS.includes('reduceMotion') && !DOC_KEYS.includes('sidebar'));
});

test('a slider drag becomes one request carrying the last value', async () => {
  const sent: Array<[Scope, Record<string, unknown>]> = [];
  const { b, timers } = make(async (s, p) => { sent.push([s, p]); });
  for (const n of [14, 15, 16, 17, 18]) b.add('global', { fontSize: n });
  assert.equal(sent.length, 0);
  assert.equal(timers.pending(), 1, 'only the newest debounce timer is alive');
  timers.run(); await tick();
  assert.deepEqual(sent, [['global', { fontSize: 18 }]]);
  assert.equal(b.phase, 'saved');
});

test('document and global changes are never merged into one request', async () => {
  const sent: Array<[Scope, Record<string, unknown>]> = [];
  const { b, timers } = make(async (s, p) => { sent.push([s, p]); });
  b.add('doc', { fontSize: 20 }); b.add('global', { reduceMotion: true });
  timers.run(); await tick();
  assert.deepEqual(sent, [['doc', { fontSize: 20 }], ['global', { reduceMotion: true }]]);
});

test('the newest choice of a setting wins even if it changes scope', async () => {
  const sent: Array<[Scope, Record<string, unknown>]> = [];
  const { b, timers } = make(async (s, p) => { sent.push([s, p]); });
  b.add('doc', { fontSize: 20 }); b.add('global', { fontSize: 22 });
  timers.run(); await tick();
  assert.deepEqual(sent, [['global', { fontSize: 22 }]]);
});

test('unconfirmed keys cover waiting and in-flight changes, so the panel does not overwrite them', async () => {
  let release!: () => void;
  const { b, timers } = make(() => new Promise<void>((r) => { release = r; }));
  b.add('global', { fontSize: 20 });
  assert.deepEqual([...b.unconfirmedKeys()], ['fontSize']);
  timers.run(); await tick();
  assert.deepEqual([...b.unconfirmedKeys()], ['fontSize'], 'sent but not answered');
  assert.equal(b.phase, 'saving');
  release(); await tick();
  assert.equal(b.unconfirmedKeys().size, 0);
  assert.equal(b.phase, 'saved');
});

test('a failed save is reported, not shown as saved, and can be retried', async () => {
  let fail = true; let calls = 0;
  const { b, timers } = make(async () => { calls++; if (fail) throw new Error('Could not save your settings. The previous settings were kept.'); });
  b.add('global', { theme: 'dark' }, true); await tick();
  assert.equal(b.phase, 'error');
  assert.match(b.error ?? '', /previous settings were kept/);
  fail = false; b.retry(); await tick();
  assert.equal(calls, 2);
  assert.equal(b.phase, 'saved');
  assert.equal(b.error, null);
  void timers;
});

test('retry does not bring back a value the person has changed since the failure', async () => {
  let fail = true; const sent: Array<Record<string, unknown>> = [];
  const { b, timers } = make(async (_s, p) => { sent.push(p); if (fail) throw new Error('nope'); });
  b.add('global', { fontSize: 20, lineSpacing: 1.8 }, true); await tick();
  b.add('global', { fontSize: 30 });                      // changed after the failure
  assert.equal(b.phase, 'error', 'a new edit does not hide an unresolved error');
  fail = false; timers.run(); await tick();               // the new edit saves
  b.retry(); await tick();
  assert.deepEqual(sent.at(-1), { lineSpacing: 1.8 }, 'fontSize 20 must not overwrite the newer 30');
});

test('a reset discards edits that were not sent and does not offer old failures for retry', async () => {
  const sent: Array<Record<string, unknown>> = [];
  const { b, timers } = make(async (_s, p) => { sent.push(p); });
  b.add('doc', { fontSize: 40 });
  b.discard('doc');
  timers.run(); await tick();
  assert.deepEqual(sent, [], 'the pending slider value must not be applied after the reset');
  assert.equal(b.phase, 'idle');
});

test('an edit in flight during a reset that then fails is not offered for retry', async () => {
  let reject!: (e: Error) => void;
  const { b, timers } = make(() => new Promise<void>((_r, j) => { reject = j; }));
  b.add('doc', { fontSize: 40 }); timers.run(); await tick();
  b.discard();
  reject(new Error('late failure')); await tick();
  b.retry();
  assert.equal(b.hasPending(), false);
});

test('flush sends immediately and resolves after the answers', async () => {
  const order: string[] = [];
  const { b } = make(async (s) => { await tick(); order.push(s); });
  b.add('global', { theme: 'dark' }); b.add('doc', { fontSize: 20 });
  await b.flush();
  assert.deepEqual(order.sort(), ['doc', 'global']);
});
