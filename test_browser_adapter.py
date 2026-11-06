import tempfile,unittest
from browser_adapter import ingest_browser
from mirror import Mirror

def evidence():return {'url':'https://en.community.sonos.com/test-1/topic-123','provenance':'ordinary-public-browser-rendered-dom','observedAt':'2026-11-05T01:03:34Z','topic':{'publicId':'123','currentPage':1,'pagesExpected':1,'topExpected':1,'allExpected':2},'posts':[{'publicReplyId':'11','parentPublicReplyId':None,'html':'<p>parent</p>'},{'publicReplyId':'12','parentPublicReplyId':'11','html':'<p>child</p>'}],'remainingMore':[]}
class Tests(unittest.TestCase):
 def test_counts_and_separate_provenance(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);a=ingest_browser(m,evidence());self.assertTrue(a['topic_count_consistent']);self.assertEqual(m.db.execute('SELECT count(*) FROM responses').fetchone()[0],0);self.assertEqual(m.db.execute('SELECT count(*) FROM browser_posts').fetchone()[0],2)
 def test_more_controls_and_multipage_never_claim_whole_topic(self):
  with tempfile.TemporaryDirectory() as d:
   m=Mirror(d);e=evidence();e['remainingMore']=['Show 1 more reply'];self.assertFalse(ingest_browser(m,e)['page_count_consistent']);e['remainingMore']=[];e['topic']['pagesExpected']=2;self.assertFalse(ingest_browser(m,e)['topic_count_consistent'])
 def test_dangling_parent_or_duplicate_or_cycle_rejected(self):
  for mode in ['dangling','duplicate','cycle']:
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
    e=evidence()
    if mode=='dangling':e['posts'][1]['parentPublicReplyId']='99'
    if mode=='duplicate':e['posts'][1]['publicReplyId']='11'
    if mode=='cycle':e['posts'][0]['parentPublicReplyId']='12'
    with self.assertRaises(ValueError):ingest_browser(Mirror(d),e)
