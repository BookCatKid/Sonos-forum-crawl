"""Real local checkpoint/lineage recovery, synthetic data, zero network."""
import unittest,tempfile,json
from pathlib import Path
import test_unattended
from test_generations import MemoryTransport
from packed_generation import publish,restore
from recovery import recover_terminated
from automation_state import current_generation
from attempt_guard import reserve
from public_store import check_public
class RecoveryRoundtripTests(unittest.TestCase):
 def test_lost_hosted_attempt_retained_then_new_generation_fresh_restores(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);root=p/'root';root.mkdir();test_unattended.UnattendedTests().setup_state(root);remote=MemoryTransport();publish(root,'checkpoint-base',remote)
   lost='checkpoint-20261106-180000-123';reserve(remote,'checkpoint-base',lost)
   remote.put(lost,'checkpoint.part-0000',b'incomplete uncommitted bytes')
   remote.workflow_run=lambda i:{'id':i,'status':'completed','event':'workflow_dispatch','path':'.github/workflows/capture.yml'}
   gaps=recover_terminated(remote);self.assertEqual(gaps[0]['generation'],lost);self.assertEqual(remote.get(lost,'checkpoint.part-0000'),b'incomplete uncommitted bytes');self.assertEqual(current_generation(remote),'checkpoint-base')
   restored=p/'restored';restore('checkpoint-base',remote,restored);check_public(restored)
   nextgen='checkpoint-20261106-183000-124';reserved=reserve(remote,'checkpoint-base',nextgen);publish(restored,nextgen,reserved);self.assertEqual(current_generation(remote),nextgen)
   fresh=p/'fresh';restore(nextgen,remote,fresh);self.assertEqual(check_public(fresh)['receipts_checked'],1)
