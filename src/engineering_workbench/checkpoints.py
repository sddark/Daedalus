"""Small integration adapter; no HTTP routes, UI replacement or automatic saves."""
from __future__ import annotations

from .history import HistoryError
from .placements import make_state, validate_import


def save_session_checkpoint(session, store, *, revision, expected_head, message, request_id, replay_only=False):
    """Save the authoritative session under its existing lock and revision guard.

    Call from a CSRF-protected mutation route. The store rejects stale Git heads;
    this adapter also rejects a stale displayed editing revision, even if no one
    has created a checkpoint since the last movement.
    """
    with session.lock:
        session._check_revision(revision)
        if session.assembly is None or session.model is None:
            raise HistoryError("Import an assembly before creating a checkpoint")
        if session.recovery_required:
            raise HistoryError("Recover the document before creating a checkpoint")
        session.assembly._check_source()
        manifest = store.manifest
        model = session.model
        if model['source_sha256'] != manifest['source_sha256']:
            raise HistoryError("Active session is not this project's source")
        occurrences = model['occurrences']
        if len(occurrences) != len(manifest['occurrences']):
            raise HistoryError("Active occurrence identities changed")
        for item in occurrences:
            imported = manifest['occurrences'].get(item['id'])
            if imported is None or item['path'] != imported['path'] or item['baseline_matrix_3x4'] != imported['matrix_3x4']:
                raise HistoryError("Active session does not match the project's imported baseline")
        offsets = {item['id']: item['offset_mm'] for item in occurrences}
        if len(offsets) != len(occurrences):
            raise HistoryError("Duplicate active occurrence identity")
        state = make_state(manifest, offsets)
        return store.save(state, expected_head=expected_head, message=message, request_id=request_id, replay_only=replay_only)


def load_checkpoint_assembly(store, checkpoint_id, kernel_version):
    """Reconstruct a candidate without changing or replacing an active session.

    Caller must hold the shared OCCT/kernel lock. Validate/tessellate this candidate
    before swapping it into ViewerSession. Import baseline remains unchanged.
    """
    from .cad import StepAssembly
    state = store.read(checkpoint_id)
    manifest = store.manifest
    candidate = StepAssembly.load(store.source_path)
    validate_import(manifest, candidate.snapshot(), kernel_version)
    for occurrence_id, offset in state['offsets'].items():
        candidate.translate_occurrence(occurrence_id, offset)
    snapshot = candidate.snapshot()
    for item in snapshot['occurrences']:
        item['baseline_matrix_3x4'] = manifest['occurrences'][item['id']]['matrix_3x4']
        item['offset_mm'] = state['offsets'][item['id']]
    return candidate, snapshot
