import json,tempfile,unittest
from pathlib import Path
import test_unattended
from public_store import PublicStore
from access_migration import apply_reviewed_resource_exclusion,HOST
class AccessMigrationTests(unittest.TestCase):
 def test_exact_stop_lifted_once_exclusion_retained(self):
  with tempfile.TemporaryDirectory() as d:
   root=test_unattended.UnattendedTests().setup_state(d);s=PublicStore(root)
   s.task('https://'+HOST+'/contentNavigation/topics',{'sort':{},'filters':{'category':[229122]},'pagination':{'page':1,'pageSize':25}},'phase-listing');s.db.commit();s.db.close()
   self.assertTrue(apply_reviewed_resource_exclusion(root));s=PublicStore(root);self.assertEqual(s.db.execute('select count(*) from transport_stops').fetchone()[0],0);self.assertEqual(s.db.execute("select count(*) from tasks where state='failed'").fetchone()[0],1)
   s.db.execute('INSERT INTO transport_stops VALUES(?,?,?)',(HOST,'HTTP 429','new'));s.db.commit();s.db.close();self.assertFalse(apply_reviewed_resource_exclusion(root));s=PublicStore(root);self.assertEqual(s.db.execute('select reason from transport_stops').fetchone()[0],'HTTP 429');s.db.close()
 def test_unrelated_stop_never_lifted(self):
  with tempfile.TemporaryDirectory() as d:
   root=test_unattended.UnattendedTests().setup_state(d);s=PublicStore(root);s.db.execute("UPDATE transport_stops SET at='other'");s.db.commit();s.db.close();self.assertFalse(apply_reviewed_resource_exclusion(root))
