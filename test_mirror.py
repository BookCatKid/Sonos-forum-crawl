import tempfile,unittest,json,urllib.error,gzip
from pathlib import Path
from urllib.robotparser import RobotFileParser
from mirror import Mirror,parse_page,safe_url,RobotsPolicy

def page(topic='1234567',replies=None,total=0,pages=1,current=1,nexturl=None):
 d={'topic':{'publicId':topic,'id':'100','title':'fixture','contentType':'conversation','fullTopicUrl':'https://en.community.sonos.com/test-1/topic-'+topic,'content':'<p>post body retained</p>'},'currentPage':current,'repliesProps':{'replies':{'result':replies or [],'metadata':{'totalCount':total,'hasMore':bool(nexturl),'nextPageTopicUrl':nexturl}},'totalPages':pages,'totalReplies':total}}
 import html
 return ('<div data-preact="destination/modules/Content/TopicView/V2/index.sync" data-props="'+html.escape(json.dumps(d),quote=True)+'"></div>').encode()
def reply(i,children=None):
 x={'id':str(i),'content':'<p>no keyword analysis</p>','url':'https://en.community.sonos.com/test-1/topic-1234567?postid='+str(i),'publicReplyId':str(i)}
 if children:x['children']=children
 return x
class Tests(unittest.TestCase):
 def test_safe_public_scope(self):
  for u in ['https://evil.test/test-1','https://u:p@en.community.sonos.com/topic-1','https://en.community.sonos.com/logout','https://en.community.sonos.com/members/name-123','https://en.community.sonos.com/search/activity/recent','https://en.community.sonos.com/topic/1/replies','https://en.community.sonos.com/test-1?sort=recent']:
   self.assertIsNone(safe_url(u),u)
  self.assertTrue(safe_url('https://de.community.sonos.com/forum-1/thread-123/index2.html'))
 def test_pagination_dedupe_resume_and_original(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);u='https://en.community.sonos.com/test-1/topic-1234567';v=u+'/index2.html';m.ingest(u,page(replies=[reply(1)],total=2,pages=2,nexturl=v));self.assertFalse(m.report()['topics'][0]['complete']);m.ingest(v,page(replies=[reply(2)],total=2,pages=2,current=2));self.assertTrue(m.report()['topics'][0]['complete']);m.ingest(v,page(replies=[reply(2)],total=2,pages=2,current=2));self.assertEqual(m.db.execute("SELECT count(*) FROM posts WHERE kind='reply'").fetchone()[0],2)
   x=m.db.execute('SELECT path,sha256 FROM responses WHERE url=?',(u,)).fetchone();self.assertEqual(gzip.decompress((Path(d)/x['path']).read_bytes()),page(replies=[reply(1)],total=2,pages=2,nexturl=v));m.db.execute("UPDATE queue SET state='fetching' WHERE url=?",(v,));m.db.commit();m.recover();self.assertEqual(m.db.execute('SELECT state FROM queue WHERE url=?',(v,)).fetchone()[0],'pending')
 def test_nested_gap_cannot_pass_as_complete(self):
  child={'result':[reply(2)],'metadata':{'totalCount':2,'hasMore':True,'offset':0}}
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);m.ingest('https://en.community.sonos.com/test-1/topic-1234567',page(replies=[reply(1,child)],total=1));r=m.report()['topics'][0];self.assertFalse(r['complete']);self.assertIn('nested-replies-incomplete',r['gaps'])
 def test_robots_and_challenge_halt(self):
  robots=RobotFileParser();robots.parse(['User-agent: *','Disallow:'])
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d)
   for i in [1,2]:m.enqueue('https://en.community.sonos.com/test-'+str(i))
   m.db.commit();calls=[]
   def fetch(u):calls.append(u);raise urllib.error.HTTPError(u,429,'stop',{},None)
   m.run(2,{'en.community.sonos.com':robots},delay=0,fetch=fetch);self.assertEqual(len(calls),1);self.assertEqual(len(m.report()['host_stops']),1);m.run(2,{'en.community.sonos.com':robots},delay=0,fetch=fetch);self.assertEqual(len(calls),1)
 def test_missing_policy_excluded_no_fetch(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);m.enqueue('https://en.community.sonos.com/test-1');m.db.commit();m.run(1,{},delay=0,fetch=lambda u:self.fail('fetched'));self.assertEqual(m.report()['queue']['excluded'],1)
 def test_public_link_discovery_no_body_scan(self):
  b=page()+b'<a href="/section-44/unindexed-thread-7654321">title</a><a href="/members/u-1">user</a><a href="https://evil.test/">other</a>'
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);m.ingest('https://en.community.sonos.com/test-1/topic-1234567',b);links=[r[0] for r in m.db.execute('SELECT url FROM links')];self.assertEqual(links,['https://en.community.sonos.com/section-44/unindexed-thread-7654321'])
 def test_validation_limit_enforced(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):Mirror(d).run(100,{})

class MoreTests(unittest.TestCase):
 def test_reply_permalink_normalizes_without_extra_fetch(self):
  self.assertEqual(safe_url('https://en.community.sonos.com/test-1/topic-123?postid=99'),'https://en.community.sonos.com/test-1/topic-123')
 def test_language_list_reply_shape(self):
  import html
  for host in ['de','nl']:
   d={'topic':{'publicId':'123','id':'1','contentType':'conversation','fullTopicUrl':f'https://{host}.community.sonos.com/test-1/topic-123','replyCount':1},'currentPage':1,'repliesProps':{'replies':[reply(2)],'totalPages':1,'totalReplies':1}}
   b=('<div data-preact="TopicView/V2" data-props="'+html.escape(json.dumps(d),quote=True)+'"></div>').encode()
   with tempfile.TemporaryDirectory() as root:
    m=Mirror(root);m.ingest(d['topic']['fullTopicUrl'],b);self.assertTrue(m.report()['topics'][0]['complete'])
 def test_changed_snapshots_retained_and_marked_not_reconciled(self):
  with tempfile.TemporaryDirectory() as root:
   m=Mirror(root);u='https://en.community.sonos.com/test-1/topic-1234567';m.ingest(u,page());m.ingest(u,page(replies=[reply(2)],total=1));self.assertEqual(m.db.execute('SELECT count(*) FROM response_history').fetchone()[0],2);self.assertFalse(m.report()['topics'][0]['complete']);self.assertEqual(m.verify_originals(),[])
 def test_stopped_host_does_not_block_independent_host(self):
  r=RobotFileParser();r.parse(['User-agent: *','Disallow:'])
  with tempfile.TemporaryDirectory() as root:
   m=Mirror(root);m.enqueue('https://en.community.sonos.com/test-1');m.enqueue('https://de.community.sonos.com/test-1');m.db.commit();m.stop('en.community.sonos.com','403');calls=[]
   m.run(1,{'de.community.sonos.com':r},delay=0,fetch=lambda u:(calls.append(u) or (page(),u,{})))
   self.assertEqual(calls,['https://de.community.sonos.com/test-1'])

class ComponentLinkTests(unittest.TestCase):
 def test_component_url_fields_discover_without_reading_post_words(self):
  import html
  d={'categories':[{'url':'/forum-22/topic-7654321'}],'content':'https://en.community.sonos.com/not-a-link-1234567','profileUrl':'/members/member-1'}
  b=page()+('<div data-preact="mega-menu" data-props="'+html.escape(json.dumps(d),quote=True)+'"></div>').encode()
  parsed=parse_page(b,'https://en.community.sonos.com/test-1/topic-1234567')
  self.assertIn('https://en.community.sonos.com/forum-22/topic-7654321',parsed['links']);self.assertNotIn('https://en.community.sonos.com/not-a-link-1234567',parsed['links'])

class EvidenceTests(unittest.TestCase):
 def test_schema_change_retains_raw_and_exposes_gap(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);b=b'<div data-preact="TopicView/V2" data-props="{&quot;topic&quot;:1}"></div>';u='https://en.community.sonos.com/test-1';m.ingest(u,b)
   self.assertEqual(m.db.execute('SELECT count(*) FROM responses').fetchone()[0],1);self.assertEqual(m.verify_originals(),[]);self.assertEqual(m.db.execute('SELECT kind FROM gaps').fetchone()[0],'parse-failed')
 def test_missing_all_reply_count_never_claims_count_consistency(self):
  import html
  d={'topic':{'publicId':'1','id':'1','contentType':'conversation','fullTopicUrl':'https://en.community.sonos.com/topic-1'},'repliesProps':{'replies':{'result':[],'metadata':{'totalCount':0}},'totalPages':1}}
  b=('<div data-preact="TopicView/V2" data-props="'+html.escape(json.dumps(d),quote=True)+'"></div>').encode()
  with tempfile.TemporaryDirectory() as root:
   m=Mirror(root);m.ingest(d['topic']['fullTopicUrl'],b);self.assertFalse(m.report()['topics'][0]['complete'])


class StructureTests(unittest.TestCase):
 def test_real_schema_structure_frontier_and_count_metadata(self):
  import html
  u='https://de.community.sonos.com/category-123';nexturl=u+'/index2.html?sort=lastActivityAt'
  d={'categoryId':123,'initialPage':1,'totalPageCount':2,'totalTopicsCount':26,'nextPageUrl':nexturl,'topics':[{'topicUrl':{'destination':'https://de.community.sonos.com/category-123/thread-456'}}]}
  b=('<div data-preact="destination/CategoryTopicList" data-props="'+html.escape(json.dumps(d),quote=True)+'"></div>').encode()
  with tempfile.TemporaryDirectory() as root:
   m=Mirror(root);m.ingest(u,b);r=m.report();self.assertEqual(r['structure_pages'][0]['pages_expected'],2);self.assertEqual(len(r['structure_pages'][0]['topic_urls']),1)
   self.assertIsNotNone(m.db.execute('SELECT url FROM queue WHERE url=?',(nexturl,)).fetchone());self.assertEqual(m.db.execute('SELECT count(*) FROM gaps').fetchone()[0],0)
 def test_policy_can_disallow_sorted_category_pagination(self):
  r=RobotsPolicy('User-agent: *\nDisallow: /*?sort=')
  self.assertFalse(r.can_fetch('*','https://en.community.sonos.com/category-123/index2.html?sort=lastActivityAt'))

class PolicyTests(unittest.TestCase):
 def test_longest_allow_and_terminal_anchor(self):
  r=RobotsPolicy('User-agent: *\nDisallow: /private/*\nAllow: /private/public-123$')
  self.assertFalse(r.can_fetch('*','https://de.community.sonos.com/private/x'))
  self.assertTrue(r.can_fetch('*','https://de.community.sonos.com/private/public-123'))
  self.assertFalse(r.can_fetch('*','https://de.community.sonos.com/private/public-123/more'))
 def test_original_corruption_is_detected(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);m.ingest('https://en.community.sonos.com/test-1',page());path=m.db.execute('SELECT path FROM responses').fetchone()[0];(Path(d)/path).write_bytes(b'bad');self.assertEqual(len(m.verify_originals()),1)

class SnapshotTests(unittest.TestCase):
 def test_page_refresh_removes_stale_current_post_but_keeps_both_snapshots(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);u='https://en.community.sonos.com/test-1/topic-1234567';m.ingest(u,page(replies=[reply(1)],total=1));m.ingest(u,page(replies=[reply(2)],total=1))
   ids=[r[0] for r in m.db.execute("SELECT id FROM posts WHERE kind='reply'")];self.assertEqual(ids,['2'])
   snapshots=[json.loads(r[0]) for r in m.db.execute('SELECT parsed_json FROM parsed_snapshots')];self.assertEqual(len(snapshots),2);self.assertEqual(snapshots[0]['posts'][1]['id'],'1');self.assertFalse(m.report()['topics'][0]['complete'])
 def test_post_moving_between_pages_deduplicates_without_losing_old_snapshot(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);u='https://en.community.sonos.com/test-1/topic-1234567';v=u+'/index2.html';m.ingest(u,page(replies=[reply(1)],total=1,pages=2,nexturl=v));m.ingest(v,page(replies=[reply(1)],total=1,pages=2,current=2))
   self.assertEqual(m.db.execute("SELECT count(*) FROM posts WHERE kind='reply'").fetchone()[0],1);self.assertEqual(m.db.execute('SELECT count(*) FROM parsed_snapshots').fetchone()[0],2)
if __name__=='__main__':unittest.main()
