import tempfile,unittest
from pathlib import Path
from public_job import resume_once,stop_code
from test_generations import MemoryTransport
from packed_generation import publish
from automated_batch import BackupRequired
import test_public_store
class PublicJobTests(unittest.TestCase):
 def seed(self,p,r):test_public_store.PublicStoreTests().seed(p);publish(p,'base',r)
 def test_resume_from_verified_generation_then_commits(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);r=MemoryTransport();self.seed(p/'seed',r);calls=[]
   def run(*a,**k):calls.append(k);return {'captured_this_batch':0,'phase':'listing','reason':'fixture'}
   result=resume_once(r,'base','next',p/'run',run=run);self.assertEqual(result['committed_generation'],'next');self.assertIn('COMMITTED',r.store['next']);self.assertEqual(calls[0]['store_factory'].__name__,'PublicStore')
 def test_missing_baseline_never_fetches_or_starts_empty(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);calls=[]
   with self.assertRaises(Exception):resume_once(MemoryTransport(),'absent','next',p/'run',run=lambda *a,**k:calls.append(1))
   self.assertFalse((p/'run').exists());self.assertEqual(calls,[])
 def test_post_batch_failure_latches(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);r=MemoryTransport();self.seed(p/'seed',r)
   def run(*a,**k):r.fail_at='index.json';return {'captured_this_batch':0,'phase':'listing','reason':'fixture'}
   with self.assertRaises(BackupRequired):resume_once(r,'base','next',p/'run',run=run)
   self.assertTrue((p/'run/BACKUP_FAILED').exists());self.assertNotIn('COMMITTED',r.store['next'])
 def test_error_strings_reduce_to_reviewed_codes(self):
  self.assertEqual(stop_code(ValueError('arbitrary server text')),'unexpected-response');self.assertEqual(stop_code(TimeoutError('details')),'timeout')
if __name__=='__main__':unittest.main()
