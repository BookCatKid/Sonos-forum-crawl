import unittest,datetime
from continue_capture import continue_once
class ChainTests(unittest.TestCase):
 def api(self,other=False):
  calls=[]
  def f(m,p,data=None):
   calls.append((m,p,data))
   if p=='/actions/runs/123':return {'id':123,'path':'.github/workflows/capture.yml','created_at':'2026-11-06T18:00:00Z'}
   if 'runs?'in p:return {'workflow_runs':[{'id':123,'status':'in_progress'}]+([{'id':124,'status':'queued'}]if other else [])}
   return {}
  return f,calls
 def test_successor_once_after_minimum_cadence(self):
  api,c=self.api();slept=[];r=continue_once(api,123,{},now=lambda:datetime.datetime.fromisoformat('2026-11-06T18:20:00+00:00'),sleep=slept.append);self.assertTrue(r['chained']);self.assertEqual(slept,[600]);self.assertEqual(sum(x[0]=='POST'for x in c),1)
 def test_existing_run_no_stack(self):
  api,c=self.api(True);r=continue_once(api,123,{},now=lambda:datetime.datetime.fromisoformat('2026-11-06T18:40:00+00:00'));self.assertFalse(r['chained']);self.assertFalse(any(x[0]=='POST'for x in c))
 def test_all_stopped_no_chain(self):
  api,c=self.api();self.assertFalse(continue_once(api,123,{'paused':True})['chained']);self.assertEqual(c,[])

 def test_failed_capture_can_chain_recovery_without_faking_success(self):
  api,c=self.api();r=continue_once(api,123,{'recovery_required':True},now=lambda:datetime.datetime.fromisoformat('2026-11-06T18:40:00+00:00'));self.assertTrue(r['chained']);self.assertEqual(sum(x[0]=='POST' for x in c),1)
