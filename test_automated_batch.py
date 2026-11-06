import tempfile,json,unittest
from pathlib import Path
from automated_batch import one_batch,BackupRequired
from test_generations import MemoryTransport
import test_checkpoint
class AutomatedTests(unittest.TestCase):
 def seed(self,p):
  test_checkpoint.CheckpointTests().seed(p)
  (p/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes())
 def test_absent_scope_fails_before_baseline_or_fetch(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_checkpoint.CheckpointTests().seed(p);(p/'scope.json').unlink();r=MemoryTransport();calls=[]
   with self.assertRaises(FileNotFoundError):one_batch(p,r,'g',run=lambda *a,**k:calls.append(1))
   self.assertEqual(calls,[]);self.assertEqual(r.store,{})
 def test_baseline_failure_prevents_fetch(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);r=MemoryTransport();r.fail_at='api.sqlite';calls=[]
   with self.assertRaises(Exception):one_batch(p,r,'g',run=lambda *a,**k:calls.append(1))
   self.assertEqual(calls,[])
 def test_unreviewed_faster_or_larger_batch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);r=MemoryTransport()
   with self.assertRaises(ValueError):one_batch(p,r,'g',requests=16)
 def test_both_generations_committed_and_rate_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);r=MemoryTransport();calls=[]
   one_batch(p,r,'g',run=lambda *a,**k:calls.append(k) or {'captured_this_batch':0})
   self.assertIn('COMMITTED',r.store['g-before']);self.assertIn('COMMITTED',r.store['g-after']);self.assertEqual(calls[0]['delay'],1)
 def test_post_batch_backup_failure_is_loud(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);r=MemoryTransport()
   def run(*a,**k):r.fail_at='api.sqlite';return {'captured_this_batch':1}
   with self.assertRaises(BackupRequired):one_batch(p,r,'g',run=run)
   self.assertIn('COMMITTED',r.store['g-before']);self.assertNotIn('COMMITTED',r.store['g-after'])
   r.fail_at=None
   with self.assertRaises(BackupRequired):one_batch(p,r,'next',run=run)
   self.assertNotIn('next-before',r.store)
if __name__=='__main__':unittest.main()
