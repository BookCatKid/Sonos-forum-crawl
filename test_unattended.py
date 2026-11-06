import tempfile,unittest,json
from pathlib import Path
from public_store import PublicStore,check_public
from unattended import cycle_action,prepare_action
from test_public_payload import PublicPayloadTests
class UnattendedTests(unittest.TestCase):
 def setup_state(self,d):
  root=Path(d);(root/'scope.json').write_bytes((Path(__file__).parent/'scope.json').read_bytes());s=PublicStore(root)
  s.db.execute('CREATE TABLE controller(key TEXT,value TEXT)');s.db.execute("INSERT INTO controller VALUES('phase','listing')");s.db.execute('CREATE TABLE transport_stops(host TEXT PRIMARY KEY,reason TEXT,at TEXT)')
  e=PublicPayloadTests().sample();e['url']=e['url'].replace('en.community','de.community');s.ingest(e)
  s.db.execute("INSERT INTO transport_stops VALUES('en.community.sonos.com','HTTP 403','2026-11-06T14:17:56+00:00')");s.db.commit();s.db.close();return root
 def test_auto_replies_never_clears_en_stop(self):
  with tempfile.TemporaryDirectory() as d:
   root=self.setup_state(d);self.assertEqual(cycle_action(root),'replies');prepare_action(root,'replies');s=PublicStore(root)
   self.assertEqual(s.db.execute("select value from controller where key='phase'").fetchone()[0],'replies');self.assertEqual(s.db.execute('select count(*) from transport_stops').fetchone()[0],1)
   self.assertFalse(any('en.community' in r[0] for r in s.db.execute("select url from tasks where kind='topic-replies'")));s.db.close();check_public(root)
 def test_reply_cycle_refreshes_then_requeues_roots(self):
  with tempfile.TemporaryDirectory() as d:
   root=self.setup_state(d);prepare_action(root,'replies');s=PublicStore(root);s.db.execute("update tasks set state='captured'");s.db.commit();s.db.close()
   self.assertEqual(cycle_action(root),'refresh');prepare_action(root,'refresh');self.assertEqual(cycle_action(root),'collect')
   s=PublicStore(root);s.db.execute("update tasks set state='captured' where url like '%contentNavigation%'");s.db.commit();s.db.close();prepare_action(root,'replies');self.assertEqual(cycle_action(root),'collect')
