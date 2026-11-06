import tempfile,unittest
from pathlib import Path
from attempt_guard import reserve
from generations import RemoteError
from packed_generation import publish
from test_generations import MemoryTransport
import test_public_store
class AttemptTests(unittest.TestCase):
 def seed(self,p,r):test_public_store.PublicStoreTests().seed(p);publish(p,'checkpoint-base',r)
 def test_crash_after_reservation_blocks_later_attempt(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();self.seed(Path(d)/'run',r);reserve(r,'checkpoint-base','checkpoint-next')
   with self.assertRaises(RemoteError):reserve(r,'checkpoint-base','checkpoint-third')
   self.assertNotIn('checkpoint-third',r.store)
 def test_reserved_publish_commits_then_future_allowed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'run';r=MemoryTransport();self.seed(p,r);reserved=reserve(r,'checkpoint-base','checkpoint-next');publish(p,'checkpoint-next',reserved);
   with self.assertRaises(RemoteError):reserve(r,'checkpoint-base','checkpoint-stale')
   reserve(r,'checkpoint-next','checkpoint-third');self.assertIn('ATTEMPT.json',r.store['checkpoint-third'])
 def test_partial_release_without_attempt_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();self.seed(Path(d)/'run',r);r.create('checkpoint-crashed')
   with self.assertRaises(RemoteError):reserve(r,'checkpoint-base','checkpoint-next')
 def test_ancestry_transport_error_is_not_treated_as_missing(self):
  with tempfile.TemporaryDirectory() as d:
   r=MemoryTransport();self.seed(Path(d)/'run',r);old=r.get
   def get(g,k):
    if k=='ATTEMPT.json':raise RemoteError('network')
    return old(g,k)
   r.get=get
   with self.assertRaises(RemoteError):reserve(r,'checkpoint-base','checkpoint-next')
   self.assertNotIn('checkpoint-next',r.store)
if __name__=='__main__':unittest.main()
