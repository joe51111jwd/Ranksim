import { test } from 'node:test';
import assert from 'node:assert/strict';
import { History, undoKey } from '../../../src/aeo/ranksim/static/js/editor/history.js';

test('undo and redo walk the snapshots', () => {
  const h = new History();
  h.reset('a');
  assert.equal(h.canUndo, false);
  h.record('b', { now: 0 });
  h.record('c', { now: 5000 });
  assert.equal(h.undo(), 'b');
  assert.equal(h.undo(), 'a');
  assert.equal(h.undo(), null);
  assert.equal(h.redo(), 'b');
  assert.equal(h.redo(), 'c');
  assert.equal(h.redo(), null);
});

test('typing in one place within the window is one step', () => {
  const h = new History({ window: 1000 });
  h.reset('');
  h.record('H', { group: 'type:1', now: 0 });
  h.record('He', { group: 'type:1', now: 300 });
  h.record('Hel', { group: 'type:1', now: 900 });
  h.record('Help', { group: 'type:1', now: 2500 }); // a pause starts a new step
  h.record('Help!', { group: 'type:2', now: 2600 }); // another block too
  assert.equal(h.undo(), 'Help');
  assert.equal(h.undo(), 'Hel');
  assert.equal(h.undo(), '');
});

test('a new edit after undo drops the redo branch, and same state is ignored', () => {
  const h = new History();
  h.reset('a');
  h.record('b', { now: 0 });
  h.undo();
  assert.equal(h.record('a'), false);
  h.record('x', { now: 10 });
  assert.equal(h.canRedo, false);
  assert.equal(h.undo(), 'a');
});

test('seal ends a typing run', () => {
  const h = new History();
  h.reset('');
  h.record('a', { group: 'g', now: 0 });
  h.seal();
  h.record('ab', { group: 'g', now: 10 });
  assert.equal(h.undo(), 'a');
});

test('the stack is capped and survives a save and load', () => {
  const h = new History({ limit: 3 });
  h.reset('0');
  for (let k = 1; k <= 6; k += 1) h.record(String(k), { now: k * 5000 });
  assert.equal(h.past.length, 3);
  const back = History.from(JSON.parse(JSON.stringify(h)), { limit: 3 });
  assert.equal(back.present, '6');
  assert.equal(back.undo(), '5');
  assert.equal(History.from(null).present, '');
});

test('keys: Cmd or Ctrl Z undoes, with Shift redoes, Ctrl Y redoes', () => {
  assert.equal(undoKey({ metaKey: true, key: 'z' }), 'undo');
  assert.equal(undoKey({ ctrlKey: true, key: 'Z', shiftKey: true }), 'redo');
  assert.equal(undoKey({ ctrlKey: true, key: 'y' }), 'redo');
  assert.equal(undoKey({ key: 'z' }), null);
  assert.equal(undoKey({ ctrlKey: true, altKey: true, key: 'z' }), null);
});
