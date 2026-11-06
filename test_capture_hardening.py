import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from api_adapter import APIStore
from audit_api_capture import audit
from verify_missing_inventory import verify
class HardeningTests(unittest.TestCase):
 def seed(self,d):
  store=APIStore(d);store.ingest({'url':'https://de.community.sonos.com/contentNavigation/topics','method':'POST','status':200,'request':{'sort':{},'pagination':{'page':1,'pageSize':25}},'response':{'topics':[{'publicId':'1','contentType':'question','content':'full'}],'total':1}});return store
 def test_missing_receipt_is_reported(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.seed(d);(Path(d)/s.db.execute('select path from receipts').fetchone()[0]).unlink();self.assertEqual(audit(d)['issues'][0]['kind'],'unreadable-receipt')
 def test_projection_corruption_is_reported(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.seed(d);s.db.execute('delete from topic_rows');s.db.commit();self.assertEqual(audit(d)['topic_projection_audit']['missing'],1)
 def test_historical_denial_is_sticky_without_network(self):
  with tempfile.TemporaryDirectory() as d,patch('verify_missing_inventory.Session') as session:
   u='https://en.community.sonos.com/example-123';Path(d,'results.json').write_text(json.dumps([{'url':u,'error':'HTTP 403'}]));verify([u,'https://en.community.sonos.com/other-124'],d);session.assert_not_called();self.assertTrue(Path(d,'stops.json').exists())
 def test_explicit_unaffected_hosts_continue_with_gap_record(self):
  from controller import prepare,advance_replies
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'run';p=Path(d)/'plan';p.write_text('{"categories":[]}');prepare(root,p);self.seed(root)
   with patch('audit_api_capture.audit',return_value={'issues':[],'malformed_current_topics':0,'listing_families':[{'count_consistent':False}]}):
    result=advance_replies(root,['de.community.sonos.com'],True)
   self.assertEqual(result['tasks']['pending'],1);s=APIStore(root);self.assertIsNotNone(s.db.execute("select value from controller where key='listing_coverage_gaps'").fetchone())
