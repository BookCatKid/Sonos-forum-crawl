import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
import test_unattended
from public_store import PublicStore,check_public
from fetch_failures import import_observed,record,blocked,ensure
from packed_generation import publish,restore
from test_generations import MemoryTransport
class FetchFailureTests(unittest.TestCase):
 def test_import_publish_restore_and_other_stops_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'root';root.mkdir();test_unattended.UnattendedTests().setup_state(root)
   import_observed(root);s=PublicStore(root);self.assertEqual(s.db.execute('select count(*) from transport_stops').fetchone()[0],0);self.assertEqual(s.db.execute('select count(*) from fetch_failures').fetchone()[0],1)
   s.db.execute("INSERT INTO transport_stops VALUES('en.community.sonos.com','HTTP 429','2026-11-06T23:40:00+00:00')");s.db.commit();s.db.close();import_observed(root);check_public(root)
   r=MemoryTransport();publish(root,'checkpoint-failure-test',r);fresh=Path(d)/'fresh';restore('checkpoint-failure-test',r,fresh);check_public(fresh);s=PublicStore(fresh);self.assertEqual(s.db.execute('select reason from transport_stops').fetchone()[0],'HTTP 429');self.assertEqual(s.db.execute('select count(*) from fetch_failures').fetchone()[0],1);s.db.close()
 def test_category_provenance_blocks_other_endpoint(self):
  with tempfile.TemporaryDirectory() as d:
   root=test_unattended.UnattendedTests().setup_state(d);s=PublicStore(root);q={'categoryId':123};s.task('https://de.community.sonos.com/categoryV2/listReadableCategoryOrderTopics',q,'phase-listing');s.db.commit();row=s.db.execute('select * from tasks where request=?',(json.dumps(q,sort_keys=True),)).fetchone();record(s,row,'HTTP 403','2026-11-06T23:40:00+00:00')
   self.assertTrue(blocked(s,{'url':'https://de.community.sonos.com/contentNavigation/topics','request':json.dumps({'filters':{'category':[123]}})}));self.assertFalse(blocked(s,{'url':'https://de.community.sonos.com/contentNavigation/topics','request':json.dumps({'filters':{'category':[124]}})}));s.db.close()
 def test_bootstrap_denial_and_throttle_are_host_stops(self):
  from controller import run_batch
  from run_api import Stop
  from public_job import stop_code
  for bootstrap in [True,False]:
   class Fake:
    def __init__(self,*a):pass
    def bootstrap(self):
     if bootstrap:raise Stop('HTTP 403')
    def read(self,*a):raise Stop('HTTP 429')
   with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
    root=test_unattended.UnattendedTests().setup_state(d);s=PublicStore(root);s.task('https://de.community.sonos.com/contentNavigation/topics',{'sort':{},'pagination':{'page':2,'pageSize':25}},'phase-listing');s.db.commit();s.db.close();run_batch(root,1,session_factory=Fake,store_factory=PublicStore,stop_reason=stop_code);s=PublicStore(root);self.assertEqual(s.db.execute("select reason from transport_stops where host='de.community.sonos.com'").fetchone()[0],'HTTP 403' if bootstrap else 'HTTP 429');s.db.close()

 def test_newly_enqueued_denied_category_never_fetched_in_same_batch(self):
  from controller import run_batch
  from run_api import Stop
  from public_job import stop_code
  calls=[]
  with tempfile.TemporaryDirectory() as d,patch('controller.time.sleep'):
   root=test_unattended.UnattendedTests().setup_state(d);s=PublicStore(root)
   q={'sort':{},'filters':{'category':[123]},'pagination':{'page':1,'pageSize':25}}
   s.task('https://de.community.sonos.com/contentNavigation/topics',q,'phase-listing');s.db.commit();row=s.db.execute("select * from tasks where state='pending'").fetchone();record(s,row,'HTTP 403','2026-11-06T23:40:00+00:00');s.db.close()
   class Fake:
    def __init__(self,*a):self.policy=None
    def bootstrap(self):pass
    def read(self,url,p):calls.append(p);raise Stop('HTTP 404')
   run_batch(root,5,session_factory=Fake,store_factory=PublicStore,stop_reason=stop_code);self.assertEqual(calls,[])
