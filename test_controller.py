import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from controller import prepare,run_batch,advance_replies
from api_adapter import APIStore
from run_api import Stop
class Fake:
 def __init__(self,host,root):self.host=host;self.policy=None
 def bootstrap(self):pass
 def read(self,url,p):return {'url':url,'method':'POST','status':200,'request':p,'response':{'topics':[{'publicId':'1','contentType':'question','content':'full'}],'total':1}}
class Denied(Fake):
 def read(self,url,p):raise Stop('HTTP403')
class Tests(unittest.TestCase):
 def prep(self,d):
  p=Path(d)/'plan';p.write_text(json.dumps({'categories':[{'container':False,'first_request':{'url':'https://en.community.sonos.com/contentNavigation/topics','request':{'sort':{},'pagination':{'page':1,'pageSize':25}}}}]}));root=Path(d)/'run';prepare(root,p);return root,p
 def test_checkpoint_resume_and_explicit_phase_gate(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root,p=self.prep(d)
   with self.assertRaises(ValueError):advance_replies(root)
   a=run_batch(root,1,session_factory=Fake);self.assertEqual(a['captured_this_batch'],1);self.assertEqual(run_batch(root,1,session_factory=Fake)['captured_this_batch'],0)
   with self.assertRaises(ValueError):prepare(root,p)
   r=advance_replies(root);self.assertEqual(r['tasks']['pending'],1)
 def test_denial_persists_and_prevents_resume(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root,p=self.prep(d);a=run_batch(root,1,session_factory=Denied);self.assertIn('logged gap',a['reason'])
   self.assertEqual(run_batch(root,1,session_factory=Fake,host_filter='en.community.sonos.com')['captured_this_batch'],0)
 def test_storage_budget_prevents_network(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root,p=self.prep(d);a=run_batch(root,1,byte_budget=0,session_factory=Denied);self.assertEqual(a['captured_this_batch'],0);self.assertEqual(a['reason'],'disk budget reached')
 def test_faster_spacing_rejected(self):
  with self.assertRaises(ValueError):run_batch('/tmp/not-opened',1,delay=0.5)
 def test_listing_never_runs_injected_reply_task(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root,p=self.prep(d);m=APIStore(root)
   with m.db:
    m.task('https://en.community.sonos.com/topic/123/replies',{'offset':0,'pageSize':20,'sort':'oldestFirst'},'observed')
   a=run_batch(root,3,session_factory=Fake);self.assertEqual(a['captured_this_batch'],1);self.assertEqual(a['status']['tasks']['pending'],1);self.assertIn('queue empty',a['reason'])
 def test_genuinely_empty_listing_advances_without_fetch_or_missing_table(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   p=Path(d)/'plan';p.write_text('{"categories":[]}');root=Path(d)/'empty';prepare(root,p);r=advance_replies(root);self.assertEqual(r['topic_rows'],0);self.assertEqual(run_batch(root,1,session_factory=Denied)['captured_this_batch'],0)
 def test_reply_phase_does_not_run_listing_task(self):
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   p=Path(d)/'plan';p.write_text('{"categories":[]}');root=Path(d)/'empty';prepare(root,p);advance_replies(root);m=APIStore(root)
   with m.db:m.task('https://en.community.sonos.com/contentNavigation/topics',{'sort':{},'pagination':{'page':1,'pageSize':25}},'unexpected')
   self.assertEqual(run_batch(root,1,session_factory=Denied)['captured_this_batch'],0)
class StandaloneTests(unittest.TestCase):
 def test_initial_and_existing_run_sync_is_idempotent(self):
  from controller import sync_standalone
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'plan';p.write_text(json.dumps({'categories':[],'standalone_reads':[{'url':'https://en.community.sonos.com/productUpdates/topics?page=1','method':'GET','request':{}},{'url':'https://en.community.sonos.com/events/events?page=1','method':'GET','request':{}}]}));root=Path(d)/'run';prepare(root,p);self.assertEqual(sync_standalone(root,p)['tasks']['pending'],2);self.assertEqual(sync_standalone(root,p)['tasks']['pending'],2)

class CoverageGateTests(unittest.TestCase):
 def test_total_drift_cannot_advance_even_with_no_pending_pages(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'plan';p.write_text('{"categories":[]}');root=Path(d)/'run';prepare(root,p);store=APIStore(root)
   for page,total,ids in [(1,2,['1']),(2,3,['2']),(3,3,['3'])]:
    store.ingest({'url':'https://en.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{},'pagination':{'page':page,'pageSize':1}},'response':{'total':total,'topics':[{'publicId':i,'contentType':'question','content':'full'} for i in ids]}})
   self.assertFalse(list(store.db.execute("SELECT 1 FROM tasks WHERE state='pending'")))
   with self.assertRaisesRegex(ValueError,'audit is inconsistent'):advance_replies(root)
   self.assertEqual(store.db.execute("SELECT value FROM controller WHERE key='phase'").fetchone()[0],'listing')

class ResourceIsolationTests(unittest.TestCase):
 def test_denied_resource_does_not_block_unrelated_topic(self):
  class OneDenied(Fake):
   def read(self,url,p):
    if url.endswith('/1/replies'):raise Stop('HTTP 403')
    return {'url':url,'method':'POST','status':200,'request':p,'response':{'topicReplyCount':0,'replies':{'result':[],'metadata':{'totalCount':0,'hasMore':False}}}}
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root,p=Tests().prep(d);store=APIStore(root)
   with store.db:
    store.db.execute("UPDATE controller SET value='replies' WHERE key='phase'")
    for i in [1,2]:store.task('https://en.community.sonos.com/topic/'+str(i)+'/replies',{'offset':0,'pageSize':20,'sort':'oldestFirst'},'topic-replies')
   result=run_batch(root,2,session_factory=OneDenied)
   self.assertEqual(result['captured_this_batch'],1)
   self.assertEqual(store.db.execute('select count(*) from fetch_failures').fetchone()[0],1)
   self.assertEqual(store.db.execute('select count(*) from transport_stops').fetchone()[0],0)
