import json,unittest
from unittest.mock import patch
from recovery import recover_terminated,abandoned
from automation_state import current_generation
from generations import RemoteError
from test_generations import MemoryTransport
class RecoveryTests(unittest.TestCase):
 def remote(self,status='completed',path='.github/workflows/capture.yml'):
  r=MemoryTransport();r.store['checkpoint-base']={'index.json':b'{}','COMMITTED':json.dumps({'index_sha256':__import__('hashlib').sha256(b'{}').hexdigest()}).encode()};g='checkpoint-20261106-180000-123';r.store[g]={'ATTEMPT.json':json.dumps({'version':1,'source':'checkpoint-base','generation':g}).encode()};r.workflow_run=lambda n:{'id':n,'status':status,'event':'schedule','path':path};return r,g
 def test_terminated_attempt_abandoned_without_delete_and_tip_parent(self):
  r,g=self.remote()
  with patch('packed_generation.restore') as restore:gaps=recover_terminated(r)
  self.assertEqual(len(gaps),1);self.assertIn('ATTEMPT.json',r.store[g]);self.assertIn('ABANDONED.json',r.store[g]);self.assertEqual(current_generation(r),'checkpoint-base');self.assertEqual(len(recover_terminated(r)),1)
 def test_active_run_never_abandoned(self):
  r,g=self.remote('in_progress')
  with self.assertRaises(RemoteError):recover_terminated(r)
  self.assertNotIn('ABANDONED.json',r.store[g])
 def test_wrong_workflow_never_abandoned(self):
  r,g=self.remote(path='.github/workflows/other.yml')
  with self.assertRaises(RemoteError):recover_terminated(r)
 def test_parent_restore_failure_preserves_block(self):
  r,g=self.remote()
  with patch('packed_generation.restore',side_effect=RemoteError('bad')):
   with self.assertRaises(RemoteError):recover_terminated(r)
  self.assertNotIn('ABANDONED.json',r.store[g])
 def test_manual_partial_never_abandoned(self):
  r,g=self.remote();r.store['checkpoint-manual']=r.store.pop(g)
  with self.assertRaises(RemoteError):recover_terminated(r)
 def test_malformed_abandonment_stops(self):
  r,g=self.remote();r.store[g]['ABANDONED.json']=b'{}'
  with self.assertRaises(RemoteError):current_generation(r)
