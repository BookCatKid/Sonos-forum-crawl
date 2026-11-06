import unittest,json,hashlib
from automation_state import current_generation
from test_generations import MemoryTransport
from generations import RemoteError
class StateTests(unittest.TestCase):
 def add(self,r,g,parent=None):
  r.create(g);raw=b'{}';r.put(g,'index.json',raw);r.put(g,'COMMITTED',json.dumps({'index_sha256':hashlib.sha256(raw).hexdigest()}).encode())
  if parent:r.put(g,'ATTEMPT.json',json.dumps({'version':1,'source':parent,'generation':g}).encode())
 def test_selects_tip_not_synthetic_or_sorted_name(self):
  r=MemoryTransport();self.add(r,'checkpoint-synthetic-z');self.add(r,'checkpoint-real-baseline');self.add(r,'checkpoint-a','checkpoint-real-baseline');self.add(r,'checkpoint-b','checkpoint-a');self.assertEqual(current_generation(r),'checkpoint-b')
 def test_incomplete_stops_instead_of_fallback(self):
  r=MemoryTransport();self.add(r,'checkpoint-base');r.create('checkpoint-bad')
  with self.assertRaises(RemoteError):current_generation(r)
 def test_branches_stop(self):
  r=MemoryTransport();self.add(r,'checkpoint-base');self.add(r,'checkpoint-a','checkpoint-base');self.add(r,'checkpoint-b','checkpoint-base')
  with self.assertRaises(RemoteError):current_generation(r)
 def test_multiple_roots_stop(self):
  r=MemoryTransport();self.add(r,'checkpoint-base');self.add(r,'checkpoint-other')
  with self.assertRaises(RemoteError):current_generation(r)
if __name__=='__main__':unittest.main()
