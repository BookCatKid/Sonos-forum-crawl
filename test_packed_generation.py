import tempfile,unittest
from pathlib import Path
from packed_generation import publish,restore
from test_generations import MemoryTransport
import test_public_store
from generations import RemoteError
class PackedTests(unittest.TestCase):
 def test_roundtrip_many_chunks_and_restore(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_public_store.PublicStoreTests().seed(p/'run');r=MemoryTransport();publish(p/'run','a',r,1024);self.assertEqual(list(r.store['a'])[-1],'COMMITTED');self.assertGreater(len(r.store['a']),3);restore('a',r,p/'restored');self.assertTrue((p/'restored/api.sqlite').exists())
 def test_corrupt_chunk_never_restores_empty(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_public_store.PublicStoreTests().seed(p/'run');r=MemoryTransport();publish(p/'run','a',r);r.store['a']['checkpoint.part-0000']=b'bad'
   with self.assertRaises(RemoteError):restore('a',r,p/'restored')
   self.assertFalse((p/'restored').exists())
 def test_interruption_preserves_previous(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);test_public_store.PublicStoreTests().seed(p/'run');r=MemoryTransport();publish(p/'run','good',r);r.fail_at='index.json'
   with self.assertRaises(RemoteError):publish(p/'run','bad',r)
   self.assertNotIn('COMMITTED',r.store['bad']);restore('good',r,p/'restored')
 def test_raw_input_stops_before_create(self):
  from test_checkpoint import CheckpointTests
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);CheckpointTests().seed(p/'run');r=MemoryTransport()
   with self.assertRaises(Exception):publish(p/'run','raw',r)
   self.assertEqual(r.store,{})
if __name__=='__main__':unittest.main()
