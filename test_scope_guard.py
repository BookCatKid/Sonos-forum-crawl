import json,tempfile,unittest
from pathlib import Path
from api_adapter import APIStore
from scope_guard import load_scope,permitted,enforce
class ScopeTests(unittest.TestCase):
 def policy(self):return load_scope(Path(__file__).parent/'scope.json')
 def test_denied_and_unchecked_replies_excluded(self):
  p=self.policy()
  for i in p['excluded_en_topic_ids']:self.assertFalse(permitted('https://en.community.sonos.com/topic/'+i+'/replies',p))
  self.assertTrue(permitted('https://en.community.sonos.com/topic/6935090/replies',p))
 def test_other_origin_or_identity_rejected(self):
  for u in ('http://en.community.sonos.com/topic/1/replies','https://evil.invalid/topic/1/replies','https://user@en.community.sonos.com/topic/1/replies'):
   self.assertFalse(permitted(u,self.policy()))
 def test_exclusion_persists_without_resetting_denial(self):
  with tempfile.TemporaryDirectory() as d:
   s=APIStore(d);r={'parentType':'question','offset':0,'parentId':None,'pageSize':20,'sort':'oldestFirst'};s.task('https://en.community.sonos.com/topic/6935006/replies',r,'topic-replies');s.db.commit()
   self.assertEqual(enforce(s,self.policy()),1);self.assertEqual(s.db.execute('SELECT state FROM tasks').fetchone()[0],'failed');self.assertEqual(enforce(s,self.policy()),1)
 def test_controller_never_fetches_excluded_pending_task(self):
  from controller import run_batch
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=APIStore(d);s.db.execute('CREATE TABLE controller(key TEXT PRIMARY KEY,value TEXT)');s.db.execute("INSERT INTO controller VALUES('phase','replies')")
   s.task('https://en.community.sonos.com/topic/6935006/replies',{'offset':0,'pageSize':20,'sort':'oldestFirst'},'topic-replies');s.db.commit();(root/'scope.json').write_text(json.dumps(self.policy()))
   def forbidden(*args):raise AssertionError('network session created for excluded resource')
   with patch('controller.time.sleep'):
    result=run_batch(root,1,session_factory=forbidden)
   self.assertEqual(result['captured_this_batch'],0)
 def test_rate_floor_and_longer_robots_delay(self):
  from controller import run_batch,prepare
  from test_controller import Fake
  from unittest.mock import patch
  class SlowerRobots:
   def crawl_delay(self,agent):return 3
  class RobotsFake(Fake):
   def __init__(self,*a):super().__init__(*a);self.policy=SlowerRobots()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);plan=p/'plan';plan.write_text(json.dumps({'categories':[{'container':False,'first_request':{'url':'https://en.community.sonos.com/contentNavigation/topics','request':{'sort':{},'pagination':{'page':1,'pageSize':25}}}}]}));root=p/'run';prepare(root,plan)
   with patch('controller.time.sleep') as sleep:run_batch(root,1,session_factory=RobotsFake)
   self.assertEqual([x.args[0] for x in sleep.call_args_list],[1,3])
 def test_scope_widening_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.policy();p['anonymous_only']=False;f=Path(d)/'s';f.write_text(json.dumps(p))
   with self.assertRaises(ValueError):load_scope(f)
