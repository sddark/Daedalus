import { AssemblyViewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const fields = ['offset-x', 'offset-y', 'offset-z'].map($);
const MAX_FILE_BYTES = 20 * 1024 * 1024;
let state = { model: null, revision: 0, csrf_token: '' };
let selectedId = null;
let busy = false;
let ready = false;
let viewer = null;

const moved = (part) => part.offset_mm.some((value) => Math.abs(value) > 1e-9);
const selectedPart = () => state.model?.occurrences.find((part) => part.id === selectedId);
const hasChanges = () => Boolean(state.model?.occurrences.some(moved));
const offsets = () => fields.map((field) => field.value === '' ? NaN : Number(field.value));
const validOffsets = (values) => values.every((value) => Number.isFinite(value) && Math.abs(value) <= 1_000_000);
const sameOffsets = (a, b) => a.every((value, index) => Math.abs(value - b[index]) < 1e-9);
const formatNumber = (value) => Number(value.toFixed(6)).toLocaleString(undefined, { maximumFractionDigits: 6 });

function status(message, error = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
}

function viewerFailure(message) {
  $('viewer-error').textContent = message;
  $('viewer-error').hidden = false;
}

try {
  viewer = new AssemblyViewer($('viewport'), (id) => { if (!busy) select(id); }, viewerFailure);
} catch (error) {
  console.warn('The 3D viewer could not start.', error);
  viewerFailure('3D rendering is unavailable. Enable WebGL in your browser, then reload. You can still import, select, move, and export components.');
}

function controls() {
  const part = selectedPart();
  const values = offsets();
  const canEdit = ready && !busy && Boolean(part);
  for (const field of fields) field.disabled = !canEdit;
  for (const id of ['import-button', 'empty-import', 'demo-button']) $(id).disabled = busy || !ready;
  $('apply-button').disabled = !canEdit || !validOffsets(values) || sameOffsets(values, part.offset_mm);
  $('reset-button').disabled = !canEdit || (!moved(part) && sameOffsets(values, part.offset_mm));
  $('fit-button').disabled = !viewer || !state.model;
  const canExport = ready && !busy && Boolean(state.model);
  $('export-button').disabled = !canExport;
  for (const button of $('component-list').querySelectorAll('button')) button.disabled = busy;
  $('viewport').setAttribute('aria-busy', String(busy));
  $('retry-button').disabled = busy;
}

function setBusy(value, label = '') {
  busy = value;
  $('busy-indicator').hidden = !value;
  $('busy-label').textContent = label;
  controls();
}

function select(id) {
  selectedId = id;
  const part = selectedPart();
  for (const button of $('component-list').querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.id === id));
  $('move-form').hidden = !part;
  $('movement-title').textContent = part ? part.name || 'Unnamed component' : 'Select a component';
  $('movement-help').textContent = part ? 'Move this occurrence without changing its shape.' : 'Pick a part in the view or component list to adjust its position.';
  if (part) {
    part.offset_mm.forEach((value, index) => { fields[index].value = String(value); });
    $('selection-status').textContent = moved(part) ? `Moved ${formatNumber(Math.hypot(...part.offset_mm))} mm from the imported position` : 'At imported position';
    $('selection-status').classList.toggle('is-moved', moved(part));
  }
  viewer?.select(id);
  controls();
}

function renderList() {
  $('component-list').replaceChildren();
  for (const [index, part] of (state.model?.occurrences || []).entries()) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'component-row';
    button.dataset.id = part.id;
    button.setAttribute('aria-pressed', String(part.id === selectedId));
    button.setAttribute('aria-label', `${part.name || 'Unnamed component'}, component ${index + 1}${moved(part) ? ', moved' : ''}`);
    // Only this fixed decorative icon uses innerHTML; all server labels use textContent.
    button.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m10 2 7 4v8l-7 4-7-4V6l7-4Zm0 8 7-4M3 6l7 4m0 0v8"/></svg>';
    const name = document.createElement('span');
    name.className = 'component-name';
    name.textContent = part.name || 'Unnamed component';
    name.title = name.textContent;
    button.append(name);
    const indicator = document.createElement('span');
    indicator.className = moved(part) ? 'component-indicator' : 'small-mark';
    indicator.setAttribute('aria-hidden', 'true');
    if (!moved(part)) indicator.textContent = String(index + 1).padStart(2, '0');
    button.append(indicator);
    button.addEventListener('click', () => { if (!busy) select(part.id); });
    item.append(button);
    $('component-list').append(item);
  }
}

function acceptState(next) {
  if (!next || typeof next.csrf_token !== 'string' || !Number.isInteger(next.revision) || !('model' in next)) throw new Error('The server returned an incomplete workspace.');
  state = next;
  ready = true;
  $('retry-button').hidden = true;
  const model = state.model;
  const parts = model?.occurrences || [];
  if (!parts.some((part) => part.id === selectedId)) selectedId = parts[0]?.id || null;
  $('model-name').textContent = model?.name || 'Your workspace';
  $('model-name').title = model?.name || '';
  const count = parts.length;
  const changed = parts.filter(moved).length;
  $('model-caption').textContent = model ? `${count} component${count === 1 ? '' : 's'} · ${changed ? `${changed} moved` : 'Imported positions'} · Millimetres` : 'One view. Every move in context.';
  $('component-count').textContent = String(count);
  $('components-help').textContent = model ? 'Select a component to adjust its position.' : 'Your imported assembly will appear here.';
  $('empty-state').hidden = Boolean(model);
  $('empty-components').hidden = Boolean(model);
  $('view-legend').hidden = !model;
  $('axes-label').hidden = !model;
  renderList();
  try { viewer?.setModel(model, selectedId); }
  catch (error) { console.warn('Could not display assembly geometry.', error); viewerFailure('This assembly could not be drawn. Component controls and STEP export remain available.'); }
  select(selectedId);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', credentials: 'same-origin', ...options });
  let result;
  try { result = await response.json(); }
  catch { const error = new Error('The local server returned an unreadable response.'); error.status = response.ok ? undefined : response.status; throw error; }
  if (!response.ok) {
    const error = new Error(typeof result.error === 'string' ? result.error : `The request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return result;
}

async function loadState() {
  if (busy) return;
  setBusy(true, 'Opening workspace…');
  try {
    acceptState(await request('/api/state'));
    status(state.model ? 'Workspace restored. Select a component to begin.' : 'Ready when you are. Import a STEP assembly or explore the sample.');
  } catch (error) {
    ready = false;
    $('retry-button').hidden = false;
    status(`Could not connect to the local workspace. ${error.message}`, true);
  } finally { setBusy(false); }
}

async function mutate(path, options, label, success) {
  if (busy || !ready) return;
  setBusy(true, label);
  status(label);
  try {
    const next = await request(path, { method: 'POST', ...options, headers: { ...options.headers, 'X-CSRF-Token': state.csrf_token } });
    acceptState(next);
    status(typeof success === 'function' ? success() : success);
  } catch (error) {
    if (error.status === 409) {
      try {
        acceptState(await request('/api/state'));
        status('The workspace changed in another tab. The latest positions are loaded. Review them before applying another move.', true);
      } catch {
        ready = false;
        $('retry-button').hidden = false;
        status('The workspace changed, but its latest state could not be loaded. Reconnect before editing.', true);
      }
    } else if (!error.status || error.status >= 500 || error.status === 403) {
      // A lost response is not evidence that a POST failed. Re-read, never retry
      // a mutation automatically; absolute offsets make a user retry idempotent.
      try {
        acceptState(await request('/api/state'));
        status(`${error.message} The latest workspace is loaded; check the result before retrying.`, true);
      } catch {
        ready = false;
        $('retry-button').hidden = false;
        status(`${error.message} The result could not be verified. Reconnect before retrying.`, true);
      }
    } else {
      status(error.message, true);
    }
  } finally { setBusy(false); }
}

function confirmReplacement() {
  return !hasChanges() || window.confirm('Replace this assembly? Current movements will be discarded. Export a STEP file first if you want to keep them.');
}

function chooseFile() { if (ready && !busy) $('file-input').click(); }
$('import-button').addEventListener('click', chooseFile);
$('empty-import').addEventListener('click', chooseFile);
$('file-input').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = ''; // Same file can be selected again after Cancel or failure.
  if (!file || busy || !ready) return;
  if (!/\.(step|stp)$/i.test(file.name)) { status('Choose a STEP assembly with a .step or .stp extension.', true); return; }
  if (file.size === 0) { status('This file is empty. Choose a STEP assembly with geometry.', true); return; }
  if (file.size > MAX_FILE_BYTES) { status('This file exceeds the 20 MiB import limit. Choose a smaller STEP assembly.', true); return; }
  if (!confirmReplacement()) return;
  await mutate('/api/import', { body: file, headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name), 'X-Revision': String(state.revision) } }, 'Importing and tessellating assembly…', () => `Imported ${state.model?.name || file.name}. Source file unchanged.`);
});
$('demo-button').addEventListener('click', () => {
  if (!confirmReplacement()) return;
  void mutate('/api/demo', { body: JSON.stringify({ revision: state.revision }), headers: { 'Content-Type': 'application/json' } }, 'Preparing sample assembly…', 'Sample ready. Select Movable and try a 10 mm X offset.');
});
$('move-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const part = selectedPart();
  const values = offsets();
  if (!part || !validOffsets(values) || sameOffsets(values, part.offset_mm)) return;
  void mutate('/api/move', { body: JSON.stringify({ occurrence_id: part.id, offset_mm: values, revision: state.revision }), headers: { 'Content-Type': 'application/json' } }, 'Applying movement…', 'Movement applied. The dashed outline marks the imported position.');
});
$('reset-button').addEventListener('click', () => {
  const part = selectedPart();
  if (!part || busy) return;
  if (!moved(part)) { select(part.id); status('Unapplied offsets cleared. Component remains at its imported position.'); return; }
  void mutate('/api/move', { body: JSON.stringify({ occurrence_id: part.id, offset_mm: [0, 0, 0], revision: state.revision }), headers: { 'Content-Type': 'application/json' } }, 'Restoring imported position…', 'Component restored to its imported position.');
});
for (const field of fields) field.addEventListener('input', controls);
$('fit-button').addEventListener('click', () => viewer?.fit());
$('retry-button').addEventListener('click', () => void loadState());
async function exportStep() {
  if (busy || !ready || !state.model) return;
  setBusy(true, 'Preparing STEP export…');
  status('Preparing STEP export…');
  try {
    const response = await fetch(`/api/export?revision=${state.revision}`, { cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok) {
      let result;
      try { result = await response.json(); } catch { result = {}; }
      const error = new Error(result.error || `STEP export failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'daedalus-modified.step';
    link.hidden = true;
    document.body.append(link);
    try { link.click(); }
    finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 60_000); }
    status('STEP download started. Your imported source is unchanged.');
  } catch (error) {
    if (error.status === 409) {
      try {
        acceptState(await request('/api/state'));
        status('The workspace changed in another tab. Review the latest assembly, then export again.', true);
      } catch {
        ready = false;
        $('retry-button').hidden = false;
        status('The workspace changed, but its latest state could not be loaded. Reconnect before exporting.', true);
      }
    } else status(`Could not export STEP. ${error.message}`, true);
  } finally { setBusy(false); }
}
$('export-button').addEventListener('click', () => void exportStep());
void loadState();
