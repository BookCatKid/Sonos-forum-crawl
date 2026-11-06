import json,tempfile,unittest
from pathlib import Path
from build_discovery_plan import build
from api_adapter import APIStore
from reconcile_inventory import reconcile
class Tests(unittest.TestCase):
 def test_leaf_cap_and_parent_reconciliation(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'tree.json';p.write_text(json.dumps({'0':[{'id':1,'url':'https://en.community.sonos.com/root-1','isContainer':True,'visibleTopicsCount':10001,'children':[{'id':2,'url':'https://en.community.sonos.com/leaf-2','isContainer':False,'visibleTopicsCount':10001}]}]}));r=build([p]);self.assertEqual(r['tasks_started'],0);self.assertTrue(r['categories'][1]['needs_subpartition']);self.assertEqual(r['categories'][0]['count_delta'],0)
 def test_sitemap_not_treated_as_exhaustive(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(Path(d)/'db');m.db.execute('INSERT INTO topic_rows VALUES(?,?,?)',('en.community.sonos.com','2','{}'));m.db.commit();p=Path(d)/'urls';p.write_text('https://en.community.sonos.com/section-1/topic-1\n');r=reconcile(m,[p]);self.assertEqual(r['by_host'][0]['api_not_in_saved_index'],1);self.assertFalse(r['complete'])
