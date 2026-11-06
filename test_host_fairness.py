import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from controller import prepare,run_batch
from api_adapter import APIStore
class FairnessTests(unittest.TestCase):
 def test_large_de_queue_does_not_starve_nl_and_requests_stay_serial(self):
  calls=[]
  class Fake:
   def __init__(self,host,root):self.host=host;self.policy=None
   def bootstrap(self):pass
   def read(self,url,p):
    calls.append(self.host);return {'url':url,'method':'POST','request':p,'status':200,'response':{'topics':[],'total':0}}
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   plan=Path(d)/'plan';plan.write_text('{"categories":[]}');root=Path(d)/'run';prepare(root,plan);s=APIStore(root)
   for h in ['de.community.sonos.com']*4+['nl.community.sonos.com']*2:
    n=s.db.execute('select count(*) from tasks').fetchone()[0]+1;s.task('https://'+h+'/contentNavigation/topics',{'sort':{},'pagination':{'page':n,'pageSize':25}},'phase-listing')
   s.db.commit();s.db.close();run_batch(root,4,session_factory=Fake)
   self.assertEqual(calls,['de.community.sonos.com','nl.community.sonos.com']*2)
