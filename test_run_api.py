import tempfile,unittest
from unittest.mock import patch
from api_adapter import APIStore
from run_api import run,Stop
class Fake:
 def __init__(self,host,root):pass
 def bootstrap(self):pass
 def read(self,url,payload):return {'url':url,'method':'POST','request':payload,'status':200,'response':{'topics':[{'publicId':'1','content':'<p>full</p>'}],'total':1}}
class Denied(Fake):
 def read(self,url,payload):raise Stop('HTTP403')
class Tests(unittest.TestCase):
 def seed(self,root):
  m=APIStore(root);m.task('https://de.community.sonos.com/contentNavigation/topics',{'sort':{},'pagination':{'page':1,'pageSize':25}},'test');m.db.commit()
 def test_transport_to_store(self):
  with tempfile.TemporaryDirectory() as d,patch('run_api.time.sleep'):
   self.seed(d);r=run(d,'de.community.sonos.com',1,1,Fake);self.assertEqual(r['captured_this_run'],1);self.assertEqual(r['report']['topic_rows'],1)
 def test_denial_is_persistent_not_retried(self):
  with tempfile.TemporaryDirectory() as d,patch('run_api.time.sleep'):
   self.seed(d);r=run(d,'de.community.sonos.com',1,1,Denied);self.assertEqual(r['stopped'],'HTTP403')
   with self.assertRaises(Stop):run(d,'de.community.sonos.com',1,1,Fake)
 def test_full_crawl_cap(self):
  with tempfile.TemporaryDirectory() as d,patch('run_api.time.sleep'):
   with self.assertRaises(ValueError):run(d,'de.community.sonos.com',100,0,Fake)
