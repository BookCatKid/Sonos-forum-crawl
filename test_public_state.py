import json,sqlite3,tempfile,unittest
from pathlib import Path
from public_store import check_public
from public_payload import PublicationError,project
import test_public_store,test_public_payload
class StateTests(unittest.TestCase):
 def seed(self,p):test_public_store.PublicStoreTests().seed(p)
 def test_unknown_column_stops(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);db=sqlite3.connect(p/'api.sqlite');db.execute('ALTER TABLE tasks ADD COLUMN token TEXT');db.close()
   with self.assertRaises(PublicationError):check_public(p)
 def test_changed_scope_metadata_stops(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);scope=json.loads((p/'scope.json').read_text());scope['arbitrary']='not approved';(p/'scope.json').write_text(json.dumps(scope))
   with self.assertRaises(PublicationError):check_public(p)
 def test_arbitrary_stop_details_stops(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);db=sqlite3.connect(p/'api.sqlite');db.execute('CREATE TABLE transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)');db.execute('INSERT INTO transport_stops VALUES(?,?,?)',('en.community.sonos.com','arbitrary details','2026-11-05T00:00:00+00:00'));db.commit();db.close()
   with self.assertRaises(PublicationError):check_public(p)
 def test_future_request_fields_stop(self):
  e=test_public_payload.PublicPayloadTests().sample();e['request']['token']='unapproved'
  with self.assertRaises(PublicationError):project(e)
 def test_product_update_schema(self):
  e=test_public_payload.PublicPayloadTests().sample();e.update(url='https://en.community.sonos.com/productUpdates/topics?page=1',method='GET',request={});e['response']={'topics':e['response']['topics'],'totalPageCount':1,'totalTopicCount':1};self.assertEqual(project(e)['response']['totalTopicCount'],1)
 def test_tree_schema(self):
  e={'url':'https://de.community.sonos.com/categoryV2/getAccessibleCategoriesTree','method':'POST','request':{'type':[0],'excludeFields':['description','thumbnailImage']},'status':200,'response':{'0':[{'id':1,'children':[],'title':'Public category','bannerConfig':None,'heroImage':'unused'}]}}
  self.assertNotIn('heroImage',project(e)['response']['0'][0])
 def test_nonempty_attachment_requires_review(self):
  e={'url':'https://en.community.sonos.com/topic/1/replies','method':'POST','status':200,'request':{'offset':0,'pageSize':20,'sort':'oldestFirst'},'response':{'authors':{},'topicReplyCount':1,'replies':{'metadata':{'totalCount':1,'hasMore':False,'offset':0},'result':[{'id':1,'publicReplyId':2,'authorId':3,'content':'body','attachments':[{'unreviewed':'value'}]}]}}}
  with self.assertRaises(PublicationError):project(e)
 def test_nested_payload_in_scalar_field_stops(self):
  e=test_public_payload.PublicPayloadTests().sample();e['response']['topics'][0]['content']={'unreviewed':'nested'}
  with self.assertRaises(PublicationError):project(e)
 def test_topic_destination_schema(self):
  e=test_public_payload.PublicPayloadTests().sample();e['response']['topics'][0]['topicUrl']={'destination':'https://en.community.sonos.com/public-1'}
  self.assertEqual(project(e)['response']['topics'][0]['topicUrl']['destination'],'https://en.community.sonos.com/public-1')
 def test_excluded_id_in_listing_stops_before_storage(self):
  from public_store import PublicStore
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.seed(p);e=test_public_payload.PublicPayloadTests().sample();e['response']['topics'][0]['publicId']='6935006';s=PublicStore(p);before=s.db.execute('SELECT count(*) FROM receipts').fetchone()[0]
   with self.assertRaises(PublicationError):s.ingest(e)
   self.assertEqual(s.db.execute('SELECT count(*) FROM receipts').fetchone()[0],before);s.db.close()
 def test_empty_author_id_omitted_without_identity_fabrication(self):
  e=test_public_payload.PublicPayloadTests().sample();e['response']['topics'][0]['author']['id']='';out=project(e);self.assertNotIn('id',out['response']['topics'][0]['author']);self.assertEqual(out['response']['topics'][0]['author']['username'],'public-name')
 def test_nonempty_nonnumeric_author_id_still_rejected(self):
  e=test_public_payload.PublicPayloadTests().sample();e['response']['topics'][0]['author']['id']='unreviewed'
  with self.assertRaises(PublicationError):project(e)
if __name__=='__main__':unittest.main()
