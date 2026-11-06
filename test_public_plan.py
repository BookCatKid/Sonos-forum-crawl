import json,tempfile,unittest
from pathlib import Path
from initialize_public import initialize
from public_plan import validate_pinned
from public_store import check_public
from public_payload import project,PublicationError
from packed_generation import publish,restore
from test_generations import MemoryTransport
class PlanTests(unittest.TestCase):
 def test_current_plan_offline_init_and_checkpoint_restore(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);initialize(p/'run');self.assertEqual(check_public(p/'run')['receipts_checked'],0);r=MemoryTransport();publish(p/'run','checkpoint-bootstrap',r);restore('checkpoint-bootstrap',r,p/'restored');self.assertTrue((p/'restored/discovery-plan.json').exists())
 def test_plan_mutation_rejected(self):
  x=json.loads((Path(__file__).parent/'approved-discovery-plan.json').read_text());x['arbitrary']='unreviewed'
  with self.assertRaises(PublicationError):validate_pinned(x)
 def test_category_public_schema(self):
  e={'url':'https://en.community.sonos.com/categoryV2/listReadableCategoryContent','method':'POST','status':200,'request':{'categoryId':1,'type':None,'pagination':{'page':1,'pageSize':25}},'response':{'result':{'topics':[],'total':0}}};self.assertEqual(project(e)['response']['result']['total'],0)
 def test_unknown_event_schema_is_rejected(self):
  e={'url':'https://en.community.sonos.com/events/events?page=1','method':'GET','status':200,'request':{},'response':{'pastEvents':[],'upcomingEvents':[],'totalPageCountPast':0,'totalPageCountUpcoming':0}};project(e);e['response']['pastEvents']=[{'unknown':'value'}]
  with self.assertRaises(PublicationError):project(e)
if __name__=='__main__':unittest.main()
