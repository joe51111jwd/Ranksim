// node --test tests/e2e/hooks.test.mjs
// The plugin key collision guard in static/js/hooks.js. hooks.js has no DOM dependency.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SOURCE = readFileSync(new URL('../../src/aeo/ranksim/static/js/hooks.js', import.meta.url), 'utf8');
let n = 0;
const fresh = () => import(`data:text/javascript;base64,${Buffer.from(`${SOURCE}\n// ${n += 1}`).toString('base64')}`);

function warnings(fn) {
  const seen = [];
  const keep = console.warn;
  console.warn = (...args) => seen.push(args.join(' '));
  try { fn(); } finally { console.warn = keep; }
  return seen;
}

test('a plugin key that matches a built-in warns, and still replaces it', async () => {
  const H = await fresh();
  H.registerOverviewTile({ key: 'share', order: 60, render: () => 'built in' });
  H.registerOverviewTile({ key: 'rankings', order: 50, render: () => 'ranks' });
  assert.equal(H.sealBuiltIns(), 2);
  const seen = warnings(() => H.registerOverviewTile({ key: 'share', order: 90, render: () => 'plugin' }));
  assert.equal(seen.length, 1);
  assert.match(seen[0], /overview tile key 'share'/);
  assert.match(seen[0], /replace: true/);
  const tiles = H.overviewTiles();
  assert.deepEqual(tiles.map((t) => t.key), ['rankings', 'share']);
  assert.equal(tiles[1].render(), 'plugin');
});

test('replaceable built-ins and replace: true stay quiet; new keys and pre-seal duplicates too', async () => {
  const H = await fresh();
  H.registerSettingsSection({ key: 'gsc', order: 20, replaceable: true, render: () => null });
  H.registerSettingsSection({ key: 'delete', order: 90, render: () => null });
  H.registerScreen('overview', { label: 'Overview', order: 10, render: () => null });
  H.registerScreen('overview', { label: 'Overview', order: 10, render: () => null }); // before the seal: quiet
  H.sealBuiltIns();
  const seen = warnings(() => {
    H.registerSettingsSection({ key: 'gsc', order: 20, render: () => 'gsc plugin' });
    H.registerSettingsSection({ key: 'delete', order: 90, replace: true, render: () => null });
    H.registerOverviewTile({ key: 'client-report', order: 110, render: () => null });
    H.registerOverviewTile({ key: 'client-report', order: 110, render: () => null }); // plugin replacing itself
  });
  assert.deepEqual(seen, []);
  assert.equal(H.settingsSections().find((s) => s.key === 'gsc').render(), 'gsc plugin');
});

test('the same key in different registries never collides', async () => {
  const H = await fresh();
  H.registerSettingsSection({ key: 'share', order: 50, render: () => null });
  H.sealBuiltIns();
  const seen = warnings(() => H.registerOverviewTile({ key: 'share', order: 60, render: () => null }));
  assert.deepEqual(seen, []);
});

test('screen notices: per screen and for every screen, in order, and a throwing render is skipped', async () => {
  const H = await fresh();
  H.registerScreenNotice({ key: 'moved', screens: ['searches', 'track-history'], order: 20, render: (ctx) => `moved on ${ctx.screen}` });
  H.registerScreenNotice({ key: 'everywhere', order: 10, render: () => 'all screens' });
  H.registerScreenNotice({ key: 'broken', screen: 'searches', order: 30, render: () => { throw new Error('boom'); } });
  assert.deepEqual(H.screenNotices('searches').map((n) => n.key), ['everywhere', 'moved', 'broken']);
  assert.deepEqual(H.screenNotices('overview').map((n) => n.key), ['everywhere']);
  assert.deepEqual(H.screenNotices('track-history').map((n) => n.key), ['everywhere', 'moved']);
  const keep = console.error;
  const errors = [];
  console.error = (...args) => errors.push(args.join(' '));
  try {
    assert.deepEqual(H.renderAll(H.screenNotices('searches'), { screen: 'searches' }), ['all screens', 'moved on searches']);
  } finally {
    console.error = keep;
  }
  assert.equal(errors.length, 1);
  assert.match(errors[0], /broken render failed/);
});
