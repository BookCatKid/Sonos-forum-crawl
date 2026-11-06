import unittest
from audit_public_config import summarize
from integrate_config import integrate
class Tests(unittest.TestCase):
 def test_KB_config_adds_both_ordered_and_regular_reads(self):
  page=summarize([{'component':'category/CategoryListWithTopics.sync','props':{'id':9,'isKnowledgeBase':True}}],'https://en.community.sonos.com/kb-9');plan={'categories':[{'host':'en.community.sonos.com','id':9,'url':page['url'],'container':False}]};r=integrate(plan,[{'pages':{page['url']:page},'stopped':None}]);self.assertEqual(len(r['categories'][0]['additional_reads']),2);self.assertTrue(r['configuration_audit']['all_leaf_nodes_audited'])
