/* DOM behavior tests. These do not substitute for hardware/browser rendering QA. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../src/engineering_workbench/static');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8').replace("import { AssemblyViewer } from './viewer.js';", 'const AssemblyViewer = window.TestViewer;');
const clone = (value) => JSON.parse(JSON.stringify(value));
const matrix = (x = 0, y = 0, z = 0) => [[1, 0, 0, x], [0, 1, 0, y], [0, 0, 1, z]];
function model(name = 'sample.step') {
  return { name, source_sha256: name, units: 'mm', meshes: { shared: { positions: [0,0,0, 10,0,0, 0,20,30], edges: [0,0,0, 10,0,0] } }, occurrences: [
    { id: 'fixed', name: 'Fixed', definition_id: 'shared', matrix_3x4: matrix(), baseline_matrix_3x4: matrix(), offset_mm: [0,0,0] },
    { id: 'movable', name: 'Movable', definition_id: 'shared', matrix_3x4: matrix(30), baseline_matrix_3x4: matrix(30), offset_mm: [0,0,0] },
  ] };
}
function makeState(withModel = false) { return { csrf_token: 'test-csrf', revision: 0, model: withModel ? model() : null }; }
function movedState() { const state = makeState(true); state.model.occurrences[1].offset_mm = [10,0,0]; state.model.occurrences[1].matrix_3x4 = matrix(40); return state; }
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() { for (let n = 0; n < 5; n++) await tick(); }
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => clone(body) });

async function setup(t, initial = makeState(), options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8000', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const window = dom.window;
  const $ = (id) => window.document.getElementById(id);
  const api = { state: clone(initial), calls: [], fail: null, hook: null, confirms: [], confirm: true, downloads: [], objectUrls: [], revoked: [] };
  window.URL.createObjectURL = (blob) => { api.objectUrls.push(blob); return 'blob:local-test'; };
  window.URL.revokeObjectURL = (url) => { api.revoked.push(url); };
  window.HTMLAnchorElement.prototype.click = function () { api.downloads.push({ href: this.href, filename: this.download }); };
  let view;
  window.TestViewer = class {
    constructor(container, select, failure) { if (options.webglFails) throw new Error('WebGL unavailable'); this.selectCallback = select; this.failureCallback = failure; this.models = []; view = this; }
    setModel(next) { this.models.push(clone(next)); }
    select(id) { this.selectedId = id; }
    fit() { this.fitCalled = true; }
  };
  window.console.warn = () => {};
  window.confirm = (message) => { api.confirms.push(message); return api.confirm; };
  window.fetch = async (url, init = {}) => {
    api.calls.push({ url, ...init });
    if (api.hook) { const result = await api.hook(url, init); if (result) return result; }
    if (options.initialFailure && api.calls.length === 1) throw new Error('Offline');
    if (url === '/api/state') return response(api.state);
    if (api.fail) { const result = api.fail; api.fail = null; if (result instanceof Error) throw result; return result; }
    if (url.startsWith('/api/export?')) {
      assert.equal(url, `/api/export?revision=${api.state.revision}`);
      return { ok: true, status: 200, blob: async () => new window.Blob(['ISO-10303-21;'], { type: 'application/step' }) };
    }
    assert.equal(init.headers['X-CSRF-Token'], api.state.csrf_token);
    const payload = typeof init.body === 'string' ? JSON.parse(init.body) : null;
    if (url === '/api/demo') {
      assert.equal(payload.revision, api.state.revision);
      api.state.model = model();
    } else if (url === '/api/import') {
      assert.equal(init.headers['X-Revision'], String(api.state.revision));
      assert.equal(init.headers['Content-Type'], 'application/octet-stream');
      api.state.model = model(decodeURIComponent(init.headers['X-Filename']));
    } else if (url === '/api/move') {
      assert.equal(payload.revision, api.state.revision);
      const part = api.state.model.occurrences.find((part) => part.id === payload.occurrence_id);
      part.offset_mm = payload.offset_mm;
      const base = part.baseline_matrix_3x4;
      part.matrix_3x4 = matrix(base[0][3] + payload.offset_mm[0], base[1][3] + payload.offset_mm[1], base[2][3] + payload.offset_mm[2]);
    } else throw new Error(`Unexpected route: ${url}`);
    api.state.revision++;
    return response(api.state);
  };
  window.eval(app);
  if (!options.noSettle) await settle();
  const input = (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event('input', { bubbles: true })); };
  const select = (id) => window.document.querySelector(`[data-id="${id}"]`).click();
  const upload = async (name, size = 12) => {
    const file = { name, size };
    Object.defineProperty($('file-input'), 'files', { value: [file], configurable: true });
    $('file-input').dispatchEvent(new window.Event('change'));
    await settle();
  };
  const submit = async () => { $('move-form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await settle(); };
  return { window, $, api, view, input, select, upload, submit };
}

test('initial workspace loads with no model; mutations unlock only after CSRF state', async (t) => {
  const h = await setup(t, makeState(), { noSettle: true });
  assert.equal(h.$('import-button').disabled, true);
  await settle();
  assert.equal(h.$('import-button').disabled, false);
  assert.equal(h.$('empty-state').hidden, false);
  assert.equal(h.$('move-form').hidden, true);
  assert.equal(h.$('export-button').disabled, true);
});

test('connection failure is recoverable through Retry', async (t) => {
  const h = await setup(t, makeState(), { initialFailure: true });
  assert.equal(h.$('retry-button').hidden, false);
  assert.equal(h.$('import-button').disabled, true);
  h.$('retry-button').click(); await settle();
  assert.equal(h.$('retry-button').hidden, true);
  assert.equal(h.$('import-button').disabled, false);
});

test('sample request carries revision and CSRF then populates genuine model state', async (t) => {
  const h = await setup(t);
  h.$('demo-button').click(); await settle();
  assert.equal(h.$('component-count').textContent, '2');
  assert.equal(h.$('empty-state').hidden, true);
  assert.equal(h.$('view-legend').hidden, false);
  assert.equal(h.$('movement-title').textContent, 'Fixed');
  assert.equal(h.view.models.at(-1).meshes.shared.positions.length, 9);
  assert.deepEqual(JSON.parse(h.api.calls.at(-1).body), { revision: 0 });
});

test('component list and viewport selection synchronize accessible selected state', async (t) => {
  const h = await setup(t, movedState());
  h.select('movable');
  assert.equal(h.$('offset-x').value, '10');
  assert.equal(h.view.selectedId, 'movable');
  assert.equal(h.window.document.querySelector('[data-id="movable"]').getAttribute('aria-pressed'), 'true');
  h.view.selectCallback('fixed');
  assert.equal(h.$('movement-title').textContent, 'Fixed');
  assert.equal(h.$('offset-x').value, '0');
});

test('offset submission is absolute and repeated identical values do not reapply', async (t) => {
  const h = await setup(t, makeState(true)); h.select('movable');
  h.input('offset-x', '10'); await h.submit();
  assert.deepEqual(h.api.state.model.occurrences[1].offset_mm, [10,0,0]);
  assert.equal(h.api.state.model.occurrences[1].matrix_3x4[0][3], 40);
  assert.equal(h.$('apply-button').disabled, true);
  const count = h.api.calls.length; await h.submit(); assert.equal(h.api.calls.length, count);
  h.input('offset-x', '20'); await h.submit();
  assert.equal(h.api.state.model.occurrences[1].matrix_3x4[0][3], 50);
  assert.match(h.$('selection-status').textContent, /Moved 20 mm/);
});

test('pending mutation disables selection, fields and imports and rejects duplicate submits', async (t) => {
  const h = await setup(t, makeState(true)); h.select('movable');
  let release; const gate = new Promise((resolve) => { release = resolve; });
  h.api.hook = async (url) => { if (url === '/api/move') await gate; };
  h.input('offset-x', '10');
  h.$('move-form').dispatchEvent(new h.window.Event('submit', { cancelable: true }));
  await tick();
  assert.equal(h.$('offset-x').disabled, true); assert.equal(h.$('import-button').disabled, true);
  assert.equal(h.window.document.querySelector('[data-id="fixed"]').disabled, true);
  h.view.selectCallback('fixed'); assert.equal(h.$('movement-title').textContent, 'Movable');
  await h.submit(); assert.equal(h.api.calls.filter((call) => call.url === '/api/move').length, 1);
  release(); await settle(); assert.equal(h.$('import-button').disabled, false);
});

test('reset sends zero absolute offsets and removes the moved status', async (t) => {
  const h = await setup(t, movedState()); h.select('movable');
  h.$('reset-button').click(); await settle();
  assert.deepEqual(h.api.state.model.occurrences[1].offset_mm, [0,0,0]);
  assert.equal(h.$('selection-status').textContent, 'At imported position');
  assert.equal(h.$('reset-button').disabled, true);
});

test('reset clears unapplied numeric changes without a mutation', async (t) => {
  const h = await setup(t, makeState(true));
  h.input('offset-y', '7'); h.$('reset-button').click();
  assert.equal(h.$('offset-y').value, '0');
  assert.equal(h.api.calls.length, 1);
});

test('empty, non-finite and out-of-range offsets cannot be submitted', async (t) => {
  const h = await setup(t, makeState(true));
  for (const value of ['', 'NaN', 'Infinity', '1000001', '-1000001']) {
    h.input('offset-x', value); assert.equal(h.$('apply-button').disabled, true); await h.submit();
  }
  assert.equal(h.api.calls.length, 1);
  h.input('offset-x', '-0.125'); assert.equal(h.$('apply-button').disabled, false);
});

test('invalid extension, empty and oversized imports are rejected locally', async (t) => {
  const h = await setup(t);
  await h.upload('part.exe'); assert.match(h.$('status').textContent, /extension/);
  await h.upload('empty.step', 0); assert.match(h.$('status').textContent, /empty/);
  await h.upload('large.step', 20 * 1024 * 1024 + 1); assert.match(h.$('status').textContent, /20 MiB/);
  assert.equal(h.api.calls.length, 1); assert.equal(h.$('file-input').value, '');
});

test('edited assembly replacement can be cancelled, then accepted with encoded name and revision', async (t) => {
  const h = await setup(t, movedState());
  h.api.confirm = false; await h.upload('New assembly.step');
  assert.equal(h.api.calls.length, 1); assert.equal(h.$('model-name').textContent, 'sample.step');
  h.api.confirm = true; await h.upload('New assembly.step');
  assert.equal(h.api.confirms.length, 2);
  assert.equal(h.$('model-name').textContent, 'New assembly.step');
  assert.equal(h.api.calls.at(-1).headers['X-Filename'], 'New%20assembly.step');
  assert.equal(h.api.calls.at(-1).headers['X-Revision'], '0');
});

test('failed import preserves displayed assembly and allows same file retry', async (t) => {
  const h = await setup(t, makeState(true));
  h.api.fail = response({ error: 'STEP import failed' }, 400);
  await h.upload('retry.step');
  assert.equal(h.$('model-name').textContent, 'sample.step'); assert.equal(h.$('file-input').value, '');
  assert.equal(h.$('status').textContent, 'STEP import failed');
  await h.upload('retry.step'); assert.equal(h.$('model-name').textContent, 'retry.step');
});

test('stale movement reloads current state instead of silently replaying the edit', async (t) => {
  const h = await setup(t, makeState(true)); h.select('movable');
  h.api.state.model.occurrences[1].offset_mm = [27,0,0]; h.api.state.revision = 9;
  h.api.fail = response({ error: 'Stale revision' }, 409);
  h.input('offset-x', '10'); await h.submit();
  assert.equal(h.$('offset-x').value, '27');
  assert.match(h.$('status').textContent, /another tab/);
  assert.equal(h.api.calls.filter((call) => call.url === '/api/move').length, 1);
});

test('stale replacement reloads the other tab model without retrying import', async (t) => {
  const h = await setup(t, makeState(true));
  h.api.state.model.name = 'Other tab.step'; h.api.state.revision = 8;
  h.api.fail = response({ error: 'Stale revision' }, 409);
  await h.upload('replace.step');
  assert.equal(h.$('model-name').textContent, 'Other tab.step');
  assert.equal(h.api.calls.filter((call) => call.url === '/api/import').length, 1);
});

test('lost response is reconciled by a read, never an automatic POST retry', async (t) => {
  const h = await setup(t, makeState(true)); h.select('movable');
  h.api.fail = new Error('Network interrupted');
  h.input('offset-x', '10'); await h.submit();
  assert.equal(h.api.calls.filter((call) => call.url === '/api/move').length, 1);
  assert.equal(h.api.calls.at(-1).url, '/api/state');
  assert.match(h.$('status').textContent, /latest workspace/);
});

test('unverifiable mutation locks editing until a successful reconnect', async (t) => {
  const h = await setup(t, makeState(true));
  h.api.hook = async () => { throw new Error('Disconnected'); };
  h.input('offset-x', '10'); await h.submit();
  assert.equal(h.$('import-button').disabled, true); assert.equal(h.$('retry-button').hidden, false);
  assert.match(h.$('status').textContent, /could not be verified/);
  h.api.hook = null; h.$('retry-button').click(); await settle();
  assert.equal(h.$('import-button').disabled, false);
});

test('server labels and errors remain text, not executable HTML', async (t) => {
  const state = makeState(true); state.model.name = '<img src=x onerror=alert(1)>';
  state.model.occurrences[0].name = '<script>danger()</script>';
  const h = await setup(t, state);
  assert.equal(h.$('model-name').textContent, state.model.name);
  assert.equal(h.$('component-list').querySelector('script'), null);
  assert.equal(h.$('model-name').querySelector('img'), null);
  h.api.fail = response({ error: '<img src=x>' }, 400); await h.upload('bad.step');
  assert.equal(h.$('status').textContent, '<img src=x>');
  assert.equal(h.$('status').children.length, 0);
});

test('WebGL failure leaves import, selection, editing and export controls usable', async (t) => {
  const h = await setup(t, makeState(true), { webglFails: true });
  assert.equal(h.$('viewer-error').hidden, false); assert.equal(h.$('fit-button').disabled, true);
  assert.equal(h.$('import-button').disabled, false);
  h.input('offset-x', '2'); await h.submit();
  assert.deepEqual(h.api.state.model.occurrences[0].offset_mm, [2,0,0]);
  assert.equal(h.$('export-button').disabled, false);
});

test('export without a loaded model is disabled and does not send a request', async (t) => {
  const h = await setup(t);
  h.$('export-button').click(); await settle();
  assert.equal(h.$('export-button').disabled, true);
  assert.equal(h.api.calls.length, 1);
});

test('export includes displayed revision, locks changes and starts a local blob download', async (t) => {
  const state = makeState(true); state.revision = 4;
  const h = await setup(t, state);
  let release; const gate = new Promise((resolve) => { release = resolve; });
  h.api.hook = async (url) => { if (url.startsWith('/api/export?')) await gate; };
  h.$('export-button').click(); await tick();
  assert.equal(h.$('import-button').disabled, true); assert.equal(h.$('offset-x').disabled, true);
  h.$('export-button').click(); assert.equal(h.api.calls.filter((call) => call.url.startsWith('/api/export')).length, 1);
  release(); await settle();
  assert.equal(h.api.calls.at(-1).url, '/api/export?revision=4');
  assert.deepEqual(h.api.downloads, [{ href: 'blob:local-test', filename: 'daedalus-modified.step' }]);
  assert.equal(h.api.objectUrls[0].type, 'application/step');
  assert.match(h.$('status').textContent, /download started/);
  assert.equal(h.$('export-button').disabled, false);
  assert.equal(h.window.document.querySelector('a[download]'), null);
});

test('stale export reconciles state without downloading or automatically retrying', async (t) => {
  const h = await setup(t, makeState(true));
  h.api.state.revision = 7; h.api.state.model.name = 'Latest model.step';
  h.api.fail = response({ error: 'Stale export revision' }, 409);
  h.$('export-button').click(); await settle();
  assert.equal(h.$('model-name').textContent, 'Latest model.step');
  assert.equal(h.api.downloads.length, 0);
  assert.equal(h.api.calls.filter((call) => call.url.startsWith('/api/export')).length, 1);
  assert.match(h.$('status').textContent, /then export again/);
});

test('failed export reports failure, retains the assembly, and unlocks retry', async (t) => {
  const h = await setup(t, makeState(true));
  h.api.fail = response({ error: 'Export failed' }, 500);
  h.$('export-button').click(); await settle();
  assert.match(h.$('status').textContent, /Could not export STEP/);
  assert.equal(h.$('model-name').textContent, 'sample.step');
  assert.equal(h.$('export-button').disabled, false);
  assert.equal(h.api.downloads.length, 0);
  h.$('export-button').click(); await settle(); assert.equal(h.api.downloads.length, 1);
});

test('document ships only local scripts/styles, named numeric fields and responsive styles', () => {
  assert.doesNotMatch(html, /<script(?![^>]*src=)[^>]*>/i);
  assert.doesNotMatch(html, /https?:\/\//);
  for (const axis of ['X', 'Y', 'Z']) assert.ok(html.includes(`aria-label="${axis} offset in millimetres"`));
  const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
  assert.match(css, /max-width:730px/); assert.match(css, /prefers-reduced-motion/);
  assert.match(fs.readFileSync(path.join(root, 'vendor/OrbitControls.js'), 'utf8'), /from '\.\/three.module.js'/);
});
