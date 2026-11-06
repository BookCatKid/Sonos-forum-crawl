import json,tempfile,unittest
from pathlib import Path
from html import escape
from discover_groups import group_rows
from build_discovery_plan import build
class Tests(unittest.TestCase):
 def test_only_public_group_classes_kept(self):
  rows=[{'id':1,'type':4,'url':'https://en.community.sonos.com/groups/example-1','totalTopics':3},{'id':2,'type':5,'url':'https://en.community.sonos.com/groups/private-2','totalTopics':8}];s='<div data-props="'+escape(json.dumps({'groups':{'otherGroups':rows}}),quote=True)+'">';self.assertEqual(len(group_rows(s,'en.community.sonos.com')),1)
 def test_group_inventory_becomes_category_contract(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'groups.json';p.write_text(json.dumps([{'id':1,'type':4,'url':'https://en.community.sonos.com/groups/example-1','totalTopics':3}]))
   r=build([p]);self.assertEqual(r['categories'][0]['surface'],'group');self.assertEqual(r['categories'][0]['first_request']['request']['filters']['category'],[1])
 def test_cross_origin_rejected(self):
  with self.assertRaises(ValueError):group_rows('<div data-props="'+escape(json.dumps({'groups':{'otherGroups':[{'id':1,'type':4,'url':'https://evil.test/groups/1'}]}}),quote=True)+'">','en.community.sonos.com')
