import tempfile,unittest,json
from pathlib import Path
from collect_sample import sample
class Tests(unittest.TestCase):
 def test_rejects_unevidenced_category_without_network(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'plan';p.write_text('{"categories":[]}')
   with self.assertRaises(ValueError):sample(Path(d)/'db',p,'en.community.sonos.com',9,1)
 def test_rejects_capped_leaf_without_verified_partition(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'plan';p.write_text(json.dumps({'categories':[{'host':'en.community.sonos.com','id':9,'container':False,'needs_subpartition':True}]}))
   with self.assertRaises(ValueError):sample(Path(d)/'db',p,'en.community.sonos.com',9,1)
