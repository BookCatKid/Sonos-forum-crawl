"""Offline end-to-end public phase and checkpoint test; no forum traffic."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_unattended import UnattendedTests
from public_store import PublicStore,check_public
from unattended import prepare_action,cycle_action
from controller import run_batch
from public_job import stop_code
from test_generations import MemoryTransport
from packed_generation import publish,restore
class IntegrationTests(unittest.TestCase):
 def test_eligible_reply_capture_checkpoint_restore_keeps_en_stop(self):
  class Replies:
   def __init__(self,host,root):self.host=host;self.policy=None
   def bootstrap(self):pass
   def read(self,url,request):
    if self.host=='en.community.sonos.com':raise AssertionError('stopped host contacted')
    return {'url':url,'method':'POST','request':request,'status':200,'response':{'authors':{},'topicReplyCount':1,'replies':{'result':[{'id':4,'publicReplyId':5,'authorId':2,'content':'<p>Full preserved reply</p>','attachments':[]}],'metadata':{'totalCount':1,'hasMore':False,'offset':0}}}}
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root=Path(d)/'run';root.mkdir();UnattendedTests().setup_state(root)
   prepare_action(root,'replies');r=run_batch(root,1,store_factory=PublicStore,stop_reason=stop_code,session_factory=Replies);self.assertEqual(r['captured_this_batch'],1)
   check_public(root);remote=MemoryTransport();publish(root,'checkpoint-integration',remote);fresh=Path(d)/'restored';restore('checkpoint-integration',remote,fresh)
   s=PublicStore(fresh);self.assertEqual(s.db.execute('select count(*) from reply_rows').fetchone()[0],1);self.assertIn('Full preserved reply',s.db.execute('select raw_json from reply_rows').fetchone()[0]);self.assertEqual(s.db.execute('select count(*) from transport_stops').fetchone()[0],1);s.db.close();self.assertEqual(cycle_action(fresh),'refresh')
