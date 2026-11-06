import tempfile,unittest,json
from pathlib import Path
from discovery_config import config
from finite_discovery import Discovery
from run_api import Stop
class DiscoveryTests(unittest.TestCase):
 def test_config_never_retains_post_session_or_profile_fields(self):
  p=config([{'component':'category/CategoryListWithTopics.sync','props':{'csrfToken':'fixture','topics':[{'content':'do not persist'}],'authors':{'email':'fixture'},'phrases':{'x':'y'}}}], 'https://en.community.sonos.com/kb-1');self.assertNotIn('fixture',json.dumps(p));self.assertNotIn('do not persist',json.dumps(p))
 def test_group_visibility_is_explicit(self):
  x=config([{'component':'group/GroupTopicsList','props':{'canSee':True,'group':{'visibility':'public','membershipStatus':'unused'},'totalPagesCount':1}}],'https://en.community.sonos.com/groups/g-1');self.assertEqual(x['public_group'],{'can_see':True,'visibility':'public'});self.assertNotIn('membershipStatus',json.dumps(x))
 def test_persistent_stop_prevents_session_creation(self):
  with tempfile.TemporaryDirectory() as d:
   x=Discovery(d);x.state['stopped']={'reason':'fixture'}
   with self.assertRaises(Stop):x.get_session('en.community.sonos.com')
if __name__=='__main__':unittest.main()
