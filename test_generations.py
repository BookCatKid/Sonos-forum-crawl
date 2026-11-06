import tempfile,unittest
from pathlib import Path
from generations import publish,restore,RemoteError
import test_checkpoint
class MemoryTransport:
 def __init__(self):self.store={};self.fail_at=None
 def exists(self,g):return g in self.store
 def generations(self):return list(self.store)
 def create(self,g):self.store[g]={}
 def put(self,g,k,b):
  if k==self.fail_at:raise RemoteError('fixture upload interruption')
  self.store[g][k]=b
 def get(self,g,k):return self.store[g][k]
class GenerationsTests(unittest.TestCase):
 def test_remote_roundtrip_exact_and_committed_last(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_checkpoint.CheckpointTests().seed(p/'run');r=MemoryTransport();publish(p/'run','gen1',r);self.assertEqual(list(r.store['gen1'])[-1],'COMMITTED');restore('gen1',r,p/'restored');self.assertTrue((p/'restored/api.sqlite').is_file())
 def test_interrupted_publish_keeps_old_generation_and_no_new_commit(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_checkpoint.CheckpointTests().seed(p/'run');r=MemoryTransport();publish(p/'run','good',r);r.fail_at='manifest.json'
   with self.assertRaises(RemoteError):publish(p/'run','bad',r)
   self.assertNotIn('COMMITTED',r.store['bad']);restore('good',r,p/'restored')
   with self.assertRaises(KeyError):restore('bad',r,p/'uncommitted')
   self.assertFalse((p/'uncommitted').exists())
 def test_existing_generation_cannot_be_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_checkpoint.CheckpointTests().seed(p/'run');r=MemoryTransport();publish(p/'run','good',r)
   with self.assertRaises(RemoteError):publish(p/'run','good',r)
 def test_missing_or_corrupted_remote_fails_without_empty_fallback(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_checkpoint.CheckpointTests().seed(p/'run');r=MemoryTransport();publish(p/'run','good',r);r.store['good']['api.sqlite']=b'bad'
   with self.assertRaises(ValueError):restore('good',r,p/'restored')
   self.assertFalse((p/'restored').exists())
if __name__=='__main__':unittest.main()
