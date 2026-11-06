import unittest,tempfile
from pathlib import Path
from html import escape
import json
from group_discovery import parse_group_page,project_group
from public_payload import PublicationError
from public_store import PublicStore,check_public
class GroupDiscoveryTests(unittest.TestCase):
 def test_index_and_explicit_public_page_enqueue(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes());s=PublicStore(root);url='https://de.community.sonos.com/groups/new-public-123'
   s.ingest({'url':'https://de.community.sonos.com/groups','method':'GET','request':{},'status':200,'response':{'groups':[{'id':123,'type':4,'url':url}]}})
   s.ingest({'url':url,'method':'GET','request':{},'status':200,'response':{'id':123,'canSee':True,'visibility':'public','totalTopicsCount':5,'supportedContentTypes':['question']}})
   self.assertTrue(s.db.execute("select 1 from tasks where json_extract(request,'$.filters.category[0]')=123").fetchone());s.db.close();check_public(root)
 def test_private_page_cannot_expand(self):
  b={'id':123,'canSee':False,'visibility':'private','totalTopicsCount':5,'supportedContentTypes':['question']}
  with self.assertRaises(PublicationError):project_group(b,'https://de.community.sonos.com/groups/private-123')
 def test_page_parser_drops_session_and_topics(self):
  props={'categoryId':123,'canSee':True,'group':{'visibility':'public'},'totalTopicsCount':5,'supportedContentTypes':['question'],'csrfToken':'not-stored','topics':[{'content':'not-stored'}]}
  html='<div data-preact="GroupTopicsList" data-props="'+escape(json.dumps(props),quote=True)+'"></div>'
  b=parse_group_page(html.encode(),'https://de.community.sonos.com/groups/public-123');self.assertNotIn('csrfToken',b);self.assertNotIn('topics',b)
