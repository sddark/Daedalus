from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from engineering_workbench.cad import StepAssembly, PlacementRecoveryError, create_two_instance_fixture, file_sha256
from engineering_workbench.checkpoints import save_session_checkpoint, load_checkpoint_assembly
from engineering_workbench.history import CheckpointStore
from engineering_workbench.placements import compare, make_state, validate_import
from engineering_workbench.viewer import ViewerSession, RequestError


class CheckpointCADTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)/'original.step'
        create_two_instance_fixture(self.source)
        self.assembly = StepAssembly.load(self.source)

    def test_real_source_checkpoint_reopen_move_restore_and_export_roundtrip(self):
        before = self.assembly.snapshot()
        original_hash = file_sha256(self.source)
        store = CheckpointStore.create(Path(self.temp.name)/'project',self.source.read_bytes(),before,'test-runtime')
        occurrence = next(item for item in before['occurrences'] if item['name']=='Movable')
        state = make_state(store.manifest); state['offsets'][occurrence['id']] = [12,-3,2]
        saved = store.save(state,expected_head=store.head(),message='Move one box',request_id=str(uuid4()))
        reopened_store = CheckpointStore(store.path)
        reopened = StepAssembly.load(reopened_store.source_path)
        validate_import(store.manifest,reopened.snapshot(),'test-runtime')
        for key,offset in reopened_store.read(saved.checkpoint_id)['offsets'].items():
            reopened.translate_occurrence_checked(key,offset)
        after = reopened.snapshot()
        moved = next(item for item in after['occurrences'] if item['id']==occurrence['id'])
        self.assertEqual(moved['translation_mm'],[42,-3,2])
        report = compare(store.manifest,make_state(store.manifest),state)
        self.assertEqual(len(report['changes']),1)
        self.assertEqual(report['changes'][0]['delta_mm'],[12,-3,2])
        export = Path(self.temp.name)/'export.step'; reopened.export_step(export)
        exported = {item['name']:item for item in StepAssembly.load(export).snapshot()['occurrences']}
        self.assertEqual(exported['Movable']['translation_mm'],[42,-3,2])
        self.assertEqual(exported['Fixed']['translation_mm'],[0,0,0])
        self.assertAlmostEqual(exported['Movable']['volume_mm3'],6000)
        self.assertEqual(len({item['definition_id'] for item in exported.values()}),1)
        self.assertEqual(file_sha256(self.source),original_hash)
        self.assertEqual(file_sha256(store.source_path),original_hash)

    def test_session_adapter_rejects_stale_display_revision_even_with_current_git_head(self):
        session=ViewerSession(); self.addCleanup(session.close)
        imported=session.import_step(self.source.read_bytes(),'fixture.step')
        store=CheckpointStore.create(Path(self.temp.name)/'project',self.source.read_bytes(),
                                     session.assembly.snapshot(),'test-runtime')
        head=store.head(); item=imported['model']['occurrences'][1]
        latest=session.move({'revision':imported['revision'],'occurrence_id':item['id'],'offset_mm':[9,0,0]})
        with self.assertRaises(RequestError):
            save_session_checkpoint(session,store,revision=imported['revision'],expected_head=head,
                                    message='Stale display',request_id=str(uuid4()))
        self.assertEqual(store.head(),head)
        result=save_session_checkpoint(session,store,revision=latest['revision'],expected_head=head,
                                       message='Current display',request_id=str(uuid4()))
        candidate,model=load_checkpoint_assembly(store,result.checkpoint_id,'test-runtime')
        restored=next(x for x in model['occurrences'] if x['id']==item['id'])
        self.assertEqual(restored['offset_mm'],[9,0,0])
        self.assertEqual(restored['baseline_matrix_3x4'],item['baseline_matrix_3x4'])
        self.assertEqual(session.revision,latest['revision'])

    def test_replacing_restored_candidate_never_deletes_durable_project(self):
        store=CheckpointStore.create(Path(self.temp.name)/'project',self.source.read_bytes(),
                                     self.assembly.snapshot(),'test-runtime')
        candidate,model=load_checkpoint_assembly(store,store.head(),'test-runtime')
        session=ViewerSession(); self.addCleanup(session.close)
        session.assembly,session.model=candidate,model
        session.import_step(self.source.read_bytes(),'replacement.step',session.revision)
        self.assertTrue(store.path.is_dir())
        self.assertEqual(CheckpointStore(store.path).read(),make_state(store.manifest))
        session.close()
        self.assertTrue(store.source_path.exists())
        self.assertTrue(store.repository.is_dir())

    def test_snapshot_failure_restores_exact_native_transform(self):
        before = self.assembly.snapshot(); item = before['occurrences'][1]
        with patch.object(self.assembly,'snapshot',side_effect=RuntimeError('Injected snapshot failure')):
            with self.assertRaisesRegex(RuntimeError,'Injected snapshot failure'):
                self.assembly.translate_occurrence_checked(item['id'],[12,3,-5])
        self.assertEqual(self.assembly.snapshot(),before)

    def test_session_snapshot_failure_keeps_model_revision_and_kernel_consistent(self):
        session = ViewerSession(); self.addCleanup(session.close)
        state = session.import_step(self.source.read_bytes(),'fixture.step')
        before = deepcopy(session.model)
        item = state['model']['occurrences'][1]
        with patch.object(session.assembly,'snapshot',side_effect=RuntimeError('Injected failure')):
            with self.assertRaises(RuntimeError):
                session.move({'revision':state['revision'],'occurrence_id':item['id'],'offset_mm':[10,0,0]})
        self.assertEqual(session.model,before)
        self.assertEqual(session.revision,state['revision'])
        self.assertFalse(session.recovery_required)
        self.assertEqual(session.assembly.snapshot()['occurrences'][1]['matrix_3x4'],item['matrix_3x4'])

    def test_failed_native_recovery_blocks_further_edit_and_export(self):
        session = ViewerSession(); self.addCleanup(session.close)
        state = session.import_step(self.source.read_bytes(),'fixture.step')
        item=state['model']['occurrences'][1]
        real_tool=session.assembly.shape_tool
        class FailingRollback:
            calls=0
            def __getattr__(self,name): return getattr(real_tool,name)
            def UpdateAssemblies(self):
                self.calls+=1
                if self.calls>1: raise RuntimeError('Injected rollback failure')
                return real_tool.UpdateAssemblies()
        session.assembly.shape_tool=FailingRollback()
        with patch.object(session.assembly,'snapshot',side_effect=RuntimeError('Injected snapshot failure')):
            with self.assertRaises(PlacementRecoveryError):
                session.move({'revision':state['revision'],'occurrence_id':item['id'],'offset_mm':[10,0,0]})
        self.assertTrue(session.state()['recovery_required'])
        with self.assertRaises(RequestError): session.export_step()
        with self.assertRaises(RequestError): session.move({})
        recovered=session.import_step(self.source.read_bytes(),'fixture.step',session.revision)
        self.assertFalse(recovered['recovery_required'])
        self.assertTrue(session.export_step())


if __name__=='__main__': unittest.main()
