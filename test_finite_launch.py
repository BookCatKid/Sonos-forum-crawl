import tempfile,unittest
from pathlib import Path
from finite_launch import first_batch
from test_generations import MemoryTransport
from generations import RemoteError
class LaunchTests(unittest.TestCase):
 def test_baseline_reservation_and_final_commit_before_end(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();calls=[]
   def run(root,n,**kw):
    self.assertIn('COMMITTED',r.store['checkpoint-first-baseline']);self.assertIn('ATTEMPT.json',r.store['checkpoint-first']);calls.append(n);return {'captured_this_batch':0,'phase':'listing','reason':'fixture'}
   out=first_batch(r,'checkpoint-first',Path(d)/'job',run=run);self.assertEqual(calls,[15]);self.assertIn('COMMITTED',r.store['checkpoint-first']);self.assertEqual(out['committed_generation'],'checkpoint-first')
 def test_baseline_failure_sends_no_forum_reads(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();r.fail_at='index.json';calls=[]
   with self.assertRaises(RemoteError):first_batch(r,'checkpoint-first',Path(d)/'job',run=lambda *a,**k:calls.append(1))
   self.assertEqual(calls,[])
 def test_partial_prior_attempt_blocks_reads(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();r.create('checkpoint-incomplete');calls=[]
   with self.assertRaises((RemoteError,KeyError)):first_batch(r,'checkpoint-first',Path(d)/'job',run=lambda *a,**k:calls.append(1))
   self.assertEqual(calls,[])
if __name__=='__main__':unittest.main()
