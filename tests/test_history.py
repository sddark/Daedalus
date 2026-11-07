from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from engineering_workbench.history import CheckpointStore, HistoryError, StaleCheckpoint, UncertainCheckpoint, HEAD
from engineering_workbench.placements import (StateError, canonical, compare, make_manifest,
                                            make_state, validate_import, validate_state)

SOURCE = b'ISO-10303-21; storage-only fixture; END-ISO-10303-21;'
ID_A = '01234567-1234-4234-8234-123456789abc'
ID_B = '01234567-1234-4234-8234-123456789abd'
PROJECT = '01234567-1234-4234-8234-123456789abe'


def snapshot():
    return {'source_sha256': hashlib.sha256(SOURCE).hexdigest(), 'units': 'mm', 'occurrences': [
        {'id': ID_A, 'path': '0:1/0:1:1', 'name': 'Same name', 'definition_id': '0:2',
         'matrix_3x4': [[1,0,0,0],[0,1,0,0],[0,0,1,0]]},
        {'id': ID_B, 'path': '0:1/0:1:2', 'name': 'Same name', 'definition_id': '0:2',
         'matrix_3x4': [[0,-1,0,30],[1,0,0,5],[0,0,1,7]]}]}


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.manifest = make_manifest(PROJECT, snapshot(), '8.0.1')
        self.state = make_state(self.manifest)

    def test_canonical_order_negative_zero_and_integer_normalization(self):
        other = deepcopy(self.state)
        other['offsets'] = {ID_B: [0,-0.0,0], ID_A: [-0.0,0,0]}
        self.assertEqual(canonical(self.state), canonical(validate_state(self.manifest, other)))

    def test_comparison_is_per_occurrence_with_parent_axes_and_preserved_rotation(self):
        after = deepcopy(self.state); after['offsets'][ID_B] = [3,4,0]
        report = compare(self.manifest, self.state, after)
        self.assertEqual(report['unchanged_count'], 1)
        item = report['changes'][0]
        self.assertEqual(item['id'], ID_B)
        self.assertEqual(item['delta_mm'], [3,4,0]); self.assertEqual(item['distance_mm'], 5)
        self.assertEqual(item['after_matrix_3x4'], [[0,-1,0,33],[1,0,0,9],[0,0,1,7]])
        self.assertEqual(compare(self.manifest, after, self.state)['changes'][0]['delta_mm'], [-3,-4,0])
        self.assertEqual(self.state['offsets'][ID_B], [0,0,0])

    def test_tolerance_does_not_drop_saved_intent(self):
        after = deepcopy(self.state); after['offsets'][ID_A] = [1e-10,0,0]
        self.assertEqual(compare(self.manifest, self.state, after)['changes'], [])
        self.assertNotEqual(canonical(self.state), canonical(validate_state(self.manifest, after)))
        self.assertEqual(len(compare(self.manifest, self.state, after, tolerance_mm=0)['changes']), 1)

    def test_invalid_offsets_and_identity_are_rejected(self):
        for vector in ([True,0,0], [float('nan'),0,0], [float('inf'),0,0], [1000001,0,0], [1,2], '123'):
            with self.subTest(vector=vector):
                state = deepcopy(self.state); state['offsets'][ID_A] = vector
                with self.assertRaises(StateError): validate_state(self.manifest, state)
        for field,value in (('units','inch'), ('project_id',str(uuid4())), ('source_sha256','0'*64), ('schema_version',True)):
            state = deepcopy(self.state); state[field] = value
            with self.assertRaises(StateError): validate_state(self.manifest, state)
        state = deepcopy(self.state); state['offsets'].pop(ID_A)
        with self.assertRaises(StateError): validate_state(self.manifest, state)
        state = deepcopy(self.state); state['offsets'][str(uuid4())] = [0,0,0]
        with self.assertRaises(StateError): validate_state(self.manifest, state)

    def test_reimport_must_match_source_ids_kernel_and_imported_transform(self):
        validate_import(self.manifest, snapshot(), '8.0.1')
        for mutate in (lambda s: s.update(source_sha256='0'*64),
                       lambda s: s['occurrences'][1]['matrix_3x4'][0].__setitem__(3,31),
                       lambda s: s['occurrences'][0].update(id=str(uuid4()))):
            candidate = snapshot(); mutate(candidate)
            with self.assertRaises(StateError): validate_import(self.manifest, candidate, '8.0.1')
        with self.assertRaises(StateError): validate_import(self.manifest, snapshot(), 'different')

    def test_duplicate_id_path_and_bad_matrix_rejected(self):
        for field in ('id','path'):
            candidate = snapshot(); candidate['occurrences'][1][field] = candidate['occurrences'][0][field]
            with self.assertRaises(StateError): make_manifest(PROJECT,candidate,'8')
        candidate = snapshot(); candidate['occurrences'][0]['matrix_3x4'][0][0] = True
        with self.assertRaises(StateError): make_manifest(PROJECT,candidate,'8')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'project'
        self.store = CheckpointStore.create(self.path, SOURCE, snapshot(), '8.0.1')
        self.initial = self.store.head()

    def state(self, x=10):
        state = self.store.read(); state['offsets'][ID_B] = [x,0,0]; return state

    def save(self, x=10, **kwargs):
        options = dict(expected_head=self.store.head(), message='Move occurrence', request_id=str(uuid4()))
        options.update(kwargs)
        return self.store.save(self.state(x), **options)

    def test_save_reopen_read_and_restore_as_descendant(self):
        result = self.save()
        reopened = CheckpointStore(self.path)
        self.assertEqual(reopened.read()['offsets'][ID_B], [10,0,0])
        original = reopened.read(self.initial)
        restored = reopened.save(original, expected_head=result.head_id, message='Restore import', request_id=str(uuid4()))
        self.assertEqual(reopened.read()['offsets'][ID_B], [0,0,0])
        self.assertEqual(reopened.history()['checkpoints'][0]['parent'], result.head_id)
        self.assertEqual(len(reopened.history()['checkpoints']),3)
        self.assertEqual(self.store.source_path.read_bytes(), SOURCE)
        source_oids = {reopened._tree(item['id'])['source.step'] for item in reopened.history()['checkpoints']}
        self.assertEqual(len(source_oids),1)
        self.assertTrue(restored.created)

    def test_noop_does_not_create_commit_but_tiny_difference_does(self):
        result = self.save(0)
        self.assertFalse(result.created); self.assertEqual(result.checkpoint_id,self.initial)
        result = self.save(1e-10)
        self.assertTrue(result.created)

    def test_noop_retry_is_durable_and_request_id_cannot_be_reused(self):
        request_id=str(uuid4()); original=self.store.read()
        result=self.store.save(original,expected_head=self.initial,message='No changes',request_id=request_id)
        self.assertFalse(result.created)
        self.save(12)
        reopened=CheckpointStore(self.path)
        retry=reopened.save(original,expected_head=self.initial,message='No changes',request_id=request_id)
        self.assertTrue(retry.replayed)
        self.assertEqual(retry.checkpoint_id,self.initial)
        self.assertEqual(len(reopened.history()['checkpoints']),2)
        with self.assertRaises(HistoryError):
            reopened.save(original,expected_head=self.initial,message='Changed request',request_id=request_id)

    def test_stale_save_is_rejected_without_overwrite(self):
        winner = self.save(5)
        with self.assertRaises(StaleCheckpoint): self.save(10, expected_head=self.initial)
        self.assertEqual(self.store.head(), winner.head_id)
        self.assertEqual(self.store.read()['offsets'][ID_B], [5,0,0])

    def test_two_store_instances_have_one_concurrent_winner(self):
        other = CheckpointStore(self.path)
        def save(store,x):
            state = make_state(store.manifest); state['offsets'][ID_B]=[x,0,0]
            try:
                return store.save(state,expected_head=self.initial,message='Race',request_id=str(uuid4()))
            except StaleCheckpoint: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda args: save(*args), [(self.store,5),(other,8)]))
        self.assertEqual(sum(item is not None for item in outcomes),1)
        self.assertEqual(len(self.store.history()['checkpoints']),2)

    def test_retry_token_reconciles_lost_response_even_after_later_save(self):
        request_id = str(uuid4()); first_state = self.state(4)
        first = self.store.save(first_state,expected_head=self.initial,message='Move',request_id=request_id)
        self.save(7)
        retry = self.store.save(first_state,expected_head=self.initial,message='Move',request_id=request_id)
        self.assertTrue(retry.replayed); self.assertFalse(retry.created)
        self.assertEqual(retry.checkpoint_id,first.checkpoint_id)
        self.assertEqual(len(self.store.history()['checkpoints']),3)
        with self.assertRaises(HistoryError):
            self.store.save(first_state,expected_head=self.initial,message='Different',request_id=request_id)

    def test_failure_before_ref_transaction_preserves_head(self):
        with patch.object(self.store,'_update_refs',side_effect=HistoryError('Injected write failure')):
            with self.assertRaisesRegex(HistoryError,'not saved'): self.save()
        self.assertEqual(self.store.head(),self.initial)
        self.assertEqual(len(self.store.history()['checkpoints']),1)

    def test_commit_before_lost_ref_response_is_verified_as_success(self):
        original = self.store._update_refs
        def lost_response(updates):
            original(updates)
            raise HistoryError('Injected lost response')
        with patch.object(self.store,'_update_refs',side_effect=lost_response):
            result = self.save()
        self.assertTrue(result.created)
        self.assertEqual(self.store.head(),result.checkpoint_id)
        self.assertEqual(len(self.store.history()['checkpoints']),2)

    def test_failure_writing_objects_preserves_head(self):
        with patch.object(self.store,'_blob',side_effect=HistoryError('Disk full')):
            with self.assertRaises(HistoryError): self.save()
        self.assertEqual(self.store.head(),self.initial)

    def test_git_ref_transaction_is_atomic_if_one_ref_is_locked(self):
        request_id = str(uuid4())
        request_lock = self.store.repository / (self.store._request_ref(request_id) + '.lock')
        request_lock.parent.mkdir(parents=True,exist_ok=True)
        request_lock.write_text('busy')
        with self.assertRaises(HistoryError): self.save(request_id=request_id)
        self.assertEqual(self.store.head(),self.initial)
        self.assertIsNone(self.store._ref(self.store._request_ref(request_id)))

    def test_unverifiable_ref_failure_is_explicitly_uncertain(self):
        original_ref = self.store._ref
        unavailable = [False]
        def fail_update(updates):
            unavailable[0]=True
            raise HistoryError('Disconnected')
        def fail_verification(ref):
            if unavailable[0]: raise HistoryError('Cannot read')
            return original_ref(ref)
        with patch.object(self.store,'_update_refs',side_effect=fail_update), patch.object(self.store,'_ref',side_effect=fail_verification):
            with self.assertRaises(UncertainCheckpoint): self.save()
        self.assertEqual(self.store.head(),self.initial)

    def test_source_tamper_or_symlink_is_rejected(self):
        self.store.source_path.chmod(0o600); self.store.source_path.write_bytes(b'tampered')
        with self.assertRaises(HistoryError): self.save()
        with self.assertRaises(HistoryError): CheckpointStore(self.path)
        self.store.source_path.unlink(); other = Path(self.temp.name)/'outside'; other.write_bytes(SOURCE)
        self.store.source_path.symlink_to(other)
        with self.assertRaises(HistoryError): self.store.read()
        with self.assertRaises(HistoryError): CheckpointStore(self.path)

    def test_hooks_and_inherited_git_environment_do_not_execute(self):
        marker = self.path/'hook-ran'
        hook = self.store.repository/'hooks'/'reference-transaction'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n'); hook.chmod(0o700)
        with patch.dict(os.environ, {'GIT_DIR':'/invalid', 'GIT_WORK_TREE':'/invalid',
                                    'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'core.hooksPath',
                                    'GIT_CONFIG_VALUE_0':str(hook.parent)}):
            self.save()
        self.assertFalse(marker.exists())

    def test_bad_refs_messages_or_request_tokens_are_not_interpreted(self):
        for value in ('HEAD','HEAD~1','--help','../outside','0'*39,'z'*40):
            with self.assertRaises(HistoryError): self.store.read(value)
        with self.assertRaises(HistoryError): self.save(message='x\0y')
        with self.assertRaises(ValueError): self.save(request_id='../evil')
        self.assertEqual(self.store.head(),self.initial)

    def test_existing_directory_and_source_hash_mismatch_are_not_overwritten(self):
        with self.assertRaises(FileExistsError): CheckpointStore.create(self.path,SOURCE,snapshot(),'8')
        with self.assertRaises(HistoryError): CheckpointStore.create(Path(self.temp.name)/'bad',b'other',snapshot(),'8')
        self.assertFalse((Path(self.temp.name)/'bad').exists())
        self.assertEqual(self.store.source_path.read_bytes(),SOURCE)

    def test_history_pagination_and_bounds(self):
        self.save(1); self.save(2)
        page = self.store.history(limit=1,skip=1)
        self.assertEqual(len(page['checkpoints']),1)
        self.assertEqual(page['checkpoints'][0]['parent'],self.initial)
        for options in ({'limit':101},{'limit':True},{'skip':-1}):
            with self.assertRaises(HistoryError): self.store.history(**options)

    def test_unreachable_commit_and_changed_source_tree_are_rejected(self):
        state=self.state(1)
        request={'request_id':str(uuid4()),'parent':self.initial,'state_digest':'ignored','message':'Unreachable'}
        commit=self.store._make_commit(self.store._source_oid,self.store._manifest_oid,state,request)
        with self.assertRaises(HistoryError): self.store.read(commit)
        bad=self.store._make_commit(self.store._blob(b'changed source'),self.store._manifest_oid,state,request)
        self.store._update_refs(f'update {HEAD} {bad} {self.initial}\n')
        with self.assertRaises(HistoryError): self.store.read()
        with self.assertRaises(HistoryError): CheckpointStore(self.path)


if __name__ == '__main__': unittest.main()
