"""Offline automatic reply->listing->reply cycle; not live freshness proof."""
import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
import test_unattended
from public_store import PublicStore,check_public
from unattended import prepare_action,cycle_action
from controller import run_batch
from public_job import stop_code
class RefreshRoundtripTests(unittest.TestCase):
 def test_new_listing_identity_and_changed_reply_survive_cycle(self):
  class Fake:
   def __init__(self,host,root):self.host=host;self.policy=None
   def bootstrap(self):pass
   def read(self,url,q):
    if '/topic/' in url:body={'authors':{},'topicReplyCount':1,'replies':{'metadata':{'totalCount':1,'hasMore':False,'offset':0},'result':[{'id':'4','publicReplyId':'5','content':'new full reply'}]}}
    elif url.endswith('/groups'):body={'groups':[]}
    elif url.endswith('getAccessibleCategoriesTree'):body={'0':[]}
    else:body={'topics':[{'publicId':'888','contentType':'question','content':'new full topic'}],'total':1,'authors':{}}
    return {'url':url,'method':'GET' if url.endswith('/groups') else 'POST','request':q,'status':200,'response':body}
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root=test_unattended.UnattendedTests().setup_state(d);prepare_action(root,'replies');s=PublicStore(root);s.db.execute("update tasks set state='captured'");s.db.commit();s.db.close()
   self.assertEqual(cycle_action(root),'refresh');prepare_action(root,'refresh');run_batch(root,20,session_factory=Fake,store_factory=PublicStore,stop_reason=stop_code)
   self.assertEqual(cycle_action(root),'replies');prepare_action(root,'replies');run_batch(root,20,session_factory=Fake,store_factory=PublicStore,stop_reason=stop_code)
   check_public(root);s=PublicStore(root);self.assertTrue(s.db.execute("select 1 from topic_rows where public_id='888'").fetchone());self.assertEqual(s.db.execute('select count(*) from transport_stops').fetchone()[0],1);self.assertIn('new full reply',s.db.execute('select raw_json from reply_rows limit 1').fetchone()[0]);s.db.close();self.assertEqual(cycle_action(root),'refresh')
