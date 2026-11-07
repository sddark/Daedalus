import { AssemblyViewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const fields = ['offset-x', 'offset-y', 'offset-z'].map($);
const MAX_FILE_BYTES = 20 * 1024 * 1024;
let state = { model: null, revision: 0, csrf_token: '' };
let selectedId = null;
let busy = false;
let ready = false;
let viewer = null;
let diffMode = false;
let comparison = null;
let comparisonId = '';
let comparisonAfterId = '';
const reviewing = () => Boolean(comparison?.after_checkpoint_id);
let pendingCheckpoint = null;
let pendingPlacement = null;
const placementIdentity = () => JSON.stringify([state.project?.id, state.model?.source_sha256, state.model?.occurrences.map((part) => [part.id, part.offset_mm, part.matrix_3x4])]);
const canRetryCheckpoint = () => Boolean(pendingCheckpoint && pendingCheckpoint.revision === state.revision && pendingPlacement === placementIdentity());
const persistent = () => Boolean(state.persistence_enabled);
const dirty = () => persistent() ? Boolean(state.project?.dirty) : hasChanges();

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
  const canEdit = ready && !busy && !state.recovery_required && !pendingCheckpoint && !reviewing() && Boolean(part);
  for (const field of fields) field.disabled = !canEdit;
  for (const id of ['import-button', 'empty-import', 'demo-button']) $(id).disabled = busy || !ready;
  $('move-button').disabled = !canEdit;
  $('apply-button').disabled = !canEdit || !validOffsets(values) || sameOffsets(values, part.offset_mm);
  $('reset-button').disabled = !canEdit || (!moved(part) && sameOffsets(values, part.offset_mm));
  $('fit-button').disabled = !viewer || !state.model;
  for (const id of ['normal-view', 'diff-view']) $(id).disabled = !viewer || !state.model || busy;
  const canExport = ready && !busy && !reviewing() && !state.recovery_required && Boolean(state.model);
  $('export-button').disabled = !canExport;
  for (const button of $('component-list').querySelectorAll('button')) button.disabled = busy;
  $('viewport').setAttribute('aria-busy', String(busy));
  $('retry-button').disabled = busy;
  $('checkpoint-button').disabled = busy || !ready || reviewing() || !state.project || Boolean(state.recovery_required) || (pendingCheckpoint ? !canRetryCheckpoint() : Boolean(state.project?.history_conflict) || !dirty());
  $('checkpoint-button').textContent = pendingCheckpoint ? 'Verify pending save' : 'Save checkpoint';
  $('checkpoint-message').disabled = busy || reviewing() || Boolean(pendingCheckpoint) || Boolean(state.recovery_required || state.project?.history_conflict);
  $('checkpoint-select').disabled = busy || !ready || !state.project;
  $('comparison-after-select').disabled = busy || !ready || !state.project || Boolean(state.recovery_required);
  $('return-working').disabled = busy;
  $('restore-button').disabled = busy || !ready || reviewing() || !comparisonId || Boolean(state.project?.history_conflict);
  $('project-select').disabled = busy || !ready;
  $('reload-project').disabled = busy || !ready || !state.project;
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
  $('move-form').hidden = !part || reviewing();
  $('movement-title').textContent = part ? part.name || 'Unnamed component' : 'Select a component';
  $('movement-help').textContent = reviewing() ? 'Read-only checkpoint review. Return to working positions to edit.' : part ? 'Move this occurrence without changing its shape.' : 'Pick a part in the view or component list to adjust its position.';
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
  if (state.project?.id !== next.project?.id) { comparisonId = ''; comparisonAfterId = ''; pendingCheckpoint = null; }
  comparison = null;
  viewer?.setComparison?.(null);
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
  renderHistory();
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
    error.code = result.code;
    throw error;
  }
  return result;
}

async function loadState() {
  if (busy) return;
  setBusy(true, 'Opening workspace…');
  try {
    acceptState(await request('/api/state'));
    if (comparisonId) await loadComparison();
    status(state.model ? 'Workspace restored. Select a component to begin.' : 'Ready when you are. Import a STEP assembly or explore the sample.');
  } catch (error) {
    ready = false;
    $('retry-button').hidden = false;
    status(`Could not connect to the local workspace. ${error.message}`, true);
  } finally { setBusy(false); }
}

async function mutate(path, options, label, success) {
  if (busy || !ready || (reviewing() && ['/api/move', '/api/checkpoint', '/api/restore'].includes(path)) || (state.recovery_required && !['/api/restore', '/api/project/open', '/api/import', '/api/demo'].includes(path))) return false;
  setBusy(true, label);
  status(label);
  try {
    const next = await request(path, { method: 'POST', ...options, headers: { ...options.headers, 'X-CSRF-Token': state.csrf_token } });
    if (['/api/project/open', '/api/restore', '/api/import', '/api/demo'].includes(path)) { pendingCheckpoint = null; pendingPlacement = null; }
    acceptState(next);
    if (comparisonId) {
      try { await loadComparison(); }
      catch { comparisonId = ''; comparisonAfterId = ''; comparison = null; viewer?.setComparison?.(null); renderHistory(); }
    }
    status(typeof success === 'function' ? success() : success);
    return true;
  } catch (error) {
    if (error.status === 409 && error.code !== 'uncertain_checkpoint') {
      if (path === '/api/checkpoint') pendingCheckpoint = null;
      try {
        acceptState(await request('/api/state'));
        status('The workspace changed in another tab. The latest positions are loaded. Review them before applying another move.', true);
      } catch {
        ready = false;
        $('retry-button').hidden = false;
        status('The workspace changed, but its latest state could not be loaded. Reconnect before editing.', true);
      }
    } else if (!error.status || error.status >= 500 || error.status === 403 || error.code === 'uncertain_checkpoint') {
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
      if (path === '/api/checkpoint') pendingCheckpoint = null;
      status(error.message, true);
    }
  } finally { setBusy(false); }
  return false;
}

async function confirmReplacement() {
  if (busy || !ready) return null;
  if (!dirty() && !state.recovery_required) return {};
  if (!persistent()) return window.confirm('Replace this assembly? Current movements will be discarded. Export a STEP file first if you want to keep them.') ? {} : null;
  const previousFocus = document.activeElement;
  setBusy(true);
  const dialog = $('replacement-dialog');
  $('replacement-title').textContent = state.recovery_required ? 'Reload a verified version?' : 'Keep your changes?';
  $('replacement-description').textContent = state.recovery_required ? 'The working state cannot be verified. Explicitly discard it to reload saved positions, or cancel.' : 'There are uncheckpointed changes. Save them before continuing, or explicitly discard them.';
  dialog.hidden = false;
  $('replacement-save').disabled = Boolean(state.recovery_required) || (pendingCheckpoint ? !canRetryCheckpoint() : Boolean(state.project?.history_conflict));
  $('replacement-cancel').focus();
  const choice = await new Promise((resolve) => {
    const finish = (choice) => {
      for (const [id, handler] of handlers) $(id).removeEventListener('click', handler);
      dialog.removeEventListener('keydown', keydown);
      resolve(choice);
    };
    const handlers = ['save', 'discard', 'cancel'].map((choice) => [`replacement-${choice}`, () => finish(choice)]);
    for (const [id, handler] of handlers) $(id).addEventListener('click', handler);
    const keydown = (event) => {
      if (event.key === 'Escape') { event.preventDefault(); finish('cancel'); }
      if (event.key === 'Tab') {
        const buttons = [...dialog.querySelectorAll('button')].filter((button) => !button.disabled);
        const index = buttons.indexOf(document.activeElement);
        event.preventDefault(); buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
      }
    };
    dialog.addEventListener('keydown', keydown);
  });
  dialog.hidden = true;
  setBusy(false);
  previousFocus?.focus();
  if (choice === 'discard') return { discard_changes: true };
  if (choice === 'save') {
    // The dialog explicitly saves the working assembly, never a reviewed version.
    comparisonAfterId = ''; comparison = null; viewer?.setComparison?.(null);
    renderHistory(); select(selectedId);
    if (await saveCheckpoint()) return {};
  }
  return null;
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
  const replacement = await confirmReplacement();
  if (!replacement) return;
  await mutate('/api/import', { body: file, headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name), 'X-Revision': String(state.revision), ...(replacement.discard_changes ? { 'X-Discard-Changes': 'true' } : {}) } }, 'Importing and tessellating assembly…', () => `Imported ${state.model?.name || file.name}. Source file unchanged.`);
});
$('demo-button').addEventListener('click', async () => {
  const replacement = await confirmReplacement();
  if (!replacement) return;
  void mutate('/api/demo', { body: JSON.stringify({ revision: state.revision, ...replacement }), headers: { 'Content-Type': 'application/json' } }, 'Preparing sample assembly…', 'Sample ready. Select Movable and try a 10 mm X offset.');
});
$('move-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const part = selectedPart();
  const values = offsets();
  if (reviewing() || !part || !validOffsets(values) || sameOffsets(values, part.offset_mm)) return;
  void mutate('/api/move', { body: JSON.stringify({ occurrence_id: part.id, offset_mm: values, revision: state.revision }), headers: { 'Content-Type': 'application/json' } }, 'Applying movement…', () => diffMode ? 'Movement applied. Diff shows the selected reference and current positions.' : 'Movement applied. Choose Diff to compare with the imported position.');
});
$('reset-button').addEventListener('click', () => {
  const part = selectedPart();
  if (!part || busy || reviewing()) return;
  if (!moved(part)) { select(part.id); status('Unapplied offsets cleared. Component remains at its imported position.'); return; }
  void mutate('/api/move', { body: JSON.stringify({ occurrence_id: part.id, offset_mm: [0, 0, 0], revision: state.revision }), headers: { 'Content-Type': 'application/json' } }, 'Restoring imported position…', 'Component restored to its imported position.');
});
for (const field of fields) field.addEventListener('input', controls);
$('move-button').addEventListener('click', () => {
  if (!ready || busy || reviewing() || !selectedPart()) return;
  fields[0].focus();
  fields[0].select();
});
$('fit-button').addEventListener('click', () => viewer?.fit());
for (const [id, enabled] of [['normal-view', false], ['diff-view', true]]) {
  $(id).addEventListener('click', () => {
    diffMode = enabled;
    viewer?.setDiffMode(enabled);
    $('normal-view').setAttribute('aria-pressed', String(!enabled));
    $('diff-view').setAttribute('aria-pressed', String(enabled));
    $('original-legend').hidden = !enabled;
    $('view-legend').classList.toggle('diff', enabled);
    status(reviewing() ? (enabled ? 'Checkpoint review: translucent from and to positions. Working changes are kept.' : 'Checkpoint review: opaque to positions. Working changes are kept.') : enabled ? 'Diff view: translucent current and selected reference positions. Unchanged components appear once.' : 'Normal view: opaque current assembly only.');
  });
}
$('retry-button').addEventListener('click', () => void loadState());
async function exportStep() {
  if (busy || !ready || reviewing() || !state.model || state.recovery_required) return;
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

function renderHistory() {
  $('projects-control').hidden = !persistent();
  $('history-panel').hidden = !persistent() || !state.project;
  $('project-select').replaceChildren(new Option('Open project…', ''));
  for (const project of state.projects || []) $('project-select').add(new Option(`${project.name} · ${project.id.slice(0, 8)}`, project.id));
  $('project-select').value = state.project?.id || '';
  $('save-status').textContent = state.recovery_required ? 'Recovery required. Reload a saved project or checkpoint before editing.' : pendingCheckpoint ? (canRetryCheckpoint() ? 'Save outcome uncertain. Verify the pending save before editing.' : 'Workspace changed since the pending save. Reload the saved project to reconcile it.') : state.project?.history_conflict ? 'Saved history changed elsewhere. Reload the saved project before saving.' : dirty() ? 'Unsaved changes' : 'All changes checkpointed';
  $('checkpoint-select').replaceChildren(new Option('Imported positions', ''));
  $('comparison-after-select').replaceChildren(new Option('Working positions', ''));
  for (const checkpoint of (state.project?.checkpoints || []).slice(0, 50)) {
    const label = `${checkpoint.message || 'Checkpoint'} · ${checkpoint.id.slice(0, 8)}${checkpoint.id === (state.project.latest_head || state.project.head) ? ' (latest)' : ''}`;
    $('checkpoint-select').add(new Option(label, checkpoint.id));
    $('comparison-after-select').add(new Option(label, checkpoint.id));
  }
  if (![...$('checkpoint-select').options].some((option) => option.value === comparisonId)) comparisonId = '';
  $('checkpoint-select').value = comparisonId;
  if (![...$('comparison-after-select').options].some((option) => option.value === comparisonAfterId)) comparisonAfterId = '';
  $('comparison-after-select').value = comparisonAfterId;
  renderComparison();
}

function renderComparison() {
  const review = reviewing();
  $('review-notice').hidden = !review;
  $('return-working').hidden = !review;
  const count = state.model?.occurrences.length || 0;
  const changed = state.model?.occurrences.filter(moved).length || 0;
  if (state.model) $('model-caption').textContent = review ? `Read-only checkpoint review · ${comparisonId.slice(0, 8)} → ${comparisonAfterId.slice(0, 8)} · Working changes kept` : `${count} component${count === 1 ? '' : 's'} · ${changed ? `${changed} moved` : 'Imported positions'} · Millimetres`;
  $('current-legend').replaceChildren();
  const currentMarker = document.createElement('i'); currentMarker.className = 'legend-current';
  $('current-legend').append(currentMarker, review ? `To: ${comparisonAfterId.slice(0, 8)}` : 'Current position');
  $('comparison-deltas').replaceChildren();
  $('comparison-summary').textContent = comparison ? `${comparison.report.changes.length} moved · ${comparison.report.unchanged_count} unchanged` : comparisonId ? 'Comparison needs to be loaded. Select a checkpoint to compare.' : 'Comparing with imported positions.';
  $('original-legend').replaceChildren();
  const marker = document.createElement('i'); marker.className = 'legend-original';
  $('original-legend').append(marker, comparison ? `From: ${comparisonId.slice(0, 8)}` : 'Imported position');
  for (const change of comparison?.report.changes || []) {
    const item = document.createElement('li');
    const part = state.model?.occurrences.find((part) => part.id === change.id);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'comparison-change';
    button.textContent = `${part?.name || change.id}: Δ [${change.delta_mm.map(formatNumber).join(', ')}] mm · ${formatNumber(change.distance_mm)} mm`;
    button.addEventListener('click', () => { if (!busy) select(change.id); });
    item.append(button);
    $('comparison-deltas').append(item);
  }
}

async function loadComparison() {
  comparison = null;
  viewer?.setComparison?.(null);
  if (!comparisonId || state.recovery_required) { renderComparison(); return; }
  const result = await request(`/api/compare?checkpoint=${encodeURIComponent(comparisonId)}&revision=${state.revision}${comparisonAfterId ? `&after=${encodeURIComponent(comparisonAfterId)}` : ''}`);
  if (result.revision !== state.revision || result.checkpoint_id !== comparisonId || (result.after_checkpoint_id || '') !== comparisonAfterId || (comparisonAfterId && !result.after_matrices) || !result.matrices || !result.report) throw new Error('The comparison does not match this workspace.');
  comparison = result;
  viewer?.setComparison?.(result.matrices, comparisonAfterId ? result.after_matrices : null);
  renderComparison();
  select(selectedId);
}

async function saveCheckpoint() {
  if (busy || !ready || reviewing() || !state.project || state.recovery_required || (pendingCheckpoint ? !canRetryCheckpoint() : state.project.history_conflict)) return false;
  // Retain the original request identity after an uncertain response. Never silently
  // create a second commit with a fresh UUID when retrying the same save.
  if (!pendingCheckpoint) pendingPlacement = placementIdentity();
  pendingCheckpoint ||= { revision: state.revision, expected_head: state.project.head, request_id: crypto.randomUUID(), message: $('checkpoint-message').value.trim() || 'Save working positions' };
  const saved = await mutate('/api/checkpoint', { body: JSON.stringify(pendingCheckpoint), headers: { 'Content-Type': 'application/json' } }, 'Saving checkpoint…', 'Checkpoint saved locally.');
  if (saved) { pendingCheckpoint = null; pendingPlacement = null; $('checkpoint-message').value = ''; renderHistory(); controls(); }
  return saved;
}
$('checkpoint-form').addEventListener('submit', (event) => { event.preventDefault(); void saveCheckpoint(); });
async function changeComparison() {
  if (busy || !ready) return;
  comparisonId = $('checkpoint-select').value;
  comparisonAfterId = $('comparison-after-select').value;
  // Saved-to-saved review requires two explicit saved endpoints. If the user
  // starts from Imported, use the oldest listed checkpoint and show that choice.
  if (comparisonAfterId && !comparisonId) {
    comparisonId = state.project?.checkpoints?.slice(0, 50).at(-1)?.id || '';
    $('checkpoint-select').value = comparisonId;
  }
  setBusy(true, 'Comparing checkpoints…');
  try {
    await loadComparison();
    status(state.recovery_required ? 'Checkpoint selected. Restore its saved positions to recover the workspace.' : reviewing() ? 'Read-only checkpoint comparison ready. Working changes are kept. Choose Diff to overlay both versions.' : comparisonId ? 'Checkpoint comparison ready. Choose Diff to see both positions.' : 'Comparing with imported positions.');
  } catch (error) {
    comparisonId = ''; comparisonAfterId = ''; comparison = null; viewer?.setComparison?.(null); renderHistory();
    if (error.status === 409) {
      try { acceptState(await request('/api/state')); }
      catch { ready = false; $('retry-button').hidden = false; }
    }
    status(`Could not compare checkpoint. ${error.message}`, true);
  } finally { select(selectedId); setBusy(false); }
}
$('checkpoint-select').addEventListener('change', () => {
  // Choosing Imported explicitly returns to the working assembly.
  if (!$('checkpoint-select').value) $('comparison-after-select').value = '';
  void changeComparison();
});
$('comparison-after-select').addEventListener('change', () => void changeComparison());
$('return-working').addEventListener('click', () => {
  if (busy) return;
  $('comparison-after-select').value = '';
  void changeComparison();
});
$('restore-button').addEventListener('click', async () => {
  const checkpoint_id = comparisonId;
  if (!checkpoint_id || reviewing()) return;
  const replacement = await confirmReplacement();
  if (!replacement) return;
  await mutate('/api/restore', { body: JSON.stringify({ revision: state.revision, expected_head: state.project?.head, checkpoint_id, ...replacement }), headers: { 'Content-Type': 'application/json' } }, 'Restoring checkpoint positions…', 'Positions restored into the working assembly. Save a checkpoint to keep this as a new version.');
});
async function openProject(project_id) {
  if (!project_id) return;
  const replacement = await confirmReplacement();
  if (!replacement) { $('project-select').value = state.project?.id || ''; return; }
  await mutate('/api/project/open', { body: JSON.stringify({ revision: state.revision, project_id, ...replacement }), headers: { 'Content-Type': 'application/json' } }, 'Opening saved project…', 'Saved project opened at its latest checkpoint.');
}
$('project-select').addEventListener('change', () => void openProject($('project-select').value));
$('reload-project').addEventListener('click', () => void openProject(state.project?.id));
void loadState();
