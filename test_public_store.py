import json,tempfile,unittest
from pathlib import Path
from public_store import PublicStore,check_public
from public_payload import PublicationError
import test_public_payload
class PublicStoreTests(unittest.TestCase):
 def seed(self,root):
  root.mkdir(parents=True,exist_ok=True);(root/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes());s=PublicStore(root);s.ingest(test_public_payload.PublicPayloadTests().sample());s.db.close()
 def test_public_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);self.assertEqual(check_public(p)['receipts_checked'],1)
 def test_raw_store_not_publishable(self):
  from api_adapter import APIStore
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);s=APIStore(p);s.ingest(test_public_payload.PublicPayloadTests().sample());s.db.close();(p/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes())
   with self.assertRaises(PublicationError):check_public(p)
 def test_hidden_database_table_stops(self):
  import sqlite3
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);db=sqlite3.connect(p/'api.sqlite');db.execute('CREATE TABLE secrets(value TEXT)');db.close()
   with self.assertRaises(PublicationError):check_public(p)
 def test_projection_injection_stops(self):
  import sqlite3
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);db=sqlite3.connect(p/'api.sqlite');db.execute("UPDATE topic_rows SET raw_json=?",(json.dumps({'content':'extra'}),));db.commit();db.close()
   with self.assertRaises(PublicationError):check_public(p)
 def test_refresh_keeps_observed_id_union_with_receipt_backing(self):
  import copy
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes())
   store=PublicStore(root);first=test_public_payload.PublicPayloadTests().sample();store.ingest(first)
   old=str(first['response']['topics'][0]['publicId'])
   second=copy.deepcopy(first);second['response']['topics'][0]['publicId']='8888888';store.ingest(second)
   self.assertEqual({r[0] for r in store.db.execute('SELECT public_id FROM topic_rows')},{old,'8888888'})
   self.assertEqual(store.db.execute('SELECT count(*) FROM refreshed_tasks').fetchone()[0],1)
   store.db.close();self.assertEqual(check_public(root)['receipts_checked'],2)
 def test_accessible_tree_queues_only_evidenced_leaf_ids(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes());s=PublicStore(root)
   e={'url':'https://de.community.sonos.com/categoryV2/getAccessibleCategoriesTree','method':'POST','status':200,'request':{'excludeFields':['description','thumbnailImage'],'type':[0]},'response':{'0':[{'id':55,'url':'https://de.community.sonos.com/example-55','isContainer':False,'isKnowledgeBase':False,'supportedContentTypes':['question','conversation'],'visibleTopicsCount':12000,'children':[]}]}}
   s.ingest(e);rows=s.db.execute("select url,request from tasks where state='pending'").fetchall();self.assertEqual(len(rows),3)
   self.assertTrue(all('de.community.sonos.com' in r[0] for r in rows));s.db.close();check_public(root)
if __name__=='__main__':unittest.main()
