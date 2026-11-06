import unittest,tempfile,json
from public_payload import replies,PublicationError
from parser_migration import apply_reply_shapes
from test_unattended import UnattendedTests
from public_store import PublicStore
from pathlib import Path
class ReplyShapeTests(unittest.TestCase):
 def test_ui_pagination_url_dropped_absent_author_omitted(self):
  x={'metadata':{'totalCount':1,'hasMore':True,'offset':0,'nextPageTopicUrl':'https://de.community.sonos.com/topic-1/index2.html'},'result':[{'id':'1','publicReplyId':'2','authorId':'','content':'unchanged'}]};p=replies(x);self.assertNotIn('authorId',p['result'][0]);self.assertNotIn('nextPageTopicUrl',p['metadata']);self.assertEqual(p['result'][0]['content'],'unchanged')
 def test_invalid_nonempty_author_rejected(self):
  with self.assertRaises(PublicationError):replies({'metadata':{},'result':[{'id':'1','publicReplyId':'2','authorId':'bad'}]})
 def test_exact_migration_keeps_en_access_stop(self):
  with tempfile.TemporaryDirectory() as d:
   root=UnattendedTests().setup_state(d);s=PublicStore(root);s.db.execute("INSERT INTO transport_stops VALUES('de.community.sonos.com','public-schema:unknown-field','2026-11-06T17:43:07.640151+00:00')");s.task('https://de.community.sonos.com/topic/34077/replies',{'offset':0,'pageSize':20,'sort':'oldestFirst'},'topic-replies');s.db.commit();s.db.close();self.assertEqual(apply_reply_shapes(root),['de.community.sonos.com']);s=PublicStore(root);self.assertEqual([r[0]for r in s.db.execute('select host from transport_stops')],['en.community.sonos.com']);s.db.close()
