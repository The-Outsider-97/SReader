import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { activateSide, AppState, type SideTab } from '../src/client/state.js';

const sections: Array<Exclude<SideTab, 'none'>> = [
  'outline', 'thumbnails', 'annotations', 'search', 'assistant',
];

test('active rail icon collapses its own panel', () => {
  for (const id of sections) assert.equal(activateSide(id, id), 'none');
});

test('switching between sections never requires an intermediate collapse', () => {
  for (const current of sections) {
    for (const selected of sections) {
      if (current !== selected) assert.equal(activateSide(current, selected), selected);
    }
  }
});

test('each rail icon directly opens its section from collapsed state', () => {
  for (const id of sections) assert.equal(activateSide('none', id), id);
});

test('setting an unchanged sidebar state does not emit a redundant event', () => {
  const state = new AppState({} as ConstructorParameters<typeof AppState>[0]);
  let events = 0;
  state.on('side', () => { events++; });
  state.setSide('outline');
  state.setSide('outline');
  state.setSide('none');
  state.setSide('none');
  assert.equal(events, 2);
});
