import tempfile,unittest
from api_adapter import APIStore,validate

def reply(i,children=None):
 r={'id':str(i),'publicReplyId':str(i+100),'content':'<p>raw post</p>'}
 if children:r['children']=children
 return r

def container(rows,total,more=False,offset=0):return {'result':rows,'metadata':{'totalCount':total,'hasMore':more,'offset':offset}}
def evidence(body,payload=None):return {'url':'https://en.community.sonos.com/topic/123/replies','method':'POST','status':200,'request':payload or {'offset':0,'pageSize':20,'sort':'oldestFirst'},'response':body}
class Tests(unittest.TestCase):
 def test_child_queue_and_count_reconciliation(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest(evidence({'topicReplyCount':3,'replies':container([reply(1,container([reply(2)],2,True))],1)}),'question');self.assertFalse(m.report()['reply_audits'][0]['count_consistent']);self.assertEqual(m.report()['tasks']['pending'],1)
   m.ingest(evidence({'topicReplyCount':3,'replies':container([reply(3)],2,False,1)},{'offset':1,'pageSize':20,'sort':'oldestFirst','parentId':{'id':1,'contentType':'question'}}),'question');self.assertTrue(m.report()['reply_audits'][0]['count_consistent']);self.assertNotIn('pending',m.report()['tasks'])
 def test_listing_continuation_retains_full_content(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest({'url':'https://de.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{'lastActivityAt':'desc'},'pagination':{'page':1,'pageSize':25}},'response':{'topics':[{'publicId':'123','content':'<p>full body</p>'}],'total':10000}});self.assertEqual(m.report()['tasks']['pending'],1);self.assertIn('full body',m.db.execute('SELECT raw_json FROM topic_rows').fetchone()[0])
 def test_unsafe_routes_denied(self):
  for url in ['https://evil.test/contentNavigation/topics','https://en.community.sonos.com/community/api/users','https://en.community.sonos.com/post/save','https://u:p@en.community.sonos.com/topic/1/replies']:
   with self.assertRaises(ValueError):validate(url,'POST',{'offset':0,'pageSize':20,'sort':'oldestFirst'})
 def test_error_response_is_evidence_not_capture_success(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);e=evidence({});e['status']=403;m.ingest(e);self.assertEqual(m.report()['tasks'],{'failed':1});self.assertFalse(m.report()['complete'])

class ExtendedTests(unittest.TestCase):
 def test_product_pages_queue_and_event_receipts(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest({'url':'https://en.community.sonos.com/productUpdates/topics?page=1','method':'GET','status':200,'response':{'topics':[],'totalPageCount':2,'totalTopicCount':30}});self.assertEqual(m.report()['tasks']['pending'],1)
   m.ingest({'url':'https://en.community.sonos.com/events/events?page=1','method':'GET','status':200,'response':{'pastEvents':[],'upcomingEvents':[],'totalPageCountUpcoming':0,'totalPageCountPast':0}});self.assertEqual(m.db.execute('SELECT count(*) FROM event_pages').fetchone()[0],1)
 def test_ideas_denied_route_not_runnable(self):
  with self.assertRaises(ValueError):validate('https://en.community.sonos.com/ideas/topics?page=1','GET',{})
class PlanningTests(unittest.TestCase):
 def test_reply_task_only_from_captured_topic_identity(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d)
   with self.assertRaises(ValueError):m.plan_replies('en.community.sonos.com','999')
   m.db.execute('INSERT INTO topic_rows VALUES(?,?,?)',('en.community.sonos.com','123','{"publicId":"123","contentType":"question"}'));m.db.commit();m.plan_replies('en.community.sonos.com','123');self.assertEqual(m.db.execute('SELECT content_type FROM known_topic_types').fetchone()[0],'question')

class RefreshTests(unittest.TestCase):
 def test_changed_response_replaces_latest_reply_projection_keeps_raw_history(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest(evidence({'topicReplyCount':1,'replies':container([reply(1)],1)}),'question');m.ingest(evidence({'topicReplyCount':1,'replies':container([reply(2)],1)}),'question');self.assertEqual([r[0] for r in m.db.execute('SELECT internal_id FROM reply_rows')],['2']);self.assertEqual(m.report()['receipts'],2);self.assertEqual(m.report()['changed_snapshot_tasks'],1);self.assertFalse(m.report()['reply_audits'][0]['refresh_reconciled'])
 def test_new_root_generation_invalidates_old_children(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest(evidence({'topicReplyCount':2,'replies':container([reply(1,container([reply(2)],1))],1)}),'question');m.ingest(evidence({'topicReplyCount':1,'replies':container([reply(3)],1)}),'question');a=m.report()['reply_audits'][0];self.assertTrue(a['count_consistent']);self.assertEqual(a['captured'],1);self.assertFalse(a['refresh_reconciled'])
 def test_list_refresh_removes_only_its_stale_ids(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d)
   def e(page,ids):return {'url':'https://en.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{'lastActivityAt':'desc'},'pagination':{'page':page,'pageSize':25}},'response':{'topics':[{'publicId':i,'content':'raw'} for i in ids],'total':30}}
   m.ingest(e(1,['1','2']));m.ingest(e(2,['2','3']));m.ingest(e(1,['4']));self.assertEqual({r[0] for r in m.db.execute('SELECT public_id FROM topic_rows')},{'2','3','4'});self.assertEqual(m.report()['receipts'],3)
 def test_listing_cap_does_not_enqueue_inaccessible_page(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest({'url':'https://en.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{},'pagination':{'page':400,'pageSize':25}},'response':{'topics':[],'total':10501}});self.assertNotIn('pending',m.report()['tasks'])
class CurationTests(unittest.TestCase):
 def test_ordered_and_regular_KB_rows_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d)
   for route,payload,topics in [('listReadableCategoryOrderTopics',{'categoryId':9},{'9':[{'publicId':'1','contentType':'article','content':'pinned full'}]}),('listReadableCategoryContent',{'categoryId':9,'type':None,'pagination':{'page':1,'pageSize':25}},[{'publicId':'2','contentType':'article','content':'body full'}])]:
    m.ingest({'url':'https://en.community.sonos.com/categoryV2/'+route,'method':'POST','status':200,'request':payload,'response':{'result':{'topics':topics,'total':30}}})
   self.assertEqual(m.report()['topic_rows'],2);self.assertEqual(m.report()['tasks']['pending'],1);m.plan_replies('en.community.sonos.com','1')
 def test_unobserved_module_type_rejected(self):
  with self.assertRaises(ValueError):validate('https://en.community.sonos.com/categoryV2/listReadableCategoryContent','POST',{'categoryId':9,'type':1,'pagination':{'page':1,'pageSize':25}})
class CuratedContinuationTests(unittest.TestCase):
 def test_curated_endpoint_does_not_invent_pagination(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest({'url':'https://en.community.sonos.com/categoryV2/getReadableCuratedContent','method':'POST','status':200,'request':{'categoryId':9,'type':None,'pagination':{'page':1,'pageSize':1}},'response':{'result':{'topics':[{'publicId':'1','contentType':'article'}]}}});self.assertNotIn('pending',m.report()['tasks'])
class EventContinuationTests(unittest.TestCase):
 def test_event_tabs_queue_independently(self):
  with tempfile.TemporaryDirectory() as d:
   m=APIStore(d);m.ingest({'url':'https://en.community.sonos.com/events/events?page=1','method':'GET','status':200,'response':{'pastEvents':[],'upcomingEvents':[],'totalPageCountPast':2,'totalPageCountUpcoming':3}});self.assertEqual(m.report()['tasks']['pending'],2)
